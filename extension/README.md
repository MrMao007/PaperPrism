# PaperPrism Chrome Extension

Auto-organize arxiv papers into a local, multi-dimensional library.

This extension is the **capture layer and UI** of PaperPrism. Papers are
saved to the user's normal Downloads folder exactly as before; the
extension notifies a local **Agent** to mirror each finished PDF into a
**hidden workspace** where metadata extraction, LLM classification, and
auto-tagging happen. The same extension also ships an in-browser
Dashboard, a 4-step Options wizard, a bulk-import tool, and an
Auto-tag → Topic panel.

## Flow

```
User clicks arxiv PDF
        |
        v
Chrome saves to ~/Downloads/<filename>.pdf    (unchanged, visible)
        |
        | background SW reads the resolved filesystem path
        v
POST http://127.0.0.1:17321/api/ingest  (event=archive.completed,
                                          downloadPath=<absolute path>)
        |
        v
Local Agent copies to ~/.paperprism/vault/YYYY/MM/<arxivId>/paper.pdf
   runs arxiv API + PDF parse + LLM classification
   writes meta.json / notes.md sidecars
   builds symlinks across multi-dimensional views
```

The hidden vault lives under a dot-prefixed directory (hidden on
macOS/Linux). **The extension itself never writes there** -- Chrome's
security model forbids extensions from writing outside the Downloads
folder. The Agent owns the vault.

## What the extension does

1. **Detects arxiv downloads** via `chrome.downloads.onCreated`; no
   redirection, no filename rewriting.
2. **Records the on-disk path** after the download completes (via
   `chrome.downloads.search`).
3. **Emits two events** to the local Agent:
   - `archive.requested` — on download start, so the Agent can prefetch
     arxiv API metadata in parallel.
   - `archive.completed` — on download finish, carrying the absolute
     filesystem path. Triggers Agent to copy + classify + auto-tag.
4. **Popup UI** shows agent status, recent events, one-click archive
   current tab, one-click open Dashboard.
5. **Content script** injects a "Save to PaperPrism" button on
   `https://arxiv.org/abs/*` pages.
6. **Options page** runs a 4-step first-run wizard (probe Agent → pick
   LLM provider → enter API key → test + open Dashboard) and lets the
   user re-configure the Agent URL, token, LLM provider / model / key,
   auto-tag-on-ingest toggle, and enrichment / classification switches.
7. **Dashboard** (`entrypoints/dashboard/`) renders a full SPA inside
   the extension: paginated / filterable paper list, tag chip editor,
   per-row PDF viewer, delete, a **bulk toolbar** with
   **Import folder** (streams every PDF to `/api/ingest/upload` with
   live progress) and **Auto-tag selected** (drives a topic-synthesis
   job and navigates to the resulting Topic page), plus a **Topics**
   tab listing every saved topic.

## Layout

```
extension/
├── wxt.config.ts              # manifest + WXT config
├── entrypoints/
│   ├── background.ts          # onCreated / onChanged + agent comms
│   ├── content.ts             # abs-page "Save to PaperPrism" button
│   ├── popup/                 # toolbar popup UI (React)
│   ├── options/               # settings page + first-run wizard (React)
│   └── dashboard/             # in-extension Dashboard SPA (React),
│                              #   papers list, tags, topics, bulk
│                              #   import, auto-tag panel
├── lib/
│   ├── arxiv.ts               # URL / filename parsing
│   ├── agent.ts               # typed HTTP client for the local Agent
│   ├── settings.ts            # chrome.storage-backed settings
│   └── events.ts              # persistent ring buffer of recent events
└── package.json
```

## Development

Requires Node.js >= 18.

```bash
cd extension
npm install        # generates .wxt/tsconfig.json, pulls types
npm run dev        # launches Chrome with hot reload
# or
npm run build      # production bundle in .output/chrome-mv3/
```

Load unpacked build: `chrome://extensions` > Developer mode > Load unpacked
> select `.output/chrome-mv3/`.

## Settings

Two layers of settings live in different stores:

**1. Extension-side (`chrome.storage.local`, set from Options page):**

