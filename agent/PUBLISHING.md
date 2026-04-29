# Publishing `paperprism-agent` to PyPI

This doc is a step-by-step checklist for cutting a new release of the
`paperprism-agent` Python package (so that `uvx paperprism-agent serve`
and `uv tool install paperprism-agent` work for end users).

All paths in this doc are relative to `agent/` unless otherwise noted.

---

## 0. Prerequisites (one-time)

1. **PyPI account**: https://pypi.org/account/register/
2. **TestPyPI account** (for dry runs): https://test.pypi.org/account/register/
3. Enable 2FA on both, then create API tokens:
   - PyPI → *Account settings* → *API tokens* → scope = `paperprism-agent`
     (create once you have the project uploaded; start with scope
     `Entire account` for the first release).
   - TestPyPI → same flow.
4. Save the tokens to `~/.pypirc`:

   ```ini
   [distutils]
   index-servers =
     pypi
     testpypi

   [pypi]
   username = __token__
   password = pypi-AgENdGVzdC5weXBpLm9yZwI...

   [testpypi]
   repository = https://test.pypi.org/legacy/
   username = __token__
   password = pypi-AgENdGVzdC5weXBpLm9yZwI...
   ```

   `chmod 600 ~/.pypirc`.

5. Install build tooling once per environment:

   ```bash
   pip install --upgrade build twine
   # (already listed under [project.optional-dependencies].dev)
   ```

---

## 1. Bump the version

Version lives in **two** places and MUST match:

- `agent/pyproject.toml` → `[project] version`
- `agent/src/paperprism_agent/__init__.py` → `__version__`

SemVer. Typical flows:

- **Patch release** (bug fix only): `0.1.0 → 0.1.1`
- **Minor release** (backwards-compatible feature): `0.1.0 → 0.2.0`
- **Major release** (breaking API / data-model change): `0.1.0 → 1.0.0`

> ⚠️ PyPI does not allow re-uploading the same version, even after
> deleting it. If a release is broken, bump the patch and upload again.

Commit the bump on its own:

```bash
git commit -am "release: paperprism-agent vX.Y.Z"
git tag -a vX.Y.Z -m "paperprism-agent vX.Y.Z"
```

---

## 2. Clean build

```bash
cd agent
rm -rf dist build *.egg-info
python -m build
```

This produces two files under `dist/`:

- `paperprism_agent-X.Y.Z-py3-none-any.whl` (the wheel)
- `paperprism_agent-X.Y.Z.tar.gz`            (the sdist)

---

## 3. Sanity checks

```bash
# Metadata / README render
twine check dist/*

# Verify non-py data files are inside the wheel
python -m zipfile -l dist/*.whl | grep -E 'migrations/.*\.sql|resources/.*\.yaml'

# Cold install in a throwaway venv and smoke the CLI
tmp=$(mktemp -d)
python3 -m venv "$tmp/v"
"$tmp/v/bin/pip" install dist/*.whl
"$tmp/v/bin/paperprism-agent" version
"$tmp/v/bin/paperprism-agent" --help | head -5
rm -rf "$tmp"
```

All three should pass before you upload.

---

## 4. Upload to TestPyPI first

```bash
twine upload --repository testpypi dist/*
```

Then verify end-to-end via `uvx` against TestPyPI:

```bash
uvx --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    paperprism-agent version
```

(The `--extra-index-url` is required because TestPyPI does not mirror
runtime dependencies like `fastapi`.)

---

## 5. Upload to real PyPI

```bash
twine upload dist/*
```

Watch for:

- `HTTP 400: File already exists` → you forgot to bump the version.
- `HTTP 403: Invalid or non-existent authentication` → token expired or
  missing `__token__` username.

---

## 6. Verify the release is live

```bash
# Wait ~30s for PyPI CDN warm-up, then:
uvx paperprism-agent@X.Y.Z version

uv tool install paperprism-agent==X.Y.Z
~/.local/bin/paperprism-agent version
```

The project page should show: https://pypi.org/project/paperprism-agent/X.Y.Z/

---

## 7. Push git tag

```bash
git push origin main
git push origin vX.Y.Z
```

GitHub Actions (see `.github/workflows/release.yml`) picks up the tag
and builds the native installer artefacts (macOS `.pkg`, Linux
tarballs). Those are **separate** from the PyPI upload — PyPI is the
canonical Python-land distribution; the installers are for users who
don't want to touch Python at all.

---

## Troubleshooting

### Wheel is missing `migrations/*.sql` or `resources/*.yaml`

`hatchling` should include these automatically because
`[tool.hatch.build.targets.wheel] packages = ["src/paperprism_agent"]`
recurses into every subdirectory. If they ever disappear:

1. Check `python -m zipfile -l dist/*.whl` — if the files are absent,
2. add them via `[tool.hatch.build.targets.wheel.force-include]`
   (but remove the automatic inclusion to avoid *duplicate-name*
   warnings during the build).

### `twine check` warns about README rendering

`pypi` rejects READMEs with invalid reStructuredText / Markdown. Our
`readme = "README.md"` uses Markdown, which needs `Description-Content-Type: text/markdown`. `hatchling` sets this automatically, but check the
METADATA file inside the wheel if `twine check` complains:

```bash
python -m zipfile -e dist/*.whl /tmp/whl && \
    grep Content-Type /tmp/whl/paperprism_agent-*.dist-info/METADATA
```

### `uvx paperprism-agent install` fails with "ephemeral uvx environment"

That's intentional — launchd can't safely bind to a path inside the
uv cache. Tell users to run:

```bash
uv tool install paperprism-agent
paperprism-agent install
```

See `launchd.resolve_launcher()` for the detection logic.
