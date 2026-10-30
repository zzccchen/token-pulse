import json
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest
from test_transport import events

from token_pulse.collector import Collector
from token_pulse.history import History
from token_pulse.transport import REVISION, TARGETS
from token_pulse.transport_logs import TransportLogs

NOW = 1767225620


def append_events(path, values=None, *, start=NOW - 20, task="task", process="process"):
    values = events() if values is None else values
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY,ts,ts_nanos,thread_id,"
            "process_uuid,target,feedback_log_body)"
        )
        for i, event in enumerate(values):
            body = f'session_loop{{thread_id="{task}"}}:turn{{turn.id=turn}}: websocket event: '
            body += json.dumps(event)
            db.execute(
                "INSERT INTO logs(ts,ts_nanos,thread_id,process_uuid,target,feedback_log_body) "
                "VALUES(?,?,?,?,?,?)",
                (start + i, 0, task, process, TARGETS[1], body),
            )


def bind(reader, history):
    key = history.source_key("transport", reader.path)
    reader.digest = lambda value: history.anonymous_id("transport-cursor", value)
    reader.checkpoint = history.transport_checkpoints().get(key)
    return key


def drain(reader, now=NOW):
    result = []
    for _ in range(100):
        values, issues, _ = reader.poll(now)
        assert not issues
        result.extend(values)
        if not reader.pending:
            return result
    raise AssertionError("Reader did not finish")


def test_response_across_small_batches_and_restart_without_parser_state(tmp_path):
    path = tmp_path / "logs.sqlite"
    append_events(path)
    first = TransportLogs(path, batch_size=2)
    assert first.poll(NOW)[0] == []
    assert first.completed is None and first.pending
    restarted = TransportLogs(path, batch_size=2)
    values = drain(restarted)
    assert len(values) == 1 and values[0].tps == 20
    assert restarted.completed[0] == 8
    assert restarted.poll(NOW)[0] == []


def test_saved_quiescent_cursor_resumes_only_after_persisted_output(tmp_path):
    path = tmp_path / "logs.sqlite"
    append_events(path)
    storage = tmp_path / "history.sqlite"
    history = History(storage)
    reader = TransportLogs(path)
    key = bind(reader, history)
    values = drain(reader)
    history.append(values, now=NOW)
    history.save_transport_checkpoints({key: reader.completed}, now=NOW)
    expected = history.read()
    history.close()
    history = History(storage)
    restarted = TransportLogs(path, batch_size=2)
    bind(restarted, history)
    assert drain(restarted) == [] and restarted.cursor == 8
    append_events(path, events("next", "next-item"), start=NOW - 10)
    values = drain(restarted)
    assert len(values) == 1 and values[0].response_id == "next"
    history.append(values, now=NOW)
    assert len(history.read()) == 2
    assert any(s.key == expected[0].key for s in history.read())
    rows = history.connection.execute("SELECT * FROM transport_cursors").fetchall()
    assert str(path) not in repr(rows) and "PRIVATE" not in repr(rows)
    history.close()


def test_old_parser_cursor_replays_and_a_partial_response_never_advances_checkpoint(tmp_path):
    path = tmp_path / "logs.sqlite"
    append_events(path)
    history = History(tmp_path / "history.sqlite")
    try:
        reader = TransportLogs(path)
        key = bind(reader, history)
        values = drain(reader)
        history.append(values, now=NOW)
        old = list(reader.completed)
        old[3] = REVISION - 1
        history.save_transport_checkpoints({key: tuple(old)}, now=NOW)
        restarted = TransportLogs(path)
        bind(restarted, history)
        assert len(drain(restarted)) == 1
        cursor = restarted.completed[0]
        append_events(path, events("unfinished", "u")[:4], start=NOW - 5)
        partial = drain(restarted)
        assert len(partial) == 1 and partial[0].issue == "transport_incomplete_stream"
        assert partial[0].output_tokens is None and partial[0].tps is None
        assert restarted.completed[0] == cursor
    finally:
        history.close()


@pytest.mark.parametrize("change", ["replacement", "truncate", "rewrite"])
def test_database_rotation_and_reused_row_ids_invalidate_cursor(tmp_path, change):
    path = tmp_path / "logs.sqlite"
    append_events(path)
    reader = TransportLogs(path)
    assert len(drain(reader)) == 1
    if change == "replacement":
        path.rename(tmp_path / "old.sqlite")
        append_events(path, events("new", "new-item"))
    elif change == "truncate":
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("DELETE FROM logs")
        append_events(path, events("new", "new-item")[-1:])
    else:
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("DELETE FROM logs")
        append_events(path, events("new", "new-item"))
    values = drain(reader)
    assert len(values) == 1 and values[0].response_id == "new"
    assert (values[0].tps is None) == (change == "truncate")


