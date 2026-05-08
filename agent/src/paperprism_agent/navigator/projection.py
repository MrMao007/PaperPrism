"""UMAP dimensionality reduction + Procrustes alignment for stability."""
from __future__ import annotations

import logging

import numpy as np
from scipy.spatial import procrustes
import umap

log = logging.getLogger("paperprism.navigator.projection")

UMAP_NEIGHBORS = 30
UMAP_MIN_DIST = 0.1
UMAP_RANDOM_STATE = 42


def fit_umap(embs: np.ndarray, random_state: int = UMAP_RANDOM_STATE) -> np.ndarray:
    """Fit UMAP on (N, 384) embeddings and return (N, 2) coordinates."""
    n = embs.shape[0]
    if n == 0:
        return np.empty((0, 2), dtype=np.float64)
    if n == 1:
        return np.array([[0.0, 0.0]], dtype=np.float64)
    if n == 2:
        # UMAP needs at least 3 points; spread 2 points on a line
        from scipy.spatial.distance import cosine
        d = 1.0 - np.dot(embs[0], embs[1]) / (
            np.linalg.norm(embs[0]) * np.linalg.norm(embs[1]) + 1e-10
        )
        return np.array([[-d / 2, 0.0], [d / 2, 0.0]], dtype=np.float64)

    n_neighbors = min(n - 1, UMAP_NEIGHBORS)
    if n < 6:
        # Too few points for UMAP to be meaningful; use PCA directly
        log.warning(
            "Only %d points; using PCA instead of UMAP.", n,
        )
        from sklearn.decomposition import PCA
        return PCA(n_components=2, random_state=random_state).fit_transform(embs)

    if n < UMAP_NEIGHBORS + 1:
        log.warning(
            "Only %d points; using n_neighbors=%d instead of %d.",
            n, n_neighbors, UMAP_NEIGHBORS,
        )
    try:
        reducer = umap.UMAP(
            n_neighbors=n_neighbors,
            min_dist=UMAP_MIN_DIST,
            random_state=random_state,
        )
        return reducer.fit_transform(embs)
    except Exception as exc:
        log.warning("UMAP failed (%s); falling back to PCA.", exc)
        from sklearn.decomposition import PCA
        return PCA(n_components=2, random_state=random_state).fit_transform(embs)


def align_to_anchor(anchor: np.ndarray, new: np.ndarray) -> np.ndarray:
    """Align ``new`` coordinates to ``anchor`` via Procrustes.

    Both inputs must have the same shape (N, 2).  Returns the aligned
    version of ``new``.
    """
    if anchor.shape != new.shape:
        raise ValueError(
            f"Shape mismatch: anchor {anchor.shape} vs new {new.shape}"
        )
    if anchor.shape[0] < 3:
        # Procrustes needs at least 3 points
        return new
    _, aligned, _ = procrustes(anchor, new)
    return aligned


def project_with_alignment(
    all_embs: np.ndarray,
    anchor_coords: np.ndarray | None = None,
) -> np.ndarray:
    """Fit UMAP on ``all_embs`` and optionally align the first N points to
    ``anchor_coords`` via Procrustes.

    Returns (M, 2) where M = len(all_embs).
    """
    coords = fit_umap(all_embs)
    if anchor_coords is not None:
        n = len(anchor_coords)
        if n <= len(coords):
            aligned = align_to_anchor(anchor_coords, coords[:n])
            coords[:n] = aligned
        else:
            log.warning(
                "Anchor larger than new coords (%d > %d); skipping alignment.",
                n,
                len(coords),
            )
    return coords
