"""One background owner for collection and SQLite; the UI only reads snapshots."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from token_pulse.collector import Collector, Snapshot
from token_pulse.domain import Sample
from token_pulse.evidence_archive import EvidenceArchive
from token_pulse.history import History
from token_pulse.retention import HISTORY_SECONDS
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
        archive = None
        historical: tuple[Sample, ...] = ()
        pending: list[Sample] = []
        rejected: dict[tuple, Sample] = {}
        retry_snapshot = None
        try:
            while not self.stopped.is_set():
                history_error = False
                if history is None:
                    candidate = None
                    try:
                        candidate = History(self.data_dir / "history.sqlite")
                        candidate.append([], now=time.time())
                        historical = tuple(candidate.read(now=time.time()))
                        collector.backfill.checkpoints = candidate.source_checkpoints()
                        collector.backfill.checkpoint_key = candidate.source_key
                        if hasattr(collector, "desktop_logs"):
                            collector.desktop_logs.checkpoints = candidate.source_checkpoints()
                            collector.desktop_logs.checkpoint_key = candidate.source_key
                        collector.transport_checkpoints = candidate.transport_checkpoints()
                        collector.transport_key = partial(candidate.source_key, "transport")
                        collector.transport_digest = partial(
                            candidate.anonymous_id, "transport-cursor"
                        )
                        archive = EvidenceArchive(
                            candidate.connection,
                            candidate.anonymous_id,
                            self.root,
                            retention_days=candidate.retention_days,
                        )
                        archive.restore(collector.evidence, now=time.time())
                        history = candidate
                    except Exception:
                        if candidate is not None:
                            candidate.close()
                        archive = None
                        history_error = True
                if retry_snapshot is not None:
                    snapshot = retry_snapshot
                elif history is None:
                    snapshot = Snapshot(at=time.time())
                else:
                    try:
                        snapshot = collector.poll()
                    except Exception:
                        # Fail closed without exposing raw source data or paths.
                        snapshot = Snapshot(diagnostics=("monitor_error",), at=time.time())
                pending = unique([*pending, *snapshot.samples])
                for sample in snapshot.rejected_samples:
                    previous = rejected.get(sample.key)
                    if previous is None or previous.issue not in {
                        "inherited_history",
                        "superseded_usage",
                    }:
                        rejected[sample.key] = sample
                try:
                    if history is None:
                        raise RuntimeError("History is unavailable")
                    if archive is not None:
                        archive.save(collector.evidence, now=snapshot.at)
                    history.reject(list(rejected.values()), now=snapshot.at)
                    history.append(pending, now=snapshot.at)
                    historical = tuple(
                        history.enrich_interruptions(
                            history.enrich_config_tags(
                                history.enrich_submissions(
                                    history.read(now=snapshot.at), collector.evidence.submissions
                                ),
                                collector.evidence.config_tags,
                            ),
                            collector.evidence.interruptions,
                            archived=collector.evidence.archived_interruptions,
                        )
                    )
                    if collector.backfill.completed:
                        history.save_checkpoints(collector.backfill.completed, now=snapshot.at)
                        collector.backfill.checkpoints.update(
                            (key, value[0]) for key, value in collector.backfill.completed.items()
                        )
                        collector.backfill.completed.clear()
                    if getattr(collector, "transport_completed", None):
                        history.save_transport_checkpoints(
                            collector.transport_completed, now=snapshot.at
                        )
                        collector.transport_completed.clear()
                    if (
                        getattr(collector, "desktop_logs", None)
                        and collector.desktop_logs.completed
                    ):
                        history.save_checkpoints(collector.desktop_logs.completed, now=snapshot.at)
                        collector.desktop_logs.checkpoints.update(
                            (key, value[0])
                            for key, value in collector.desktop_logs.completed.items()
                        )
                        collector.desktop_logs.completed.clear()
                    pending.clear()
                    rejected.clear()
                    retry_snapshot = None
                except Exception:
                    history_error = True
                    # Backpressure keeps one bounded collection batch until durable.
                    # Never discard older pending outputs to make room for new ones.
                    if history is not None:
                        retry_snapshot = snapshot
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
                                    and snapshot.at - HISTORY_SECONDS <= s.ended_at <= snapshot.at
                                ),
                            ]
                        )
                    )
                self._publish(Update(snapshot, combined))
                # Historical recovery runs in bounded chunks and publishes each chunk;
                # idle/live polling returns to the normal low-frequency interval.
                self.stopped.wait(
                    min(self.interval, 0.25)
                    if not history_error
                    and (
                        snapshot.backfill_pending
                        or snapshot.transport_pending
                        or snapshot.activity_pending
                    )
                    else self.interval
                )
        finally:
            if history is not None:
                history.close()