def test_missing_database_and_process_capability_degrade_without_fabricating_timing(tmp_path):
    path = tmp_path / "logs.sqlite"
    reader = TransportLogs(path)
    assert reader.poll(NOW)[1] == {"logs_unavailable"}
    append_events(path, process=None)
    sample = drain(reader)[0]
    assert sample.output_tokens == 100 and sample.issue == "transport_missing_process"
    assert sample.tps is None


def test_oversized_and_malformed_rows_break_the_sequence_not_the_cursor(tmp_path):
    path = tmp_path / "logs.sqlite"
    append_events(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE logs SET feedback_log_body=? WHERE id=4", ("x" * (2 * 1024 * 1024 + 1),))
    reader = TransportLogs(path, batch_size=3)
    samples, issues = [], set()
    for _ in range(10):
        values, found, used = reader.poll(NOW)
        assert used <= 4 * 1024 * 1024
        samples.extend(values)
        issues.update(found)
        if not reader.pending:
            break
    assert reader.cursor == 8
    assert "records_skipped" in issues
    assert len(samples) == 1 and samples[0].tps is None


def test_reader_byte_budget_leaves_unconsumed_row_for_next_poll(tmp_path):
    path = tmp_path / "logs.sqlite"
    append_events(path)
    reader = TransportLogs(path)
    values, issues, used = reader.poll(NOW, budget=1)
    assert not values and not issues and used == 0 and reader.pending
    assert len(drain(reader)) == 1


@pytest.mark.parametrize("column,value", [("feedback_log_body", b"binary"), ("ts_nanos", b"bad")])
def test_malformed_sqlite_field_types_cannot_trap_incremental_progress(tmp_path, column, value):
    path = tmp_path / "logs.sqlite"
    append_events(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(f"UPDATE logs SET {column}=? WHERE id=4", (value,))
    reader = TransportLogs(path)
    values, issues, _ = reader.poll(NOW)
    assert "logs_unavailable" not in issues
    assert reader.cursor == 8
    assert len(values) == 1 and values[0].tps is None


def test_collector_imports_valid_transport_and_deduplicates_retained_database_copy(tmp_path):
    path = tmp_path / "logs_2.sqlite"
    append_events(path)
    old = tmp_path / "sqlite"
    old.mkdir()
    (old / "logs_2.sqlite").write_bytes(path.read_bytes())
    collector = Collector(tmp_path)
    snapshot = collector.poll(NOW)
    assert len(snapshot.samples) == 1 and snapshot.samples[0].tps == 20
    assert snapshot.samples[0].context.actual_tier == "future-tier"
    assert len(collector.transport_readers) == 2


def test_collector_does_not_import_transport_outside_the_selected_history_horizon(tmp_path):
    append_events(tmp_path / "logs_2.sqlite", start=NOW - 8 * 86400)
    assert not Collector(tmp_path).poll(NOW).samples


def test_log_loss_does_not_erase_saved_valid_transport_measurement(tmp_path):
    path = tmp_path / "logs.sqlite"
    append_events(path)
    reader = TransportLogs(path)
    original = drain(reader)[0]
    history = History(tmp_path / "history.sqlite")
    try:
        history.append([original], now=NOW)
        incomplete = replace(
            original, issue="transport_missing_start", started_at=None, response_aliases=()
        )
        history.append([incomplete], now=NOW)
        assert history.read()[0].tps == 20
    finally:
        history.close()


def test_incomplete_eof_record_is_repaired_by_later_events(tmp_path):
    path = tmp_path / "logs.sqlite"
    append_events(path, events()[:4])
    reader = TransportLogs(path)
    with closing(History(tmp_path / "history.sqlite")) as history:
        partial = drain(reader)
        history.append(partial, now=NOW)
        assert len(partial) == 1 and partial[0].issue == "transport_incomplete_stream"
        assert reader.completed is None
        append_events(path, events()[4:], start=NOW - 16)
        history.append(drain(reader), now=NOW)
        saved = history.read()
        assert len(saved) == 1 and saved[0].tps == 20
        assert reader.completed[0] == 8


def test_collector_replaces_oldest_reader_when_database_versions_rotate(tmp_path):
    for version in range(8):
        append_events(tmp_path / f"logs_{version}.sqlite", events(f"r{version}", f"i{version}"))
    collector = Collector(tmp_path)
    assert len(collector.poll(NOW).samples) == 8
    append_events(tmp_path / "logs_8.sqlite", events("latest", "latest-item"))
    snapshot = collector.poll(NOW + 61)
    assert len(collector.transport_readers) == 8
    assert tmp_path / "logs_8.sqlite" in collector.transport_readers
    assert tmp_path / "logs_0.sqlite" not in collector.transport_readers
    assert len(snapshot.samples) == 1 and snapshot.samples[0].response_id == "latest"
