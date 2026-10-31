"""Known writer segments preserve evidence without inventing cross-file owners."""

import json
from contextlib import closing

import pytest
from test_rollout import completed_item, event, usage
from test_stream_activity import BASE, NOW, binding, completion, line, notification, response

from token_pulse.desktop_logs import MAX_LINE, DesktopLogs, DesktopTail
from token_pulse.evidence_archive import EvidenceArchive
from token_pulse.history import History
from token_pulse.logs import LogEvidence
from token_pulse.rollout import Rollout

WRITER = "codex-desktop-00000000-0000-0000-0000-000000000001-10-t0-i1"


def source(root, body, *, day="2026/01/01", clock="000001", index=0, writer=WRITER):
    path = root / day / f"{writer}-{clock}-{index}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def drain(reader, now=NOW, budget=8 * 1024 * 1024):
    for _ in range(1000):
        assert not reader.poll(now, budget=budget)
        if not reader.pending:
            return
    pytest.fail("Reader failed to drain within its byte budget")


def daily(root, *, second=5):
    before = source(root, binding(second=-5), day="2025/12/31", clock="235955")
    after = source(root, notification(second=second), clock="000005")
    return before, after


def test_daily_continuity_preserves_exact_origin_and_budget(tmp_path):
    daily(tmp_path)
    logs = LogEvidence()
    reader = DesktopLogs(tmp_path, logs)
    drain(reader, budget=17)
    result = response().samples(logs)[0]
    assert result.tps == 12.5
    assert (result.started_at, result.ended_at) == (BASE + 2, BASE + 10)
    assert len(logs.activities) == 1
    offsets = [r.tail.offset for r in reader.files.values()]
    drain(reader)
    assert [r.tail.offset for r in reader.files.values()] == offsets


def test_size_rotation_requires_adjacency_and_real_rollover(tmp_path, monkeypatch):
    monkeypatch.setattr("token_pulse.desktop_logs.SEGMENT_BYTES", 1024)
    filler = line("ignored", second=4)
    prefix = binding() + filler[:-1] + b" " * (1024 - len(binding()) - len(filler)) + b"\n"
    source(tmp_path, prefix)
    source(tmp_path, notification(), index=1)
    reader = DesktopLogs(tmp_path, LogEvidence())
    drain(reader)
    assert response().samples(reader.evidence)[0].tps == 12.5


@pytest.mark.parametrize(
    "day,clock,index,writer,second",
    [
        ("2026/01/02", "000005", 0, WRITER, 86405),  # Missing day.
        ("2026/01/01", "000005", 1, WRITER, 5),  # Missing new-day prefix.
        ("2026/01/01", "000005", 0, WRITER.replace("-10-", "-11-"), 5),
        ("2026/01/01", "000005", 0, WRITER.replace("-t0-", "-t1-"), 5),
        ("2026/01/01", "000005", 0, WRITER.replace("-i1", "-i2"), 5),
        ("2026/01/01", "000005", 0, WRITER.replace("000001-10", "000002-10"), 5),
        ("2026/01/01", "000005", 0, WRITER, -6),  # Reversed timestamp / wrong day.
        ("2025/12/31", "235955", 1, WRITER, -4),  # No size rollover.
        ("2025/12/31", "235955", 2, WRITER, -4),  # Missing size segment.
        ("2025/12/31", "235956", 0, WRITER, -4),  # Conflicting daily prefix.
    ],
)
def test_unproven_seams_never_inherit(tmp_path, day, clock, index, writer, second):
    source(tmp_path, binding(second=-5), day="2025/12/31", clock="235955")
    source(tmp_path, notification(second=second), day=day, clock=clock, index=index, writer=writer)
    reader = DesktopLogs(tmp_path, LogEvidence())
    drain(reader, now=NOW + 86400)
    assert not reader.evidence.activities


