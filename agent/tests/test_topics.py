"""Tests for topic CRUD event emission (T09)."""

from __future__ import annotations

import sqlite3

from paperprism_agent import repository


def test_create_topic_emits_created(db_conn: sqlite3.Connection) -> None:
    tid = repository.create_topic(
        db_conn,
        slug="ml-basics",
        name="Machine Learning Basics",
        summary="Intro papers",
        model="test-model",
        source_job_id=None,
        actor="user",
    )
    assert tid > 0

    ev = db_conn.execute(
        "SELECT * FROM events WHERE event_type = 'topic.created'"
    ).fetchone()
    assert ev is not None
    assert ev["subject_id"] == "ml-basics"
    assert ev["actor"] == "user"
    import json
    payload = json.loads(ev["payload"])
    assert payload["name"] == "Machine Learning Basics"


def test_delete_topic_emits_deleted(db_conn: sqlite3.Connection) -> None:
    tid = repository.create_topic(
        db_conn,
        slug="to-delete",
        name="To Delete",
        summary="...",
        model=None,
        source_job_id=None,
        actor="user",
    )
    ok = repository.delete_topic(db_conn, tid, actor="user")
    assert ok is True

    ev = db_conn.execute(
        "SELECT * FROM events WHERE event_type = 'topic.deleted'"
    ).fetchone()
    assert ev is not None
    assert ev["subject_id"] == "to-delete"
    assert ev["actor"] == "user"


def test_double_delete_returns_false(db_conn: sqlite3.Connection) -> None:
    tid = repository.create_topic(
        db_conn,
        slug="double-delete",
        name="Double Delete",
        summary="...",
        model=None,
        source_job_id=None,
    )
    repository.delete_topic(db_conn, tid)
    ok = repository.delete_topic(db_conn, tid)
    assert ok is False

    count = db_conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_type = 'topic.deleted'"
    ).fetchone()[0]
    assert count == 1
