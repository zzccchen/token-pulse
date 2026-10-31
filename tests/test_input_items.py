"""Input declarations and delivered agent mail are not generated output."""

import json
from dataclasses import replace

import pytest
from test_rollout import event, evidence, output, ready, usage

from token_pulse.history import History
from token_pulse.rollout import PARSER_REVISION
from token_pulse.tailer import json_object


def mail():
    return {
        "type": "agent_message",
        "id": "input-id",
        "author": "/parent",
        "recipient": "/child",
        "content": [{"type": "input_text", "text": "PRIVATE MESSAGE"}],
    }


def marker(parser, second=1):
    event(parser, "inter_agent_communication_metadata", {"trigger_turn": False}, second)


@pytest.mark.parametrize("at", [1, 12.5])
@pytest.mark.parametrize("encrypted", [False, True])
def test_exact_delivered_input_does_not_extend_output_or_poison_usage(at, encrypted):
    parser = ready()
    if at > 12:
        output(parser)
    marker(parser, at)
    item = mail()
    if encrypted:
        item["content"] = [{"type": "encrypted_content", "encrypted_content": "PRIVATE CIPHER"}]
    event(parser, "response_item", item, at)
    if at < 12:
        output(parser)
    usage(parser)
    measured = parser.samples(evidence())[0]
    assert measured.tps == 50
    assert measured.ended_at == 1767225612
    assert parser.responses[0].items == (("item", 1767225612),)
    assert "PRIVATE" not in repr(parser.__dict__)


@pytest.mark.parametrize("role", ["system", "developer"])
def test_tool_declarations_are_inputs_even_between_output_and_usage(role):
    parser = ready()
    output(parser)
    event(
        parser,
        "response_item",
        {"type": "additional_tools", "role": role, "tools": [{"description": "PRIVATE"}]},
        12.5,
    )
    usage(parser)
    assert parser.samples(evidence())[0].tps == 50
    assert "PRIVATE" not in repr(parser.__dict__)


@pytest.mark.parametrize("invalid", ["absent", "gap", "reset", "intervening", "malformed"])
def test_unproven_agent_messages_remain_unsupported(invalid):
    parser = ready()
    if invalid != "absent":
        marker(parser)
    if invalid == "gap":
        parser.damage("record_gap")
    elif invalid == "reset":
        parser.reset()
    elif invalid == "intervening":
        event(parser, "world_state", {}, 1)
    elif invalid == "malformed":
        event(parser, "inter_agent_communication_metadata", {"trigger_turn": 0}, 1)
    event(parser, "response_item", mail(), 2)
    output(parser)
    usage(parser)
    assert parser.samples(evidence())[0].tps is None


@pytest.mark.parametrize("change", ["duplicate", "content", "author", "ordinal", "time"])
def test_input_classification_rejects_ambiguous_metadata(change):
    parser = ready()
    mark = {
        "timestamp": "2026-01-01T00:00:01+00:00",
        "ordinal": 10,
        "type": "inter_agent_communication_metadata",
        "payload": {"trigger_turn": False},
    }
    record = {
        "timestamp": "2026-01-01T00:00:01+00:00",
        "ordinal": 11,
        "type": "response_item",
        "payload": mail(),
    }
    if change == "duplicate":
        mark["payload"] = json.loads(
            '{"trigger_turn":true,"trigger_turn":false}', object_pairs_hook=json_object
        )
    elif change == "content":
        record["payload"]["content"] = [{"type": "output_text", "text": "PRIVATE"}]
    elif change == "author":
        record["payload"]["author"] = None
    elif change == "ordinal":
        record["ordinal"] = 12
    else:
        record["timestamp"] = "2026-01-01T00:00:00+00:00"
    parser.feed(mark)
    parser.feed(record)
    output(parser)
    usage(parser)
    assert parser.samples(evidence())[0].tps is None


@pytest.mark.parametrize("role,tools", [("assistant", []), (None, []), ("developer", None)])
def test_unknown_tool_declaration_shapes_stay_excluded(role, tools):
    parser = ready()
    event(parser, "response_item", {"type": "additional_tools", "role": role, "tools": tools}, 1)
    output(parser)
    usage(parser)
    assert parser.samples(evidence())[0].exclusion == "unsupported_output_item"


def test_recovery_revalidates_same_response_without_duplicating_history(tmp_path):
    parser = ready()
    marker(parser)
    event(parser, "response_item", mail(), 1)
    output(parser)
    usage(parser)
    verified = parser.samples(evidence())[0]
    old = replace(verified, issue="unsupported_output_item", parser_revision=PARSER_REVISION - 1)
    history = History(tmp_path / "history.sqlite")
    history.append([old], now=1767225620)
    assert history.read()[0].tps is None
    history.append([verified, verified, old], now=1767225621)
    saved = history.read()
    assert len(saved) == 1 and saved[0].tps == 50
    assert saved[0].output_tokens == old.output_tokens
    history.close()
