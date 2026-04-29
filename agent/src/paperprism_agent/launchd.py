"""macOS launchd LaunchAgent helpers.

A LaunchAgent runs on user login, stays up as long as the user is
logged in, and can auto-restart on crash. This is exactly the
lifecycle we want for PaperPrism's local Agent.

Generated plist lives at:
    ~/Library/LaunchAgents/com.paperprism.agent.plist

Modern (Big Sur+) launchctl verbs are used: `bootstrap`, `bootout`,
`print`, `enable`. They are idempotent-friendly when paired with the
helper functions here.
"""

from __future__ import annotations

import logging
import os
import plistlib
import stat
import subprocess
import sys
from pathlib import Path

from paperprism_agent.config import Config

log = logging.getLogger("paperprism.launchd")

LABEL = "com.paperprism.agent"

# Env vars we allow to be provisioned through secrets.env -> plist.
# Restrict to a known allowlist so a stray line in secrets.env can't leak
# $PATH overrides or system-sensitive variables into the service env.
_SECRET_ALLOWLIST: set[str] = {
    "OPENAI_API_KEY",
    "DASHSCOPE_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "DEEPINFRA_API_KEY",
    "GROQ_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
}


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _service_target() -> str:
    return f"{_domain()}/{LABEL}"


def _is_under(path: Path, base: Path) -> bool:
    """True if `path` is inside `base` (after resolving). Safe for missing paths."""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def resolve_launcher() -> list[str]:
    """Pick a stable argv prefix for launchd to invoke.

    Launchd needs an absolute, long-lived path; it cannot rely on ``PATH`` the
    way an interactive shell does. We enumerate the known install modes:

    1. **PyInstaller one-file** (``sys.frozen``): invoke the bundled binary
       directly (``[sys.executable]``).
    2. **uv tool install** (``~/.local/share/uv/tools/paperprism-agent/``):
       the venv's python and shim are both stable. Prefer the user-level
       shim ``~/.local/bin/paperprism-agent`` if present (survives
       reinstalls), else fall back to the venv-local shim.
    3. **uvx / uv tool run cache** (``~/.cache/uv/``): this path rotates on
       cache GC and would break launchd silently. Refuse and point the user
       at ``uv tool install`` (persistent) instead.
    4. **Plain Python venv / pipx / system interpreter**: use
       ``[sys.executable, "-m", "paperprism_agent"]`` which works for any
       site-packages layout that has the package importable.

    The caller is expected to append the subcommand (e.g. ``serve``).
    """
    exe = Path(sys.executable)

    # (1) Frozen binary.
    if getattr(sys, "frozen", False):
        return [str(exe), "serve"]

    home = Path.home()
    uv_tools_root = home / ".local" / "share" / "uv" / "tools"
    uv_cache_root = home / ".cache" / "uv"

    # (2) uv tool install.
    if _is_under(exe, uv_tools_root):
        user_shim = home / ".local" / "bin" / "paperprism-agent"
        if user_shim.exists():
            return [str(user_shim), "serve"]
        venv_shim = exe.parent / "paperprism-agent"
        if venv_shim.exists():
            return [str(venv_shim), "serve"]
        # Falls through to the default path below, which still works.

    # (3) Ephemeral uvx cache: refuse.
    if _is_under(exe, uv_cache_root):
        raise RuntimeError(
            f"Detected an ephemeral `uvx` environment at {exe}.\n"
            "Launchd needs a stable launcher path, but uvx runs from a cache "
            "directory that can be garbage-collected at any time.\n\n"
            "Run this instead:\n"
            "    uv tool install paperprism-agent\n"
            "    paperprism-agent install\n"
        )

    # (4) Default: module invocation via the current interpreter.
    return [str(exe), "-m", "paperprism_agent", "serve"]


