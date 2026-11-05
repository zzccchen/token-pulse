"""Bounded desktop notifications with explicit ownership and writer continuity."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from token_pulse.logs import LogEvidence
from token_pulse.retention import HISTORY_DAYS
from token_pulse.rollout import timestamp
from token_pulse.stream_activity import ACTIVITY_REVISION, Activity, merge_activity
from token_pulse.tailer import Batch, JsonlTail, json_object

HEADER = re.compile(r"^(\S+) (info|warning) \[([^\]]+)\] (.*)$")
BINDING = "mcp_server_startup_status_updated "
SUMMARY = "Reasoning summary part added payload="
COMPLETED = "Reasoning summary item completed "
HOST = re.compile(
    r"hostId=([^\s=]+) server=[^\s=]+ "
    r"status=(?:starting|ready|failed|cancelled) threadId=([A-Za-z0-9_-]+)"
)
SUFFIX = re.compile(
    r"(?: rendererWebContentsId=\d+ rendererWindowAppearance=[A-Za-z_-]+ "
    r"rendererWindowFocused=(?:true|false) rendererWindowId=\d+ "
    r"rendererWindowVisible=(?:true|false))?"
)
WRITER = re.compile(
    r"(codex-desktop-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
    r"-\d+-t\d+-i\d+)-(\d{6})-([0-4])\.log"
)
# Supported file-logger format: rotate before the next line exceeds 10 MiB.
SEGMENT_BYTES = 10 * 1024 * 1024
MAX_LINE = 1024 * 1024


@dataclass(frozen=True)
class Segment:
    writer: str
    day: date
    clock: str
    index: int


def segment(path: Path) -> Segment | None:
    match = WRITER.fullmatch(path.name)
    if match is None:
        return None
    try:
        day = date(*map(int, path.parts[-4:-1]))
        datetime.strptime(match[2], "%H%M%S")
    except (TypeError, ValueError):
        return None
    return Segment(match[1], day, match[2], int(match[3]))


def file_version(path: Path) -> tuple:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def continuous(previous: DesktopFile, current: DesktopFile) -> bool:
    before, after = segment(previous.tail.path), segment(current.tail.path)
    if (
        before is None
        or after is None
        or before.writer != after.writer
        or previous.version is None
        or previous.tail.last_at is None
        or current.tail.first_at is None
        or previous.tail.last_at > current.tail.first_at
        or datetime.fromtimestamp(previous.tail.last_at, UTC).date() != before.day
        or datetime.fromtimestamp(current.tail.first_at, UTC).date() != after.day
    ):
        return False
    if after.day == before.day:
        return (
            after.clock == before.clock
            and after.index == before.index + 1
            and previous.version[2] <= SEGMENT_BYTES
            and previous.version[2] + current.tail.first_size > SEGMENT_BYTES
        )
    return (after.day - before.day).days == 1 and after.index == 0


def desktop_log_root() -> Path | None:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Codex" / "Logs"
    return None  # No unverified Linux path assumption.


class DesktopTail(JsonlTail):
    def __init__(self, path: Path):
        super().__init__(path, max_line=MAX_LINE, backfill=None)
        self.first_at: float | None = None
        self.last_at: float | None = None
        self.first_size = 0

    def _parse(self, data: bytes, batch: Batch) -> None:
        if batch.reset or batch.skipped_prefix:
            self.first_at = self.last_at = None
            self.first_size = 0
        first = self.first_size == 0
        super()._parse(data, batch)
        # Unread/invalid first-line bytes cannot prove a rotation seam.
        if first and batch.gaps:
            self.first_at = None
            self.first_size = -1

    def decode_line(self, line: bytes) -> dict | None:
        text = line.decode("utf-8").rstrip("\r\n")
        header = HEADER.fullmatch(text)
        self.last_at = timestamp(header[1]) if header else None
        if not self.first_size:
            self.first_size, self.first_at = len(line), self.last_at
        if text.startswith("[file-logger] dropped "):
            return {"kind": "gap"}
        if not header:
            return None
        date, level, logger, body = header.groups()
        binding = logger == "AppServerConnection" and body.startswith(BINDING)
        summary = logger in {
            "electron-message-handler",
            "host-app-server-manager",
        } and body.startswith(SUMMARY)
        completed = logger in {
            "electron-message-handler",
            "host-app-server-manager",
        } and body.startswith(COMPLETED)
        if not binding and not summary and not completed:
            return None
        at = timestamp(date)
        if at is None:
            raise ValueError("Invalid notification time")
        if binding:
            fields_text = body[len(BINDING) :]
            # Error values are JSON scalars. Skip them structurally; text inside
            # them cannot supply a host, task, event or any timing evidence.
            for name in ("error", "failureReason"):
                if not fields_text.startswith(name + "="):
                    return {"kind": "gap"}
                try:
                    value, end = json.JSONDecoder().raw_decode(fields_text[len(name) + 1 :])
                except ValueError:
                    return {"kind": "gap"}
                if value is not None and not isinstance(value, str):
                    return {"kind": "gap"}
                fields_text = fields_text[len(name) + 1 + end :]
                if not fields_text.startswith(" "):
                    return {"kind": "gap"}
                fields_text = fields_text[1:]
            fields = HOST.fullmatch(fields_text)
            if not fields:
                # Free-form error strings cannot supply outer host fields.
                return {"kind": "gap"}
            host, task = fields.groups()
            return {"kind": "host", "at": at, "task": task, "host": host}
        if level != "info":
            raise ValueError("Unsupported notification level")
        if completed:
            head = re.match(
                r"itemId=([A-Za-z0-9_-]{1,256})" + SUFFIX.pattern + r" summary=",
                body[len(COMPLETED) :],
            )
            if head is None:
                raise ValueError("Unsupported completion envelope")
            tail = body[len(COMPLETED) + head.end() :]
            parts, end = json.JSONDecoder().raw_decode(tail)
            fields = re.fullmatch(
                r" summaryPartCount=(\d+) threadId=([A-Za-z0-9_-]{1,256}) "
                r"turnId=([A-Za-z0-9_-]{1,256})",
                tail[end:],
            )
            if (
                fields is None
                or not isinstance(parts, list)
                or not all(isinstance(p, str) for p in parts)
                or len(parts) != int(fields[1])
            ):
                raise ValueError("Conflicting completion fields")
            return {
                "kind": "completed",
                "at": at,
                "task": fields[2],
                "turn": fields[3],
                "item": head[1],
            }
        value, end = json.JSONDecoder(object_pairs_hook=json_object).raw_decode(
            body[len(SUMMARY) :]
        )
        if type(value) is not dict or set(value) != {
            "threadId",
            "turnId",
            "itemId",
            "summaryIndex",
        }:
            raise ValueError("Unsupported activity envelope")
        if (
            not all(
                isinstance(value[k], str) and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value[k])
                for k in ("threadId", "turnId", "itemId")
            )
            or type(value["summaryIndex"]) is not int
            or value["summaryIndex"] < 0
        ):
            raise ValueError("Invalid activity identity")
        if not SUFFIX.fullmatch(body[len(SUMMARY) + end :]):
            raise ValueError("Unsupported notification suffix")
        return {
            "kind": "activity",
            "at": at,
            "task": value["threadId"],
            "turn": value["turnId"],
            "item": value["itemId"],
        }


class DesktopFile:
    def __init__(self, path: Path):
        self.tail = DesktopTail(path)
        self.hosts: dict[str, tuple[str, float]] = {}
        self.owners: dict[str, str] = {}
        self.observed: dict[tuple[str, str, str], Activity] = {}
        self.pending_activity: dict[tuple[str, str, str], Activity] = {}
        self.version: tuple | None = None
        self.blocked = False
        self.prefix: tuple | None = None
        self.initialized = False

    def inherit(self, previous: DesktopFile) -> None:
        self.hosts = previous.hosts.copy()
        self.owners = previous.owners.copy()
        self.observed = previous.observed.copy()
        self.pending_activity = previous.pending_activity.copy()
        self.blocked = previous.blocked

    def reset(self) -> None:
        self.hosts.clear()
        self.owners.clear()
        self.observed.clear()
        self.pending_activity.clear()
        self.blocked = False

    def gap(self) -> None:
        self.hosts.clear()
        self.pending_activity.clear()

    def overflow(self, evidence: LogEvidence) -> None:
        for key, value in self.observed.items():
            evidence.record_activity(*key, Activity(value.times, True))
        self.blocked = True

    def accept(self, record: dict, evidence: LogEvidence) -> None:
        kind = record["kind"]
        if kind == "gap":
            self.gap()
            return
        if self.blocked:
            return
        task, at = record["task"], record["at"]
        if kind == "host":
            previous = self.hosts.get(task)
            host = record["host"]
            owner = self.owners.get(task)
            if (owner is not None and owner != host) or (previous and at < previous[1]):
                host = "conflict"
                for key, value in self.observed.items():
                    if key[0] == task:
                        evidence.record_activity(*key, Activity(value.times, True))
            if task not in self.owners and len(self.owners) >= min(evidence.capacity, 4096):
                self.overflow(evidence)
                return
            self.hosts[task] = (host, at)
            self.owners[task] = host
        elif kind in {"activity", "completed"}:
            binding = self.hosts.get(task)
            if binding is None or binding[0] != "local" or binding[1] > at:
                return
            key = task, record["turn"], record["item"]
            if kind == "completed":
                previous = self.pending_activity.pop(key, None)
                if previous is None or any(t > at for t in previous.times):
                    return
                value = Activity(
                    previous.times,
                    previous.ambiguous,
                    tuple((t, float(at)) for t in previous.times),
                )
            else:
                value = Activity((float(at),))
                previous = self.pending_activity.get(key)
                self.pending_activity[key] = merge_activity(previous, value) if previous else value
            evidence.record_activity(*key, value)
            self.observed[key] = evidence.activities[key]
            if len(self.observed) > min(evidence.capacity, 4096):
                self.overflow(evidence)


class DesktopLogs:
    def __init__(self, root: Path | None, evidence: LogEvidence):
        self.root, self.evidence = root, evidence
        self.files: OrderedDict[Path, DesktopFile] = OrderedDict()
        self.groups: OrderedDict[str, tuple[Path, ...]] = OrderedDict()
        self.next_discovery = 0.0
        self.checkpoints: dict[str, str] = {}
        self.checkpoint_key = None
        self.completed: dict[str, tuple[str, float]] = {}
        self.pending = 0
        self.bytes_read = 0

    def discover(self, now: float) -> None:
        self.next_discovery = now + 60
        if self.root is None:
            return
        paths = []
        day = datetime.fromtimestamp(now, UTC)
        # Eight intersecting UTC dates plus one predecessor for writer continuity.
        # Old prefixes establish ownership only; archives retain seven-day evidence.
        for offset in range(HISTORY_DAYS + 2):
            folder = self.root / (day - timedelta(days=offset)).strftime("%Y/%m/%d")
            paths.extend(sorted(folder.glob("*.log")))
            if len(paths) >= 256:
                break
        self.files = OrderedDict((p, self.files.get(p) or DesktopFile(p)) for p in paths[:256])
        groups: dict[str, list[Path]] = {}
        for path in self.files:
            part = segment(path)
            groups.setdefault(part.writer if part else str(path), []).append(path)
        self.groups = OrderedDict(
            (key, tuple(sorted(values, key=lambda p: (p.parent.parts, p.name))))
            for key, values in groups.items()
        )

    def read_group(self, paths: tuple[Path, ...], now: float, budget: int) -> tuple[int, bool]:
        versions = tuple(file_version(path) for path in paths)
        keys = (
            tuple(self.checkpoint_key("desktop-writer", p) for p in paths)
            if self.checkpoint_key
            else ()
        )
        stamp = hashlib.sha256(json.dumps((ACTIVITY_REVISION, keys, versions)).encode()).hexdigest()
        key = keys[0] if keys else None
        if key and self.checkpoints.get(key) == stamp:
            return 0, False
        if key:
            self.completed.pop(key, None)
        used = 0
        previous = None
        for index, (path, version) in enumerate(zip(paths, versions, strict=True)):
            reader = self.files[path]
            prefix = tuple(zip(paths[:index], versions[:index], strict=True))
            if reader.prefix is not None and reader.prefix != prefix:
                # An earlier segment changed or disappeared. Replay this suffix
                # with its current ownership prefix, never with stale context.
                reader = self.files[path] = DesktopFile(path)
            reader.prefix = prefix
            if reader.version != version:
                if used >= budget:
                    return used, True
                reader.tail.budget = min(budget - used, 2 * 1024 * 1024)
                batch = reader.tail.read()
                used += batch.bytes_read
                self.bytes_read += batch.bytes_read
                if batch.reset or batch.skipped_prefix:
                    reader.reset()
                    reader.initialized = False
                if batch.error:
                    raise OSError(batch.error)
                if versions[:index] != tuple(file_version(p) for p in paths[:index]):
                    self.files[path] = DesktopFile(path)
                    return used, True
                if not reader.initialized and reader.tail.first_size:
                    if previous is not None and continuous(previous, reader):
                        reader.inherit(previous)
                    reader.initialized = True
                gaps = set(batch.gaps)
                for offset, record in enumerate(batch.records):
                    if offset in gaps:
                        reader.gap()
                    reader.accept(record, self.evidence)
                if len(batch.records) in gaps:
                    reader.gap()
                if not (
                    batch.eof
                    and not reader.tail.pending
                    and not reader.tail.dropping
                    and version == file_version(path)
                ):
                    return used, True
                reader.version = version
            previous = reader
        # A predecessor can change while a later segment is being read.
        if versions != tuple(file_version(path) for path in paths):
            return used, True
        if key:
            self.completed[key] = stamp, now
        return used, False

    def poll(self, now: float, *, budget: int = 8 * 1024 * 1024) -> set[str]:
        issues = set()
        try:
            if now >= self.next_discovery:
                self.discover(now)
        except OSError:
            issues.add("source_unavailable")
        self.pending = 0
        self.bytes_read = 0
        for group, paths in list(self.groups.items()):
            before = self.bytes_read
            try:
                _, pending = self.read_group(paths, now, budget)
                self.pending += pending
            except OSError:
                issues.add("source_unavailable")
                self.pending += 1
            finally:
                used = self.bytes_read - before
                budget -= used
                if used:
                    self.groups.move_to_end(group)
        return issues
