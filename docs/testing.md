# Testing — PaperPrism

> Single source of truth for how to run, write, and extend tests in this
> repo. Follows the **TDD discipline** (see
> [`test-driven-development` skill](https://github.com/anthropics/skills)):
> for every behaviour change, write the test first; for every bug fix,
> write the reproduction test first.

## TL;DR

| Layer | Framework | Run command | Status |
|-------|-----------|-------------|--------|
| **Agent** (Python / FastAPI / SQLite) | `pytest` | `cd agent && .venv/bin/python -m pytest tests/` | ✅ 61 tests, all green |
| **Extension** (TS / React / WXT) | none yet | — | ⚠️ Type-check only via `npm run compile` |

---

## Agent — pytest

### Run

```bash
cd agent
source .venv/bin/activate            # if not already active
python -m pytest tests/              # full suite (~10 s, includes UMAP)
python -m pytest tests/test_smoke.py # one file
python -m pytest tests/ -k feed      # filter by name
python -m pytest tests/ -v           # verbose, show each test
```

Exit code 0 means everything passed. CI / pre-commit should treat any
non-zero exit as a hard failure.

### Test layout

```
agent/tests/
├── conftest.py                  # `tmp_home` + `db_conn` fixtures (isolated per test)
├── test_smoke.py                # harness sanity + EXPECTED_SCHEMA_VERSION guard
├── test_events.py               # core EventLogger contract
├── test_events_feed.py          # feed.fetched / paper.classified additions ★
├── test_weekly_digest_events.py # _fetch_week_events aggregation ★
├── test_api_events.py           # GET /api/events + timeline
├── test_api_feed_status.py      # GET /api/feed/status ★
├── test_ingest_emit.py          # ingest → ledger event chain
├── test_delete.py               # delete cascade + ledger
├── test_navigator.py            # UMAP projection / blind spots
├── test_topics.py               # topic auto-tag flow
├── test_topic_papers.py         # topic membership ops
├── test_track_event.py          # external POST /api/events ingestion
├── test_tags_user.py            # user tag normalisation
└── test_auto_tag_perf.py        # latency budget for tagger
```

★ = added in May 2026 to cover the daily arXiv feed + LLM classification
ledger work. Use them as templates for new event-emitting features.

### Fixture model — `tmp_home` + `db_conn`

Every test gets an isolated `~/.paperprism` rooted at `tmp_path`:

```python
def test_my_feature(db_conn: sqlite3.Connection) -> None:
    EventLogger.emit(db_conn, Event(...))
    row = db_conn.execute("SELECT ... FROM events").fetchone()
    assert row["..."] == ...
```

- `tmp_home` → a `Config` whose `paths.home` is `tmp_path/`. Migrations
  apply on a fresh SQLite file. The `paperprism_agent.db` connection
  singleton is closed before and after the test, so no state ever leaks.
- `db_conn` → opens that DB and returns a `sqlite3.Connection`. Use
  this for direct SQL setup/inspection.
- For HTTP tests, build a fresh app per test:
  ```python
  app = server.create_app(tmp_home)
  client = TestClient(app)
  resp = client.get("/api/feed/status")
  ```

### Writing a new test — TDD recipe

Adding a feature or fixing a bug:

1. **RED** — write the test first; run it; **see it fail** with the
   exact error you expect.
2. **GREEN** — write the minimum code to make the test pass.
3. **REFACTOR** — clean up, run tests again to confirm green.

For ledger / event work specifically:

```python
# 1. Use EventLogger.emit() to insert events. NEVER raw SQL — you'll
#    bypass the validator and won't catch a typo in event_type.
# 2. To backdate events for window-based queries (e.g. weekly digest),
#    emit normally then UPDATE the ts column directly:
eid = EventLogger.emit(conn, Event(...))
conn.execute("UPDATE events SET ts = ? WHERE id = ?", ("2026-05-04T08:00:00Z", eid))
# 3. Always assert on the OUTCOME (row contents, response shape), not
#    on internal call sequences.
```

### Common pitfalls

- **Forgot to bump `EXPECTED_SCHEMA_VERSION`.** When adding a new
  `migrations/000N_*.sql`, also update the constant in
  `tests/test_smoke.py`. Otherwise `test_migrations_apply` fails with
  `assert <new> == <old>`.
- **NOT NULL constraints on directly-inserted rows.** Many tables
  (e.g. `arxiv_feed_papers`) require `created_at`. When seeding test
  data with raw SQL, include all NOT NULL columns or use the
  repository helpers.
- **`EventLogger` always stamps `ts = now()`.** For tests that depend
  on time-windowed queries, emit then `UPDATE ts` (see recipe above).
- **DB connection caching across tests.** `paperprism_agent.db.connect`
  caches a singleton per process. The `tmp_home` fixture closes it on
  setup AND teardown — don't bypass this by reaching into internals.

### Coverage philosophy

Follow the test pyramid:

- **~80 % unit tests** — pure functions (`_fetch_week_events`,
  `repository.normalise_tag`, `arxiv.parse_url`) hit only `db_conn`,
  no HTTP, no LLM, no network. Sub-100 ms each.
- **~15 % integration tests** — `TestClient` against `server.create_app`,
  exercising the full REST surface with a real SQLite file.
- **~5 % heavy tests** — UMAP projection (`test_navigator.py`), perf
  budgets (`test_auto_tag_perf.py`). These take seconds, run them
  sparingly during local dev.

LLM calls and external HTTP are **never** invoked in tests. Mock at the
module boundary if you need to test code paths that would otherwise
call them.

---

## Extension — currently type-check only

The Chrome extension has **no test framework installed**. Every change
must at minimum pass:

```bash
cd extension
npm run compile        # tsc --noEmit (catches type errors only)
npm run build          # full WXT build (catches bundler / asset errors)
```

For runtime verification, use the **DevTools workflow** (see the
`browser-testing-with-devtools` skill):

```
1. cd extension && npm run build
2. chrome://extensions → 🔄 refresh PaperPrism card
3. Reload Dashboard / Atlas tab
4. Open DevTools → Console (must be clean) + Network (verify API calls)
5. Reproduce the change manually; capture screenshot if visual
```

### Future: introducing Vitest

When the extension grows enough to justify a runtime test framework,
the recommended path is **Vitest + happy-dom** (lightweight DOM,
fastest startup, native ESM, plays well with WXT/Vite):

```bash
cd extension
npm install -D vitest @vitest/ui happy-dom @testing-library/react @testing-library/jest-dom
```

Add to `package.json`:

```json
{
  "scripts": {
    "test": "vitest run",
    "test:watch": "vitest"
  }
}
```

Configure `vitest.config.ts` with `environment: 'happy-dom'` and the
existing WXT path aliases. First targets to write tests for:

| Target | Why first |
|--------|-----------|
| `lib/agent.ts` (HTTP client) | Pure functions, single responsibility, drives the contract |
| `lib/dialog.tsx` (`useDialog`) | Shared across all entrypoints; regressions break every UI |
| `lib/arxiv.ts` (URL parser) | Pure parsing logic with many edge cases |

UI-heavy components (`PaperRow`, `WeeklySidebar`, `PointDrawer`) are
better covered by DevTools-driven runtime checks until the suite has
matured.

---

## When you must add a test

| Situation | Test required |
|-----------|---------------|
| New REST endpoint in `server.py` | Integration test in `agent/tests/test_api_*.py` |
| New event type in `events.py` | Unit test in `agent/tests/test_events*.py` covering: emit accepted, validator rejects typo, payload shape preserved |
| New migration `000N_*.sql` | Bump `EXPECTED_SCHEMA_VERSION` in `test_smoke.py`; add an assertion for the new table/column |
| Bug fix anywhere | **Reproduction test first**, watch it fail, then fix, then watch it pass |
| LLM prompt template change | Snapshot the rendered prompt in `test_*_prompts.py` (TBD); for now, manually verify with a sample call |
| Pure CSS / asset change | Type-check + visual smoke test in DevTools; no test required |
