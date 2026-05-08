"""Regression tests for the feed/classified ledger event types.

Covers the additions made when the daily arXiv feed and LLM
classification gained their own ledger entries:

- ``feed.fetched`` event type with ``subject_type='feed'``
- ``paper.classified`` event type
- ``EventLogger.emit`` / ``emit_many`` accepting the new ``feed`` subject type
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from paperprism_agent.events import (
    Event,
    EventLogger,
    UnknownEventType,
)


def test_emit_feed_fetched(db_conn: sqlite3.Connection) -> None:
    """feed.fetched event is accepted with subject_type='feed' and arbitrary date id."""
    eid = EventLogger.emit(
        db_conn,
        Event(
            actor="system",
            event_type="feed.fetched",
            subject_type="feed",
            subject_id="2026-05-08",
            payload={
                "categories": ["cs.AI", "cs.LG"],
                "total_fetched": 300,
                "new_papers": 250,
                "filtered_library": 12,
            },
        ),
    )
    assert isinstance(eid, int) and eid > 0

    row = db_conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    assert row["actor"] == "system"
    assert row["event_type"] == "feed.fetched"
    assert row["subject_type"] == "feed"
    assert row["subject_id"] == "2026-05-08"
    payload = json.loads(row["payload"])
    assert payload["new_papers"] == 250
    assert payload["categories"] == ["cs.AI", "cs.LG"]


def test_emit_paper_classified(db_conn: sqlite3.Connection) -> None:
    """paper.classified event is accepted with actor='llm' on paper subjects."""
    eid = EventLogger.emit(
        db_conn,
        Event(
            actor="llm",
            event_type="paper.classified",
            subject_type="paper",
            subject_id="2501.00042",
            payload={"model": "gpt-4o-mini", "has_summary": True, "dimension_count": 5},
        ),
    )
    row = db_conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    assert row["actor"] == "llm"
    assert row["event_type"] == "paper.classified"
    assert row["subject_type"] == "paper"
    payload = json.loads(row["payload"])
    assert payload["dimension_count"] == 5


def test_emit_rejects_invalid_subject_type_for_feed(db_conn: sqlite3.Connection) -> None:
    """The validator should still reject typos like 'feeds' / 'Feed'."""
    with pytest.raises(ValueError, match="invalid subject_type"):
        EventLogger.emit(
            db_conn,
            Event(
                actor="system",
                event_type="feed.fetched",
                subject_type="feeds",  # typo
                subject_id="2026-05-08",
            ),
        )


def test_emit_rejects_unknown_feed_event_type(db_conn: sqlite3.Connection) -> None:
    """Only whitelisted feed.* events are allowed."""
    with pytest.raises(UnknownEventType):
        EventLogger.emit(
            db_conn,
            Event(
                actor="system",
                event_type="feed.refreshed",  # not in whitelist
                subject_type="feed",
                subject_id="2026-05-08",
            ),
        )


def test_emit_many_accepts_mixed_feed_and_paper(db_conn: sqlite3.Connection) -> None:
    """Batch emission must validate subject types per-event."""
    n = EventLogger.emit_many(
        db_conn,
        [
            Event(
                actor="system",
                event_type="feed.fetched",
                subject_type="feed",
                subject_id="2026-05-08",
                payload={"new_papers": 10},
            ),
            Event(
                actor="llm",
                event_type="paper.classified",
                subject_type="paper",
                subject_id="2501.00001",
                payload={"model": "x", "has_summary": False, "dimension_count": 0},
            ),
        ],
    )
    assert n == 2
    rows = db_conn.execute(
        "SELECT event_type, subject_type FROM events ORDER BY id"
    ).fetchall()
    assert [(r["event_type"], r["subject_type"]) for r in rows] == [
        ("feed.fetched", "feed"),
        ("paper.classified", "paper"),
    ]
