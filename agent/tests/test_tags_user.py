"""Tests for user tag add/remove event emission (T07)."""

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


def test_add_paper_tag_emits_added_by_user(db_conn: sqlite3.Connection) -> None:
    pid = _insert_paper(db_conn, "2501.00001", "Tag Test Paper")

    n = repository.add_paper_tags(
        db_conn, paper_id=pid, tag_names=["machine-learning", "nlp"], source="user", actor="user"
    )
    assert n == 2

    rows = db_conn.execute(
        "SELECT * FROM events WHERE event_type = 'tag.added_by_user' ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["subject_id"] == "machine-learning"
    assert rows[1]["subject_id"] == "nlp"
    assert rows[0]["payload"] is not None


def test_remove_paper_tag_emits_removed_by_user(db_conn: sqlite3.Connection) -> None:
    pid = _insert_paper(db_conn, "2501.00002", "Remove Tag Paper")
    repository.add_paper_tags(
        db_conn, paper_id=pid, tag_names=["to-remove"], source="user", actor="user"
    )

    ok = repository.remove_paper_tag(db_conn, paper_id=pid, tag_name="to-remove", actor="user")
    assert ok is True

    ev = db_conn.execute(
        "SELECT * FROM events WHERE event_type = 'tag.removed_by_user'"
    ).fetchone()
    assert ev is not None
    assert ev["subject_id"] == "to-remove"


def test_add_existing_tag_skips_emit(db_conn: sqlite3.Connection) -> None:
    pid = _insert_paper(db_conn, "2501.00003", "Dup Tag Paper")
    repository.add_paper_tags(
        db_conn, paper_id=pid, tag_names=["dup"], source="user", actor="user"
    )
    count_before = db_conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_type = 'tag.added_by_user'"
    ).fetchone()[0]

    # Second add of same tag is idempotent
    n = repository.add_paper_tags(
        db_conn, paper_id=pid, tag_names=["dup"], source="user", actor="user"
    )
    assert n == 0
    count_after = db_conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_type = 'tag.added_by_user'"
    ).fetchone()[0]
    assert count_after == count_before


def test_remove_nonexistent_tag_no_emit(db_conn: sqlite3.Connection) -> None:
    pid = _insert_paper(db_conn, "2501.00004", "No Tag Paper")
    ok = repository.remove_paper_tag(db_conn, paper_id=pid, tag_name="missing", actor="user")
    assert ok is False
    count = db_conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_type = 'tag.removed_by_user'"
    ).fetchone()[0]
    assert count == 0
