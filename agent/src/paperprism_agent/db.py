"""SQLite connection + migration runner.

Design:
  - One long-lived module-level connection (SQLite serialises writers anyway).
  - `check_same_thread=False` because FastAPI dispatches across threads.
  - WAL mode so reads during long writes don't block.
  - `row_factory=sqlite3.Row` for dict-like access in repository.py.
  - Migrations are plain .sql files under `migrations/`; highest version is
    recorded in the `schema_version` table.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import pysqlite3 as _pysqlite3
import sqlite_vec

log = logging.getLogger("paperprism.db")

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_CURRENT_PATH: Path | None = None


def connect(db_path: Path) -> sqlite3.Connection:
    """Return the singleton connection, creating it and running migrations
    on first call. Safe to call many times."""
    global _CONN, _CURRENT_PATH
    with _LOCK:
        if _CONN is not None and _CURRENT_PATH == db_path:
            return _CONN
        if _CONN is not None:
            # Path changed (typically tests); close and reopen.
            _CONN.close()
            _CONN = None

        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = _pysqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,  # we manage transactions explicitly
            timeout=30.0,
        )
        conn.row_factory = _pysqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")

        _migrate(conn)

        _CONN = conn
        _CURRENT_PATH = db_path
        log.info("SQLite opened at %s (WAL, FK on)", db_path)
        return conn


def close() -> None:
    global _CONN, _CURRENT_PATH
    with _LOCK:
        if _CONN is not None:
            _CONN.close()
            _CONN = None
            _CURRENT_PATH = None


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply every migration whose version number is greater than the max
    in `schema_version`."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    current = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM schema_version"
    ).fetchone()["v"]

    pkg = resources.files("paperprism_agent.migrations")
    files = sorted(p for p in pkg.iterdir() if p.name.endswith(".sql"))

    for f in files:
        # filename format NNNN_description.sql
        try:
            version = int(f.name.split("_", 1)[0])
        except ValueError:
            continue
        if version <= current:
            continue
        sql = f.read_text(encoding="utf-8")
        log.info("Applying migration %s", f.name)
        # NOTE: executescript() issues an implicit COMMIT before running and
        # ignores the current isolation_level, so we cannot wrap it in an
        # outer BEGIN/COMMIT. Migrations are idempotent (IF NOT EXISTS) which
        # keeps partial-apply safe.
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
            (version, _now_iso()),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
