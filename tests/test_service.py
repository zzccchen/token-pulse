import time

from token_pulse.backfill import Backfill
from token_pulse.service import Monitor


def test_worker_stops_and_missing_source_remains_explainable(tmp_path):
    monitor = Monitor(tmp_path / "missing", tmp_path / "data", interval=0.1)
    monitor.start()
    deadline = time.monotonic() + 5
    update = None
    while update is None and time.monotonic() < deadline:
        update = monitor.latest()
        time.sleep(0.01)
    monitor.stop()
    assert not monitor.thread.is_alive()
    assert update is not None
    assert "no_sessions" in update.snapshot.diagnostics
    assert (tmp_path / "data/history.sqlite").exists()
    assert not (tmp_path / "missing").exists()


def test_worker_unifies_disk_and_live_ids_without_double_counting(tmp_path, monkeypatch):
    from token_pulse.collector import Snapshot
    from token_pulse.domain import Sample
    from token_pulse.history import History

    now = time.time()
    sample = Sample("private-task", "private-turn", "private-response", now - 1, 100, now - 11)
    history = History(tmp_path / "data" / "history.sqlite")
    history.append([sample], now=now)
    expected = history.read()[0]
    history.close()

    class SyntheticCollector:
        def __init__(self, root):
            from token_pulse.logs import LogEvidence

            self.evidence = LogEvidence()
            self.backfill = Backfill()

        def poll(self):
            return Snapshot(samples=(sample,), at=now)

    monkeypatch.setattr("token_pulse.service.Collector", SyntheticCollector)
    monitor = Monitor(tmp_path / "source", tmp_path / "data", interval=0.1)
    monitor.start()
    try:
        deadline = time.monotonic() + 5
        update = None
        while update is None and time.monotonic() < deadline:
            update = monitor.latest()
            time.sleep(0.01)
        assert update.history == (expected,)
        assert update.history[0].task_id != sample.task_id
    finally:
        monitor.stop()


def test_worker_withdraws_copied_history_and_does_not_publish_it_again(tmp_path, monkeypatch):
    from dataclasses import replace

    from token_pulse.collector import Snapshot
    from token_pulse.domain import Sample
    from token_pulse.history import History
    from token_pulse.logs import LogEvidence

    now = time.time()
    sample = Sample("private-task", "private-turn", "private-response", now - 1, 100, now - 11)
    history = History(tmp_path / "data" / "history.sqlite")
    history.append([sample], now=now)
    history.close()

    class SyntheticCollector:
        def __init__(self, root):
            self.evidence = LogEvidence()
            self.backfill = Backfill()

        def poll(self):
            rejected = replace(sample, issue="inherited_history", parser_revision=2)
            return Snapshot(samples=(sample,), rejected_samples=(rejected,), at=now)

    monkeypatch.setattr("token_pulse.service.Collector", SyntheticCollector)
    monitor = Monitor(tmp_path / "source", tmp_path / "data", interval=0.1)
    monitor.start()
    try:
        deadline = time.monotonic() + 5
        update = None
        while update is None and time.monotonic() < deadline:
            update = monitor.latest()
            time.sleep(0.01)
        assert update is not None
        assert update.history == ()
    finally:
        monitor.stop()


def test_failed_chunk_is_retried_before_completion_checkpoint_is_saved(tmp_path, monkeypatch):
    from token_pulse.collector import Snapshot
    from token_pulse.domain import Sample
    from token_pulse.history import History
    from token_pulse.logs import LogEvidence

    now = time.time()
    one = Sample("task", "turn", "one", now - 2, 100, now - 12)
    two = Sample("task", "turn", "two", now - 1, 200, now - 11)
    checks = []

    class SyntheticCollector:
        def __init__(self, root):
            self.evidence = LogEvidence()
            self.backfill = Backfill()
            self.polls = 0

        def poll(self):
            self.polls += 1
            if self.polls == 1:
                self.backfill.completed["anonymous-source"] = ("fingerprint", now)
                return Snapshot(samples=(one,), at=now)
            return Snapshot(samples=(two,), at=now)

    original_append = History.append
    original_save = History.save_checkpoints
    attempts = 0

    def append(self, samples, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("synthetic temporary write failure")
        return original_append(self, samples, **kwargs)

    def save(self, values, **kwargs):
        assert attempts >= 2
        assert len(self.read()) == 2
        checks.append(True)
        return original_save(self, values, **kwargs)

    monkeypatch.setattr("token_pulse.service.Collector", SyntheticCollector)
    monkeypatch.setattr(History, "append", append)
    monkeypatch.setattr(History, "save_checkpoints", save)
    monitor = Monitor(tmp_path, tmp_path / "data", interval=0.1)
    monitor.start()
    try:
        deadline = time.monotonic() + 5
        while not checks and time.monotonic() < deadline:
            time.sleep(0.01)
        assert checks
    finally:
        monitor.stop()
    history = History(tmp_path / "data/history.sqlite")
    assert len(history.read()) == 2
    assert history.source_checkpoints() == {"anonymous-source": "fingerprint"}
    history.close()
