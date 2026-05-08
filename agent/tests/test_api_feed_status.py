"""Tests for GET /api/feed/status.

The Dashboard uses this endpoint to decide whether to show the
"new arXiv feed available — Open Atlas" toast at most once per day.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from fastapi.testclient import TestClient

from paperprism_agent import db, server


def _insert_feed_paper(conn: sqlite3.Connection, arxiv_id: str, feed_date: str) -> None:
    conn.execute(
        """
        INSERT INTO arxiv_feed_papers (arxiv_id, title, abstract, feed_date, created_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        """,
        (arxiv_id, f"Title {arxiv_id}", "abstract", feed_date),
    )
    conn.commit()


def test_feed_status_empty(tmp_home) -> None:
    """No feed data → ready=false, count=0, today's date echoed back."""
    app = server.create_app(tmp_home)
    client = TestClient(app)

    resp = client.get("/api/feed/status")
    assert resp.status_code == 200
    data = resp.json()

    assert data["ready"] is False
    assert data["count"] == 0
    assert data["date"] == dt.date.today().isoformat()


def test_feed_status_today_ready(tmp_home) -> None:
    """When feed papers exist for today, ready=true and count reflects total."""
    conn = db.connect(tmp_home.paths.db_file)
    today = dt.date.today().isoformat()
    for i in range(3):
        _insert_feed_paper(conn, f"2501.0000{i}", today)

    app = server.create_app(tmp_home)
    client = TestClient(app)

    resp = client.get("/api/feed/status")
    assert resp.status_code == 200
    data = resp.json()

    assert data["ready"] is True
    assert data["count"] == 3
    assert data["date"] == today


def test_feed_status_only_counts_today(tmp_home) -> None:
    """Yesterday's feed papers must not flip ready=true for today."""
    conn = db.connect(tmp_home.paths.db_file)
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    _insert_feed_paper(conn, "2501.99999", yesterday)
    _insert_feed_paper(conn, "2501.99998", yesterday)

    app = server.create_app(tmp_home)
    client = TestClient(app)

    resp = client.get("/api/feed/status")
    data = resp.json()

    assert data["ready"] is False
    assert data["count"] == 0


def test_feed_status_response_shape(tmp_home) -> None:
    """Contract test — Dashboard's TS interface depends on these exact keys."""
    app = server.create_app(tmp_home)
    client = TestClient(app)

    resp = client.get("/api/feed/status")
    data = resp.json()

    assert set(data.keys()) == {"date", "count", "ready"}
    assert isinstance(data["date"], str)
    assert isinstance(data["count"], int)
    assert isinstance(data["ready"], bool)
