"""Synthetic snapshots: ownership, chronology, overrides, recovery and privacy."""

import csv
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest
from pyzstd import compress
from test_backfill import bind_cache
from test_submissions import body

from token_pulse.backfill import RECOVERY_REVISION, Backfill
from token_pulse.domain import Context, reconcile
from token_pulse.history import History, export_csv
from token_pulse.logs import LogEvidence, Start
from token_pulse.rollout import Rollout
from token_pulse.tailer import JsonlTail
from token_pulse.thread_settings import ThreadSettings
from token_pulse.ui.timeline_chart import mode_info

BASE = 1767225600


def record(kind, payload, at):
    return {
        "type": kind,
        "timestamp": datetime.fromtimestamp(BASE + at, UTC).isoformat(),
        "payload": payload,
    }


def snapshot(tier="priority", *, owner="task", at=1):
    return record(
        "event_msg",
        {
            "type": "thread_settings_applied",
            "thread_id": owner,
            "thread_settings": {
                "service_tier": tier,
                "cwd": "PRIVATE PATH",
                "instructions": "PRIVATE TEXT",
            },
        },
        at,
    )


def started(turn="turn", at=3, **values):
    return record("event_msg", {"type": "task_started", "turn_id": turn, **values}, at)


def records(*, prefix=None):
    return [
        *(prefix if prefix is not None else [snapshot(), started()]),
        record("turn_context", {"turn_id": "turn", "model": "future-model"}, 3.5),
        record(
            "event_msg",
            {
                "type": "item_completed",
                "thread_id": "task",
                "turn_id": "turn",
                "item": {"id": "item", "type": "AgentMessage"},
                "started_at_ms": (BASE + 4) * 1000,
                "completed_at_ms": (BASE + 6) * 1000,
            },
            6,
        ),
        record("response_item", {"type": "message", "role": "assistant", "id": "item"}, 6),
        record(
            "token_usage_record",
            {
                "thread_id": "task",
                "turn_id": "turn",
                "response_id": "response",
                "usage": {"output_tokens": 100},
            },
            7,
        ),
    ]


def evidence(*, tier="None", start="None", at=2, turn="turn", task="task"):
    result = LogEvidence()
    result.starts["task", "item"] = Start(BASE + 4, "turn")
    result.accept(
        task,
        BASE + at,
        "codex_core::session::handlers",
        body(tier, task=task, turn=turn, start=start),
    )
    return result


def parse(values=None, logs=None):
    parser = Rollout("task")
    for r in records() if values is None else values:
        parser.feed(r)
    logs = logs or LogEvidence()
    logs.starts["task", "item"] = Start(BASE + 4, "turn")
    return parser, parser.samples(logs)[0]


def test_unobserved_override_stays_unknown_without_losing_thread_configuration():
    parser, sample = parse()
    assert sample.context.thread_config_tier == "priority"
    assert sample.context.thread_config_at == BASE + 1
    assert sample.context.thread_config_binding == "override_unknown"
    assert mode_info(sample)[0] == "other"
    assert sample.context.requested_tier is None and sample.context.actual_tier is None
    assert sample.tps == 50
    assert "PRIVATE" not in repr(parser.__dict__) and "PRIVATE" not in repr(sample)


def test_explicit_absence_of_override_binds_exact_task_and_turn_only():
    _, sample = parse(logs=evidence())
    assert sample.context.thread_config_binding == "matched"
    assert mode_info(sample)[0] == "fast"
    assert sample.context.submitted_tier is None
    assert sample.context.requested_tier is None and sample.context.actual_tier is None
    for logs in (evidence(task="foreign"), evidence(turn="foreign"), evidence(at=5)):
        _, unknown = parse(logs=logs)
        assert unknown.context.thread_config_binding == "override_unknown"
        assert mode_info(unknown)[0] == "other"


@pytest.mark.parametrize(
    "raw,expected,state",
    [
        ('Some("default")', "standard", "set"),
        ('Some("ultrafast")', "ultrafast", "set"),
        ("Some(None)", "other", "ambiguous"),
    ],
)
def test_turn_override_is_separate_and_takes_precedence(raw, expected, state):
    _, sample = parse(logs=evidence(start=raw))
    assert sample.context.thread_config_tier == "priority"
    assert sample.context.turn_override_state == state
    assert mode_info(sample)[0] == expected
    assert sample.context.requested_tier is None and sample.context.actual_tier is None
    requested = replace(sample, context=replace(sample.context, requested_tier="priority"))
    assert mode_info(requested)[0] == "fast"


