# Spec: Academic Navigator v1

> Status: A1–A3 spikes passed (2026-05-07). Ready for implementation.  
> Builds on: [Memory Ledger L0](memory-ledger.md) + [Academic Memory one-pager](../ideas/academic-memory.md)

---

## Objective

Add a `/map` route to the PaperPrism Dashboard that renders a 2D embedding map of the user's paper library. The map is the primary surface for daily opens, replacing the ranked list as the "hero" view while keeping the list accessible as a fallback.

**User stories**
- As a researcher, I open PaperPrism and immediately see where my current reading sits in the landscape of my library.
- As a researcher, I notice a cluster of papers I haven't touched in weeks and realise I've drifted from a sub-field.
- As a researcher, I click a blue glow and discover a paper that is *related to what I know* but *outside what I've read*.

**Acceptance criteria**
1. Opening `/map` renders ≤2 s after API response.
2. Pan/zoom on the map stays at 60 fps with ≤5 000 points.
3. Blind-spot recommendations are rated ≥3.5 / 5 for "meaningfulness" in internal dogfood.
4. Adding 50 new papers and reopening `/map` does not visibly reshuffle the plane (≤10 % of points move >3 neighbour ranks).

---

## Tech Stack

| Layer | Technology | Version | Reason |
|---|---|---|---|
| Embedding model | `sentence-transformers` | ≥3.0 | `BAAI/bge-small-en-v1.5`, local, 384-dim |
| Vector store | `sqlite-vec` | ≥0.1.0 | Zero new infra, same SQLite file |
| SQLite driver | `pysqlite3` | ≥0.5.0 | `enable_load_extension` required by sqlite-vec |
| Dimensionality reduction | `umap-learn` | ≥0.5 | Fast, preserves local + global structure |
| Alignment | `scipy.spatial.procrustes` | ≥1.0 | Removes rotation/scale after incremental re-fit |
| Backend framework | FastAPI | existing | No change |
| Frontend renderer | Canvas 2D | built-in | Lighter than D3/SVG for >1 000 points |

---

## Commands

```bash
# Agent — full re-index (rare, e.g. after model swap)
cd agent && uv run python -m paperprism_agent.cli reindex

# Agent — dev server
cd agent && uv run paperprism-agent serve

# Extension — dev build with HMR
cd extension && npm run dev

# Extension — production build
cd extension && npm run build
```

---

## Project Structure

**Agent (new files only)**
```
src/paperprism_agent/
├── navigator/
│   ├── __init__.py
│   ├── embedding.py          # SentenceTransformer wrapper + batch encode
│   ├── projection.py         # UMAP fit + Procrustes alignment
│   ├── blind_spot.py         # Local-density-contrast scorer
│   ├── map_data.py           # Assemble /api/map payload
│   └── tasks.py              # Background celery-like jobs (re-index, daily arXiv pull)
├── repository.py             # + read/write paper_embeddings, arxiv_feed_embeddings
├── server.py                 # + GET /api/map
└── migrations/
    └── 0005_embeddings.sql   # sqlite-vec virtual tables (already applied)
```

**Extension (new files only)**
```
entrypoints/
├── map/                      # new-tab route /map
│   ├── App.tsx               # layout: CanvasMap + PointDrawer
│   ├── CanvasMap.tsx         # Canvas 2D renderer (pan, zoom, hover, click)
│   ├── PointDrawer.tsx       # Right-side slide-out for paper details
│   └── types.ts              # MapPoint, TrajectorySegment, BlindSpot
└── dashboard/
    └── App.tsx               # Add "Open Map" link/button
```

---

## Code Style

**Python — embedding batching**
```python
def encode_batch(texts: list[str], batch_size: int = 32) -> np.ndarray:
    model = _lazy_model()          # singleton, loaded once
    return model.encode(texts, batch_size=batch_size, show_progress_bar=False)
```

**TypeScript — Canvas point**
```typescript
interface MapPoint {
  id: number;           // papers.id or arxiv_feed rowid
  arxivId: string;
  x: number;
  y: number;
  kind: 'library' | 'feed' | 'blind_spot';
  title: string;
}
```

