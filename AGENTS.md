# Agent instructions

These rules apply throughout the repository; follow more specific child instructions too. Explicit instructions from the user take precedence. Read relevant code and documentation before editing, preserve unrelated changes, and keep work reviewable.

## Product and scope

TokenPulse (词脉) is an independent local, read-only Codex tray monitor for Windows and Linux, using Python 3.11+, PySide6 Essentials, and SQLite. Its own code is MIT-licensed. [Architecture](docs/architecture.md) and the [measurement contract](docs/measurement.md) describe the current implementation.

The initial scope includes local discovery, model/effort/channel/tier evidence, completed-output speed, recent task state, local history, filtering, and CSV export. One model combines conversations, modes, providers, efforts, and accepted sources. Remote collection, other clients, cost estimates, and changing Codex configuration are separate future proposals; do not build speculative frameworks.

## Measurement correctness

- Keep global configuration, submitted settings, outbound request values, and server-confirmed values separate. Match submissions to exact tasks and turns. A setting does not prove what was requested; a request does not prove server execution.
- Channel/provider and authentication are separate from service tier. Unknown values remain unknown; never infer Standard, zero TPS, or effective priority from missing fields or observed speed.
- Parse only bounded outer submission structures, skipping user content. Distinguish clear `Some(None)`, unspecified `None`, explicit default, and conflicts. Evidence enrichment preserves sample identity and valid TPS without duplicating records.
- Warn about ignored configuration only with explicit evidence. Do not generalize one environment's Flex or tier behavior.
- Record available task/turn/response/item identity, model, effort, channel, tiers, timestamps, token scope, and source. Missing evidence stays null; group-level known values must not fill raw unknown values.
- TPS requires matching tokens and output-stream time. Exclude waiting and tool execution. Whole-turn time is not output time. Total output tokens may include reasoning/tool generation; never call them visible words or add reasoning tokens twice.
- Delayed usage after tool completion is not intrinsically invalid. Matching non-tool `item_completed` millisecond boundaries may supplement diagnostic logs. Tool execution timing and zero-duration lifecycle placeholders cannot establish generation starts.
- Tool-first notifications already name the tool and cannot establish the complete generation start for total response tokens. Keep tokens and exclude TPS. Do not introduce arbitrary duration or speed thresholds.
- If the first non-tool start/completion and matching diagnostic notification collapse to the same millisecond, later items cannot establish the whole response duration. A matching earlier diagnostic start may still validate it.
- Forked/copied history, unchanged cumulative usage snapshots, and rereads are not new output. Handle resets, retries, duplicate events, interleaved tasks, and missing boundaries explicitly.
- Proven old parsing errors can be corrected or withdrawn with stable pseudonymized identities and newer parser revisions. Missing new logs alone must not overwrite saved valid measurements. Repeated same-version withdrawal notifications must not erase already revalidated samples.
- The selected-window average is total valid output tokens divided by total matching stream seconds, not the arithmetic mean of sample TPS. The panel's median/range use that window's valid samples; any other recent-sample window must be labeled with its size.
- Aggregate only compatible total-output samples. Preserve excluded records and reasons. Concurrent samples each contribute duration; wall-clock combined throughput is a separate metric.
- Label speeds as completed-output log estimates and show freshness. Do not claim real-time measurement without sufficiently granular matching streaming evidence. Local comparisons are not controlled benchmarks or performance promises.

## UI contract

- The compact panel prioritizes model, window-average speed, variation, and individual-output timeline. Channels/configuration belong in details; history/settings are secondary.
- Select models only through the dropdown. Retain selection when other tasks become active. Do not reintroduce mode/provider/channel subgroups or hidden partial totals.
- Offer 30-minute, 1-day, and 7-day ranges. Model choices use the selected range; current state uses only evidence within 120 seconds. Historical errors/idle states are not current activity.
- Count conversations with incomplete records. Show total, measurable, and excluded records separately; insufficient timing is not an unidentified model or zero conversations.
- Plot single-output scatter points by completion time and TPS. No mean bars/error bars, invented zero points for missing data, jitter, or implied per-second output.
- Use shape and color for Fast, Standard, and other/unrecorded modes. Prefer explicit request evidence, then submitted settings. Unknown is not Standard. Hover cards show time, mode, TPS, tokens, and duration; raw evidence/source/confirmation belong in clicked records. Keyboard navigation must reach overlaps.
- Keep unconfirmed actual tier an ordinary informational state. Only explicit anomalies generate deduplicated alerts. State indications need text, not color alone.
- Tray speed text is optional and uses the same model/range average. Retain it while that range has valid data; do not hide it after 60 seconds without output. Tooltip includes range and most recent valid sample. Without valid data, show the icon.
- Provide a normal-window fallback without a tray. Record native desktop verification precisely; WSL/offscreen success does not establish all Linux tray support.
- Use `i18n.tr()` for UI text. Translate full templates before formatting; retain Chinese keys and complete English entries. Raw evidence and CSV remain untranslated.
- Language previews immediately; Save persists, Cancel/failed save restores the previous display language. Refresh existing windows with `retranslate_ui()`, preserving all selections. Support `--language system|zh_CN|en`.

