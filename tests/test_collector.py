import json
import os
import sqlite3

import pytest

from token_pulse.collector import Collector, newest_database


def make_source(root):
    path = root / "sessions.jsonl"
    records = [
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn"}},
        {
            "type": "turn_context",
            "payload": {"turn_id": "turn", "model": "model-a", "effort": "high"},
        },
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "assistant", "id": "item"},
        },
        {
            "type": "token_usage_record",
            "payload": {"response_id": "response", "usage": {"output_tokens": 100}},
        },
    ]
    path.write_text(
        "\n".join(
            json.dumps(r | {"timestamp": f"2026-01-01T00:00:{i:02d}Z"})
            for i, r in enumerate(records)
        )
        + "\n"
    )
    with sqlite3.connect(root / "state_5.sqlite") as c:
        c.execute("CREATE TABLE threads(id,rollout_path,title,archived,updated_at)")
        c.execute("INSERT INTO threads VALUES(?,?,?,0,1)", ("task", str(path), "PRIVATE TITLE"))
    return path


def test_discovery_is_read_only_and_missing_logs_degrade(tmp_path):
    make_source(tmp_path)
    (tmp_path / "config.toml").write_text('service_tier="priority"\n')
    snapshot = Collector(tmp_path).poll(1767225610)
    assert len(snapshot.tasks) == 1
    assert snapshot.tasks[0].context.model == "model-a"
    assert snapshot.tasks[0].context.configured_tier == "priority"
    assert snapshot.tasks[0].context.requested_tier is None
    assert snapshot.tasks[0].latest.tps is None
    assert "PRIVATE TITLE" not in repr(snapshot.samples)
    assert snapshot.samples[0].context.configured_tier is None
    assert "logs_unavailable" in snapshot.diagnostics


def test_empty_source_and_invalid_config(tmp_path):
    (tmp_path / "config.toml").write_text("broken =")
    snapshot = Collector(tmp_path).poll()
    assert not snapshot.tasks
    assert set(snapshot.diagnostics) >= {"no_sessions", "config_unavailable"}


def test_database_version_order_is_numeric(tmp_path):
    for version in [2, 10, 9]:
        (tmp_path / f"state_{version}.sqlite").touch()
    assert newest_database(tmp_path, "state").name == "state_10.sqlite"


def test_partial_append_does_not_replay_and_source_loss_is_visible(tmp_path):
    path = make_source(tmp_path)
    collector = Collector(tmp_path)
    first = collector.poll(1767225610)
    second = collector.poll(1767225612)
    assert first.samples == second.samples
    path.unlink()
    assert collector.poll(1767225614).tasks[0].state == "unknown"


def test_gap_after_turn_start_is_not_erased_by_the_earlier_boundary(tmp_path):
    path = make_source(tmp_path)
    lines = path.read_text().splitlines()
    lines.insert(3, "broken record")
    path.write_text("\n".join(lines) + "\n")
    snapshot = Collector(tmp_path).poll(1767225610)
    assert snapshot.samples[0].exclusion == "record_gap"
    assert "records_skipped" in snapshot.diagnostics


def test_seven_day_recovery_includes_older_and_archived_tasks_beyond_live_limit(tmp_path):
    path = make_source(tmp_path)
    now = 1767225610
    with sqlite3.connect(tmp_path / "state_5.sqlite") as c:
        c.execute("UPDATE threads SET updated_at=?", (now,))
        for index in range(40):
            other = tmp_path / f"task-{index}.jsonl"
            other.write_bytes(path.read_bytes())
            c.execute(
                "INSERT INTO threads VALUES(?,?,?,?,?)",
                (f"task-{index}", str(other), "PRIVATE TITLE", index % 2, now - index - 1),
            )
        c.execute(
            "INSERT INTO threads VALUES(?,?,?,?,?)",
            ("outside-range", str(path), "PRIVATE TITLE", 0, now - 8 * 86400),
        )
    collector = Collector(tmp_path, max_tasks=1)
    samples = {}
    for _ in range(10):
        snapshot = collector.poll(now)
        samples.update((s.key, s) for s in snapshot.samples)
        if not snapshot.backfill_pending:
            break
    assert len(snapshot.tasks) == 1
    assert snapshot.backfill_completed == 41
    assert len(samples) == 41
    assert "outside-range" not in {s.task_id for s in samples.values()}
    assert "PRIVATE TITLE" not in repr(list(samples.values()))


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-length path spelling")
def test_extended_length_paths_are_the_same_source_root_on_windows(tmp_path):
    path = make_source(tmp_path)
    now = 1767225610
    with sqlite3.connect(tmp_path / "state_5.sqlite") as c:
        c.execute(
            "UPDATE threads SET rollout_path=?, updated_at=?",
            ("\\\\?\\" + str(path.resolve()), now),
        )
    snapshot = Collector(tmp_path).poll(now)
    assert len(snapshot.tasks) == 1
    assert len(snapshot.samples) == 1
    assert snapshot.backfill_completed == 1
