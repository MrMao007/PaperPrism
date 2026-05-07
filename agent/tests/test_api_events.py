"""Tests for GET /api/events and GET /api/papers/{id}/timeline (T11)."""

from __future__ import annotations

import sqlite3

from paperprism_agent import db, repository, server
from paperprism_agent.events import Event, EventLogger


def _insert_paper(conn: sqlite3.Connection, arxiv_id: str, title: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO papers (full_id, arxiv_id, version, is_legacy, title, pdf_path, vault_dir, sha256, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (f"arxiv:{arxiv_id}", arxiv_id, "v1", 0, title, "/tmp/x.pdf", "/tmp/vault/x", "abc"),
    )
    return int(cur.lastrowid)


def _seed_events(conn: sqlite3.Connection) -> None:
    pid = _insert_paper(conn, "2501.00001", "Test Paper")
    repository.delete_paper(conn, pid, actor="user")
    EventLogger.emit(
        conn,
        Event(
            actor="user",
            event_type="topic.created",
            subject_type="topic",
            subject_id="ml",
            payload={"name": "ML"},
        ),
    )


def test_list_events_basic(tmp_home):
    conn = db.connect(tmp_home.paths.db_file)
    _seed_events(conn)

    app = server.create_app(tmp_home)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/events")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["next_cursor"] is None


def test_list_events_filter_by_subject(tmp_home):
    conn = db.connect(tmp_home.paths.db_file)
    _seed_events(conn)

    app = server.create_app(tmp_home)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/events?subject_type=paper")
    assert resp.status_code == 200
    data = resp.json()
    assert all(e["subject_type"] == "paper" for e in data["items"])


def test_timeline_for_paper(tmp_home):
    conn = db.connect(tmp_home.paths.db_file)
    pid = _insert_paper(conn, "2501.00002", "Timeline Paper")
    repository.delete_paper(conn, pid, actor="user")

    app = server.create_app(tmp_home)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get(f"/api/papers/{pid}/timeline")
    assert resp.status_code == 200
    data = resp.json()
    assert data["paper_id"] == pid
    assert data["arxiv_id"] == "2501.00002"
    assert len(data["events"]) == 1
    assert data["events"][0]["event_type"] == "paper.deleted"


def test_timeline_404_for_missing_paper(tmp_home):
    app = server.create_app(tmp_home)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/papers/99999/timeline")
    assert resp.status_code == 404
