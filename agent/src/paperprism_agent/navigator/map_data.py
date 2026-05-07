"""Assemble the JSON payload for ``GET /api/map``."""
from __future__ import annotations

import logging
import sqlite3

import numpy as np

from paperprism_agent import repository
from paperprism_agent.navigator import blind_spot, projection

log = logging.getLogger("paperprism.navigator.map_data")
_MAX_FEED = 200
_MAX_BLIND = 5


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
    feed_rows = repository.list_arxiv_feed_embeddings(conn)

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

    # ---- UMAP projection ----
    all_embs = np.vstack([lib_embs, feed_embs])
    coords = projection.fit_umap(all_embs)
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

    # ---- feed hits (all feed papers within reasonable range) ----
    # MVP: return every feed paper with coordinates; frontend filters visually
    feed_hits = []
    if len(feed_rows) > 0:
        for i in range(min(len(feed_rows), _MAX_FEED)):
            feed_hits.append(
                {
                    "arxiv_id": feed_rows[i]["arxiv_id"],
                    "x": float(feed_coords[i][0]),
                    "y": float(feed_coords[i][1]),
                }
            )

    # ---- blind spots ----
    blind_spots = []
    if len(feed_embs) > 0:
        bs_idx = blind_spot.find_blind_spots(
            lib_embs, feed_embs, top_n=_MAX_BLIND
        )
        for i in bs_idx:
            blind_spots.append(
                {
                    "arxiv_id": feed_rows[i]["arxiv_id"],
                    "x": float(feed_coords[i][0]),
                    "y": float(feed_coords[i][1]),
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
