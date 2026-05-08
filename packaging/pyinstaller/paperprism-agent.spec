# PyInstaller spec for packaging the PaperPrism Agent as a single-file binary.
#
# Build:
#   cd packaging/pyinstaller
#   pip install pyinstaller
#   pyinstaller paperprism-agent.spec --clean --noconfirm
#
# Output: dist/paperprism-agent (macOS/Linux) or dist/paperprism-agent.exe
#
# The resulting binary includes a frozen Python interpreter and every
# dependency declared in agent/pyproject.toml. At launch it behaves exactly
# like `paperprism-agent` installed via pip -- same CLI subcommands.
# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# SPECPATH = directory containing this spec file, resolved at runtime.
PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, '..', '..'))
AGENT_SRC = os.path.join(PROJECT_ROOT, 'agent', 'src')
AGENT_PKG = os.path.join(AGENT_SRC, 'paperprism_agent')

block_cipher = None

# Hidden imports: PyInstaller can't always follow uvicorn's lazy loader trees.
hiddenimports = []
for mod in ('uvicorn', 'uvicorn.protocols', 'uvicorn.lifespan',
            'uvicorn.loops', 'uvicorn.logging', 'anyio',
            'email_validator', 'pymupdf'):
    try:
        hiddenimports += collect_submodules(mod)
    except Exception:
        # Module not present in this install; skip silently.
        pass

# Bundled data: YAML defaults + SQL migrations (read via importlib.resources).
datas = [
    (os.path.join(AGENT_PKG, 'resources'), 'paperprism_agent/resources'),
    (os.path.join(AGENT_PKG, 'migrations'), 'paperprism_agent/migrations'),
]

# If fastapi / pydantic ship data files (json schemas etc.), include them.
for pkg in ('fastapi', 'starlette', 'pydantic'):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

# Large ML/AI dependencies that are lazily loaded at runtime and must NOT
# be frozen into the binary.  sentence-transformers downloads the embedding
# model on first use (~130 MB); umap-learn / scipy are only needed when the
# user opens the Atlas map.  Excluding them keeps the Linux binary well
# below GitHub's 2 GB release-asset limit (without exclusions it balloons
# to ~2.6 GB; with exclusions it is typically 200–400 MB).
#
# At runtime the binary resolves these packages from the user's Python
# environment (installed automatically alongside the Agent via pip/uv).
_HEAVY_EXCLUDES = [
    # ML inference stack
    'torch', 'torchvision', 'torchaudio',
    'transformers', 'tokenizers', 'huggingface_hub',
    'sentence_transformers',
    # Scientific computing pulled in by umap-learn / sentence-transformers
    'scipy', 'sklearn', 'scikit_learn',
    'umap', 'umap_learn',
    'numba', 'llvmlite',
    # Other large optional deps unlikely to be needed at startup
    'matplotlib', 'PIL', 'cv2',
    'IPython', 'ipykernel', 'notebook',
    # Standard exclusions
    'tkinter', 'test', 'tests',
]

a = Analysis(
    [os.path.join(AGENT_PKG, '__main__.py')],
    pathex=[AGENT_SRC],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=_HEAVY_EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='paperprism-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
