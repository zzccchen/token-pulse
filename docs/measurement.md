# Measurement contract

TokenPulse reports client-log estimates of completed output streams. It does not measure visible words per second, promise service-tier execution, or provide a controlled model benchmark.

## Match tokens and time

```text
TPS = total output tokens / (output end − output start)
```

Total output tokens can include reasoning and generated tool calls. Reasoning tokens are not added again when included in total output. Other scopes remain recorded but are excluded from speed statistics.

The adapter matches task, turn, response, and output-item identifiers when available. Explicit per-response usage takes precedence. Legacy last-usage events need a matching pending output and cumulative-counter validation. Resets, duplicate snapshots, and conflicting increments are handled explicitly.

Missing, malformed, or negative cumulative totals cannot validate legacy last-usage tokens. An immediately following explicit usage can replace the provisional legacy record only with matching task, turn, and output-token count, before any intervening record. The old identity is withdrawn so persisted history cannot count both. Equal tokens or nearby timestamps alone do not join separated records. Explicit response usage without output items remains an excluded record with its available identity and tokens.

Timing spans the first reliably identified generated item through the last output completion, including gaps within the stream. It excludes waiting before generation and tool execution after output. A usage notification may arrive after a tool finishes without extending generation time. Whole-turn time and tool-execution timestamps cannot substitute for output timing.

An `item_completed` lifecycle interval cannot independently establish the output start, even when its duration is positive. Core can synthesize a start while processing an already completed item; elapsed time then includes local work rather than generation. A session-derived measurement therefore requires an exactly matched diagnostic first-item stream notification. Matching non-tool lifecycle ends can still avoid delayed rollout write times. A later item's start cannot repair an unverified first-item origin. This rule applies at every duration and speed, without assuming that a particular unsupported interval was buffered or estimating a replacement duration.

For supported SSE/WebSocket log envelopes, the adapter validates a complete Responses sequence from `response.created` through `response.completed`. Event numbers must be contiguous; response, output-item, content-part and delta identities must agree. Exact duplicate events are ignored, while gaps, conflicting replays, unknown events and ambiguous interleaved responses exclude speed. Tokens come from final `usage.output_tokens`, never from delta counts or text length. The interval uses client log timestamps from the first item added through the last item done. It is a client-observed stream estimate, not server decode time; delayed final usage does not extend it. An empty final `output` array is acceptable only when the full event sequence establishes the item inventory. Tool-first and zero-duration initial items remain excluded.

Explicit sampling-retry warnings within the same task and turn break a session-derived interval when they fall strictly between its output boundaries (`interrupted_response`). A pending item before a retry must not be joined to later output as uninterrupted generation. The adapter preserves usage and identity, but does not estimate or subtract the wait. Retries before the output start or after its completion do not invalidate that output; remote compaction and unrelated scopes are not sampling evidence. A complete, exactly identified transport sequence can independently establish its own interval.

## When no speed is shown

Incomplete samples remain in coverage and history with an exclusion reason. Missing information is never rendered as zero TPS. A valid zero-token sample with positive matching duration can have zero TPS.

Important exclusions include:

- Missing, non-finite, non-positive, conflicting, or unmatchable boundaries.
- Lifecycle-only first-item timing without an independent matched stream origin (`unverified_lifecycle_start`). Retain usage and identity; do not treat positive lifecycle duration as proof of generation.
- Unsupported token scopes, invalid counters, or incomplete usage.
- Unknown output-item types: ignoring them could omit generation or include execution within the response. Keep the usage, but exclude the entire response instead of timing a recognized suffix (`unsupported_output_item`).
- A first item that is a function/custom tool call: its notification already names the tool and cannot establish the beginning of all counted generation (`unconfirmed_tool_start`).
- A first non-tool item whose start and completion collapse to the same millisecond, without an earlier matching diagnostic start (`unconfirmed_initial_span`). Later items cannot supply the start for the full response.

There is no arbitrary minimum duration or maximum TPS cutoff. The millisecond comparison reflects event precision, not a performance threshold. A reliably matched earlier diagnostic start can establish a complete boundary. See [source evidence](source-audit.md).

