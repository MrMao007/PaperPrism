"""CRUD for `papers` and `classifications`.

Pure sqlite3 — no ORM. Keep the schema close at hand; speed >> sugar.

All write methods return the primary key / affected row count so callers
can keep going without re-querying.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from paperprism_agent.events import Actor, Event, EventLogger, SubjectType

log = logging.getLogger("paperprism.repository")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PaperRow:
    id: int
    full_id: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PaperRow":
        return cls(id=row["id"], full_id=row["full_id"])


def track_event(
    conn: sqlite3.Connection,
    *,
    actor: Actor,
    event_type: str,
    subject_type: str,
    subject_id: str,
    payload: dict[str, Any] | None = None,
) -> int:
    """Append a single L1 read-behaviour event to the ledger.

    Unlike mutation events, this does NOT wrap a business write; it is
    safe to call from server.py for read-only events.
    """
    event = Event(
        actor=actor,
        event_type=event_type,
        subject_type=subject_type,  # type: ignore[arg-type]
        subject_id=subject_id,
        payload=payload,
    )
    return EventLogger.emit(conn, event)


def upsert_paper(
    conn: sqlite3.Connection,
    *,
    full_id: str,
    arxiv_id: str,
    version: str | None,
    is_legacy: bool,
    pdf_path: str,
    vault_dir: str,
    source_url: str | None,
    abs_url: str | None,
    sha256: str | None,
    size_bytes: int | None,
) -> PaperRow:
    """Insert-or-update a papers row keyed by full_id. Returns the row id."""
    row = conn.execute(
        "SELECT id FROM papers WHERE full_id = ?", (full_id,)
    ).fetchone()

    if row is None:
        in_txn = conn.in_transaction
        if not in_txn:
            conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """
                INSERT INTO papers (
                    full_id, arxiv_id, version, is_legacy,
                    pdf_path, vault_dir, source_url, abs_url,
                    sha256, size_bytes, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    full_id, arxiv_id, version, int(is_legacy),
                    pdf_path, vault_dir, source_url, abs_url,
                    sha256, size_bytes, _now(),
                ),
            )
            paper_id = cur.lastrowid
            if not in_txn:
                conn.execute("COMMIT")
        except Exception:
            if not in_txn:
                conn.execute("ROLLBACK")
            raise
        log.info("paper inserted id=%s full_id=%s", paper_id, full_id)
        return PaperRow(id=paper_id, full_id=full_id)

    # Existing row: refresh filesystem fields only (do not clobber
    # enrichment data from P2.2/P2.3). Clear soft-delete if present.
    conn.execute(
        """
        UPDATE papers SET
            pdf_path    = ?,
            vault_dir   = ?,
            source_url  = COALESCE(?, source_url),
            abs_url     = COALESCE(?, abs_url),
            sha256      = COALESCE(?, sha256),
            size_bytes  = COALESCE(?, size_bytes),
            deleted_at  = NULL
        WHERE id = ?
        """,
        (pdf_path, vault_dir, source_url, abs_url, sha256, size_bytes, row["id"]),
    )
    log.info("paper refreshed id=%s full_id=%s", row["id"], full_id)
    return PaperRow(id=row["id"], full_id=full_id)


def mark_enriched(
    conn: sqlite3.Connection,
    *,
    paper_id: int,
    title: str | None,
    authors: list[str] | None,
    abstract: str | None,
    categories: list[str] | None,
    published_at: str | None,
    updated_at_arxiv: str | None,
    venue: str | None,
    code_url: str | None,
    affiliations: list[str] | None,
) -> None:
    conn.execute(
        """
        UPDATE papers SET
            title                = COALESCE(?, title),
            authors_json         = COALESCE(?, authors_json),
            first_author         = COALESCE(?, first_author),
            abstract             = COALESCE(?, abstract),
            arxiv_categories_json = COALESCE(?, arxiv_categories_json),
            published_at         = COALESCE(?, published_at),
            updated_at_arxiv     = COALESCE(?, updated_at_arxiv),
            venue                = COALESCE(?, venue),
            code_url             = COALESCE(?, code_url),
            affiliations_json    = COALESCE(?, affiliations_json),
            enriched_at          = ?
        WHERE id = ?
        """,
        (
            title,
            json.dumps(authors) if authors else None,
            (authors or [None])[0],
            abstract,
            json.dumps(categories) if categories else None,
            published_at,
            updated_at_arxiv,
            venue,
            code_url,
            json.dumps(affiliations) if affiliations else None,
            _now(),
            paper_id,
        ),
    )


