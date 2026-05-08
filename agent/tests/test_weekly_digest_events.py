"""Tests for weekly_digest._fetch_week_events aggregation.

Covers the new feed/classified dimensions added so the weekly research
summary can describe "前沿雷达" and LLM analysis volume.

Also serves as a SQL-injection regression: ``week_start`` is supplied as
a parameter, never f-string interpolated.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from paperprism_agent.events import Event, EventLogger
from paperprism_agent.weekly_digest import _fetch_week_events


def _emit_at(conn: sqlite3.Connection, ts: str, event: Event) -> None:
    """Insert an event with a fixed timestamp (bypasses EventLogger's clock).

    EventLogger always stamps events with ``now()``; for week-bucketing tests
    we need to backdate, so we issue a direct UPDATE after emit.
    """
    eid = EventLogger.emit(conn, event)
    conn.execute("UPDATE events SET ts = ? WHERE id = ?", (ts, eid))


def test_fetch_week_events_counts_feed_and_classified(db_conn: sqlite3.Connection) -> None:
    """feed.fetched and paper.classified events feed the weekly summary."""
    week_start = "2026-05-04"  # a Monday

    # Two feed.fetched days in this week, with distinct new_papers counts
    _emit_at(
        db_conn,
        f"{week_start}T08:00:00Z",
        Event(
            actor="system",
            event_type="feed.fetched",
            subject_type="feed",
            subject_id="2026-05-04",
            payload={"new_papers": 250, "total_fetched": 300, "filtered_library": 5, "categories": ["cs.AI"]},
        ),
    )
    _emit_at(
        db_conn,
        "2026-05-05T08:00:00Z",
        Event(
            actor="system",
            event_type="feed.fetched",
            subject_type="feed",
            subject_id="2026-05-05",
            payload={"new_papers": 180, "total_fetched": 220, "filtered_library": 3, "categories": ["cs.AI"]},
        ),
    )
    # Three paper.classified events in this week
    for arxiv_id in ("2501.0001", "2501.0002", "2501.0003"):
        _emit_at(
            db_conn,
            "2026-05-06T10:00:00Z",
            Event(
                actor="llm",
                event_type="paper.classified",
                subject_type="paper",
                subject_id=arxiv_id,
                payload={"model": "gpt-4o-mini", "has_summary": True, "dimension_count": 5},
            ),
        )

    out = _fetch_week_events(db_conn, week_start)
    assert out["feed_days"] == 2
    assert out["feed_total"] == 250 + 180
    assert out["classified"] == 3


def test_fetch_week_events_excludes_other_weeks(db_conn: sqlite3.Connection) -> None:
    """Events outside the [week_start, week_start+7) window must not be counted."""
    week_start = "2026-05-04"

    # Inside the week
    _emit_at(
        db_conn,
        "2026-05-06T08:00:00Z",
        Event(
            actor="system",
            event_type="feed.fetched",
            subject_type="feed",
            subject_id="2026-05-06",
            payload={"new_papers": 100},
        ),
    )
    # Day before week_start — must be excluded
    _emit_at(
        db_conn,
        "2026-05-03T23:59:59Z",
        Event(
            actor="system",
            event_type="feed.fetched",
            subject_type="feed",
            subject_id="2026-05-03",
            payload={"new_papers": 999},
        ),
    )
    # Exactly week_start + 7 days — boundary, must be excluded (half-open interval)
    _emit_at(
        db_conn,
        "2026-05-11T00:00:00Z",
        Event(
            actor="system",
            event_type="feed.fetched",
            subject_type="feed",
            subject_id="2026-05-11",
            payload={"new_papers": 999},
        ),
    )

    out = _fetch_week_events(db_conn, week_start)
    assert out["feed_days"] == 1
    assert out["feed_total"] == 100


def test_fetch_week_events_empty_returns_zero_dimensions(db_conn: sqlite3.Connection) -> None:
    """No events in the week → all aggregates are 0 / 0.0, never None."""
    out = _fetch_week_events(db_conn, "2026-05-04")
    assert out["ingested"] == 0
    assert out["feed_ingested"] == 0
    assert out["opened"] == 0
    assert out["read_sessions"] == 0
    assert out["read_minutes"] == 0
    assert out["classified"] == 0
    assert out["feed_days"] == 0
    assert out["feed_total"] == 0


def test_fetch_week_events_handles_corrupt_payload(db_conn: sqlite3.Connection) -> None:
    """A feed.fetched event with malformed payload must not crash aggregation.

    The aggregator catches ValueError/TypeError and skips the bad row,
    so feed_days still increments but feed_total stays at 0 for that row.
    """
    week_start = "2026-05-04"
    eid = EventLogger.emit(
        db_conn,
        Event(
            actor="system",
            event_type="feed.fetched",
            subject_type="feed",
            subject_id="2026-05-04",
            payload={"new_papers": 50},
        ),
    )
    # Backdate + corrupt the payload
    db_conn.execute("UPDATE events SET ts = ?, payload = ? WHERE id = ?",
                    (f"{week_start}T08:00:00Z", "not-json{{", eid))

    out = _fetch_week_events(db_conn, week_start)
    assert out["feed_days"] == 1            # row is still counted
    assert out["feed_total"] == 0           # but new_papers is unrecoverable


def test_fetch_week_events_safe_against_sql_injection(db_conn: sqlite3.Connection) -> None:
    """week_start is parameterised, so a malicious string cannot break the SQL.

    If the implementation regressed to f-string interpolation, this would
    raise sqlite3.OperationalError; with parameterisation it just returns
    an empty result set.
    """
    malicious = "2026-05-04'; DROP TABLE events; --"
    out = _fetch_week_events(db_conn, malicious)
    assert out["feed_days"] == 0
    # Table must still exist
    assert db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
    ).fetchone() is not None
