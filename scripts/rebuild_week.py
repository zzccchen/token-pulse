"""Rebuild a fixed seven-day window without changing an existing monitor database.

Only pseudonymized statistics/evidence and aggregate reports are written. A
baseline supplies the identity salt and comparison rows, never measurements for
the reconstruction. Archived evidence is optional and reported separately.
Source content stays in memory.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from token_pulse.backfill import RECOVERY_REVISION
from token_pulse.collector import Collector, codex_home, resolved_path
from token_pulse.evidence_archive import EvidenceArchive
from token_pulse.history import History
from token_pulse.logs import read_only
from token_pulse.retention import HISTORY_SECONDS
from token_pulse.rollout import PARSER_REVISION


def summary(samples):
    valid = [s for s in samples if s.tps is not None]
    inferred = [s for s in samples if s.tps is None and s.display_tps is not None]
    return {
        "records": len(samples),
        "conversations": len({s.task_id for s in samples}),
        "measurable": len(valid),
        "inferred": len(inferred),
        "rate_coverage": (len(valid) + len(inferred)) / len(samples) if samples else None,
        "unavailable": len(samples) - len(valid) - len(inferred),
        "excluded": len(samples) - len(valid),
        "exclusions": dict(sorted(Counter(s.exclusion for s in samples if s.exclusion).items())),
        "timing_sources": dict(
            sorted(Counter(s.timing_source or "unknown" for s in valid).items())
        ),
        "parser_revisions": dict(sorted(Counter(s.parser_revision for s in samples).items())),
        "oldest_completion": min((s.ended_at for s in samples), default=None),
        "latest_completion": max((s.ended_at for s in samples), default=None),
    }


def compare(before, after):
    old = {s.key: s for s in before}
    new = {s.key: s for s in after}
    common = old.keys() & new.keys()
    return {
        "matched_identities": len(common),
        "new_identities": len(new.keys() - old.keys()),
        "baseline_identities_not_rebuilt": len(old.keys() - new.keys()),
        "matched_became_measurable": sum(
            old[k].tps is None and new[k].tps is not None for k in common
        ),
        "matched_became_excluded": sum(
            old[k].tps is not None and new[k].tps is None for k in common
        ),
        "matched_remained_measurable": sum(
            old[k].tps is not None and new[k].tps is not None for k in common
        ),
        "new_measurable_identities": sum(new[k].tps is not None for k in new.keys() - old.keys()),
        "baseline_measurable_not_rebuilt": sum(
            old[k].tps is not None for k in old.keys() - new.keys()
        ),
    }


def persist(history, archive, collector, snapshot):
    archive.save(collector.evidence, now=snapshot.at)
    history.reject(list(snapshot.rejected_samples), now=snapshot.at)
    history.append(list(snapshot.samples), now=snapshot.at)
    for reader in (collector.backfill, collector.desktop_logs):
        if reader.completed:
            history.save_checkpoints(reader.completed, now=snapshot.at)
            reader.checkpoints.update((key, value[0]) for key, value in reader.completed.items())
            reader.completed.clear()
    if collector.transport_completed:
        history.save_transport_checkpoints(collector.transport_completed, now=snapshot.at)
        collector.transport_completed.clear()


def import_evidence(archive, baseline, *, end):
    """Copy known pseudonymized evidence columns, with exact source scope.

    No sample or checkpoint is imported: every output is reparsed. Retry
    evidence and its retention floor travel with origins for safety.
    """
    imported = {}
    tables = (
        ("stream_origins", "at"),
        ("sampling_interruptions", "at"),
        ("stream_activity", "last_at"),
        ("evidence_retention_floor", "at"),
    )
    with closing(read_only(baseline)) as source, archive.db:
        source.execute("BEGIN")
        for table, clock in tables:
            columns = [row[1] for row in archive.db.execute(f"PRAGMA table_info({table})")]
            available = {row[1] for row in source.execute(f"PRAGMA table_info({table})")}
            if not set(columns) - {"completions"} <= available:
                continue
            columns = [column for column in columns if column in available]
            projection = ",".join(columns)
            if table == "evidence_retention_floor":
                rows = source.execute(
                    f"SELECT {projection} FROM {table} WHERE at<=?", (end,)
                ).fetchall()
            else:
                rows = source.execute(
                    f"SELECT {projection} FROM {table} WHERE scope=? AND {clock} BETWEEN ? AND ? "
                    f"ORDER BY {clock} DESC LIMIT ?",
                    (archive.scope, end - HISTORY_SECONDS, end, archive.capacity),
                ).fetchall()
            archive.db.executemany(
                f"INSERT INTO {table} ({projection}) VALUES ({','.join('?' for _ in columns)})",
                rows,
            )
            imported[table] = len(rows)
    from token_pulse.activity_archive import ActivityArchive

    archive.activity_archive = ActivityArchive(
        archive.db,
        archive.anon,
        archive.scope,
        retention_days=archive.retention_days,
        capacity=archive.capacity,
    )
    return imported


def rebuild(
    root: Path,
    output: Path,
    *,
    end: float,
    baseline: Path | None = None,
    max_seconds: float = 1800,
    restore_evidence: bool = False,
) -> dict:
    root, output = resolved_path(root), resolved_path(output)
    if output.is_relative_to(root):
        raise ValueError("Output must be outside the read-only source directory")
    if restore_evidence and baseline is None:
        raise ValueError("Archived evidence requires a baseline with matching identities")
    # Refuse to open an existing destination, including the running monitor store.
    output.mkdir(parents=True, exist_ok=False)
    before = []
    salt = None
    if baseline is not None:
        with closing(read_only(baseline)) as db:
            salt = db.execute("SELECT value FROM settings WHERE key='salt'").fetchone()[0]
            before = [
                History._decode(row[0])
                for row in db.execute(
                    "SELECT payload FROM samples WHERE ended_at BETWEEN ? AND ?",
                    (end - HISTORY_SECONDS, end),
                )
            ]
    history = History(output / "history.sqlite")
    started = time.monotonic()
    diagnostics = set()
    batches = 0
    complete = False
    try:
        if salt is not None:
            history.salt = bytes.fromhex(salt)
            with history.connection:
                history.connection.execute("UPDATE settings SET value=? WHERE key='salt'", (salt,))
            history.empty_turn = history.anonymous_id("turn", "")
        collector = Collector(root)
        collector.backfill.checkpoint_key = history.source_key
        collector.desktop_logs.checkpoint_key = history.source_key
        collector.transport_key = partial(history.source_key, "transport")
        collector.transport_digest = partial(history.anonymous_id, "transport-cursor")
        archive = EvidenceArchive(history.connection, history.anonymous_id, root)
        imported = import_evidence(archive, baseline, end=end) if restore_evidence else {}
        archive.restore(collector.evidence, now=end)
        budget = collector.backfill.budget
        collector.backfill.budget = 0
        phase = "evidence"
        sources = 0
        next_progress = started
        rescan = 0
        while time.monotonic() - started < max_seconds:
            snapshot = collector.poll(end)
            batches += 1
            sources = max(sources, len(collector.backfill.queued | collector.backfill.done.keys()))
            diagnostics.update(snapshot.diagnostics)
            persist(history, archive, collector, snapshot)
            logs_pending = collector.logs is not None and collector.logs.pending
            evidence_pending = (
                logs_pending or snapshot.activity_pending or snapshot.transport_pending
            )
            if phase == "evidence" and not evidence_pending:
                collector.backfill.budget = budget
                phase = "sessions"
            elif phase == "sessions" and not evidence_pending and not snapshot.backfill_pending:
                if len(collector.backfill.done) == sources:
                    complete = True
                    break
                if rescan >= 3:
                    break  # Partial/unavailable files are never acknowledged as complete.
                collector.next_discovery = collector.next_backfill_discovery = 0
                rescan += 1
            if time.monotonic() >= next_progress:
                print(
                    json.dumps(
                        {
                            "phase": phase,
                            "batches": batches,
                            "completed_files": len(collector.backfill.done),
                            "pending_files": snapshot.backfill_pending,
                            "records": history.connection.execute(
                                "SELECT count(*) FROM samples"
                            ).fetchone()[0],
                            "elapsed_seconds": round(time.monotonic() - started, 1),
                        }
                    ),
                    flush=True,
                )
                next_progress = time.monotonic() + 15
        # Match the monitor's final evidence enrichment without repeatedly decoding
        # the entire database during each recovery batch.
        evidence = collector.evidence
        samples = history.enrich_interruptions(
            history.enrich_config_tags(
                history.enrich_submissions(history.read(now=end), evidence.submissions),
                evidence.config_tags,
            ),
            evidence.interruptions,
            archived=evidence.archived_interruptions,
        )
        integrity = history.connection.execute("PRAGMA integrity_check").fetchone()[0]
        complete = complete and integrity == "ok" and "source_unavailable" not in diagnostics
        report = {
            "window_start_utc": datetime.fromtimestamp(end - HISTORY_SECONDS, UTC).isoformat(),
            "window_end_utc": datetime.fromtimestamp(end, UTC).isoformat(),
            "parser_revision": PARSER_REVISION,
            "recovery_revision": RECOVERY_REVISION,
            "all_discovered_session_files_completed": complete,
            "discovered_session_files": sources,
            "completed_session_files": len(collector.backfill.done),
            "batches": batches,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "integrity_check": integrity,
            "imported_archived_evidence": imported,
            "diagnostics": sorted(diagnostics),
            "fresh_rebuild": summary(samples),
            "baseline": summary(before) if baseline is not None else None,
            "comparison": compare(before, samples) if baseline is not None else None,
            "scope": "Accessible source logs and explicitly imported archived evidence; "
            "baseline supplies no samples. "
            "Live sources are not an atomic snapshot; completion is not proof of "
            "complete original logs. Reports contain no raw source identities or paths.",
        }
    finally:
        history.close()
    # Verify a reopen preserves contents, without touching any source or baseline.
    reopened = History(output / "history.sqlite")
    try:
        report["restart_summary_matches"] = (
            summary(reopened.read(now=end)) == report["fresh_rebuild"]
        )
    finally:
        reopened.close()
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New isolated directory")
    parser.add_argument("--codex-home", type=Path, default=codex_home())
    parser.add_argument("--baseline", type=Path, help="Read-only history for aggregate comparison")
    parser.add_argument(
        "--restore-evidence",
        action="store_true",
        help="Also use matching baseline archived origins, retries and activity",
    )
    parser.add_argument("--end", type=float, default=None, help="Fixed window end, Unix seconds")
    parser.add_argument("--max-seconds", type=float, default=1800)
    args = parser.parse_args()
    report = rebuild(
        args.codex_home,
        args.output,
        end=time.time() if args.end is None else args.end,
        baseline=args.baseline,
        max_seconds=args.max_seconds,
        restore_evidence=args.restore_evidence,
    )
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["all_discovered_session_files_completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