def test_missing_start_override_field_is_not_literal_none():
    logs = LogEvidence()
    logs.accept(
        "task",
        BASE + 2,
        "codex_core::session::handlers",
        body("None").replace("TurnStartOptions { service_tier: None }", "TurnStartOptions { }"),
    )
    _, sample = parse(logs=logs)
    assert sample.context.turn_override_state is None
    assert mode_info(sample)[0] == "other"


def test_later_override_evidence_never_backdates_to_earlier_submission():
    logs = evidence(at=2)
    logs.accept(
        "task", BASE + 20, "codex_core::session::handlers", body("None", start='Some("default")')
    )
    assert logs.submissions["task", "turn"].override_state == "ambiguous"
    _, sample = parse(logs=logs)
    assert mode_info(sample)[0] == "other"
    unknown = LogEvidence()
    unknown.accept(
        "task",
        BASE + 2,
        "codex_core::session::handlers",
        body("None").replace("TurnStartOptions { service_tier: None }", "TurnStartOptions { }"),
    )
    unknown.accept(
        "task", BASE + 20, "codex_core::session::handlers", body("None", start='Some("default")')
    )
    _, sample = parse(logs=unknown)
    assert sample.context.turn_override_state is None
    assert mode_info(sample)[0] == "other"


@pytest.mark.parametrize(
    "tier,state",
    [
        (None, "unspecified"),
        ("unset", "unspecified"),
        (42, "invalid"),
        ({"service_tier": "priority"}, "invalid"),
        ("x" * 41, "invalid"),
    ],
)
def test_missing_or_invalid_tiers_never_mean_standard(tier, state):
    _, sample = parse(records(prefix=[snapshot(tier), started()]), evidence())
    assert sample.context.thread_config_state == state
    assert sample.context.thread_config_tier is None
    assert mode_info(sample)[0] == "other"


def test_future_or_mid_turn_snapshots_do_not_relabel_earlier_output():
    _, sample = parse(records(prefix=[started(), snapshot("default", at=3.2)]), evidence())
    assert sample.context.thread_config_tier == "default"
    assert sample.context.thread_config_binding == "during_turn"
    assert mode_info(sample)[0] == "other"
    _, sample = parse(records(prefix=[started(), snapshot("default", at=5)]), evidence())
    assert sample.context.thread_config_tier is None
    _, sample = parse(records() + [snapshot("default", at=9)], evidence())
    assert sample.context.thread_config_tier == "priority" and mode_info(sample)[0] == "fast"


def test_mid_turn_thread_change_is_for_later_turns():
    _, sample = parse(
        records(prefix=[snapshot(), started(), snapshot("default", at=3.2)]), evidence()
    )
    assert (
        sample.context.thread_config_tier == "priority"
        and sample.context.thread_config_binding == "matched"
    )
    tracker = ThreadSettings("task")
    tracker.feed(snapshot()["payload"], BASE + 1, inherited=False)
    tracker.feed(started()["payload"], BASE + 3, inherited=False)
    tracker.feed(snapshot("default")["payload"], BASE + 4, inherited=False)
    tracker.feed(started("next")["payload"], BASE + 8, inherited=False)
    assert tracker.capture(Context(), "next", BASE + 9).thread_config_tier == "default"


@pytest.mark.parametrize("owner", ["parent", None, ""])
def test_foreign_or_absent_snapshot_owner_is_not_inherited(owner):
    _, sample = parse(records(prefix=[snapshot(owner=owner), started()]), evidence())
    assert sample.context.thread_config_tier is None and mode_info(sample)[0] == "other"


