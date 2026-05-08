# AGENTS.md — Packaging & release

> Scope: everything under `packaging/`. Complements the root
> [AGENTS.md](../AGENTS.md).

## Purpose

Produce **fallback** end-user artifacts for the **Agent** side of
PaperPrism. The extension ships via the Chrome Web Store
(item id `jjlclcocagjnohgcpbgcpkodcnmmabif`); the primary Agent
channel is **PyPI** (`paperprism-agent`, consumed via `uvx` /
`uv tool install`). Everything under `packaging/` exists so users who
explicitly don't want to install `uv` still have a path.

Targets:

- Single-file PyInstaller binary (`paperprism-agent`).
- macOS `.pkg` installer (wraps the binary + LaunchAgent).
- Homebrew formula.
- `curl | bash` quick installer.

What is **NOT** built here:

- **PyPI wheel + sdist** — built from `agent/` directly via
  `python -m build`. See `agent/AGENTS.md` → "Publishing to PyPI".
- **Chrome Web Store zip** — built from `extension/` via `npm run zip`.
  See `extension/AGENTS.md` → "Distribution".

## Layout

```
packaging/
├── install.sh                    # curl | bash installer
│                                 #   - detects OS/arch
│                                 #   - downloads the matching tarball from GitHub Releases
│                                 #   - drops the binary in $PAPERPRISM_PREFIX/bin
│                                 #   - on macOS: registers the LaunchAgent
├── pyinstaller/
│   ├── build.sh                  # spins up a temp venv, runs pyinstaller
│   ├── paperprism-agent.spec     # one-file, hidden imports for FastAPI/uvicorn
│   ├── .build-venv/              # (gitignored) build-only venv
│   ├── build/                    # (gitignored) pyinstaller temp
│   └── dist/                     # (gitignored) final binary
├── macos/
│   ├── build_pkg.sh              # wraps the binary into a signed-ready .pkg
│   ├── distribution.xml          # pkgbuild product config
│   ├── welcome.txt               # installer welcome screen
│   └── scripts/                  # postinstall script (registers LaunchAgent)
└── homebrew/
    └── paperprism-agent.rb       # formula pointing at the GitHub Release tarball
```

## Release workflow

1. Bump versions:
   - `agent/pyproject.toml#version`
   - `extension/package.json#version` + `wxt.config.ts#manifest.version`
2. Publish to PyPI (primary channel):
   - `cd agent && rm -rf dist && python -m build && twine upload dist/*`
3. Upload the new zip to the Chrome Web Store:
   - `cd extension && npm run build && npm run zip`
   - Upload `extension/.output/paperprism-extension-<ver>-chrome.zip`
     via the developer dashboard and submit for review.
4. Commit and push.
5. Tag: `git tag vX.Y.Z && git push --tags`.
6. `.github/workflows/release.yml` runs (fallback channels):
   - macOS arm64 runner → `pyinstaller/build.sh` + `macos/build_pkg.sh`.
   - Linux arm64 + x86_64 runners → `pyinstaller/build.sh`.
   - Uploads artifacts to the GitHub Release.
7. Update `homebrew/paperprism-agent.rb` with the new URL + SHA256 and
   push the tap repo (`MrMao007/homebrew-paperprism`).

Intel macOS builds have been dropped — Intel Macs use the arm64 binary
via Rosetta 2, or install via `uv tool install paperprism-agent` /
from source.

## Local release build

```bash
# 1. Single-file binary (arch = host arch)
bash packaging/pyinstaller/build.sh
# -> packaging/pyinstaller/dist/paperprism-agent

# 2. macOS .pkg (requires macOS host)
bash packaging/macos/build_pkg.sh 0.2.0
# -> packaging/macos/dist/paperprism-agent-0.2.0-macos-arm64.pkg

# 3. Smoke test the binary
./packaging/pyinstaller/dist/paperprism-agent version
./packaging/pyinstaller/dist/paperprism-agent serve
```

The binary is a self-contained Python + FastAPI + uvicorn blob. It
**does not bundle** `~/.paperprism/*` — user state is created on first
run. If the user had prior state from a `pip install -e .` setup it
stays compatible: the binary points at the same home dir.

## Installer behaviour (`install.sh`)

- Default prefix: `~/.local` (so no sudo needed).
- `PAPERPRISM_PREFIX=/usr/local bash install.sh` to install
  system-wide.
- macOS post-install: creates
  `~/Library/LaunchAgents/com.paperprism.agent.plist` pointing at the
  installed binary, then `launchctl bootstrap gui/<uid>`.
- Linux post-install: no auto-start yet; prints a hint to run
  `paperprism-agent serve`.

## PyInstaller spec pitfalls

`paperprism-agent.spec` must keep these `hiddenimports` / `datas` in
sync with the code:

- `uvicorn.logging`, `uvicorn.loops`, `uvicorn.protocols` families —
  uvicorn resolves these by string at runtime.
- `paperprism_agent.migrations` resources — include as `datas` so
  PyInstaller copies the `.sql` files.
- `paperprism_agent.resources` — same: `llm.default.yaml` must be
  bundled.

If you add a new resource file or a new lazily-imported module, update
the spec **before** cutting a release, otherwise the binary will crash
on first use with `FileNotFoundError` or `ModuleNotFoundError`.

## macOS .pkg notes

- Not code-signed in CI yet; users may need to
  `Right-click → Open` the first time.
- postinstall script writes the LaunchAgent plist **for the installing
  user only** (uses `$USER` / `$HOME`). Installing via `sudo` with a
  different account will register the plist under root — usually not
  what you want.
- `.pkg` receipts can be inspected with `pkgutil --pkgs | grep paperprism`.

## Homebrew formula

`packaging/homebrew/paperprism-agent.rb` points at the GitHub Release
tarball (per-arch URL + SHA256). After a new release:

1. Download the new tarballs and compute SHA256.
2. Update the formula.
3. `brew install --build-from-source packaging/homebrew/paperprism-agent.rb`
   locally to smoke-test.
4. Commit the formula to the tap repo.

## Conventions for AI edits

- **Never install globally from a script without asking the user.**
  `install.sh` must respect `$PAPERPRISM_PREFIX` and default to
  user-local.
- **Shell scripts should be `set -euo pipefail`.** Check before adding
  any new scripts here.
- **Keep `macos/scripts/postinstall` idempotent.** Re-installing the
  `.pkg` over an existing install must not break a running Agent —
  stop, replace, start.
- **Release scripts must never print API keys or tokens** — none are
  read during packaging, but be defensive if that changes.
