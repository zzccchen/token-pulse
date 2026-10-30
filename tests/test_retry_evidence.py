"""Explicit sampling retries break output intervals, irrespective of speed or length."""

import sqlite3
from dataclasses import replace

import pytest
from test_rollout import evidence, output, ready, usage

from token_pulse.backfill import Source, fingerprint
from token_pulse.domain import reconcile
from token_pulse.history import History
from token_pulse.logs import LogEvidence, SqliteLogs
from token_pulse.rollout import PARSER_REVISION

TARGET = "codex_core::responses_retry"
EPOCH = 1767225600


def retry_body(turn="turn", request="Sampling", message=None):
    message = message or "stream connection failed; waiting to retry"
    return (
        f"session_loop{{thread_id=task}}:turn{{turn.id={turn}}}:"
        f"run_sampling_request{{turn_id={turn}}}:"
        f"handle_response_stream_error{{request={request}}}: "
        f"{message} turn_id={turn} error=PRIVATE retry_delay=5s"
    )


@pytest.mark.parametrize(
    "message",
    [
        "stream connection failed; waiting to retry",
        "stream disconnected - retrying sampling request (2/5 in 1.25s)...",
    ],
)
def test_only_explicit_scoped_sampling_retry_supplies_an_interruption(message):
    logs = LogEvidence()
    body = retry_body(message=message)
    logs.accept("task", 10, TARGET, body)
    logs.accept("task", 10, TARGET, body)
    assert logs.interruptions == {("task", "turn"): [10]}
    assert logs.interrupted("task", "turn", 9, 11)
    assert "PRIVATE" not in repr(logs.__dict__)


@pytest.mark.parametrize(
    "task,target,body",
    [
        ("foreign", TARGET, retry_body()),
        ("task", "other_target", retry_body()),
        ("task", TARGET, retry_body(request="RemoteCompactionV2")),
        ("task", TARGET, retry_body().replace("turn_id=turn error", "turn_id=foreign error")),
        (
            "task",
            TARGET,
            retry_body().replace("run_sampling_request{turn_id=turn}", "sampling{turn_id=other}"),
        ),
        ("task", TARGET, retry_body().replace("request=Sampling", 'note="request=Sampling"')),
        (
            "task",
            TARGET,
            retry_body(message="ToolCall: stream connection failed; waiting to retry"),
        ),
        (
            "task",
            TARGET,
            retry_body().replace("handle_response_stream_error{request=Sampling}:", ""),
        ),
    ],
)
def test_other_tasks_compaction_nested_content_and_conflicts_cannot_invalidate_output(
    task, target, body
):
    logs = LogEvidence()
    logs.accept(task, 10, target, body)
    assert not logs.interruptions


def test_retry_index_is_bounded_deduplicated_and_accepts_out_of_order_evidence():
    logs = LogEvidence(capacity=2)
    for at in (12, 10, 12, 11):
        logs.accept("task", at, TARGET, retry_body())
    assert logs.interruptions == {("task", "turn"): [10, 11]}
    assert not logs.interrupted("task", "turn", 11, 20)
    assert not logs.interrupted("task", "another", 9, 20)


def test_retained_retries_outside_generic_log_window_still_correct_history(tmp_path):
    path = tmp_path / "logs.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE logs(id INTEGER PRIMARY KEY,ts,ts_nanos,"
            "thread_id,target,feedback_log_body)"
        )
        db.execute("INSERT INTO logs VALUES(1,10,0,?,?,?)", ("task", TARGET, retry_body()))
        db.execute("INSERT INTO logs VALUES(100000,20,0,'task','other','unrelated')")
    logs = LogEvidence()
    reader = SqliteLogs(path, logs, backfill=10)
    assert reader.poll() is None
    assert logs.interrupted("task", "turn", 9, 11)
    assert reader.poll() is None
    assert logs.interruptions == {("task", "turn"): [10]}


