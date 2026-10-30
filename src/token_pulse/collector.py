"""Bounded discovery and polling, suitable for a background worker."""

from __future__ import annotations

import os
import re
import sqlite3
import time
import tomllib
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from token_pulse.backfill import Backfill
from token_pulse.domain import Context, Sample, Task
from token_pulse.logs import (
    LogEvidence,
    SqliteLogs,
    compatible_database,
    database_candidates,
    label,
    read_only,
)
from token_pulse.rollout import REJECTIONS, Rollout
from token_pulse.stats import unique
from token_pulse.tailer import JsonlTail
from token_pulse.transport_logs import REQUIRED_COLUMNS, TransportLogs


def resolved_path(path: Path) -> Path:
    path = path.expanduser().resolve()
    # Windows' extended-length spelling can identify the same local file but fail
    # pathlib's containment comparison against the ordinary source-root spelling.
    value = str(path)
    if os.name == "nt" and value.startswith("\\\\?\\"):
        value = "\\\\" + value[8:] if value.startswith("\\\\?\\UNC\\") else value[4:]
        return Path(value)
    return path


def codex_home() -> Path:
    return resolved_path(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))


def data_home() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "TokenPulse"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "token-pulse"


def rollout_path(root: Path, value: str) -> Path | None:
    path = resolved_path(Path(value))
    if not path.is_relative_to(root):
        return None
    if path.suffix == ".jsonl":
        compressed = resolved_path(Path(str(path) + ".zst"))
        if not path.exists() and compressed.is_relative_to(root) and compressed.is_file():
            return compressed
        return path
    if path.name.endswith(".jsonl.zst"):
        return path
    return None


def newest_database(root: Path, stem: str) -> Path | None:
    candidates = []
    for path in root.glob(f"{stem}_*.sqlite"):
        match = re.fullmatch(rf"{stem}_(\d+)\.sqlite", path.name)
        if match:
            candidates.append((int(match[1]), path))
    return max(candidates)[1] if candidates else None


@dataclass(frozen=True)
class Snapshot:
    tasks: tuple[Task, ...] = ()
    samples: tuple[Sample, ...] = ()
    diagnostics: tuple[str, ...] = ()
    at: float = 0
    backfill_pending: int = 0
    backfill_completed: int = 0
    rejected_samples: tuple[Sample, ...] = ()
    backfill_reused: int = 0
    transport_pending: int = 0


