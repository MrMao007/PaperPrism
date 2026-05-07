"""Unit tests for the Memory Ledger EventLogger."""

from __future__ import annotations

import json
import sqlite3

import pytest

from paperprism_agent.events import (
    Event,
    EventLogger,
    PayloadTooLarge,
    UnknownEventType,
)


def test_emit_basic(db_conn: sqlite3.Connection) -> None:
    eid = EventLogger.emit(
        db_conn,
        Event(
            actor="user",
            event_type="paper.ingested.downloaded",
            subject_type="paper",
            subject_id="2501.00001",
            payload={"source_url": "https://arxiv.org/abs/2501.00001"},
        ),
    )
    assert isinstance(eid, int) and eid > 0

    row = db_conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    assert row["actor"] == "user"
    assert row["event_type"] == "paper.ingested.downloaded"
    assert row["subject_type"] == "paper"
    assert row["subject_id"] == "2501.00001"
    assert row["schema_v"] == 1
    assert row["ts"].endswith("Z")
    payload = json.loads(row["payload"])
    assert payload["source_url"] == "https://arxiv.org/abs/2501.00001"


def test_emit_many(db_conn: sqlite3.Connection) -> None:
    events = [
        Event(
            actor="user",
            event_type="paper.ingested.downloaded",
            subject_type="paper",
            subject_id="2501.00001",
        ),
        Event(
            actor="agent",
            event_type="paper.ingested.uploaded",
            subject_type="paper",
            subject_id="2501.00002",
        ),
    ]
    n = EventLogger.emit_many(db_conn, events)
    assert n == 2

    rows = db_conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0]["subject_id"] == "2501.00001"
    assert rows[1]["subject_id"] == "2501.00002"


def test_emit_many_empty(db_conn: sqlite3.Connection) -> None:
    assert EventLogger.emit_many(db_conn, []) == 0


def test_unknown_event_type(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(UnknownEventType):
        EventLogger.emit(
            db_conn,
            Event(
                actor="user",
                event_type="paper.unknown",
                subject_type="paper",
                subject_id="x",
            ),
        )


def test_invalid_actor(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="invalid actor"):
        EventLogger.emit(
            db_conn,
            Event(
                actor="hacker",
                event_type="paper.deleted",
                subject_type="paper",
                subject_id="x",
            ),
        )


def test_payload_too_large(db_conn: sqlite3.Connection) -> None:
    huge = {"key": "x" * (20 * 1024)}
    with pytest.raises(PayloadTooLarge):
        EventLogger.emit(
            db_conn,
            Event(
                actor="user",
                event_type="paper.ingested.downloaded",
                subject_type="paper",
                subject_id="x",
                payload=huge,
            ),
        )


def test_transaction_rollback(db_conn: sqlite3.Connection) -> None:
    """If the caller rolls back, emitted events must also vanish."""
    db_conn.execute("BEGIN")
    EventLogger.emit(
        db_conn,
        Event(
            actor="user",
            event_type="paper.deleted",
            subject_type="paper",
            subject_id="2501.00001",
        ),
    )
    db_conn.execute("ROLLBACK")

    count = db_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 0


def test_related_ids_json(db_conn: sqlite3.Connection) -> None:
    eid = EventLogger.emit(
        db_conn,
        Event(
            actor="user",
            event_type="topic.papers_added",
            subject_type="topic",
            subject_id="my-topic",
            related_ids=["2501.00001", "2501.00002"],
        ),
    )
    row = db_conn.execute(
        "SELECT related_ids FROM events WHERE id = ?", (eid,)
    ).fetchone()
    parsed = json.loads(row["related_ids"])
    assert parsed == ["2501.00001", "2501.00002"]


def test_payload_canonicalization_sorts_keys(db_conn: sqlite3.Connection) -> None:
    eid = EventLogger.emit(
        db_conn,
        Event(
            actor="user",
            event_type="paper.ingested.downloaded",
            subject_type="paper",
            subject_id="x",
            payload={"z": 1, "a": 2, "m": 3},
        ),
    )
    row = db_conn.execute(
        "SELECT payload FROM events WHERE id = ?", (eid,)
    ).fetchone()
    # compact separators, sorted keys
    assert row["payload"] == '{"a":2,"m":3,"z":1}'
