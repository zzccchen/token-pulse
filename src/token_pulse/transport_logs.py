"""Incremental read-only transport recovery with quiescent, HMAC-only cursors."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from token_pulse.logs import read_only
from token_pulse.transport import REVISION, TARGETS, Transport

MAX_ROW = 2 * 1024 * 1024
REQUIRED_COLUMNS = {"id", "ts", "ts_nanos", "thread_id", "target", "feedback_log_body"}


class TransportLogs:
    def __init__(self, path: Path, *, batch_size=1000, backfill=50000, transport=None):
        self.path, self.batch_size, self.backfill = path, batch_size, backfill
        self.transport = transport or Transport()
        self.cursor: int | None = None
        self.identity: str | None = None
        self.anchor: str | None = None
        self.pending = False
        self.completed: tuple | None = None
        self.checkpoint: tuple | None = None
        self.version: tuple | None = None
        secret = secrets.token_bytes(32)
        self.digest: Callable[[str], str] = lambda text: hmac.new(
            secret, text.encode(), hashlib.sha256
        ).hexdigest()

    def _stamp(self, row) -> str:
        # SQLite's dynamic typing can expose malformed binary metadata. It must
        # invalidate the event without trapping recovery at the same row forever.
        values = [
            {"binary_sha256": hashlib.sha256(value).hexdigest()}
            if isinstance(value, bytes)
            else value
            for value in row
        ]
        return self.digest(json.dumps(values, ensure_ascii=False))

    def poll(self, now: float, *, budget=4 * 1024 * 1024) -> tuple[list, set[str], int]:
        issues: set[str] = set()
        used = 0
        was_pending = self.pending
        self.pending = False
        try:
            stat = self.path.stat()
            wal = Path(str(self.path) + "-wal")
            wal_stat = wal.stat() if wal.exists() else None
            version = (
                stat.st_dev,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                (wal_stat.st_size, wal_stat.st_mtime_ns) if wal_stat else None,
            )
            if self.cursor is not None and self.version == version and not was_pending:
                return self.transport.drain(), issues, used
            identity = self.digest(f"{stat.st_dev}:{stat.st_ino}")
            with closing(read_only(self.path)) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(logs)")}
                if not REQUIRED_COLUMNS <= columns:
                    raise ValueError("Unsupported log columns")
                process = "process_uuid" if "process_uuid" in columns else "NULL"
                projection = (
                    f"id,ts,ts_nanos,thread_id,{process},target,"
                    "CASE WHEN target IN ('codex_api::sse::responses',"
                    "'codex_api::endpoint::responses_websocket') AND "
                    "typeof(feedback_log_body)='text' AND "
                    f"length(CAST(feedback_log_body AS BLOB)) <= {MAX_ROW} "
                    "THEN feedback_log_body END, length(CAST(feedback_log_body AS BLOB))"
                )
                maximum = db.execute("SELECT max(id) FROM logs").fetchone()[0] or 0
                if self.cursor is None and self.checkpoint:
                    cursor, anchor, saved_identity, revision = self.checkpoint
                    if revision == REVISION and saved_identity == identity and cursor <= maximum:
                        self.cursor, self.anchor, self.identity = cursor, anchor, identity
                reset = self.cursor is None or identity != self.identity or maximum < self.cursor
                if not reset and self.cursor and self.anchor is not None:
                    row = db.execute(
                        f"SELECT {projection} FROM logs WHERE id=?", (self.cursor,)
                    ).fetchone()
                    used += len(row[6].encode()) if row and isinstance(row[6], str) else 0
                    reset = row is None or self._stamp(row) != self.anchor
                if reset:
                    self.transport.reset()
                    self.completed = None
                    first = db.execute(
                        "SELECT min(id) FROM (SELECT id FROM logs WHERE target IN (?,?) "
                        "AND ts>=? ORDER BY id DESC LIMIT ?)",
                        (*TARGETS, now - 604800, self.backfill),
                    ).fetchone()[0]
                    self.cursor = first - 1 if first is not None else maximum
                    self.anchor = None
                    self.identity = identity
                rows = db.execute(
                    f"SELECT {projection} FROM logs WHERE id>? AND target IN (?,?) AND ts>=? "
                    "ORDER BY id LIMIT ?",
                    (self.cursor, *TARGETS, now - 604800, self.batch_size),
                )
                processed = 0
                for row in rows:
                    cost = len(row[6].encode()) if isinstance(row[6], str) else 0
                    if used + cost > budget:
                        self.pending = True
                        break
                    used += cost
                    row_id, seconds, nanos, task, process_id, target, body, size = row
                    if (
                        isinstance(task, str)
                        and type(seconds) in (int, float)
                        and type(nanos) in (int, float)
                        and math.isfinite(seconds)
                        and 0 <= seconds <= 2**53
                        and 0 <= nanos < 1e9
                    ):
                        self.transport.accept(task, process_id, seconds + nanos / 1e9, target, body)
                    else:
                        self.transport.gap(task if isinstance(task, str) else None)
                    if body is None:
                        issues.add("records_skipped")
                    self.cursor, self.anchor = row_id, self._stamp(row)
                    processed += 1
                    if not self.transport.active:
                        # A restart can resume here without storing any in-flight
                        # parser state. The caller commits this only after samples.
                        self.completed = self.cursor, self.anchor, identity, REVISION, now
                if processed == self.batch_size:
                    self.pending = True
                if not self.pending:
                    self.transport.snapshot_pending()
                self.version = version
        except (OSError, sqlite3.Error, TypeError, ValueError):
            issues.add("logs_unavailable")
            self.transport.reset()
            self.cursor = None
            self.anchor = None
            self.completed = None
            self.version = None
        return self.transport.drain(), issues, used
