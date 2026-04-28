# PaperPrism

A local-first, privacy-preserving arxiv paper organizer. A Chrome extension
watches your arxiv downloads; a tiny local Agent mirrors each paper into a
hidden workspace, extracts metadata, and classifies them with your LLM of
choice. No papers ever leave your machine.

- **Chrome extension** — popup archive button, Options first-run wizard,
  built-in Dashboard to browse / filter / view PDF / delete papers.
- **Local Agent** — FastAPI service (default `http://127.0.0.1:17321`),
  SQLite + FTS5 store, LLM classifier, auto-start at login.

## Install (end users)

### macOS — one line

```bash
curl -fsSL https://raw.githubusercontent.com/paperprism/PaperPrism/main/packaging/install.sh | bash
```

This downloads the latest `paperprism-agent` binary from GitHub Releases,
drops it in `~/.local/bin`, and registers a LaunchAgent so it auto-starts
at login. Override the install prefix via `PAPERPRISM_PREFIX=...`.

### macOS — .pkg installer

Download `paperprism-agent-<version>-macos-<arch>.pkg` from the
[Releases page](https://github.com/paperprism/PaperPrism/releases) and
double-click. The installer places the binary under `/usr/local` and
registers the LaunchAgent for your account automatically.

### macOS / Linux — Homebrew

```bash
brew tap paperprism/paperprism
brew install paperprism-agent
brew services start paperprism-agent
```

### Linux — one line

```bash
curl -fsSL https://raw.githubusercontent.com/paperprism/PaperPrism/main/packaging/install.sh | bash
```

Auto-start on Linux is not yet wired up — run `paperprism-agent serve`
manually or set up a systemd user unit.

### Windows / Debian

`.msi` and `.deb` artifacts are on the v0.2 roadmap. For now, install from
source:

```bash
pip install git+https://github.com/paperprism/PaperPrism#subdirectory=agent
paperprism-agent serve
```

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

## Install (developers)

```bash
# 1. Agent
cd agent
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .
paperprism-agent install  # register LaunchAgent
paperprism-agent status   # confirm it's running

# 2. Extension
cd ../extension
npm install
npm run dev               # hot-reload into Chrome
```

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
does the same build across macOS (arm64 + x86_64) and Linux (arm64 + x86_64)
whenever you push a `v*` tag, and attaches tarballs + `.pkg` to a GitHub
Release automatically.

## Project layout

```
agent/           FastAPI + SQLite Agent (Python 3.10+)
extension/       Chrome MV3 extension (WXT + React + TS)
packaging/       install.sh, PyInstaller spec, macOS .pkg, Homebrew formula
.github/         Release automation
```

## License

MIT — see [LICENSE](LICENSE).
