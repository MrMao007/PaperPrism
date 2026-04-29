# PaperPrism Agent

Local HTTP service that receives archive events from the PaperPrism Chrome
extension, mirrors arxiv PDFs into a hidden workspace vault
(`~/.paperprism/vault`), enriches them with arxiv-API + PDF metadata,
classifies them with the user's LLM, auto-tags every new paper, and
exposes a small REST API that the extension's Dashboard / Options /
Topic pages talk to.

## Stack

- Python 3.10+
- FastAPI + uvicorn
- Pydantic v2
- SQLite (with FTS5) for metadata, tags, topics, and jobs
- PyPDF + arxiv API for enrichment
- Pluggable LLM backends: OpenAI / Anthropic / Google Gemini / Qwen /
  DeepSeek / Moonshot / OpenRouter / Ollama (configured via
  `~/.paperprism/llm.yaml` + `secrets.env`)

## Quick start

The Agent ships on [PyPI](https://pypi.org/project/paperprism-agent/) as
`paperprism-agent`. Pick whichever install mode suits you.

### Option A — `uvx` (zero-install, one-off)

Best for "just try it" or CI smoke tests. No venv to manage.

```bash
# Needs uv 0.4+ — see https://docs.astral.sh/uv/
uvx paperprism-agent serve
```

Each invocation runs in a throwaway environment, so this is **not**
suitable for `paperprism-agent install` (launchd needs a stable path —
see Option B).

### Option B — `uv tool install` (recommended for daily use)

Installs a stable shim at `~/.local/bin/paperprism-agent` that launchd
can call into. Upgrades are `uv tool upgrade`.

```bash
uv tool install paperprism-agent
paperprism-agent serve            # foreground test
paperprism-agent install          # register launchd LaunchAgent (macOS)
```

### Option C — `pipx` / `pip` in a venv

```bash
pipx install paperprism-agent
# or
python3 -m venv .venv && source .venv/bin/activate
pip install paperprism-agent
paperprism-agent serve
```

### Option D — editable checkout (for contributors)

```bash
cd agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
paperprism-agent serve
# or equivalently:
python -m paperprism_agent serve
```

Default bind: `http://127.0.0.1:17321`.

## Smoke test

```bash
# Health
curl -s http://127.0.0.1:17321/api/health | jq .

# Fake an archive.completed event pointing at any local PDF
curl -s -X POST http://127.0.0.1:17321/api/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "event": "archive.completed",
    "arxivId": {"id":"2401.08281","fullId":"2401.08281v1","version":"v1","legacy":false},
    "sourceUrl": "https://arxiv.org/pdf/2401.08281v1.pdf",
    "absUrl": "https://arxiv.org/abs/2401.08281",
    "downloadPath": "/absolute/path/to/any.pdf",
    "triggerClassification": true,
    "emittedAt": "2026-04-27T10:00:00Z"
  }' | jq .
```

You should see the file mirrored under:

```
~/.paperprism/vault/YYYY/MM/2401.08281v1/
├── paper.pdf
└── meta.json
```

…and, if an LLM is configured and `auto_tag_on_ingest` is on (default),
the paper will gain 2–5 tags visible via `GET /api/tags` within a few
seconds.

## HTTP API (summary)

All endpoints are rooted at `http://127.0.0.1:17321`. Auth header
`X-PaperPrism-Token` required only if `--token` (or `PAPERPRISM_TOKEN`)
is set.

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/health` | Liveness + version |
| `POST` | `/api/ingest` | Extension's archive.requested / archive.completed event sink |
| `POST` | `/api/ingest/upload` | Multipart PDF upload used by the Dashboard bulk-import |
| `GET`  | `/api/papers` | Paginated paper list with filters (tag, topic, free text) |
| `GET`  | `/api/papers/{id}` | Single paper detail (metadata + tags + topics) |
| `DELETE` | `/api/papers/{id}` | Remove a paper (and its vault files) |
| `GET`  | `/api/papers/{id}/pdf` | Stream the archived PDF |
| `GET`  | `/api/dimensions/values` | Distinct values for each LLM dimension (for Dashboard filters) |
| `GET`  | `/api/tasks/stats` | Per-dimension counts (for Dashboard headline stats) |
| `GET`  | `/api/tags` | List all tags + counts |
| `GET`  | `/api/papers/{id}/tags` | Tags attached to a given paper |
| `POST` | `/api/papers/{id}/tags` | Edit tags: body `{"add":[...],"remove":[...]}` |
| `POST` | `/api/tags/auto` | Start a batch auto-tag + topic-synthesis job for the given paper ids |
| `GET`  | `/api/tags/auto/{job_id}` | Poll status of an auto-tag job |
| `DELETE` | `/api/tags/auto/{job_id}` | Cancel a running auto-tag job |
| `POST` | `/api/tags/auto/{job_id}/retry` | Retry the failed papers of a finished job |
| `GET`  | `/api/topics` | List topics (each with name, summary, all tags) |
| `GET`  | `/api/topics/{slug}` | Topic detail + papers |
| `DELETE` | `/api/topics/{topic_id}` | Delete a topic (papers keep their tags) |
| `GET`  | `/api/llm/config` | Read current LLM provider / model / toggles |
| `PUT`  | `/api/llm/config` | Update LLM config (written to `llm.yaml` + `secrets.env`) |
| `POST` | `/api/llm/test` | Tiny chat request to verify the configured key |

Concrete request/response schemas live in
`paperprism_agent/models.py` and `paperprism_agent/server.py`, and the
TypeScript client is `extension/lib/agent.ts`.

## CLI

| Command | Purpose |
|---|---|
| `paperprism-agent serve` | Run HTTP server in the foreground |
| `paperprism-agent install` | macOS: install launchd LaunchAgent and start it |
| `paperprism-agent uninstall` | macOS: stop and remove LaunchAgent |
| `paperprism-agent status` | macOS: print launchctl state |
| `paperprism-agent restart` | macOS: force launchd to (re)start the service |
| `paperprism-agent logs` | Tail logs (`--which out\|err\|launchd-out\|launchd-err`, `--follow`) |
| `paperprism-agent version` | Print version |

Flags for `serve` / `install`:

- `--host`, `--port`, `--token`, `--home`
- `serve` also: `--log-level`
- Env fallbacks: `PAPERPRISM_HOST`, `PAPERPRISM_PORT`, `PAPERPRISM_TOKEN`, `PAPERPRISM_HOME`

## Background autostart on macOS

```bash
paperprism-agent install     # writes ~/Library/LaunchAgents/com.paperprism.agent.plist
                             # and bootstraps it into gui/<uid>
paperprism-agent status      # shows launchctl state
paperprism-agent logs --follow
paperprism-agent uninstall   # stop + delete plist
```

The LaunchAgent:

- starts at user login (`RunAtLoad`)
- auto-restarts on crash (`KeepAlive.Crashed`), **not** on clean stops, so
  `uninstall` and graceful SIGTERM actually work
- rate-limits respawns (`ThrottleInterval=10`) to avoid hot-looping
- captures launchd-level stdout/stderr to `logs/launchd.{out,err}.log` while
  the app's structured log continues to flow into `logs/agent.{out,err}.log`
- uses `sys.executable` from the venv you ran `install` from, so upgrading is
  just `pip install -e . && paperprism-agent restart`

### Linux (future)

A `systemd --user` unit will be generated by `install` when the platform
is Linux. For now, run `paperprism-agent serve` in a terminal or wire it
into your own systemd user unit.

## Filesystem layout

```
~/.paperprism/
├── runtime.json        # {port, pid, token, version}
├── llm.yaml            # provider, model, api_base, api_key_env, auto_tag_on_ingest, ...
├── secrets.env         # API keys (mode 600)
├── db.sqlite           # papers, tags, paper_tags, topics, jobs, FTS5
├── logs/
│   ├── agent.out.log
│   ├── agent.err.log
│   ├── launchd.out.log
│   └── launchd.err.log
└── vault/
    └── YYYY/MM/<arxivId>/
        ├── paper.pdf
        └── meta.json
```

`db.sqlite` is managed by numbered migrations under
`paperprism_agent/migrations/` and applied automatically on startup
whenever the file's `schema_version` is behind the code.

## LLM configuration

`~/.paperprism/llm.yaml` (also editable from the extension's Options
page → LLM section) shape:

```yaml
provider: qwen            # one of: openai, anthropic, google, qwen,
                          # deepseek, moonshot, openrouter, ollama
model: qwen-plus
api_base: https://dashscope.aliyuncs.com/compatible-mode/v1
api_key_env: QWEN_API_KEY  # name of the env var holding the key
enrichment_enabled: true   # pull arxiv API metadata + PDF abstract
classification_enabled: true
auto_tag_on_ingest: true   # LLM-tag every paper added via /api/ingest
```

Secrets are never written into `llm.yaml`; they sit in
`~/.paperprism/secrets.env` at mode 600 and are loaded into the Agent's
process environment on startup (and live-injected when the Options
wizard saves a new key).

## Contract with the extension

See `extension/lib/agent.ts`. The server validates incoming JSON with
`paperprism_agent.models`. Breaking changes on either side must bump
`meta.schema_version` and ship a matching SQLite migration.