def sample_and_logs():
    parser = ready()
    output(parser, "first", 12)
    output(parser, "last", 30)
    usage(parser, second=31)
    return parser, evidence("first", second=10)


@pytest.mark.parametrize(
    "at,turn,excluded",
    [
        (11, "turn", True),
        (20, "turn", True),
        (9, "turn", False),
        (32, "turn", False),
        (20, "other", False),
    ],
)
def test_retry_inside_output_interval_is_excluded_without_a_duration_threshold(at, turn, excluded):
    parser, logs = sample_and_logs()
    before = parser.samples(logs)[0]
    logs.accept("task", EPOCH + at, TARGET, retry_body(turn=turn))
    after = parser.samples(logs)[0]
    assert before.tps == 5
    assert (after.issue == "interrupted_response") == excluded
    assert after.output_tokens == 100 and after.key == before.key
    assert (after.tps is None) == excluded
    if excluded:
        assert not after.response_aliases


def test_retry_after_last_output_before_delayed_usage_does_not_extend_or_invalidate_output():
    parser = ready()
    output(parser, second=12)
    logs = evidence()
    logs.accept("task", EPOCH + 20, TARGET, retry_body())
    usage(parser, second=30)
    assert parser.samples(logs)[0].tps == 50


def test_explicit_retry_corrects_saved_old_timing_and_missing_logs_cannot_restore_it(tmp_path):
    parser, logs = sample_and_logs()
    original = parser.samples(logs)[0]
    old = replace(original, parser_revision=PARSER_REVISION - 1)
    source = Source("task", tmp_path / "session.jsonl", old.context, (10, 20))
    before_stamp = fingerprint(source, logs)
    logs.accept("task", EPOCH + 20, TARGET, retry_body())
    corrected = parser.samples(logs)[0]
    assert fingerprint(source, logs) != before_stamp
    history = History(tmp_path / "history.sqlite")
    history.append([old], now=EPOCH + 40)
    history.append([corrected], now=EPOCH + 40)
    history.close()
    history = History(tmp_path / "history.sqlite")
    try:
        history.append([old, original], now=EPOCH + 41)
        saved = history.read()
        assert len(saved) == 1 and saved[0].issue == "interrupted_response"
        assert saved[0].output_tokens == 100 and saved[0].tps is None
    finally:
        history.close()


def test_complete_exact_transport_sequence_can_supply_independent_timing_after_retry():
    parser, logs = sample_and_logs()
    logs.accept("task", EPOCH + 20, TARGET, retry_body())
    interrupted = parser.samples(logs)[0]
    wire = replace(
        interrupted,
        issue=None,
        started_at=EPOCH + 25,
        timing_source="transport-log",
        source="codex-transport-log-v1",
    )
    assert reconcile(interrupted, wire).tps == 20
    assert reconcile(wire, interrupted).tps == 20


def test_saved_measurements_outside_live_cache_are_corrected_by_exact_retry_evidence(tmp_path):
    parser, logs = sample_and_logs()
    sample = parser.samples(logs)[0]
    unrelated = replace(sample, turn_id="other", response_id="other")
    complete_wire = replace(
        sample, response_id="wire", timing_source="transport-log", response_aliases=()
    )
    history = History(tmp_path / "history.sqlite")
    try:
        history.append([sample, unrelated, complete_wire], now=EPOCH + 40)
        before = history.read()
        assert history.enrich_interruptions(before, {}) == before
        logs.accept("task", EPOCH + 20, TARGET, retry_body())
        after = history.enrich_interruptions(before, logs.interruptions)
        assert sum(s.issue == "interrupted_response" for s in after) == 1
        assert sum(s.tps is not None for s in after) == 2
        assert sum(row[0] for row in history.connection.execute("SELECT valid FROM samples")) == 2
        assert history.read() == after
        assert history.enrich_interruptions(after, {}) == after
        assert history.enrich_interruptions(after, logs.interruptions) == after
    finally:
        history.close()
