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
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        log.info("paper inserted id=%s full_id=%s", paper_id, full_id)
        return PaperRow(id=paper_id, full_id=full_id)

    # Existing row: refresh filesystem fields only (do not clobber
    # enrichment data from P2.2/P2.3).
    conn.execute(
        """
        UPDATE papers SET
            pdf_path    = ?,
            vault_dir   = ?,
            source_url  = COALESCE(?, source_url),
            abs_url     = COALESCE(?, abs_url),
            sha256      = COALESCE(?, sha256),
            size_bytes  = COALESCE(?, size_bytes)
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


def delete_paper(conn: sqlite3.Connection, paper_id: int) -> bool:
    """Delete a paper and its related tasks/classifications (FK CASCADE).
    Returns True if a row was actually removed."""
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM tasks WHERE paper_id = ?", (paper_id,))
        cur = conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    deleted = cur.rowcount > 0
    if deleted:
        log.info("paper deleted id=%s", paper_id)
    return deleted


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
) -> tuple[list[dict], int]:
    """Return (items, total) with optional FTS search, dimension filters,
    and configurable sort order.  Each item includes a compact
    ``classifications`` dict ``{dim_name: [values]}``."""

    if sort not in _SORT_COLUMNS:
        sort = "ingested_at"
    if order not in ("asc", "desc"):
        order = "desc"

    # --- build dynamic WHERE clauses ---
    where_parts: list[str] = []
    params: list[object] = []

    if q:
        where_parts.append(
            "p.id IN (SELECT rowid FROM papers_fts WHERE papers_fts MATCH ?)"
        )
        params.append(q)

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

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        for key in ("authors_json", "arxiv_categories_json", "affiliations_json"):
            raw = d.pop(key)
            d[key.removesuffix("_json")] = json.loads(raw) if raw else []
        d["classifications"] = cls_map.get(d["id"], {})
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
