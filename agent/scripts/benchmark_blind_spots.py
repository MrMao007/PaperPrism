#!/usr/bin/env python3
"""A3 spike: test 3 blind-spot algorithms and output candidates for human rating.

Usage:
    uv run --with scipy scripts/benchmark_blind_spots.py

Reads embeddings from sqlite-vec, treats 500 random papers as "user library",
runs 3 algorithms, prints top-5 blind spots per algorithm with titles.
Rate each 1-5 for "meaningfulness" (related but unexplored).
"""
from __future__ import annotations

import os
import sys
import random
import xml.etree.ElementTree as ET
from typing import Callable

import numpy as np
import requests
from pysqlite3 import dbapi2 as sqlite3
import sqlite_vec
from scipy.spatial.distance import cdist

DB_PATH = os.path.expanduser("~/.paperprism/db.sqlite")
LIB_SIZE = 500
K = 15
random.seed(42)
np.random.seed(42)


def load_data() -> tuple[np.ndarray, list[str], np.ndarray, list[str]]:
    """Return (lib_embs, lib_ids, cand_embs, cand_ids)."""
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    rows = conn.execute(
        "SELECT arxiv_id, embedding FROM arxiv_feed_embeddings"
    ).fetchall()
    conn.close()
    ids = [r[0] for r in rows]
    embs = np.array([np.frombuffer(r[1], dtype=np.float32) for r in rows])
    idx = list(range(len(embs)))
    random.shuffle(idx)
    lib_idx = idx[:LIB_SIZE]
    cand_idx = idx[LIB_SIZE:]
    return (
        embs[lib_idx],
        [ids[i] for i in lib_idx],
        embs[cand_idx],
        [ids[i] for i in cand_idx],
    )


def fetch_titles(arxiv_ids: list[str]) -> dict[str, str]:
    """Batch-fetch titles from arXiv API."""
    titles: dict[str, str] = {}
    for i in range(0, len(arxiv_ids), 50):
        batch = arxiv_ids[i : i + 50]
        url = "http://export.arxiv.org/api/query?id_list=" + ",".join(batch)
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            aid = entry.find("atom:id", ns)
            title = entry.find("atom:title", ns)
            raw = (aid.text or "").split("/")[-1].split("v")[0]
            titles[raw] = (title.text or "").replace("\n", " ").strip()[:120]
    return titles


# ---------- Algorithm 1: Relative Local Density Contrast ----------
def algo_density_contrast(
    lib: np.ndarray, cand: np.ndarray
) -> np.ndarray:
    """Score = candidate density / mean neighbour density in library.
    Higher = candidate sits in a sparser pocket surrounded by library.
    """
    # Library self-distances
    d_lib = cdist(lib, lib, metric="euclidean")
    d_lib_sorted = np.sort(d_lib, axis=1)
    # avg kNN distance for each library point
    lib_density = np.mean(d_lib_sorted[:, 1 : K + 1], axis=1)

    # Candidate-to-library distances
    d_cand_lib = cdist(cand, lib, metric="euclidean")
    d_cand_sorted = np.sort(d_cand_lib, axis=1)
    cand_knn = d_cand_sorted[:, :K]
    cand_density = np.mean(cand_knn, axis=1)

    # For each candidate, average density of its K nearest library points
    idx = np.argpartition(d_cand_lib, K, axis=1)[:, :K]
    neigh_density = np.array([np.mean(lib_density[i]) for i in idx])

    # Ratio: if candidate is sparser than its neighbours, score is high
    scores = neigh_density / (cand_density + 1e-9)
    return scores


# ---------- Algorithm 2: kNN Distance Band ----------
def algo_knn_band(
    lib: np.ndarray, cand: np.ndarray
) -> np.ndarray:
    """Score = how close candidate's nearest-neighbour distance is to
    1.5x the library's mean nearest-neighbour distance.
    Peak score at the sweet spot (related but not identical).
    """
    d_lib = cdist(lib, lib, metric="euclidean")
    d_lib_sorted = np.sort(d_lib, axis=1)
    mean_nn = np.mean(d_lib_sorted[:, 1])
    std_nn = np.std(d_lib_sorted[:, 1])

    d_cand_lib = cdist(cand, lib, metric="euclidean")
    d_min = np.min(d_cand_lib, axis=1)

    target = 1.5 * mean_nn
    scores = np.exp(-0.5 * ((d_min - target) / (std_nn + 1e-9)) ** 2)
    return scores


# ---------- Algorithm 3: Directional Gap ----------
def algo_directional_gap(
    lib: np.ndarray, cand: np.ndarray
) -> np.ndarray:
    """Score = angular distance from candidate to nearest library direction.
    High = candidate points in a direction the library hasn't explored.
    """
    centroid = np.mean(lib, axis=0)
    lib_dirs = lib - centroid
    lib_dirs = lib_dirs / (np.linalg.norm(lib_dirs, axis=1, keepdims=True) + 1e-9)
    cand_dirs = cand - centroid
    cand_dirs = cand_dirs / (np.linalg.norm(cand_dirs, axis=1, keepdims=True) + 1e-9)

    # Cosine similarity between each candidate direction and all library directions
    sim = cand_dirs @ lib_dirs.T  # (n_cand, n_lib)
    max_sim = np.max(sim, axis=1)
    # Angular distance = arccos(sim), but we can work directly with (1 - sim)
    scores = 1.0 - max_sim
    return scores


def run_algo(
    name: str,
    scorer: Callable[[np.ndarray, np.ndarray], np.ndarray],
    lib: np.ndarray,
    cand: np.ndarray,
    cand_ids: list[str],
    titles: dict[str, str],
) -> list[tuple[str, float, str]]:
    scores = scorer(lib, cand)
    top_idx = np.argpartition(scores, -5)[-5:]
    top_idx = top_idx[np.argsort(-scores[top_idx])]
    results = []
    for i in top_idx:
        aid = cand_ids[i]
        results.append((aid, float(scores[i]), titles.get(aid, "???")))
    return results


def main() -> None:
    print("Loading embeddings …", file=sys.stderr)
    lib, lib_ids, cand, cand_ids = load_data()
    print(f"Library: {len(lib)}, Candidates: {len(cand)}", file=sys.stderr)

    print("Fetching titles for top candidates …", file=sys.stderr)
    # Collect all top-candidate IDs first, then fetch titles in one batch
    scores_dc = algo_density_contrast(lib, cand)
    scores_kb = algo_knn_band(lib, cand)
    scores_dg = algo_directional_gap(lib, cand)

    top_ids = set()
    for scores in (scores_dc, scores_kb, scores_dg):
        top_idx = np.argpartition(scores, -5)[-5:]
        for i in top_idx:
            top_ids.add(cand_ids[i])

    titles = fetch_titles(list(top_ids))

    algos = [
        ("1 — Local Density Contrast", algo_density_contrast),
        ("2 — kNN Distance Band", algo_knn_band),
        ("3 — Directional Gap", algo_directional_gap),
    ]

    for name, scorer in algos:
        results = run_algo(name, scorer, lib, cand, cand_ids, titles)
        print(f"\n=== Algorithm {name} ===")
        print(f"{'Rank':>4}  {'arXiv ID':<12}  {'Score':>8}  Title")
        print("-" * 80)
        for rank, (aid, score, title) in enumerate(results, 1):
            print(f"{rank:>4}  {aid:<12}  {score:>8.3f}  {title}")
        print("\nRate each 1-5 (1=random noise, 5=clearly related but new direction)")


if __name__ == "__main__":
    main()
