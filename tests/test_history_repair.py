import sqlite3
from dataclasses import replace

import pytest
from test_history import sample

from token_pulse.history import History
from token_pulse.history_repair import restore_independent, restore_preserved


def backup_of(history, path):
    with sqlite3.connect(path) as target:
        history.connection.backup(target)


def independent_pair(tmp_path, **changes):
    saved = replace(sample(), timing_source="stream-log", parser_revision=9)
    baseline = History(tmp_path / "baseline.sqlite")
    baseline.append([saved], now=110)
    salt = baseline.salt
    baseline.close()
    history = History(tmp_path / "fresh.sqlite")
    history.salt = salt
    history.empty_turn = history.anonymous_id("turn", "")
    with history.connection:
        history.connection.execute("UPDATE settings SET value=? WHERE key='salt'", (salt.hex(),))
    missing = replace(
        saved, issue="unverified_lifecycle_start", timing_source="item-event", parser_revision=13
    )
    history.append([replace(missing, **changes)], now=110)
    return history, tmp_path / "baseline.sqlite", saved


def test_restore_independent_exact_measurement_is_idempotent_and_preserves_identity(tmp_path):
    history, baseline, saved = independent_pair(tmp_path)
    original = baseline.read_bytes()
    before = history.read()[0]
    assert restore_independent(history, baseline, now=110) == 1
    assert history.read()[0].tps is None
    assert restore_independent(history, baseline, now=110, apply=True) == 1
    restored = history.read()[0]
    assert restored.key == before.key
    assert restored.tps == saved.tps and restored.output_tokens == before.output_tokens
    assert restored.timing_source == "stream-log"
    assert restore_independent(history, baseline, now=110, apply=True) == 0
    history.close()
    history = History(tmp_path / "fresh.sqlite")
    assert history.read() == [restored]
    history.close()
    assert baseline.read_bytes() == original


@pytest.mark.parametrize(
    "changes",
    [
        {"output_tokens": 101},
        {"ended_at": 103},
        {"token_scope": "visible"},
        {"issue": "interrupted_response"},
        {"issue": "unconfirmed_tool_start"},
        {"issue": "unsupported_output_item"},
        {"issue": "ambiguous_stream_activity"},
        {"response_id": "different"},
        {"turn_id": "different"},
    ],
)
def test_independent_restore_rejects_mismatch_and_explicit_exclusions(tmp_path, changes):
    history, baseline, _ = independent_pair(tmp_path, **changes)
    before = history.read()
    assert restore_independent(history, baseline, now=110, apply=True) == 0
    assert history.read() == before
    history.close()


def test_independent_restore_checks_archived_retries_and_rejections(tmp_path):
    from token_pulse.evidence_archive import EvidenceArchive
    from token_pulse.logs import LogEvidence

    history, baseline, saved = independent_pair(tmp_path)
    archive = EvidenceArchive(history.connection, history.anonymous_id, tmp_path / "source")
    evidence = LogEvidence()
    archive.restore(evidence, now=110)
    evidence.record_interruption(saved.task_id, saved.turn_id, 101)
    archive.save(evidence, now=110)
    assert restore_independent(history, baseline, now=110, apply=True) == 0
    with history.connection:
        history.connection.execute("DELETE FROM sampling_interruptions")
    history.reject([replace(saved, issue="inherited_history")], now=110)
    assert restore_independent(history, baseline, now=110, apply=True) == 0
    history.close()


def test_independent_restore_rejects_unrelated_salt(tmp_path):
    history, baseline, _ = independent_pair(tmp_path)
    history.salt = b"unrelated"
    with pytest.raises(ValueError, match="salt"):
        restore_independent(history, baseline, now=110, apply=True)
    history.close()


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("stale_usage_snapshot", 1),
        ("inherited_history", 0),
        ("superseded_usage", 0),
    ],
)
def test_independent_restore_distinguishes_old_blank_turn_shadow(tmp_path, reason, expected):
    history, baseline, saved = independent_pair(tmp_path)
    safe = history.anonymous(saved)
    key = "|".join((safe.task_id, history.empty_turn, safe.response_id))
    with history.connection:
        history.connection.execute("INSERT INTO rejected_outputs VALUES(?,?,?)", (key, reason, 102))
    history.rejections[key] = reason, 102
    assert restore_independent(history, baseline, now=110, apply=True) == expected
    history.close()


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
