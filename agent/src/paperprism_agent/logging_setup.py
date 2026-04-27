"""Structured logging for the Agent.

Two sinks:
  - stdout/stderr (so launchd / systemd / foreground terminals see it)
  - rotating files under `<home>/logs/` for long-running installs
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
MAX_BYTES = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 5


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    # Reset any prior handlers so repeated calls stay idempotent.
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(LOG_FORMAT)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    out_file = RotatingFileHandler(
        log_dir / "agent.out.log",
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    out_file.setFormatter(fmt)
    out_file.setLevel(logging.INFO)
    root.addHandler(out_file)

    err_file = RotatingFileHandler(
        log_dir / "agent.err.log",
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    err_file.setFormatter(fmt)
    err_file.setLevel(logging.WARNING)
    root.addHandler(err_file)

    # Tame noisy third-party loggers.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
