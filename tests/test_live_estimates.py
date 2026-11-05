import csv
from dataclasses import replace

import pytest
from test_rollout import event, output, ready, usage

from token_pulse.domain import reconcile
from token_pulse.history import History, export_csv
from token_pulse.logs import LogEvidence


def test_live_cycles_survive_drains_storage_and_restart_without_becoming_tps(tmp_path):
    parser = ready()
    output(parser)
    usage(parser)
    first = parser.samples(LogEvidence())[0]
    assert first.tps is None and first.display_tps == pytest.approx(100 / 13)
    assert first.cycle_anchor == "task_started"
    parser.responses.clear()
    parser.corrections.clear()
    event(parser, "response_item", {"type": "function_call", "id": "tool"}, 20)
    usage(parser, "second", 23)
    second = parser.samples(LogEvidence())[0]
    assert second.tps is None and second.display_tps == 10
    assert second.cycle_anchor == "previous_usage"
    # Repeated usage neither duplicates the output nor advances its cycle.
    usage(parser, "second", 24)
    assert parser.samples(LogEvidence()) == [second]
    path = tmp_path / "history.sqlite"
    history = History(path)
    history.append([first, second], now=1767225625)
    saved = history.read(now=1767225625)
    history.close()
    history = History(path)
    assert history.read(now=1767225625) == saved
    assert all(s.tps is None and s.display_tps is not None for s in saved)
    assert (
        history.connection.execute("SELECT count(*) FROM samples WHERE valid=1").fetchone()[0] == 0
    )
    export_csv(tmp_path / "samples.csv", saved)
    with (tmp_path / "samples.csv").open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert all(not r["tps"] and r["estimated_tokens_per_second"] for r in rows)
    assert {r["rate_basis"] for r in rows} == {"elapsed_task_started", "elapsed_previous_usage"}
    history.close()


def test_live_gap_does_not_reuse_old_turn_start_and_new_turn_can_recover():
    parser = ready()
    parser.damage("record_gap")
    output(parser)
    usage(parser)
    first = parser.samples(LogEvidence())[0]
    assert first.display_tps is None
    event(parser, "event_msg", {"type": "task_started", "turn_id": "new"}, 20)
    output(parser, "next", 22)
    usage(parser, "next-response", 24)
    assert parser.samples(LogEvidence())[-1].display_tps == 25


def test_cycle_enrichment_preserves_stronger_proof_and_same_rule_conflicts():
    parser = ready()
    output(parser)
    usage(parser)
    inferred = parser.samples(LogEvidence())[0]
    saved = replace(
        inferred,
        started_at=inferred.ended_at - 2,
        issue=None,
        timing_source="stream-log",
        parser_revision=9,
        cycle_started_at=None,
        cycle_ended_at=None,
        cycle_anchor=None,
        cycle_revision=0,
    )
    merged = reconcile(saved, inferred)
    assert merged.tps == 50 and merged.cycle_anchor == "task_started"
    assert reconcile(merged, saved).cycle_anchor == "task_started"
    conflict = reconcile(merged, replace(inferred, cycle_started_at=inferred.cycle_started_at + 1))
    assert conflict.tps == 50 and conflict.cycle_anchor == "ambiguous"
    assert reconcile(conflict, inferred).cycle_anchor == "ambiguous"


def test_compaction_correction_retains_original_observed_cycle_after_batch_drain():
    parser = ready()
    usage(parser, "compact", 10)
    first = parser.samples(LogEvidence())[0]
    parser.responses.clear()
    parser.corrections.clear()
    event(
        parser,
        "compacted",
        {
            "compaction_response_id": "compact",
            "latest_token_usage_record": {
                "thread_id": "task",
                "turn_id": "turn",
                "response_id": "compact",
                "usage": {"output_tokens": 100},
            },
        },
        11,
    )
    corrected = parser.samples(LogEvidence())[0]
    assert corrected.issue == "compaction_usage" and corrected.display_tps == 10
    assert corrected.cycle_started_at == first.cycle_started_at
    assert corrected.cycle_ended_at == first.cycle_ended_at
