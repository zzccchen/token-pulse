"""Synthetic cross-format regressions; no private source records or version gates."""

from dataclasses import replace

import pytest
from test_rollout import (
    completed_item,
    event,
    evidence,
    legacy_count,
    output,
    ready,
    usage,
)

from token_pulse.domain import reconcile
from token_pulse.history import History
from token_pulse.logs import LogEvidence
from token_pulse.rollout import PARSER_REVISION, REJECTIONS, Rollout


@pytest.mark.parametrize("kind", ["web_search_call", "tool_search_call", "future_output"])
@pytest.mark.parametrize("position", ["before", "after"])
def test_unknown_output_cannot_shorten_the_total_response_interval(kind, position):
    parser = ready()
    if position == "after":
        output(parser, second=11)
    event(parser, "response_item", {"type": kind, "id": "unknown"}, 12)
    if position == "before":
        output(parser, second=13)
    usage(parser, second=14)
    sample = parser.samples(evidence())[0]
    assert sample.output_tokens == 100
    assert sample.tps is None
    assert sample.exclusion == "unsupported_output_item"


@pytest.mark.parametrize("total", [None, -1, True, "100", float("nan")])
def test_invalid_legacy_counter_never_proves_a_matching_token_scope(total):
    parser = ready()
    output(parser)
    legacy_count(parser, 100, total, 13)
    assert parser.samples(evidence())[0].exclusion == "missing_usage_baseline"


@pytest.mark.parametrize("total", [None, 100, 500])
def test_explicit_usage_supersedes_provisional_identity_even_across_persistence(tmp_path, total):
    parser = ready()
    output(parser)
    legacy_count(parser, 100, total, 13)
    history = History(tmp_path / "history.sqlite")
    try:
        provisional = parser.samples(evidence())[0]
        history.append([provisional], now=1767225620)
        usage(parser, second=14)
        values = parser.samples(evidence())
        accepted = [s for s in values if s.issue not in REJECTIONS]
        rejected = [s for s in values if s.issue in REJECTIONS]
        assert len(accepted) == len(rejected) == 1
        assert accepted[0].response_id == "response"
        assert accepted[0].tps == 50
        assert rejected[0].issue == "superseded_usage"
        history.reject(rejected, now=1767225620)
        history.append(accepted + [provisional], now=1767225620)
        assert len(history.read()) == 1
        assert history.read()[0].tps == 50
    finally:
        history.close()
    history = History(tmp_path / "history.sqlite")
    try:
        history.append([provisional], now=1767225621)
        assert len(history.read()) == 1
    finally:
        history.close()


def test_explicit_usage_without_items_is_retained_once_and_foreign_usage_is_ignored():
    parser = ready()
    usage(parser)
    usage(parser)
    event(
        parser,
        "token_usage_record",
        {"thread_id": "another-task", "response_id": "foreign", "usage": {"output_tokens": 100}},
        14,
    )
    samples = parser.samples(LogEvidence())
    assert len(samples) == 1
    assert samples[0].response_id == "response"
    assert samples[0].output_tokens == 100
    assert samples[0].exclusion == "missing_output_items"


def test_duplicate_explicit_usage_does_not_consume_next_response_output():
    parser = ready()
    output(parser)
    usage(parser)
    output(parser, "next", 20)
    usage(parser, second=21)
    usage(parser, "next-response", 22)
    observed = [s for s in parser.samples(evidence("next", 18)) if s.issue not in REJECTIONS]
    assert len(observed) == 2
    assert observed[-1].tps == 50


def test_later_independent_start_validates_history_without_a_parser_version_change(tmp_path):
    parser = ready()
    completed_item(parser, start=11)
    output(parser)
    usage(parser)
    original = parser.samples(LogEvidence())[0]
    revised = parser.samples(evidence(second=10))[0]
    assert original.tps is None and revised.tps == 50
    assert original.exclusion == "unverified_lifecycle_start"
    assert reconcile(original, revised).tps == 50
    assert reconcile(revised, original).tps == 50
    history = History(tmp_path / "history.sqlite")
    try:
        history.append([original], now=1767225620)
        history.append([revised], now=1767225620)
        history.append(parser.samples(LogEvidence()), now=1767225620)
        assert history.read()[0].tps == 50
    finally:
        history.close()


