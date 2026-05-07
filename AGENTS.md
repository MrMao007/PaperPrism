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
     │  classifier.py → LLM dimension labels + TL;DR summary
     │  tagger.py → 2–5 short tags per paper (auto-tag on ingest)
     │  repository.py → write to ~/.paperprism/db.sqlite
     │  events.py       → append-only ledger (same TXN)
     ▼
   extension/ dashboard/ renders list/filter/topics via REST API
                     │  LLM TL;DR summaries, inline tag editing,
                     │  three-way search, Research Weekly sidebar
```

## Repo layout (only what matters)

```
agent/         Python 3.10+ FastAPI service. Source of truth for data model.
               Published to PyPI as `paperprism-agent` (hatchling build).
               Includes Memory Ledger (SQLite `events` table) — every
               mutation is logged append-only inside the same TXN.
extension/     WXT + React + TS MV3 Chrome extension. All UI lives here.
               Published to the Chrome Web Store
               (item id `jjlclcocagjnohgcpbgcpkodcnmmabif`).
packaging/     Fallback channels: install.sh, PyInstaller spec, macOS .pkg,
               Homebrew formula. (PyPI wheel is built from `agent/` directly,
               not here.)
docs/          Public-facing docs linked from the Chrome Web Store listing.
               Currently holds `privacy.md` (Privacy Policy).
store-promo/   Web Store marketing assets (440×280, 1400×560 PNGs).
store-screenshots/  Web Store screenshots (1280×800 PNGs).
.github/       Release automation (workflows/release.yml).
README.md      End-user + developer docs. Quick start section is the
               canonical onboarding path.
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

**End-user path (NOT for editing the repo)** — this is what README's
"Quick start" recommends, so anything an AI agent ships must not break
it:

```bash
uvx paperprism-agent serve               # one-off, no install
uv tool install paperprism-agent         # persistent install
uv tool upgrade paperprism-agent         # after a new PyPI release
```

**Developer path** (editable install against the repo):

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
npm run zip                     # → .output/paperprism-extension-<ver>-chrome.zip
                                # (this is the zip uploaded to the Chrome Web Store)

# Release packaging (fallback channels only)
bash packaging/pyinstaller/build.sh
bash packaging/macos/build_pkg.sh 0.1.0

# PyPI release (from agent/)
cd agent
rm -rf dist && python -m build && twine check dist/*
twine upload dist/*             # needs ~/.pypirc [pypi] or TWINE_* envs
```

Health check: `curl http://127.0.0.1:17321/api/health`.

## Versioning

- Extension: bump `wxt.config.ts#manifest.version` **and**
  `extension/package.json#version` together. Upload the new
  `paperprism-extension-<ver>-chrome.zip` via the Chrome Web Store
  developer dashboard → re-submit for review.
- Agent: bump `agent/pyproject.toml#version` (read at runtime for
  `/api/health`). Publish to PyPI with `python -m build && twine
  upload dist/*` from `agent/`; `uvx`/`uv tool install` users pick it
  up automatically (may need `uv cache clean paperprism-agent` on the
  first hit).
- Git tag `vX.Y.Z` triggers `.github/workflows/release.yml` which
  publishes PyInstaller binary + `.pkg` to GitHub Releases. PyPI
  publish is currently a manual `twine upload` step — see `agent/AGENTS.md`.

## Common pitfalls (don't re-learn these)

- `pip install -e .` only links source. After **adding a new runtime
  dependency** to `pyproject.toml`, rerun `pip install -e .`, otherwise
  the Agent will crash with `ModuleNotFoundError` on next restart.
- Agent runs on port **17321**, not 8765. If already occupied (e.g. a
  launchd-managed instance), dev runs should use `--port 17322 --home
  /tmp/pp-dev-home` to avoid stomping on user state.
- `uvx`/`uv tool install` caches wheels under `~/.cache/uv/`. After
  publishing a new PyPI version, run `uv cache clean paperprism-agent`
  before re-verifying, otherwise you may still run the old one.
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
- `~/.paperprism/dimensions.yaml` overrides the bundled default. When
  adding a new dimension, update **both** files and restart the Agent.
- `tabs` permission has been removed from the extension manifest;
  use `window.open` instead of `chrome.tabs.create`.
- GitHub's `macos-13` Intel runner has been retired; `release.yml`
  only builds `macos-arm64`. Intel Mac users run via Rosetta 2 or
  install via `uv tool install paperprism-agent`.

## When in doubt

- To **add a REST endpoint**: edit `agent/src/paperprism_agent/server.py`
  + `extension/lib/agent.ts` in the same commit.
- To **add a DB field**: write a new migration SQL, extend
  `repository.py`, then surface through `server.py` → `agent.ts` → UI.
- To **change LLM behaviour**: edit `classifier.py` (dimension labels)
  or `tagger.py` (per-paper tags + topic synthesis); both are pure
  prompt+parser modules with well-defined return types.
- To **ship a release**: bump versions, tag, let CI do the rest.
