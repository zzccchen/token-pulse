"""One background owner for collection and SQLite; the UI only reads snapshots."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from token_pulse.collector import Collector, Snapshot
from token_pulse.domain import Sample
from token_pulse.history import History
from token_pulse.stats import unique


@dataclass(frozen=True)
class Update:
    snapshot: Snapshot
    history: tuple[Sample, ...] = ()


class Monitor:
    def __init__(self, root: Path, data_dir: Path, *, interval: float = 2):
        self.root, self.data_dir = root, data_dir
        self.interval = max(0.1, interval)
        self.queue: queue.Queue[Update] = queue.Queue(maxsize=1)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name="token-pulse-collector", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        self.thread.join(timeout=10)

    def latest(self) -> Update | None:
        try:
            return self.queue.get_nowait()
        except queue.Empty:
            return None

    def _publish(self, update: Update) -> None:
        try:
            self.queue.get_nowait()
        except queue.Empty:
            pass
        self.queue.put_nowait(update)

    def _run(self) -> None:
        collector = Collector(self.root)
        history = None
        historical: tuple[Sample, ...] = ()
        pending: list[Sample] = []
        rejected: dict[tuple, Sample] = {}
        try:
            while not self.stopped.is_set():
                history_error = False
                if history is None:
                    candidate = None
                    try:
                        candidate = History(self.data_dir / "history.sqlite")
                        historical = tuple(candidate.read())
                        collector.backfill.checkpoints = candidate.source_checkpoints()
                        collector.backfill.checkpoint_key = candidate.source_key
                        history = candidate
                    except Exception:
                        if candidate is not None:
                            candidate.close()
                        history_error = True
                try:
                    snapshot = collector.poll()
                except Exception:
                    # Fail closed without putting raw source data or paths in UI diagnostics.
                    snapshot = Snapshot(diagnostics=("monitor_error",), at=time.time())
                limit = history.max_samples if history else 10000
                pending = unique([*pending, *snapshot.samples])[-limit:]
                for sample in snapshot.rejected_samples:
                    previous = rejected.get(sample.key)
                    if previous is None or previous.issue != "inherited_history":
                        rejected[sample.key] = sample
                if len(rejected) > 2 * limit:
                    rejected = dict(list(rejected.items())[-2 * limit :])
                try:
                    if history is None:
                        raise RuntimeError("History is unavailable")
                    history.reject(list(rejected.values()), now=snapshot.at)
                    history.append(pending, now=snapshot.at)
                    historical = tuple(
                        history.enrich_submissions(history.read(), collector.evidence.submissions)
                    )
                    if collector.backfill.completed:
                        history.save_checkpoints(collector.backfill.completed, now=snapshot.at)
                        collector.backfill.checkpoints.update(
                            (key, value[0]) for key, value in collector.backfill.completed.items()
                        )
                        collector.backfill.completed.clear()
                    pending.clear()
                    rejected.clear()
                except Exception:
                    history_error = True
                if history_error:
                    snapshot = replace(
                        snapshot, diagnostics=(*snapshot.diagnostics, "history_unavailable")
                    )
                # Use the same anonymous keys for disk and live samples. Concatenating
                # raw live identifiers with history would double-count every output.
                combined = historical
                if history is not None:
                    combined = tuple(
                        unique(
                            [
                                *historical,
                                *(
                                    history.anonymous(s)
                                    for s in [*pending, *snapshot.samples]
                                    if not history.is_rejected(s)
                                ),
                            ]
                        )
                    )
                self._publish(Update(snapshot, combined))
                # Historical recovery runs in bounded chunks and publishes each chunk;
                # idle/live polling returns to the normal low-frequency interval.
                self.stopped.wait(
                    min(self.interval, 0.25) if snapshot.backfill_pending else self.interval
                )
        finally:
            if history is not None:
                history.close()