def test_fork_copied_ordinals_and_child_owned_boundary():
    values = [record("session_meta", {"id": "task", "subagent_history_start_ordinal": 10}, 0)]
    copied = snapshot()
    copied["ordinal"] = 2
    own = snapshot("default", at=2)
    own["ordinal"] = 10
    rest = records(prefix=[started()])
    for i, r in enumerate(rest, 11):
        r["ordinal"] = i
    _, sample = parse([*values, copied, *rest], evidence())
    assert sample.context.thread_config_tier is None
    _, sample = parse([*values, copied, own, *rest], evidence())
    assert sample.context.thread_config_tier == "default" and mode_info(sample)[0] == "standard"
    values = [
        record("session_meta", {"id": "parent"}, 0),
        snapshot(owner="parent"),
        snapshot("default", at=2),
        *records(prefix=[started()]),
    ]
    _, sample = parse(values, evidence())
    assert sample.context.thread_config_tier == "default" and sample.tps == 50


def test_goals_and_root_lineage_do_not_supply_mode_or_override_values():
    goal = started(
        turn_attribution={"turn_trigger": "goal", "root_turn_id": "parent-turn"},
        root_turn_id="parent-turn",
    )
    _, sample = parse(records(prefix=[snapshot(), goal]), evidence(turn="parent-turn"))
    assert mode_info(sample)[0] == "other"


def test_integer_turn_start_time_is_a_conservative_bound():
    _, sample = parse(
        records(prefix=[snapshot(at=2.5), started(started_at=BASE + 2)]), evidence(at=1)
    )
    assert sample.context.thread_config_binding == "during_turn"
    _, sample = parse(records(prefix=[snapshot(), started(started_at=BASE + 20)]), evidence())
    assert sample.context.thread_config_binding == "ambiguous_turn"


def test_duplicates_conflicting_starts_and_same_time_settings():
    _, sample = parse(records(prefix=[snapshot(), snapshot(), started(), started()]), evidence())
    assert sample.context.thread_config_binding == "matched"
    _, sample = parse(records(prefix=[snapshot(), snapshot("default"), started()]), evidence())
    assert sample.context.thread_config_state == "ambiguous" and mode_info(sample)[0] == "other"
    _, sample = parse(records(prefix=[snapshot(), started(at=2.5), started()]), evidence(at=2))
    assert sample.context.thread_config_binding == "ambiguous_turn"


def test_unknown_gaps_reset_ownership_and_carry_state():
    parser = Rollout("task")
    parser.feed(snapshot())
    parser.damage("record_gap")
    for r in records(prefix=[started()]):
        parser.feed(r)
    assert parser.samples(evidence())[0].context.thread_config_tier is None
    tracker = ThreadSettings("task", capacity=2)
    tracker.feed(snapshot()["payload"], BASE + 10, inherited=False)
    tracker.feed(snapshot("default")["payload"], BASE + 9, inherited=False)
    assert tracker.capture(Context(), "turn", BASE + 20).thread_config_tier is None
    for i in range(10):
        tracker.feed(snapshot()["payload"], BASE + 20 + i * 2, inherited=False)
        tracker.feed(started(str(i))["payload"], BASE + 21 + i * 2, inherited=False)
    assert len(tracker.snapshots) == len(tracker.turns) == 2


def test_duplicate_json_fields_cannot_supply_owner_tier_or_turn(tmp_path):
    for field in ("owner", "tier", "turn", "outer"):
        values = [json.dumps(r) for r in records()]
        if field == "owner":
            values[0] = values[0].replace(
                '"thread_id": "task"', '"thread_id":"parent","thread_id":"task"'
            )
        if field == "tier":
            values[0] = values[0].replace(
                '"service_tier": "priority"', '"service_tier":"default","service_tier":"priority"'
            )
        if field == "turn":
            values[1] = values[1].replace(
                '"turn_id": "turn"', '"turn_id":"foreign","turn_id":"turn"'
            )
        if field == "outer":
            values[0] = values[0].replace(
                '"type": "event_msg"', '"type":"response_item","type":"event_msg"', 1
            )
        path = tmp_path / f"{field}.jsonl"
        path.write_text("\n".join(values) + "\n")
        _, sample = parse(JsonlTail(path, backfill=None).read().records, evidence())
        assert mode_info(sample)[0] == "other"


