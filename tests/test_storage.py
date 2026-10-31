"""Synthetic redirection, stale journal, interrupted migration and restart cases."""

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from test_history import sample

from token_pulse.history import History
from token_pulse.storage import (
    MANIFEST,
    StorageError,
    database_path,
    initialize_directory,
    writable_directory,
)


def populated(directory):
    history = History(directory / "history.sqlite")
    history.append([sample(parser_revision=9, timing_source="stream-log")], now=110)
    history.save_checkpoints({"pseudonym": ("fingerprint", 110)}, now=110)
    values = history.read()
    history.close()
    return values


def test_existing_directory_and_history_remain_stable(tmp_path):
    before = populated(tmp_path)
    assert writable_directory(tmp_path) == tmp_path.resolve()
    initialize_directory(tmp_path, tmp_path.resolve())
    assert not (tmp_path / MANIFEST).exists()
    history = History(tmp_path / "history.sqlite")
    assert history.read() == before
    history.close()
    assert not list(tmp_path.glob(".token-pulse-probe-*"))


def test_writable_history_refuses_split_main_journal_location_before_open(tmp_path, monkeypatch):
    import token_pulse.storage as module

    logical, physical = tmp_path / "logical", tmp_path / "physical"
    before = populated(logical)
    physical.mkdir()
    monkeypatch.setattr(module, "writable_directory", lambda path: physical)
    with pytest.raises(StorageError, match="redirection"):
        History(logical / "history.sqlite")
    assert not (physical / "history.sqlite").exists()
    monkeypatch.undo()
    history = History(logical / "history.sqlite")
    assert history.read() == before
    history.close()


def test_journal_alias_and_orphan_journal_are_rejected(tmp_path, monkeypatch):
    import token_pulse.storage as module

    target = tmp_path / "history.sqlite"
    journal = Path(str(target) + "-wal")
    journal.write_bytes(b"unrelated journal")
    with pytest.raises(StorageError, match="Orphan"):
        database_path(target)
    target.write_bytes(b"main file")
    monkeypatch.setattr(module, "physical_file", lambda path: tmp_path / "elsewhere" / path.name)
    with pytest.raises(StorageError, match="physical directory"):
        database_path(target)
    assert journal.read_bytes() == b"unrelated journal"


def test_snapshot_migration_preserves_identities_and_retires_unrelated_journals(tmp_path):
    logical, physical = tmp_path / "logical", tmp_path / "physical"
    before = populated(logical)
    physical.mkdir()
    original = (logical / "history.sqlite").read_bytes()
    (logical / "settings.json").write_text('{"language":"en"}')
    for suffix in ("-wal", "-shm", "-journal"):
        (physical / ("history.sqlite" + suffix)).write_bytes(b"obsolete")
    initialize_directory(logical, physical)
    history = History(physical / "history.sqlite")
    assert history.read() == before
    assert history.source_checkpoints() == {"pseudonym": "fingerprint"}
    assert history.connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
    history.close()
    initialize_directory(logical, physical)
    initialize_directory(physical, physical)  # An explicit canonical path is usable.
    assert (logical / "history.sqlite").read_bytes() == original
    assert (physical / "settings.json").read_text() == '{"language":"en"}'
    marker = json.loads((physical / MANIFEST).read_text())
    assert marker["state"] == "complete"
    assert str(logical) not in (physical / MANIFEST).read_text()
    assert len(list((physical / marker["backup"]).glob("retired-*"))) == 3


@pytest.mark.parametrize("point", ["prepared", "retired", "installed"])
def test_interrupted_migration_resumes_from_complete_snapshot(tmp_path, monkeypatch, point):
    import token_pulse.storage as module

    logical, physical = tmp_path / "logical", tmp_path / "physical"
    before = populated(logical)
    physical.mkdir()
    (physical / "history.sqlite-wal").write_bytes(b"obsolete")
    original_replace = os.replace
    original_finish = module._finish
    original_manifest = module._write_manifest

    def finish(*args):
        if point == "prepared":
            raise OSError("simulated process exit")
        return original_finish(*args)

    def move(source, target):
        result = original_replace(source, target)
        if point == "retired" and Path(target).name == "retired-history.sqlite-wal":
            raise OSError("simulated process exit")
        return result

    def manifest(directory, value):
        if point == "installed" and value["state"] == "complete":
            raise OSError("simulated process exit")
        return original_manifest(directory, value)

    monkeypatch.setattr(module, "_finish", finish)
    monkeypatch.setattr(module.os, "replace", move)
    monkeypatch.setattr(module, "_write_manifest", manifest)
    with pytest.raises(OSError, match="simulated"):
        initialize_directory(logical, physical)
    monkeypatch.undo()
    initialize_directory(logical, physical)
    history = History(physical / "history.sqlite")
    assert history.read() == before
    assert history.connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
    history.close()


