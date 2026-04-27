"""Agent configuration.

Resolution order (highest precedence first):
  1. CLI flags (passed explicitly into `Config.from_args`)
  2. Environment variables: PAPERPRISM_PORT / PAPERPRISM_TOKEN / ...
  3. Hard-coded defaults
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field

from paperprism_agent.paths import Paths, default_home


DEFAULT_PORT = 17321
DEFAULT_HOST = "127.0.0.1"
DEFAULT_WORKER_POLL = 5.0


@dataclass
class Config:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    token: str = ""
    paths: Paths = field(default_factory=lambda: Paths(home=default_home()))
    worker_enabled: bool = True
    worker_poll_interval: float = DEFAULT_WORKER_POLL

    @classmethod
    def from_args(
        cls,
        *,
        host: str | None = None,
        port: int | None = None,
        token: str | None = None,
        home: str | None = None,
    ) -> "Config":
        resolved_home = (
            default_home()
            if home is None
            else __import__("pathlib").Path(home).expanduser().resolve()
        )
        disabled_env = os.environ.get("PAPERPRISM_WORKER_DISABLED", "").lower()
        worker_enabled = disabled_env not in {"1", "true", "yes", "on"}
        try:
            poll = float(
                os.environ.get("PAPERPRISM_WORKER_POLL", DEFAULT_WORKER_POLL)
            )
        except ValueError:
            poll = DEFAULT_WORKER_POLL
        return cls(
            host=host or os.environ.get("PAPERPRISM_HOST", DEFAULT_HOST),
            port=port or int(os.environ.get("PAPERPRISM_PORT", DEFAULT_PORT)),
            token=token if token is not None else os.environ.get("PAPERPRISM_TOKEN", ""),
            paths=Paths(home=resolved_home),
            worker_enabled=worker_enabled,
            worker_poll_interval=poll,
        )


def generate_token() -> str:
    """Generate a URL-safe random token for local auth."""
    return secrets.token_urlsafe(24)
