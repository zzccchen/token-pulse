"""Synthetic protocol sequences independent of model names and local log versions."""

import copy
import hashlib
import json
from dataclasses import replace

import pytest

from token_pulse.domain import Context, Sample
from token_pulse.history import History, export_csv
from token_pulse.stats import summarize, unique
from token_pulse.transport import REVISION, SOURCE, TARGETS, Transport, decode_event

ROUTE = ("task", "turn", "process", TARGETS[0], ())


def events(response="response", item="item", *, tokens=100, model="future-model"):
    values = [
        {"type": "response.created", "response": {"id": response, "status": "in_progress"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"id": item, "type": "message", "role": "assistant"},
        },
        {
            "type": "response.content_part.added",
            "item_id": item,
            "output_index": 0,
            "content_index": 0,
            "part": {"type": "output_text", "text": ""},
        },
        {
            "type": "response.output_text.delta",
            "item_id": item,
            "output_index": 0,
            "content_index": 0,
            "delta": "PRIVATE OUTPUT AND TOOL ARGUMENTS",
        },
        {
            "type": "response.output_text.done",
            "item_id": item,
            "output_index": 0,
            "content_index": 0,
            "text": "PRIVATE OUTPUT AND TOOL ARGUMENTS",
        },
        {
            "type": "response.content_part.done",
            "item_id": item,
            "output_index": 0,
            "content_index": 0,
            "part": {"type": "output_text", "text": "PRIVATE OUTPUT"},
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {"id": item, "type": "message", "role": "assistant"},
        },
        {
            "type": "response.completed",
            "response": {
                "id": response,
                "status": "completed",
                "model": model,
                "output": [],
                "service_tier": "future-tier",
                "usage": {
                    "output_tokens": tokens,
                    "output_tokens_details": {"reasoning_tokens": 40},
                },
                "reasoning": {"effort": "high"},
                "created_at": 90,
                "completed_at": 120,
            },
        },
    ]
    return [dict(value, sequence_number=i) for i, value in enumerate(values)]


def feed(parser, values=None, *, route=ROUTE, start=100):
    for i, event in enumerate(events() if values is None else values):
        parser.feed(route, start + i, event)
    return parser.samples()


def test_complete_stream_uses_usage_and_item_interval_not_server_seconds_or_delta_count():
    parser = Transport()
    sample = feed(parser)[0]
    assert sample.output_tokens == 100  # Reasoning is already included.
    assert sample.started_at == 101 and sample.ended_at == 106
    assert sample.tps == 20
    assert sample.context == Context(model="future-model", effort="high", actual_tier="future-tier")
    assert sample.context.requested_tier is None
    assert sample.response_aliases == (hashlib.sha256(b"item").hexdigest(),)
    assert "PRIVATE" not in repr(parser.__dict__) and "PRIVATE" not in repr(sample)


@pytest.mark.parametrize(
    "mutation,issue",
    [
        (lambda v: v[3].update(item_id="foreign"), "transport_unknown_item"),
        (lambda v: v[3].update(output_index=9), "transport_unknown_item"),
        (lambda v: v[3].update(content_index=9), "transport_unknown_content"),
        (lambda v: v[3].update(type="response.future_tool_execution"), "transport_unknown_event"),
        (lambda v: v[3].update(response_id="foreign"), "transport_conflict"),
        (lambda v: v[3].update(sequence_number=30), "transport_sequence_gap"),
        (lambda v: v[0].update(sequence_number=True), "transport_missing_start"),
        (lambda v: v[0].update(sequence_number=1), "transport_conflict"),
        (lambda v: v[3].update(delta={}), "transport_invalid_delta"),
        (lambda v: v[3].update(type={}), "transport_record_gap"),
        (lambda v: v[1]["item"].update(type={}), "transport_unsupported_item"),
        (lambda v: v[2]["part"].update(type={}), "transport_unsupported_content"),
        (lambda v: v[6]["item"].update(id="foreign"), "transport_incomplete_items"),
        (lambda v: v[-1]["response"].update(status="incomplete"), "transport_failed"),
        (lambda v: v[-1]["response"].update(error={"message": "PRIVATE"}), "transport_failed"),
        (
            lambda v: v[-1]["response"].update(usage={"output_tokens": True}),
            "transport_missing_usage",
        ),
        (
            lambda v: v[-1]["response"].update(output=[{"id": "foreign", "type": "message"}]),
            "transport_conflict",
        ),
    ],
)
def test_invalid_events_keep_completion_usage_without_tps(mutation, issue):
    values = events()
    mutation(values)
    sample = feed(Transport(), values)[0]
    assert sample.tps is None
    assert sample.issue == issue
    assert sample.response_aliases == ()