A very high positive rate alone does not prove a parsing error. Matching lifecycle and diagnostic timestamps can share the same client clock and event-delivery behavior; agreement is not independent evidence of server decode speed. Without sufficiently granular transport evidence, buffering or batched delivery cannot be confirmed or quantitatively corrected. Neither a short positive first-item interval nor the magnitude of TPS supplies a new rejection threshold.

## Model summaries

One model combines all conversations, efforts, providers, authentication modes, tiers, and accepted sources. Original evidence remains separate in records; known values do not fill unknown values in the group.

```text
Weighted average = sum(valid output tokens) / sum(valid stream durations)
```

The median, minimum, and maximum use per-sample TPS in the same selected window. For example, 100 tokens in 1 second and 100 tokens in 9 seconds give **20 TPS**, not the arithmetic mean of their speeds. This is invented arithmetic, not a benchmark.

Concurrent outputs each contribute their own duration. This average is not combined throughput over wall-clock time. Comparing different settings does not establish that a setting caused a speed change.

Windows are rolling 30-minute, 1-day, and 7-day ranges filtered by completion time. Future and out-of-range records do not contribute. The plot displays individual valid samples without jitter or invented per-second values. Record count and conversation coverage include excluded samples.

## Distinguish tier evidence

| Evidence | What it establishes |
| --- | --- |
| Configuration | Observed global setting; not necessarily a task or historical request setting |
| Historical thread snapshot | A thread-owned preference observed at a specific time; turn association requires additional evidence |
| Submitted setting | A value or instruction associated with a specific task and turn |
| Turn override | An explicit per-turn start option, separate from the persistent thread preference |
| Turn configuration tag | A sampling step's scoped client setting; not proof of submission, outbound request, or execution |
| Request value | A value explicitly observed in outbound-request evidence |
| Actual tier | Explicit `response.completed.response.service_tier` in supported transport logs; absent values remain unknown |

Channel/provider and authentication mode are independent of tier. Mode markers prefer explicit request values, then an explicit turn override, then submitted thread settings, scoped configuration tags and finally a validated thread-snapshot association. Conflicting evidence blocks weaker fallbacks. Explicit `ultrafast` maps to Ultra Fast, while `priority` and `fast` map to Fast; explicit `default` or clearing a submitted thread override maps to Standard. Ultra Fast uses an orange triangle, Fast a teal diamond, Standard a blue circle, and other/unrecorded modes a cross. All modes still contribute to the same model's window statistics. Raw tier values remain unchanged in record details, history, and CSV. Missing, unspecified, or conflicting evidence does not default to Standard. Display classification does not establish execution tier.

Submission parsing reads bounded outer fields and skips user content. Thread-setting `Some(None)` (clear), `None` (unspecified), and explicit values remain distinct. Supported `TurnStartOptions.service_tier` is stored separately: `Some("value")` is an explicit turn override, literal `None` proves no override, and an omitted field is unknown. Unsupported nonempty shapes are ambiguous. Contradictory evidence remains a conflict rather than being resolved by arrival order. A turn override never rewrites the observed thread preference.

Scoped diagnostic feedback tags are stored independently as `config_tag_tier` and `config_tag_state` when every observed task and turn scope agrees. With no explicit request or decisive submission evidence, they provide a labeled fallback such as **Fast (configuration tag)**. They never populate submitted, requested, or actual tiers. A conflicting submission blocks the fallback; an unspecified submission permits it. `unset`, missing/null tags, or conflicting configuration tags do not imply Standard. History places the mode and evidence beside model and TPS, with each raw evidence category in its own column and in CSV.

### Historical thread configuration

The adapter reads only outer `thread_settings_applied.thread_id` and `thread_settings.service_tier` fields from session metadata. The explicit owner must match the indexed thread. Missing owners and parent snapshots are not inherited, including copied fork prefixes and subagent ordinal prefixes. Neither `history_base`, root-turn lineage nor a goal trigger supplies an owner or a service tier. A missing/null/`unset` snapshot tier stays unspecified, not Standard.

