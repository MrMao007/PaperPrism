# AGENTS.md — Agent (Python FastAPI service)

> Scope: everything under `agent/`. Complements the root
> [AGENTS.md](../AGENTS.md) with module-level detail.

## Purpose

Local-only HTTP service that:

1. receives archive events from the extension (`/api/ingest`),
2. enriches each paper via arxiv API + PDF parsing,
3. classifies + auto-tags via user-configured LLM,
4. stores everything in `~/.paperprism/db.sqlite` + `~/.paperprism/vault`,
5. exposes a REST API for the extension Dashboard / Options / Topic views.

## Runtime layout

```
agent/
├── pyproject.toml                # hatchling; entrypoint `paperprism-agent`
├── README.md                     # user-facing docs
└── src/paperprism_agent/
    ├── __main__.py               # python -m paperprism_agent
    ├── cli.py                    # Click-style CLI: serve/install/status/...
    ├── server.py                 # FastAPI app factory (all routes here)
    ├── config.py                 # AgentConfig: paths, host/port/token
    ├── paths.py                  # resolves ~/.paperprism/* (db, vault, logs)
    ├── logging_setup.py          # rotating file + stdout
    ├── launchd.py                # macOS LaunchAgent install/uninstall
    ├── db.py                     # connect() + migrations runner
    ├── migrations/               # numbered .sql, append-only
    ├── repository.py             # ALL SQL access; normalises tags, joins topics
    ├── models.py                 # Pydantic v2 request/response schemas
    ├── ingest.py                 # archive.completed pipeline (copy + enrich)
    ├── arxiv_client.py           # arxiv API client w/ caching
    ├── pdf.py                    # PyPDF text/abstract extraction + arxiv-id sniff
    ├── dimensions.py             # LLM dimension taxonomy (topic/task/venue/methods/summary)
    ├── classifier.py             # LLM → dimension labels + TL;DR summary
    │                             #   PaperContext includes pdf_head_text + pdf_full_text
    ├── tagger.py                 # LLM → per-paper tags + topic synthesis
    ├── events.py                 # Memory Ledger L0: EventLogger + Event dataclass
    ├── auto_tag_jobs.py          # async in-process job store for /api/tags/auto
    ├── tasks.py                  # lightweight background task helpers
    ├── worker.py                 # ingest worker + per-paper auto-tag on ingest
    ├── llm.py                    # provider registry (openai/anthropic/gemini/...)
    └── resources/                # llm.default.yaml template + static assets
```

## Module responsibilities and boundaries

| Module | Allowed to import | Forbidden |
|---|---|---|
| `server.py` | everything | contain business logic beyond request/response glue |
| `repository.py` | `db`, `paths`, stdlib | FastAPI, HTTP, LLM (pure data layer) |
| `ingest.py` / `worker.py` | `repository`, `arxiv_client`, `pdf`, `classifier`, `tagger`, `llm` | FastAPI directly (called from server) |
| `classifier.py` / `tagger.py` | `llm`, `dimensions`, stdlib | `repository` (must stay pure) |
| `llm.py` | `httpx`, providers' SDKs | `repository`, `server` |
| `auto_tag_jobs.py` | `repository`, `tagger`, `llm` | FastAPI request objects |

Keep `classifier.py` and `tagger.py` **pure**: input = paper metadata
text, output = typed dataclass. They must be unit-testable without a DB.

## Data model

Defined by migrations (append-only, applied on startup):

- `0001_init.sql` — `papers`, `classifications`, `schema_meta`, FTS5 over `papers`.
- `0002_tags_topics.sql` — `tags`, `paper_tags`, `topics`, auto-tag `jobs` table.
- `0003_topics_top_tag_limit.sql` — adds `topics.top_tag_limit` column
  (**currently unused** — code returns all distinct tags; kept for
  backwards compat).
- `0004_events.sql` — `events` (append-only ledger), `papers.deleted_at`
  (soft-delete), 4 indexes on events. Bumps `schema_version` to 4.

Key invariants the code assumes:

- **Tag normalisation**: lowercase, ASCII only, words joined by `-`.
  Enforced in `repository.normalise_tag`. Never build tag names by
  string concatenation elsewhere.
- **Memory Ledger.** Every mutating `repository.py` method emits ≥1
  `events` row in the **same transaction** as the business write.
  `events.py` owns the schema; `repository.py` is the only caller.
  Event types are whitelisted (14 core types); payload capped at 16 KB.
- **Topic deletion keeps tags.** `paper_tags.topic_id` is nullable and
  ON DELETE SET NULL, so removing a topic does not drop its papers'
  tags. (See `0002_tags_topics.sql`.)
- **Papers are identified by arxiv id.** For PDFs we cannot resolve
  (bulk import fallback), synthesise `local-<sha1_12>` and mark the
  row accordingly.
- **FTS5 mirror must be updated via triggers in `0001_init.sql`** — do
  not `INSERT INTO papers_fts` by hand.
- **Dimension config override.** `~/.paperprism/dimensions.yaml` takes
  precedence over the bundled `resources/dimensions.default.yaml`.
  When adding a new dimension (e.g. `summary`), update **both** files;
  otherwise the Agent will load the user's override which lacks the new
  dimension, and the Agent process must be restarted for changes to
  take effect.

## HTTP API surface (canonical list)

Source of truth: `server.py`. Keep this table in sync whenever routes
change.

- Health / ingest: `GET /api/health`, `POST /api/ingest`,
  `POST /api/ingest/upload`.
- Papers: `GET/DELETE /api/papers`, `GET /api/papers/{id}`,
  `GET /api/papers/{id}/pdf`, `GET /api/papers/{id}/tags`,
  `POST /api/papers/{id}/tags` (body `{add:[], remove:[]}`).