def test_missing_created_or_process_or_sequence_is_not_repaired_by_later_output():
    assert feed(Transport(), events()[1:])[0].issue == "transport_missing_start"
    assert (
        feed(Transport(), route=("task", "turn", None, TARGETS[0], ()))[0].issue
        == "transport_missing_process"
    )
    values = events()
    del values[3]
    assert feed(Transport(), values)[0].issue == "transport_sequence_gap"


def test_regressing_receive_timestamps_and_new_created_events_cannot_establish_timing():
    parser = Transport()
    for i, event in enumerate(events()):
        parser.feed(ROUTE, 100 + i if i != 4 else 102.9999, event)
    assert parser.samples()[0].issue == "transport_timestamp_order"
    values = events()
    values.insert(4, values[0])
    values = [dict(value, sequence_number=i) for i, value in enumerate(values)]
    assert feed(Transport(), values)[0].issue == "transport_conflict"


def test_incomplete_parts_and_deltas_after_done_are_rejected():
    values = events()
    values[4]["type"] = "response.metadata"
    assert feed(Transport(), values)[0].issue == "transport_incomplete_content"
    values = events()
    values[4], values[5] = values[5], values[4]
    for i, value in enumerate(values):
        value["sequence_number"] = i
    assert feed(Transport(), values)[0].issue == "transport_unknown_content"


def test_replays_deduplicate_and_conflicting_duplicates_stay_excluded():
    parser = Transport()
    values = events()
    feed(parser, values[:4])
    for value in values[:4]:
        parser.feed(ROUTE, 200, value)  # Whole-prefix replay, including created.
    for i, value in enumerate(values[4:]):
        parser.feed(ROUTE, 104 + i, value)
    original = parser.samples()[0]
    assert original.tps == 20
    feed(parser, values, start=300)
    assert parser.samples() == [original]
    conflicting = copy.deepcopy(values)
    conflicting[-1]["response"]["usage"]["output_tokens"] = 101
    feed(parser, conflicting, start=400)
    assert parser.samples()[0].issue == "transport_conflict"
    feed(parser, values, start=500)
    assert parser.samples()[0].tps is None


def test_interleaved_tasks_and_explicitly_routed_responses_do_not_mix():
    parser = Transport()
    first, second = events("one", "a", tokens=100), events("two", "b", tokens=200)
    for i, (a, b) in enumerate(zip(first, second, strict=True)):
        parser.feed(ROUTE, 100 + i, a)
        parser.feed(("other-task", "turn", "process", TARGETS[0], ()), 100 + i, b)
    assert sorted(s.tps for s in parser.samples()) == [20, 40]
    parser = Transport()
    for i, (a, b) in enumerate(zip(first, second, strict=True)):
        parser.feed(ROUTE, 100 + i, dict(a, response_id="one"))
        parser.feed(ROUTE, 100 + i, dict(b, response_id="two"))
    assert sorted(s.tps for s in parser.samples()) == [20, 40]


def test_interleaving_without_response_or_connection_identity_is_ambiguous():
    parser = Transport()
    a, b = events("one", "a"), events("two", "b")
    for i, (one, two) in enumerate(zip(a, b, strict=True)):
        parser.feed(ROUTE, 100 + i, one)
        parser.feed(ROUTE, 100 + i, two)
    assert len(parser.samples()) == 2
    assert all(s.tps is None for s in parser.samples())


