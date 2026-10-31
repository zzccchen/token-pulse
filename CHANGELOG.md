# Changelog

## Unreleased

## 0.1.7 — Verified stream origins and historical thread configuration

- Recover thread-owned `thread_settings_applied` snapshots as separate historical evidence. Validate fork ownership, event order, turn-start boundaries and exact turn overrides; missing evidence keeps Other rather than inheriting a parent, goal, or current global setting.
- Parse supported `TurnStartOptions.service_tier` overrides separately from persistent submitted settings. Only an explicit absence of an override can bind a compatible pre-turn snapshot for a labeled mode fallback; request and server-confirmed values remain independent.
- Expose snapshots, observation times, association reasons and turn overrides in record details/history and CSV. Preserve sample identity and TPS through enrichment, source loss, restart and transport reconciliation; earlier transport starts recheck snapshot validity.
- Require an independent, exactly matched diagnostic stream start or a validated transport sequence. A positive `item_completed` lifecycle duration can be synthesized during completion handling and no longer establishes generation time by itself, regardless of duration or speed.
- Correct saved pre-revision-8 lifecycle-only measurements at history startup, even without source files. Preserve identities, usage and tier evidence; protect saved independent timing from source loss and allow later independent evidence to revalidate the same record. Do not clamp or discard samples by TPS.
- Preserve subsecond precision in timeline hover cards instead of rounding millisecond intervals to centiseconds or zero.
- Bump measurement/transport parser revisions to 8 and recovery revision to 12. Add synthetic ownership, chronology, conflicts, incremental recovery, lifecycle-origin migration and bilingual UI regressions.

## 0.1.6 — Validated transport measurements and historical recovery

- Exclude whole responses with unknown output-item scopes or unvalidated legacy usage counters. Preserve explicit usage records without output timing instead of dropping them.
- Replace adjacent, exactly scoped legacy/explicit usage without duplicate history. Let later matched diagnostic starts extend saved lifecycle intervals within the same parser revision; keep valid measurements when logs disappear.
- Recover indexed `.jsonl.zst` archives with bounded streaming decompression, complete-frame validation, restart-safe checkpoints, and plain/compressed identity deduplication.
- Discover compatible SQLite schemas in current and historical layouts, parse bounded older Debug item starts, and respect explicit subagent history ordinal boundaries. Selection depends on available evidence rather than Codex version labels.
- Integrate validated SSE/WebSocket Responses sequences from compatible diagnostic databases into normal statistics. Require exact routing, contiguous event sequences, matching item/content lifecycles and completed-response usage; preserve excluded records and explicit server-returned tiers.
- Deduplicate transport and session evidence by scoped response IDs or complete ordered item identities, including across restarts. Prefer the complete validated transport interval; conflicting usage or ambiguous identities cannot produce TPS.
- Recover transport logs incrementally with bounded reads, rotation detection and HMAC-only checkpoints committed after samples. Interrupted streams replay from the last quiescent checkpoint without persisting their content.
- Exclude intervals spanning explicit, exactly scoped sampling retries; retain their tokens without estimating or subtracting retry time. Correct saved measurements outside the live response cache and prevent missing logs from restoring invalid timing. A complete exact transport sequence can independently validate an interval.
- Preserve unsupported server-tool exclusion reasons instead of masking them with subsequent item-index conflicts. No speed or duration thresholds are introduced.
- Bump measurement parser revision to 7 and recovery revision to 10. Add synthetic regressions and pyzstd dependency/packaging notices. Thread-history projections remain outside production sample feeds.

## 0.1.5 — Sampling tags with unquoted working directories

- Recognize scoped configuration tags when a sampling span ends with an unquoted absolute working directory containing spaces. Ignore the directory value; keep exact task/turn matching and reject ambiguous field-like path content.
- Bump recovery revision to 7 so retained outputs can gain the previously missed tags without changing tokens, timing, or TPS. Configuration tags remain separate from submitted settings, requests, and server confirmation.
- Follow up on [issue #3](https://github.com/zzccchen/token-pulse/issues/3) with synthetic Windows, UNC, and POSIX path regressions, identity-conflict rejection, and measurement-preserving enrichment checks.

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
