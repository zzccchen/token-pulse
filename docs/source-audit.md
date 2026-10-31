# Codex source evidence

This is a version-scoped adapter reference, not a promise about future Codex formats. The project was developed against observed 0.153.4-series local logs. The supported Codex CLI range is 0.153.4–0.160.0; see [compatibility](compatibility.md#codex-versions) for details. Earlier source review used the public `rust-v0.153.0` implementation. A session's creation version need not match every later appended event.

## Source relationships

| Source | Evidence | Interpretation boundary |
| --- | --- | --- |
| State SQLite `threads` | Session index, paths, current settings | Current model/settings cannot fill historical turns |
| JSONL `turn_context` | Turn model and reasoning effort | Parse the outer structure, not user text |
| JSONL `thread_settings_applied` | Historical thread preference with explicit owner | Additional turn-start and override evidence is required for mode association |
| `token_usage_record.usage` | Explicit per-response usage and identifiers | Turn/thread aggregates are different scopes |
| `token_count` | Last usage and cumulative snapshots | An unchanged cumulative snapshot is not a new response |
| `item_completed` | Item start/end millisecond timestamps | Non-tool ends may supplement independent stream starts; lifecycle starts alone are not generation evidence; tool timing describes execution |
| Thread-history SQLite | Derived turn/item projections | Not another independent source of samples |
| Diagnostic SQLite | Output starts, submission/request/auth evidence | Retention and association limit availability |
| Diagnostic SQLite SSE/WebSocket events | Completed-response usage, item timing and returned service tier | Only complete, identity-consistent supported sequences qualify for TPS |
| Desktop logs | UI and transport diagnostics | Events without item IDs cannot establish precise boundaries |

Historical upstream reference: [protocol definitions](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/protocol/src/protocol.rs) and [thread-history projection](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/app-server-protocol/src/protocol/thread_history_projection.rs). Tests in this repository are the executable contract for supported input shapes.

## Timing and identity pitfalls

- Usage can follow tool results. Preserve the matching model outputs and end timing at output completion, not usage arrival.
- Completion events may carry original start/end times. Non-tool positive-duration boundaries can supplement a matching diagnostic start; contradictory evidence still invalidates timing.
- Tool-first notifications already identify the tool. They cannot establish the full generation interval for total response tokens, even if argument streaming continues afterward.
- First-item start/completion events can be synthesized during completion handling. Their elapsed time can be positive; duration alone cannot establish streaming provenance. Later output cannot repair the first item's missing stream origin.
- Forks can copy ancestor history with new outer write timestamps. Detect ownership boundaries rather than counting copied output as newly generated.
- Repeated cumulative snapshots cannot prematurely close pending output using old last-usage values.
- Diagnostic retention is task-dependent. Old dates elsewhere in the database do not prove a particular task's starts remain available.

Historical references: [turn processing](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core/src/session/turn.rs), [session lifecycle](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core/src/session/mod.rs), and [rollout recorder](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/rollout/src/recorder.rs). These references explain the original investigation; do not generalize a version's behavior into an unconditional adapter rule.

## Submitted-tier association

The adapter recognizes bounded outer `TurnInput.request.thread_settings.service_tier` evidence for supported Start / StartOrSteer submissions. Thread identity and submission/turn identity must match. Standalone settings changes and agent messages do not automatically apply to an unrelated output.

The parser distinguishes clear, unspecified, explicit, and conflicting states. It handles nested delimiters and escaped strings while skipping user text. Truncation, duplicate fields, excessive depth, conflicting records, and unsupported nonempty overrides are not guessed away. See `tests/test_submissions.py` and `tests/test_logs.py`.

Newer diagnostic `feedback_tags` records can place the client service tier in the outer `tags_json.service_tier` string. This adapter requires `session_loop.thread_id` to match the database task, a recognized `turn` or `codex.turn_input` span with `turn.id`, and a matching `try_run_sampling_request` or `run_sampling_request` span with `turn_id`. Any additional thread/turn fields must agree. Quoted strings cannot supply scope fields. Parsing is bounded to 16,000 characters. Duplicate fields, invalid JSON, absent/null tiers, and nested tier-like content provide no tag. Contradictory tags for the same task and turn remain ambiguous.

These tags have a separate configuration-evidence category. They are neither submitted settings, outbound request values, nor server confirmation. A `thread_settings_applied` event alone does not identify a turn; its separate adapter requires the additional checks below. Recovery revision 6 invalidated previous checkpoints to enrich retained samples from scoped feedback without changing the measurement parser revision or timing rules. The public 0.1.1 format remains readable; 0.1.2/0.1.3 were unpublished local previews whose tag/submission classification required correction.

Recovery revision 7 also handles a terminal `cwd` on recognized sampling spans when Path Display formatting leaves an absolute path containing spaces unquoted. The directory contributes no identity evidence and is not retained. Field-like content, embedded quotes/braces/newlines, and nonterminal ambiguous paths are rejected; every observed task/turn field still has to agree. This does not relax parsing of unrelated structured diagnostic values.

The source review on 2026-10-22 used [sampling feedback emission](https://github.com/openai/codex/blob/86e17e98c7f901277aeeb4649cf1b01462b082f2/codex-rs/core/src/session/turn.rs) and the [scalar feedback tag builder](https://github.com/openai/codex/blob/86e17e98c7f901277aeeb4649cf1b01462b082f2/codex-rs/core/src/feedback_config.rs). The tier comes from the sampling step's settings. The builder represents an absent value as `unset`; the adapter retains that as unspecified, not as a clear instruction or explicit Standard setting. This source reference explains the interpretation and is not a claim that the installed binary was built from that commit.

A display marker prefers request evidence, otherwise supported submitted settings. Source attribution and raw values remain in record details. Supported transport completions separately expose the server-returned `service_tier`; diagnostic configuration tags never populate that field. Global configuration cannot fill historical request values.

## Parser corrections

### Historical thread snapshot semantics

Source review uses the pinned upstream [event definitions](https://github.com/openai/codex/blob/13ab0b254e7b9ed0ab473d38d978fa100bf417ae/codex-rs/protocol/src/protocol.rs) and [turn-input options](https://github.com/openai/codex/blob/13ab0b254e7b9ed0ab473d38d978fa100bf417ae/codex-rs/protocol/src/turn_input.rs). `ThreadSettingsAppliedEvent.thread_id` identifies logical ownership independently of the physical file: copied snapshots retain their original owner, and older histories may omit it. `TurnStartedEvent.started_at` is optional integer Unix seconds. `TurnStartOptions.service_tier` is a per-turn override rather than a persistent preference mutation. Steered input can apply thread settings to subsequent turns while the active turn retains its own options.

The [goal runtime](https://github.com/openai/codex/blob/13ab0b254e7b9ed0ab473d38d978fa100bf417ae/codex-rs/ext/goal/src/runtime.rs) can start continuation turns with start options. Goal attribution therefore cannot prove absence of a tier override. These source references explain supported semantics; they do not identify the commit of an installed binary or certify every historical format.

Recovery revision 11 adds separate `thread_config_*` and `turn_override_*` evidence without copying values into request or actual-tier fields. The [association contract](measurement.md#historical-thread-configuration) requires ownership, conservative chronology and exactly scoped override evidence. Unsupported/absent owners, copied prefixes, duplicate JSON metadata fields, gaps, unknown overrides and conflicting starts cannot establish the fallback. `tests/test_thread_settings.py` covers synthetic plain/compressed recovery, source loss, restart, forks, goals, conflicts and transport timing replacement; `tests/test_i18n.py` covers the independent display columns.

### Measurement parser revisions

| Revision | Correction preserved in recovery |
| --- | --- |
| 2 | Complete response measurements can replace legacy split ranges; proven fork/snapshot duplicates can be withdrawn |
| 3 | Tool-first measurements with unconfirmed full starts lose TPS while retaining identity and tokens |
| 4 | Same-millisecond first-item placeholders without earlier matched starts are excluded |
| 5 | Unknown output scopes and unvalidated legacy counters cannot produce TPS; retain explicit usage without items; replace adjacent, matching legacy usage without duplicating history |
| 6 | Validate transport sequences and exact cross-source aliases; prefer complete transport timing and exclude conflicting usage or ambiguous response ownership |
| 7 | Reject session intervals that cross explicit scoped sampling retries; correct saved measurements without estimating wait time; retain the original unsupported server-tool exclusion |
| 8 | Require independent first-item stream-origin evidence; withdraw saved lifecycle-only rates without source files, preserve independent saved timing and support later revalidation |
| 9 | Distinguish supported inter-agent/tool-definition inputs from output; revalidate exact identities using surviving diagnostic records and archived origins/retries |

New corrections must update parser/recovery versions as appropriate. Mere log absence cannot erase an existing valid measurement. Old parser writes must not restore proven invalid timing; repeated same-version withdrawals cannot remove already revalidated samples.

Regression fixtures cover delayed usage, duplicate snapshots, resets/conflicts, fork boundaries, millisecond placeholders, earlier valid starts, cross-restart withdrawals, missing-turn reconciliation, and evidence enrichment. See `tests/test_rollout.py`, `tests/test_history.py`, and `tests/test_backfill.py`. Public fixtures are synthetic; private development logs are not part of the repository.

## Capability-based historical formats

Recovery revision 12 re-evaluates retained sources for parser revision 8 and the lifecycle-origin correction, following thread snapshots in revision 11, scoped retries in revision 10, the transport adapter in revision 9 and historical adapters in revision 8. Runtime decisions use actual fields and container formats, not `cli_version`, model names, machine-specific paths, or observed speed. Session creation labels are not reliable writer-version boundaries: migration and later appends can change a file's format.

| Observed capability | Adapter behavior |
| --- | --- |
| Required SQLite index/log columns in the root or historical `sqlite/` directory | Select a compatible database; newer incompatible names do not hide usable older schemas |
| Plain JSONL or a `.jsonl.zst` sibling | Decode bounded chunks with the same event parser and stable sample identities |
| Legacy cumulative usage with complete matching counters | Validate the increment before measuring; missing IDs/timing remain exclusions |
| Explicit per-response usage and non-tool lifecycle timestamps | Preserve response identity and usage; require an independent stream origin before measuring; matching lifecycle ends can supplement it |
| Diagnostic `item_type`/`item_id` fields or outer Rust Debug `Message`/`Reasoning`/tool objects | Extract bounded outer item identity and consistent task/turn scope; skip nested content |
| `subagent_history_start_ordinal` | Withdraw copied-prefix output; missing ordinals cannot establish ownership |
| `history_base` reference | Do not follow the parent or count its output as the child's |

## Transport event validation

The production adapter reads compatible SQLite `logs` tables for the targets `codex_api::sse::responses` and `codex_api::endpoint::responses_websocket`. Required columns are `id`, `ts`, `ts_nanos`, `thread_id`, `target` and `feedback_log_body`. `process_uuid` supplies process identity; without it, completed usage can be retained but cannot establish valid timing. Process, task, turn, response and item IDs are opaque identifiers, not prescribed UUID or model-name formats.

Recognized scoped outer messages are `SSE event:`, `sse event:`, `websocket event:` and `event:` followed by JSON, JSON-quoted JSON or a single SSE data frame. All observed task/turn scopes must agree; optional request/connection IDs refine routing. Nested content cannot provide routing identity. Plain desktop text files and arbitrary Debug event renderings are not additional transport feeds.

Each stream requires a created event at sequence zero, contiguous integer sequence numbers, consistent response identity, ordered supported items, matching content/summary parts and channel completion, then successful completed status and integer output usage. Exact replay is ignored; conflicting duplicates, gaps, out-of-order timestamps, unsupported event/item types and incomplete boundaries exclude TPS. Supported generated items are messages, reasoning, function calls and custom tool calls. A tool-first response still lacks a proven complete start. Interleaved explicit response IDs are tracked independently; id-less events with multiple possible owners are ambiguous.

Final response output arrays may be empty. Only a validated full event inventory can compensate; a terminal event alone cannot. Delta-event counts are not token counts, and integer server timestamps are not decode durations. Client log item timestamps determine the interval, while final response usage determines tokens. In-memory HMACs detect replay conflicts without retaining content in parser state. Persisted aliases require the exact complete ordered item set, never estimated time/token matches.

The reader incrementally replays only a bounded seven-day source window and saves quiescent cursors after samples are durable. Unsupported or incomplete sequences remain excluded observations when response identity is available. Thread-history projections remain outside production sample feeds. See `tests/test_transport.py` and `tests/test_transport_logs.py` for synthetic format, concurrency, conflict, privacy and recovery regressions.

## Sampling interruptions and timing limits

The adapter recognizes bounded outer warnings from `codex_core::responses_retry`, with consistent task/turn scopes, `handle_response_stream_error{request=Sampling}` and a matching outer `turn_id`. Supported messages identify connection-retry waits or disconnected sampling retries. It skips error text, remote-compaction retries, nested lookalikes and conflicting identities. The [upstream retry handler](https://github.com/openai/codex/blob/main/codex-rs/core/src/responses_retry.rs) distinguishes sampling from remote compaction and sleeps between attempts; this source reference explains the interpretation, not the version of an installed binary. Synthetic tests in `tests/test_retry_evidence.py` define supported shapes.

Session output items can survive a failed attempt without a closing usage record, then be joined to a later successful response in the same turn. A start/end pair spanning an explicit retry therefore cannot measure continuous output. Retry evidence participates in recovery invalidation and exact pseudonymized history correction. Tokens remain available; retry duration is never guessed or subtracted.

Short positive spans have a different limitation: diagnostic item notifications and lifecycle timestamps can originate from the same received event batch. They do not independently certify server generation speed. Absent complete matching transport records, the adapter cannot identify a buffering delay or reconstruct a more accurate rate. Large TPS values are not removed solely for their magnitude.

### Positive lifecycle intervals without a stream origin

The pinned upstream [completed-item handler](https://github.com/openai/codex/blob/13ab0b254e7b9ed0ab473d38d978fa100bf417ae/codex-rs/core/src/stream_events_utils.rs) emits a start immediately before completion when no previously active non-tool item exists. The [session event emitter](https://github.com/openai/codex/blob/13ab0b254e7b9ed0ab473d38d978fa100bf417ae/codex-rs/core/src/session/mod.rs) records wall-clock times around asynchronous work and completion contributors. A synthesized pair therefore need not have zero duration; one millisecond or a much longer duration cannot distinguish it from a genuine streaming lifecycle.

Parser revision 8 consequently stops treating positive lifecycle time alone as a generation start. It does not assert that every such item was synthesized, and it does not infer a replacement start from a nearby turn, tool or previous response. An exact diagnostic stream start or a fully validated transport sequence remains usable. Persisted `item-event` provenance identifies the old parsing assumption for correction even after source logs are deleted. `tests/test_lifecycle_origin.py` verifies short/long intervals, messages/reasoning, later items, restart migration, independent timing preservation, exact revalidation, token conflicts and subsecond display precision.

### Recovering coverage without inventing timing

The pinned [inter-agent protocol conversion](https://github.com/openai/codex/blob/13ab0b254e7b9ed0ab473d38d978fa100bf417ae/codex-rs/protocol/src/protocol.rs) constructs a model input, and [session recording](https://github.com/openai/codex/blob/13ab0b254e7b9ed0ab473d38d978fa100bf417ae/codex-rs/core/src/session/mod.rs) persists its communication metadata immediately before that input item. Revision 9 recognizes this bounded pair rather than treating every `agent_message` as unknown generated output. It does not classify messages by text, goal status or inferred fork ancestry. Synthetic tests in `tests/test_input_items.py` cover adjacency, ordinals, gaps, ambiguity, encrypted input and revalidation.

SQLite deletion can leave complete records in unused storage until reuse. The [SQLite file format](https://www.sqlite.org/fileformat.html) specifies freelist chains, B-tree cells, linked freeblocks and record serial types. The optional adapter follows those structures in a consistent read-only snapshot; it rejects partial payloads, invalid geometry and unsupported schemas. Exact original diagnostic timestamps and task/turn/item scopes then pass the normal parser. A freeblock can overwrite the cell's rowid while leaving its entire record header and payload intact; the adapter neither reconstructs that rowid nor uses it as a response identity. Conflicting observations remain conflicts. Tests in `tests/test_sqlite_recovery.py` cover page sizes, WAL snapshots, source immutability, secure deletion, corruption, retries and duplicate/conflicting origins.

The origin archive stores only HMAC identities and normalized timing/retry evidence. `tests/test_evidence_archive.py` checks delayed discovery, restarts, source isolation, conflict persistence, failed writes, capacity pruning and rotation-stable recovery fingerprints. This prevents future loss of already observed evidence; it cannot recover bytes overwritten before collection. Coverage review must report actual recovered measurements and unresolved gaps, independently of whether an extreme point disappears.

The revision-1 split-usage shadow has a separate withdrawal revision of 2. A later unrelated measurement-parser revision does not make that repeated shadow new evidence against a previously validated sample. Explicit newer corrections remain possible through their own evidence and revision. The optional `scripts/restore_history.py` can restore absent independent measurements from a matching pseudonymized TokenPulse backup when the only deletion reason is a stale-usage shadow; it preserves current records and rejects identity-salt mismatches and inherited/superseded output.
