"""Runtime paths and filesystem layout for the PaperPrism Agent.

All on-disk state lives under a single `PAPERPRISM_HOME` directory, which
defaults to `~/.paperprism`. The dot-prefix keeps it hidden on macOS and
Linux. On Windows it is conventionally placed under `%USERPROFILE%`.

Layout:

    ~/.paperprism/
    |- runtime.json      # {port, pid, token, version} so CLI and plugin can find us
    |- logs/
    |   |- agent.out.log
    |   \\- agent.err.log
    |- vault/            # the hidden workspace
    |   \\- YYYY/MM/<arxivId>/
    |       |- paper.pdf
    |       \\- meta.json
    \\- db.sqlite        # created by future Phase-2 indexer
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def default_home() -> Path:
    raw = os.environ.get("PAPERPRISM_HOME", "~/.paperprism")
    return Path(raw).expanduser().resolve()


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def vault(self) -> Path:
        return self.home / "vault"

    @property
    def logs(self) -> Path:
        return self.home / "logs"

    @property
    def runtime_file(self) -> Path:
        return self.home / "runtime.json"

    @property
    def pid_file(self) -> Path:
        return self.home / "agent.pid"

    @property
    def db_file(self) -> Path:
        return self.home / "db.sqlite"

    @property
    def dimensions_file(self) -> Path:
        return self.home / "dimensions.yaml"

    @property
    def llm_config_file(self) -> Path:
        return self.home / "llm.yaml"

    @property
    def secrets_file(self) -> Path:
        """User-managed dotenv-style file of secrets (API keys, etc).
        Read at `install` time and baked into the launchd plist so the
        Agent process receives them without the user having to rely on
        shell env (which launchd does not inherit)."""
        return self.home / "secrets.env"

    def ensure(self) -> None:
        """Create every directory we depend on. Idempotent."""
        for p in (self.home, self.vault, self.logs):
            p.mkdir(parents=True, exist_ok=True)


def resolve_vault(hint: str | None, default: Path) -> Path:
    """Resolve the effective vault directory.

    If the extension sent a `vaultPathHint`, we honour it (expanding `~`),
    otherwise fall back to `<home>/vault`.
    """
    if hint:
        return Path(hint).expanduser().resolve()
    return default


def write_runtime(paths: Paths, *, port: int, token: str, pid: int, version: str) -> None:
    payload = {
        "port": port,
        "token": token,
        "pid": pid,
        "version": version,
    }
    paths.runtime_file.write_text(json.dumps(payload, indent=2))


def clear_runtime(paths: Paths) -> None:
    try:
        paths.runtime_file.unlink()
    except FileNotFoundError:
        pass
