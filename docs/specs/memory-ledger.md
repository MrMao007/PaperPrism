# Spec: Memory Ledger (L0 Event Log)

> Status: **v1 — OQ1 & OQ2 decided** (soft-delete ✅, no bonus events ❌). OQ3–6 still pending.
>
> Decisions on 2026-04-27:
> - OQ1: papers 使用**软删**（新增 `papers.deleted_at` 列）。
> - OQ2: 3 个 bonus event (`classification.computed` / `llm.called` / `settings.changed`) **v1 不做**，推迟到 v2。
> Owner: [@MrMao007](https://github.com/MrMao007)
> Last updated: 2026-04-27
> Skill: [`spec-driven-development`](../../.qoder/skills/spec-driven-development/SKILL.md)

PaperPrism's **Memory Ledger** is an append-only event log that records
every state-changing operation performed on papers, tags, topics, and
settings. It's the `L0` layer that future memory features (weekly
digests, LLM-augmented context, undo windows) will build on.

---

## 1. Objective

### 1.1 Why

Today PaperPrism holds current state in SQLite (`papers`, `tags`,
`topics`) but keeps **no record of how that state got there**. That
blocks five capabilities we want:

1. **Audit** — "what happened to this paper since I added it?"
2. **Undo window** — "I deleted the wrong paper 30 seconds ago, get it
   back." (Requires an event trail to reverse-engineer.)
3. **User insight** — "LLM auto-tagged me 47 papers last week; I
   overrode 9 of its tags." (Feedback for prompt tuning.)
4. **LLM memory** — give the classifier / Dashboard assistant "what
   the user did recently" as context, so it can do weekly briefings
   and de-duplicate recommendations.
5. **Developer debugging** — "this paper has the wrong topic now — when
   did it get added and by which actor?"

### 1.2 User stories

- **As a user**, I want a chronological "library activity" view so I
  can see what I (or the agent) did this week.
- **As a user**, I want to know which tags on a paper were auto-generated
  versus which I added myself.
- **As a developer**, I want to run `sqlite3 ... 'SELECT * FROM events
  WHERE subject_id=?'` and get a paper's full lifecycle.
- **As the classifier**, I want to read the last 500 events so I can
  avoid re-suggesting topics the user just rejected.

### 1.3 Non-goals (explicitly out of scope for v1)

- ❌ **Event sourcing / state rebuild** — state stays in `papers` /
  `tags` / `topics`. Events are a side-channel, never the source of
  truth for current state.
- ❌ **Cross-machine replication** — PaperPrism is local-first; the
  ledger stays on-device.
- ❌ **Read-event tracking** — we do NOT log "paper viewed",
  "Dashboard opened", "search performed". Too noisy, too little value.
- ❌ **Approval workflows / signing** — the user is their own auditor.
- ❌ **Dashboard UI for the ledger** — this is a backend-only v1; UI
  (Activity drawer) is a v2 task.

### 1.4 Success = all of these true

- [x] Every mutating `repository.py` method appends ≥ 1 event in the
      same SQLite transaction as the business write.
- [x] The 14 `event_type` values below are all emittable through
      normal user flows (verifiable via `SELECT DISTINCT event_type
      FROM events` after a demo session).
- [x] `GET /api/events?subject_type=paper&subject_id=<arxiv_id>`
      returns the paper's full timeline in reverse-chronological order.
- [x] Auto-tagging a batch of 50 papers produces ≤ 300 events and the
      batch insert completes in ≤ 200 ms of DB time (excluding LLM
      latency).
- [x] `paperprism-agent serve` on a fresh home dir applies migration
      `0004_events.sql` automatically, no manual step.
- [x] All existing tests remain green. New tests in
      [agent/tests/test_events.py](../../agent/tests/test_events.py)
      cover both the `EventLogger` unit contract and the
      mutation-to-event integration per operation type.

---

## 2. Tech stack (no new runtime deps)