@pytest.mark.parametrize("issue", ["unsupported_output_item", "missing_usage_baseline"])
def test_proven_revision_five_exclusion_replaces_old_valid_speed(issue):
    parser = ready()
    output(parser)
    usage(parser)
    old = replace(parser.samples(evidence())[0], parser_revision=4)
    revised = replace(old, issue=issue, parser_revision=PARSER_REVISION)
    assert reconcile(old, revised).exclusion == issue
    assert reconcile(revised, old).exclusion == issue
    assert reconcile(old, replace(revised, issue="missing_start", started_at=None)) == old


def test_ordinal_owned_prefix_is_withdrawn_even_with_child_owner_and_current_timestamps():
    parser = Rollout("task")
    records = [
        ("session_meta", {"id": "task", "subagent_history_start_ordinal": 4}),
        ("turn_context", {"turn_id": "parent-turn", "model": "parent-model"}),
        ("response_item", {"type": "message", "role": "assistant", "id": "parent-item"}),
        ("token_usage_record", {"response_id": "parent-response", "usage": {"output_tokens": 30}}),
        ("turn_context", {"turn_id": "turn", "model": "model-a"}),
        ("response_item", {"type": "message", "role": "assistant", "id": "item"}),
        ("token_usage_record", {"response_id": "response", "usage": {"output_tokens": 100}}),
    ]
    for ordinal, (kind, payload) in enumerate(records):
        parser.feed(
            {
                "ordinal": ordinal,
                "type": kind,
                "payload": payload,
                "timestamp": f"2026-01-01T00:00:{ordinal + 10:02d}Z",
            }
        )
    samples = parser.samples(evidence(second=14))
    assert samples[0].issue == "inherited_history"
    assert samples[-1].tps == 100
    assert samples[-1].context.model == "model-a"


def test_missing_output_identity_retains_usage_but_not_a_suffix_speed():
    parser = ready()
    event(parser, "response_item", {"type": "reasoning"}, 11)
    output(parser)
    usage(parser)
    assert parser.samples(evidence())[0].exclusion == "missing_item_id"


def test_separated_or_unscoped_usage_does_not_join_by_equal_tokens():
    for intervening in (True, False):
        parser = ready()
        output(parser)
        legacy_count(parser, 100, 100, 13)
        if intervening:
            event(parser, "event_msg", {"type": "task_complete"}, 14)
            usage(parser, second=15)
        else:
            event(
                parser,
                "token_usage_record",
                {"response_id": "response", "usage": {"output_tokens": 100}},
                15,
            )
        values = parser.samples(evidence())
        assert len(values) == 2
        assert not any(s.issue == "superseded_usage" for s in values)
        assert values[-1].exclusion == "missing_output_items"


def test_explicit_usage_keeps_its_turn_when_the_context_is_missing_or_conflicting():
    parser = Rollout("task")
    event(
        parser,
        "token_usage_record",
        {"response_id": "response", "turn_id": "reported-turn", "usage": {"output_tokens": 10}},
        15,
    )
    sample = parser.samples(LogEvidence())[0]
    assert sample.turn_id == "reported-turn"
    assert sample.tps is None


def test_superseded_legacy_cannot_return_through_a_later_stale_rejection(tmp_path):
    parser = ready()
    output(parser)
    legacy_count(parser, 100, 100, 13)
    old = parser.samples(evidence())[0]
    history = History(tmp_path / "history.sqlite")
    try:
        history.append([old], now=1767225620)
        history.reject([replace(old, issue="superseded_usage")], now=1767225620)
        history.reject([replace(old, issue="stale_usage_snapshot")], now=1767225620)
        history.append([old], now=1767225620)
        assert not history.read()
    finally:
        history.close()
