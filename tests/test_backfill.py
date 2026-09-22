import json
from datetime import UTC, datetime

import pytest

from token_pulse.backfill import Backfill
from token_pulse.domain import Context
from token_pulse.history import History
from token_pulse.logs import LogEvidence, Start

NOW = 1788789600


def record(kind, payload, at):
    return (
        json.dumps(
            {
                "type": kind,
                "timestamp": datetime.fromtimestamp(at, UTC).isoformat(),
                "payload": payload,
            }
        )
        + "\n"
    )


def source(path, task, *, count=400, padding=0):
    evidence = LogEvidence()
    at = NOW - 10000
    with path.open("w", encoding="utf-8") as stream:
        stream.write(record("turn_context", {"turn_id": "turn", "model": "model-old"}, at))
        for i in range(count):
            end = at + 2 * i + 2
            evidence.starts[task, f"item-{i}"] = Start(end - 1, "turn", "Chatgpt")
            stream.write(
                record(
                    "response_item",
                    {"type": "message", "role": "assistant", "id": f"item-{i}"},
                    end,
                )
            )
            stream.write(
                record(
                    "token_usage_record",
                    {"response_id": f"response-{i}", "usage": {"output_tokens": 100}},
                    end,
                )
            )
            if padding and i == count // 2:
                stream.write(
                    record(
                        "response_item",
                        {"type": "message", "role": "user", "content": "private" * padding},
                        end,
                    )
                )
    return evidence


def test_long_rollout_is_drained_without_tail_or_response_ring_loss(tmp_path):
    path = tmp_path / "long.jsonl"
    evidence = source(path, "task", padding=750000)
    assert path.stat().st_size > 4 * 1024 * 1024
    backfill = Backfill(budget=256 * 1024)
    backfill.add("task", path, Context(model="model-new"), live=True)
    samples = []
    polls = 0
    while backfill.pending:
        result, issues = backfill.poll(evidence, NOW)
        samples.extend(result)
        assert not issues
        polls += 1
        assert polls < 100
    assert polls > 10
    assert len(samples) == 400
    assert len({s.key for s in samples}) == 400
    assert all(s.tps == 100 and s.context.model == "model-old" for s in samples)
    assert "private" not in repr(samples)
    backfill.add("task", path, Context(), live=False)
    assert backfill.pending == 0
    history = History(tmp_path / "history.sqlite")
    history.append(samples, now=NOW)
    history.append(samples, now=NOW)
    assert len(history.read()) == 400
    history.close()


def test_append_partial_line_and_unavailable_file_are_retried(tmp_path):
    path = tmp_path / "short.jsonl"
    evidence = source(path, "task", count=1)
    data = path.read_bytes()
    path.write_bytes(data[:-15])
    backfill = Backfill()
    backfill.add("task", path, Context(), live=False)
    assert backfill.poll(evidence, NOW)[0] == []
    path.write_bytes(data)
    backfill.add("task", path, Context(), live=False)
    assert len(backfill.poll(evidence, NOW)[0]) == 1
    backfill.add("other", path, Context(), live=False)
    path.unlink()
    assert "source_unavailable" in backfill.poll(evidence, NOW)[1]
    path.write_bytes(data)
    backfill.add("other", path, Context(), live=False)
    assert len(backfill.poll(evidence, NOW)[0]) == 1


def test_rotation_during_recovery_restarts_parser_and_excludes_old_samples(tmp_path):
    path = tmp_path / "rotate.jsonl"
    evidence = source(path, "task", count=2)
    backfill = Backfill(budget=100)
    backfill.add("task", path, Context(), live=False)
    backfill.poll(evidence, NOW)
    source(path, "task", count=1)
    samples = []
    for _ in range(20):
        result, _ = backfill.poll(evidence, NOW + 8 * 86400)
        samples.extend(result)
    assert samples == []
    assert backfill.pending == 0


def bind_cache(backfill, history):
    backfill.checkpoints = history.source_checkpoints()
    backfill.checkpoint_key = history.source_key


