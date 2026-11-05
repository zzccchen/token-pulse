"""Calculate tiered relaxed coverage in a new isolated database.

The denominator is the fixed reference population. Original samples and their
validated TPS flags remain intact; estimates live in a separate SQLite table.
No message content, raw identity or source path is written to the artifacts.
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

from token_pulse.collector import Collector, codex_home
from token_pulse.domain import resolve_response_alias
from token_pulse.history import History, csv_cell
from token_pulse.logs import LogEvidence, read_only
from token_pulse.relaxed import ELAPSED_ISSUES, RELAXED_REVISION, estimate
from token_pulse.rollout import REJECTIONS, Rollout
from token_pulse.tailer import open_tail


def run(reference: Path, output: Path, root: Path, *, target=0.99):
    reference, output, root = reference.resolve(), output.resolve(), root.resolve()
    if not math.isfinite(target) or not 0 < target <= 1:
        raise ValueError("Target must be in (0, 1]")
    if output.exists() or output.is_relative_to(root):
        raise ValueError("Output must be a new directory outside the source directory")
    window = json.loads((reference.parent / "report.json").read_text(encoding="utf-8"))
    end = datetime.fromisoformat(window["window_end_utc"]).timestamp()
    started = time.monotonic()
    with closing(sqlite3.connect(":memory:")) as memory:
        with closing(read_only(reference)) as source:
            source.backup(memory)
        salt = bytes.fromhex(
            memory.execute("SELECT value FROM settings WHERE key='salt'").fetchone()[0]
        )

        def anon(kind, raw):
            return hmac.new(salt, f"{kind}:{raw}".encode(), hashlib.sha256).hexdigest()[:24]

        samples = {}
        for (payload,) in memory.execute("SELECT payload FROM samples"):
            sample = History._decode(payload)
            if sample is None:
                raise ValueError("Reference contains an undecodable sample")
            samples[sample.key] = sample
        if not samples:
            raise ValueError("Reference population is empty")
        aliases = defaultdict(set)
        for task, turn, alias, canonical in memory.execute(
            "SELECT task,turn,alias,canonical FROM response_aliases"
        ):
            aliases[task, turn, alias].add(canonical)
        candidates = defaultdict(set)
        matched = set()
        collector = Collector(root)
        collector._discover(end)
        diagnostics = Counter()
        bytes_read = completed = 0
        for source in collector.backfill.queue:
            tail = open_tail(source.path, backfill=None)
            parser = Rollout(source.task, context=source.context)

            def drain(parser):
                responses = [*parser.responses, *parser.corrections]
                for _response, parsed in zip(responses, parser.samples(LogEvidence()), strict=True):
                    if parsed.issue in REJECTIONS:
                        continue
                    envelope = (
                        dict(
                            elapsed_start=parsed.cycle_started_at,
                            usage_at=parsed.cycle_ended_at,
                            anchor=parsed.cycle_anchor,
                        )
                        if parsed.cycle_anchor
                        else None
                    )
                    safe = replace(
                        parsed,
                        task_id=anon("task", parsed.task_id),
                        turn_id=anon("turn", parsed.turn_id),
                        response_id=anon("response", parsed.response_id),
                        response_aliases=tuple(
                            anon("response", a) for a in parsed.response_aliases
                        ),
                    )
                    safe = resolve_response_alias(safe, aliases)
                    saved = samples.get(safe.key)
                    if saved is None or (
                        saved.output_tokens != safe.output_tokens
                        or saved.token_scope != safe.token_scope
                        or saved.ended_at != safe.ended_at
                    ):
                        continue
                    matched.add(safe.key)
                    if envelope and (safe.exclusion is None or safe.exclusion in ELAPSED_ISSUES):
                        candidates[safe.key].add(tuple(sorted(envelope.items())))
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
                        diagnostics["record_gap"] += 1
                    parser.feed(record)
                    # Drain immediately to freeze the anchor at observation time.
                    if parser.responses or parser.corrections:
                        drain(parser)
                if len(batch.records) in gaps:
                    parser.damage("record_gap")
                    diagnostics["record_gap"] += 1
                if batch.eof:
                    completed += int(not tail.pending and not tail.dropping)
                    break
            if completed % 20 == 0:
                print(
                    json.dumps({"parsed_files": completed, "matched_records": len(matched)}),
                    flush=True,
                )
        estimates = {}
        for key, sample in samples.items():
            choices = candidates[key]
            envelope = dict(next(iter(choices))) if len(choices) == 1 else {}
            estimates[key] = estimate(sample, **envelope)
        groups = Counter(e.basis for e in estimates.values() if e)
        covered = sum(groups.values())
        strict = groups["validated_output"]
        lifecycle = groups["unverified_lifecycle"]
        result = {
            "relaxed_revision": RELAXED_REVISION,
            "window_start_utc": window["window_start_utc"],
            "window_end_utc": window["window_end_utc"],
            "label": "Tiered rate-estimate coverage; not validated output TPS coverage",
            "elapsed_caveat": (
                "Cycle intervals may include waiting, tools, retries and delayed usage"
            ),
            "records": len(samples),
            "covered": covered,
            "coverage": covered / len(samples),
            "target": target,
            "required_records": math.ceil(target * len(samples)),
            "target_met": covered >= math.ceil(target * len(samples)),
            "validated_records": strict,
            "validated_coverage": strict / len(samples),
            "groups": dict(groups),
            "estimated_exclusions": dict(
                Counter(
                    samples[k].exclusion
                    for k, e in estimates.items()
                    if e is not None and e.basis != "validated_output"
                )
            ),
            "coverage_ladder": {
                "validated_only": strict / len(samples),
                "with_unverified_lifecycle": (strict + lifecycle) / len(samples),
                "with_observed_cycle_elapsed": covered / len(samples),
            },
            "remaining_exclusions": dict(
                Counter(s.exclusion for k, s in samples.items() if estimates[k] is None)
            ),
            "reparsed_records": len(matched),
            "not_reparsed_records": len(samples) - len(matched),
            "ambiguous_cycles": sum(len(v) > 1 for v in candidates.values()),
            "indexed_files": len(collector.backfill.queue),
            "completed_files": completed,
            "incomplete_files": len(collector.backfill.queue) - completed,
            "bytes_read": bytes_read,
            "diagnostics": dict(diagnostics),
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }
        output.mkdir(parents=True, exist_ok=False)
        destination = output / "history.sqlite"
        with closing(sqlite3.connect(destination)) as db:
            memory.backup(db)
            with db:
                db.execute(
                    "CREATE TABLE relaxed_estimates (key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
                db.executemany(
                    "INSERT INTO relaxed_estimates VALUES (?,?)",
                    [
                        ("|".join(k), json.dumps(asdict(e), allow_nan=False))
                        for k, e in estimates.items()
                        if e
                    ],
                )
            result["integrity_check"] = db.execute("PRAGMA integrity_check").fetchone()[0]
        with closing(read_only(destination)) as db:
            result["original_samples_unchanged"] = list(
                db.execute("SELECT * FROM samples ORDER BY key")
            ) == list(memory.execute("SELECT * FROM samples ORDER BY key"))
            result["restart_matches"] = dict(
                db.execute("SELECT key,payload FROM relaxed_estimates")
            ) == {
                "|".join(k): json.dumps(asdict(e), allow_nan=False)
                for k, e in estimates.items()
                if e
            }
        with (output / "estimates.csv").open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "task_hmac",
                    "turn_hmac",
                    "response_hmac",
                    "completed_at_unix",
                    "output_tokens",
                    "original_exclusion",
                    "basis",
                    "interval_start",
                    "interval_end",
                    "seconds",
                    "estimated_tokens_per_second",
                    "validated_output_tps",
                ]
            )
            for key, sample in sorted(samples.items()):
                e = estimates[key]
                writer.writerow(
                    [
                        csv_cell(v)
                        for v in [
                            *key,
                            sample.ended_at,
                            sample.output_tokens,
                            sample.exclusion,
                            e.basis if e else "unavailable",
                            e.started_at if e else None,
                            e.ended_at if e else None,
                            e.ended_at - e.started_at if e else None,
                            e.tokens_per_second if e else None,
                            sample.tps,
                        ]
                    ]
                )
        (output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--codex-home", type=Path, default=codex_home())
    parser.add_argument("--target", type=float, default=0.99)
    args = parser.parse_args()
    result = run(args.reference, args.output, args.codex_home, target=args.target)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["target_met"] else 2)