- Python 3.10+, FastAPI, Pydantic v2 — same as existing Agent.
- Stdlib `sqlite3` — no ORM. Same as existing.
- `pytest` + `httpx.AsyncClient` — same dev stack as existing tests
  (if any are added alongside).
- TypeScript / React / WXT (extension) — only for the `lib/agent.ts`
  typed client stub in v2; v1 doesn't touch the extension build.

---

## 3. Commands

```bash
# Run migrations + serve (auto-applies 0004_events.sql)
paperprism-agent serve --log-level debug

# Poke the ledger
sqlite3 ~/.paperprism/db.sqlite \
  "SELECT ts, actor, event_type, subject_id FROM events ORDER BY ts DESC LIMIT 20;"

# Count events by type
sqlite3 ~/.paperprism/db.sqlite \
  "SELECT event_type, COUNT(*) FROM events GROUP BY event_type ORDER BY 2 DESC;"

# Query via REST (after v1 lands)
curl 'http://127.0.0.1:17321/api/events?subject_type=paper&subject_id=2505.01234&limit=50'
curl 'http://127.0.0.1:17321/api/papers/2505.01234/timeline'

# Run the new tests
cd agent && pytest tests/test_events.py -v

# Schema version check (expect 4 after migration runs)
sqlite3 ~/.paperprism/db.sqlite 'SELECT schema_version FROM schema_meta;'
```

---

## 4. Project structure (what changes)

### 4.1 New files

```
agent/src/paperprism_agent/
├── events.py                           # EventLogger + EventType enum + payload sanitizer
└── migrations/
    └── 0004_events.sql                 # CREATE TABLE + 4 indexes + schema_meta bump

agent/tests/
└── test_events.py                      # Unit + integration (uses tmp_path home)

docs/specs/
└── memory-ledger.md                    # ← this file (already present)
```

### 4.2 Modified files

```
agent/src/paperprism_agent/
├── repository.py                       # emit events in mutation methods
├── server.py                           # 2 new read-only routes + dependency for Actor
├── models.py                           # Event, EventList, EventQuery schemas
└── paths.py                            # (only if we add an events ndjson export helper; optional)

extension/lib/
└── agent.ts                            # add getEvents + getPaperTimeline typed fetch (v1.5)
```

No changes to: `ingest.py` / `worker.py` / `classifier.py` / `tagger.py` —
they already go through `repository.py`, so they inherit event emission
for free. That's the payoff of the single-point-emit rule in
[AGENTS.md](../../AGENTS.md).

---

## 5. Data model

### 5.1 Schema (migration `0004_events.sql`)

```sql
-- Memory Ledger L0 event log. Append-only. No UPDATEs, no DELETEs.
-- Business writes and their events MUST be wrapped in the same TXN.
CREATE TABLE IF NOT EXISTS events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT    NOT NULL,          -- UTC ISO8601 with Z suffix
  actor         TEXT    NOT NULL,          -- user | agent | llm | system
  event_type    TEXT    NOT NULL,          -- e.g. paper.ingested.downloaded
  subject_type  TEXT    NOT NULL,          -- paper | tag | topic
  subject_id    TEXT    NOT NULL,          -- arxiv_id | tag name | topic slug
  related_ids   TEXT,                      -- JSON array, null if empty
  payload       TEXT,                      -- JSON object, null if empty
  schema_v      INTEGER NOT NULL DEFAULT 1 -- bump when adding new required payload keys
);

CREATE INDEX IF NOT EXISTS idx_events_ts         ON events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_subject    ON events(subject_type, subject_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_type       ON events(event_type, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_actor_type ON events(actor, event_type, ts DESC);

-- Soft delete for papers so delete events can still JOIN back.
ALTER TABLE papers ADD COLUMN deleted_at TEXT;

UPDATE schema_meta SET schema_version = 4 WHERE rowid = 1;
```

### 5.2 `event_type` taxonomy (14 core + 3 bonus)

