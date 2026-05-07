#!/usr/bin/env python3
"""A2 spike: measure UMAP layout stability when adding new papers.

Usage:
    uv run --with umap-learn scripts/benchmark_umap_stability.py

Reads embeddings from sqlite-vec, projects 500 papers, then 550 (500+50),
and measures neighbour-rank changes of the original 500.
"""
from __future__ import annotations

import os
import sys

import numpy as np
from pysqlite3 import dbapi2 as sqlite3
import sqlite_vec
from sklearn.neighbors import NearestNeighbors

DB_PATH = os.path.expanduser("~/.paperprism/db.sqlite")
SEED_SIZE = 500
INCREMENT = 50
K = 10
RANDOM_STATE = 42


def load_embeddings(limit: int) -> np.ndarray:
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    rows = conn.execute(
        "SELECT embedding FROM arxiv_feed_embeddings LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return np.array([np.frombuffer(r[0], dtype=np.float32) for r in rows])


def neighbour_ranks(coords: np.ndarray, candidates: np.ndarray, k: int) -> np.ndarray:
    """Return (n, k) array of neighbour indices in candidates ranked by distance."""
    nbrs = NearestNeighbors(n_neighbors=k, metric="euclidean").fit(candidates)
    distances, indices = nbrs.kneighbors(coords)
    return indices


def mean_rank_change(old_ranks: np.ndarray, new_ranks: np.ndarray, k: int) -> float:
    """For a single point, compute mean absolute rank change of shared neighbours."""
    # old_ranks and new_ranks are both length-k arrays of neighbour indices
    old_pos = {idx: pos for pos, idx in enumerate(old_ranks)}
    new_pos = {idx: pos for pos, idx in enumerate(new_ranks)}
    shared = set(old_ranks) & set(new_ranks)
    if not shared:
        return float(k)
    changes = []
    for idx in shared:
        changes.append(abs(old_pos[idx] - new_pos[idx]))
    return sum(changes) / len(changes)


def main() -> None:
    print(f"Loading {SEED_SIZE + INCREMENT} embeddings …", file=sys.stderr)
    embs = load_embeddings(SEED_SIZE + INCREMENT)
    print(f"Shape: {embs.shape}", file=sys.stderr)

    seed_embs = embs[:SEED_SIZE]
    full_embs = embs[: SEED_SIZE + INCREMENT]

    import umap

    print("UMAP fit_transform on seed …", file=sys.stderr)
    reducer_seed = umap.UMAP(n_neighbors=30, min_dist=0.1, random_state=RANDOM_STATE)
    coords_seed = reducer_seed.fit_transform(seed_embs)

    print("UMAP fit_transform on seed+increment …", file=sys.stderr)
    reducer_full = umap.UMAP(n_neighbors=30, min_dist=0.1, random_state=RANDOM_STATE)
    coords_full = reducer_full.fit_transform(full_embs)
    coords_full_seed = coords_full[:SEED_SIZE]

    # Procrustes: remove global rotation/translation/scaling
    from scipy.spatial import procrustes
    coords_seed_aligned, coords_full_aligned, _ = procrustes(coords_seed, coords_full_seed)

    # Compare neighbour ranks among the original 500 only (ignore the 50 new points)
    ranks_seed = neighbour_ranks(coords_seed_aligned, coords_seed_aligned, K)
    ranks_full = neighbour_ranks(coords_full_aligned, coords_full_aligned, K)

    mrcs = []
    for i in range(SEED_SIZE):
        mrc = mean_rank_change(ranks_seed[i], ranks_full[i], K)
        mrcs.append(mrc)

    mrcs = np.array(mrcs)
    moved = np.sum(mrcs > 3)
    pct_moved = moved / SEED_SIZE * 100

    print("\n--- A2 Results ---")
    print(f"Seed size      : {SEED_SIZE}")
    print(f"Increment      : {INCREMENT}")
    print(f"K              : {K}")
    print(f"Mean rank change: {mrcs.mean():.2f}")
    print(f"Median rank change: {np.median(mrcs):.2f}")
    print(f"Max rank change : {mrcs.max():.2f}")
    print(f"Points with >3 rank change: {moved} / {SEED_SIZE} ({pct_moved:.1f}%)")
    print(f"\n--- A2 Target ---")
    print(f"≤10% points move >3 ranks: {'PASS' if pct_moved <= 10 else 'FAIL'}")


if __name__ == "__main__":
    main()
