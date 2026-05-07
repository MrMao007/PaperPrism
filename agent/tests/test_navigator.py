"""Tests for the navigator sub-modules (embedding, projection, blind_spot)."""
from __future__ import annotations

import struct

import numpy as np
import pytest

from paperprism_agent.navigator import blind_spot, projection


# ---------- projection ----------


class TestProjection:
    def test_fit_umap_returns_2d(self):
        embs = np.random.randn(50, 384).astype(np.float32)
        coords = projection.fit_umap(embs, random_state=0)
        assert coords.shape == (50, 2)

    def test_align_to_anchor_same_shape(self):
        anchor = np.random.randn(30, 2).astype(np.float64)
        new = anchor + np.random.randn(*anchor.shape) * 0.1
        aligned = projection.align_to_anchor(anchor, new)
        assert aligned.shape == anchor.shape
        # Aligned should have roughly the same centroid distance
        # (Procrustes preserves structure, just rotates/scales/translates)
        assert np.isclose(np.mean(aligned), np.mean(new), atol=5.0)

    def test_align_to_anchor_mismatched_shape_raises(self):
        a = np.random.randn(10, 2)
        b = np.random.randn(20, 2)
        with pytest.raises(ValueError, match="Shape mismatch"):
            projection.align_to_anchor(a, b)

    def test_project_with_alignment(self):
        embs = np.random.randn(60, 384).astype(np.float32)
        anchor_coords = np.random.randn(40, 2).astype(np.float64)
        coords = projection.project_with_alignment(embs, anchor_coords)
        assert coords.shape == (60, 2)


# ---------- blind_spot ----------


class TestBlindSpot:
    def test_score_candidates_returns_correct_length(self):
        lib = np.random.randn(30, 384).astype(np.float32)
        cand = np.random.randn(10, 384).astype(np.float32)
        scores = blind_spot.score_candidates(lib, cand)
        assert scores.shape == (10,)

    def test_scores_are_positive(self):
        lib = np.random.randn(30, 384).astype(np.float32)
        cand = np.random.randn(10, 384).astype(np.float32)
        scores = blind_spot.score_candidates(lib, cand)
        assert np.all(scores > 0)

    def test_find_blind_spots_top_n(self):
        lib = np.random.randn(50, 384).astype(np.float32)
        cand = np.random.randn(20, 384).astype(np.float32)
        idx = blind_spot.find_blind_spots(lib, cand, top_n=3)
        assert len(idx) == 3
        # Indices should be valid
        assert np.all(idx >= 0)
        assert np.all(idx < 20)

    def test_score_candidates_basic(self):
        """Score returns correct shape and all values positive."""
        rng = np.random.RandomState(42)
        lib = rng.randn(50, 10).astype(np.float32)
        cand = rng.randn(10, 10).astype(np.float32)
        scores = blind_spot.score_candidates(lib, cand)
        assert scores.shape == (10,)
        assert np.all(scores > 0)
        # Top blind spots should return correct count
        idx = blind_spot.find_blind_spots(lib, cand, top_n=3)
        assert len(idx) == 3
        # Scores at top indices should be the highest
        top_scores = sorted(scores, reverse=True)[:3]
        for i in idx:
            assert scores[i] in top_scores
