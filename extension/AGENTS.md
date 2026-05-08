# AGENTS.md — Extension (Chrome MV3, WXT + React + TS)

> Scope: everything under `extension/`. Complements the root
> [AGENTS.md](../AGENTS.md). For the Agent's REST surface see
> [../agent/AGENTS.md](../agent/AGENTS.md).

## Purpose

The capture layer **and** the user-facing UI:

1. intercepts arxiv downloads and notifies the local Agent,
2. renders a popup, an Options page (+ first-run wizard), an
   in-browser Dashboard SPA with paper/tag/topic views, LLM TL;DR
      summaries, inline tag editing, three-way search, weekly digest
      sidebar, and batch tools, plus an **Atlas** map view
   (`entrypoints/map/`) that visualises the library + arXiv feed as a
   2D star field with an "Add to Library" action wired to
   `POST /api/ingest/feed`,
3. speaks to the Agent exclusively through `lib/agent.ts`.

## Tech choices

- **WXT** (`wxt.dev`) — MV3 bundler, handles manifest + entrypoints +
  HMR. Do not roll your own webpack config.
- **React 18 + TypeScript**.
- **Vanilla `chrome.storage.local`** for extension settings (see
  `lib/settings.ts`). No Redux, no Zustand.
- **fetch** via the helpers in `lib/agent.ts`. Do not import any HTTP
  library.

## Directory map

```
extension/
├── wxt.config.ts                 # manifest (name/desc/version/permissions/icons)
├── package.json                  # scripts: dev / build / zip / compile
├── entrypoints/
│   ├── background.ts             # MV3 service worker
│   │                             #   - chrome.downloads.onCreated/onChanged
│   │                             #   - emits archive.requested / .completed
│   │                             #   - talks to Agent via lib/agent.ts
│   ├── content.ts                # Injects "Save to PaperPrism" on abs pages
│   ├── popup/                    # Toolbar popup (React)
│   │   ├── index.html
│   │   ├── main.tsx
│   │   └── App.tsx               # shows Agent status, last events, buttons
│   ├── options/                  # Options page (React)
│   │   ├── index.html
│   │   ├── main.tsx
│   │   ├── App.tsx               # full settings editor + LLM section
│   │   └── Wizard.tsx            # 4-step first-run wizard
│   └── dashboard/                # Full SPA (React, hash-router)
│       ├── index.html
│       ├── main.tsx
│       ├── App.tsx               # paper list, filters, bulk toolbar, LLM summary,
│       │                         #   inline tag editing, weekly research digest sidebar
│       ├── AutoTagPanel.tsx      # modal for batch auto-tag → topic
│       ├── TopicsView.tsx        # /#/topics + /#/topics/:slug pages
│       ├── router.ts             # tiny hash router
│       └── style.css
└── lib/
    ├── arxiv.ts                  # parse arxiv URL / filename → {id, version, legacy}
    ├── agent.ts                  # typed HTTP client (SOURCE OF TRUTH for API shape)
    ├── events.ts                 # recent-events ring buffer in chrome.storage
    ├── providers.ts              # LLM provider metadata (defaults, api_base, env)
    └── settings.ts               # Settings type + load/save/onChange
```

## Module boundaries

- `background.ts` may import from `lib/*` only. Never import React.
- `entrypoints/popup|options|dashboard/*` may import from `lib/*` and
  React. They must NOT import from each other; the three are separate
  bundle entrypoints and sharing would cause duplication.
- `lib/agent.ts` is the only place that calls `fetch` against the Agent.
  Callers receive typed promises, never raw `Response`.

## Contract with the Agent

`lib/agent.ts` mirrors `agent/src/paperprism_agent/server.py`. When the
Agent adds or changes an endpoint:

1. Update the TypeScript function signature (+ the `interface` it
   returns) in `lib/agent.ts`.
2. Update the single calling site (usually in dashboard / options).
3. Rebuild (`npm run build`) and reload the unpacked extension.

Breaking changes MUST also bump Agent's ingest `schema_version` and
ship a DB migration if persistence is involved.

## Settings & storage

Two stores, clearly separated:

- **Extension-side (`chrome.storage.local`)** — see `lib/settings.ts`:
  `agentBaseUrl`, `agentToken`, `archiveEnabled`, `notifyEnabled`,
  `vaultPathHint` (hint forwarded to Agent), `wizardCompleted`. Written
  only from the Options page (or the first-run wizard).
- **Agent-side (`~/.paperprism/llm.yaml`)** — LLM config. The Options
  page reads it via `GET /api/llm/config` and writes via `PUT
  /api/llm/config`. Never duplicate LLM config into `chrome.storage`.

## Build & dev commands