class Collector:
    def __init__(self, root: Path, *, max_tasks: int = 32):
        self.root = resolved_path(root)
        self.max_tasks = max_tasks
        self.evidence = LogEvidence()
        self.logs: SqliteLogs | None = None
        self.watched: dict[str, tuple[JsonlTail, Rollout]] = {}
        self.next_discovery = 0.0
        self.configured_tier: str | None = None
        self.discovery_issues: set[str] = set()
        self.backfill = Backfill()
        self.next_backfill_discovery = 0.0
        self.transport_readers: dict[Path, TransportLogs] = {}
        self.transport_checkpoints: dict[str, tuple] = {}
        self.transport_completed: dict[str, tuple] = {}
        self.transport_key = None
        self.transport_digest = None
        self.next_transport_discovery = 0.0
        self.retired_transport: list[Sample] = []

    def _discover_transports(self, now: float) -> None:
        selected: dict[Path, TransportLogs] = {}
        candidates = [
            *database_candidates(self.root, "logs"),
            *database_candidates(self.root, "state"),
        ]
        for path in candidates:
            try:
                with closing(read_only(path)) as db:
                    columns = {row[1] for row in db.execute("PRAGMA table_info(logs)")}
                    if not REQUIRED_COLUMNS <= columns:
                        continue
                reader = self.transport_readers.get(path)
                if reader is None:
                    reader = TransportLogs(path)
                    if self.transport_key and self.transport_digest:
                        reader.digest = self.transport_digest
                        reader.checkpoint = self.transport_checkpoints.get(self.transport_key(path))
                selected[path] = reader
                if len(selected) >= 8:
                    break
            except (OSError, sqlite3.Error):
                self.discovery_issues.add("logs_unavailable")
        for path, reader in self.transport_readers.items():
            if path not in selected:
                reader.transport.reset()
                self.retired_transport.extend(reader.transport.drain())
        self.transport_readers = selected
        self.next_transport_discovery = now + 60

    def _discover(self, now: float) -> None:
        self.discovery_issues.clear()
        try:
            with (self.root / "config.toml").open("rb") as stream:
                config = tomllib.load(stream)
            self.configured_tier = label(config.get("service_tier"))
        except FileNotFoundError:
            self.configured_tier = None
        except (OSError, ValueError):
            self.configured_tier = None
            self.discovery_issues.add("config_unavailable")
        log_columns = {"id", "ts", "ts_nanos", "thread_id", "target", "feedback_log_body"}
        logs_path = compatible_database(self.root, "logs", "logs", log_columns)
        if logs_path is None:
            logs_path = compatible_database(self.root, "state", "logs", log_columns)
        if logs_path and (self.logs is None or self.logs.path != logs_path):
            self.logs = SqliteLogs(logs_path, self.evidence)
        if logs_path is None:
            self.logs = None
            self.discovery_issues.add("logs_unavailable")
        if now >= self.next_transport_discovery:
            self._discover_transports(now)
        found: list[tuple[str, Path, str, Context]] = []
        optional = None
        state = compatible_database(
            self.root, "state", "threads", {"id", "rollout_path", "updated_at"}
        )
        if state:
            try:
                with closing(read_only(state)) as connection:
                    columns = {r[1] for r in connection.execute("PRAGMA table_info(threads)")}
                    optional = [
                        name if name in columns else f"NULL AS {name}"
                        for name in ("model", "reasoning_effort", "model_provider")
                    ]
                    title_column = "title" if "title" in columns else "NULL"
                    archive_filter = "archived=0" if "archived" in columns else "1"
                    updated = (
                        "COALESCE(updated_at_ms, updated_at * 1000) / 1000.0"
                        if "updated_at_ms" in columns
                        else "updated_at"
                    )
                    rows = connection.execute(
                        f"SELECT id, rollout_path, {title_column}, "
                        + ", ".join(optional)
                        + f" FROM threads WHERE {archive_filter} ORDER BY {updated} DESC LIMIT ?",
                        (self.max_tasks,),
                    ).fetchall()
                for task_id, path, title, model, effort, provider in rows:
                    path = rollout_path(self.root, path)
                    if path is not None and path.suffix == ".jsonl":
                        # Titles are for this in-memory UI only; never part of a Sample.
                        found.append(
                            (
                                task_id,
                                path,
                                str(title or "")[:160],
                                Context(label(model), label(effort), label(provider)),
                            )
                        )
            except (OSError, sqlite3.Error, TypeError, ValueError):
                self.discovery_issues.add("state_unavailable")
        if not found:
            # Fallback probes recent date directories, never a recursive full-history scan.
            recent = datetime.fromtimestamp(now, UTC)
            directories = [self.root / "sessions"]
            directories += [
                self.root / "sessions" / (recent - timedelta(days=i)).strftime("%Y/%m/%d")
                for i in range(3)
            ]
            candidates = []
            for directory in directories:
                for path in directory.glob("*.jsonl"):
                    match = re.search(r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", path.name)
                    if match:
                        candidates.append((path.stat().st_mtime, match[1], path))
            for _, task_id, path in sorted(candidates, reverse=True)[: self.max_tasks]:
                found.append((task_id, path, "", Context()))
        selected = {item[0] for item in found}
        self.watched = {key: value for key, value in self.watched.items() if key in selected}
        for task_id, path, title, context in found:
            if task_id not in self.watched or self.watched[task_id][0].path != path:
                self.watched[task_id] = (JsonlTail(path), Rollout(task_id, title, context))
            elif title:
                self.watched[task_id][1].title = title
        if state and optional is not None and now >= self.next_backfill_discovery:
            try:
                with closing(read_only(state)) as connection:
                    rows = connection.execute(
                        "SELECT id, rollout_path, "
                        + ", ".join(optional)
                        + f" FROM threads WHERE {updated} >= ? ORDER BY {updated} DESC",
                        (now - 604800,),
                    )
                    for task_id, path, model, effort, provider in rows:
                        path = rollout_path(self.root, path)
                        if path is None:
                            continue
                        try:
                            self.backfill.add(
                                task_id,
                                path,
                                Context(label(model), label(effort), label(provider)),
                                live=task_id in self.watched,
                                evidence=self.evidence,
                            )
                        except OSError:
                            self.discovery_issues.add("source_unavailable")
            except (OSError, sqlite3.Error, TypeError, ValueError):
                self.discovery_issues.add("state_unavailable")
            self.next_backfill_discovery = now + 60
        self.next_discovery = now + 10

    def poll(self, now: float | None = None) -> Snapshot:
        now = time.time() if now is None else now
        issues: set[str] = set()
        if now >= self.next_discovery:
            try:
                self._discover(now)
            except OSError:
                self.discovery_issues.add("source_unavailable")
                self.next_discovery = now + 10
        issues.update(self.discovery_issues)
        if self.logs:
            # Drain at most 10k rows per poll, allowing rollout/log arrival to differ.
            for _ in range(2):
                issue = self.logs.poll()
                if issue:
                    issues.add(issue)
                    break
        tasks, samples = [], []
        samples.extend(self.retired_transport)
        self.retired_transport.clear()
        for tail, rollout in self.watched.values():
            batch = tail.read()
            if batch.reset or batch.skipped_prefix:
                rollout.reset()
            if batch.malformed or batch.oversized:
                issues.add("records_skipped")
            if batch.error:
                issues.add(batch.error)
            gaps = set(batch.gaps)
            for index, record in enumerate(batch.records):
                if index in gaps:
                    rollout.damage("record_gap")
                rollout.feed(record)
            if len(batch.records) in gaps:
                rollout.damage("record_gap")
            task = rollout.task(self.evidence)
            task = replace(
                task, context=replace(task.context, configured_tier=self.configured_tier)
            )
            if batch.error:
                task = replace(task, state="unknown")
            tasks.append(task)
            samples.extend(rollout.samples(self.evidence))
        if not tasks:
            issues.add("no_sessions")
        recovered, recovery_issues = self.backfill.poll(self.evidence, now)
        issues.update(recovery_issues)
        remaining = 8 * 1024 * 1024
        for path, reader in self.transport_readers.items():
            if remaining < 4 * 1024 * 1024:
                reader.pending = True
                continue
            values, transport_issues, used = reader.poll(now, budget=4 * 1024 * 1024)
            samples.extend(values)
            issues.update(transport_issues)
            remaining -= used
            if reader.completed and self.transport_key:
                self.transport_completed[self.transport_key(path)] = reader.completed
                reader.completed = None
        # Share the global byte budget fairly when several retained stores have a backlog.
        if self.transport_readers:
            first_path = next(iter(self.transport_readers))
            self.transport_readers[first_path] = self.transport_readers.pop(first_path)
        observed = [s for s in (*recovered, *samples) if s.issue not in REJECTIONS]
        rejected = [s for s in (*recovered, *samples) if s.issue in REJECTIONS]
        return Snapshot(
            tuple(sorted(tasks, key=lambda t: t.updated_at, reverse=True)),
            tuple(unique(observed)),
            tuple(sorted(issues)),
            now,
            self.backfill.pending,
            len(self.backfill.done),
            tuple(rejected),
            self.backfill.reused,
            sum(reader.pending for reader in self.transport_readers.values()),
        )
