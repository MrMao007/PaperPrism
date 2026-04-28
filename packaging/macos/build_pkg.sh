#!/usr/bin/env bash
# Assemble a double-clickable macOS installer (.pkg) from the PyInstaller
# binary produced by packaging/pyinstaller/build.sh.
#
# Layout written into the package:
#   /usr/local/bin/paperprism-agent     (symlink -> libexec binary)
#   /usr/local/libexec/paperprism/paperprism-agent  (actual executable)
#
# A postinstall script then runs `paperprism-agent install` **as the
# currently logged-in user** so launchd picks it up at their LaunchAgents
# (a system-level daemon would need root privileges every boot).
#
# Usage:
#   bash packaging/macos/build_pkg.sh [VERSION]
#
# Output:
#   packaging/macos/dist/paperprism-agent-<VERSION>-macos.pkg

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${HERE}/../.." && pwd)"
PYI_DIST="${PROJECT_ROOT}/packaging/pyinstaller/dist"

VERSION="${1:-0.1.0}"
IDENT="org.paperprism.agent"
ARCH="$(uname -m)"

if [ ! -x "${PYI_DIST}/paperprism-agent" ]; then
  echo "Missing ${PYI_DIST}/paperprism-agent." >&2
  echo "Run packaging/pyinstaller/build.sh first." >&2
  exit 1
fi

STAGE="${HERE}/build/pkgroot"
SCRIPTS="${HERE}/build/scripts"
OUT="${HERE}/dist"

rm -rf "${HERE}/build" "${OUT}"
mkdir -p "${STAGE}/usr/local/bin"
mkdir -p "${STAGE}/usr/local/libexec/paperprism"
mkdir -p "${SCRIPTS}"
mkdir -p "${OUT}"

# 1. Lay out the payload.
cp "${PYI_DIST}/paperprism-agent" "${STAGE}/usr/local/libexec/paperprism/paperprism-agent"
chmod 755 "${STAGE}/usr/local/libexec/paperprism/paperprism-agent"
ln -sf "../libexec/paperprism/paperprism-agent" "${STAGE}/usr/local/bin/paperprism-agent"

# 2. Copy postinstall (runs as root after payload is laid down).
cp "${HERE}/scripts/postinstall" "${SCRIPTS}/postinstall"
chmod 755 "${SCRIPTS}/postinstall"

# 3. Build the component .pkg.
COMPONENT_PKG="${HERE}/build/paperprism-agent-component.pkg"
pkgbuild \
  --root "${STAGE}" \
  --identifier "${IDENT}" \
  --version "${VERSION}" \
  --install-location "/" \
  --scripts "${SCRIPTS}" \
  "${COMPONENT_PKG}"

# 4. Wrap with productbuild for a nicer installer UI.
PRODUCT_PKG="${OUT}/paperprism-agent-${VERSION}-macos-${ARCH}.pkg"
productbuild \
  --distribution "${HERE}/distribution.xml" \
  --package-path "${HERE}/build" \
  --version "${VERSION}" \
  "${PRODUCT_PKG}"

echo
echo "Built: ${PRODUCT_PKG}"
ls -lh "${PRODUCT_PKG}"

cat <<EOF

Signing & notarization (optional but recommended for redistribution):
  productsign --sign "Developer ID Installer: <Your Name> (TEAMID)" \
    "${PRODUCT_PKG}" "${PRODUCT_PKG%.pkg}-signed.pkg"
  xcrun notarytool submit "${PRODUCT_PKG%.pkg}-signed.pkg" \
    --keychain-profile "AC_PASSWORD" --wait
  xcrun stapler staple "${PRODUCT_PKG%.pkg}-signed.pkg"
EOF