@pytest.mark.parametrize("compressed", [False, True])
def test_recovery_restart_source_loss_and_csv_keep_separate_evidence(tmp_path, compressed):
    path = tmp_path / ("source.jsonl.zst" if compressed else "source.jsonl")
    raw = ("\n".join(json.dumps(r) for r in records()) + "\n").encode()
    path.write_bytes(compress(raw) if compressed else raw)
    history = History(tmp_path / "history.sqlite")
    try:
        recovery = Backfill(budget=150)
        bind_cache(recovery, history)
        recovery.add("task", path, Context(), live=False)
        for _ in range(100):
            samples, issues = recovery.poll(evidence(), BASE + 100)
            assert not issues
            history.append(samples, now=BASE + 100)
            if recovery.completed:
                history.save_checkpoints(recovery.completed, now=BASE + 100)
            if not recovery.pending:
                break
        saved = history.read()[0]
        assert saved.context.thread_config_binding == "matched" and saved.tps == 50
        assert saved.context.requested_tier is None and saved.context.actual_tier is None
        key = saved.key
        history.close()
        history = History(tmp_path / "history.sqlite")
        assert history.read()[0].key == key
        restarted = Backfill()
        bind_cache(restarted, history)
        restarted.add("task", path, Context(), live=False)
        assert restarted.poll(evidence(), BASE + 100)[0] == [] and restarted.reused == 1
        _, sparse = parse(records(prefix=[started()]))
        history.append([sparse], now=BASE + 100)
        assert history.read()[0].context.thread_config_binding == "matched"
        exported = tmp_path / "export.csv"
        export_csv(exported, history.read())
        with exported.open(encoding="utf-8-sig", newline="") as stream:
            row = next(csv.DictReader(stream))
        assert row["thread_config_tier"] == "priority" and row["turn_override_state"] == "none"
        assert row["requested_tier"] == row["actual_tier"] == ""
        assert "PRIVATE" not in exported.read_text(encoding="utf-8-sig")
        assert RECOVERY_REVISION >= 11
    finally:
        history.close()


def test_saved_history_gains_exact_override_evidence_without_rereading_source(tmp_path):
    _, sample = parse()
    history = History(tmp_path / "history.sqlite")
    try:
        history.append([sample], now=BASE + 100)
        old = history.read()[0]
        updated = history.enrich_submissions([old], evidence().submissions)[0]
        assert updated.context.thread_config_binding == "matched"
        assert (updated.key, updated.started_at, updated.ended_at, updated.output_tokens) == (
            old.key,
            old.started_at,
            old.ended_at,
            old.output_tokens,
        )
        history.append([sample], now=BASE + 100)
        assert history.read()[0].context.thread_config_binding == "matched"
    finally:
        history.close()


def test_cross_source_merge_retains_configuration_without_changing_transport_timing():
    _, local = parse(logs=evidence())
    wire = replace(
        local,
        context=Context(model="future-model", actual_tier="default"),
        source="codex-transport-log-v1",
        timing_source="transport-log",
        started_at=BASE + 4.5,
    )
    for left, right in ((local, wire), (wire, local)):
        merged = reconcile(left, right)
        assert merged.started_at == wire.started_at
        assert merged.context.thread_config_binding == "matched"
        assert merged.context.actual_tier == "default"
    legacy = asdict(local)
    for field in list(legacy["context"]):
        if field.startswith(("thread_config_", "turn_override_")):
            del legacy["context"][field]
    assert History._decode(json.dumps(legacy)).context.thread_config_state is None


def test_earlier_transport_start_revalidates_snapshot_in_both_merge_orders():
    _, local = parse(logs=evidence())
    wire = replace(
        local,
        context=Context(model="future-model"),
        source="codex-transport-log-v1",
        timing_source="transport-log",
        started_at=BASE + 0.5,
    )
    for left, right in ((local, wire), (wire, local)):
        merged = reconcile(left, right)
        assert merged.context.thread_config_binding == "after_output_start"
        assert merged.context.thread_config_tier == "priority"
        assert mode_info(merged)[0] == "other"
        assert merged.started_at == wire.started_at and merged.tps == wire.tps
        assert reconcile(merged, local).context.thread_config_binding == "after_output_start"


def test_older_partial_snapshot_and_late_replayed_submission_cannot_roll_back_evidence():
    _, older = parse(logs=evidence())
    _, newer = parse(records(prefix=[snapshot("default", at=2), started()]), evidence())
    for left, right in ((older, newer), (newer, older)):
        merged = reconcile(left, right)
        assert merged.context.thread_config_tier == "default"
        assert merged.context.thread_config_binding == "matched"
    late = replace(newer, context=replace(newer.context, turn_override_at=BASE + 5))
    assert reconcile(newer, late).context.thread_config_binding == "matched"


