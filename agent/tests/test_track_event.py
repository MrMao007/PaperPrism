"""Tests for L1 read-behaviour event tracking (paper.opened / paper.read_session)."""

from __future__ import annotations

import sqlite3

import pytest

from paperprism_agent import db, repository


def test_track_event_paper_opened(db_conn: sqlite3.Connection) -> None:
    repository.track_event(
        db_conn,
        actor="user",
        event_type="paper.opened",
        subject_type="paper",
        subject_id="2501.00001",
        payload={"source": "dashboard_pdf_button"},
    )

    row = db_conn.execute(
        "SELECT actor, event_type, subject_type, subject_id FROM events WHERE subject_id = ?",
        ("2501.00001",),
    ).fetchone()
    assert row is not None
    assert row["actor"] == "user"
    assert row["event_type"] == "paper.opened"
    assert row["subject_type"] == "paper"


def test_track_event_paper_read_session(db_conn: sqlite3.Connection) -> None:
    repository.track_event(
        db_conn,
        actor="user",
        event_type="paper.read_session",
        subject_type="paper",
        subject_id="2501.00002",
        payload={"duration_seconds": 45},
    )

    row = db_conn.execute(
        "SELECT event_type, payload FROM events WHERE subject_id = ?",
        ("2501.00002",),
    ).fetchone()
    assert row is not None
    assert row["event_type"] == "paper.read_session"
    import json
    assert json.loads(row["payload"])["duration_seconds"] == 45


def test_track_event_unknown_type_raises(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="unknown event_type"):
        repository.track_event(
            db_conn,
            actor="user",
            event_type="paper.invalid",
            subject_type="paper",
            subject_id="2501.00003",
        )