| Field                 | Default                       | Meaning                                          |
| --------------------- | ----------------------------- | ------------------------------------------------ |
| Hidden workspace path | `~/.paperprism/vault`         | Forwarded to Agent as `vaultPathHint`.           |
| Local Agent base URL  | `http://127.0.0.1:17321`      | `/api/health`, `/api/ingest`, etc. live here.    |
| Agent auth token      | (empty)                       | Sent via `X-PaperPrism-Token` header.            |
| Archive enabled       | on                            | Master switch for talking to the Agent.          |
| Notifications enabled | on                            | System notification after each archive event.    |

**2. Agent-side LLM config (`~/.paperprism/llm.yaml` + `secrets.env`,
edited via Options page → LLM section, persisted via
`PUT /api/llm/config`):**

| Field                  | Default | Meaning                                                    |
| ---------------------- | ------- | ---------------------------------------------------------- |
| Provider               | qwen    | openai / anthropic / google / qwen / deepseek / moonshot / openrouter / ollama |
| Model                  | per-provider | e.g. `qwen-plus`, `gpt-4o-mini`, `claude-3-5-sonnet`  |
| API base               | per-provider | OpenAI-compatible base URL                            |
| API key env            | per-provider | Name of env var holding the key (e.g. `QWEN_API_KEY`) |
| Enrichment enabled     | on      | Pull arxiv API metadata + PDF abstract on ingest            |
| Classification enabled | on      | Run LLM dimension classifier on ingest                      |
| Auto-tag on ingest     | on      | LLM-tag every paper added via `/api/ingest`                 |

Open the popup, click **Settings** (or `chrome://extensions` →
PaperPrism → Extension options), or click **Settings** in the
Dashboard header.

## Agent contract

The typed HTTP client in `lib/agent.ts` is the source of truth for the
extension ↔ Agent contract. Only the ingest endpoints are reproduced
below; for the full REST surface (papers / tags / topics / auto-tag
jobs / llm config) see [../agent/README.md](../agent/README.md).

The Agent must expose at a minimum:

- `GET  /api/health` -> `200 OK` when reachable.
- `POST /api/ingest` -> `200 OK` with JSON body:

  ```ts
  interface IngestRequest {
    event: 'archive.requested' | 'archive.completed';
    arxivId: { id: string; version?: string; fullId: string; legacy: boolean };
    sourceUrl: string;
    downloadPath?: string;   // absolute filesystem path, set on archive.completed
    vaultPathHint?: string;  // where the user wants the hidden vault, e.g. ~/.paperprism/vault
    downloadId?: number;
    triggerClassification: boolean;
    absUrl?: string;
    emittedAt: string;       // ISO 8601
  }

  interface IngestResponse {
    accepted: boolean;
    vaultPath?: string;      // absolute path inside the vault where the Agent placed the copy
    status?: 'queued' | 'classified' | 'needs_review';
    message?: string;
  }
  ```

The Agent is responsible for:

1. Resolving `~` and creating the hidden vault directory if missing.
2. Copying the file from `downloadPath` into the vault (not moving — the
   original stays in Downloads as the user expects).
3. Fetching arxiv API metadata + parsing PDF abstract.
4. Running LLM classification (topic / task / venue / methods).
5. **Auto-tagging** the paper with 2–5 short LLM tags (if
   `auto_tag_on_ingest` is on).
6. Writing `meta.json` sidecar and indexing into `db.sqlite`.

## Known limitations

- **Downloads stay in Downloads.** By design. If the user empties Downloads,
  the original file disappears but the vault copy is intact.
- **De-duplication** is the Agent's job: a re-download of the same paper
  is detected by arxiv id + SHA-256 and skipped (or treated as version
  bump).
- **Bulk-import re-uses the Agent's ingest path**, so every PDF goes
  through the same arxiv-id resolution (filename → LLM fallback) and
  LLM classification + auto-tag pipeline. Expect it to be I/O- and
  token-bound on large folders.
- **Chrome Web Store listing** is pending. For now install unpacked
  from `.output/chrome-mv3/` after `npm run build`.