Names follow `<subject>.<verb>[.<qualifier>]`, all lowercase, `.`-separated.

| # | `event_type` | Typical `actor` | `subject_type` | `subject_id` | Key payload fields |
|---|---|---|---|---|---|
| 1 | `paper.ingested.downloaded` | user | paper | arxiv_id | `source_url`, `filename`, `vault_path` |
| 2 | `paper.ingested.uploaded` | user | paper | arxiv_id | `filename`, `size_bytes` |
| 3 | `paper.ingested.bulk_imported` | user | paper | arxiv_id | `batch_id`, `index`, `total`, `source_dir` |
| 4 | `paper.deleted` | user | paper | arxiv_id | `reason` (optional), `title_at_delete` |
| 5 | `topic.created` | user | topic | slug | `name`, `summary`, `paper_ids` |
| 6 | `topic.renamed` | user | topic | slug | `old_name`, `new_name` |
| 7 | `topic.deleted` | user | topic | slug | `name_at_delete`, `paper_count_at_delete` |
| 8 | `topic.papers_added` | user | topic | slug | `paper_ids[]` |
| 9 | `topic.papers_removed` | user | topic | slug | `paper_ids[]` |
| 10 | `tag.auto_generated` | agent | paper | arxiv_id | `tags[]`, `model`, `tokens_in`, `tokens_out` |
| 11 | `tag.added_by_user` | user | tag | `"{arxiv_id}:{tag}"` | `paper_id`, `tag` |
| 12 | `tag.removed_by_user` | user | tag | `"{arxiv_id}:{tag}"` | `paper_id`, `tag` |
| 13 | `tag.added_by_llm` | llm | tag | `"{arxiv_id}:{tag}"` | `paper_id`, `tag`, `job_id` |
| 14 | `tag.removed_by_llm` | llm | tag | `"{arxiv_id}:{tag}"` | `paper_id`, `tag`, `job_id` |

> v1 ships exactly these 14 events. `classification.computed` /
> `llm.called` / `settings.changed` were considered but **deferred to
> v2** (see Decision log at the top of this doc).

### 5.3 Payload conventions

- JSON object, serialized with `json.dumps(sort_keys=True, separators=(',',':'))`.
- **Hard size cap**: after serialization, max 16 KB. If larger, replace
  with `{"truncated": true, "summary": "<one-line>", "original_keys":
  ["..."]}`.
- **Redaction**: never write `api_key`, `token`, or any value from
  `~/.paperprism/secrets.env` — even in `settings.changed` use
  `"old_redacted": "***", "new_redacted": "***"` for secret keys.
- `related_ids` holds arxiv_ids / topic slugs / tag names that the
  event affects beyond its primary `subject_id`. Example:
  `topic.papers_added` has `subject_id=<topic_slug>` and
  `related_ids=["2505.01234","2505.02345"]`.

---

## 6. Code style (one real example beats three paragraphs)

### 6.1 `events.py` — the only place that writes to `events`

