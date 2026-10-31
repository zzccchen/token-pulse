# User guide

[English overview](../README.md) · [中文概览](../README.zh-CN.md)

## Start and stop

Run `uv run token-pulse` from an installed checkout. It reads existing local Codex sessions without changing their configuration and needs no separate API key.

The panel opens by default. Closing it leaves the app running if a tray is available. Use the tray menu to quit or reopen it. With `--no-tray`, closing the last window exits. `--hidden` starts in the tray and falls back to a window when necessary.

Use `uv run token-pulse --demo` for isolated synthetic observations and temporary app storage. It does not read your Codex sessions. Close an existing instance before launching another against the same real data directory.

## Read the panel

Choose a model and a rolling **30-minute**, **1-day**, or **7-day** window. Model choices use the same window. Your selection is retained while other models become active; an out-of-range selection shows no records instead of silently switching.

The large number is a duration-weighted average of valid completed outputs. The median and minimum–maximum use valid samples in the selected window. Conversation coverage includes incomplete observations; the measurable count and exclusion reasons are separate.

Each point is one completed output. Hover for time, mode, TPS, tokens, and duration. Click to inspect its anonymous record. Left/Right moves between points, Home/End selects the first/last, Enter opens a record, and Escape clears highlighting. Keyboard navigation reaches overlapping points.

Ultra Fast, Fast, Standard, and other/unrecorded markers use distinct shapes and colors. `ultrafast` appears as an orange triangle, separate from the teal Fast diamond; all modes remain combined in the selected model's statistics. The legend wraps on narrow charts. Request evidence takes precedence over submitted settings. With neither available, a scoped configuration tag may supply a fallback explicitly labeled **configuration tag** in the hover card and history. The history table places mode/evidence next to model and TPS; raw configuration, submission, request, and confirmation stay separate. Missing, unset, or conflicting tags do not imply Standard. Labels do not establish server execution tier. See [measurement](measurement.md).

Recent state uses evidence no older than 120 seconds. Older observations do not prove a task is still generating, idle, or failing. The latest output becomes stale after 60 seconds, independently of state; the selected-window average remains available.

## History and export

History allows filtering and inspecting saved records, including raw model, effort, channel, and tier evidence. CSV preserves original fields and machine-readable codes regardless of interface language. Identifiers are locally pseudonymized; spreadsheet formula prefixes are escaped.

History retains at most 30 days and 10,000 samples. Background recovery scans accessible indexed sessions updated within seven days, including archived ones. This takes multiple batches. A completed scan does not prove the original logs are complete.

## Settings and language

Settings controls the source directory, appearance, reduced transparency/motion, optional tray speed text, and language. Language previews immediately; Save retains it and Cancel restores the previous display language.

System language selects Simplified Chinese for Chinese locales and English otherwise. Override it for a single run:

```sh
uv run token-pulse --language en
uv run token-pulse --language zh_CN
uv run token-pulse --language system
```

Raw evidence and CSV data are not translated. Timestamps use local time; native file-dialog buttons follow the OS language.

The tray number uses the selected model and time window. It remains visible while that range contains a valid average; it is not live speed. Hover for the range and latest valid sample time.

## Data and privacy

| Data | Location or behavior |
| --- | --- |
| Codex source | `--codex-home`, saved source setting, or `CODEX_HOME` / `~/.codex` default |
| Windows app data | `%LOCALAPPDATA%/TokenPulse` |
| Linux app data | `$XDG_DATA_HOME/token-pulse`, default `~/.local/share/token-pulse` |
| Alternate app data | Set `--data-dir` |

TokenPulse reads session JSONL, selected state/diagnostic SQLite tables, and the global service-tier setting in `config.toml`. It does not read credential files, change Codex settings, send model requests, or upload telemetry.

History stores necessary statistical fields and locally keyed HMAC identifiers instead of raw task, turn, and response IDs. A separate bounded evidence archive stores normalized stream origins and retry timestamps with HMAC source/task/turn/item identities, so later recovery can survive diagnostic rotation. Optional recovery inspects intact deleted SQLite records in memory; raw snapshots and log bodies are never saved. Completed-file records store HMAC identities and digests, not paths or parsing content. Titles can be read into memory but are not written to history or CSV. Preferences retain your selected source path. Exported evidence can reveal model/provider usage; review it before sharing. Pseudonymization is not a promise that usage patterns cannot identify you.

## Troubleshooting

If an earlier upgrade removed independently validated measurements through repeated legacy usage withdrawals, a matching TokenPulse history backup can restore them. Close TokenPulse and back up its current history first. Preview with `uv run python scripts/restore_history.py --backup <backup.sqlite> --history <history.sqlite>`; add `--apply` to restore eligible records. The tool requires the same identity salt and restores only absent, independently timed records with a legacy stale-usage rejection. It does not replace current records, resurrect inherited/superseded output, recover lifecycle-only rates, or read Codex credentials/log content. Preview works on a temporary copy. Without a matching backup, the deleted statistical record cannot be reconstructed by this tool.

| Symptom | What to check |
| --- | --- |
| No sessions | Confirm the source directory and that a local session has produced output. Remote hosts are not collected. |
| Model exists, but no TPS | Inspect the exclusion reason. Missing boundaries, unsupported formats, and tool-first outputs can prevent measurement. |
| Incomplete older history | Wait for recovery. Deleted logs, index omissions, retention limits, or missing starts leave gaps. |
| Unknown Fast / actual tier | Settings and requests are different evidence. Actual tier requires an explicit value in a validated transport completion; other records may remain unknown. |
| No Linux tray | Try `--no-tray`; availability depends on the desktop and extensions. |
| Qt fails to load on Linux | Check platform-plugin errors and [system libraries](compatibility.md). |

For a read-only diagnostic summary:

```sh
uv run token-pulse-cli --language en --diagnose
```

This performs bounded collection and reports the last poll, not a full seven-day audit. It excludes titles and paths. Review output before sharing, and include OS, desktop, versions, and reproduction steps in a [bug report](https://github.com/zzccchen/token-pulse/issues/new?template=bug_report.yml). Do not upload raw logs.
