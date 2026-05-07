"""Performance test for batch event emission (T08).

Verifies that emitting 250 events (50 papers x 5 tags) via emit_many
completes in <= 200 ms of DB time.
"""

from __future__ import annotations

import sqlite3
import time

from paperprism_agent.events import Event, EventLogger


def _insert_paper(conn: sqlite3.Connection, idx: int) -> int:
    cur = conn.execute(
        """
        INSERT INTO papers (full_id, arxiv_id, version, is_legacy, title, pdf_path, vault_dir, sha256, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (f"arxiv:2501.{idx:05d}", f"2501.{idx:05d}", "v1", 0, f"Paper {idx}", "/tmp/x.pdf", "/tmp/vault/x", "abc"),
    )
    return int(cur.lastrowid)


def test_emit_many_250_events_under_200ms(db_conn: sqlite3.Connection) -> None:
    """Batch insert 250 tag.auto_generated events should be fast."""
    paper_ids = [_insert_paper(db_conn, i) for i in range(50)]
    tags = ["deep-learning", "nlp", "cv", "rl", "graphs"]

    events = [
        Event(
            actor="llm",
            event_type="tag.auto_generated",
            subject_type="tag",
            subject_id=tag,
            payload={"paper_id": pid, "model": "test-model"},
        )
        for pid in paper_ids
        for tag in tags
    ]
    assert len(events) == 250

    db_conn.execute("BEGIN")
    t0 = time.perf_counter()
    EventLogger.emit_many(db_conn, events)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    db_conn.execute("COMMIT")

    # PRD target: <= 200 ms DB time
    assert elapsed_ms < 200, f"emit_many took {elapsed_ms:.1f} ms"

    count = db_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 250
