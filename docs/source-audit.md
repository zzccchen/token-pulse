# Codex source evidence

This is a version-scoped adapter reference, not a promise about future Codex formats. The project was developed against observed 0.153.4-series local logs. Earlier source review used the public `rust-v0.153.0` implementation. A session's creation version need not match every later appended event.

## Source relationships

| Source | Evidence | Interpretation boundary |
| --- | --- | --- |
| State SQLite `threads` | Session index, paths, current settings | Current model/settings cannot fill historical turns |
| JSONL `turn_context` | Turn model and reasoning effort | Parse the outer structure, not user text |
| `token_usage_record.usage` | Explicit per-response usage and identifiers | Turn/thread aggregates are different scopes |
| `token_count` | Last usage and cumulative snapshots | An unchanged cumulative snapshot is not a new response |
| `item_completed` | Item start/end millisecond timestamps | Non-tool timing may supplement logs; tool timing describes execution |
| Thread-history SQLite | Derived turn/item projections | Not another independent source of samples |
| Diagnostic SQLite | Output starts, submission/request/auth evidence | Retention and association limit availability |
| Desktop logs | UI and transport diagnostics | Events without item IDs cannot establish precise boundaries |

Historical upstream reference: [protocol definitions](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/protocol/src/protocol.rs) and [thread-history projection](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/app-server-protocol/src/protocol/thread_history_projection.rs). Tests in this repository are the executable contract for supported input shapes.

## Timing and identity pitfalls

- Usage can follow tool results. Preserve the matching model outputs and end timing at output completion, not usage arrival.
- Completion events may carry original start/end times. Non-tool positive-duration boundaries can supplement a matching diagnostic start; contradictory evidence still invalidates timing.
- Tool-first notifications already identify the tool. They cannot establish the full generation interval for total response tokens, even if argument streaming continues afterward.
- Same-millisecond first-item starts/completions can be lifecycle placeholders. Later output cannot repair that initial boundary without earlier matching evidence.
- Forks can copy ancestor history with new outer write timestamps. Detect ownership boundaries rather than counting copied output as newly generated.
- Repeated cumulative snapshots cannot prematurely close pending output using old last-usage values.
- Diagnostic retention is task-dependent. Old dates elsewhere in the database do not prove a particular task's starts remain available.

Historical references: [turn processing](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core/src/session/turn.rs), [session lifecycle](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/core/src/session/mod.rs), and [rollout recorder](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/rollout/src/recorder.rs). These references explain the original investigation; do not generalize a version's behavior into an unconditional adapter rule.

## Submitted-tier association

The adapter recognizes bounded outer `TurnInput.request.thread_settings.service_tier` evidence for supported Start / StartOrSteer submissions. Thread identity and submission/turn identity must match. Standalone settings changes and agent messages do not automatically apply to an unrelated output.

The parser distinguishes clear, unspecified, explicit, and conflicting states. It handles nested delimiters and escaped strings while skipping user text. Truncation, duplicate fields, excessive depth, conflicting records, and unsupported nonempty overrides are not guessed away. See `tests/test_submissions.py` and `tests/test_logs.py`.

A display marker prefers request evidence, otherwise supported submitted settings. Source attribution and raw values remain in record details. The current adapter does not extract final server-confirmed tiers. Global configuration cannot fill historical request values.

## Parser corrections

| Revision | Correction preserved in recovery |
| --- | --- |
| 2 | Complete response measurements can replace legacy split ranges; proven fork/snapshot duplicates can be withdrawn |
| 3 | Tool-first measurements with unconfirmed full starts lose TPS while retaining identity and tokens |
| 4 | Same-millisecond first-item placeholders without earlier matched starts are excluded |

New corrections must update parser/recovery versions as appropriate. Mere log absence cannot erase an existing valid measurement. Old parser writes must not restore proven invalid timing; repeated same-version withdrawals cannot remove already revalidated samples.

Regression fixtures cover delayed usage, duplicate snapshots, resets/conflicts, fork boundaries, millisecond placeholders, earlier valid starts, cross-restart withdrawals, missing-turn reconciliation, and evidence enrichment. See `tests/test_rollout.py`, `tests/test_history.py`, and `tests/test_backfill.py`. Public fixtures are synthetic; private development logs are not part of the repository.
