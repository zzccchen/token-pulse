"""Bounded desktop notification evidence with explicit, file-local host ownership."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from token_pulse.logs import LogEvidence
from token_pulse.rollout import timestamp
from token_pulse.stream_activity import ACTIVITY_REVISION, Activity, merge_activity
from token_pulse.tailer import JsonlTail, json_object

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


def desktop_log_root() -> Path | None:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Codex" / "Logs"
    return None  # No unverified Linux path assumption.


class DesktopTail(JsonlTail):
    def decode_line(self, line: bytes) -> dict | None:
        text = line.decode("utf-8").rstrip("\r\n")
        header = HEADER.fullmatch(text)
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
        self.tail = DesktopTail(path, max_line=32768, backfill=None)
        self.hosts: dict[str, tuple[str, float]] = {}
        self.owners: dict[str, str] = {}
        self.observed: dict[tuple[str, str, str], Activity] = {}
        self.pending_activity: dict[tuple[str, str, str], Activity] = {}
        self.version: tuple | None = None
        self.blocked = False

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
        self.next_discovery = 0.0
        self.checkpoints: dict[str, str] = {}
        self.checkpoint_key = None
        self.completed: dict[str, tuple[str, float]] = {}
        self.pending = 0

    def discover(self, now: float) -> None:
        self.next_discovery = now + 60
        if self.root is None:
            return
        paths = []
        day = datetime.fromtimestamp(now, UTC)
        for offset in range(9):
            folder = self.root / (day - timedelta(days=offset)).strftime("%Y/%m/%d")
            paths.extend(sorted(folder.glob("*.log")))
            if len(paths) >= 256:
                break
        self.files = OrderedDict((p, self.files.get(p) or DesktopFile(p)) for p in paths[:256])

    def poll(self, now: float, *, budget: int = 8 * 1024 * 1024) -> set[str]:
        issues = set()
        try:
            if now >= self.next_discovery:
                self.discover(now)
        except OSError:
            issues.add("source_unavailable")
        self.pending = 0
        for path, reader in list(self.files.items()):
            try:
                stat = path.stat()
                version = stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
                stamp = hashlib.sha256(
                    json.dumps((ACTIVITY_REVISION, version)).encode()
                ).hexdigest()
                key = self.checkpoint_key("desktop-activity", path) if self.checkpoint_key else None
                if reader.version == version or (key and self.checkpoints.get(key) == stamp):
                    continue
                if key:
                    self.completed.pop(key, None)
                if budget <= 0:
                    self.pending += 1
                    continue
                reader.tail.budget = min(budget, 2 * 1024 * 1024)
                batch = reader.tail.read()
                budget -= batch.bytes_read
                self.files.move_to_end(path)
                if batch.reset or batch.skipped_prefix:
                    reader.reset()
                if batch.error:
                    issues.add(batch.error)
                    continue
                gaps = set(batch.gaps)
                for index, record in enumerate(batch.records):
                    if index in gaps:
                        reader.gap()
                    reader.accept(record, self.evidence)
                if len(batch.records) in gaps:
                    reader.gap()
                # Never acknowledge a partial line, interrupted read or changing file.
                after = path.stat()
                unchanged = version == (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                )
                if batch.eof and not reader.tail.pending and not reader.tail.dropping and unchanged:
                    reader.version = version
                    if key:
                        self.completed[key] = stamp, now
                elif not batch.eof:
                    self.pending += 1
            except OSError:
                issues.add("source_unavailable")
        return issues
