"""Task queue on top of SQLite.

Contract:
  - `enqueue(paper_id, kind)`: add a pending task (or re-queue existing one).
  - `claim_next()`: atomically pick one ready task and mark it `running`.
  - `complete(task_id)`: mark `ok`.
  - `fail(task_id, error)`: bump attempts; reschedule with exponential
    backoff; after `MAX_ATTEMPTS` move to `dead`.
  - `reset_stale_running()`: on startup, running tasks whose owner died
    are flipped back to pending (crash recovery).

All methods take a `conn` so the caller can batch transactions if needed.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

log = logging.getLogger("paperprism.tasks")

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 30  # grows 30s, 60s, 120s, 240s, 480s


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(conn: sqlite3.Connection, *, paper_id: int, kind: str) -> int:
    """Insert a pending task. If a non-terminal task of the same kind
    already exists for this paper, re-arm it instead of duplicating."""
    existing = conn.execute(
        """
        SELECT id, status FROM tasks
        WHERE paper_id = ? AND kind = ? AND status IN ('pending','running','failed')
        ORDER BY id DESC LIMIT 1
        """,
        (paper_id, kind),
    ).fetchone()

    now = _now()
    if existing:
        conn.execute(
            """
            UPDATE tasks SET
                status = 'pending',
                attempts = 0,
                error = NULL,
                next_attempt_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (now, existing["id"]),
        )
        log.info("task re-armed id=%s paper_id=%s kind=%s", existing["id"], paper_id, kind)
        return existing["id"]

    cur = conn.execute(
        """
        INSERT INTO tasks (paper_id, kind, status, attempts, created_at, updated_at)
        VALUES (?, ?, 'pending', 0, ?, ?)
        """,
        (paper_id, kind, now, now),
    )
    log.info("task enqueued id=%s paper_id=%s kind=%s", cur.lastrowid, paper_id, kind)
    return cur.lastrowid


def claim_next(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Atomically pick the oldest ready task. Returns None if queue empty."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'pending'
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            ORDER BY id ASC
            LIMIT 1
            """,
            (_now(),),
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        conn.execute(
            "UPDATE tasks SET status='running', updated_at=? WHERE id=?",
            (_now(), row["id"]),
        )
        conn.execute("COMMIT")
        return row
    except Exception:
        conn.execute("ROLLBACK")
        raise


def complete(conn: sqlite3.Connection, *, task_id: int) -> None:
    conn.execute(
        "UPDATE tasks SET status='ok', error=NULL, updated_at=? WHERE id=?",
        (_now(), task_id),
    )


def fail(conn: sqlite3.Connection, *, task_id: int, error: str) -> None:
    row = conn.execute(
        "SELECT attempts FROM tasks WHERE id=?", (task_id,)
    ).fetchone()
    attempts = (row["attempts"] if row else 0) + 1
    if attempts >= MAX_ATTEMPTS:
        conn.execute(
            """
            UPDATE tasks SET status='dead', attempts=?, error=?, updated_at=?
            WHERE id=?
            """,
            (attempts, error[:2000], _now(), task_id),
        )
        log.error("task %s moved to dead after %s attempts: %s", task_id, attempts, error)
        return

    delay = BACKOFF_BASE_SECONDS * (2 ** (attempts - 1))
    next_at = (_now_dt() + timedelta(seconds=delay)).isoformat()
    conn.execute(
        """
        UPDATE tasks SET
            status='pending',
            attempts=?,
            error=?,
            next_attempt_at=?,
            updated_at=?
        WHERE id=?
        """,
        (attempts, error[:2000], next_at, _now(), task_id),
    )
    log.warning(
        "task %s failed attempt=%s, retry at %s: %s",
        task_id, attempts, next_at, error,
    )


def reset_stale_running(conn: sqlite3.Connection) -> int:
    """On startup, any 'running' rows are leftovers from a crashed worker."""
    cur = conn.execute(
        "UPDATE tasks SET status='pending', updated_at=? WHERE status='running'",
        (_now(),),
    )
    if cur.rowcount:
        log.info("reset %s stale running tasks to pending", cur.rowcount)
    return cur.rowcount


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) as n FROM tasks GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}
