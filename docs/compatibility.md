# Compatibility and limits

TokenPulse targets Windows and Linux. Internal Codex log formats are not a stable API; the current adapter targets observed 0.153.4-series formats and associated synthetic fixtures. No claim is made for all Codex versions.

## Platform evidence

| Environment | Recorded validation | Not established |
| --- | --- | --- |
| Windows 11 x64 | Native tray, normal window, hidden startup/fallback, history integration; local Python 3.11 / 3.13 checks | Other Windows versions or a clean-machine EXE installation |
| Ubuntu 24.04 WSL2, Python 3.12 | Wayland window, automated tests, no-tray fallback | Native GNOME/KDE/Xfce tray interaction or notifications |
| GitHub Actions Windows / Ubuntu, Python 3.11 / 3.13 | Automated matrix defined in the [workflow](../.github/workflows/ci.yml) | Native desktop interaction; consult actual run results |

The native Windows and WSL observations above were recorded during development on 2026-09-08. A local Windows EXE check on 2026-09-09 used Windows build 26200, Python 3.13.7, Qt 6.11.2, and PyInstaller 6.22.2. It exercised demo, tray, and hidden startup with a restricted PATH. It did not test a fresh Windows installation, signing, or full distribution readiness.

A Windows publication-preparation run on 2026-09-21 passed 205 offline tests and Ruff checks. Test counts describe that run, not a permanent project promise.

## Linux runtime

CI installs `libegl1`, `libopengl0`, and `libxkbcommon0` on Ubuntu. Real X11/Wayland platform plugins may need additional desktop-specific libraries; inspect Qt's load error for the target distribution. The locked x86-64 Linux Qt wheel uses `manylinux_2_34`; older glibc systems are outside the recorded validation.

Tray support depends on desktop protocols and extensions. Use `--no-tray` as a normal-window fallback. WSL window success does not establish general Linux tray compatibility.

## Source formats

| Source | Used for | Boundary |
| --- | --- | --- |
| `state_*.sqlite / threads` | Indexed paths, recent task snapshots, model and provider context | Current settings cannot fill historical request values |
| Session JSONL | Turn context, output items, usage, lifecycle events and state | Inherited history and repeated usage are not new output |
| `logs_*.sqlite / logs` | Item starts, scoped submission settings, request/auth evidence, explicit ignored-tier warnings | Only known outer structures and event shapes are parsed |
| `config.toml` | Current global `service_tier` snapshot | Does not resolve every project, task, or CLI override |

Final server-confirmed tier extraction is not implemented. Old request/response formats observed in research do not establish evidence for current requests. See [measurement](measurement.md) and [source evidence](source-audit.md).

## Bounded collection

- Live tracking: up to 32 recent unarchived tasks; normal polling about every 2 seconds and discovery every 10 seconds.
- Initial live tail: at most 4 MiB per session; incremental reads at most 2 MiB per poll. The live parser retains up to 300 responses.
- Historical recovery: indexed sessions updated within seven days, including archived sessions, independent of live limits; from the beginning of each file in chunks of up to 8 MiB and 16 file operations per batch. New/changed files are discovered every 60 seconds; active recovery batches are spaced at most 0.25 seconds apart.
- Without a usable index: probe the most recent three date directories and session-root files, rather than recursively scanning all history.
- JSONL lines: at most 8 MiB. Partial lines wait for completion; interrupted files are not marked fully recovered.
- Diagnostic logs: initial general ID window of 50,000, advancing by at most 10,000 rows per poll. Separate bounded startup passes cover up to 2,000 submission rows and 40,000 relevant output/auth events, with 16,000-character row limits and a 20,000-entry output-start cache.
- Storage: at most 30 days / 10,000 samples; bounded withdrawal records prevent proven old duplicates from returning.
- State freshness: 120 seconds. Latest-output staleness: 60 seconds. Tray averages remain available for their selected history window.

Files and diagnostic evidence can be rotated, truncated, unavailable, or internally inconsistent. Recovery completion only describes accessible inputs. It cannot reconstruct deleted evidence or guarantee all historical outputs.

Versioned parser corrections may invalidate proven old timing errors; missing logs alone do not erase saved valid evidence. Recovery reuse is invalidated by file, evidence, or rule changes. See [architecture](architecture.md) for the ownership and persistence boundaries.
