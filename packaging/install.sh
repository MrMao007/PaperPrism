#!/usr/bin/env bash
# PaperPrism Agent one-liner installer.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/<owner>/PaperPrism/main/packaging/install.sh | bash
#
# What it does:
#   1. Detects OS + CPU arch (macOS/Linux on x86_64 or arm64).
#   2. Downloads the matching tarball from the latest GitHub Release
#      (or a pinned tag via PAPERPRISM_VERSION).
#   3. Extracts paperprism-agent into $PREFIX/bin (default: ~/.local/bin).
#   4. Runs `paperprism-agent install` so the Agent auto-starts at login
#      (macOS: LaunchAgent; Linux: prints systemd --user hint for now).
#
# Environment overrides:
#   PAPERPRISM_VERSION   pin to a specific tag, e.g. v0.1.0 (default: latest)
#   PAPERPRISM_REPO      GitHub repo, default paperprism/PaperPrism
#   PAPERPRISM_PREFIX    install prefix (default: $HOME/.local)

set -euo pipefail

REPO="${PAPERPRISM_REPO:-paperprism/PaperPrism}"
PREFIX="${PAPERPRISM_PREFIX:-${HOME}/.local}"
VERSION="${PAPERPRISM_VERSION:-latest}"

log()  { printf '\033[1;34m[paperprism]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[paperprism]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[paperprism]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- detect platform ----------
uname_s="$(uname -s)"
uname_m="$(uname -m)"

case "${uname_s}" in
    Darwin)  OS="macos" ;;
    Linux)   OS="linux" ;;
    *)       die "Unsupported OS: ${uname_s}. Use pip install paperprism-agent instead." ;;
esac

case "${uname_m}" in
    x86_64|amd64)  ARCH="x86_64" ;;
    arm64|aarch64) ARCH="arm64" ;;
    *)             die "Unsupported arch: ${uname_m}" ;;
esac

log "Platform: ${OS}-${ARCH}"

# ---------- resolve release asset URL ----------
API_URL="https://api.github.com/repos/${REPO}/releases"
if [ "${VERSION}" = "latest" ]; then
    API_URL="${API_URL}/latest"
else
    API_URL="${API_URL}/tags/${VERSION}"
fi

command -v curl >/dev/null || die "curl is required."
command -v tar  >/dev/null || die "tar is required."

log "Fetching release info from ${API_URL}"
RELEASE_JSON="$(curl -fsSL "${API_URL}")" \
    || die "Failed to fetch release info. Is the repo public? Is VERSION valid?"

ASSET_NAME="paperprism-agent-${OS}-${ARCH}.tar.gz"
ASSET_URL="$(printf '%s' "${RELEASE_JSON}" \
    | grep -o "\"browser_download_url\": *\"[^\"]*${ASSET_NAME}\"" \
    | head -n1 \
    | sed -E 's/.*"(https[^"]+)".*/\1/')"

[ -n "${ASSET_URL}" ] || die "Release does not contain ${ASSET_NAME}. See https://github.com/${REPO}/releases"
log "Downloading ${ASSET_URL}"

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
curl -fsSL "${ASSET_URL}" -o "${TMP}/pkg.tgz"
tar -xzf "${TMP}/pkg.tgz" -C "${TMP}"

BIN_SRC="${TMP}/paperprism-agent"
[ -x "${BIN_SRC}" ] || die "Archive did not contain an executable named paperprism-agent."

# ---------- install to prefix ----------
mkdir -p "${PREFIX}/bin"
install -m 0755 "${BIN_SRC}" "${PREFIX}/bin/paperprism-agent"
log "Installed: ${PREFIX}/bin/paperprism-agent"

# Clear macOS quarantine so Gatekeeper doesn't block first launch.
if [ "${OS}" = "macos" ]; then
    xattr -d com.apple.quarantine "${PREFIX}/bin/paperprism-agent" 2>/dev/null || true
fi

# ---------- PATH hint ----------
case ":${PATH}:" in
    *":${PREFIX}/bin:"*) ;;
    *) warn "${PREFIX}/bin is not on your PATH. Add this line to your shell rc:"
       warn "    export PATH=\"${PREFIX}/bin:\$PATH\"" ;;
esac

# ---------- register auto-start ----------
if [ "${OS}" = "macos" ]; then
    log "Registering LaunchAgent (no sudo required)..."
    "${PREFIX}/bin/paperprism-agent" install || warn "install failed; run 'paperprism-agent install' manually."
else
    warn "Auto-start on Linux is not wired up yet. Run in the foreground with:"
    warn "    ${PREFIX}/bin/paperprism-agent serve"
fi

log "Done. Open the Chrome extension Options page to finish the first-run wizard."