## Implementation and privacy

- Separate source collection, normalized statistics/storage, and UI. The UI never parses raw logs. Keep OS differences in discovery, paths, and tray/window integration.
- Treat internal source formats as version-dependent. Document assumptions and useful degradation; do not fabricate defaults after parse failures.
- Incremental reading must handle rotation, truncation, partial lines, out-of-order events, temporary unavailability, and process exit.
- Persist completed-file records only after corresponding samples are saved. Store HMAC identities/digests, not paths, raw IDs, text, or parser content. Changed files/evidence/rules invalidate reuse. Bump `backfill.RECOVERY_REVISION` for interpretation/association changes; update parser revisions when correcting existing samples.
- Do not mark interrupted or incomplete files finished. Retain bounded pending writes and retry. Publish recovery batches and checking times without calling each check new output.
- Keep live state tracking independent of historical recovery: the 32-task limit must not limit seven-day indexed session coverage, including archives. Normalize long Windows paths before validating containment. Missing-turn merging requires a unique task/response match.
- Avoid repeated whole-directory scans and full-history rereads. Establish resource claims through measurement. Do not hardcode developer usernames, private paths, task IDs, or model lists.
- Only read necessary local sources. Do not modify Codex settings, read credentials, send model requests, or upload telemetry.
- Do not persist prompts, answers, tool arguments, titles, full logs, credentials, or raw task identities. Titles may exist in memory; selected source paths may exist in preferences. Explain pseudonymization honestly.
- Debug output, reports, screenshots, and CSV follow the same privacy boundaries. Escape CSV formulas; use synthetic fixtures and demo screenshots. Never commit private session data, databases, or credentials.
- Public documents must stand alone, without private task links or brainstorming transcripts. Preserve third-party attribution/licenses. State that the project is independent of OpenAI.
- Source/wheel publication does not establish frozen EXE distribution readiness. Follow [third-party notices](THIRD_PARTY.md) and [release checks](docs/development.md#release-verification).

## Commands and validation

- `uv sync --locked`: install project/development dependencies.
- `uv run pytest`: offline regression tests.
- `uv run ruff check .` and `uv run ruff format --check .`: lint and format.
- `uv run python scripts/check_docs.py`: local documentation links and anchors.
- `uv run token-pulse --demo`: isolated demo; omit `--demo` for local collection.
- `uv run token-pulse-cli --diagnose`: bounded read-only summary, without titles or paths.
- `uv run python -m token_pulse --demo --no-tray --smoke-test`: startup/exit check.
- `uv build`: source archive/wheel; inspect actual contents before publishing.
- Windows packaging: set `$env:UV_PROJECT_ENVIRONMENT = 'build/.venv-packaging'`, then `uv sync --locked --group packaging` and `uv run --locked --group packaging python scripts/build_windows.py`.
- UI changes: run `uv run pytest tests/test_i18n.py`; capture with `uv run python scripts/capture_demo.py --language en --output artifacts/ui/en`. Repeat with Chinese, dark theme, and relevant accessibility/width options. Range arguments are `30m`, `1d`, `7d`.

Use deterministic tests for parsing/statistical changes, including normal output, resets, duplicates, retries, concurrency, missing fields, invalid duration, and incomplete boundaries. File readers need recovery tests; UI changes need no-data, stale, switching, and no-tray checks. Run checks relevant to the change; document-only changes need consistency/link review, not invented business tests. Report what was actually validated and any remaining limits.

## Collaboration and Git

Communicate with the maintainer in Chinese by default. Public docs, comments, identifiers, and new commit messages use English; both READMEs must stay consistent. Chinese localization keys are intentional.

Make focused, reviewable commits within the user's authorization. Use `<type>: <summary>` with feat, fix, refactor, perf, docs, test, style, build, ci, or chore. Bodies explain actual changes and relevant validation. Commit authorization alone does not authorize pushing, merging, or publishing; follow the user's explicit task scope. Preserve unrelated changes and never silently discard local work.
