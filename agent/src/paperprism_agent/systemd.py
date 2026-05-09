"""Linux systemd --user service helpers.

Manages a systemd user unit that keeps the PaperPrism Agent running
across logins and auto-restarts on crash. This is the Linux equivalent
of the macOS launchd support in launchd.py.

The unit file lives at:
    ~/.config/systemd/user/paperprism-agent.service

Usage (via the CLI):
    paperprism-agent install    # write unit, enable, start
    paperprism-agent uninstall  # stop, disable, remove unit
    paperprism-agent status     # systemctl --user status
    paperprism-agent restart    # systemctl --user restart

Requirements:
    - systemd >= 232 (systemd --user support, available in all major
      distros since 2016: Ubuntu 16.04, Debian 9, Fedora 25, Arch).
    - The login session must have a systemd --user instance running
      (standard on desktop Linuxes; on headless servers you may need
      `loginctl enable-linger <user>`).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("paperprism.systemd")

UNIT_NAME = "paperprism-agent.service"


def unit_path() -> Path:
    """~/.config/systemd/user/paperprism-agent.service"""
    xdg_config = os.environ.get("XDG_CONFIG_HOME", "")
    config_home = Path(xdg_config).expanduser() if xdg_config else Path.home() / ".config"
    return config_home / "systemd" / "user" / UNIT_NAME


def resolve_launcher() -> list[str]:
    """Pick a stable argv list for the ExecStart= line.

    Mirrors the logic in launchd.resolve_launcher but for systemd:

    1. PyInstaller frozen binary: use the binary directly.
    2. uv tool install (~/.local/share/uv/tools/…): prefer the user-level
       shim at ~/.local/bin/paperprism-agent which survives reinstalls.
    3. Ephemeral uvx cache (~/.cache/uv/…): refuse — the path rotates and
       would silently break the service after a cache GC.
    4. Default: [sys.executable, "-m", "paperprism_agent", "serve"].
    """
    exe = Path(sys.executable)

    # (1) Frozen binary (PyInstaller).
    if getattr(sys, "frozen", False):
        return [str(exe), "serve"]

    home = Path.home()
    uv_tools_root = home / ".local" / "share" / "uv" / "tools"
    uv_cache_root = home / ".cache" / "uv"

    def _is_under(path: Path, base: Path) -> bool:
        try:
            path.resolve().relative_to(base.resolve())
            return True
        except (ValueError, OSError):
            return False

    # (2) uv tool install.
    if _is_under(exe, uv_tools_root):
        user_shim = home / ".local" / "bin" / "paperprism-agent"
        if user_shim.exists():
            return [str(user_shim), "serve"]
        venv_shim = exe.parent / "paperprism-agent"
        if venv_shim.exists():
            return [str(venv_shim), "serve"]

    # (3) Ephemeral uvx cache: refuse.
    if _is_under(exe, uv_cache_root):
        raise RuntimeError(
            f"Detected an ephemeral `uvx` environment at {exe}.\n"
            "systemd needs a stable launcher path, but uvx runs from a cache\n"
            "directory that can be garbage-collected at any time.\n\n"
            "Run this instead:\n"
            "    uv tool install paperprism-agent\n"
            "    paperprism-agent install\n"
        )

    # (4) Default: module invocation.
    return [str(exe), "-m", "paperprism_agent", "serve"]


def build_unit(cfg: "Config") -> str:  # type: ignore[name-defined]  # noqa: F821
    """Render the systemd unit file contents.

    Secrets from ~/.paperprism/secrets.env are injected as individual
    Environment= lines (same allowlist as the launchd path).
    """
    from paperprism_agent.launchd import load_secrets  # reuse the same parser

    launcher_parts = resolve_launcher()
    exec_start = " ".join(launcher_parts)

    env_lines = [
        f"Environment=PAPERPRISM_HOME={cfg.paths.home}",
        f"Environment=PAPERPRISM_HOST={cfg.host}",
        f"Environment=PAPERPRISM_PORT={cfg.port}",
    ]
    if cfg.token:
        env_lines.append(f"Environment=PAPERPRISM_TOKEN={cfg.token}")

    secrets, warnings = load_secrets(cfg.paths.secrets_file)
    for key, value in secrets.items():
        env_lines.append(f"Environment={key}={value}")
    for warning in warnings:
        log.warning(warning)

    env_block = "\n".join(env_lines)

    out_log = cfg.paths.logs / "agent.out.log"
    err_log = cfg.paths.logs / "agent.err.log"

    return f"""[Unit]
Description=PaperPrism Local Agent
Documentation=https://github.com/MrMao007/PaperPrism
After=network.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=10
{env_block}
WorkingDirectory={cfg.paths.home}
StandardOutput=append:{out_log}
StandardError=append:{err_log}

[Install]
WantedBy=default.target
"""


def write_unit(cfg: "Config") -> Path:  # type: ignore[name-defined]  # noqa: F821
    """Write the unit file to disk and return its path."""
    target = unit_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_unit(cfg), encoding="utf-8")
    log.info("Wrote systemd unit: %s", target)
    return target


def _systemctl(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Run `systemctl --user <args>` and return the result."""
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def daemon_reload() -> None:
    _systemctl(["daemon-reload"])


def enable_and_start() -> None:
    """Enable and immediately start the service."""
    daemon_reload()
    _systemctl(["enable", "--now", UNIT_NAME])


def stop_and_disable() -> None:
    """Stop and disable the service (idempotent)."""
    _systemctl(["stop", UNIT_NAME], check=False)
    _systemctl(["disable", UNIT_NAME], check=False)
    daemon_reload()


def remove_unit() -> bool:
    """Delete the unit file. Returns True if it existed."""
    target = unit_path()
    if target.exists():
        target.unlink()
        return True
    return False


def is_active() -> bool:
    """Return True if the unit is currently active (running)."""
    result = _systemctl(["is-active", UNIT_NAME], check=False)
    return result.returncode == 0


def print_status() -> tuple[int, str]:
    """Return (returncode, status_text) from `systemctl --user status`."""
    result = _systemctl(["status", UNIT_NAME], check=False)
    return result.returncode, result.stdout or result.stderr


def restart() -> None:
    """Restart the service (must already be active)."""
    _systemctl(["restart", UNIT_NAME])


def linger_hint() -> str:
    """Return a hint about enabling linger if needed (headless servers)."""
    username = os.environ.get("USER", os.environ.get("LOGNAME", "$(whoami)"))
    return (
        "If the Agent doesn't start at boot on a headless server, enable linger:\n"
        f"    sudo loginctl enable-linger {username}"
    )
