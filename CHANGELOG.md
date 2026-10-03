# Changelog

## 0.1.1 — Windows portable preview

- Update both READMEs with the Codex development credit, supported Codex CLI 0.153.4–0.160.0 range, and issue reporting links.
- Package local Windows builds as a versioned ZIP with dependency notices, runtime metadata, startup instructions, and SHA-256 checksums.
- Use fresh notice staging and a configurable output directory so a running older EXE can remain in place.
- Ship replaceable runtime DLLs, LGPL/GPL texts and upstream attributions, complete matching Qt/PySide sources, and library replacement/rebuild instructions.
- Pin and verify source archive hashes; omit unused Qt plugins and software OpenGL. Builds remain unsigned; clean-machine validation remains pending.

## 0.1.0 — Initial public baseline

- Read-only collection of supported local Codex session and diagnostic formats.
- Per-model weighted output TPS, median, range, and individual-output scatter plots over three time windows.
- Separate configuration, submission, request, and actual-tier fields, with missing evidence kept explicit.
- Bounded historical recovery, local pseudonymized storage, filtering, and CSV export.
- Tray and normal-window entry points, light/dark appearance, reduced effects, and English/Chinese UI.
- Synthetic offline regression tests, cross-platform CI, and source/wheel build support.
- Bilingual overview, measurement and privacy documentation, and contributor guidance.

This baseline consolidates pre-publication development. Internal logs can change, full Linux desktop tray validation remains open, and server-confirmed tier extraction is not implemented. A standalone Windows executable can be built locally but is not yet offered as a supported release artifact. See [compatibility](docs/compatibility.md).