def replace_classifications(
    conn: sqlite3.Connection,
    *,
    paper_id: int,
    rows: Iterable[dict[str, Any]],
    model: str,
    classification_version: int,
) -> None:
    """Atomically swap out every classification row for this paper."""
    now = _now()
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM classifications WHERE paper_id = ?", (paper_id,))
        for r in rows:
            conn.execute(
                """
                INSERT INTO classifications
                    (paper_id, dim_name, value, numeric_value, confidence, model, classified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    paper_id,
                    r["dim_name"],
                    str(r["value"]),
                    r.get("numeric_value"),
                    r.get("confidence"),
                    model,
                    now,
                ),
            )
        conn.execute(
            """
            UPDATE papers SET
                classified_at = ?,
                classification_version = ?
            WHERE id = ?
            """,
            (now, classification_version, paper_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def get_paper(conn: sqlite3.Connection, paper_id: int) -> dict | None:
    """Fetch a single paper row by primary key, or None."""
    row = conn.execute(
        "SELECT * FROM papers WHERE id = ?", (paper_id,)
    ).fetchone()
    return dict(row) if row else None


def find_paper_by_sha256(
    conn: sqlite3.Connection, sha256: str
) -> dict | None:
    """Return the first paper matching ``sha256`` or None. Used by the
    upload endpoint to avoid re-ingesting the same PDF twice."""
    if not sha256:
        return None
    row = conn.execute(
        "SELECT * FROM papers WHERE sha256 = ? AND deleted_at IS NULL LIMIT 1", (sha256,)
    ).fetchone()
    return dict(row) if row else None


def find_paper_by_arxiv_id(
    conn: sqlite3.Connection, arxiv_id: str
) -> dict | None:
    """Return the first non-deleted paper matching ``arxiv_id`` or None.
    Used by the feed ingest endpoint to check for duplicates."""
    if not arxiv_id:
        return None
    row = conn.execute(
        "SELECT * FROM papers WHERE arxiv_id = ? LIMIT 1", (arxiv_id,)
    ).fetchone()
    return dict(row) if row else None


def delete_paper(conn: sqlite3.Connection, paper_id: int, *, actor: Actor = "user") -> bool:
    """Soft-delete a paper (set deleted_at). Returns True if a live row
    was actually tombstoned."""
    row = conn.execute(
        "SELECT arxiv_id, title FROM papers WHERE id = ? AND deleted_at IS NULL",
        (paper_id,),
    ).fetchone()
    if row is None:
        return False

    arxiv_id = row["arxiv_id"]
    title = row["title"]
    now = _now()

    conn.execute("BEGIN")
    try:
        conn.execute(
            "UPDATE papers SET deleted_at = ? WHERE id = ?",
            (now, paper_id),
        )
        # Clean up embedding so Map no longer shows this paper
        conn.execute(
            "DELETE FROM paper_embeddings WHERE paper_id = ?",
            (paper_id,),
        )
        EventLogger.emit(
            conn,
            Event(
                actor=actor,
                event_type="paper.deleted",
                subject_type="paper",
                subject_id=arxiv_id,
                payload={"title_at_delete": title},
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    log.info("paper soft-deleted id=%s arxiv_id=%s", paper_id, arxiv_id)
    return True


# Allowed sort columns (whitelist to prevent SQL injection)
_SORT_COLUMNS = {"ingested_at", "published_at", "title"}


def list_papers(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    sort: str = "ingested_at",
    order: str = "desc",
    domain: str | None = None,
    affiliations: str | None = None,
    tag: str | None = None,
    topic_slug: str | None = None,
) -> tuple[list[dict], int]:
    """Return (items, total) with optional FTS search, dimension filters,
    and configurable sort order.  Each item includes a compact
    ``classifications`` dict ``{dim_name: [values]}`` and a ``tags`` list."""

    if sort not in _SORT_COLUMNS:
        sort = "ingested_at"
    if order not in ("asc", "desc"):
        order = "desc"

    # --- build dynamic WHERE clauses ---
    where_parts: list[str] = ["p.deleted_at IS NULL"]
    params: list[object] = []

    if q:
        # Tokenize: split on whitespace, join with OR for FTS5.
        # Each token is wrapped in '"' to enable prefix matching.
        tokens = q.strip().split()
        fts_query = " OR ".join(f'"{t}"*' for t in tokens if t)
        if not fts_query:
            fts_query = '""*'
        # Tag search: any token matches any tag name (substring)
        tag_clauses = []
        tag_params: list[str] = []
        for t in tokens:
            tag_clauses.append("t.name LIKE ?")
            tag_params.append(f"%{t}%")
        tag_sql = " OR ".join(tag_clauses) if tag_clauses else "1=0"
        # LIKE fallback: title or abstract contains any token
        like_clauses = []
        like_params: list[str] = []
        for t in tokens:
            like_clauses.append("(p.title LIKE ? OR p.abstract LIKE ?)")
            like_params.extend([f"%{t}%", f"%{t}%"])
        like_sql = " OR ".join(like_clauses) if like_clauses else "1=0"
        where_parts.append(
            "("
            "  p.id IN (SELECT rowid FROM papers_fts WHERE papers_fts MATCH ?)"
            "  OR p.id IN (SELECT pt.paper_id FROM paper_tags pt"
            f"    JOIN tags t ON t.id = pt.tag_id WHERE {tag_sql})"
            f"  OR ({like_sql})"
            ")"
        )
        params.append(fts_query)
        params.extend(tag_params)
        params.extend(like_params)

    if domain:
        where_parts.append(
            "p.id IN (SELECT paper_id FROM classifications WHERE dim_name='domain' AND value = ?)"
        )
        params.append(domain)

    if affiliations:
        where_parts.append(
            "p.id IN (SELECT paper_id FROM classifications WHERE dim_name='affiliations' AND value = ?)"
        )
        params.append(affiliations)

    if tag:
        # canonical form stored in tags.name
        where_parts.append(
            "p.id IN (SELECT pt.paper_id FROM paper_tags pt "
            "JOIN tags t ON t.id = pt.tag_id WHERE t.name = ?)"
        )
        params.append(_norm_tag(tag))

    if topic_slug:
        where_parts.append(
            "p.id IN (SELECT tp.paper_id FROM topic_papers tp "
            "JOIN topics tt ON tt.id = tp.topic_id WHERE tt.slug = ?)"
        )
        params.append(topic_slug)

    where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

    # --- total count ---
    total = conn.execute(
        f"SELECT COUNT(*) FROM papers p{where_sql}", params
    ).fetchone()[0]

    # --- fetch page ---
    rows = conn.execute(
        f"""
        SELECT p.id, p.full_id, p.arxiv_id, p.version, p.title, p.first_author,
               p.authors_json, p.arxiv_categories_json, p.affiliations_json,
               p.abstract,
               p.venue, p.code_url, p.published_at, p.updated_at_arxiv,
               p.ingested_at, p.enriched_at, p.classified_at, p.abs_url
        FROM papers p
        {where_sql}
        ORDER BY p.{sort} {order}
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()

    paper_ids = [r["id"] for r in rows]

    # --- batch-fetch classifications for the page ---
    cls_map: dict[int, dict[str, list[str]]] = {pid: {} for pid in paper_ids}
    if paper_ids:
        placeholders = ",".join("?" * len(paper_ids))
        cls_rows = conn.execute(
            f"""
            SELECT paper_id, dim_name, value
            FROM classifications
            WHERE paper_id IN ({placeholders})
            ORDER BY paper_id, dim_name, value
            """,
            paper_ids,
        ).fetchall()
        for cr in cls_rows:
            cls_map[cr["paper_id"]].setdefault(cr["dim_name"], []).append(cr["value"])

    # --- batch-fetch tags for the page ---
    tags_map = get_tags_for_papers(conn, paper_ids)

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        for key in ("authors_json", "arxiv_categories_json", "affiliations_json"):
            raw = d.pop(key)
            d[key.removesuffix("_json")] = json.loads(raw) if raw else []
        d["classifications"] = cls_map.get(d["id"], {})
        d["tags"] = tags_map.get(d["id"], [])
        out.append(d)
    return out, total


def list_dimension_values(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Return {dim_name: [distinct values]} for all classified dimensions."""
    rows = conn.execute(
        "SELECT DISTINCT dim_name, value FROM classifications ORDER BY dim_name, value"
    ).fetchall()
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(r["dim_name"], []).append(r["value"])
    return result


# ============================================================================
# Tags & topics
# ============================================================================

import re as _re  # local alias to avoid name clashes

_TAG_SEP_RE = _re.compile(r"[\s_/]+")
_TAG_COLLAPSE_RE = _re.compile(r"-{2,}")
_TAG_STRIP_CHARS = "-.,;:!?()[]{}\"'`"
_SLUG_KEEP_RE = _re.compile(r"[^a-z0-9]+")


def _norm_tag(name: str) -> str:
    """Canonicalise a tag name. Mirrors ``tagger.normalize_tag`` so the
    repository can be used without importing tagger."""
    s = (name or "").strip().lower()
    s = _TAG_SEP_RE.sub("-", s)
    s = _TAG_COLLAPSE_RE.sub("-", s)
    s = s.strip(_TAG_STRIP_CHARS)
    return s


def _slugify(text: str, *, fallback: str = "topic") -> str:
    s = (text or "").strip().lower()
    s = _SLUG_KEEP_RE.sub("-", s).strip("-")
    if not s:
        s = fallback
    return s[:60]


def upsert_tag(
    conn: sqlite3.Connection,
    *,
    name: str,
    display_name: str | None = None,
    description: str | None = None,
) -> int | None:
    """Insert-or-fetch a tag by canonical name. Returns tag id, or None
    if the canonical form is empty (caller should skip)."""
    norm = _norm_tag(name)
    if not norm:
        return None
    row = conn.execute(
        "SELECT id FROM tags WHERE name = ?", (norm,)
    ).fetchone()
    if row is not None:
        # Light upgrade: fill display_name/description if we have better ones.
        if display_name or description:
            conn.execute(
                """
                UPDATE tags SET
                    display_name = COALESCE(display_name, ?),
                    description  = COALESCE(description,  ?)
                WHERE id = ?
                """,
                (display_name, description, row["id"]),
            )
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO tags (name, display_name, description, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (norm, display_name or name.strip() or norm, description, _now()),
    )
    return int(cur.lastrowid)


def add_paper_tags(
    conn: sqlite3.Connection,
    *,
    paper_id: int,
    tag_names: Iterable[str],
    source: str = "user",
    topic_id: int | None = None,
    actor: Actor = "user",
) -> int:
    """Upsert each tag name and link it to the paper. Idempotent: an
    existing (paper_id, tag_id) row is left alone except its source is
    upgraded user>llm so user-added labels win the colour war.

    Returns the number of *new* paper_tags rows inserted.
    """
    if source not in ("llm", "user"):
        source = "user"
    inserted = 0
    conn.execute("BEGIN")
    try:
        paper_row = conn.execute(
            "SELECT arxiv_id FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        arxiv_id = paper_row["arxiv_id"] if paper_row else str(paper_id)
        for raw in tag_names:
            tag_id = upsert_tag(conn, name=raw)
            if tag_id is None:
                continue
            existing = conn.execute(
                "SELECT source FROM paper_tags WHERE paper_id = ? AND tag_id = ?",
                (paper_id, tag_id),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO paper_tags (paper_id, tag_id, source, topic_id, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (paper_id, tag_id, source, topic_id, _now()),
                )
                inserted += 1
                event_type = (
                    "tag.added_by_llm" if source == "llm" else "tag.added_by_user"
                )
                EventLogger.emit(
                    conn,
                    Event(
                        actor=actor,
                        event_type=event_type,
                        subject_type="tag",
                        subject_id=_norm_tag(raw),
                        payload={"paper_id": paper_id, "arxiv_id": arxiv_id},
                    ),
                )
            else:
                # Promote: llm tag later marked as user-added sticks as user.
                if existing["source"] == "llm" and source == "user":
                    conn.execute(
                        "UPDATE paper_tags SET source = 'user' WHERE paper_id = ? AND tag_id = ?",
                        (paper_id, tag_id),
                    )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return inserted


def remove_paper_tag(
    conn: sqlite3.Connection, *, paper_id: int, tag_name: str, actor: Actor = "user"
) -> bool:
    norm = _norm_tag(tag_name)
    if not norm:
        return False
    paper_row = conn.execute(
        "SELECT arxiv_id FROM papers WHERE id = ?", (paper_id,)
    ).fetchone()
    arxiv_id = paper_row["arxiv_id"] if paper_row else str(paper_id)
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """
            DELETE FROM paper_tags
            WHERE paper_id = ?
              AND tag_id IN (SELECT id FROM tags WHERE name = ?)
            """,
            (paper_id, norm),
        )
        if cur.rowcount > 0:
            EventLogger.emit(
                conn,
                Event(
                    actor=actor,
                    event_type="tag.removed_by_user",
                    subject_type="tag",
                    subject_id=norm,
                    payload={"paper_id": paper_id, "arxiv_id": arxiv_id},
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return cur.rowcount > 0


def get_tags_for_paper(
    conn: sqlite3.Connection, paper_id: int
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT t.id, t.name, t.display_name, pt.source, pt.topic_id, pt.created_at
        FROM paper_tags pt
        JOIN tags t ON t.id = pt.tag_id
        WHERE pt.paper_id = ?
        ORDER BY t.name
        """,
        (paper_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_tags_for_papers(
    conn: sqlite3.Connection, paper_ids: list[int]
) -> dict[int, list[dict]]:
    if not paper_ids:
        return {}
    placeholders = ",".join("?" * len(paper_ids))
    rows = conn.execute(
        f"""
        SELECT pt.paper_id, t.id AS tag_id, t.name, t.display_name, pt.source, pt.topic_id
        FROM paper_tags pt
        JOIN tags t ON t.id = pt.tag_id
        WHERE pt.paper_id IN ({placeholders})
        ORDER BY pt.paper_id, t.name
        """,
        paper_ids,
    ).fetchall()
    out: dict[int, list[dict]] = {pid: [] for pid in paper_ids}
    for r in rows:
        out.setdefault(r["paper_id"], []).append(
            {
                "id": r["tag_id"],
                "name": r["name"],
                "display_name": r["display_name"],
                "source": r["source"],
                "topic_id": r["topic_id"],
            }
        )
    return out


def list_tags(conn: sqlite3.Connection) -> list[dict]:
    """All tags with a usage count and source breakdown."""
    rows = conn.execute(
        """
        SELECT t.id, t.name, t.display_name,
               COUNT(pt.paper_id) AS total,
               SUM(CASE WHEN pt.source='llm'  THEN 1 ELSE 0 END) AS llm_count,
               SUM(CASE WHEN pt.source='user' THEN 1 ELSE 0 END) AS user_count
        FROM tags t
        LEFT JOIN paper_tags pt ON pt.tag_id = t.id
        GROUP BY t.id
        ORDER BY total DESC, t.name ASC
        """
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "display_name": r["display_name"],
            "count": int(r["total"] or 0),
            "llm_count": int(r["llm_count"] or 0),
            "user_count": int(r["user_count"] or 0),
        }
        for r in rows
    ]


# ---------------- Topics ----------------

def reserve_unique_topic_slug(conn: sqlite3.Connection, hint: str) -> str:
    """Turn ``hint`` into a slug that is not yet in ``topics.slug``."""
    base = _slugify(hint)
    slug = base
    n = 2
    while conn.execute(
        "SELECT 1 FROM topics WHERE slug = ?", (slug,)
    ).fetchone():
        slug = f"{base}-{n}"
        n += 1
    return slug


def create_topic(
    conn: sqlite3.Connection,
    *,
    slug: str,
    name: str,
    summary: str | None,
    model: str | None,
    source_job_id: str | None,
    actor: Actor = "user",
) -> int:
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """
            INSERT INTO topics (slug, name, summary, model, source_job_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (slug, name, summary, model, source_job_id, _now()),
        )
        topic_id = int(cur.lastrowid)
        EventLogger.emit(
            conn,
            Event(
                actor=actor,
                event_type="topic.created",
                subject_type="topic",
                subject_id=slug,
                payload={"name": name, "summary": summary, "paper_ids": []},
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return topic_id


def add_topic_papers(
    conn: sqlite3.Connection, *, topic_id: int, paper_ids: list[int], actor: Actor = "user"
) -> int:
    inserted = 0
    conn.execute("BEGIN")
    try:
        for pos, pid in enumerate(paper_ids):
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO topic_papers (topic_id, paper_id, position)
                VALUES (?, ?, ?)
                """,
                (topic_id, pid, pos),
            )
            inserted += cur.rowcount
        if inserted > 0:
            topic_row = conn.execute(
                "SELECT slug FROM topics WHERE id = ?", (topic_id,)
            ).fetchone()
            slug = topic_row["slug"] if topic_row else str(topic_id)
            EventLogger.emit(
                conn,
                Event(
                    actor=actor,
                    event_type="topic.papers_added",
                    subject_type="topic",
                    subject_id=slug,
                    related_ids=[str(p) for p in paper_ids],
                    payload={"added_count": inserted, "requested_count": len(paper_ids)},
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return inserted


def remove_topic_papers(
    conn: sqlite3.Connection, *, topic_id: int, paper_ids: list[int], actor: Actor = "user"
) -> int:
    """Remove specific papers from a topic. Returns number of rows deleted."""
    if not paper_ids:
        return 0
    conn.execute("BEGIN")
    try:
        placeholders = ",".join("?" * len(paper_ids))
        cur = conn.execute(
            f"DELETE FROM topic_papers WHERE topic_id = ? AND paper_id IN ({placeholders})",
            (topic_id, *paper_ids),
        )
        removed = cur.rowcount
        if removed > 0:
            topic_row = conn.execute(
                "SELECT slug FROM topics WHERE id = ?", (topic_id,)
            ).fetchone()
            slug = topic_row["slug"] if topic_row else str(topic_id)
            EventLogger.emit(
                conn,
                Event(
                    actor=actor,
                    event_type="topic.papers_removed",
                    subject_type="topic",
                    subject_id=slug,
                    related_ids=[str(p) for p in paper_ids],
                    payload={"removed_count": removed, "requested_count": len(paper_ids)},
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return removed


def backfill_topic_id_on_paper_tags(
    conn: sqlite3.Connection,
    *,
    topic_id: int,
    paper_ids: list[int],
    source_job_id: str | None = None,
) -> int:
    """Set paper_tags.topic_id = topic_id for every (paper_id in set,
    source='llm', topic_id IS NULL) row. source_job_id is unused at the
    DB level (paper_tags doesn't carry it) but callers pass it for logging.
    """
    if not paper_ids:
        return 0
    placeholders = ",".join("?" * len(paper_ids))
    cur = conn.execute(
        f"""
        UPDATE paper_tags
        SET topic_id = ?
        WHERE topic_id IS NULL
          AND source = 'llm'
          AND paper_id IN ({placeholders})
        """,
        [topic_id, *paper_ids],
    )
    return cur.rowcount


def list_topics(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT t.id, t.slug, t.name, t.summary, t.model, t.created_at, t.source_job_id,
               COUNT(tp.paper_id) AS paper_count
        FROM topics t
        LEFT JOIN topic_papers tp ON tp.topic_id = t.id
        WHERE t.is_archived = 0
        GROUP BY t.id
        ORDER BY t.created_at DESC
        """
    ).fetchall()
    topics = [dict(r) for r in rows]
    # Surface every tag attached to the topic -- no truncation.
    for t in topics:
        t["top_tags"] = _top_tags_for_topic(conn, t["id"])
    return topics


def _top_tags_for_topic(conn: sqlite3.Connection, topic_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT t.name, COUNT(pt.paper_id) AS c
        FROM paper_tags pt
        JOIN tags t ON t.id = pt.tag_id
        WHERE pt.topic_id = ?
        GROUP BY t.id
        ORDER BY c DESC, t.name ASC
        """,
        (topic_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def get_topic_by_slug(conn: sqlite3.Connection, slug: str) -> dict | None:
    row = conn.execute(
        """
        SELECT id, slug, name, summary, model, source_job_id, created_at, is_archived
        FROM topics WHERE slug = ?
        """,
        (slug,),
    ).fetchone()
    if row is None:
        return None
    topic = dict(row)
    topic["top_tags"] = _top_tags_for_topic(conn, topic["id"])
    return topic


def get_topic_by_id(conn: sqlite3.Connection, topic_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, slug FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()
    return dict(row) if row else None


def delete_topic(conn: sqlite3.Connection, topic_id: int, *, actor: Actor = "user") -> bool:
    """Remove the topic row. topic_papers cascades; paper_tags.topic_id
    is SET NULL so the LLM-produced labels remain on each paper."""
    row = conn.execute(
        "SELECT slug, name FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()
    if row is None:
        return False
    slug = row["slug"]
    name = row["name"]

    conn.execute("BEGIN")
    try:
        cur = conn.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
        EventLogger.emit(
            conn,
            Event(
                actor=actor,
                event_type="topic.deleted",
                subject_type="topic",
                subject_id=slug,
                payload={"name_at_delete": name},
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return cur.rowcount > 0


# ---------- Navigator / embedding helpers ----------


def list_paper_embeddings(conn: sqlite3.Connection) -> list[dict]:
    """Return every non-deleted paper that has an embedding.

    Each dict has keys: ``paper_id``, ``arxiv_id``, ``title``, ``embedding``
    (embedding is a bytes blob).
    """
    rows = conn.execute(
        """
        SELECT p.id, p.arxiv_id, p.title, e.embedding
        FROM paper_embeddings e
        JOIN papers p ON p.id = e.paper_id
        WHERE p.deleted_at IS NULL
        """
    ).fetchall()
    return [
        {
            "paper_id": r[0],
            "arxiv_id": r[1],
            "title": r[2],
            "embedding": r[3],
        }
        for r in rows
    ]


def upsert_paper_embedding(
    conn: sqlite3.Connection,
    paper_id: int,
    embedding: bytes,
) -> None:
    """Insert or replace a paper embedding in ``paper_embeddings``."""
    conn.execute(
        "DELETE FROM paper_embeddings WHERE paper_id = ?",
        (paper_id,),
    )
    conn.execute(
        "INSERT INTO paper_embeddings(paper_id, embedding) VALUES (?, ?)",
        (paper_id, embedding),
    )


def upsert_arxiv_feed_embedding(
    conn: sqlite3.Connection,
    arxiv_id: str,
    embedding: bytes,
) -> None:
    """Insert or replace an arXiv feed embedding."""
    conn.execute(
        "DELETE FROM arxiv_feed_embeddings WHERE arxiv_id = ?",
        (arxiv_id,),
    )
    conn.execute(
        "INSERT INTO arxiv_feed_embeddings(arxiv_id, embedding) VALUES (?, ?)",
        (arxiv_id, embedding),
    )


def list_arxiv_feed_embeddings(
    conn: sqlite3.Connection, limit: int | None = None
) -> list[dict]:
    """Return arXiv feed embeddings.  Each dict has ``arxiv_id`` and
    ``embedding`` (bytes blob)."""
    sql = "SELECT arxiv_id, embedding FROM arxiv_feed_embeddings"
    params: tuple = ()
    if limit:
        sql += " LIMIT ?"
        params = (limit,)
    rows = conn.execute(sql, params).fetchall()
    return [
        {"arxiv_id": r[0], "embedding": r[1]}
        for r in rows
    ]


def list_arxiv_feed_embeddings_by_ids(
    conn: sqlite3.Connection, arxiv_ids: list[str]
) -> list[dict]:
    """Return feed embeddings for the given arxiv_ids.

    Each dict has ``arxiv_id`` and ``embedding`` (bytes blob).
    """
    if not arxiv_ids:
        return []
    placeholders = ",".join("?" * len(arxiv_ids))
    rows = conn.execute(
        f"SELECT arxiv_id, embedding FROM arxiv_feed_embeddings "
        f"WHERE arxiv_id IN ({placeholders})",
        arxiv_ids,
    ).fetchall()
    return [
        {"arxiv_id": r[0], "embedding": r[1]}
        for r in rows
    ]


def delete_old_arxiv_feed(conn: sqlite3.Connection, before_date: str) -> int:
    """Delete arXiv feed rows older than *before_date* (ISO 8601).
    Returns number of rows deleted."""
    cur = conn.execute(
        "DELETE FROM arxiv_feed_embeddings WHERE rowid IN ("
        "  SELECT rowid FROM arxiv_feed_embeddings WHERE arxiv_id < ?"
        ")",
        (before_date,),
    )
    return cur.rowcount


# ---------- Weekly digest ----------


def list_digests(conn: sqlite3.Connection, limit: int = 8) -> list[dict]:
    """Return the most recent weekly digests, newest first."""
    rows = conn.execute(
        """
        SELECT id, week, week_start, content, user_note, created_at, updated_at
        FROM weekly_digests
        ORDER BY week DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "week": r[1],
            "week_start": r[2],
            "content": r[3],
            "user_note": r[4],
            "created_at": r[5],
            "updated_at": r[6],
        }
        for r in rows
    ]


def update_digest_user_note(
    conn: sqlite3.Connection, digest_id: int, user_note: str
) -> bool:
    """Update the user_note field of a digest. Returns True if found."""
    cur = conn.execute(
        "UPDATE weekly_digests SET user_note = ?, updated_at = datetime('now') WHERE id = ?",
        (user_note, digest_id),
    )
    conn.commit()
    return cur.rowcount > 0