@pytest.mark.parametrize("first", ["reasoning", "function_call", "custom_tool_call"])
def test_tool_first_excluded_but_later_zero_length_item_does_not_invalidate_stream(first):
    values = [
        {"type": "response.created", "response": {"id": "response"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"id": "a", "type": first},
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {"id": "a", "type": first},
        },
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {"id": "b", "type": "function_call"},
        },
        {
            "type": "response.output_item.done",
            "output_index": 1,
            "item": {"id": "b", "type": "function_call"},
        },
        events()[-1],
    ]
    parser = Transport()
    for i, value in enumerate(values):
        parser.feed(ROUTE, 100 + min(i, 3), dict(value, sequence_number=i))
    sample = parser.samples()[0]
    if first == "reasoning":
        assert sample.tps == 50
    else:
        assert sample.issue == "unconfirmed_tool_start" and sample.tps is None


def test_scope_parsing_and_payload_wrappers_do_not_search_nested_text():
    for marker, quote in [("websocket event", False), ("SSE event", True), ("event", False)]:
        parser = Transport()
        for i, event in enumerate(events()):
            raw = json.dumps(event)
            body = 'session_loop{thread_id=task}:turn{turn.id="turn"}: ' + marker + ": "
            body += json.dumps(raw) if quote else raw
            parser.accept("task", "process", 100 + i, TARGETS[0], body)
        assert parser.samples()[0].tps == 20
    parser = Transport()
    for i, event in enumerate(events()):
        body = 'turn{note="turn.id=turn"}: websocket event: ' + json.dumps(event)
        parser.accept("task", "process", 100 + i, TARGETS[0], body)
    assert not parser.samples()
    event = events()[-1]
    assert decode_event("event: response.completed\ndata: " + json.dumps(event) + "\n\n") == event
    with pytest.raises(ValueError):
        decode_event('{"type":"response.completed","type":"response.created"}')


def test_capacity_and_reset_preserve_excluded_response_identities():
    parser = Transport(max_active=1)
    parser.feed(ROUTE, 100, events("one")[0])
    parser.feed(ROUTE, 101, events("two")[0])
    assert parser.samples()[0].issue == "transport_capacity"
    parser.reset()
    assert len(parser.samples()) == 2
    assert not parser.active


def measurements():
    wire = feed(Transport())[0]
    legacy = Sample(
        "task",
        "turn",
        wire.response_aliases[0],
        106,
        100,
        102,
        Context(model="future-model"),
        parser_revision=REVISION,
        timing_source="item-event",
    )
    return legacy, wire


@pytest.mark.parametrize("reverse", [False, True])
def test_cross_source_aliases_preserve_one_response_in_stats_and_across_restart(tmp_path, reverse):
    legacy, wire = measurements()
    pair = [wire, legacy] if reverse else [legacy, wire]
    values = unique(pair)
    assert len(values) == 1 and values[0].tps == 20
    assert summarize(pair).tokens == 100
    history = History(tmp_path / "history.sqlite")
    history.append([pair[0]], now=110)
    history.close()
    history = History(tmp_path / "history.sqlite")
    history.append([pair[1]], now=110)
    saved = history.read()
    assert len(saved) == 1 and saved[0].tps == 20
    assert saved[0].context.actual_tier == "future-tier"
    assert set(saved[0].evidence_sources) == {SOURCE, "codex-local-log-v1"}
    history.close()
    history = History(tmp_path / "history.sqlite")
    history.append([legacy, wire], now=111)
    assert history.read() == saved
    export_csv(tmp_path / "out.csv", saved)
    history.close()
    assert b"future-model" in (tmp_path / "history.sqlite").read_bytes()
    assert b"PRIVATE" not in (tmp_path / "history.sqlite").read_bytes()
    assert wire.response_aliases[0] not in (tmp_path / "out.csv").read_text(encoding="utf-8-sig")


def test_alias_collision_and_usage_disagreement_never_enter_statistics(tmp_path):
    legacy, wire = measurements()
    assert unique([legacy, replace(wire, output_tokens=101)])[0].issue == "cross_source_conflict"
    other = replace(wire, response_id="different-response")
    assert all(s.tps is None for s in unique([wire, other, legacy]))
    history = History(tmp_path / "history.sqlite")
    try:
        history.append([wire], now=110)
        history.append([other], now=110)
        assert all(s.tps is None for s in history.read())
        history.append([legacy, wire], now=111)
        assert all(s.tps is None for s in history.read())
    finally:
        history.close()


