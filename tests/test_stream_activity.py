"""Activity corroborates an exact lifecycle, never supplies a guessed boundary."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from test_rollout import completed_item, event, ready, usage

from token_pulse.backfill import Source, fingerprint
from token_pulse.desktop_logs import DesktopFile, DesktopLogs, DesktopTail
from token_pulse.domain import Context, reconcile
from token_pulse.evidence_archive import EvidenceArchive
from token_pulse.history import History
from token_pulse.logs import LogEvidence, Start
from token_pulse.stream_activity import Activity
from token_pulse.transport import REVISION, SOURCE

BASE = 1767225600
NOW = BASE + 20


def response(*, kind="reasoning", start=2, end=10):
    parser = ready()
    completed_item(
        parser, kind="Reasoning" if kind == "reasoning" else "AgentMessage", start=start, end=end
    )
    event(parser, "response_item", {"type": kind, "id": "item", "role": "assistant"}, end)
    usage(parser)
    return parser


def activity(logs, *, at=5, task="task", turn="turn", item="item", ambiguous=False):
    logs.record_activity(task, turn, item, Activity((float(BASE + at),), ambiguous))


def line(body, second=5, logger="host-app-server-manager", level="info"):
    at = datetime.fromtimestamp(BASE + second, UTC).isoformat()
    return f"{at} {level} [{logger}] {body}\n".encode()


def binding(host="local", task="task", second=1):
    return line(
        f"mcp_server_startup_status_updated error=null failureReason=null hostId={host} "
        f"server=example status=ready threadId={task}",
        second,
        "AppServerConnection",
    )


def notification(*, second=5, **fields):
    payload = {"threadId": "task", "turnId": "turn", "itemId": "item", "summaryIndex": 0, **fields}
    return line("Reasoning summary part added payload=" + json.dumps(payload), second)


def completion(*, second=12, task="task", turn="turn", item="item", parts=None):
    parts = [] if parts is None else parts
    return line(
        f"Reasoning summary item completed itemId={item} summary={json.dumps(parts)} "
        f"summaryPartCount={len(parts)} threadId={task} turnId={turn}",
        second,
    )


@pytest.mark.parametrize("at,done", [(10, 12), (11, 12), (11, 11)])
def test_queued_activity_can_prove_origin_via_ordered_completion(tmp_path, at, done):
    logs, reader = LogEvidence(), DesktopFile(tmp_path / "notifications.log")
    for raw in [binding(), notification(second=at), completion(second=done)]:
        reader.accept(reader.tail.decode_line(raw), logs)
    result = response().samples(logs)[0]
    assert result.tps == 12.5
    assert result.started_at == BASE + 2 and result.ended_at == BASE + 10
    assert logs.activity("task", "turn", "item").completions == ((BASE + at, BASE + done),)


@pytest.mark.parametrize(
    "done",
    [
        completion(item="other"),
        completion(task="other"),
        completion(turn="other"),
        completion(second=9),
    ],
)
def test_delayed_activity_needs_exact_ordered_completion(tmp_path, done):
    logs, reader = LogEvidence(), DesktopFile(tmp_path / "notifications.log")
    for raw in [binding(), notification(second=11), done]:
        reader.accept(reader.tail.decode_line(raw), logs)
    assert response().samples(logs)[0].tps is None


def test_delivery_pairs_cannot_cross_files_gaps_or_reverse_event_order(tmp_path):
    logs, one, two = LogEvidence(), DesktopFile(tmp_path / "one"), DesktopFile(tmp_path / "two")
    for reader, raw in [
        (one, binding()),
        (one, notification(second=11)),
        (two, binding()),
        (two, completion()),
    ]:
        reader.accept(reader.tail.decode_line(raw), logs)
    assert response().samples(logs)[0].tps is None
    one.gap()
    for raw in [binding(), completion(), notification(second=11)]:
        one.accept(one.tail.decode_line(raw), logs)
    assert response().samples(logs)[0].tps is None
    assert response(start=10, end=10).samples(logs)[0].tps is None


def test_completion_content_never_creates_activity(tmp_path):
    logs, reader = LogEvidence(), DesktopFile(tmp_path / "notifications.log")
    for raw in [binding(), completion(parts=[notification().decode()])]:
        reader.accept(reader.tail.decode_line(raw), logs)
    assert not logs.activities
    with pytest.raises(ValueError):
        reader.tail.decode_line(completion().replace(b"summaryPartCount=0", b"summaryPartCount=1"))


def test_delivery_pair_survives_restart_and_late_host_conflict(tmp_path):
    path = tmp_path / "history.sqlite"
    with closing(History(path)) as history:
        logs, reader = LogEvidence(), DesktopFile(tmp_path / "notifications.log")
        archive = EvidenceArchive(history.connection, history.anonymous_id, tmp_path)
        archive.restore(logs, now=NOW)
        for raw in [binding(), notification(second=11), completion()]:
            reader.accept(reader.tail.decode_line(raw), logs)
        archive.save(logs, now=NOW)
    with closing(History(path)) as history:
        restored = LogEvidence()
        archive = EvidenceArchive(history.connection, history.anonymous_id, tmp_path)
        archive.restore(restored, now=NOW)
        assert response().samples(restored)[0].tps == 12.5
        reader.accept(reader.tail.decode_line(binding("remote", second=15)), logs)
        archive.save(logs, now=NOW)
        archive.restore(restored, now=NOW)
        assert response().samples(restored)[0].exclusion == "ambiguous_stream_activity"


def test_revision_one_activity_archive_migrates_without_losing_saved_proof(tmp_path):
    with closing(History(tmp_path / "history.sqlite")) as history:
        db = history.connection
        db.execute(
            "CREATE TABLE stream_activity(scope,task,turn,item,times,ambiguous,last_at,revision, "
            "PRIMARY KEY(scope,task,turn,item))"
        )
        anon = history.anonymous_id
        db.execute(
            "INSERT INTO stream_activity VALUES(?,?,?,?,?,?,?,1)",
            (
                anon("evidence-root", str(tmp_path.resolve())),
                anon("task", "task"),
                anon("turn", "turn"),
                anon("item", "item"),
                json.dumps([BASE + 5]),
                0,
                BASE + 5,
            ),
        )
        db.commit()
        logs = LogEvidence()
        archive = EvidenceArchive(db, anon, tmp_path)
        archive.restore(logs, now=NOW)
        assert response().samples(logs)[0].tps == 12.5
        assert logs.activity("task", "turn", "item").completions == ()


def test_activity_validates_original_start_not_notification_time():
    parser, logs = response(), LogEvidence()
    original = parser.samples(logs)[0]
    assert original.exclusion == "unverified_lifecycle_start"
    activity(logs)
    result = parser.samples(logs)[0]
    assert result.key == original.key
    assert result.output_tokens == original.output_tokens == 100
    assert result.started_at == original.started_at == BASE + 2
    assert result.ended_at == original.ended_at == BASE + 10
    assert result.tps == 12.5
    assert result.timing_source == "corroborated-item-event"
    assert reconcile(original, result) == result


@pytest.mark.parametrize(
    "kwargs",
    [
        {"at": 1},
        {"at": 2},
        {"at": 10},
        {"at": 11},
        {"task": "other"},
        {"turn": "other"},
        {"item": "later"},
        {"ambiguous": True},
    ],
)
def test_other_scopes_replay_endpoints_and_conflicts_cannot_validate(kwargs):
    parser, logs = response(), LogEvidence()
    activity(logs, **kwargs)
    assert parser.samples(logs)[0].tps is None


@pytest.mark.parametrize("kind,start,end", [("message", 2, 10), ("reasoning", 5, 5)])
def test_wrong_kind_and_collapsed_initial_span_remain_excluded(kind, start, end):
    parser, logs = response(kind=kind, start=start, end=end), LogEvidence()
    activity(logs)
    assert parser.samples(logs)[0].tps is None


def test_retry_and_later_contradictory_boundary_still_invalidate():
    parser, logs = response(), LogEvidence()
    activity(logs)
    logs.record_interruption("task", "turn", BASE + 6)
    assert parser.samples(logs)[0].exclusion == "interrupted_response"
    logs = LogEvidence()
    activity(logs)
    parser = ready()
    completed_item(parser, kind="Reasoning", start=2, end=10)
    event(parser, "response_item", {"type": "reasoning", "id": "item"}, 10)
    event(parser, "response_item", {"type": "custom_tool_call", "id": "tool"}, 12)
    usage(parser)
    logs.starts["task", "tool"] = Start(BASE + 1, "turn")
    assert parser.samples(logs)[0].exclusion == "invalid_duration"


def test_late_conflict_withdraws_only_dependent_timing():
    parser, logs = response(), LogEvidence()
    activity(logs)
    good = parser.samples(logs)[0]
    activity(logs, ambiguous=True)
    bad = parser.samples(logs)[0]
    assert reconcile(good, bad).exclusion == "ambiguous_stream_activity"
    logs.starts["task", "item"] = Start(BASE + 2, "turn")
    independent = parser.samples(logs)[0]
    assert independent.tps == 12.5
    assert reconcile(bad, independent).tps == 12.5
    assert reconcile(independent, bad).tps == 12.5


@pytest.mark.parametrize("source", ["stream-log", "recovered-stream-log", "transport-log"])
def test_activity_enrichment_cannot_replace_a_saved_independent_interval(source):
    logs = LogEvidence()
    activity(logs)
    incoming = response().samples(logs)[0]
    saved = replace(incoming, started_at=BASE + 1, timing_source=source, parser_revision=9)
    result = reconcile(saved, incoming)
    assert (result.started_at, result.ended_at, result.tps, result.timing_source) == (
        saved.started_at,
        saved.ended_at,
        saved.tps,
        source,
    )


def test_complete_transport_replaces_corroboration_but_conflicting_usage_does_not():
    logs = LogEvidence()
    activity(logs)
    sample = response().samples(logs)[0]
    transport = replace(
        sample, source=SOURCE, timing_source="transport-log", parser_revision=REVISION
    )
    assert reconcile(sample, transport).timing_source == "transport-log"
    conflict = replace(transport, output_tokens=200)
    assert reconcile(sample, conflict).exclusion == "cross_source_conflict"


@pytest.mark.parametrize(
    "prefix", [b"", binding("remote"), binding(task="other"), binding(second=6)]
)
def test_file_requires_prior_exact_local_binding(tmp_path, prefix):
    reader, logs = DesktopFile(tmp_path / "input.log"), LogEvidence()
    for raw in prefix.splitlines(keepends=True) + [notification()]:
        reader.accept(reader.tail.decode_line(raw), logs)
    assert not logs.activities


def test_bindings_do_not_cross_file_or_gap_and_conflicts_survive_gap(tmp_path):
    one, two, logs = DesktopFile(tmp_path / "one"), DesktopFile(tmp_path / "two"), LogEvidence()
    one.accept(one.tail.decode_line(binding()), logs)
    two.accept(two.tail.decode_line(notification()), logs)
    assert not logs.activities
    one.accept(one.tail.decode_line(notification()), logs)
    assert response().samples(logs)[0].tps == 12.5
    one.accept({"kind": "gap"}, logs)
    one.accept(one.tail.decode_line(binding("remote", second=7)), logs)
    assert response().samples(logs)[0].exclusion == "ambiguous_stream_activity"


@pytest.mark.parametrize(
    "raw",
    [
        notification(summaryIndex=True),
        notification(summaryIndex=-1),
        notification(turnId=None),
        notification(extra="unsupported"),
        notification().replace(b'"summaryIndex": 0', b'"summaryIndex": 0, "itemId":"item"'),
        notification().replace(b"\n", b" unrecognized=trailer\n"),
    ],
)
def test_malformed_outer_fields_cannot_supply_activity(tmp_path, raw):
    with pytest.raises(ValueError):
        DesktopTail(tmp_path / "unused").decode_line(raw)


def test_error_text_and_unrelated_logs_are_not_evidence(tmp_path):
    tail = DesktopTail(tmp_path / "unused")
    assert tail.decode_line(line("arbitrary " + notification().decode())) is None
    forged = binding().replace(b"error=null", b'error="hostId=remote threadId=other"')
    assert tail.decode_line(forged)["host"] == "local"
    assert tail.decode_line(forged)["task"] == "task"
    assert tail.decode_line(binding().replace(b"error=null", b"error=unquoted")) == {"kind": "gap"}


def test_archive_restart_preserves_identity_fingerprint_and_measurement(tmp_path):
    path = tmp_path / "history.sqlite"
    with closing(History(path)) as history:
        logs = LogEvidence()
        archive = EvidenceArchive(history.connection, history.anonymous_id, tmp_path)
        archive.restore(logs, now=NOW)
        source = Source("task", tmp_path / "session", Context(), (10, 20))
        without_activity = fingerprint(source, logs)
        activity(logs)
        before = fingerprint(source, logs)
        assert before != without_activity
        archive.save(logs, now=NOW)
        history.append(response().samples(logs), now=NOW)
        assert fingerprint(source, logs) == before
    with closing(History(path)) as history:
        logs = LogEvidence()
        archive = EvidenceArchive(history.connection, history.anonymous_id, tmp_path)
        archive.restore(logs, now=NOW)
        assert fingerprint(source, logs) == before
        assert response().samples(logs)[0].tps == 12.5
        history.append(response().samples(logs), now=NOW)
        assert len(history.read()) == 1
        lost_source = replace(response().samples(LogEvidence())[0], parser_revision=11)
        history.append([lost_source], now=NOW)
        assert history.read()[0].tps == 12.5
        dump = "\n".join(history.connection.iterdump())
        assert all(
            value not in dump
            for value in ('"task"', '"turn"', '"item"', str(tmp_path), "summaryIndex")
        )
        assert logs.activity("different", "turn", "item") is None
        assert logs.activity("task", "wrong", "item") is None


def test_incremental_partial_rotation_and_saved_file_resume(tmp_path):
    folder = tmp_path / "2026/01/01"
    folder.mkdir(parents=True)
    path = folder / "desktop.log"
    path.write_bytes(binding() + notification()[:-1])
    logs = LogEvidence()
    reader = DesktopLogs(tmp_path, logs)
    reader.checkpoint_key = lambda kind, path: kind + path.name
    while reader.pending or not reader.files:
        reader.poll(NOW, budget=17)
        if next(iter(reader.files.values())).tail.offset == path.stat().st_size:
            break
    assert not logs.activities and not reader.completed
    with path.open("ab") as stream:
        stream.write(b"\n")
    reader.poll(NOW)
    assert response().samples(logs)[0].tps == 12.5
    checkpoints = {k: v[0] for k, v in reader.completed.items()}
    restarted = DesktopLogs(tmp_path, LogEvidence())
    restarted.checkpoint_key = reader.checkpoint_key
    restarted.checkpoints = checkpoints
    restarted.poll(NOW)
    assert next(iter(restarted.files.values())).tail.offset == 0
    # Changed files replay their complete ownership prefix after a restart.
    with path.open("ab") as stream:
        stream.write(notification(second=6))
    restarted.poll(NOW)
    assert restarted.evidence.activity("task", "turn", "item").times == (BASE + 5, BASE + 6)
    path.write_bytes(notification(itemId="new"))
    restarted.poll(NOW)
    assert restarted.evidence.activity("task", "turn", "new") is None


def test_duplicate_witnesses_do_not_duplicate_response_or_change_timing(tmp_path):
    logs, reader = LogEvidence(), DesktopFile(tmp_path / "unused")
    for raw in [binding(), notification(), notification(), notification(second=6)]:
        reader.accept(reader.tail.decode_line(raw), logs)
    assert logs.activity("task", "turn", "item").times == (BASE + 5, BASE + 6)
    assert len(response().samples(logs)) == 1
    assert response().samples(logs)[0].tps == 12.5


def test_failed_activity_write_retries_without_acknowledging_evidence(tmp_path):
    with closing(History(tmp_path / "history.sqlite")) as history:
        logs = LogEvidence()
        archive = EvidenceArchive(history.connection, history.anonymous_id, tmp_path)
        archive.restore(logs, now=NOW)
        activity(logs)
        history.connection.execute("PRAGMA query_only=ON")
        with pytest.raises(sqlite3.OperationalError):
            archive.save(logs, now=NOW)
        assert not archive.activity_archive.seen
        history.connection.execute("PRAGMA query_only=OFF")
        archive.save(logs, now=NOW)
        restored = LogEvidence()
        archive.restore(restored, now=NOW)
        assert response().samples(restored)[0].tps == 12.5
        # A different selected source cannot inherit the proof.
        other = EvidenceArchive(history.connection, history.anonymous_id, tmp_path / "other")
        other.restore(restored, now=NOW)
        assert response().samples(restored)[0].tps is None


def test_activity_capacity_and_retry_floor_cannot_create_proof(tmp_path):
    logs, reader = LogEvidence(capacity=1), DesktopFile(tmp_path / "unused")
    for raw in [binding(), notification(), notification(itemId="later")]:
        reader.accept(reader.tail.decode_line(raw), logs)
    assert reader.blocked
    assert response().samples(logs)[0].tps is None
    logs = LogEvidence()
    activity(logs)
    logs.archive_floor = BASE + 5
    assert response().samples(logs)[0].tps is None


def test_window_discovery_is_bounded_and_pending_bytes_are_budgeted(tmp_path):
    folder = tmp_path / "2026/01/01"
    folder.mkdir(parents=True)
    for i in range(3):
        (folder / f"source-{i}.log").write_bytes(binding() + notification())
    old = tmp_path / "2025/10/01"
    old.mkdir(parents=True)
    (old / "unrelated.log").write_bytes(binding() + notification())
    logs = LogEvidence()
    reader = DesktopLogs(tmp_path, logs)
    reader.poll(NOW, budget=15)
    assert len(reader.files) == 3 and reader.pending == 3
    assert sum(file.tail.offset for file in reader.files.values()) == 15
    for _ in range(100):
        reader.poll(NOW, budget=15)
        if not reader.pending:
            break
    assert not reader.pending and len(logs.activities) == 1