Each sample stores the snapshot observation time, source, matched turn-start times and association status in `thread_config_*` fields. The timestamp records an observed applied configuration; it is not a reconstructed time of server execution. The latest snapshot preceding the exact turn start is frozen for that turn, because a mid-turn preference update can apply to subsequent turns. If no pre-turn snapshot is available, a snapshot before the first known output boundary can still be shown as thread-only evidence. Later snapshots never relabel earlier outputs. Where `task_started.started_at` provides integer Unix seconds, the beginning of that precision interval is the conservative start bound; older shapes use the task-start event timestamp. Invalid or contradictory boundaries cannot establish association.

A **thread configuration snapshot, turn matched** mode fallback requires all of the following:

- A valid, non-conflicting, thread-owned snapshot precedes the exact turn-start boundary in both event order and time, and does not follow the known output start.
- A supported submission matches that exact thread and turn and explicitly records no per-turn tier override. Omitted fields, other turns and parent/root turns do not qualify. Its observation must be no later than the task-start event.
- The submitted thread setting is unspecified or explicitly agrees with the snapshot. A clear, changed value or conflict prevents using the old snapshot.

Otherwise the snapshot remains independently visible, with the reason association was not established; it does not remove Other. An explicit turn override is shown independently and takes precedence over the thread setting. Request and server-confirmed tiers remain untouched. Missing logs cannot establish absence of an override.

The adapter bounds retained snapshots and turn starts, resets carry-forward state on unread gaps/ownership changes, and rejects duplicate JSON metadata fields. Plain and compressed recovery use the same rules. Recovery revision 12 rechecks existing completion checkpoints for snapshot association and measurement parser revision 8. Source loss or partial rereads preserve saved evidence, known override conflicts remain conflicts, and replacing timing with an earlier transport start rechecks the snapshot association.

## Identity and recovery

Snapshots, log rereads, and inherited fork history are not new output. Samples use stable task/turn/response identities and locally pseudonymized storage keys. Missing-turn records merge only when the same task and response uniquely identify a known turn.

Transport and session records join by the same task, turn and response ID, or an exact complete ordered item-ID digest linking a legacy response to an explicit one. Token counts and timestamp proximity are never association evidence. Aliases and contributing sources survive restarts and CSV export as pseudonymized identity fields and source labels. An alias with multiple owners is ambiguous; contradictory authoritative output usage is a cross-source conflict. Both retain records but exclude speed. A valid transport interval takes precedence over session timing at the same or newer parser revision as a whole interval, without combining endpoints from different clocks or sources.

Evidence enrichment preserves identity and valid TPS. Missing logs alone cannot invalidate a saved complete measurement. Newer parsers may correct proven incorrect boundaries or withdraw inherited/duplicate records, preserving the reason. Same-version legacy withdrawal notifications must not remove a sample that the parser already validated.

Parser revision 8 withdraws saved pre-revision-8 valid session measurements explicitly marked `item-event`: this stored provenance proves use of the now-invalid lifecycle-origin assumption. The history migration does not require surviving logs or a live task entry. It preserves sample identity, tokens, observed timestamps, aliases and tier evidence while removing TPS, and old parser replays cannot restore that assumption. Retained lifecycle timestamps are unverified observations, not valid stream seconds; the exclusion reason and timing source remain explicit. Already saved independent diagnostic/transport timing survives source loss. A matching independent interval can revalidate the same record; repeated withdrawal notifications cannot erase it.

Copied prefixes with explicit subagent ordinal boundaries are attributed to inherited history; referenced `history_base` files are not followed or counted again.

Recovery completion records are committed only after corresponding samples have been saved. File changes, evidence changes, or a recovery revision invalidate reuse. Interrupted files and partial lines are not marked complete. These rules prevent live and historical collection from counting the same output twice.

Transport cursors record only quiescent boundaries with no active response. They contain a source HMAC, row cursor, row/physical-file HMACs and parser revision. Row replacement, truncation, rotation and parser changes trigger bounded replay. EOF with an unfinished response retains an excluded record and does not advance the safe checkpoint; later events can repair that same record. No raw event bodies, text, arguments or in-flight parser state are persisted.

Retained retry evidence also checks saved session intervals by exact HMAC task/turn identity, independently of live task and response-cache limits. Proven interrupted intervals lose TPS with a newer parser revision; their identities and usage remain. Re-reading the same version without the retry logs cannot restore those intervals. No historical samples are discarded merely for being slow or fast.
