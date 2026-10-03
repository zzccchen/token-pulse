# Development and builds

## Setup

Install Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```sh
uv sync --locked
uv run token-pulse --demo
```

Use the isolated demo for UI work. Tests use synthetic data and need no Codex account or API key. See [architecture](architecture.md) and [contribution rules](../CONTRIBUTING.md).

## Checks

```sh
uv run ruff check .
uv run ruff format --check .
uv run python scripts/check_docs.py
uv run pytest
uv run python -m token_pulse --demo --no-tray --smoke-test
uv build
```

On a headless Linux runner, set `QT_QPA_PLATFORM=offscreen`. In PowerShell, use `$env:QT_QPA_PLATFORM = 'offscreen'`. Unset it before testing native tray/window behavior. Required Linux libraries and validation limits are in [compatibility](compatibility.md).

CI runs checks, synthetic tests, a demo smoke test, and package builds on Windows / Ubuntu with Python 3.11 / 3.13. It does not validate a real desktop tray. Check the [actual workflow results](https://github.com/zzccchen/token-pulse/actions/workflows/ci.yml) rather than treating the presence of a workflow as a passing run. Actions are pinned to commit SHAs.

## Screenshots

```sh
uv run python scripts/capture_demo.py --language en --output artifacts/ui/en
uv run python scripts/capture_demo.py --language en --appearance dark --output artifacts/ui/en-dark
uv run python scripts/capture_demo.py --language zh_CN --output artifacts/ui/zh
uv run python scripts/capture_demo.py --language zh_CN --appearance dark --output artifacts/ui/zh-dark
```

The script captures panel, history, settings, and empty states from synthetic data. Additional switches include `--width 410`, `--reduce-transparency`, `--reduce-motion`, and `--range 30m|1d|7d`. Inspect rendered images for clipping and legibility before copying selected previews into `docs/assets/`. Never substitute real private sessions.

## Builds

`uv build` creates a source archive and wheel in `dist/`. The wheel does not bundle Python or Qt; a package manager installs dependencies. Inspect archive contents and test installation in a fresh environment before distributing them.

For a **local Windows EXE**, use an isolated packaging environment in PowerShell:

```powershell
$env:UV_PROJECT_ENVIRONMENT = 'build/.venv-packaging'
uv sync --locked --group packaging
uv run --locked --group packaging python scripts/build_windows.py
```

This creates `dist/windows/TokenPulse.exe`, a versioned Windows ZIP, and `SHA256SUMS.txt`. The ZIP includes dependency notices, `build-info.json` with runtime versions, and startup instructions. Notices are also embedded in the EXE. Only explicitly selected build files enter the ZIP; application data is not copied. Use `--output-dir <directory>` to choose a different destination, for example when a previous EXE is running. Each build uses fresh notice staging. The single-file GUI executable unpacks runtime dependencies to a temporary directory and opens no console. Use the source `token-pulse-cli` entry point for diagnostic output. The EXE shares the app data directory with a source installation; close existing instances before testing it.

The EXE is not signed or offered as a supported download. Its full distribution review is separate from source publication; see [third-party notices](../THIRD_PARTY.md). No AppImage or PyPI release is currently provided.

## Release verification

Before publishing a version:

1. Reconcile version metadata, both READMEs, changelog, compatibility scope, and examples.
2. Run checks from a clean checkout; inspect the wheel/source archive and test the installed package outside the source tree.
3. Inspect synthetic screenshots and any native platform behavior claimed for that version.
4. Review tracked files, commit metadata, screenshots, and package contents for private data and credentials.
5. Verify dependency notices for the actual artifacts being distributed. Source/wheel validation does not establish EXE distribution readiness.
6. Publish only verified artifacts and describe remaining limitations accurately. A tag or release must not imply unsupported platform coverage.
