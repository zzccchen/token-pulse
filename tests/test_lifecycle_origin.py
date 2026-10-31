"""A positive lifecycle interval does not establish streaming provenance."""

import csv
import json
from dataclasses import asdict, replace

import pytest
from test_rollout import completed_item, event, evidence, output, ready, usage

from token_pulse.domain import Context, Sample, correct_legacy_lifecycle, reconcile
from token_pulse.history import History, export_csv
from token_pulse.logs import LogEvidence, Start
from token_pulse.rollout import PARSER_REVISION
from token_pulse.ui.timeline_chart import duration_text, sample_text


@pytest.mark.parametrize(
    "kind,lifecycle", [("message", "AgentMessage"), ("reasoning", "Reasoning")]
)
@pytest.mark.parametrize("start,end,tool_end", [(12, 12.001, 12.006), (1, 8, 13)])
def test_positive_lifecycle_and_later_tool_item_cannot_supply_full_generation(
    kind, lifecycle, start, end, tool_end
):
    parser = ready()
    completed_item(parser, kind=lifecycle, start=start, end=end)
    event(parser, "response_item", {"type": kind, "role": "assistant", "id": "item"}, end)
    event(parser, "response_item", {"type": "custom_tool_call", "id": "tool"}, tool_end)
    usage(parser, second=14)
    sample = parser.samples(LogEvidence())[0]
    assert sample.exclusion == "unverified_lifecycle_start"
    assert sample.started_at == 1767225600 + start and sample.tps is None
    assert sample.output_tokens == 100 and sample.ended_at == 1767225600 + tool_end
    # A later item's start cannot prove the beginning of the first one.
    later = LogEvidence()
    later.starts["task", "tool"] = Start(1767225600 + end, "turn")
    assert parser.samples(later)[0].exclusion == "unverified_lifecycle_start"
    # Exact independent first-item evidence still validates even very high TPS.
    verified = parser.samples(evidence(second=start))[0]
    assert verified.exclusion is None
    assert verified.tps == pytest.approx(100 / (tool_end - start), rel=1e-4)


def test_first_lifecycle_plus_later_message_never_promotes_without_origin():
    parser = ready()
    completed_item(parser, kind="Reasoning", start=5, end=5.001)
    event(parser, "response_item", {"type": "reasoning", "id": "item"}, 5.002)
    completed_item(parser, item="later", kind="AgentMessage", start=6, end=10)
    output(parser, "later", 10)
    usage(parser)
    assert parser.samples(evidence("later", second=6))[0].tps is None


def legacy_sample(response="old", duration=0.006):
    return Sample(
        "task",
        "turn",
        response,
        100,
        100,
        100 - duration,
        context=Context(model="model", requested_tier="priority"),
        timing_source="item-event",
        parser_revision=7,
        response_aliases=(f"ordered-item-digest-{response}",),
    )


def test_saved_legacy_timings_are_migrated_without_source_files_or_live_cache(tmp_path):
    path = tmp_path / "history.sqlite"
    history = History(path)
    old = [legacy_sample(str(i), duration=0.006 if i % 2 else 60) for i in range(350)]
    safe = [history.anonymous(s) for s in old]
    with history.connection:
        history.connection.executemany(
            "INSERT INTO samples VALUES(?,?,?,?)",
            [("|".join(s.key), s.ended_at, 1, json.dumps(asdict(s))) for s in safe],
        )
    history.close()
    history = History(path)
    try:
        corrected = history.read()
        assert {s.key for s in corrected} == {s.key for s in safe}
        assert all(s.exclusion == "unverified_lifecycle_start" for s in corrected)
        assert all(s.output_tokens == 100 and s.parser_revision == 8 for s in corrected)
        assert {s.key: s.started_at for s in corrected} == {s.key: s.started_at for s in safe}
        assert all(s.context.requested_tier == "priority" for s in corrected)
        assert all(s.response_aliases for s in corrected)
        assert history.connection.execute("SELECT sum(valid) FROM samples").fetchone()[0] == 0
        history.append(old, now=110)
        assert all(s.tps is None for s in history.read())
        export_csv(tmp_path / "samples.csv", history.read())
        with (tmp_path / "samples.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert all(r["tps"] == r["stream_seconds"] == "" for r in rows)
    finally:
        history.close()
    history = History(path)
    assert all(s.exclusion == "unverified_lifecycle_start" for s in history.read())
    history.close()


@pytest.mark.parametrize("timing", ["stream-log", "transport-log"])
def test_independent_saved_boundaries_survive_source_loss_even_at_high_rates(tmp_path, timing):
    independent = replace(legacy_sample(), timing_source=timing)
    assert correct_legacy_lifecycle(independent) == independent
    history = History(tmp_path / "history.sqlite")
    history.append([independent], now=110)
    history.close()
    history = History(tmp_path / "history.sqlite")
    assert history.read()[0].tps == independent.tps
    unknown = replace(
        legacy_sample(),
        issue="unverified_lifecycle_start",
        started_at=None,
        parser_revision=PARSER_REVISION,
    )
    history.append([unknown], now=111)
    assert history.read()[0].tps == independent.tps
    assert reconcile(independent, unknown).tps == independent.tps
    assert reconcile(unknown, independent).tps == independent.tps
    history.close()


@pytest.mark.parametrize("timing", ["stream-log", "transport-log"])
def test_revalidation_survives_repeated_withdrawal_and_legacy_replays(tmp_path, timing):
    old = legacy_sample()
    corrected = correct_legacy_lifecycle(old)
    complete = replace(
        old,
        started_at=98,
        timing_source=timing,
        parser_revision=PARSER_REVISION,
        source="codex-transport-log-v1" if timing == "transport-log" else old.source,
    )
    for left, right in ((complete, corrected), (corrected, complete), (old, complete)):
        assert reconcile(left, right).tps == 50
    history = History(tmp_path / "history.sqlite")
    try:
        history.append([old], now=110)
        assert history.read()[0].tps is None
        history.append([complete, corrected, old], now=111)
        assert history.read()[0].tps == 50
        assert len(history.read()) == 1
    finally:
        history.close()


def test_transport_usage_conflicts_remain_excluded_after_lifecycle_withdrawal():
    local = correct_legacy_lifecycle(legacy_sample())
    wire = replace(
        local,
        source="codex-transport-log-v1",
        timing_source="transport-log",
        output_tokens=123,
        started_at=98,
        issue=None,
    )
    assert reconcile(local, wire).exclusion == "cross_source_conflict"


@pytest.mark.parametrize("seconds,display", [(0.006, "0.006"), (0.00012, "0.00012"), (2, "2.00")])
def test_short_duration_display_does_not_round_to_zero_or_a_centisecond(seconds, display):
    assert duration_text(seconds) == display
    sample = replace(legacy_sample(duration=seconds), timing_source="stream-log")
    assert display in sample_text(sample)
