"""Budgeted seven-day rollout recovery, independent of the live task limit."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from token_pulse.domain import Context, Sample
from token_pulse.logs import LogEvidence
from token_pulse.rollout import Rollout
from token_pulse.tailer import JsonlTail, open_tail


@dataclass
class Source:
    task: str
    path: Path
    context: Context
    version: tuple[int, int]
    identity: tuple[int, int] = (0, 0)


# Bump when rollout interpretation changes so unchanged files are re-evaluated.
RECOVERY_REVISION = 16


def fingerprint(source: Source, evidence: LogEvidence) -> str:
    values = (
        RECOVERY_REVISION,
        source.version,
        source.identity,
        asdict(source.context),
        sorted((item, asdict(start)) for item, start in evidence.start_fingerprint(source.task)),
        sorted(
            (turn, asdict(setting))
            for (task, turn), setting in evidence.submissions.items()
            if task == source.task
        ),
        sorted(
            (turn, asdict(tag))
            for (task, turn), tag in evidence.config_tags.items()
            if task == source.task
        ),
        evidence.interruption_fingerprint(source.task),
        evidence.activity_fingerprint(source.task),
    )
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


class Backfill:
    """Recover indexed history independently of the live task count and tail cache.

    Completion is provisional until the caller persists all returned samples and
    commits the corresponding checkpoint. Interrupted files must be re-evaluated.
    """

    def __init__(self, *, budget: int = 8 * 1024 * 1024):
        self.budget = budget
        self.queue: deque[Source] = deque()
        self.queued: set[str] = set()
        self.done: dict[str, tuple[Path, tuple[int, int]]] = {}
        self.done_stamps: dict[str, str] = {}
        self.done_through: dict[str, float] = {}
        self.active: tuple[Source, JsonlTail, Rollout] | None = None
        self.checkpoints: dict[str, str] = {}
        self.completed: dict[str, tuple[str, float]] = {}
        self.checkpoint_key: Callable[[str, Path], str] | None = None
        self.reused = 0
        self.active_stamp = ""
        self.active_evidence_changed_at = float("inf")

    def evidence_changed(self, task: str, at: float) -> None:
        """Revisit older live history when late proof predates its last scan.

        New output after that scan belongs to live collection. A reused checkpoint
        has no in-memory boundary yet and is rechecked once when evidence changes.
        """
        if task in self.done and at <= self.done_through.get(task, float("inf")):
            previous = self.done.pop(task)
            if self.checkpoint_key:
                self.completed.pop(self.checkpoint_key(task, previous[0]), None)
            self.done_stamps.pop(task, None)
            self.done_through.pop(task, None)
        if self.active and self.active[0].task == task:
            self.active_evidence_changed_at = min(self.active_evidence_changed_at, at)

    def add(
        self,
        task: str,
        path: Path,
        context: Context,
        *,
        live: bool,
        evidence: LogEvidence | None = None,
    ) -> None:
        if task in self.queued:
            return
        stat = path.stat()
        version = stat.st_size, stat.st_mtime_ns
        source = Source(task, path, context, version, (stat.st_dev, stat.st_ino))
        previous = self.done.get(task)
        if previous and previous[0] == path:
            if live or (
                previous[1] == version
                and (
                    evidence is None or self.done_stamps.get(task) == fingerprint(source, evidence)
                )
            ):
                return
        self.queue.append(source)
        self.queued.add(task)

    @property
    def pending(self) -> int:
        return len(self.queued)

    def poll(self, evidence: LogEvidence, now: float) -> tuple[list[Sample], set[str]]:
        samples: list[Sample] = []
        issues: set[str] = set()
        remaining = self.budget
        # Also bound filesystem operations when there are many empty/unreadable files.
        for _ in range(16):
            if remaining <= 0:
                break
            if self.active is None:
                if not self.queue:
                    break
                source = self.queue.popleft()
                key = self.checkpoint_key(source.task, source.path) if self.checkpoint_key else None
                self.active_stamp = fingerprint(source, evidence)
                self.active_evidence_changed_at = float("inf")
                if key and self.checkpoints.get(key) == self.active_stamp:
                    self.done[source.task] = source.path, source.version
                    self.done_stamps[source.task] = self.active_stamp
                    self.done_through.pop(source.task, None)
                    self.queued.discard(source.task)
                    self.reused += 1
                    continue
                self.active = (
                    source,
                    open_tail(source.path, backfill=None),
                    Rollout(source.task, context=source.context),
                )
            source, tail, rollout = self.active
            tail.budget = min(2 * 1024 * 1024, remaining)
            before = tail.offset
            batch = tail.read()
            remaining -= max(
                1, batch.bytes_read, tail.offset if batch.reset else tail.offset - before
            )
            if batch.reset:
                try:
                    stat = source.path.stat()
                    source.version = stat.st_size, stat.st_mtime_ns
                    source.identity = stat.st_dev, stat.st_ino
                    self.active_stamp = fingerprint(source, evidence)
                except OSError:
                    batch.error = "source_unavailable"
                rollout = Rollout(source.task, context=source.context)
                self.active = source, tail, rollout
            if batch.error:
                issues.add(batch.error)
                self.queued.discard(source.task)
                self.active = None  # Retry at the next index discovery.
                continue
            if batch.malformed or batch.oversized:
                issues.add("records_skipped")
            gaps = set(batch.gaps)

            def drain(rollout: Rollout = rollout) -> None:
                samples.extend(
                    s for s in rollout.samples(evidence) if now - 604800 <= s.ended_at <= now
                )
                rollout.responses.clear()
                rollout.corrections.clear()

            for index, record in enumerate(batch.records):
                if index in gaps:
                    rollout.damage("record_gap")
                rollout.feed(record)
                # Persist each chunk before the live parser's 300-response ring evicts it.
                if len(rollout.responses) >= 128 or len(rollout.corrections) >= 128:
                    drain()
            if len(batch.records) in gaps:
                rollout.damage("record_gap")
            drain()
            if batch.eof:
                # An appended partial line is picked up by live tailing or rediscovery.
                # Re-reading a changed file is safe: history uses stable sample keys.
                try:
                    stat = source.path.stat()
                    unchanged = (stat.st_size, stat.st_mtime_ns) == source.version
                    unchanged &= (stat.st_dev, stat.st_ino) == source.identity
                except OSError:
                    unchanged = False
                    issues.add("source_unavailable")
                if (
                    unchanged
                    and not tail.pending
                    and not tail.dropping
                    and self.active_evidence_changed_at > rollout.updated_at
                ):
                    self.done[source.task] = source.path, source.version
                    self.done_stamps[source.task] = self.active_stamp
                    self.done_through[source.task] = rollout.updated_at
                    if self.checkpoint_key:
                        key = self.checkpoint_key(source.task, source.path)
                        self.completed[key] = self.active_stamp, now
                self.queued.discard(source.task)
                self.active = None
        return samples, issues
