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
        log.info("Loading embedding model %s …", MODEL_NAME)
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def encode_batch(texts: list[str], batch_size: int = _BATCH) -> np.ndarray:
    """Return (N, 384) float32 embeddings."""
    if not texts:
        return np.empty((0, EMB_DIM), dtype=np.float32)
    model = _lazy_model()
    return model.encode(
        texts, batch_size=batch_size, show_progress_bar=False, convert_to_numpy=True
    ).astype(np.float32)


def reindex_papers(conn: sqlite3.Connection) -> int:
    """Re-embed all non-deleted papers with abstracts and store in
    ``paper_embeddings``.  Returns number of papers indexed."""
    rows = conn.execute(
        "SELECT id, title, abstract FROM papers "
        "WHERE deleted_at IS NULL AND abstract IS NOT NULL"
    ).fetchall()

    if not rows:
        log.warning("No papers with abstracts to index.")
        return 0

    texts = [f"{title or ''}\n{abstract or ''}" for _id, title, abstract in rows]
    embs = encode_batch(texts)

    conn.execute("DELETE FROM paper_embeddings")
    for (_id, _title, _abstract), emb in zip(rows, embs):
        blob = struct.pack(f"{EMB_DIM}f", *emb)
        conn.execute(
            "INSERT INTO paper_embeddings(paper_id, embedding) VALUES (?, ?)",
            (_id, blob),
        )
    conn.commit()
    log.info("Indexed %d papers into paper_embeddings.", len(rows))
    return len(rows)
