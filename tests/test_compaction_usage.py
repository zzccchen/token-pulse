"""Compaction accounting is not an ordinary output with missing items."""

import csv
from contextlib import closing
from dataclasses import replace

import pytest
from test_rollout import event, evidence, output, ready, usage

from token_pulse.domain import reconcile
from token_pulse.history import History, export_csv
from token_pulse.logs import LogEvidence
from token_pulse.tailer import AmbiguousObject


def compact(parser, *, response="response", task="task", turn="turn", tokens=100, at=15):
    event(
        parser,
        "compacted",
        {
            "compaction_response_id": response,
            "latest_token_usage_record": {
                "thread_id": task,
                "turn_id": turn,
                "response_id": response,
                "usage": {"output_tokens": tokens},
            },
            "replacement_history": [{"type": "message", "content": "PRIVATE CONTENT"}],
        },
        at,
    )


def test_exact_compaction_usage_is_classified_without_fabricating_timing(tmp_path):
    parser, logs = ready(), LogEvidence()
    usage(parser)
    old = parser.samples(logs)[0]
    assert old.exclusion == "missing_output_items"
    compact(parser)
    sample = parser.samples(logs)[0]
    assert sample.key == old.key and sample.output_tokens == old.output_tokens
    assert sample.response_kind == "compaction" and sample.exclusion == "compaction_usage"
    assert sample.token_scope == "total_output"
    assert sample.tps is None and sample.started_at is None
    assert "PRIVATE CONTENT" not in repr(parser.__dict__)
    with closing(History(tmp_path / "history.sqlite")) as history:
        history.append([old, sample], now=1767225620)
        assert len(history.read()) == 1
        assert history.read()[0].response_kind == "compaction"
    with closing(History(tmp_path / "history.sqlite")) as restarted:
        saved = restarted.read()
        assert len(saved) == 1 and saved[0].response_kind == "compaction"
        path = tmp_path / "export.csv"
        export_csv(path, saved)
    rows = list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0]["response_kind"] == "compaction"
    assert rows[0]["token_scope"] == "total_output"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"response": "other"},
        {"task": "parent"},
        {"turn": "other"},
        {"tokens": 101},
        {"tokens": True},
        {"at": 12},
    ],
)
def test_nearby_or_conflicting_usage_is_not_a_compaction_match(kwargs):
    parser = ready()
    usage(parser)
    compact(parser, **kwargs)
    assert parser.samples(LogEvidence())[0].response_kind is None


def test_compaction_match_survives_recovery_batch_drain_and_is_idempotent():
    parser = ready()
    usage(parser)
    parser.responses.clear()
    compact(parser)
    sample = parser.samples(LogEvidence())[0]
    assert sample.exclusion == "compaction_usage"
    parser.corrections.clear()
    compact(parser)
    assert parser.samples(LogEvidence()) == []


def test_unowned_snapshot_and_content_cannot_classify_usage():
    parser = ready()
    usage(parser)
    event(
        parser,
        "compacted",
        {
            "message": 'compaction_response_id="response"',
            "compaction_response_id": "response",
            "latest_token_usage_record": AmbiguousObject(
                thread_id="task",
                turn_id="turn",
                response_id="response",
                usage={"output_tokens": 100},
            ),
        },
        15,
    )
    assert parser.samples(LogEvidence())[0].exclusion == "missing_output_items"


def test_purpose_enrichment_preserves_saved_timing_and_survives_missing_new_evidence():
    parser = ready()
    usage(parser)
    compact(parser)
    classified = parser.samples(LogEvidence())[0]
    old = replace(
        classified,
        parser_revision=10,
        response_kind=None,
        issue=None,
        started_at=classified.ended_at - 2,
    )
    assert old.tps == 50
    merged = reconcile(old, classified)
    assert merged.tps == 50 and merged.response_kind == "compaction"
    missing = replace(classified, response_kind=None, issue="missing_output_items")
    assert reconcile(classified, missing).exclusion == "compaction_usage"
    assert reconcile(classified, missing).response_kind == "compaction"


def test_parent_compaction_snapshot_cannot_classify_child_usage():
    parser = ready()
    usage(parser)
    event(parser, "session_meta", {"id": "parent"}, 14)
    compact(parser)
    assert parser.samples(LogEvidence())[0].response_kind is None


@pytest.mark.parametrize("ordinal", [None, 49])
def test_missing_or_copied_ordinal_compaction_cannot_classify_usage(ordinal):
    parser = ready()
    usage(parser)
    event(parser, "session_meta", {"id": "task", "subagent_history_start_ordinal": 50}, 14)
    record = {
        "type": "compacted",
        "timestamp": "2026-01-01T00:00:15Z",
        "payload": {
            "compaction_response_id": "response",
            "latest_token_usage_record": {
                "thread_id": "task",
                "turn_id": "turn",
                "response_id": "response",
                "usage": {"output_tokens": 100},
            },
        },
    }
    if ordinal is not None:
        record["ordinal"] = ordinal
    parser.feed(record)
    assert parser.samples(LogEvidence())[0].response_kind is None


def test_compaction_snapshot_does_not_remove_already_collected_output_items():
    parser = ready()
    output(parser)
    usage(parser)
    before = parser.samples(evidence())[0]
    compact(parser)
    assert parser.samples(evidence())[0] == before


@pytest.mark.parametrize("reverse", [False, True])
def test_compaction_purpose_does_not_bypass_cross_source_usage_conflicts(reverse):
    parser = ready()
    usage(parser)
    compact(parser)
    classified = parser.samples(LogEvidence())[0]
    transport = replace(
        classified,
        source="codex-transport-log-v1",
        timing_source="transport-log",
        response_kind=None,
        issue=None,
        output_tokens=101,
        started_at=classified.ended_at - 2,
    )
    pair = (transport, classified) if reverse else (classified, transport)
    merged = reconcile(*pair)
    assert merged.response_kind == "compaction"
    assert merged.exclusion == "cross_source_conflict" and merged.tps is None