def test_equal_tokens_and_timestamps_do_not_establish_an_alias():
    legacy, wire = measurements()
    unlinked = replace(wire, response_aliases=())
    assert len(unique([legacy, unlinked])) == 2
    assert len(unique([wire, replace(wire, task_id="another-task")])) == 2


@pytest.mark.parametrize("reverse", [False, True])
def test_transport_timing_preserves_later_exact_session_channel_and_request_evidence(reverse):
    legacy, wire = measurements()
    legacy = replace(
        legacy,
        context=replace(
            legacy.context, provider="provider", auth_mode="api-key", requested_tier="priority"
        ),
    )
    saved = unique([legacy, wire] if reverse else [wire, legacy])[0]
    assert saved.tps == 20 and saved.timing_source == "transport-log"
    assert saved.context.provider == "provider"
    assert saved.context.auth_mode == "api-key"
    assert saved.context.requested_tier == "priority"
    assert saved.context.actual_tier == "future-tier"


def test_opaque_provider_and_process_identifiers_are_not_assumed_to_be_uuids():
    parser = Transport()
    sample = feed(
        parser,
        events("provider/response:1", "provider/item:1"),
        route=("task", "turn", "session:worker", TARGETS[0], ()),
    )[0]
    assert sample.tps == 20


def test_full_replay_with_changed_delta_conflicts_without_retaining_text():
    parser = Transport()
    feed(parser)
    changed = events()
    changed[3]["delta"] = "DIFFERENT PRIVATE CONTENT"
    assert feed(parser, changed, start=200)[0].issue == "transport_conflict"
    assert "PRIVATE CONTENT" not in repr(parser.__dict__)


def test_conflicting_partial_replay_preserves_already_observed_usage():
    parser = Transport()
    feed(parser)
    feed(parser, events()[:4], start=200)
    parser.feed(ROUTE, 204, dict(events()[3], delta="conflicting replay"))
    parser.snapshot_pending()
    sample = parser.samples()[0]
    assert sample.issue == "transport_conflict" and sample.output_tokens == 100
    assert sample.tps is None


def test_unsupported_server_tool_does_not_become_a_misleading_later_index_conflict():
    values = events()
    values[-1:-1] = [
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {"id": "search", "type": "web_search_call"},
        },
        {
            "type": "response.output_item.done",
            "output_index": 1,
            "item": {"id": "search", "type": "web_search_call"},
        },
        {
            "type": "response.output_item.added",
            "output_index": 2,
            "item": {"id": "later", "type": "reasoning"},
        },
        {
            "type": "response.output_item.done",
            "output_index": 2,
            "item": {"id": "later", "type": "reasoning"},
        },
    ]
    values = [dict(value, sequence_number=i) for i, value in enumerate(values)]
    sample = feed(Transport(), values)[0]
    assert sample.issue == "transport_unsupported_item"
    assert sample.tps is None and sample.output_tokens == 100


def test_all_active_sequences_share_an_event_memory_budget():
    parser = Transport(max_events=3)
    parser.feed(ROUTE, 100, events("one")[0])
    parser.feed(ROUTE, 100, events("two")[0])
    parser.feed(ROUTE, 101, dict(events("one")[1], response_id="one"))
    parser.feed(ROUTE, 101, dict(events("two")[1], response_id="two"))
    assert sum(len(s.digests) for s in parser.active.values()) <= 3
    assert parser.samples()[0].issue == "transport_capacity"


def test_alias_repairs_missing_turn_only_when_the_item_identity_has_one_turn(tmp_path):
    legacy, wire = measurements()
    history = History(tmp_path / "history.sqlite")
    try:
        partial = replace(legacy, turn_id="", started_at=None)
        history.append([partial], now=110)
        history.append([wire], now=110)
        assert len(history.read()) == 1 and history.read()[0].tps == 20
        history.append([partial], now=111)
        assert len(history.read()) == 1
    finally:
        history.close()
