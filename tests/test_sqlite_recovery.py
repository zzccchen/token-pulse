"""Synthetic SQLite deletion, corruption, replay and source privacy cases."""

import sqlite3
from contextlib import closing

import pytest
from test_rollout import output, ready, usage

from token_pulse.logs import LogEvidence, SqliteLogs, read_only
from token_pulse.sqlite_recovery import _free_record, _leaves, _page, recover_origins

NOW = 1767225620


def make_deleted(path, *, secure=False, at=1767225610, conflicts=False, retry=False, size=4096):
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA page_size={size}")
        db.execute(f"PRAGMA secure_delete={int(secure)}")
        db.execute("""CREATE TABLE logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,ts INTEGER,ts_nanos INTEGER,level TEXT,
            target TEXT,feedback_log_body TEXT,module_path TEXT,file TEXT,line INTEGER,
            thread_id TEXT,process_uuid TEXT,estimated_bytes INTEGER)""")
        rows = []
        for i in range(max(200, size // 64)):
            stamp = at + (i % 2 if conflicts else 0)
            body = 'turn{turn.id=turn}: Output item item_type="message" item_id="item"'
            target = "codex_core::stream_events_utils"
            if retry and i % 2:
                stamp = at + 1
                target = "codex_core::responses_retry"
                body = (
                    "turn{turn.id=turn}:handle_response_stream_error{request=Sampling}: "
                    "stream connection failed; waiting to retry turn_id=turn"
                )
            rows.append(
                (
                    stamp,
                    0,
                    "INFO",
                    target,
                    body,
                    "module",
                    "source.rs",
                    42,
                    "task",
                    "process",
                    len(body) + 128,
                )
            )
        db.executemany("INSERT INTO logs VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.commit()
        db.execute("DELETE FROM logs")
        db.commit()
        assert db.execute("PRAGMA freelist_count").fetchone()[0] > 0


def sample(logs):
    parser = ready()
    output(parser)
    usage(parser)
    return parser.samples(logs)[0]


@pytest.mark.parametrize("size", [512, 4096, 65536])
def test_intact_deleted_cells_restore_exact_start_without_modifying_source(tmp_path, size):
    path = tmp_path / "logs.sqlite"
    make_deleted(path, size=size)
    before = path.read_bytes()
    logs = LogEvidence()
    with closing(read_only(path)) as db:
        assert recover_origins(db, logs, now=NOW) > 0
        assert db.total_changes == 0
        assert db.execute("SELECT count(*) FROM logs").fetchone()[0] == 0
    assert path.read_bytes() == before
    value = sample(logs)
    assert value.tps == 50
    assert value.timing_source == "recovered-stream-log"
    assert "source.rs" not in repr(logs.__dict__)


def test_deleted_retry_is_retained_with_start(tmp_path):
    path = tmp_path / "logs.sqlite"
    make_deleted(path, retry=True)
    logs = LogEvidence()
    with closing(read_only(path)) as db:
        assert recover_origins(db, logs, now=NOW)
    assert sample(logs).exclusion == "interrupted_response"


def test_conflicting_deleted_starts_cannot_be_averaged_or_selected(tmp_path):
    path = tmp_path / "logs.sqlite"
    make_deleted(path, conflicts=True)
    logs = LogEvidence()
    with closing(read_only(path)) as db:
        assert recover_origins(db, logs, now=NOW)
    assert sample(logs).exclusion == "ambiguous_response"


@pytest.mark.parametrize("mode", ["secure", "expired", "future", "schema", "budget"])
def test_unavailable_or_unsupported_records_do_not_supply_origins(tmp_path, mode, monkeypatch):
    import token_pulse.sqlite_recovery as module

    path = tmp_path / "logs.sqlite"
    at = NOW - 604801 if mode == "expired" else NOW + 6 if mode == "future" else NOW - 10
    make_deleted(path, secure=mode == "secure", at=at)
    if mode == "schema":
        with sqlite3.connect(path) as db:
            db.execute("ALTER TABLE logs ADD COLUMN unsupported TEXT")
    if mode == "budget":
        monkeypatch.setattr(module, "MAX_SNAPSHOT", 1)
    logs = LogEvidence()
    with closing(read_only(path)) as db:
        assert recover_origins(db, logs, now=NOW) == 0
    assert not logs.starts


def test_corrupt_freelist_is_not_text_carved(tmp_path):
    path = tmp_path / "logs.sqlite"
    make_deleted(path)
    image = bytearray(path.read_bytes())
    size = int.from_bytes(image[16:18], "big")
    trunk = int.from_bytes(image[32:36], "big")
    image[(trunk - 1) * size : (trunk - 1) * size + 4] = trunk.to_bytes(4, "big")
    with pytest.raises(ValueError, match="freelist chain"):
        _leaves(bytes(image), size, size - image[20])


def test_recovery_runs_once_per_reader_reset(tmp_path, monkeypatch):
    path = tmp_path / "logs.sqlite"
    make_deleted(path)
    calls = []
    original = recover_origins

    def recover(db, evidence, **kwargs):
        calls.append(True)
        return original(db, evidence, now=NOW)

    monkeypatch.setattr("token_pulse.sqlite_recovery.recover_origins", recover)
    logs = LogEvidence()
    reader = SqliteLogs(path, logs)
    assert reader.poll() is None
    assert sample(logs).tps == 50
    assert reader.poll() is None
    assert len(calls) == 1


def freeblock_source(path):
    make_deleted(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA secure_delete=OFF")
        body = 'turn{turn.id=turn}: Output item item_type="message" item_id="item"'
        # A three-byte rowid leaves the complete record header after freeing.
        for i in range(12):
            db.execute(
                "INSERT INTO logs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    2000000 + i,
                    NOW - 10,
                    0,
                    "INFO",
                    "codex_core::stream_events_utils",
                    body,
                    None,
                    None,
                    None,
                    "task",
                    "process",
                    len(body),
                ),
            )
        db.execute("DELETE FROM logs WHERE id=2000005")
        root = db.execute("SELECT rootpage FROM sqlite_master WHERE name='logs'").fetchone()[0]
    image = path.read_bytes()
    size = int.from_bytes(image[16:18], "big")
    cells, blocks = _page(image, root, size, size)
    assert len(cells) == 11 and len(blocks) == 1
    return image, root, size, blocks[0]


def test_complete_payload_in_freeblock_restores_original_time(tmp_path):
    path = tmp_path / "logs.sqlite"
    image, root, size, (begin, end) = freeblock_source(path)
    row = _free_record(image, begin, end, size, 2000011)
    assert row[1] == NOW - 10 and row[9] == "task"
    # A consistent WAL snapshot must see deletion even before checkpointing.
    with sqlite3.connect(path) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE logs SET target='irrelevant' WHERE id=2000000")
        writer.commit()
        before = path.read_bytes(), path.with_name(path.name + "-wal").read_bytes()
        with closing(read_only(path)) as db:
            logs = LogEvidence()
            assert recover_origins(db, logs, now=NOW) > 0
        assert sample(logs).tps == 50
        assert before == (path.read_bytes(), path.with_name(path.name + "-wal").read_bytes())


@pytest.mark.parametrize("damage", ["header", "truncated", "suffix", "overlap", "cycle"])
def test_damaged_freeblock_cannot_supply_partial_or_guessed_fields(tmp_path, damage):
    image, root, size, (begin, end) = freeblock_source(tmp_path / "logs.sqlite")
    value = bytearray(image)
    if damage == "header":
        value[begin + 5] = 0  # Entire record header must remain intact.
    elif damage == "truncated":
        end -= 1
    elif damage == "suffix":
        value[begin + 4] = 255  # Rowid suffix does not end at the record boundary.
    elif damage == "overlap":
        base = (root - 1) * size
        value[base + 8 : base + 10] = (begin - base).to_bytes(2, "big")
    else:
        value[begin : begin + 2] = (begin % size).to_bytes(2, "big")
    if damage in {"overlap", "cycle"}:
        assert _page(bytes(value), root, size, size) == ([], [])
    else:
        assert _free_record(bytes(value), begin, end, size, 2000011) is None


def test_live_conflict_with_recovered_origin_is_not_silently_preferred(tmp_path):
    path = tmp_path / "logs.sqlite"
    freeblock_source(path)
    logs = LogEvidence()
    with closing(read_only(path)) as db:
        recover_origins(db, logs, now=NOW)
    logs.accept(
        "task",
        NOW - 9,
        "codex_core::stream_events_utils",
        'turn{turn.id=turn}: Output item item_type="message" item_id="item"',
    )
    assert sample(logs).exclusion == "ambiguous_response"