**Naming conventions**
- Python modules: `snake_case.py`
- React components: `PascalCase.tsx`
- API routes: `/api/map` (kebab-case)
- Database tables: `paper_embeddings`, `arxiv_feed_embeddings` (snake_case)

---

## Testing Strategy

| Level | Framework | Location | What |
|---|---|---|---|
| Unit | pytest | `agent/tests/test_navigator_*.py` | embedding shape, Procrustes alignment correctness, blind-spot scores |
| Integration | pytest | `agent/tests/test_api_map.py` | `GET /api/map` returns valid JSON, sqlite-vec kNN works end-to-end |
| Manual | — | Browser | Canvas pan/zoom at 5 000 points, drawer opens on click |

**Coverage target:** navigator module ≥ 80 %.

---

## Boundaries

- **Always do**
  - Run `pytest` before any navigator code commit.
  - Use Procrustes alignment after every UMAP re-fit.
  - Persist embeddings in sqlite-vec (never in-memory only).
  - Rate-limit arXiv API calls to ≤1 req / 3 s.

- **Ask first**
  - Adding a new PyTorch model > 100 MB.
  - Changing `paper_embeddings` or `arxiv_feed_embeddings` schema.
  - Switching from Canvas 2D to WebGL or SVG.
  - Introducing a task queue (celery, rq) instead of simple background threads.

- **Never do**
  - Call cloud embedding APIs (OpenAI, Voyage, Cohere).
  - Embed full PDF text in v1 (abstract only).
  - Cache map data in the Extension's `chrome.storage` (too large).
  - Show a ranked "recommended list" as the primary surface.

---

## Data Model

```sql
-- User-library embeddings (abstract, bge-small-en-v1.5, 384-dim)
CREATE VIRTUAL TABLE paper_embeddings USING vec0(
    paper_id INTEGER,
    embedding FLOAT[384]
);

-- arXiv feed embeddings (cs.* + stat.ML, last 30 days, ≤500/day)
CREATE VIRTUAL TABLE arxiv_feed_embeddings USING vec0(
    arxiv_id TEXT PRIMARY KEY,
    embedding FLOAT[384]
);
```

**TTL enforcement:** A nightly job deletes arxiv feed rows older than 30 days.

---

## API Contract

### `GET /api/map`

Response `200 OK`:
```json
{
  "library": [
    {"id": 1, "arxiv_id": "2003.08934", "x": 12.3, "y": -4.5, "title": "...", "last_read_at": "2026-05-07T08:17:04Z"}
  ],
  "trajectory": [
    {"arxiv_id": "2003.08934", "ts": "2026-05-07T08:17:04Z", "event_type": "paper.read_session"}
  ],
  "feed_hits": [
    {"arxiv_id": "2501.00001", "x": 13.1, "y": -3.8, "title": "...", "score": 0.92}
  ],
  "blind_spots": [
    {"arxiv_id": "2403.03592", "x": -8.2, "y": 15.0, "title": "...", "score": 1.098}
  ]
}
```

**Cache:** Re-computed on-demand, cached in memory for 10 minutes or until a paper is ingested/deleted.

---

## Success Criteria

| # | Criterion | How verified |
|---|---|---|
| 1 | 5 000 abstracts embed in ≤60 s | `scripts/benchmark_embedding.py` |
| 2 | 50-abstract incremental embed in ≤2 s | `scripts/benchmark_embedding.py` |
| 3 | UMAP incremental stability ≤10 % moved >3 ranks | `scripts/benchmark_umap_stability.py` |
| 4 | Blind-spot meaningfulness ≥3.5 / 5 | Internal dogfood rating |
| 5 | `GET /api/map` <2 s wall-clock | `curl -w "%{time_total}"` |
| 6 | Canvas pan/zoom 60 fps at 5 000 points | Chrome DevTools FPS meter |

---

## Open Questions

1. **arXiv feed trigger:** Should the daily pull be a cron job inside the Agent, or a manual button in the Extension?
2. **Trajectory cone radius:** How large is the "extended cone" around the red line that qualifies a yellow pulse? (Proposal: 2× mean nearest-neighbour distance in 2D space.)
3. **Blind-spot count:** How many blue glows to show at once? (Proposal: 5, to avoid cognitive overload.)
4. **Map as default route:** Should `/map` replace `/dashboard` as the new-tab landing page, or should the user choose?
