# PaperPrism Privacy Policy

**Last updated:** 2026-05-08
**Contact:** mty1209@gmail.com · <https://github.com/MrMao007/PaperPrism>

## Summary

PaperPrism is a local-first Chrome extension paired with a self-hosted
local Agent. **We do not collect, transmit, sell, or share any user
data.** All papers, metadata, tags, and API keys remain on the user's
own device.

## What the extension stores

The extension stores the following items using `chrome.storage.local`
on the user's device only:

- The URL of the local Agent (default `http://127.0.0.1:17321`).
- The user's chosen LLM provider preference and UI state.
- Whether the first-run wizard has been completed.
- Dashboard view preferences (filters, sort, column visibility).

No paper content, no PDF bytes, and no API keys are stored in
`chrome.storage`.

## What the local Agent stores (on the user's machine)

- `~/.paperprism/vault/` — archived PDF files.
- `~/.paperprism/db.sqlite` — local SQLite database (paper metadata,
  tags, topics, LLM-generated summaries and dimension labels).
- `~/.paperprism/secrets.env` — user's LLM API keys, mode `600`
  (readable only by the user).
- `~/.paperprism/logs/` — rotating log files.

## What the extension sends over the network

The extension communicates with exactly two destinations:

1. `https://arxiv.org/*` — to download the arxiv PDF the user
   explicitly requested. Requests happen only in direct response to
   user action.
2. `http://127.0.0.1:*` / `http://localhost:*` — to talk to the user's
   own local Agent running on the same machine.

The extension does **not** contact any PaperPrism-operated server,
analytics service, ad network, or third party. There is no
PaperPrism-operated server.

## What the local Agent sends over the network

When the user configures an LLM provider, the local Agent (running on
the user's own machine) makes outbound HTTPS calls to that provider's
API (OpenAI, Anthropic, Google, Qwen, DeepSeek, Moonshot, OpenRouter,
or a local Ollama instance) to classify and tag the paper. The text
sent to the LLM includes the paper's title, abstract, and (when
available) the full PDF text (truncated to a configurable limit). The
user chooses the provider, supplies the API key, and can opt out at
any time. These calls are governed by the chosen provider's privacy
policy.

## Data sharing

We do not sell, share, or transfer user data to any third party.

## User control

- Uninstall the extension: all `chrome.storage` data is removed by
  Chrome.
- Delete `~/.paperprism/`: all local Agent data (PDFs, database,
  secrets, logs) is removed.
- Revoke LLM access: delete your API key from the Options page or from
  `~/.paperprism/secrets.env`.

## Changes

If this policy changes, the updated version will be committed to
`docs/privacy.md` in the project repository with a new "Last updated"
date.

## Contact

For privacy questions: open an issue at
<https://github.com/MrMao007/PaperPrism/issues> or email
<mty1209@gmail.com>.
