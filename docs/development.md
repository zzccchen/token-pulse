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

## Isolated seven-day rebuild

```sh
uv run python scripts/rebuild_week.py --output /new/local/audit-directory --baseline /local/monitor/history.sqlite
```

The destination must be new and outside the Codex source directory. The script uses the current parser, fixes the window end at startup, drains available evidence and indexed sessions in batches, and writes only pseudonymized statistics/evidence plus an aggregate `report.json`. `--codex-home` selects a source; `--end` accepts Unix seconds for a repeatable window. `--max-seconds` bounds the run; interrupted/partial sources do not count as completed. Keep private audit databases and reports outside the repository.

The optional baseline is opened read-only. It supplies matching HMAC identities and comparison counts, never saved output records. Add `--restore-evidence` for a second run that also restores source-matched, seven-day archived origins, retries and streaming activity. Retry retention floors remain enforced. Comparing both runs distinguishes surviving-log recovery from recovery aided by previously captured proof; missing new logs alone do not invalidate saved independent measurements. Sources may change during a run, so completion describes the discovered accessible files, not an atomic snapshot or proof that all original logs survive. Exact-key comparison counts may differ when newer parsers canonicalize output identities.

For a fixed set of excluded records from a rebuild, run:

```sh
uv run python scripts/audit_exclusions.py --reference /local/rebuild/history.sqlite --baseline /local/monitor/history.sqlite --output /new/repair/report.json --repaired-output /new/repair
```

The reference directory must contain its `report.json` with the fixed window. The audit reparses that exclusion set, checks compatible diagnostic stores (including structurally intact deleted records with a separate 256 MiB snapshot ceiling), and inventories desktop activity/completion notifications before host filtering. Unassociated notifications are candidates, never automatically trusted evidence. Resource experiments do not change production limits.

To evaluate explicitly relaxed rate coverage on the repaired fixed population:

```sh
uv run python scripts/relaxed_coverage.py --reference /local/repair/history.sqlite --output /new/relaxed --target 0.99
```

The output directory must be new and outside the Codex source root. `history.sqlite` preserves the original sample rows and adds a separate `relaxed_estimates` table. `estimates.csv` includes every reference identity, its original exclusion, estimate basis, observed interval and both estimated rate and validated TPS (blank when unavailable). `report.json` reports the unchanged denominator, coverage ladder, remaining exclusions, source diagnostics, integrity and reopen checks. Exit code 2 means the requested target was not reached; missing records are not discarded or assigned invented durations. The default target is 99%, not a promise that every source set can reach it. Lifecycle and response-cycle estimates have different semantics from output TPS; see the [measurement contract](measurement.md#identity-and-recovery). The audit now uses the same normalized cycle boundaries and estimation policy as production collection. It does not modify the running monitor database.

An optional new repair directory preserves the reference output set, applies exact independently revalidated measurements, and restores matching saved independent intervals after identity, token, completion, alias, withdrawal and retry checks. It never changes the source, baseline or reference database. The report separates newly revalidated outputs from restored saved measurements and verifies database integrity/reopening. `unverified-lifecycle.csv` contains explicitly labeled arithmetic for remaining positive lifecycle-only intervals; it does not promote those rows into validated TPS. Its HMAC identifiers follow the same local privacy boundary as normal history.

## Builds

`uv build` creates a source archive and wheel in `dist/`. The wheel does not bundle Python or Qt; a package manager installs dependencies. Inspect archive contents and test installation in a fresh environment before distributing them.

For the **Windows portable ZIP**, use the audited Python 3.13.7 runtime and an isolated packaging environment in PowerShell:

```powershell
$env:UV_PROJECT_ENVIRONMENT = 'build/.venv-packaging'
uv sync --locked --group packaging --python 3.13.7
uv run --locked --group packaging python scripts/build_windows.py
```

This produces a versioned portable ZIP and `SHA256SUMS.txt` in `dist/windows/`. Extract the entire ZIP and launch `TokenPulse.exe`; the `_internal` directory contains external DLLs that users can replace. Do not distribute the EXE alone. The ZIP includes licenses, upstream attributions, complete Qt Base and PySide/Shiboken source archives, the corresponding application source, runtime metadata, and [library replacement instructions](windows-distribution.md). It requires no separate Python installation.

The builder downloads source/notice inputs from pinned upstream URLs, verifies SHA-256, and caches them in `build/license-sources/`. This is build-time network access only. The application remains offline. Dependency versions are checked against the reviewed Python 3.13.7 / OpenSSL 3.5.3 / Qt 6.11.2 inputs; update the review and source manifest before changing them. The Qt source hashes were verified against the official download mirror metadata.

Fresh staging avoids stale notices and leaves running older copies untouched. Use `--output-dir <directory>` for a separate output destination. The source snapshot uses Git's tracked-file list; commit all intended source files before release builds. The EXE uses the same app data directory as a source installation; close existing instances before local collection, or use isolated `--demo` smoke checks.

The build is unsigned. Clean-machine Windows validation remains pending; no AppImage or PyPI release is provided. See [third-party notices](../THIRD_PARTY.md) for distribution terms.

## Release verification

Before publishing a version:

1. Reconcile version metadata, both READMEs, changelog, compatibility scope, and examples.
2. Run checks from a clean checkout; inspect the wheel/source archive and test the installed package outside the source tree.
3. Inspect synthetic screenshots and any native platform behavior claimed for that version.
4. Review tracked files, commit metadata, screenshots, and package contents for private data and credentials.
5. Verify dependency notices for the actual artifacts being distributed. Source/wheel validation does not establish EXE distribution readiness.
6. Publish only verified artifacts and describe remaining limitations accurately. A tag or release must not imply unsupported platform coverage.

For storage changes, verify a fixed pre-migration snapshot against the destination by identities, tokens, valid intervals and all checkpoint/evidence tables. Check integrity after closing the migration writer and after restarting the actual packaged executable. Resolve opened main/WAL/SHM/lock handles to confirm co-location; matching path strings are insufficient under per-file filesystem redirection. Synthetic tests must cover committed WAL recovery, wrong-generation journals, interrupted installation, conflicting destinations and lock ownership. Keep local count-only receipts outside published artifacts.