def recover(backfill, history, evidence):
    recovered = []
    while backfill.pending:
        samples, issues = backfill.poll(evidence, NOW)
        assert not issues
        recovered.extend(samples)
        history.append(samples, now=NOW)
        history.save_checkpoints(backfill.completed, now=NOW)
        backfill.completed.clear()
    return recovered


def test_restart_reuses_committed_unchanged_source_without_reading_body(tmp_path, monkeypatch):
    from token_pulse.tailer import JsonlTail

    path = tmp_path / "private-session.jsonl"
    evidence = source(path, "private-task", count=2)
    history = History(tmp_path / "history.sqlite")
    first = Backfill()
    bind_cache(first, history)
    first.add("private-task", path, Context(), live=False)
    assert len(recover(first, history, evidence)) == 2
    rows = history.connection.execute("SELECT * FROM completed_sources").fetchall()
    assert len(rows) == 1
    assert "private" not in repr(rows) and str(tmp_path) not in repr(rows)
    history.close()
    history = History(tmp_path / "history.sqlite")
    second = Backfill()
    bind_cache(second, history)
    second.add("private-task", path, Context(), live=False)

    def unexpected_read(self):
        raise AssertionError("Unchanged historical body should not be reread")

    monkeypatch.setattr(JsonlTail, "read", unexpected_read)
    assert second.poll(evidence, NOW)[0] == []
    assert second.reused == 1 and second.pending == 0
    assert len(history.read()) == 2
    history.clear()
    assert history.source_checkpoints() == {}
    history.close()


@pytest.mark.parametrize("change", ["file", "evidence", "parser", "context"])
def test_changed_source_or_evidence_replays_instead_of_trusting_checkpoint(
    tmp_path, monkeypatch, change
):
    import token_pulse.backfill as module

    path = tmp_path / "session.jsonl"
    evidence = source(path, "task", count=2)
    history = History(tmp_path / "history.sqlite")
    first = Backfill()
    bind_cache(first, history)
    first.add("task", path, Context(), live=False)
    recover(first, history, evidence)
    context = Context()
    if change == "file":
        evidence = source(path, "task", count=3)
    elif change == "evidence":
        evidence.starts.clear()
    elif change == "parser":
        monkeypatch.setattr(module, "RECOVERY_REVISION", module.RECOVERY_REVISION + 1)
    else:
        context = Context(model="changed-fallback")
    second = Backfill()
    bind_cache(second, history)
    second.add("task", path, context, live=False)
    assert recover(second, history, evidence)
    assert second.reused == 0
    history.close()


def test_interrupted_recovery_and_partial_line_never_mark_source_complete(tmp_path):
    path = tmp_path / "session.jsonl"
    evidence = source(path, "task", count=3)
    history = History(tmp_path / "history.sqlite")
    first = Backfill(budget=120)
    bind_cache(first, history)
    first.add("task", path, Context(), live=False)
    first.poll(evidence, NOW)
    assert first.pending == 1
    assert first.completed == {}
    assert history.source_checkpoints() == {}
    path.write_bytes(path.read_bytes()[:-5])
    second = Backfill()
    bind_cache(second, history)
    second.add("task", path, Context(), live=False)
    recover(second, history, evidence)
    assert history.source_checkpoints() == {}
    history.close()


def test_late_diagnostic_evidence_rechecks_completed_historical_file(tmp_path):
    path = tmp_path / "session.jsonl"
    complete = source(path, "task", count=1)
    missing = LogEvidence()
    backfill = Backfill()
    backfill.add("task", path, Context(), live=False, evidence=missing)
    values, _ = backfill.poll(missing, NOW)
    assert values[0].tps is None
    backfill.add("task", path, Context(), live=False, evidence=missing)
    assert backfill.pending == 0
    backfill.add("task", path, Context(), live=False, evidence=complete)
    assert backfill.pending == 1
    values, _ = backfill.poll(complete, NOW)
    assert values[0].tps == 100
