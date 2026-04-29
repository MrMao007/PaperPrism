# PaperPrism
<img width="1024" height="314" alt="1777376072343image" src="https://github.com/user-attachments/assets/1da69e2d-9a92-45e2-995e-eb2f100e88b6" />

<p align="center">
  <a href="https://pypi.org/project/paperprism-agent/"><img src="https://img.shields.io/pypi/v/paperprism-agent.svg?label=PyPI&color=2b6cb0" alt="PyPI version"></a>
  <a href="https://pypi.org/project/paperprism-agent/"><img src="https://img.shields.io/pypi/pyversions/paperprism-agent.svg?label=python" alt="Python versions"></a>
  <a href="https://github.com/MrMao007/PaperPrism/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-brightgreen.svg" alt="License: Apache-2.0"></a>
  <img src="https://img.shields.io/badge/manifest-MV3-f59e0b.svg" alt="Chrome MV3">
  <img src="https://img.shields.io/badge/local--first-yes-7c3aed.svg" alt="Local-first">
  <a href="https://github.com/MrMao007/PaperPrism/actions/workflows/release.yml"><img src="https://github.com/MrMao007/PaperPrism/actions/workflows/release.yml/badge.svg?branch=main" alt="Release CI"></a>
  <a href="https://github.com/MrMao007/PaperPrism/releases/latest"><img src="https://img.shields.io/github/v/release/MrMao007/PaperPrism?display_name=tag&sort=semver" alt="Latest release"></a>
  <a href="https://github.com/MrMao007/PaperPrism/stargazers"><img src="https://img.shields.io/github/stars/MrMao007/PaperPrism?style=social" alt="GitHub stars"></a>
</p>

A local-first, privacy-preserving arxiv paper organizer. A Chrome extension
watches your arxiv downloads; a tiny local Agent mirrors each paper into a
hidden workspace, extracts metadata, and classifies them with your LLM of
choice. No papers ever leave your machine.

- **Chrome extension** — popup archive button, Options first-run wizard,
  built-in Dashboard to browse / filter / view PDF / tag / delete papers,
  one-click **Settings** entry from the Dashboard, **bulk-import an
  existing folder of PDFs** with per-file progress, and a batch
  **Auto-tag → Topic** panel that turns a selection of papers into a
  themed topic page.
  <p align="center">
  <img width="352" height="270" alt="A31A2FD0" src="https://github.com/user-attachments/assets/3652f80f-59f8-474c-af97-da809f2a4cf2" />
  </p>
- **Local Agent** — FastAPI service (default `http://127.0.0.1:17321`),
  SQLite + FTS5 store, LLM classifier (OpenAI / Anthropic / Google Gemini
  / Qwen / DeepSeek / Moonshot / OpenRouter / Ollama), two-step arxiv-id
  resolver for legacy PDFs (filename → LLM fallback), **auto-tag on
  ingest** (each new paper gets 2–5 short LLM tags), auto-start at login.
  <p align="center">
  <img width="1183" height="747" alt="70312B11-77D9-4BF1-9FA5-A3E14D9C31AD" src="https://github.com/user-attachments/assets/b4d29c49-08d6-4e7e-bdd2-03548dbbef31" />
  </p>
- **Tags & Topics** — every paper accumulates tags (LLM-generated on
  ingest + user-added); any selection of papers can be summarised into a
  Topic card with a name, a one-liner summary, and the full set of tags
  shared by its papers. All state stays in local SQLite.
  <p align="center">
  <img width="400" height="365" alt="FDF3F1E1-0B56-4595-A73F-C2BDA709C97D" src="https://github.com/user-attachments/assets/abda39eb-018f-40c9-94a8-27c972dc93d8" />
  </p>
  
## Quick start (2 steps)

The fastest way to try PaperPrism. No source checkout, no build.

### 1. Install the Chrome extension

