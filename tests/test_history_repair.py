import sqlite3
from dataclasses import replace

import pytest
from test_history import sample

from token_pulse.history import History
from token_pulse.history_repair import restore_preserved


def backup_of(history, path):
    with sqlite3.connect(path) as target:
        history.connection.backup(target)


def test_restore_only_missing_independent_rows_removed_by_old_shadow(tmp_path):
    history = History(tmp_path / "history.sqlite")
    values = [
        replace(
            sample(response_id=f"response-{i}"),
            parser_revision=7,
            timing_source="stream-log" if i < 4 else "item-event",
        )
        for i in range(5)
    ]
    history.append(values, now=110)
    backup = tmp_path / "backup.sqlite"
    backup_of(history, backup)
    before = {s.key: s for s in history.read()}
    for i, reason in enumerate(("stale_usage_snapshot", "inherited_history", "superseded_usage")):
        history.reject([replace(values[i], issue=reason, parser_revision=8)], now=111)
    unchanged = history.read()
    assert restore_preserved(history, backup, now=112) == 1
    assert history.read() == unchanged
    assert restore_preserved(history, backup, now=112, apply=True) == 1
    restored = next(s for s in history.read() if s.key == history.anonymous(values[0]).key)
    assert restored == before[restored.key]
    assert restore_preserved(history, backup, now=112, apply=True) == 0
    assert all(s.key != history.anonymous(values[i]).key for i in (1, 2) for s in history.read())
    history.close()


def test_unrelated_backup_cannot_supply_identity_or_timing(tmp_path):
    history = History(tmp_path / "history.sqlite")
    other = History(tmp_path / "other.sqlite")
    other.append([sample()], now=110)
    other.close()
    with pytest.raises(ValueError, match="salt"):
        restore_preserved(history, tmp_path / "other.sqlite", now=111, apply=True)
    assert not history.read()
    history.close()


def test_existing_exclusion_and_expired_rows_are_not_overridden(tmp_path):
    history = History(tmp_path / "history.sqlite", retention_days=1)
    valid = replace(sample(), parser_revision=7, timing_source="stream-log")
    history.append([valid], now=110)
    backup = tmp_path / "backup.sqlite"
    backup_of(history, backup)
    history.append([replace(valid, issue="interrupted_response", parser_revision=8)], now=111)
    assert restore_preserved(history, backup, now=112, apply=True) == 0
    history.reject([replace(valid, issue="stale_usage_snapshot", parser_revision=9)], now=113)
    assert restore_preserved(history, backup, now=100000, apply=True) == 0
    history.close()
