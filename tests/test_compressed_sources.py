import json
import sqlite3

import pytest
from pyzstd import CParameter, compress
from test_backfill import NOW, bind_cache, recover, source
from test_collector import make_source

from token_pulse.backfill import Backfill
from token_pulse.collector import Collector, rollout_path
from token_pulse.domain import Context
from token_pulse.history import History
from token_pulse.logs import compatible_database
from token_pulse.tailer import ZstdJsonlTail, open_tail


def read_all(tail):
    batches = []
    for _ in range(10000):
        batch = tail.read()
        batches.append(batch)
        if batch.eof or batch.error:
            return batches
    raise AssertionError("Reader failed to finish within its bounded poll count")


def test_decoded_budget_offsets_multiple_frames_and_partial_utf8(tmp_path):
    path = tmp_path / "events.jsonl.zst"
    data = (json.dumps({"text": "词脉" * 1000}, ensure_ascii=False) + "\n").encode()
    path.write_bytes(compress(data[:31]) + compress(data[31:]) + compress(b'{"last":1}\n'))
    tail = open_tail(path, budget=17, backfill=1)
    batches = read_all(tail)
    assert len(batches) > 300
    assert [r for b in batches for r in b.records] == [json.loads(data), {"last": 1}]
    assert not any(b.error or b.skipped_prefix for b in batches)
    assert tail.offset == len(data) + len(b'{"last":1}\n')
    assert tail.offset > path.stat().st_size
    assert tail.read().eof and not tail.read().records


@pytest.mark.parametrize("damage", ["truncate", "checksum", "partial_line", "trailing_garbage"])
def test_damaged_compressed_source_never_commits_a_completion_checkpoint(tmp_path, damage):
    plain = tmp_path / "events.jsonl"
    evidence = source(plain, "task", count=2)
    data = plain.read_bytes()
    if damage == "partial_line":
        data = data[:-2]
    encoded = compress(data, {CParameter.checksumFlag: 1})
    if damage == "truncate":
        encoded = encoded[:-1]
    elif damage == "checksum":
        encoded = encoded[:-1] + bytes([encoded[-1] ^ 1])
    elif damage == "trailing_garbage":
        encoded += b"bad trailer"
    path = plain.with_suffix(".jsonl.zst")
    path.write_bytes(encoded)
    history = History(tmp_path / "history.sqlite")
    try:
        recovery = Backfill(budget=128)
        bind_cache(recovery, history)
        recovery.add("task", path, Context(), live=False)
        for _ in range(100):
            recovery.poll(evidence, NOW)
            if not recovery.pending:
                break
        assert recovery.pending == 0
        assert not recovery.completed and not recovery.done
        path.write_bytes(compress(plain.read_bytes()))
        recovery.add("task", path, Context(), live=False)
        assert len(recover(recovery, history, evidence)) == 2
        assert history.source_checkpoints()
    finally:
        history.close()


def test_compressed_rotation_and_line_limit(tmp_path):
    path = tmp_path / "events.jsonl.zst"
    path.write_bytes(compress(b"x" * 100 + b'\n{"n":1}\n'))
    tail = ZstdJsonlTail(path, budget=13, max_line=20)
    batches = read_all(tail)
    assert sum(b.oversized for b in batches) == 1
    assert [r for b in batches for r in b.records] == [{"n": 1}]
    path.write_bytes(compress(b'{"n":2}\n'))
    assert tail.read().reset
    path.unlink()
    assert tail.read().error == "source_unavailable"
    path.write_bytes(compress(b'{"n":3}\n'))
    batches = read_all(tail)
    assert [r for b in batches for r in b.records] == [{"n": 3}]


def test_plain_and_compressed_sources_deduplicate_and_changed_container_rechecks(tmp_path):
    plain = tmp_path / "events.jsonl"
    evidence = source(plain, "task", count=2)
    history = History(tmp_path / "history.sqlite")
    try:
        recovery = Backfill()
        bind_cache(recovery, history)
        recovery.add("task", plain, Context(), live=False)
        recover(recovery, history, evidence)
        compressed = plain.with_suffix(".jsonl.zst")
        compressed.write_bytes(compress(plain.read_bytes()))
        assert history.source_key("task", plain) == history.source_key("task", compressed)
        recovery.add("task", compressed, Context(), live=False)
        assert len(recover(recovery, history, evidence)) == 2
        assert len(history.read()) == 2
        restarted = Backfill()
        bind_cache(restarted, history)
        restarted.add("task", compressed, Context(), live=False, evidence=evidence)
        assert recover(restarted, history, evidence) == []
        assert restarted.reused == 1
    finally:
        history.close()


def test_schema_selection_falls_back_from_incompatible_newest_database(tmp_path):
    make_source(tmp_path)
    (tmp_path / "state_999.sqlite").write_bytes(b"not a database")
    old = tmp_path / "sqlite"
    old.mkdir()
    (tmp_path / "state_5.sqlite").rename(old / "state_5.sqlite")
    snapshot = Collector(tmp_path).poll(1767225610)
    assert len(snapshot.samples) == 1
    assert "state_unavailable" not in snapshot.diagnostics
    assert compatible_database(tmp_path, "state", "threads", {"unknown_column"}) is None


def test_recent_archived_compressed_source_uses_index_and_millisecond_capability(tmp_path):
    plain = make_source(tmp_path)
    compressed = plain.with_suffix(".jsonl.zst")
    compressed.write_bytes(compress(plain.read_bytes()))
    plain.unlink()
    now = 1767225610
    with sqlite3.connect(tmp_path / "state_5.sqlite") as db:
        db.execute("ALTER TABLE threads ADD COLUMN updated_at_ms INTEGER")
        db.execute("UPDATE threads SET archived=1, updated_at_ms=?", (now * 1000,))
    collector = Collector(tmp_path)
    snapshot = collector.poll(now)
    assert len(snapshot.samples) == 1
    assert snapshot.backfill_completed == 1
    assert snapshot.samples[0].output_tokens == 100
    assert collector.poll(now + 1).backfill_completed == 1
    assert rollout_path(tmp_path, str(plain)) == compressed
    assert rollout_path(tmp_path, str(tmp_path.parent / "outside.jsonl")) is None


def test_sparse_old_logs_can_live_in_state_database(tmp_path):
    make_source(tmp_path)
    with sqlite3.connect(tmp_path / "state_5.sqlite") as db:
        db.execute("CREATE TABLE logs(id,ts,ts_nanos,thread_id,target,feedback_log_body)")
        db.execute(
            "INSERT INTO logs VALUES(1,1767225601,0,?,?,?)",
            (
                "task",
                "codex_core::stream_events_utils",
                'turn{turn.id=turn}: Output item item=Message { id: Some("item"), content: [] }',
            ),
        )
    snapshot = Collector(tmp_path).poll(1767225610)
    assert snapshot.samples[0].tps == 100
    assert "logs_unavailable" not in snapshot.diagnostics
