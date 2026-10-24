# Changelog

## 0.1.5 — Unreleased

- Recognize scoped configuration tags when a sampling span ends with an unquoted absolute working directory containing spaces. Ignore the directory value; keep exact task/turn matching and reject ambiguous field-like path content.
- Bump recovery revision to 7 so retained outputs can gain the previously missed tags without changing tokens, timing, or TPS. Configuration tags remain separate from submitted settings, requests, and server confirmation.

## 0.1.4 — Scoped configuration evidence and separate Ultra Fast display

- Address [issue #3](https://github.com/zzccchen/token-pulse/issues/3), reported by @WildPickledPepper: accept bounded sampling configuration tags with exact task/turn matching, including the reported shorter trace shape.
- Store configuration tags separately from submissions, outbound requests, and server confirmation. Use an explicitly labeled configuration fallback only when stronger evidence is absent; unset, missing, and conflicting evidence never implies Standard.
- Show mode/evidence beside model and TPS in history and single-output records. Keep Ultra Fast distinct from Fast without splitting model statistics.
- Enrich retained history through pseudonymized task/turn matches without changing output identity, tokens, timing, or TPS. Recovery revision 6 invalidates old checkpoints; missing logs cannot reconstruct deleted tags.
- Correct the evidence classification in the unpublished 0.1.2/0.1.3 local previews. Existing public 0.1.1 history remains readable. No general support claim is made for all 0.162/0.163 builds.

## 0.1.3 — Local preview, not published

- Display `ultrafast` as Ultra Fast with an orange triangle, separate from the teal Fast diamond, in the timeline legend and hover cards. Preserve raw tier evidence and model-wide aggregation.
- Wrap the four-mode legend on narrow charts and refresh plot coordinates when the language changes. Include Ultra Fast in synthetic previews and bilingual UI regression checks.

## 0.1.2 — Local preview, not published

- Recognize bounded, turn-scoped service-tier tags in diagnostic `feedback_tags.tags_json` records. This preview incorrectly grouped tags with submitted settings; 0.1.4 separates their provenance.
- Re-evaluate historical recovery checkpoints for the new evidence source while preserving sample identity and valid speed measurements.
- Classify explicit `ultrafast` request/settings evidence as Fast while retaining the original value in history and CSV.
- Add synthetic regression coverage for scoped settings, conflicts, quoted content, truncated logs, replay, and measurement-preserving enrichment. Record bounded local checks of newer 0.162/0.163 alpha session formats without claiming support for all intervening versions.

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
