"""Smoke tests for the test harness itself.

These prove:

- the ``tmp_home`` fixture really isolates each test under ``tmp_path``;
- the ``db_conn`` fixture applies every shipped migration on a fresh DB;
- the current schema version matches what the codebase ships.

Bump :data:`EXPECTED_SCHEMA_VERSION` whenever a new ``000N_*.sql``
migration lands.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from paperprism_agent.config import Config


# Update when a new migration ships. 0007_arxiv_feed_papers.sql adds
# the arxiv_feed_papers table backing the daily arXiv feed.
EXPECTED_SCHEMA_VERSION = 7


def test_tmp_home_isolation(tmp_home: Config, tmp_path: Path) -> None:
    """tmp_home points at the per-test tmp_path, not the real ~/.paperprism."""
    assert tmp_home.paths.home == tmp_path.resolve()
    assert tmp_home.paths.db_file.parent == tmp_path.resolve()


def test_migrations_apply(db_conn: sqlite3.Connection) -> None:
    """The migration runner applies every shipped .sql file."""
    row = db_conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM schema_version"
    ).fetchone()
    assert row["v"] == EXPECTED_SCHEMA_VERSION


def test_papers_table_exists(db_conn: sqlite3.Connection) -> None:
    """A core table from migration 0001 is reachable after migrations run."""
    rows = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='papers'"
    ).fetchall()
    assert len(rows) == 1


def test_events_table_shape(db_conn: sqlite3.Connection) -> None:
    """0004 ships the events ledger with the canonical 9-column shape."""
    cols = {
        row["name"]: row["type"]
        for row in db_conn.execute("PRAGMA table_info(events)")
    }
    assert cols == {
        "id": "INTEGER",
        "ts": "TEXT",
        "actor": "TEXT",
        "event_type": "TEXT",
        "subject_type": "TEXT",
        "subject_id": "TEXT",
        "related_ids": "TEXT",
        "payload": "TEXT",
        "schema_v": "INTEGER",
    }


def test_papers_has_deleted_at(db_conn: sqlite3.Connection) -> None:
    """0004 adds a soft-delete column so paper.deleted events still JOIN."""
    cols = {row["name"] for row in db_conn.execute("PRAGMA table_info(papers)")}
    assert "deleted_at" in cols