@pytest.mark.parametrize(
    "gap",
    [b"[file-logger] dropped 3 lines due to backpressure\n", b"x" * (MAX_LINE + 1) + b"\n"],
    ids=["dropped", "oversized"],
)
def test_dropped_or_oversized_lines_break_context_across_rotation(tmp_path, gap):
    before, _ = daily(tmp_path)
    with before.open("ab") as stream:
        stream.write(gap + line("ignored", second=-1))
    reader = DesktopLogs(tmp_path, LogEvidence())
    drain(reader)
    assert not reader.evidence.activities


def test_large_irrelevant_line_keeps_complete_known_context(tmp_path):
    source(tmp_path, binding() + line("ignored " + "x" * 40000, second=3) + notification())
    reader = DesktopLogs(tmp_path, LogEvidence())
    drain(reader, budget=10000)
    assert response().samples(reader.evidence)[0].tps == 12.5


def test_delivery_pair_and_host_conflict_cross_known_seam(tmp_path):
    source(tmp_path, binding(second=-5) + notification(second=-1), day="2025/12/31", clock="235955")
    after = source(tmp_path, completion(second=1))
    reader = DesktopLogs(tmp_path, LogEvidence())
    drain(reader)
    parser = Rollout("task")
    event(parser, "event_msg", {"type": "task_started", "turn_id": "turn"}, -20)
    event(parser, "turn_context", {"turn_id": "turn", "model": "model-a"}, -20)
    completed_item(parser, kind="Reasoning", start=-10, end=-2)
    event(parser, "response_item", {"type": "reasoning", "id": "item"}, -2)
    usage(parser)
    sample = parser.samples(reader.evidence)[0]
    assert sample.tps == 12.5
    with after.open("ab") as stream:
        stream.write(binding("remote", second=2))
    drain(reader)
    assert parser.samples(reader.evidence)[0].exclusion == "ambiguous_stream_activity"


def test_group_checkpoint_replays_prefix_for_changed_suffix_after_restart(tmp_path):
    root = tmp_path / "logs"
    before, after = daily(root)
    path = tmp_path / "history.sqlite"
    with closing(History(path)) as history:
        reader = DesktopLogs(root, LogEvidence())
        reader.checkpoint_key = history.source_key
        archive = EvidenceArchive(history.connection, history.anonymous_id, root)
        archive.restore(reader.evidence, now=NOW)
        drain(reader)
        archive.save(reader.evidence, now=NOW)
        history.append(response().samples(reader.evidence), now=NOW)
        history.save_checkpoints(reader.completed, now=NOW)
    with closing(History(path)) as history:
        reader = DesktopLogs(root, LogEvidence())
        reader.checkpoint_key = history.source_key
        reader.checkpoints = history.source_checkpoints()
        archive = EvidenceArchive(history.connection, history.anonymous_id, root)
        archive.restore(reader.evidence, now=NOW)
        drain(reader)
        assert all(r.tail.offset == 0 for r in reader.files.values())
        assert response().samples(reader.evidence)[0].tps == 12.5
        with after.open("ab") as stream:
            stream.write(notification(itemId="later", second=6))
        drain(reader)
        assert reader.files[before].tail.offset == before.stat().st_size
        assert reader.evidence.activity("task", "turn", "later") is not None
        archive.save(reader.evidence, now=NOW)
        history.append(response().samples(reader.evidence), now=NOW)
        assert len(history.read()) == 1
        assert history.connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        dump = "\n".join(history.connection.iterdump())
        assert all(raw not in dump for raw in (str(root), WRITER, '"task"', "summaryIndex"))


@pytest.mark.parametrize("change", ["truncate", "delete", "conflict"])
def test_changed_prefix_cannot_supply_stale_owner(tmp_path, change):
    before, after = daily(tmp_path)
    reader = DesktopLogs(tmp_path, LogEvidence())
    drain(reader)
    if change == "delete":
        before.unlink()
    else:
        before.write_bytes(
            line("ignored", second=-5) if change == "truncate" else binding("remote", second=-5)
        )
    with after.open("ab") as stream:
        stream.write(notification(itemId="later", second=6))
    drain(reader, now=NOW + 61)
    assert reader.evidence.activity("task", "turn", "later") is None