def test_saved_override_conflicts_survive_enrichment_and_partial_replay(tmp_path):
    _, sample = parse(logs=evidence())
    history = History(tmp_path / "history.sqlite")
    try:
        history.append([sample], now=BASE + 100)
        saved = history.read()[0]
        override = evidence(start='Some("default")')
        updated = history.enrich_submissions([saved], override.submissions)[0]
        assert updated.context.turn_override_state == "ambiguous"
        assert mode_info(updated)[0] == "other" and updated.tps == saved.tps
        again = history.enrich_submissions([updated], evidence().submissions)[0]
        assert again.context.turn_override_state == "ambiguous"
        history.append([sample], now=BASE + 100)
        assert history.read()[0].context.turn_override_state == "ambiguous"
    finally:
        history.close()


def test_missing_or_foreign_turn_start_does_not_prove_association():
    for prefix in ([snapshot()], [snapshot(), started(thread_id="foreign")]):
        _, sample = parse(records(prefix=prefix), evidence())
        assert sample.context.thread_config_binding != "matched"
        assert mode_info(sample)[0] == "other"


def test_saved_thread_submission_conflict_cannot_promote_a_snapshot_after_restart(tmp_path):
    _, changed = parse(logs=evidence(tier='Some(Some("default"))'))
    _, unspecified = parse(logs=evidence())
    for left, right in ((changed, unspecified), (unspecified, changed)):
        merged = reconcile(left, right)
        assert merged.context.submitted_tier_state == "ambiguous"
        assert merged.context.thread_config_binding == "submission_changed"
        assert mode_info(merged)[0] == "other"
    history = History(tmp_path / "history.sqlite")
    try:
        history.append([changed], now=BASE + 100)
        history.close()
        history = History(tmp_path / "history.sqlite")
        updated = history.enrich_submissions(history.read(), evidence().submissions)[0]
        assert updated.context.submitted_tier_state == "ambiguous"
        assert mode_info(updated)[0] == "other" and updated.tps == changed.tps
        history.append([unspecified], now=BASE + 100)
        assert history.read()[0].context.thread_config_binding == "submission_changed"
    finally:
        history.close()


def test_later_turn_start_conflict_updates_already_captured_outputs():
    parser, original = parse(logs=evidence())
    parser.feed(started(at=10))
    updated = parser.samples(evidence())[0]
    assert updated.context.thread_config_binding == "ambiguous_turn"
    assert updated.tps == original.tps
    merged = reconcile(original, updated)
    assert reconcile(merged, original).context.thread_config_binding == "ambiguous_turn"


def test_backdated_metadata_cannot_reenter_after_rollout_gap_reset():
    values = [snapshot(at=2), snapshot("default", at=1), started()]
    _, sample = parse(records(prefix=values), evidence())
    assert sample.context.thread_config_tier is None
    assert mode_info(sample)[0] == "other"


@pytest.mark.parametrize("tier", ['Some(Some("default"))', "Some(None)"])
def test_changed_or_cleared_submission_blocks_old_snapshot(tier):
    _, sample = parse(logs=evidence(tier=tier))
    assert sample.context.thread_config_binding == "submission_changed"
    assert sample.context.thread_config_tier == "priority"
    assert mode_info(sample)[0] == "standard"  # Explicit submission, not inherited snapshot.


def test_partial_line_and_file_replacement_require_fresh_ownership(tmp_path):
    path = tmp_path / "source.jsonl"
    raw = ("\n".join(json.dumps(r) for r in records()) + "\n").encode()
    first_line = raw.index(b"\n")
    path.write_bytes(raw[:first_line])
    tail = JsonlTail(path, backfill=None)
    assert tail.read().records == []
    with path.open("ab") as stream:
        stream.write(raw[first_line:])
    _, sample = parse(tail.read().records, evidence())
    assert sample.context.thread_config_binding == "matched"
    path.write_text("\n".join(json.dumps(r) for r in records(prefix=[started()])) + "\n")
    batch = tail.read()
    assert batch.reset
    _, replaced = parse(batch.records, evidence())
    assert mode_info(replaced)[0] == "other"
