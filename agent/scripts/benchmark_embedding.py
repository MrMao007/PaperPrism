#!/usr/bin/env python3
"""A1 spike: measure local embedding speed (bge-small-en-v1.5).

Usage:
    uv run --with sentence-transformers scripts/benchmark_embedding.py [N]

N = number of abstracts to fetch from arXiv (default 500 for quick test).
"""
from __future__ import annotations

import sys
import time
import xml.etree.ElementTree as ET

import requests

ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}


def fetch_arxiv_abstracts(total: int, batch: int = 500) -> list[str]:
    """Fetch paper title+abstract from arXiv API."""
    texts: list[str] = []
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
            title = entry.find("atom:title", ARXIV_NS)
            abstract = entry.find("atom:summary", ARXIV_NS)
            t = (title.text or "").replace("\n", " ").strip()
            a = (abstract.text or "").replace("\n", " ").strip()
            texts.append(f"{t}\n{a}")
        if start + size < total:
            time.sleep(3)  # arXiv politeness delay
    return texts


def benchmark(texts: list[str]) -> dict:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("BAAI/bge-small-en-v1.5")

    # Warm-up (jit / cache)
    _ = model.encode(texts[:2], show_progress_bar=False)

    # Full index
    t0 = time.perf_counter()
    embs = model.encode(texts, show_progress_bar=True, batch_size=32)
    t1 = time.perf_counter()

    # Incremental batch of 50
    t2 = time.perf_counter()
    _ = model.encode(texts[:50], show_progress_bar=False, batch_size=32)
    t3 = time.perf_counter()

    return {
        "count": len(texts),
        "dim": embs.shape[1],
        "full_time_s": round(t1 - t0, 2),
        "full_pps": round(len(texts) / (t1 - t0), 1),
        "incr_time_s": round(t3 - t2, 2),
        "incr_pps": round(50 / (t3 - t2), 1),
    }


if __name__ == "__main__":
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    print(f"Target: {N} abstracts from arXiv cs.*", file=sys.stderr)
    texts = fetch_arxiv_abstracts(N)
    print(f"Fetched {len(texts)} abstracts", file=sys.stderr)

    if len(texts) < 10:
        print("Too few abstracts — aborting.", file=sys.stderr)
        sys.exit(1)

    print("Embedding with BAAI/bge-small-en-v1.5 …", file=sys.stderr)
    results = benchmark(texts)

    print("\n--- Results ---")
    for k, v in results.items():
        print(f"{k}: {v}")

    # Pass / fail against A1 targets
    print("\n--- A1 Targets ---")
    full_ok = results["full_time_s"] <= 60 if results["count"] >= 1000 else "N/A (<1000 docs)"
    incr_ok = results["incr_time_s"] <= 2
    print(f"Full index ≤60s  : {full_ok}")
    print(f"Incremental ≤2s  : {incr_ok}")