def test_partial_predecessor_cannot_be_checkpointed_or_linked(tmp_path):
    before, _ = daily(tmp_path)
    before.write_bytes(binding(second=-5)[:-1])
    reader = DesktopLogs(tmp_path, LogEvidence())
    reader.checkpoint_key = lambda kind, path: kind + path.name
    reader.poll(NOW)
    assert reader.pending and not reader.completed and not reader.evidence.activities
    with before.open("ab") as stream:
        stream.write(b"\n")
    drain(reader)
    assert response().samples(reader.evidence)[0].tps == 12.5


def test_changing_predecessor_during_read_cannot_certify_activity(tmp_path, monkeypatch):
    before, after = daily(tmp_path)
    original = DesktopTail.read
    changed = False

    def read(tail):
        nonlocal changed
        batch = original(tail)
        if tail.path == after and not changed:
            before.write_bytes(binding("remote", second=-5))
            changed = True
        return batch

    monkeypatch.setattr(DesktopTail, "read", read)
    reader = DesktopLogs(tmp_path, LogEvidence())
    reader.checkpoint_key = lambda kind, path: kind + path.name
    reader.poll(NOW)
    assert reader.pending and not reader.completed and not reader.evidence.activities
    drain(reader)
    assert not reader.evidence.activities


def test_unchanged_group_does_not_reread_prefix_on_live_append(tmp_path, monkeypatch):
    before, after = daily(tmp_path)
    reader = DesktopLogs(tmp_path, LogEvidence())
    drain(reader)
    original = DesktopTail.read
    reads = []

    def read(tail):
        reads.append((tail.path, tail.offset))
        return original(tail)

    monkeypatch.setattr(DesktopTail, "read", read)
    drain(reader)
    assert not reads
    end = after.stat().st_size
    with after.open("ab") as stream:
        stream.write(notification(itemId="later", second=6))
    drain(reader)
    assert reads == [(after, end)]
    assert reader.files[before].tail.offset == before.stat().st_size
    assert reader.evidence.activity("task", "turn", "later") is not None


def test_revision_two_delivery_pairs_survive_archive_migration(tmp_path):
    with closing(History(tmp_path / "history.sqlite")) as history:
        reader = DesktopLogs(tmp_path, LogEvidence())
        daily(tmp_path)
        archive = EvidenceArchive(history.connection, history.anonymous_id, tmp_path)
        archive.restore(reader.evidence, now=NOW)
        drain(reader)
        archive.save(reader.evidence, now=NOW)
        db = history.connection
        db.execute(
            "UPDATE stream_activity SET revision=2,completions=?",
            (json.dumps([[BASE + 5, BASE + 12]]),),
        )
        db.commit()
        restored = LogEvidence()
        EvidenceArchive(db, history.anonymous_id, tmp_path).restore(restored, now=NOW)
        assert restored.activity("task", "turn", "item").completions == ((BASE + 5, BASE + 12),)


def test_failed_read_still_consumes_shared_byte_budget(tmp_path, monkeypatch):
    first = source(tmp_path, binding() + notification())
    budget = first.stat().st_size
    source(tmp_path, binding() + notification(), writer=WRITER.replace("-10-", "-11-"))
    original = DesktopTail.read

    def read(tail):
        batch = original(tail)
        if tail.path == first:
            first.unlink()
        return batch

    monkeypatch.setattr(DesktopTail, "read", read)
    reader = DesktopLogs(tmp_path, LogEvidence())
    assert reader.poll(NOW, budget=budget) == {"source_unavailable"}
    assert reader.bytes_read == budget
    assert reader.pending == 2
