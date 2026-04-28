# AGENTS.md — PaperPrism (root)

> Purpose: give an LLM coding agent the minimum correct mental model of
> this repo so it can make safe, targeted changes. Keep this file short.
> Deeper per-module docs live at `agent/AGENTS.md`,
> `extension/AGENTS.md`, `packaging/AGENTS.md`.

## TL;DR

PaperPrism = **Chrome extension** (capture + UI) **⇄ local Agent**
(FastAPI + SQLite + LLM) **⇄ hidden vault** on disk. Everything local,
nothing leaves the user's machine.

```
arxiv.org  →  Chrome download (user-visible)
                     │
          extension/ background.ts (WXT/React)
                     │ POST /api/ingest (archive.completed)
                     ▼
   agent/ paperprism_agent/server.py (FastAPI, 127.0.0.1:17321)
     │  ingest.py → copy PDF into ~/.paperprism/vault
     │  arxiv_client.py + pdf.py → enrich metadata
     │  classifier.py → LLM dimension labels
     │  tagger.py → 2–5 short tags per paper (auto-tag on ingest)
     │  repository.py → write to ~/.paperprism/db.sqlite
     ▼
   extension/ dashboard/ renders list/filter/topics via REST API
```

## Repo layout (only what matters)

```
agent/         Python 3.10+ FastAPI service. Source of truth for data model.
extension/     WXT + React + TS MV3 Chrome extension. All UI lives here.
packaging/     install.sh, PyInstaller spec, macOS .pkg, Homebrew formula.
.github/       Release automation (workflows/release.yml).
README.md      End-user + developer docs.
```

Per-module AGENTS.md:
- [agent/AGENTS.md](agent/AGENTS.md) — Python service internals
- [extension/AGENTS.md](extension/AGENTS.md) — Chrome extension internals
- [packaging/AGENTS.md](packaging/AGENTS.md) — distribution artifacts

## Ground rules for AI edits

1. **Respect the process boundary.** Extension and Agent are two
   processes talking over HTTP. Never import from `agent/` inside
   `extension/` or vice versa.
2. **Contract file: `extension/lib/agent.ts`.** If you add/modify a
   REST endpoint in `agent/src/paperprism_agent/server.py`, update the
   matching TypeScript function in `agent.ts` in the **same change
   set**. Consumer callsites are mostly in
   `extension/entrypoints/dashboard/` and `extension/entrypoints/options/`.
3. **Data model is SQLite + numbered migrations.** Do NOT hand-edit
   production DBs. Add a new file `agent/.../migrations/000N_foo.sql`;
   the Agent auto-applies on startup. Migrations are append-only.
4. **User state lives under `~/.paperprism/`**, never inside the repo.
   Touch with care — developer and user can share the same path.
5. **Keep secrets out of `llm.yaml`.** Keys go in
   `~/.paperprism/secrets.env` (chmod 600). See `agent/AGENTS.md`.
6. **No cloud calls from the Agent other than**: arxiv API,
   user-configured LLM endpoint. Any new outbound host is a
   review-blocking change.

## Build / run / test cheatsheet

```bash
# Agent
cd agent
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .
paperprism-agent serve          # foreground
paperprism-agent install        # macOS LaunchAgent
paperprism-agent restart        # after Python changes (editable install
                                # links source but process holds old imports)
paperprism-agent logs --follow

# Extension
cd extension
npm install                     # also runs `wxt prepare`
npm run dev                     # hot-reload
npm run build                   # one-shot → .output/chrome-mv3/
npm run compile                 # tsc --noEmit (type check)
npm run zip                     # → .output/paperprism-<ver>-chrome.zip

# Release packaging
bash packaging/pyinstaller/build.sh
bash packaging/macos/build_pkg.sh 0.1.0
```

Health check: `curl http://127.0.0.1:17321/api/health`.

## Versioning

- Extension: bump `wxt.config.ts#manifest.version` **and**
  `extension/package.json#version` together.
- Agent: bump `agent/pyproject.toml#version` (read at runtime for
  `/api/health`).
- Git tag `vX.Y.Z` triggers `.github/workflows/release.yml` which
  publishes PyInstaller binary + `.pkg` to GitHub Releases.

## Common pitfalls (don't re-learn these)

- `pip install -e .` only links source. After **adding a new runtime
  dependency** to `pyproject.toml`, rerun `pip install -e .`, otherwise
  the Agent will crash with `ModuleNotFoundError` on next restart.
- Agent runs on port **17321**, not 8765.
- Chrome's MV3 service worker sleeps. Long jobs (auto-tag) must live
  server-side, with the extension polling `/api/tags/auto/{id}`.
- WXT postinstall hook (`wxt prepare`) must succeed before `tsc` will
  type-check the extension. If types look broken, `rm -rf .wxt` and
  `npm install`.
- SQLite `ALTER TABLE` can `ADD COLUMN` but cannot `DROP COLUMN` on
  older sqlite3. Prefer leaving a deprecated column no-op rather than
  trying to drop it in a migration.
- `tags` are lowercased/hyphenated by `repository.normalise_tag`;
  always write via the repository helpers, never raw SQL, to keep
  normalisation consistent.

## When in doubt

- To **add a REST endpoint**: edit `agent/src/paperprism_agent/server.py`
  + `extension/lib/agent.ts` in the same commit.
- To **add a DB field**: write a new migration SQL, extend
  `repository.py`, then surface through `server.py` → `agent.ts` → UI.
- To **change LLM behaviour**: edit `classifier.py` (dimension labels)
  or `tagger.py` (per-paper tags + topic synthesis); both are pure
  prompt+parser modules with well-defined return types.
- To **ship a release**: bump versions, tag, let CI do the rest.
