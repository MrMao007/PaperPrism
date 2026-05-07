#!/usr/bin/env python3
"""Populate sqlite-vec with embeddings from arXiv abstracts.

Usage:
    uv run scripts/populate_embeddings.py [N]

N = number of abstracts to fetch and embed (default 5000).
"""
from __future__ import annotations

import os
import sys
import time
import struct
import xml.etree.ElementTree as ET

import requests
from pysqlite3 import dbapi2 as sqlite3
import sqlite_vec
from sentence_transformers import SentenceTransformer

ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}
DB_PATH = os.path.expanduser("~/.paperprism/db.sqlite")
MODEL_NAME = "BAAI/bge-small-en-v1.5"


def fetch_arxiv_abstracts(total: int, batch: int = 500) -> list[tuple[str, str]]:
    """Fetch (arxiv_id, title\nabstract) from arXiv API."""
    seen: set[str] = set()
    items: list[tuple[str, str]] = []
    for start in range(0, total, batch):
        size = min(batch, total - start)
        url = (
            "http://export.arxiv.org/api/query?"
            f"search_query=cat:cs.*&start={start}&max_results={size}"
        )
        print(f"  Fetching {start}–{start + size} …", file=sys.stderr)
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        for entry in root.findall("atom:entry", ARXIV_NS):
            arxiv_id = entry.find("atom:id", ARXIV_NS)
            title = entry.find("atom:title", ARXIV_NS)
            abstract = entry.find("atom:summary", ARXIV_NS)
            aid = (arxiv_id.text or "").split("/")[-1].split("v")[0]
            t = (title.text or "").replace("\n", " ").strip()
            a = (abstract.text or "").replace("\n", " ").strip()
            if aid not in seen:
                seen.add(aid)
                items.append((aid, f"{t}\n{a}"))
        if start + size < total:
            time.sleep(3)
    return items


def store_embeddings(items: list[tuple[str, str]], model: SentenceTransformer) -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)

    # Ensure table exists
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS arxiv_feed_embeddings USING vec0("
        "arxiv_id TEXT PRIMARY KEY, embedding FLOAT[384])"
    )

    texts = [text for _, text in items]
    t0 = time.perf_counter()
    embs = model.encode(texts, show_progress_bar=True, batch_size=32)
    t1 = time.perf_counter()
    print(f"Embedded {len(items)} in {t1 - t0:.1f}s", file=sys.stderr)

    inserted = 0
    for (arxiv_id, _), emb in zip(items, embs):
        blob = struct.pack(f"{len(emb)}f", *emb)
        conn.execute(
            "DELETE FROM arxiv_feed_embeddings WHERE arxiv_id = ?",
            (arxiv_id,),
        )
        conn.execute(
            "INSERT INTO arxiv_feed_embeddings(arxiv_id, embedding) "
            "VALUES (?, ?)",
            (arxiv_id, blob),
        )
        inserted += 1
    conn.commit()
    conn.close()
    return inserted


def verify_knn() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM arxiv_feed_embeddings"
    ).fetchone()[0]
    print(f"Total rows in arxiv_feed_embeddings: {count}")

    # Sample kNN: pick first row as query
    row = conn.execute(
        "SELECT arxiv_id, embedding FROM arxiv_feed_embeddings LIMIT 1"
    ).fetchone()
    if row:
        aid, emb = row
        neighbors = conn.execute(
            "SELECT arxiv_id, distance FROM arxiv_feed_embeddings "
            "WHERE embedding MATCH ? AND k = 5",
            (emb,),
        ).fetchall()
        print(f"kNN for {aid}: {neighbors}")
    conn.close()


if __name__ == "__main__":
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    print(f"Target: {N} abstracts", file=sys.stderr)

    items = fetch_arxiv_abstracts(N)
    print(f"Fetched {len(items)} abstracts", file=sys.stderr)

    if len(items) < 10:
        print("Too few abstracts — aborting.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {MODEL_NAME} …", file=sys.stderr)
    model = SentenceTransformer(MODEL_NAME)

    inserted = store_embeddings(items, model)
    print(f"Stored {inserted} embeddings.")
    verify_knn()
