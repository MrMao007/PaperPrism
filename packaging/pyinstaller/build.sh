#!/usr/bin/env bash
# Build the single-file paperprism-agent binary via PyInstaller.
#
# Usage (run from any directory):
#   bash packaging/pyinstaller/build.sh
#
# Output: packaging/pyinstaller/dist/paperprism-agent
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${HERE}/../.." && pwd)"

cd "${HERE}"

# Pick the highest available Python >= 3.10 (agent requires it; hatchling >=1.25
# refuses older interpreters). Respect $PYTHON override if the caller set it.
if [ -n "${PYTHON:-}" ]; then
    PY="${PYTHON}"
else
    PY=""
    for cand in python3.12 python3.11 python3.10 python3; do
        if command -v "${cand}" >/dev/null 2>&1; then
            ver="$("${cand}" -c 'import sys; print("%d%d" % sys.version_info[:2])' 2>/dev/null || echo 0)"
            if [ "${ver}" -ge 310 ] 2>/dev/null; then
                PY="${cand}"
                break
            fi
        fi
    done
fi
if [ -z "${PY}" ]; then
    echo "No Python >= 3.10 found on PATH. Install python3.11 (brew install python@3.11) and retry." >&2
    exit 1
fi
echo "Using Python: $(${PY} -V 2>&1)"

# Create an isolated build venv so we don't mix build tooling with runtime deps.
if [ ! -d ".build-venv" ]; then
  "${PY}" -m venv .build-venv
fi
# shellcheck disable=SC1091
source .build-venv/bin/activate

python -m pip install --upgrade pip wheel
# Install the agent itself (locks runtime deps into the build env)
pip install "${PROJECT_ROOT}/agent"
pip install "pyinstaller>=6.6"

# Clean previous artifacts to make sure we don't ship stale data files.
rm -rf build dist

pyinstaller paperprism-agent.spec --clean --noconfirm

echo
echo "Built: ${HERE}/dist/paperprism-agent"
file "dist/paperprism-agent" || true
ls -lh "dist/paperprism-agent" || true