- Taxonomy: `GET /api/dimensions/values`, `GET /api/tasks/stats`,
  `GET /api/tags`.
- Auto-tag jobs: `POST /api/tags/auto`, `GET /api/tags/auto/{id}`,
  `DELETE /api/tags/auto/{id}`, `POST /api/tags/auto/{id}/retry`.
- Topics: `GET /api/topics`, `GET /api/topics/{slug}`,
  `DELETE /api/topics/{topic_id}`.
- Ledger: `GET /api/events` (filter + cursor pagination),
  `GET /api/papers/{id}/timeline`.
- LLM: `GET /api/llm/config`, `PUT /api/llm/config`, `POST /api/llm/test`.

All mutating routes depend on `Depends(require_token)`; auth is a
static header `X-PaperPrism-Token` gated by `PAPERPRISM_TOKEN`.

## LLM layer

`llm.py` exposes:

```python
def chat(cfg: AgentConfig, *, messages: list[dict], response_format: str | None = None) -> str: ...
```

Provider-agnostic. Configured via `~/.paperprism/llm.yaml` + env var
pointed to by `api_key_env`. Supported providers:

- OpenAI-compatible (`openai`, `deepseek`, `moonshot`, `openrouter`,
  `qwen`) — share one code path with different `api_base`.
- Anthropic (`anthropic`) — dedicated code path.
- Google Gemini (`google`) — dedicated code path.
- Ollama (`ollama`) — local, no api key.

When adding a provider, register it in `llm.py` and
`resources/llm.default.yaml`; the extension's `lib/providers.ts` must
pick up the same name.

### LLM dimensions & summary

Dimensions are defined in `resources/dimensions.default.yaml` (or the
user override `~/.paperprism/dimensions.yaml`). Each dimension has a
`kind` (`label` or `text`), `source`, `max_chars`, and `prompt`.

The `summary` dimension (kind: `text`) generates a TL;DR of each
paper in 2–3 short sentences: **[Background] [Method] [Key Result]**.
Limitations are only included if the paper explicitly states one. The
prompt instructs the LLM not to fabricate limitations.

`classifier.py` builds a single LLM call that covers all dimensions
(including `summary`) in one pass. The input context (`PaperContext`)
includes:

- Paper metadata (title, abstract, authors)
- `pdf_head_text` — first page of the PDF (always available)
- `pdf_full_text` — full PDF text (truncated to `pdf_full_char_limit`,
  default 8000 chars; configured in `llm.yaml`)

Output is truncated per-dimension to the configured `max_chars`.

## Background work

- **Ingest** runs inline inside the FastAPI request (`worker.ingest_paper`
  in `/api/ingest`); Chrome only waits for acceptance, actual LLM work
  runs in the event loop after response.
- **Auto-tag on ingest** is triggered from `worker.py` after a paper is
  persisted, only when `llm.yaml.auto_tag_on_ingest: true`.
- **Batch auto-tag + topic synthesis** is an in-process job tracked by
  `auto_tag_jobs.py`. Jobs are in-memory; restart = lost. This is OK
  because the Agent lives next to the user and jobs are cheap to
  re-run.

## Commands cheatsheet

```bash
# === End-user / smoke-test (consumes the published wheel) ===
uvx paperprism-agent serve                # one-shot, ephemeral
uv tool install paperprism-agent          # persistent install
uv tool upgrade paperprism-agent          # after a new PyPI release
uv cache clean paperprism-agent           # force re-download

# === Dev loop (against repo source) ===
pip install -e .
paperprism-agent serve --log-level debug

# After editing Python, if launchd-managed:
paperprism-agent restart

# DB spelunking
sqlite3 ~/.paperprism/db.sqlite '.tables'
sqlite3 ~/.paperprism/db.sqlite 'SELECT schema_version FROM schema_meta;'

# Reset a user's state (destructive)
rm -rf ~/.paperprism
```

## Publishing to PyPI

`paperprism-agent` on PyPI is the **recommended install channel** for
end users (README's Quick start calls `uvx paperprism-agent serve`).
The wheel is built directly from this directory — `packaging/` is NOT
involved.

```bash
cd agent
rm -rf dist build *.egg-info
python -m build                           # hatchling → dist/*.whl + *.tar.gz
twine check dist/*                        # lints README rendering, classifiers
twine upload dist/*                       # real PyPI
# twine upload -r testpypi dist/*         # dry-run on TestPyPI first
```

Checklist before running `twine upload`:

- `pyproject.toml#version` bumped and matches the intended Git tag.
- `authors.email` is a real, monitored inbox.
- `license = { text = "Apache-2.0" }` and classifiers stay aligned
  with the top-level `LICENSE` file.
- `pyproject.toml#project.urls` points to the canonical GitHub repo.
- `python -m build` output contains **both** wheel and sdist; upload
  both.

After publishing, bump `homebrew/paperprism-agent.rb` (separate tap)
and update the GitHub Release body so fallback channels stay in sync.
Also: `uv cache clean paperprism-agent` locally before smoke-testing
to make sure `uvx` actually fetches the new version.

## Conventions for AI edits

- **Add a migration, don't mutate old ones.** Even a typo fix in a
  shipped migration would break existing users.
- **Go through `repository.py`** for any DB read/write. No raw SQL in
  `server.py`, `ingest.py`, `worker.py`.
- **Return plain dicts from routes** (already the pattern) — we're not
  using response_model everywhere; Pydantic only for request bodies
  where schema matters.
- **Logging**: use `log = logging.getLogger(__name__)`. Never
  `print()`.
- **Paths**: always `cfg.paths.*` accessors; never hardcode
  `~/.paperprism/...`.
- **Tests** (when added) belong at `agent/tests/` and should use a
  temporary home dir via `AgentConfig.from_env(home=tmp_path)`.
