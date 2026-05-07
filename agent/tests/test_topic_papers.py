"""Tests for topic.papers_added/removed event emission (T10)."""

from __future__ import annotations

import sqlite3

from paperprism_agent import repository


def _insert_paper(conn: sqlite3.Connection, arxiv_id: str, title: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO papers (full_id, arxiv_id, version, is_legacy, title, pdf_path, vault_dir, sha256, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (f"arxiv:{arxiv_id}", arxiv_id, "v1", 0, title, "/tmp/x.pdf", "/tmp/vault/x", "abc"),
    )
    return int(cur.lastrowid)


def test_add_topic_papers_emits_event(db_conn: sqlite3.Connection) -> None:
    tid = repository.create_topic(
        db_conn,
        slug="ml",
        name="ML",
        summary="...",
        model=None,
        source_job_id=None,
        actor="user",
    )
    pids = [_insert_paper(db_conn, f"2501.{i:05d}", f"P{i}") for i in range(3)]

    n = repository.add_topic_papers(db_conn, topic_id=tid, paper_ids=pids, actor="user")
    assert n == 3

    ev = db_conn.execute(
        "SELECT * FROM events WHERE event_type = 'topic.papers_added'"
    ).fetchone()
    assert ev is not None
    assert ev["subject_id"] == "ml"
    assert ev["actor"] == "user"
    import json
    related = json.loads(ev["related_ids"])
    assert len(related) == 3
    assert all(str(p) in related for p in pids)


def test_remove_topic_papers_emits_event(db_conn: sqlite3.Connection) -> None:
    tid = repository.create_topic(
        db_conn,
        slug="cv",
        name="CV",
        summary="...",
        model=None,
        source_job_id=None,
        actor="user",
    )
    pids = [_insert_paper(db_conn, "2501.00010", "P10"), _insert_paper(db_conn, "2501.00011", "P11")]
    repository.add_topic_papers(db_conn, topic_id=tid, paper_ids=pids, actor="user")

    n = repository.remove_topic_papers(db_conn, topic_id=tid, paper_ids=[pids[0]], actor="user")
    assert n == 1

    ev = db_conn.execute(
        "SELECT * FROM events WHERE event_type = 'topic.papers_removed'"
    ).fetchone()
    assert ev is not None
    assert ev["subject_id"] == "cv"
    import json
    related = json.loads(ev["related_ids"])
    assert str(pids[0]) in related


def test_add_duplicate_papers_skips_emit(db_conn: sqlite3.Connection) -> None:
    tid = repository.create_topic(
        db_conn,
        slug="dup",
        name="Dup",
        summary="...",
        model=None,
        source_job_id=None,
        actor="user",
    )
    pids = [_insert_paper(db_conn, "2501.00020", "P20")]
    repository.add_topic_papers(db_conn, topic_id=tid, paper_ids=pids, actor="user")

    # Second add is idempotent
    n = repository.add_topic_papers(db_conn, topic_id=tid, paper_ids=pids, actor="user")
    assert n == 0

    count = db_conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_type = 'topic.papers_added'"
    ).fetchone()[0]
    assert count == 1
