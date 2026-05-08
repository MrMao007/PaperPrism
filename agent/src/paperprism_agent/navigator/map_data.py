"""Assemble the JSON payload for ``GET /api/map``."""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading

import numpy as np

from paperprism_agent import repository
from paperprism_agent.navigator import blind_spot, projection

log = logging.getLogger("paperprism.navigator.map_data")
_MAX_FEED = 200
_MAX_BLIND = 5

# Process-local cache for the UMAP projection. ``fit_umap`` is by far
# the most expensive step in /api/map (~300 ms even on small inputs)
# and it is fully deterministic for a given input matrix because the
# random_state is fixed. Polling clients (e.g. the Atlas page that
# refreshes every 5 s) hit /api/map repeatedly with the same
# embeddings between actual library mutations, so caching the last
# result drops steady-state cost from O(UMAP) to O(hash).
#
# The cache holds at most one entry; it is invalidated automatically
# whenever the input matrix changes (different shape or different
# bytes ⇒ different key). A lock guards concurrent /api/map calls
# against duplicating UMAP work on a cold cache.
_umap_cache_lock = threading.Lock()
_umap_cache_key: tuple[int, int, str] | None = None
_umap_cache_value: np.ndarray | None = None


def _umap_cache_key_for(all_embs: np.ndarray) -> tuple[int, int, str]:
    """Deterministic key for the UMAP cache: shape + content hash."""
    digest = hashlib.sha256(all_embs.tobytes()).hexdigest()
    return (all_embs.shape[0], all_embs.shape[1], digest)


def _cached_fit_umap(all_embs: np.ndarray) -> np.ndarray:
    """Return UMAP coordinates, reusing the previous result if the input
    matrix is byte-identical to the last call."""
    global _umap_cache_key, _umap_cache_value
    key = _umap_cache_key_for(all_embs)
    with _umap_cache_lock:
        if _umap_cache_key == key and _umap_cache_value is not None:
            log.debug("UMAP cache hit (n=%d)", all_embs.shape[0])
            return _umap_cache_value
        log.debug("UMAP cache miss (n=%d) — recomputing", all_embs.shape[0])
        coords = projection.fit_umap(all_embs)
        _umap_cache_key = key
        _umap_cache_value = coords
        return coords


def _load_trajectory(conn: sqlite3.Connection, days: int = 30) -> list[dict]:
    """Recent ledger events that form the red line."""
    rows = conn.execute(
        f"""
        SELECT subject_id, ts, event_type
        FROM events
        WHERE event_type IN ('paper.opened', 'paper.read_session')
          AND ts >= datetime('now', '-{days} days')
        ORDER BY ts
        """
    ).fetchall()
    return [
        {"arxiv_id": r[0], "ts": r[1], "event_type": r[2]}
        for r in rows
    ]


def build_map_data(conn: sqlite3.Connection) -> dict:
    """Return the complete map payload.

    Keys: ``library``, ``trajectory``, ``feed_hits``, ``blind_spots``.
    """
    lib_rows = repository.list_paper_embeddings(conn)

    # Build a set of arxiv_ids already in the user's library so we can
    # exclude them from feed_hits.  Otherwise a paper that the user
    # added via Atlas "Add to Library" would still appear today as a
    # blue feed star next to its golden library star (and the
    # trajectory line would be drawn against an inconsistent snapshot).
    library_arxiv_ids: set[str] = {r["arxiv_id"] for r in lib_rows if r["arxiv_id"]}

    # Only fetch today's feed papers, minus anything already in the library.
    feed_papers = conn.execute(
        "SELECT arxiv_id, title, abstract FROM arxiv_feed_papers WHERE feed_date = date('now')"
    ).fetchall()
    feed_papers = [r for r in feed_papers if r[0] not in library_arxiv_ids]
    feed_title_map: dict[str, str] = {r[0]: r[1] or "" for r in feed_papers}
    feed_abstract_map: dict[str, str] = {r[0]: r[2] or "" for r in feed_papers}
    feed_ids = [r[0] for r in feed_papers]
    feed_rows = repository.list_arxiv_feed_embeddings_by_ids(conn, feed_ids)

    if not lib_rows:
        return {
            "library": [],
            "trajectory": [],
            "feed_hits": [],
            "blind_spots": [],
        }

    # ---- embeddings → numpy ----
    lib_embs = np.array(
        [np.frombuffer(r["embedding"], dtype=np.float32) for r in lib_rows]
    )
    feed_embs = (
        np.array(
            [np.frombuffer(r["embedding"], dtype=np.float32) for r in feed_rows]
        )
        if feed_rows
        else np.empty((0, lib_embs.shape[1]), dtype=np.float32)
    )

    # ---- UMAP projection (cached on input bytes) ----
    all_embs = np.vstack([lib_embs, feed_embs])
    coords = _cached_fit_umap(all_embs)
    lib_coords = coords[: len(lib_rows)]
    feed_coords = coords[len(lib_rows) :]

    # ---- library points ----
    library = [
        {
            "id": lib_rows[i]["paper_id"],
            "arxiv_id": lib_rows[i]["arxiv_id"],
            "x": float(lib_coords[i][0]),
            "y": float(lib_coords[i][1]),
            "title": lib_rows[i]["title"] or "",
        }
        for i in range(len(lib_rows))
    ]

    # ---- feed hits (today's feed papers within reasonable range) ----
    feed_hits = []
    if len(feed_rows) > 0:
        for i in range(min(len(feed_rows), _MAX_FEED)):
            aid = feed_rows[i]["arxiv_id"]
            feed_hits.append(
                {
                    "arxiv_id": aid,
                    "x": float(feed_coords[i][0]),
                    "y": float(feed_coords[i][1]),
                    "title": feed_title_map.get(aid, ""),
                    "abstract": feed_abstract_map.get(aid, ""),
                }
            )

    # ---- blind spots ----
    blind_spots = []
    if len(feed_embs) > 0:
        bs_idx = blind_spot.find_blind_spots(
            lib_embs, feed_embs, top_n=_MAX_BLIND
        )
        for i in bs_idx:
            aid = feed_rows[i]["arxiv_id"]
            blind_spots.append(
                {
                    "arxiv_id": aid,
                    "x": float(feed_coords[i][0]),
                    "y": float(feed_coords[i][1]),
                    "title": feed_title_map.get(aid, ""),
                    "abstract": feed_abstract_map.get(aid, ""),
                }
            )

    # ---- trajectory ----
    trajectory = _load_trajectory(conn)

    return {
        "library": library,
        "trajectory": trajectory,
        "feed_hits": feed_hits,
        "blind_spots": blind_spots,
    }
