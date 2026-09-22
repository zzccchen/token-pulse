# TokenPulse

A local, read-only Codex output-speed monitor under construction.

## Available in this stage

- define output evidence and weighted model statistics.
- read bounded JSONL streams with recovery.
- extract scoped submission and diagnostic evidence.
- correlate rollout output with usage and timing.
- persist pseudonymized history and export safe CSV.
- recover historical sessions in bounded batches.
- discover local tasks and coordinate collection.
- publish background snapshots from local observations.
- add offline localization and persistent preferences.
- aggregate rolling model timelines and synthetic demos.

## Development

```sh
uv sync --locked
uv build
uv run pytest
```

Use synthetic data. Measurement needs matching output tokens and stream boundaries; missing evidence stays unknown.

[MIT](LICENSE). This staged history was reconstructed from the pre-publication implementation; it does not reproduce the original development timestamps.