[![Install from Chrome Web Store](https://img.shields.io/chrome-web-store/v/jjlclcocagjnohgcpbgcpkodcnmmabif?label=Chrome%20Web%20Store&color=4285F4&logo=googlechrome&logoColor=white)](https://chromewebstore.google.com/detail/jjlclcocagjnohgcpbgcpkodcnmmabif)
[![Users](https://img.shields.io/chrome-web-store/users/jjlclcocagjnohgcpbgcpkodcnmmabif?label=users&color=4285F4)](https://chromewebstore.google.com/detail/jjlclcocagjnohgcpbgcpkodcnmmabif)
[![Rating](https://img.shields.io/chrome-web-store/rating/jjlclcocagjnohgcpbgcpkodcnmmabif?color=4285F4)](https://chromewebstore.google.com/detail/jjlclcocagjnohgcpbgcpkodcnmmabif)

Open the [PaperPrism page on the Chrome Web Store](https://chromewebstore.google.com/detail/jjlclcocagjnohgcpbgcpkodcnmmabif)
and click **Add to Chrome**.

<details>
<summary>Or load it unpacked from a release zip (no Web Store needed)</summary>

1. Download the latest `paperprism-extension-<version>-chrome.zip`
   from the [Releases page](https://github.com/MrMao007/PaperPrism/releases)
   *(or build it yourself with `cd extension && npm install && npm run build && npm run zip`)*.
2. Unzip it, open `chrome://extensions`, toggle **Developer mode**
   on, click **Load unpacked**, and pick the unzipped folder.

</details>

### 2. Start the local Agent with `uvx`

```bash
uvx paperprism-agent serve
```

That's it. Click the PaperPrism toolbar icon — the popup should show
**Agent: online** and a 4-step first-run wizard helps you pick an LLM
provider and paste an API key. Then download any arxiv PDF and
PaperPrism will archive + classify + auto-tag it within seconds.

Prerequisite: [`uv`](https://docs.astral.sh/uv/) 0.4+
(`curl -LsSf https://astral.sh/uv/install.sh | sh` on macOS / Linux).

Want the Agent to auto-start at login (macOS) and survive crashes? Use
[the recommended `uv tool install` path](#-recommended--uv-tool-install-any-os)
below instead of `uvx`.

## Installation

### ⭐ Recommended — `uv tool install` (any OS)

This is the **primary, officially supported path**. It works on macOS
(Apple Silicon & Intel), Linux, and WSL. No prebuilt binary, no
signature pop-ups, no Rosetta — just one Python package pulled from PyPI
with everything (SQL migrations, default configs) bundled in.

Prerequisite: [uv](https://docs.astral.sh/uv/) 0.4+.

```bash
# Install uv itself if you haven't (https://docs.astral.sh/uv/#installation)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install PaperPrism Agent (persistent shim at ~/.local/bin/paperprism-agent)
uv tool install paperprism-agent

# Verify
paperprism-agent version        # -> 0.1.0

# macOS: register a LaunchAgent so it auto-starts at login + auto-restarts
paperprism-agent install
paperprism-agent status         # -> state = running

# Linux / WSL: run in foreground (systemd user unit is on the v0.2 roadmap)
paperprism-agent serve
```

Upgrade any time with `uv tool upgrade paperprism-agent` followed by
`paperprism-agent restart`.

#### One-off try with `uvx` (no install)

```bash
uvx paperprism-agent serve
```

Great for kicking the tires from a throwaway env. **Do not** combine
with `paperprism-agent install` — the `install` subcommand refuses to
register a LaunchAgent pointed at the ephemeral `uvx` cache (that path
can be garbage-collected at any time). For a permanent setup use
`uv tool install` above.

### Alternative installers

Only use these if you don't want to install `uv` on the machine.

<details>
<summary><strong>macOS — <code>.pkg</code> installer / one-line shell script</strong></summary>

```bash
# One-line (downloads the latest binary from GitHub Releases into ~/.local/bin
# and registers a LaunchAgent; override prefix via PAPERPRISM_PREFIX=...):
curl -fsSL https://raw.githubusercontent.com/MrMao007/PaperPrism/main/packaging/install.sh | bash
```

Or grab `paperprism-agent-<version>-macos-arm64.pkg` from the
[Releases page](https://github.com/MrMao007/PaperPrism/releases) and
double-click. The installer places the binary under `/usr/local` and
registers the LaunchAgent for your account automatically.

Intel Macs (pre-2020): we no longer ship a native x86_64 binary (GitHub's
Intel CI runners are being retired). Intel Macs can still run the arm64
`.pkg` via Rosetta 2 (`softwareupdate --install-rosetta`), or — much
simpler — just use the **recommended `uv tool install` path above**,
which is fully native on Intel.

</details>

<details>
<summary><strong>macOS / Linux — Homebrew</strong></summary>

```bash
brew tap MrMao007/paperprism
brew install paperprism-agent
brew services start paperprism-agent
```

</details>

<details>
<summary><strong>Linux — one-line shell script</strong></summary>

```bash
curl -fsSL https://raw.githubusercontent.com/MrMao007/PaperPrism/main/packaging/install.sh | bash
```

Auto-start on Linux is not yet wired up — run `paperprism-agent serve`
manually or set up a systemd user unit.

</details>

<details>
<summary><strong>Windows / Debian — install from source</strong></summary>

`.msi` and `.deb` artifacts are on the v0.2 roadmap. For now, use
`uv tool install paperprism-agent` (recommended) or install from git:

```bash
pip install git+https://github.com/MrMao007/PaperPrism#subdirectory=agent
paperprism-agent serve
```

</details>

### Chrome extension

Install from the Chrome Web Store *(link pending)* or load unpacked:

```bash
cd extension
npm install
npm run build
# Chrome -> chrome://extensions -> "Load unpacked" -> extension/.output/chrome-mv3
```

The first time you open the extension's Options page a 4-step wizard runs
(probe Agent → pick LLM provider → enter API key → test + open Dashboard).

## Run from source

The fastest way to try PaperPrism without downloading any release artifact:
clone, install once, load the unpacked extension into Chrome. Works on
macOS, Linux, and WSL. Total setup time: ~3 minutes.

### Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | **>= 3.10** | `hatchling` refuses older interpreters. `python3 --version` to check. |
| Node.js | **>= 18** | For the WXT build. `node --version` to check. |
| Google Chrome | any recent | Or any Chromium-based browser (Edge, Brave, Arc). |
| Git | any | |

Missing Python 3.10+? On macOS: `brew install python@3.11`. On Ubuntu:
`sudo apt install python3.11 python3.11-venv`.

### 1. Clone the repo

```bash
git clone https://github.com/MrMao007/PaperPrism.git
cd PaperPrism
```

### 2. Install and start the Agent

```bash
cd agent
python3.11 -m venv .venv            # use python3.10/3.12 if you prefer
source .venv/bin/activate
pip install -e .

# Verify the CLI works
paperprism-agent version

# macOS: register a LaunchAgent so it auto-starts at login
paperprism-agent install
paperprism-agent status              # should print "state = running"

# Linux / WSL: no launchd; run it in a separate terminal
# paperprism-agent serve
```

Health check from another shell:

```bash
curl http://127.0.0.1:17321/api/health
# {"ok":true,"version":"0.1.0",...}
```

All state lives under `~/.paperprism/` (vault, SQLite DB, logs, secrets).

### 3. Build and load the Chrome extension

```bash
cd ../extension
npm install                          # installs WXT + React toolchain
npm run build                        # produces .output/chrome-mv3/
```

Then load it into Chrome:

1. Open **`chrome://extensions`**
2. Toggle **Developer mode** on (top right)
3. Click **Load unpacked**
4. Select the folder `extension/.output/chrome-mv3`

A **PaperPrism** icon appears in the toolbar. Pin it for convenience.

> Prefer hot-reload while editing the extension? Run `npm run dev` instead
> of `npm run build` — WXT will rebuild on save; just click **Update** on
> the `chrome://extensions` card when prompted.

### 4. Finish the first-run wizard

1. Click the toolbar icon → the popup should show **Agent: online**.
2. Click **Settings** (footer) — the Options page opens and auto-launches
   the 4-step wizard:
   - **Step 1** probes the Agent.
   - **Step 2** pick an LLM provider (Qwen / OpenAI / Anthropic / Google
     Gemini / DeepSeek / Moonshot / OpenRouter / Ollama local). API base
     and env var are filled in automatically.
   - **Step 3** paste your API key (skipped for Ollama). It is written to
     `~/.paperprism/secrets.env` (mode 600) and injected into the Agent
     process for immediate use; the wizard then runs a tiny chat
     request to prove the key works.
   - **Step 4** click **Open Dashboard**.

### 5. Archive your first paper

Open any arxiv abstract page, e.g. <https://arxiv.org/abs/2310.06825>,
click **Download PDF** (or the PaperPrism popup's **Archive current
tab**). The Agent ingests the PDF, extracts metadata, classifies it with
your LLM, **auto-tags it** with 2–5 short tags, and the Dashboard lists
it within a few seconds. Auto-tag-on-ingest can be toggled in the
Options page → LLM section.

You now have everything running from source.

### 6. (Optional) Bulk-import an existing folder of PDFs

Got a local archive of arxiv papers accumulated over the years? Open the
Dashboard, click **Import folder**, and pick any directory. The extension
walks the tree, uploads every `.pdf` to the Agent, and streams live
progress (imported / duplicate / failed counters plus a "last errors"
tail). You can cancel mid-run.

For each PDF the Agent resolves its arxiv id in two steps:

1. **Filename first** — tries the file stem (e.g. `2504.19413v1.pdf`,
   `Attention_1706.03762.pdf`) as a candidate and verifies it on the
   arxiv API.
2. **LLM fallback** — if step 1 misses, the first page of the PDF is fed
   to your configured LLM with a strict `{"arxiv_id": ...}` schema;
   the returned id is re-verified on arxiv.

If both steps fail the paper is still archived under a synthetic
`local-<sha>` id so you don't lose the file.

### 7. (Optional) Roll papers up into a Topic

In the Dashboard, tick any number of papers, then click **Auto-tag
selected** in the bulk toolbar. The Agent batches them to the LLM,
persists per-paper tags, and finishes by summarising the collection
into a **Topic** card (name + 1–3 sentence summary + every shared tag).
Switch to the **Topics** tab from the header nav to browse all topics;
click one to open its detail page with the full paper list. Single
papers always keep their own tags even after a topic is deleted.

### Useful commands while developing

```bash
# Agent
paperprism-agent status               # launchd state
paperprism-agent logs --follow        # tail stdout/stderr
paperprism-agent restart              # force launchd to re-exec
paperprism-agent uninstall            # remove the LaunchAgent

# Extension
cd extension
npm run dev                           # watch mode, auto-rebuilds
npm run build                         # one-shot production build
npm run compile                       # type-check without emit
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| `pip install -e .` fails with `hatchling>=1.25` error | Your venv was built with Python < 3.10. Recreate with `python3.11 -m venv .venv`. |
| Popup shows **Agent: offline** | Run `paperprism-agent status`; if not running, `paperprism-agent restart`. Check port 17321 isn't taken: `lsof -i :17321`. |
| `npm install` warns about type errors | Run once then retry — WXT generates `.wxt/tsconfig.json` on `postinstall`. |
| Wizard **Save & Test** fails | Open `~/.paperprism/logs/agent.log`; the LLM error (401 / 404 / timeout) is usually obvious. |
| Changed code in `agent/` but Agent still runs old version | `paperprism-agent restart` — `pip install -e .` only links source, but the already-loaded process keeps old imports. |
| Agent fails to start with `Form data requires "python-multipart"` (or any other `ModuleNotFoundError`) | You added a new entry under `[project].dependencies` in `agent/pyproject.toml` but didn't re-sync the venv. Inside the activated venv run `pip install -e .` again (editable install only links source, it does **not** auto-install newly declared deps). |
| Chrome shows "This extension may soon no longer be supported" | You built an MV2 artifact by accident; make sure you loaded `.output/chrome-mv3/`, not any zipped older build. |

## Build a release locally

```bash
# 1. Produce the single-file binary
bash packaging/pyinstaller/build.sh
# -> packaging/pyinstaller/dist/paperprism-agent

# 2. (macOS only) wrap it in a .pkg
bash packaging/macos/build_pkg.sh 0.1.0
# -> packaging/macos/dist/paperprism-agent-0.1.0-macos-<arch>.pkg
```

The CI workflow in [`.github/workflows/release.yml`](.github/workflows/release.yml)
does the same build across macOS (arm64 only) and Linux (arm64 + x86_64)
whenever you push a `v*` tag, and attaches tarballs + `.pkg` to a GitHub
Release automatically. (Intel macOS native builds were dropped because
GitHub's `macos-13` runner pool is being retired; Intel Macs run the
arm64 binary via Rosetta 2.)

## Project layout

```
agent/           FastAPI + SQLite Agent (Python 3.10+)
extension/       Chrome MV3 extension (WXT + React + TS)
packaging/       install.sh, PyInstaller spec, macOS .pkg, Homebrew formula
.github/         Release automation
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