```bash
npm install                       # runs `wxt prepare` (generates .wxt/tsconfig.json)
npm run dev                       # HMR dev server, opens Chrome with extension
npm run build                     # one-shot production → .output/chrome-mv3/
npm run compile                   # tsc --noEmit (pure type check)
npm run zip                       # → .output/paperprism-extension-<ver>-chrome.zip
                                  #   (the artifact you upload to the Chrome Web Store)
```

Loading unpacked (dev or pre-review smoke test):
`chrome://extensions` → Developer mode → Load unpacked → pick
`extension/.output/chrome-mv3/`.

## Manifest (current)

Defined in `wxt.config.ts`:

- `permissions`: `downloads`, `storage`, `notifications`
- `host_permissions`:
  - `https://arxiv.org/*` (PDF download triggers)
  - `http://127.0.0.1/*`, `http://localhost/*` (Agent)
- `action.default_popup`: `popup.html`
- `options_ui`: `options.html`, `open_in_tab: true`
- `icons`: shipped from `extension/public/icon/{16,32,48,128}.png`.
  Keep `wxt.config.ts#manifest.icons` in sync if filenames ever change.

## Distribution (Chrome Web Store)

- **Item id**: `jjlclcocagjnohgcpbgcpkodcnmmabif`
- **Store URL**: <https://chromewebstore.google.com/detail/jjlclcocagjnohgcpbgcpkodcnmmabif>
- **Upload artifact**: `extension/.output/paperprism-extension-<ver>-chrome.zip`
  (produced by `npm run zip`). `wxt zip` auto-excludes sourcemaps and
  respects the manifest, so upload it as-is — do NOT re-zip
  `.output/chrome-mv3/` manually.
- **Listing category**: *Productivity* (效率工具). Not *Tools*.
- **Privacy policy URL** (required): <https://github.com/MrMao007/PaperPrism/blob/main/docs/privacy.md>.
  Source lives at [/docs/privacy.md](../docs/privacy.md) — update there
  and commit to `main` before submitting a new listing revision that
  changes data handling.
- **Marketing assets**: `/store-promo/logo-440x280.png` (small tile,
  required) and `/store-promo/logo-1400x560.png` (marquee, optional).
- **Screenshots**: `/store-screenshots/{1..5}.png`, each 1280×800 PNG.
- **Permission justifications**: kept in project memory; any change to
  `permissions` / `host_permissions` in `wxt.config.ts` requires
  re-submitting updated justification copy and usually re-triggers
  review.

Review cycle: first submission ~1–3 working days. Rejections almost
always cite a specific permission whose justification is too vague —
fix the wording and resubmit; follow-up review is usually same-day.

## Conventions for AI edits

- **Don't widen `host_permissions`**. Any new host is a review-blocking
  change; prefer routing new calls through the local Agent.
- **Don't hardcode `http://127.0.0.1:17321`**. Read `agentBaseUrl`
  from settings.
- **Every authed call must attach `X-PaperPrism-Token`** if
  `agentToken` is non-empty. `lib/agent.ts` handles this centrally; do
  not bypass it.
- **Background worker can be killed at any time.** Any multi-step flow
  needs to be resumable (persisted in `chrome.storage` or server-side
  in a job row). Do not rely on in-memory state surviving across
  events.
- **Don't use `chrome.tabs` API.** Opening URLs should use `window.open`.
  The `tabs` permission was removed after Chrome Web Store review rejection.
- **React entrypoints are separate bundles.** Shared components go in
  `lib/` or a future `lib/ui/`, not in a sibling entrypoint directory.
- **CSS is per-entrypoint.** Styling for the Dashboard lives in
  `entrypoints/dashboard/style.css` and stays there.
- **Keep `entrypoints/options/Wizard.tsx` idempotent.** Users may
  re-run the wizard; saves must upsert, never duplicate.

## Common pitfalls

- After editing `manifest` in `wxt.config.ts`, the service worker
  sometimes caches the old version. Click **Update** on the
  `chrome://extensions` card.
- `npm install` must complete before `npm run compile` — WXT's
  `postinstall` generates `.wxt/tsconfig.json`.
- `chrome.downloads.onDeterminingFilename` is **not** used; we do not
  rewrite filenames. Downloads remain in the user's Downloads folder
  untouched.
- MV3 service workers can be terminated mid-fetch. Always await the
  Agent response inside the event handler and handle 502/timeout.
- **JSX text nodes do not parse `\uXXXX` escapes.** Writing
  `<button>\u00d7</button>` ships the literal 6 characters to the
  DOM; use the real character (`×`) or wrap as
  `<button>{'\u00d7'}</button>`. Any escape inside `{...}` /
  string-literal contexts is fine — only bare JSX text is the trap.
