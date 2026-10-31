"""Rotation must not erase already observed, exactly scoped stream evidence."""

import sqlite3

import pytest
from test_rollout import evidence, output, ready, usage

from token_pulse.backfill import Source, fingerprint
from token_pulse.domain import Context
from token_pulse.evidence_archive import EvidenceArchive
from token_pulse.history import History
from token_pulse.logs import LogEvidence, Start

NOW = 1767225620


def open_archive(tmp_path, *, root="private-source", capacity=100000):
    history = History(tmp_path / "history.sqlite")
    archive = EvidenceArchive(
        history.connection, history.anonymous_id, tmp_path / root, capacity=capacity
    )
    logs = LogEvidence()
    archive.restore(logs, now=NOW)
    return history, archive, logs


def measured(logs):
    parser = ready()
    output(parser)
    usage(parser)
    return parser.samples(logs)[0]


def test_start_survives_restart_before_rollout_is_discovered(tmp_path):
    history, archive, logs = open_archive(tmp_path)
    logs.starts.update(evidence().starts)
    archive.save(logs, now=NOW)
    assert history.read() == []  # A start is not an output sample.
    history.close()
    history, archive, logs = open_archive(tmp_path)
    assert not logs.starts
    assert measured(logs).tps == 50
    history.append([measured(logs)], now=NOW)
    assert len(history.read()) == 1
    dump = "\n".join(history.connection.iterdump())
    assert all(
        private not in dump
        for private in ('"task"', '"turn"', '"item"', str(tmp_path), "private-source")
    )
    history.close()


def test_identity_and_source_root_must_match_exactly(tmp_path):
    history, archive, logs = open_archive(tmp_path)
    logs.starts.update(evidence().starts)
    archive.save(logs, now=NOW)
    logs.starts.clear()
    assert logs.start("other-task", "item", "turn") is None
    assert logs.start("task", "other-item", "turn") is None
    assert logs.start("task", "item", "other-turn").turn_id != "other-turn"
    history.close()
    history, archive, logs = open_archive(tmp_path, root="different-source")
    assert measured(logs).tps is None
    history.close()


def test_saved_retry_prevents_false_revalidation_after_source_loss(tmp_path):
    history, archive, logs = open_archive(tmp_path)
    logs.starts.update(evidence().starts)
    logs.record_interruption("task", "turn", 1767225611)
    archive.save(logs, now=NOW)
    history.close()
    history, archive, logs = open_archive(tmp_path)
    assert measured(logs).exclusion == "interrupted_response"
    known = history.anonymous(measured(evidence()))
    corrected = history.enrich_interruptions([known], {}, archived=logs.archived_interruptions)
    assert corrected[0].exclusion == "interrupted_response"
    history.close()


def test_rotation_does_not_change_recovery_fingerprint_or_duplicate_proof(tmp_path):
    history, archive, logs = open_archive(tmp_path)
    logs.starts.update(evidence().starts)
    logs.record_interruption("task", "turn", 1767225605)
    source = Source("task", tmp_path / "source.jsonl", Context(), (10, 20))
    before = fingerprint(source, logs)
    archive.save(logs, now=NOW)
    assert fingerprint(source, logs) == before
    logs.starts.clear()
    logs.interruptions.clear()
    logs.interruption_order.clear()
    assert fingerprint(source, logs) == before
    archive.save(logs, now=NOW + 1)
    assert history.connection.execute("SELECT count(*) FROM stream_origins").fetchone()[0] == 1
    assert (
        history.connection.execute("SELECT count(*) FROM sampling_interruptions").fetchone()[0] == 1
    )
    history.close()


def test_conflicting_replay_remains_ambiguous_across_restart(tmp_path):
    history, archive, logs = open_archive(tmp_path)
    logs.starts.update(evidence().starts)
    archive.save(logs, now=NOW)
    logs.starts["task", "item"] = Start(1767225609, "turn")
    assert measured(logs).exclusion == "ambiguous_response"
    archive.save(logs, now=NOW)
    history.close()
    history, archive, logs = open_archive(tmp_path)
    assert measured(logs).exclusion == "ambiguous_response"
    history.close()


def test_observation_is_retried_after_failed_transaction(tmp_path):
    history, archive, logs = open_archive(tmp_path)
    logs.starts.update(evidence().starts)
    logs.record_interruption("task", "turn", 1767225605)
    history.connection.execute("PRAGMA query_only=ON")
    with pytest.raises(sqlite3.OperationalError):
        archive.save(logs, now=NOW)
    assert not archive.seen_starts and not archive.seen_retries
    history.connection.execute("PRAGMA query_only=OFF")
    archive.save(logs, now=NOW)
    history.close()
    history, archive, logs = open_archive(tmp_path)
    assert measured(logs).tps == 50
    assert logs.interrupted("task", "turn", 1767225604, 1767225606)
    history.close()


def test_archive_is_not_limited_to_live_tasks_or_latest_response_cache(tmp_path):
    history, archive, logs = open_archive(tmp_path)
    logs.capacity = 2
    for i in range(350):
        task = f"private-task-{i}"
        logs.accept(
            task,
            1767225610,
            "codex_core::stream_events_utils",
            'turn{turn.id=private-turn}: Output item item_type="message" item_id="private-item"',
        )
        archive.save(logs, now=NOW)
    assert len(logs.starts) == 2
    history.close()
    history, archive, logs = open_archive(tmp_path)
    assert all(logs.start(f"private-task-{i}", "private-item", "private-turn") for i in range(350))
    assert "private-" not in "\n".join(history.connection.iterdump())
    history.close()


def test_old_revision_and_expired_origins_cannot_repair_samples(tmp_path, monkeypatch):
    import token_pulse.evidence_archive as module

    history, archive, logs = open_archive(tmp_path)
    logs.starts.update(evidence().starts)
    archive.save(logs, now=NOW)
    archived = LogEvidence()
    archive.restore(archived, now=NOW + 31 * 86400)
    assert measured(archived).tps is None
    monkeypatch.setattr(module, "EVIDENCE_REVISION", module.EVIDENCE_REVISION + 1)
    archive.restore(archived, now=NOW)
    assert measured(archived).tps is None
    history.close()


def test_capacity_does_not_keep_origins_with_dropped_retry_evidence(tmp_path):
    history, archive, logs = open_archive(tmp_path, capacity=2)
    logs.starts.update(evidence().starts)
    for at in (1767225611, 1767225612, 1767225613):
        logs.record_interruption("task", "turn", at)
    archive.save(logs, now=NOW)
    assert sum(map(len, logs.archived_interruptions.values())) == 2
    assert not logs.archived_starts
    history.close()
    history, archive, logs = open_archive(tmp_path, capacity=2)
    logs.starts.update(evidence().starts)
    archive.save(logs, now=NOW)
    assert measured(logs).tps is None
    assert not logs.archived_starts
    history.close()
