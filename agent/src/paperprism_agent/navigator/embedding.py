"""Lazy-loaded SentenceTransformer wrapper + paper re-indexing."""
from __future__ import annotations

import logging
import os
import sqlite3
import struct

import numpy as np
from sentence_transformers import SentenceTransformer
import sqlite_vec

log = logging.getLogger("paperprism.navigator.embedding")

MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMB_DIM = 384
_BATCH = 32

_model: SentenceTransformer | None = None


def _lazy_model() -> SentenceTransformer:
    global _model
    if _model is None:
        # Try local cache first (fast, no network). If the model has never
        # been downloaded, SentenceTransformer raises OSError; we catch it
        # and re-download automatically so new users don't have to run a
        # manual pre-download step (~130 MB, one-time).
        try:
            log.info("Loading embedding model %s from local cache …", MODEL_NAME)
            _model = SentenceTransformer(MODEL_NAME, local_files_only=True)
        except OSError:
            log.info(
                "Embedding model %s not found locally — downloading (~130 MB, one-time) …",
                MODEL_NAME,
            )
            _model = SentenceTransformer(MODEL_NAME, local_files_only=False)
            log.info("Embedding model downloaded and cached.")
    return _model


def encode_batch(texts: list[str], batch_size: int = _BATCH) -> np.ndarray:
    """Return (N, 384) float32 embeddings."""
    if not texts:
        return np.empty((0, EMB_DIM), dtype=np.float32)
    model = _lazy_model()
    return model.encode(
        texts, batch_size=batch_size, show_progress_bar=False, convert_to_numpy=True
    ).astype(np.float32)


def _build_embed_text(title: str, summary: str, tags: list[str], abstract: str) -> str:
    """Build the text sent to the embedding model.

    Priority: title + summary + tags, falling back to abstract when
    summary or tags are not yet available.
    """
    parts: list[str] = [title or ""]
    if summary:
        parts.append(f"Summary: {summary}")
    elif abstract:
        parts.append(f"Abstract: {abstract}")
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")
    return "\n".join(parts)


def _fetch_paper_ctx(conn: sqlite3.Connection, paper_id: int) -> tuple[str, str, list[str], str] | None:
    """Fetch (title, summary, tags, abstract) for a single paper.

    Returns None if the paper does not exist or is deleted.
    """
    row = conn.execute(
        "SELECT title, abstract FROM papers WHERE id = ? AND deleted_at IS NULL",
        (paper_id,),
    ).fetchone()
    if row is None:
        return None
    title, abstract = row[0] or "", row[1] or ""

    # LLM-generated summary (dim_name='summary')
    summary = ""
    srow = conn.execute(
        "SELECT value FROM classifications WHERE paper_id = ? AND dim_name = 'summary'",
        (paper_id,),
    ).fetchone()
    if srow:
        summary = (srow[0] or "").strip()

    # Tags
    tag_rows = conn.execute(
        "SELECT t.name FROM paper_tags pt JOIN tags t ON t.id = pt.tag_id "
        "WHERE pt.paper_id = ? ORDER BY t.name",
        (paper_id,),
    ).fetchall()
    tags = [r[0] for r in tag_rows]

    return title, summary, tags, abstract


def reindex_papers(conn: sqlite3.Connection) -> int:
    """Re-embed all non-deleted papers and store in ``paper_embeddings``.

    Uses title + summary + tags (falling back to abstract when summary
    is unavailable).  Returns number of papers indexed.
    """
    rows = conn.execute(
        "SELECT id FROM papers WHERE deleted_at IS NULL AND abstract IS NOT NULL"
    ).fetchall()

    if not rows:
        log.warning("No papers with abstracts to index.")
        return 0

    ids = [r[0] for r in rows]

    # Batch-fetch summaries
    placeholders = ",".join("?" * len(ids))
    summary_rows = conn.execute(
        f"SELECT paper_id, value FROM classifications "
        f"WHERE dim_name = 'summary' AND paper_id IN ({placeholders})",
        ids,
    ).fetchall()
    summary_map: dict[int, str] = {r[0]: (r[1] or "").strip() for r in summary_rows}

    # Batch-fetch titles & abstracts
    paper_rows = conn.execute(
        f"SELECT id, title, abstract FROM papers WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    paper_map: dict[int, tuple[str, str]] = {r[0]: (r[1] or "", r[2] or "") for r in paper_rows}

    # Batch-fetch tags
    tag_rows = conn.execute(
        f"SELECT pt.paper_id, t.name FROM paper_tags pt "
        f"JOIN tags t ON t.id = pt.tag_id "
        f"WHERE pt.paper_id IN ({placeholders}) ORDER BY pt.paper_id, t.name",
        ids,
    ).fetchall()
    tags_map: dict[int, list[str]] = {}
    for r in tag_rows:
        tags_map.setdefault(r[0], []).append(r[1])

    texts: list[str] = []
    for pid in ids:
        title, abstract = paper_map.get(pid, ("", ""))
        summary = summary_map.get(pid, "")
        tags = tags_map.get(pid, [])
        texts.append(_build_embed_text(title, summary, tags, abstract))

    embs = encode_batch(texts)

    conn.execute("DELETE FROM paper_embeddings")
    for pid, emb in zip(ids, embs):
        blob = struct.pack(f"{EMB_DIM}f", *emb)
        conn.execute(
            "INSERT INTO paper_embeddings(paper_id, embedding) VALUES (?, ?)",
            (pid, blob),
        )
    conn.commit()
    log.info("Indexed %d papers into paper_embeddings.", len(rows))
    return len(rows)


def embed_paper(conn: sqlite3.Connection, paper_id: int) -> bool:
    """Generate embedding for a single paper and upsert it.

    Uses title + summary + tags, falling back to abstract when summary
    is not yet available.  Returns True if embedding was generated.
    """
    from paperprism_agent import repository as repo

    ctx = _fetch_paper_ctx(conn, paper_id)
    if ctx is None:
        log.warning("paper_id=%s not found for embedding", paper_id)
        return False

    title, summary, tags, abstract = ctx
    text = _build_embed_text(title, summary, tags, abstract)
    if not text.strip():
        log.info("paper_id=%s has no text for embedding", paper_id)
        return False

    embs = encode_batch([text])
    blob = struct.pack(f"{EMB_DIM}f", *embs[0])
    repo.upsert_paper_embedding(conn, paper_id, blob)
    conn.commit()
    info_parts: list[str] = [f"paper_id={paper_id}"]
    if summary:
        info_parts.append("summary=yes")
    else:
        info_parts.append("summary=no(fallback abstract)")
    info_parts.append(f"tags={len(tags)}")
    log.info("Embedded %s", ", ".join(info_parts))
    return True
