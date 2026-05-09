"""Windows Task Scheduler helpers for the PaperPrism Agent.

Manages a per-user scheduled task that starts the Agent at logon and
auto-restarts on crash (via the built-in restart settings of Task Scheduler).
This is the Windows equivalent of launchd.py (macOS) and systemd.py (Linux).

The task is created at user level (/RL LIMITED) — no Administrator rights
needed. It relies solely on `schtasks.exe` which ships with every Windows
edition since XP, so there are zero extra dependencies.

Task name:  PaperPrismAgent
Trigger:    At logon (ONLOGON) for the current user
Action:     <launcher> serve
Restart:    On failure, up to 3 times with 30-second delay

Usage (via the CLI):
    paperprism-agent install    # create task and run it immediately
    paperprism-agent uninstall  # stop and delete the task
    paperprism-agent status     # query task state
    paperprism-agent restart    # end any running instance and re-launch
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger("paperprism.winsvc")

TASK_NAME = "PaperPrismAgent"


def resolve_launcher() -> list[str]:
    """Pick a stable argv prefix for schtasks /TR.

    Mirrors the detection logic in launchd.py and systemd.py:

    1. PyInstaller frozen binary — use the binary directly.
    2. uv tool install (~\\AppData\\Roaming\\uv\\tools\\…) — prefer the
       user-level shim at ~\\AppData\\Local\\uv\\bin\\paperprism-agent.exe.
    3. Ephemeral uvx cache (~\\AppData\\Local\\uv\\cache\\…) — refuse,
       the path rotates and would silently break the task after a cache GC.
    4. Default — [sys.executable, "-m", "paperprism_agent", "serve"].

    Note: the returned list is joined with spaces for the /TR argument;
    paths containing spaces are double-quoted by _build_tr().
    """
    exe = Path(sys.executable)

    # (1) Frozen binary.
    if getattr(sys, "frozen", False):
        return [str(exe)]

    home = Path.home()
    # uv on Windows installs tools under AppData\Roaming\uv\tools\
    uv_tools_root = home / "AppData" / "Roaming" / "uv" / "tools"
    uv_cache_root = home / "AppData" / "Local" / "uv" / "cache"

    def _is_under(path: Path, base: Path) -> bool:
        try:
            path.resolve().relative_to(base.resolve())
            return True
        except (ValueError, OSError):
            return False

    # (2) uv tool install.
    if _is_under(exe, uv_tools_root):
        # Prefer the user-level shim which survives reinstalls.
        user_shim = home / "AppData" / "Local" / "uv" / "bin" / "paperprism-agent.exe"
        if user_shim.exists():
            return [str(user_shim)]
        venv_shim = exe.parent / "paperprism-agent.exe"
        if venv_shim.exists():
            return [str(venv_shim)]

    # (3) Ephemeral uvx cache: refuse.
    if _is_under(exe, uv_cache_root):
        raise RuntimeError(
            f"Detected an ephemeral `uvx` environment at {exe}.\n"
            "Task Scheduler needs a stable launcher path, but uvx runs from a\n"
            "cache directory that can be garbage-collected at any time.\n\n"
            "Run this instead:\n"
            "    uv tool install paperprism-agent\n"
            "    paperprism-agent install\n"
        )

    # (4) Default: module invocation via the current interpreter.
    return [str(exe), "-m", "paperprism_agent"]


def _build_tr(launcher_parts: list[str], cfg: "Config") -> str:  # type: ignore[name-defined]  # noqa: F821
    """Build the /TR (task run) string, quoting paths that contain spaces.

    The final command is:  <launcher> serve
    Environment variables cannot be passed via /TR, so we rely on the
    Agent reading PAPERPRISM_HOME / PAPERPRISM_PORT etc. from the env
    that Task Scheduler inherits from the user's logon session — or from
    a wrapper batch file placed by _write_wrapper_bat().
    """
    # schtasks /TR needs a single string; quote individual path segments.
    quoted = [f'"{part}"' if " " in part else part for part in launcher_parts]
    quoted.append("serve")
    return " ".join(quoted)


def _wrapper_bat_path(cfg: "Config") -> Path:  # type: ignore[name-defined]  # noqa: F821
    """~/.paperprism/run-agent.bat — tiny wrapper that sets env vars before
    launching the Agent so Task Scheduler sees PAPERPRISM_HOME etc."""
    return cfg.paths.home / "run-agent.bat"


def write_wrapper_bat(cfg: "Config") -> Path:  # type: ignore[name-defined]  # noqa: F821
    """Write a .bat wrapper that sets env vars and launches the Agent.

    Task Scheduler's ONLOGON trigger inherits the user's default env, which
    may not include PAPERPRISM_HOME or PAPERPRISM_PORT overrides. Baking them
    into a wrapper batch file guarantees the Agent always sees the right
    values regardless of how the user's environment is configured.
    """
    from paperprism_agent.launchd import load_secrets

    launcher_parts = resolve_launcher()
    launcher_str = _build_tr(launcher_parts, cfg)

    lines = [
        "@echo off",
        f'set PAPERPRISM_HOME={cfg.paths.home}',
        f'set PAPERPRISM_HOST={cfg.host}',
        f'set PAPERPRISM_PORT={cfg.port}',
    ]
    if cfg.token:
        lines.append(f'set PAPERPRISM_TOKEN={cfg.token}')

    secrets, warnings = load_secrets(cfg.paths.secrets_file)
    for key, value in secrets.items():
        lines.append(f'set {key}={value}')
    for warning in warnings:
        log.warning(warning)

    lines.append(launcher_str)

    bat_path = _wrapper_bat_path(cfg)
    bat_path.parent.mkdir(parents=True, exist_ok=True)
    bat_path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    log.info("Wrote wrapper batch file: %s", bat_path)
    return bat_path


def _schtasks(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Run `schtasks <args>` and return the result."""
    return subprocess.run(
        ["schtasks", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _apply_restart_on_failure(
    *,
    interval_seconds: int = 30,
    count: int = 3,
) -> None:
    """Patch the already-registered task XML to add restart-on-failure.

    ``schtasks /Create`` has no CLI flags for restart-on-failure, so the
    standard Windows approach is:

    1. Export the task XML with ``schtasks /Query /XML``.
    2. Inject ``<RestartOnFailure>`` into the ``<Settings>`` element.
    3. Re-import the patched XML with ``schtasks /Create /XML /F``.

    The XML namespace used by Task Scheduler 2.0 is
    ``http://schemas.microsoft.com/windows/2004/02/mit/task``.

    This function is a no-op if the export step fails (e.g. the task
    wasn't registered yet — shouldn't happen in normal flow, but we
    don't want to crash the whole install for this optional enhancement).
    """
    ns = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    ET.register_namespace("", ns)

    # Step 1: export current XML.
    result = _schtasks(["/Query", "/TN", TASK_NAME, "/XML"], check=False)
    if result.returncode != 0 or not result.stdout:
        log.warning(
            "Could not export task XML for restart-on-failure patch "
            "(returncode=%d); skipping.", result.returncode,
        )
        return

    xml_text = result.stdout

    # Step 2: parse and inject / update <RestartOnFailure>.
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.warning("Could not parse task XML: %s; skipping restart-on-failure.", exc)
        return

    settings_tag = f"{{{ns}}}Settings"
    settings_el = root.find(settings_tag)
    if settings_el is None:
        # Shouldn't happen for a well-formed Task Scheduler XML, but guard anyway.
        log.warning("No <Settings> element in task XML; skipping restart-on-failure.")
        return

    restart_tag = f"{{{ns}}}RestartOnFailure"
    interval_tag = f"{{{ns}}}Period"
    count_tag = f"{{{ns}}}Count"

    restart_el = settings_el.find(restart_tag)
    if restart_el is None:
        restart_el = ET.SubElement(settings_el, restart_tag)

    # PT30S  = ISO 8601 duration for 30 seconds.
    interval_iso = f"PT{interval_seconds}S"
    _set_or_create(restart_el, interval_tag, interval_iso, ns)
    _set_or_create(restart_el, count_tag, str(count), ns)

    # Step 3: write patched XML to a temp file and re-import.
    patched_xml = ET.tostring(root, encoding="unicode", xml_declaration=False)
    # Prepend the XML declaration that schtasks expects.
    patched_xml = '<?xml version="1.0" encoding="UTF-16"?>\n' + patched_xml

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".xml",
            encoding="utf-16",
            delete=False,
        ) as tmp:
            tmp.write(patched_xml)
            tmp_path = tmp.name

        _schtasks(["/Create", "/TN", TASK_NAME, "/XML", tmp_path, "/F"])
        log.info(
            "Configured restart-on-failure: %d attempt(s) every %ds.",
            count, interval_seconds,
        )
    except subprocess.CalledProcessError as exc:
        log.warning(
            "Could not re-import patched task XML (restart-on-failure "
            "not active): %s", exc.stderr or exc,
        )
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _set_or_create(parent: ET.Element, tag: str, text: str, ns: str) -> None:
    """Set the text of an existing child element, or create it."""
    child = parent.find(tag)
    if child is None:
        child = ET.SubElement(parent, tag)
    child.text = text


def create_task(cfg: "Config") -> None:  # type: ignore[name-defined]  # noqa: F821
    """Register the scheduled task (overwrites if already exists).

    Uses the .bat wrapper so env vars are always set correctly.
    Configured with:
      - Trigger: ONLOGON for current user
      - Run level: LIMITED (no admin needed)
      - Restart on failure: 3 attempts, 30-second delay (via XML patch)
    """
    bat = write_wrapper_bat(cfg)
    tr = f'"{bat}"' if " " in str(bat) else str(bat)

    # Step 1: create the base task (trigger + action + run-level).
    _schtasks([
        "/Create",
        "/SC", "ONLOGON",
        "/TN", TASK_NAME,
        "/TR", tr,
        "/RL", "LIMITED",
        "/F",           # force overwrite if exists
        "/IT",          # interactive (run only when user is logged on)
    ])

    # Step 2: patch restart-on-failure into the task XML.
    # schtasks /Create has no CLI flags for this setting; the standard
    # Windows approach is to export the XML, inject <RestartOnFailure>,
    # and re-import via /Create /XML /F.
    _apply_restart_on_failure(interval_seconds=30, count=3)

    log.info("Registered Task Scheduler task: %s", TASK_NAME)


def run_task_now() -> None:
    """Start the task immediately (in addition to the ONLOGON trigger)."""
    _schtasks(["/Run", "/TN", TASK_NAME])


def delete_task() -> None:
    """Delete the scheduled task (stop first if running). Idempotent."""
    # /F = force delete without confirmation; tolerates not-found.
    _schtasks(["/Delete", "/TN", TASK_NAME, "/F"], check=False)
    log.info("Deleted Task Scheduler task: %s", TASK_NAME)


def end_task() -> None:
    """Terminate any currently running instance of the task."""
    _schtasks(["/End", "/TN", TASK_NAME], check=False)


def is_running() -> bool:
    """Return True if the task currently has a running instance."""
    result = _schtasks(
        ["/Query", "/TN", TASK_NAME, "/FO", "CSV", "/NH"],
        check=False,
    )
    if result.returncode != 0:
        return False
    # CSV output: "TaskName","Next Run Time","Status"
    # Status values: "Running", "Ready", "Disabled", etc.
    return "Running" in result.stdout


def task_exists() -> bool:
    """Return True if the task is registered (regardless of state)."""
    result = _schtasks(
        ["/Query", "/TN", TASK_NAME],
        check=False,
    )
    return result.returncode == 0


def print_status() -> tuple[int, str]:
    """Return (returncode, status_text) from `schtasks /Query`."""
    result = _schtasks(
        ["/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"],
        check=False,
    )
    return result.returncode, result.stdout or result.stderr


def restart() -> None:
    """Terminate any running instance and immediately re-launch."""
    end_task()
    run_task_now()
