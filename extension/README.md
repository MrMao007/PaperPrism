# PaperPrism Chrome Extension

Auto-organize arxiv papers into a local, multi-dimensional library.

This extension is the **capture layer** of PaperPrism. Papers are saved to
the user's normal Downloads folder exactly as before; the extension then
notifies a local **Agent** to mirror each finished PDF into a **hidden
workspace** where it runs metadata extraction and LLM classification.

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
     filesystem path. Triggers Agent to copy + classify.
4. **Popup UI** shows agent status, recent events, one-click archive
   current tab, one-click open Dashboard.
5. **Content script** injects a "Save to PaperPrism" button on
   `https://arxiv.org/abs/*` pages.

## Layout

```
extension/
├── wxt.config.ts              # manifest + WXT config
├── entrypoints/
│   ├── background.ts          # onCreated / onChanged + agent comms
│   ├── content.ts             # abs-page "Save to PaperPrism" button
│   ├── popup/                 # toolbar popup UI (React)
│   └── options/               # settings page (React)
├── lib/
│   ├── arxiv.ts               # URL / filename parsing
│   ├── agent.ts               # HTTP client for the local Agent
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

Open the popup, click **Settings** (or go to `chrome://extensions` >
PaperPrism > Extension options).

| Field                 | Default                       | Meaning                                          |
| --------------------- | ----------------------------- | ------------------------------------------------ |
| Hidden workspace path | `~/.paperprism/vault`         | Forwarded to Agent as `vaultPathHint`.           |
| Local Agent base URL  | `http://127.0.0.1:17321`      | `/api/health`, `/api/ingest` live here.          |
| Agent auth token      | (empty)                       | Sent via `X-PaperPrism-Token` header.            |
| Archive enabled       | on                            | Master switch for talking to the Agent.          |
| Notifications enabled | on                            | System notification after each archive event.    |

## Agent contract

The Agent must expose:

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
2. Copying the file from `downloadPath` into the vault (not moving -- the
   original stays in Downloads as the user expects).
3. Fetching arxiv API metadata + parsing PDF abstract.
4. Running LLM classification (topic / task / venue / methods).
5. Writing `meta.json` + `notes.md` sidecars.
6. Building symlinks under `<vault>/views/by-topic/...`, `by-year/...`, etc.

## Known limitations

- **Downloads stay in Downloads.** By design. If the user empties Downloads,
  the original file disappears but the vault copy is intact.
- **De-duplication** is the Agent's job: a re-download of the same paper
  should be detected by SHA-256 and skipped (or treated as version bump).
- **Windows symlinks** require developer mode or admin rights; the Agent
  should fall back to hard links or file copies.
