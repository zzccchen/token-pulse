# Measurement contract

TokenPulse reports client-log estimates of completed output streams. It does not measure visible words per second, promise service-tier execution, or provide a controlled model benchmark.

## Match tokens and time

```text
TPS = total output tokens / (output end − output start)
```

Total output tokens can include reasoning and generated tool calls. Reasoning tokens are not added again when included in total output. Other scopes remain recorded but are excluded from speed statistics.

The adapter matches task, turn, response, and output-item identifiers when available. Explicit per-response usage takes precedence. Legacy last-usage events need a matching pending output and cumulative-counter validation. Resets, duplicate snapshots, and conflicting increments are handled explicitly.

Timing spans the first reliably identified generated item through the last output completion, including gaps within the stream. It excludes waiting before generation and tool execution after output. A usage notification may arrive after a tool finishes without extending generation time. Whole-turn time and tool-execution timestamps cannot substitute for output timing.

## When no speed is shown

Incomplete samples remain in coverage and history with an exclusion reason. Missing information is never rendered as zero TPS. A valid zero-token sample with positive matching duration can have zero TPS.

Important exclusions include:

- Missing, non-finite, non-positive, conflicting, or unmatchable boundaries.
- Unsupported token scopes, invalid counters, or incomplete usage.
- A first item that is a function/custom tool call: its notification already names the tool and cannot establish the beginning of all counted generation (`unconfirmed_tool_start`).
- A first non-tool item whose start and completion collapse to the same millisecond, without an earlier matching diagnostic start (`unconfirmed_initial_span`). Later items cannot supply the start for the full response.

There is no arbitrary minimum duration or maximum TPS cutoff. The millisecond comparison reflects event precision, not a performance threshold. A reliably matched earlier diagnostic start can establish a complete boundary. See [source evidence](source-audit.md).

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
| Submitted setting | A value or instruction associated with a specific task and turn |
| Request value | A value explicitly observed in outbound-request evidence |
| Actual tier | Explicit server confirmation; not extracted by the current adapter |

Channel/provider and authentication mode are independent of tier. Fast markers prefer explicit request values, then submitted settings. Submitted `priority`/`fast` maps to Fast; explicit `default` or clearing an override maps to Standard. Missing, unspecified, or conflicting evidence does not default to Standard. Display classification does not establish execution tier.

Submission parsing reads bounded outer fields and skips user content. `Some(None)` (clear), `None` (unspecified), and explicit values remain distinct. Contradictory evidence remains a conflict rather than being resolved by arrival order. An unsupported second override position is not assigned a guessed precedence.

## Identity and recovery

Snapshots, log rereads, and inherited fork history are not new output. Samples use stable task/turn/response identities and locally pseudonymized storage keys. Missing-turn records merge only when the same task and response uniquely identify a known turn.

Evidence enrichment preserves identity and valid TPS. Missing logs alone cannot invalidate a saved complete measurement. Newer parsers may correct proven incorrect boundaries or withdraw inherited/duplicate records, preserving the reason. Same-version legacy withdrawal notifications must not remove a sample that the parser already validated.

Recovery completion records are committed only after corresponding samples have been saved. File changes, evidence changes, or a recovery revision invalidate reuse. Interrupted files and partial lines are not marked complete. These rules prevent live and historical collection from counting the same output twice.
