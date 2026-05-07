"""Blind-spot detection via local density contrast (A3 winner)."""
from __future__ import annotations

import logging

import numpy as np
from scipy.spatial.distance import cdist

log = logging.getLogger("paperprism.navigator.blind_spot")
K_NEIGHBOURS = 15


def score_candidates(lib: np.ndarray, cand: np.ndarray) -> np.ndarray:
    """Return a blind-spot score for every candidate.

    Score = (mean density of candidate's K nearest library neighbours)
            / (candidate's own local density)

    Higher = candidate sits in a sparser pocket surrounded by library
    activity — i.e. a meaningful blind spot.
    """
    k = min(K_NEIGHBOURS, lib.shape[0], cand.shape[1] if cand.ndim > 1 else cand.shape[0])
    k = max(k, 1)  # at least 1 neighbour

    if lib.shape[0] < 2:
        log.warning("Library has < 2 points; returning uniform scores.")
        return np.ones(cand.shape[0], dtype=np.float64)

    # 1. Library self-density: mean kNN distance for each library point
    d_lib = cdist(lib, lib, metric="euclidean")
    d_lib_sorted = np.sort(d_lib, axis=1)
    actual_k_lib = min(k, lib.shape[0] - 1)
    lib_density = np.mean(d_lib_sorted[:, 1 : actual_k_lib + 1], axis=1)

    # 2. Candidate-to-library distances
    d_cand_lib = cdist(cand, lib, metric="euclidean")
    d_cand_sorted = np.sort(d_cand_lib, axis=1)
    actual_k_cand = min(k, lib.shape[0])
    cand_knn = d_cand_sorted[:, :actual_k_cand]
    cand_density = np.mean(cand_knn, axis=1)

    # 3. Mean density of each candidate's K nearest library neighbours
    idx = np.argpartition(d_cand_lib, actual_k_cand - 1, axis=1)[:, :actual_k_cand]
    neigh_density = np.array([np.mean(lib_density[i]) for i in idx])

    scores = neigh_density / (cand_density + 1e-9)
    return scores


def find_blind_spots(
    lib: np.ndarray,
    cand: np.ndarray,
    top_n: int = 5,
) -> np.ndarray:
    """Return indices of the top-N blind-spot candidates (highest scores)."""
    scores = score_candidates(lib, cand)
    actual_top = min(top_n, len(scores))
    if actual_top <= 0:
        return np.array([], dtype=int)
    top_idx = np.argpartition(scores, -actual_top)[-actual_top:]
    return top_idx[np.argsort(-scores[top_idx])]