```python
"""Memory Ledger writer. Owned by repository.py; DO NOT call from server.py."""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

log = logging.getLogger(__name__)

Actor = Literal["user", "agent", "llm", "system"]
SubjectType = Literal["paper", "tag", "topic"]

_PAYLOAD_MAX_BYTES = 16 * 1024
_VALID_EVENT_TYPES: set[str] = {
    "paper.ingested.downloaded",
    "paper.ingested.uploaded",
    "paper.ingested.bulk_imported",
    "paper.deleted",
    "topic.created",
    "topic.renamed",
    "topic.deleted",
    "topic.papers_added",
    "topic.papers_removed",
    "tag.auto_generated",
    "tag.added_by_user",
    "tag.removed_by_user",
    "tag.added_by_llm",
    "tag.removed_by_llm",
}


@dataclass(frozen=True)
class Event:
    ts: str
    actor: Actor
    event_type: str
    subject_type: SubjectType
    subject_id: str
    related_ids: list[str] | None = None
    payload: dict[str, Any] | None = None


class EventLogger:
    """Append events to the ledger in the caller's transaction."""

    @staticmethod
    def emit(
        conn: sqlite3.Connection,
        *,
        actor: Actor,
        event_type: str,
        subject_type: SubjectType,
        subject_id: str,
        related_ids: Iterable[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if event_type not in _VALID_EVENT_TYPES:
            raise ValueError(f"unknown event_type: {event_type!r}")
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        related = list(related_ids) if related_ids else None
        payload_json = _sanitize(payload) if payload else None
        conn.execute(
            "INSERT INTO events (ts, actor, event_type, subject_type, subject_id, related_ids, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ts,
                actor,
                event_type,
                subject_type,
                subject_id,
                json.dumps(related, ensure_ascii=False) if related else None,
                payload_json,
            ),
        )

    @staticmethod
    def emit_many(conn: sqlite3.Connection, events: Iterable[Event]) -> None:
        """Bulk-insert helper for auto-tag jobs. Same TXN as caller."""
        rows = [_row_from_event(e) for e in events]
        if not rows:
            return
        conn.executemany(
            "INSERT INTO events (ts, actor, event_type, subject_type, subject_id, related_ids, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def _sanitize(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text.encode("utf-8")) <= _PAYLOAD_MAX_BYTES:
        return text
    log.warning("event payload %d bytes exceeds cap; truncating", len(text))
    return json.dumps(
        {"truncated": True, "original_keys": sorted(payload.keys())},
        ensure_ascii=False,
        separators=(",", ":"),
    )
```

### 6.2 `repository.py` call site (example: delete)

```python
def delete_paper(self, arxiv_id: str, *, actor: Actor = "user", reason: str | None = None) -> bool:
    with self._conn:  # same transaction for DELETE + event
        row = self._fetch_paper(arxiv_id)
        if row is None or row["deleted_at"] is not None:
            return False
        self._conn.execute(
            "UPDATE papers SET deleted_at = ? WHERE arxiv_id = ?",
            (_now_iso(), arxiv_id),
        )
        EventLogger.emit(
            self._conn,
            actor=actor,
            event_type="paper.deleted",
            subject_type="paper",
            subject_id=arxiv_id,
            payload={"reason": reason, "title_at_delete": row["title"]},
        )
    return True
```

### 6.3 Route-to-actor mapping (`server.py`)

```python
def _actor_from_request(req: Request) -> Actor:
    """Extension sets X-PaperPrism-Actor: user; LLM batch worker sets llm; else agent."""
    raw = (req.headers.get("X-PaperPrism-Actor") or "").strip().lower()
    if raw in {"user", "agent", "llm", "system"}:
        return raw  # type: ignore[return-value]
    return "agent"

@app.post("/api/ingest")
async def ingest_paper(body: IngestBody, req: Request, cfg: AgentConfig = Depends(get_config)):
    actor = _actor_from_request(req)
    ...  # existing logic, passes actor down to repository
```

### 6.4 Query route (`server.py`)

```python
@app.get("/api/events")
def list_events(
    subject_type: SubjectType | None = None,
    subject_id: str | None = None,
    actor: Actor | None = None,
    event_type: str | None = None,
    since: str | None = None,          # UTC ISO8601 lower bound, inclusive
    limit: int = Query(100, ge=1, le=1000),
    cursor: int | None = None,          # event.id, returns id < cursor
    cfg: AgentConfig = Depends(get_config),
) -> dict[str, Any]:
    ...
```

---

## 7. API surface (v1)

