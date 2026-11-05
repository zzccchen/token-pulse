"""Audit a fixed pseudonymized exclusion set against surviving local evidence.

Only aggregate classifications are written. Raw identities and parsed source
records remain in memory, and no source or reference database is modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import math
import sqlite3
import time
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from statistics import median

from token_pulse import sqlite_recovery
from token_pulse.backfill import RECOVERY_REVISION
from token_pulse.collector import Collector, codex_home
from token_pulse.domain import reconcile, resolve_response_alias
from token_pulse.evidence_archive import EvidenceArchive
from token_pulse.history import History, csv_cell
from token_pulse.history_repair import restore_independent
from token_pulse.logs import LogEvidence, SqliteLogs, database_candidates, read_only
from token_pulse.rollout import PARSER_REVISION, REJECTIONS, Rollout
from token_pulse.tailer import open_tail


def inspect_response(response, evidence, task):
    timing = response.timings[0] if response.timings else None
    item = response.items[0][0] if response.items else None
    start = evidence.start(task, item, response.turn_id) if item else None
    activity = evidence.activity(task, response.turn_id, item) if item else None
    return {
        "first_kind": response.first_kind or "none",
        "items": len(response.items),
        "first_lifecycle": "missing"
        if timing is None
        else "ambiguous"
        if timing.ambiguous
        else "collapsed"
        if timing.start == timing.end
        else "positive",
        "first_origin": "missing"
        if start is None
        else "ambiguous"
        if start.ambiguous
        else "turn_mismatch"
        if start.turn_id != response.turn_id
        else "matched",
        "first_activity": "missing"
        if activity is None
        else "ambiguous"
        if activity.ambiguous
        else "ordered_pair"
        if activity.completions
        else "notification_only",
        "activity_inside_lifecycle": bool(
            timing and activity and any(timing.start < at < timing.end for at in activity.times)
        ),
        "activity_at_lifecycle_start": bool(timing and activity and timing.start in activity.times),
        "activity_before_lifecycle": bool(
            timing and activity and any(at < timing.start for at in activity.times)
        ),
    }


def lifecycle_arithmetic(samples):
    """Sensitivity analysis only; these rates never enter validated statistics."""
    rates = sorted(
        s.output_tokens / (s.ended_at - s.started_at)
        for s in samples
        if s.issue == "unverified_lifecycle_start"
        and s.token_scope == "total_output"
        and type(s.output_tokens) is int
        and s.output_tokens >= 0
        and s.started_at is not None
        and math.isfinite(s.started_at)
        and math.isfinite(s.ended_at)
        and s.ended_at > s.started_at
    )
    return {
        "label": "Unverified tokens per lifecycle second; not completed-output TPS",
        "records": len(rates),
        "promoted_to_valid": 0,
        "minimum": rates[0] if rates else None,
        "median": median(rates) if rates else None,
        "p95": rates[min(len(rates) - 1, math.ceil(len(rates) * 0.95) - 1)] if rates else None,
        "maximum": rates[-1] if rates else None,
    }


def write_lifecycle_reference(path, samples):
    """Export labeled arithmetic separately, leaving the validated sample unchanged."""
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "task_hmac",
                "turn_hmac",
                "response_hmac",
                "completed_at_unix",
                "output_tokens",
                "lifecycle_seconds",
                "unverified_tokens_per_lifecycle_second",
                "assessment",
            ]
        )
        for sample in samples:
            if lifecycle_arithmetic([sample])["records"]:
                seconds = sample.ended_at - sample.started_at
                writer.writerow(
                    [
                        csv_cell(value)
                        for value in [
                            sample.task_id,
                            sample.turn_id,
                            sample.response_id,
                            sample.ended_at,
                            sample.output_tokens,
                            seconds,
                            sample.output_tokens / seconds,
                            "unverified_lifecycle_not_output_tps",
                        ]
                    ]
                )


def audit(
    reference: Path,
    baseline: Path,
    output: Path,
    root: Path,
    *,
    repaired_output: Path | None = None,
):
    root, output = root.resolve(), output.resolve()
    if output.suffix != ".json" or output.is_relative_to(root):
        raise ValueError("Report must be a JSON file outside the source directory")
    if repaired_output is not None:
        repaired_output = repaired_output.resolve()
        if repaired_output.is_relative_to(root) or repaired_output.exists():
            raise ValueError("Repaired output must be new and outside the source directory")
    report = json.loads((reference.parent / "report.json").read_text())
    end = datetime.fromisoformat(report["window_end_utc"]).timestamp()
    memory = sqlite3.connect(":memory:")
    with closing(read_only(reference)) as db:
        db.backup(memory)
    salt = bytes.fromhex(
        memory.execute("SELECT value FROM settings WHERE key='salt'").fetchone()[0]
    )

    def anon(kind, raw):
        return hmac.new(salt, f"{kind}:{raw}".encode(), hashlib.sha256).hexdigest()[:24]

    targets = {
        s.key: s
        for r in memory.execute("SELECT payload FROM samples WHERE valid=0")
        if (s := History._decode(r[0]))
    }
    aliases = defaultdict(set)
    for task, turn, alias, canonical in memory.execute(
        "SELECT task,turn,alias,canonical FROM response_aliases"
    ):
        aliases[task, turn, alias].add(canonical)
    archive = EvidenceArchive(memory, anon, root)
    evidence = LogEvidence()
    archive.restore(evidence, now=end)
    before_origins = memory.execute("SELECT count(*) FROM stream_origins").fetchone()[0]
    stores = []
    # This explicitly bounded audit also checks the secondary diagnostic store;
    # the production reader currently recovers only its selected primary store.
    old_limit = sqlite_recovery.MAX_SNAPSHOT
    try:
        sqlite_recovery.MAX_SNAPSHOT = 256 * 1024 * 1024
        for path in dict.fromkeys(
            [*database_candidates(root, "logs"), *database_candidates(root, "state")]
        ):
            with closing(read_only(path)) as db:
                columns = {r[1] for r in db.execute("PRAGMA table_info(logs)")}
            if not {"id", "ts", "ts_nanos", "thread_id", "target", "feedback_log_body"} <= columns:
                continue
            reader = SqliteLogs(path, evidence)
            issue = None
            for _ in range(1000):
                issue = reader.poll(end)
                archive.save(evidence, now=end)
                if issue or not reader.pending:
                    break
            stores.append({"issue": issue, "pending": reader.pending})
    finally:
        sqlite_recovery.MAX_SNAPSHOT = old_limit
    after_origins = memory.execute("SELECT count(*) FROM stream_origins").fetchone()[0]
    collector = Collector(root)
    collector._discover(end)
    # Inventory structurally recognized notifications even when host association
    # cannot be proven. These are candidates only, never timing evidence.
    collector.desktop_logs.discover(end)
    raw_activity = set()
    raw_completion = set()
    desktop_gaps = 0
    for reader in collector.desktop_logs.files.values():
        while True:
            batch = reader.tail.read()
            desktop_gaps += len(batch.gaps) + bool(batch.error)
            for record in batch.records:
                if record["kind"] not in {"activity", "completed"}:
                    continue
                if not end - 604800 <= record["at"] <= end + 5:
                    continue
                key = tuple(
                    anon(kind, record[field])
                    for kind, field in (("task", "task"), ("turn", "turn"), ("item", "item"))
                )
                (raw_activity if record["kind"] == "activity" else raw_completion).add(key)
            if batch.error or batch.eof:
                break
    metadata = {}
    recovered = {}
    bytes_read = 0
    completed = 0
    diagnostics = Counter()
    started = time.monotonic()
    for source in collector.backfill.queue:
        tail = open_tail(source.path, backfill=None)
        parser = Rollout(source.task, context=source.context)

        def drain(parser, task):
            responses = [*parser.responses, *parser.corrections]
            for response, sample in zip(responses, parser.samples(evidence), strict=True):
                if sample.issue in REJECTIONS:
                    continue
                safe = replace(
                    sample,
                    task_id=anon("task", sample.task_id),
                    turn_id=anon("turn", sample.turn_id),
                    response_id=anon("response", sample.response_id),
                    response_aliases=tuple(anon("response", a) for a in sample.response_aliases),
                )
                safe = resolve_response_alias(safe, aliases)
                if safe.key not in targets:
                    continue
                previous = recovered.get(safe.key)
                recovered[safe.key] = reconcile(previous, safe) if previous else safe
                metadata[safe.key] = inspect_response(response, evidence, task)
                first_key = (
                    (safe.task_id, safe.turn_id, anon("item", response.items[0][0]))
                    if response.items
                    else None
                )
                metadata[safe.key]["raw_first_activity_seen"] = first_key in raw_activity
                metadata[safe.key]["raw_first_completion_seen"] = first_key in raw_completion
            parser.responses.clear()
            parser.corrections.clear()

        while True:
            batch = tail.read()
            bytes_read += batch.bytes_read
            if batch.error:
                diagnostics[batch.error] += 1
                break
            if batch.reset:
                parser = Rollout(source.task, context=source.context)
            gaps = set(batch.gaps)
            for i, record in enumerate(batch.records):
                if i in gaps:
                    parser.damage("record_gap")
                parser.feed(record)
                if len(parser.responses) >= 128 or len(parser.corrections) >= 128:
                    drain(parser, source.task)
            if len(batch.records) in gaps:
                parser.damage("record_gap")
            drain(parser, source.task)
            if batch.eof:
                completed += int(not tail.pending and not tail.dropping)
                break
        if completed % 20 == 0:
            print(
                json.dumps(
                    {
                        "parsed_files": completed,
                        "matched_exclusions": len(metadata),
                        "elapsed_seconds": round(time.monotonic() - started, 1),
                    }
                ),
                flush=True,
            )
    groups = {}
    for reason in sorted({s.exclusion for s in targets.values()}):
        keys = [key for key, value in targets.items() if value.exclusion == reason]
        groups[reason] = {
            "records": len(keys),
            "reparsed": sum(k in metadata for k in keys),
            "newly_measurable_from_extended_sources": sum(
                k in recovered and recovered[k].tps is not None for k in keys
            ),
            "metadata": {
                field: dict(Counter(str(metadata[k][field]) for k in keys if k in metadata))
                for field in (
                    "first_kind",
                    "first_lifecycle",
                    "first_origin",
                    "first_activity",
                    "activity_inside_lifecycle",
                    "activity_at_lifecycle_start",
                    "activity_before_lifecycle",
                    "raw_first_activity_seen",
                    "raw_first_completion_seen",
                )
            },
        }
    result = {
        "parser_revision": PARSER_REVISION,
        "recovery_revision": RECOVERY_REVISION,
        "window_start_utc": report["window_start_utc"],
        "window_end_utc": report["window_end_utc"],
        "fixed_exclusions": len(targets),
        "reparsed_exclusions": len(metadata),
        "completed_files": completed,
        "bytes_read": bytes_read,
        "diagnostics": dict(diagnostics),
        "extended_stores": stores,
        "additional_archived_origins": after_origins - before_origins,
        "desktop_inventory": {
            "files": len(collector.desktop_logs.files),
            "gaps": desktop_gaps,
            "distinct_activity_items": len(raw_activity),
            "distinct_completed_items": len(raw_completion),
        },
        "groups": groups,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    newly_measured = {
        key: value
        for key, value in recovered.items()
        if value.tps is not None
        and value.output_tokens == targets[key].output_tokens
        and value.token_scope == targets[key].token_scope
        and value.ended_at == targets[key].ended_at
    }
    if repaired_output is not None:
        repaired_output.mkdir(parents=True, exist_ok=False)
        destination = repaired_output / "history.sqlite"
        with closing(sqlite3.connect(destination)) as db:
            memory.backup(db)
        history = History(destination)
        try:
            before = history.read(now=end)
            with history.connection:
                history.connection.executemany(
                    "UPDATE samples SET payload=?,valid=1 WHERE key=?",
                    [
                        (json.dumps(asdict(value), allow_nan=False), "|".join(key))
                        for key, value in newly_measured.items()
                    ],
                )
            restored = restore_independent(history, baseline, now=end, apply=True)
            after = history.read(now=end)
            result["repair"] = {
                "newly_revalidated": len(newly_measured),
                "restored_saved_independent": restored,
                "records_before": len(before),
                "records_after": len(after),
                "valid_before": sum(s.tps is not None for s in before),
                "valid_after": sum(s.tps is not None for s in after),
                "remaining_exclusions": dict(Counter(s.exclusion for s in after if s.exclusion)),
                "integrity_check": history.connection.execute("PRAGMA integrity_check").fetchone()[
                    0
                ],
            }
            result["relaxed_lifecycle_arithmetic"] = lifecycle_arithmetic(after)
            write_lifecycle_reference(repaired_output / "unverified-lifecycle.csv", after)
        finally:
            history.close()
        reopened = History(destination)
        try:
            result["repair"]["restart_matches"] = reopened.read(now=end) == after
        finally:
            reopened.close()
    else:
        result["newly_revalidated"] = len(newly_measured)
        result["relaxed_lifecycle_arithmetic"] = lifecycle_arithmetic(targets.values())
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    memory.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, default=codex_home())
    parser.add_argument(
        "--repaired-output", type=Path, help="New isolated directory for exact repairs"
    )
    args = parser.parse_args()
    print(
        json.dumps(
            audit(
                args.reference,
                args.baseline,
                args.output,
                args.codex_home,
                repaired_output=args.repaired_output,
            ),
            indent=2,
        )
    )
