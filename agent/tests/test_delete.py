"""Tests for soft-delete + paper.deleted event emission (T05)."""

from __future__ import annotations

import sqlite3

import pytest

from paperprism_agent import db, repository


def _insert_paper(conn: sqlite3.Connection, arxiv_id: str, title: str, sha256: str = "abc") -> int:
    """Minimal paper row for testing deletion."""
    cur = conn.execute(
        """
        INSERT INTO papers (full_id, arxiv_id, version, is_legacy, title, pdf_path, vault_dir, sha256, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (f"arxiv:{arxiv_id}", arxiv_id, "v1", 0, title, "/tmp/x.pdf", "/tmp/vault/x", sha256),
    )
    return int(cur.lastrowid)


def test_soft_delete_sets_deleted_at_and_emits_event(db_conn: sqlite3.Connection) -> None:
    pid = _insert_paper(db_conn, "2501.00001", "Test Paper")

    ok = repository.delete_paper(db_conn, pid, actor="user")
    assert ok is True

    # papers row is tombstoned
    row = db_conn.execute("SELECT deleted_at FROM papers WHERE id = ?", (pid,)).fetchone()
    assert row["deleted_at"] is not None

    # one event emitted
    ev = db_conn.execute("SELECT * FROM events WHERE subject_id = ?", ("2501.00001",)).fetchone()
    assert ev is not None
    assert ev["event_type"] == "paper.deleted"
    assert ev["actor"] == "user"
    import json
    assert json.loads(ev["payload"])["title_at_delete"] == "Test Paper"


def test_double_delete_returns_false(db_conn: sqlite3.Connection) -> None:
    pid = _insert_paper(db_conn, "2501.00002", "Another Paper")

    assert repository.delete_paper(db_conn, pid, actor="user") is True
    assert repository.delete_paper(db_conn, pid, actor="user") is False

    # exactly one event
    count = db_conn.execute(
        "SELECT COUNT(*) FROM events WHERE subject_id = ?", ("2501.00002",)
    ).fetchone()[0]
    assert count == 1


def test_list_papers_hides_deleted(db_conn: sqlite3.Connection) -> None:
    pid = _insert_paper(db_conn, "2501.00003", "Visible Paper")
    _insert_paper(db_conn, "2501.00004", "Deleted Paper")
    repository.delete_paper(db_conn, pid + 1, actor="user")

    items, total = repository.list_papers(db_conn)
    arxiv_ids = {r["arxiv_id"] for r in items}
    assert "2501.00003" in arxiv_ids
    assert "2501.00004" not in arxiv_ids
    assert total == 1


def test_find_by_sha256_hides_deleted(db_conn: sqlite3.Connection) -> None:
    _insert_paper(db_conn, "2501.00005", "To Be Deleted", sha256="deadbeef")
    repository.delete_paper(db_conn, 1, actor="user")

    found = repository.find_paper_by_sha256(db_conn, "deadbeef")
    assert found is None


def test_upsert_paper_clears_deleted_at(db_conn: sqlite3.Connection) -> None:
    """Re-ingesting a soft-deleted paper should restore it (T05 regression)."""
    pid = _insert_paper(db_conn, "2501.00006", "Reborn Paper")
    repository.delete_paper(db_conn, pid, actor="user")

    # confirm soft-deleted
    row = db_conn.execute("SELECT deleted_at FROM papers WHERE id = ?", (pid,)).fetchone()
    assert row["deleted_at"] is not None

    # re-ingest the same paper
    repository.upsert_paper(
        db_conn,
        full_id="arxiv:2501.00006",
        arxiv_id="2501.00006",
        version="v1",
        is_legacy=False,
        pdf_path="/tmp/x.pdf",
        vault_dir="/tmp/vault/x",
        source_url="https://arxiv.org/abs/2501.00006",
        abs_url=None,
        sha256="abc",
        size_bytes=1234,
    )

    # deleted_at cleared
    row = db_conn.execute("SELECT deleted_at FROM papers WHERE id = ?", (pid,)).fetchone()
    assert row["deleted_at"] is None

    # list_papers shows it again
    items, total = repository.list_papers(db_conn)
    arxiv_ids = {r["arxiv_id"] for r in items}
    assert "2501.00006" in arxiv_ids