All are **read-only**. Auth inherits the standard `X-PaperPrism-Token`
dependency.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/events` | Filter by `subject_type`, `subject_id`, `actor`, `event_type`, `since`; cursor-paginated via `?cursor=<id>`; default `limit=100`, max 1000. |
| `GET` | `/api/papers/{arxiv_id}/timeline` | Syntactic sugar = `events?subject_type=paper&subject_id=<arxiv_id>`. |

Response shape:

```json
{
  "items": [
    {
      "id": 12345,
      "ts": "2026-04-27T10:05:03.123Z",
      "actor": "user",
      "event_type": "paper.deleted",
      "subject_type": "paper",
      "subject_id": "2505.01234",
      "related_ids": null,
      "payload": { "reason": null, "title_at_delete": "Attention is all you need" }
    }
  ],
  "next_cursor": 12300
}
```

---

## 8. Testing strategy

Framework: `pytest`. Tests live at [agent/tests/test_events.py](../../agent/tests/test_events.py).
Use a `tmp_path`-scoped home dir via `AgentConfig.from_env(home=tmp_path)`
so tests never touch the real `~/.paperprism/`.

Levels + targets:

| Level | What | Target coverage |
|---|---|---|
| **Unit: `EventLogger.emit`** | TXN rollback when business INSERT fails → no orphan event row. Payload size cap works. Unknown `event_type` raises. | `events.py` branches ≥ 95 % |
| **Unit: `EventLogger.emit_many`** | Batch insert writes N rows atomically. | same |
| **Integration: ingest flow** | `/api/ingest` with fixture payload produces exactly one `paper.ingested.downloaded` event with correct `subject_id` and `payload.source_url`. | `ingest.py` mutation path ≥ 80 % |
| **Integration: delete flow** | After `DELETE /api/papers/{id}`, paper row has `deleted_at`, one `paper.deleted` event exists, and `GET /api/papers/{id}/timeline` still returns its earlier events. | `repository.delete_paper` 100 % |
| **Integration: tag flows** | Auto-tag job of 5 papers × 3 tags → exactly 5 `tag.auto_generated` events. User `POST /api/papers/{id}/tags` with `add=["x"]` → one `tag.added_by_user`. User `remove=["y"]` → one `tag.removed_by_user`. | `repository.set_paper_tags` + friends 100 % |
| **Integration: topic flows** | Create → rename → add papers → remove papers → delete : produces exactly 5 events in order, `related_ids` populated on the `topic.papers_*` events. | `repository.topic_*` methods ≥ 90 % |
| **Integration: query API** | `subject_type` + `subject_id` + `since` all filter correctly; `cursor` paginates stably under concurrent inserts. | `server.list_events` 100 % |

---

## 9. Boundaries

### 9.1 Always

- Emit events **inside the same SQLite transaction** as the business
  write (`with self._conn:` block or explicit `BEGIN`/`COMMIT`).
- Validate `actor` against the closed enum; default to `agent` if
  unknown header value.
- Serialize `payload` with `sort_keys=True` and enforce the 16 KB cap.
- Use UTC ISO8601 with `Z` suffix for `ts`.
- New `event_type` values must be added to the enum table in this
  spec **before** the code emits them.
- All new schema changes go through a new numbered migration file;
  never mutate `0004_events.sql` after it ships.

### 9.2 Ask first

- Adding a brand-new `event_type`.
- Adding a new column / index to `events` (needs `0005_...sql`).
- Changing the public shape of `/api/events` or the timeline endpoint.
- Introducing an async / queued writer (current design is synchronous
  in-TXN on purpose).
- Emitting events from a place other than `repository.py`.
- Building Dashboard UI for the ledger (scheduled for v2).

### 9.3 Never

- ❌ Emit events from `server.py`, `ingest.py`, `worker.py`, or the
  extension. Always go through `repository.py`.
- ❌ Record PDF bytes, raw secrets, API keys, or `Authorization`
  headers in any `payload`. If a future event type ever needs to
  reference a secret key, redact it (`"***"`) rather than omit it.
- ❌ Record read events (`paper.viewed`, `search.performed`,
  `dashboard.opened`). If we want those later, they get a separate
  `page_views` table.
- ❌ Use `events` as a state-rebuild source. `papers` / `tags` /
  `topics` are the source of truth.
- ❌ `UPDATE events SET ...` or `DELETE FROM events ...` in app code.
  The ledger is append-only; the only way to alter history is a
  future migration `0XYZ_redact.sql` with a paper trail.

---

## 10. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Bulk import of 1 k papers emits 1 k events + flush stalls UI | Medium | Medium | `emit_many` batch insert in a single TXN; import route already chunks at 50 papers. |
| `events` table grows unbounded over years | Low short-term | Low | v1 accepts unbounded growth; v2 adds `events_archive` + TTL policy. |
| Actor spoofing from plugin | Low | Low | Extension runs on the user's own machine; "user" vs "agent" is a hint for the UI, not a security boundary. |
| Migration 0004 runs on DB with prior 0001–0003 only; `schema_meta` may be absent on very old dev DBs | Low | Low | Migration is idempotent (`IF NOT EXISTS` + conditional `ALTER`). |
| Payload larger than 16 KB breaks a flow (e.g. huge bulk import batch_id list) | Medium | Low | Sanitizer truncates and logs; business logic uses `related_ids` (outside the 16 KB) for long arrays. |
| Tests on Windows fail due to path handling | Low | Low | `tmp_path` is cross-platform; no hard-coded forward slashes. |

---

## 11. Rollout plan (tasks will be expanded by `planning-and-task-breakdown` skill)

- **T1** — Migration `0004_events.sql` + `EventLogger` + unit tests.
- **T2** — Thread `actor` through `server.py` → `repository.py` public
  methods; wire emits into **ingest** and **delete** paths + integration
  tests for those paths.
- **T3** — Wire emits into **tag** paths (user + auto/llm); add
  `emit_many` usage in `auto_tag_jobs.py`.
- **T4** — Wire emits into **topic** paths; tests.
- **T5** — Add `GET /api/events` + `GET /api/papers/{id}/timeline` +
  Pydantic models + `extension/lib/agent.ts` typed stubs.
- **T6** — Doc updates: root [AGENTS.md](../../AGENTS.md) +
  [agent/AGENTS.md](../../agent/AGENTS.md) reference this spec; bump
  `schema_version` mention from 3 → 4.
- ~~**T7** — Bonus events~~ → **deferred to v2** (see Decision log).

All tasks follow `incremental-implementation` + `test-driven-development`.

---

## 12. Open questions

### Decided (2026-04-27)

1. ~~Soft-delete vs hard-delete~~ → **soft-delete**. `papers.deleted_at
   TEXT` column added in `0004_events.sql`.
2. ~~Ship 3 bonus event types in v1~~ → **deferred to v2**. v1 ships
   only the 14 events in §5.2.

### Still pending

3. ~~Query API pagination style~~ → **cursor-by-id** (decided 2026-04-27).
   Implemented in `server.py` via `?cursor=<id>`.
4. **Retention**: v1 says "never auto-delete". At what event count
   should we reconsider? 1 million? 10 million? (Rough sizing: 1 year
   of heavy use ≈ 100 k events ≈ 20 MB; don't think we'll need to worry
   for a long time.)
5. ~~Extension `lib/agent.ts` stubs~~ → **shipped in v1** (decided 2026-04-27).
   `fetchEvents` + `fetchPaperTimeline` typed stubs added; no Dashboard UI yet.
6. **Does the reviewer want a CLI** (e.g. `paperprism-agent events tail
   -n 50`)? Cheap to add; propose **yes**, in T5.

---

## 13. References

- Root architecture: [AGENTS.md](../../AGENTS.md)
- Agent internals: [agent/AGENTS.md](../../agent/AGENTS.md)
- Existing migrations: [agent/src/paperprism_agent/migrations/](../../agent/src/paperprism_agent/migrations/)
- Spec-driven-development skill: [.qoder/skills/spec-driven-development/SKILL.md](../../.qoder/skills/spec-driven-development/SKILL.md)
- (Future) `planning-and-task-breakdown` skill will expand §11 into
  Phase 3 task cards before any coding starts.