def build_plist(cfg: Config) -> dict:
    """Assemble the plist document.

    The launcher argv is chosen by :func:`resolve_launcher`, which picks a
    stable path appropriate for the current install mode (frozen binary,
    ``uv tool``, or a regular venv). Ephemeral ``uvx`` invocations are
    rejected because their paths rotate and would quietly break launchd.

    If ``~/.paperprism/secrets.env`` exists, allowlisted keys from it are
    baked into ``EnvironmentVariables`` so the Agent subprocess sees them.
    """
    program_arguments = resolve_launcher()

    env = {
        # launchd's default PATH is very thin; pad it with the usual suspects
        # so subprocesses (git, node, pdftotext, ...) resolve correctly later.
        "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
        "PAPERPRISM_HOME": str(cfg.paths.home),
        "PAPERPRISM_HOST": cfg.host,
        "PAPERPRISM_PORT": str(cfg.port),
    }
    if cfg.token:
        env["PAPERPRISM_TOKEN"] = cfg.token

    secrets, warnings = load_secrets(cfg.paths.secrets_file)
    for key, value in secrets.items():
        env[key] = value
    for w in warnings:
        log.warning(w)

    return {
        "Label": LABEL,
        "ProgramArguments": program_arguments,
        "RunAtLoad": True,
        # Restart on crash, but not on a clean exit (lets us stop it cleanly).
        "KeepAlive": {
            "SuccessfulExit": False,
            "Crashed": True,
        },
        # Floor on respawn rate; prevents hot-loop on a bad config.
        "ThrottleInterval": 10,
        "EnvironmentVariables": env,
        "WorkingDirectory": str(cfg.paths.home),
        # These files are ONLY launchd's capture of stdout/stderr; the app's
        # structured log goes to `agent.out.log` / `agent.err.log` via our
        # rotating file handlers in logging_setup.py.
        "StandardOutPath": str(cfg.paths.logs / "launchd.out.log"),
        "StandardErrorPath": str(cfg.paths.logs / "launchd.err.log"),
        "ProcessType": "Interactive",
    }


def load_secrets(path: Path) -> tuple[dict[str, str], list[str]]:
    """Parse a simple dotenv-style file: KEY=value per line; `#` comments.

    Returns (secrets_dict, warnings). Unknown keys outside the allowlist
    are ignored (with a warning) to keep the service env tight. The file
    must be mode 600 or we refuse to read it.
    """
    if not path.exists():
        return {}, []

    warnings: list[str] = []
    try:
        st = path.stat()
    except OSError as exc:
        return {}, [f"cannot stat secrets file {path}: {exc}"]

    # Enforce 600 to avoid accidentally group/world-readable secret files.
    perm = stat.S_IMODE(st.st_mode)
    if perm & 0o077:
        return {}, [
            f"refusing to read {path}: permissions are {oct(perm)}, "
            f"must be 0600 (run: chmod 600 {path})"
        ]

    out: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            warnings.append(f"{path}:{lineno}: no '=' in line; skipped")
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if key not in _SECRET_ALLOWLIST:
            warnings.append(
                f"{path}:{lineno}: {key!r} not in allowlist; ignored"
            )
            continue
        out[key] = value
    return out, warnings


def upsert_secret(path: Path, key: str, value: str) -> None:
    """Insert or update a single ``KEY=value`` line in a dotenv-style file.

    Preserves all other lines (including comments). Rejects keys outside the
    allowlist. Writes with mode 600. If the file does not exist, creates it.
    """
    if key not in _SECRET_ALLOWLIST:
        raise ValueError(
            f"refusing to write secret {key!r}: not in allowlist "
            f"({', '.join(sorted(_SECRET_ALLOWLIST))})"
        )
    lines: list[str] = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
    replaced = False
    new_line = f"{key}={value}"
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k = stripped.partition("=")[0].strip()
        if k == key:
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        lines.append(new_line)
    # Ensure trailing newline for POSIX friendliness.
    text = "\n".join(lines).rstrip("\n") + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def secret_allowlist() -> frozenset[str]:
    """Return the set of environment variable names we are willing to
    persist via ``secrets.env``."""
    return frozenset(_SECRET_ALLOWLIST)


def write_plist(cfg: Config) -> Path:
    doc = build_plist(cfg)
    target = plist_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as f:
        plistlib.dump(doc, f)
    # plist contains baked-in secrets; lock it down.
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def is_loaded() -> bool:
    result = subprocess.run(
        ["launchctl", "print", _service_target()],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def bootstrap() -> None:
    """Load the plist into launchd; idempotent via prior bootout."""
    if is_loaded():
        bootout()
    subprocess.run(
        ["launchctl", "bootstrap", _domain(), str(plist_path())],
        check=True,
    )
    # enable is a no-op if already enabled; tolerate non-zero exit.
    subprocess.run(
        ["launchctl", "enable", _service_target()],
        check=False,
        capture_output=True,
    )


def bootout() -> None:
    subprocess.run(
        ["launchctl", "bootout", _service_target()],
        check=False,
        capture_output=True,
    )


def print_status() -> tuple[int, str]:
    result = subprocess.run(
        ["launchctl", "print", _service_target()],
        capture_output=True,
        text=True,
    )
    return result.returncode, (result.stdout or result.stderr)


def remove_plist() -> bool:
    target = plist_path()
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False


def kickstart() -> None:
    """Force an immediate (re)start of the service."""
    subprocess.run(
        ["launchctl", "kickstart", "-k", _service_target()],
        check=False,
        capture_output=True,
    )