def test_conflicting_or_changed_history_is_never_overwritten(tmp_path):
    logical, physical = tmp_path / "logical", tmp_path / "physical"
    populated(logical)
    before = populated(physical)
    with pytest.raises(StorageError, match="independent"):
        initialize_directory(logical, physical)
    history = History(physical / "history.sqlite")
    assert history.read() == before
    history.close()


def test_changed_legacy_history_after_success_requires_explicit_selection(tmp_path):
    logical, physical = tmp_path / "logical", tmp_path / "physical"
    before = populated(logical)
    physical.mkdir()
    initialize_directory(logical, physical)
    history = History(logical / "history.sqlite")
    history.append([sample(response_id="new", parser_revision=9)], now=110)
    history.close()
    with pytest.raises(StorageError, match="changed after"):
        initialize_directory(logical, physical)
    initialize_directory(physical, physical)
    history = History(physical / "history.sqlite")
    assert history.read() == before
    history.close()


def test_committed_wal_is_snapshotted_after_writer_process_exit(tmp_path):
    logical, physical = tmp_path / "logical", tmp_path / "physical"
    populated(logical)
    physical.mkdir()
    code = """
import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1])
db.execute('PRAGMA journal_mode=WAL')
db.execute('PRAGMA wal_autocheckpoint=0')
db.execute("INSERT INTO settings VALUES('synthetic_marker','committed')")
db.commit()
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", code, str(logical / "history.sqlite")], check=True)
    assert (logical / "history.sqlite-wal").stat().st_size > 32
    initialize_directory(logical, physical)
    with closing(sqlite3.connect(physical / "history.sqlite")) as db:
        assert db.execute("SELECT value FROM settings WHERE key='synthetic_marker'").fetchone() == (
            "committed",
        )
        assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)


def test_wrong_generation_wal_reproduces_corruption_and_blocks_migration(tmp_path):
    old, logical, physical = tmp_path / "old", tmp_path / "logical", tmp_path / "physical"
    old.mkdir()
    populated(logical)
    physical.mkdir()
    code = """
import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1])
db.execute('PRAGMA journal_mode=WAL')
db.execute('PRAGMA wal_autocheckpoint=0')
db.execute('CREATE TABLE unrelated(x)')
db.executemany('INSERT INTO unrelated VALUES(?)',[('synthetic'*200,)]*20)
db.commit()
db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
db.execute('CREATE TABLE later(y)')
db.execute('INSERT INTO later VALUES(1)')
db.commit()
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", code, str(old / "history.sqlite")], check=True)
    # Reproduce a per-file overlay combining a new main with an older journal.
    wal = (old / "history.sqlite-wal").read_bytes()
    (logical / "history.sqlite-wal").write_bytes(wal)
    original = (logical / "history.sqlite").read_bytes()
    with closing(
        sqlite3.connect((logical / "history.sqlite").as_uri() + "?mode=ro", uri=True)
    ) as db:
        try:
            integrity = db.execute("PRAGMA quick_check").fetchall()
        except sqlite3.DatabaseError:
            integrity = []
        assert integrity != [("ok",)]
    with pytest.raises((StorageError, sqlite3.DatabaseError)):
        initialize_directory(logical, physical)
    assert not (physical / "history.sqlite").exists()
    assert (logical / "history.sqlite").read_bytes() == original
    assert (logical / "history.sqlite-wal").read_bytes() == wal


@pytest.mark.parametrize("value", [[], None, {"version": 1}, {"backup": "../escape"}])
def test_invalid_migration_marker_never_overwrites_history(tmp_path, value):
    before = populated(tmp_path)
    (tmp_path / MANIFEST).write_text(json.dumps(value))
    with pytest.raises(StorageError):
        initialize_directory(tmp_path, tmp_path)
    history = History(tmp_path / "history.sqlite")
    assert history.read() == before
    history.close()


def test_source_changed_during_interrupted_migration_is_not_lost(tmp_path, monkeypatch):
    import token_pulse.storage as module

    logical, physical = tmp_path / "logical", tmp_path / "physical"
    populated(logical)
    physical.mkdir()

    def stop(*args):
        raise OSError("interrupted")

    monkeypatch.setattr(module, "_finish", stop)
    with pytest.raises(OSError):
        initialize_directory(logical, physical)
    monkeypatch.undo()
    history = History(logical / "history.sqlite")
    history.append([sample(response_id="new", parser_revision=9)], now=110)
    history.close()
    with pytest.raises(StorageError, match="changed during"):
        initialize_directory(logical, physical)
    assert not (physical / "history.sqlite").exists()
