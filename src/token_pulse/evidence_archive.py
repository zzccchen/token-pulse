"""Durable, pseudonymized stream origins and retries for delayed history recovery."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from token_pulse.logs import LogEvidence, Start, merge_start

# Bump if diagnostic origin/retry interpretation changes. Samples have separate revisions.
EVIDENCE_REVISION = 1


class EvidenceArchive:
    def __init__(
        self,
        connection: sqlite3.Connection,
        anonymous_id: Callable[[str, str], str],
        root: Path,
        *,
        retention_days: int = 30,
        capacity: int = 100000,
    ):
        self.db, self.anon = connection, anonymous_id
        self.scope = anonymous_id("evidence-root", str(root.resolve()))
        self.retention_days, self.capacity = retention_days, capacity
        self.seen_starts: dict[tuple[str, str], Start] = {}
        self.seen_retries: set[tuple[str, str, float]] = set()
        self.next_prune = 0.0
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS stream_origins(
                scope TEXT NOT NULL, task TEXT NOT NULL, item TEXT NOT NULL,
                turn TEXT NOT NULL, at REAL NOT NULL, auth TEXT, tier TEXT,
                ambiguous INTEGER NOT NULL, revision INTEGER NOT NULL, origin TEXT NOT NULL,
                PRIMARY KEY(scope,task,item)
            );
            CREATE INDEX IF NOT EXISTS stream_origins_time ON stream_origins(at);
            CREATE TABLE IF NOT EXISTS sampling_interruptions(
                scope TEXT NOT NULL, task TEXT NOT NULL, turn TEXT NOT NULL,
                at REAL NOT NULL, revision INTEGER NOT NULL,
                PRIMARY KEY(scope,task,turn,at)
            );
            CREATE TABLE IF NOT EXISTS evidence_retention_floor(
                revision INTEGER PRIMARY KEY, at REAL NOT NULL
            );
        """)

    def restore(self, evidence: LogEvidence, *, now: float) -> None:
        evidence.archive_identity = self.anon
        floor = self.db.execute(
            "SELECT at FROM evidence_retention_floor WHERE revision=?", (EVIDENCE_REVISION,)
        ).fetchone()
        evidence.archive_floor = floor[0] if floor else None
        starts: dict[str, dict[str, Start]] = {}
        retries: dict[tuple[str, str], list[float]] = {}
        for task, item, turn, at, auth, tier, ambiguous, origin in self.db.execute(
            "SELECT task,item,turn,at,auth,tier,ambiguous,origin FROM stream_origins "
            "WHERE scope=? AND revision=? AND at BETWEEN ? AND ? ORDER BY at DESC LIMIT ?",
            (
                self.scope,
                EVIDENCE_REVISION,
                now - self.retention_days * 86400,
                now + 5,
                self.capacity,
            ),
        ):
            if evidence.archive_floor is not None and at <= evidence.archive_floor:
                continue
            starts.setdefault(task, {})[item] = Start(at, turn, auth, tier, bool(ambiguous), origin)
        for task, turn, at in self.db.execute(
            "SELECT task,turn,at FROM sampling_interruptions WHERE scope=? AND revision=? "
            "AND at BETWEEN ? AND ? ORDER BY at DESC LIMIT ?",
            (
                self.scope,
                EVIDENCE_REVISION,
                now - self.retention_days * 86400,
                now + 5,
                self.capacity,
            ),
        ):
            retries.setdefault((task, turn), []).append(at)
        evidence.archived_starts = starts
        evidence.archived_interruptions = {key: sorted(times) for key, times in retries.items()}

    def save(self, evidence: LogEvidence, *, now: float) -> None:
        starts = []
        retries = []
        oldest = now - self.retention_days * 86400
        for key, start in evidence.starts.items():
            if self.seen_starts.get(key) == start or not oldest <= start.at <= now + 5:
                continue
            if evidence.archive_floor is not None and start.at <= evidence.archive_floor:
                continue
            task, item = self.anon("task", key[0]), self.anon("item", key[1])
            saved = replace(start, turn_id=self.anon("turn", start.turn_id))
            previous = evidence.archived_starts.get(task, {}).get(item)
            if previous is not None:
                saved = merge_start(previous, saved)
            starts.append((task, item, saved))
        for task, turn, at in evidence.interruption_order:
            if (
                (task, turn, at) not in self.seen_retries
                and math.isfinite(at)
                and oldest <= at <= now + 5
            ):
                retries.append((self.anon("task", task), self.anon("turn", turn), at))
        prune = now >= self.next_prune
        prune |= sum(map(len, evidence.archived_starts.values())) + len(starts) > self.capacity
        prune |= (
            sum(map(len, evidence.archived_interruptions.values())) + len(retries) > self.capacity
        )
        with self.db:
            self.db.executemany(
                "INSERT INTO stream_origins VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(scope,task,item) DO UPDATE SET turn=excluded.turn,at=excluded.at,"
                "auth=excluded.auth,tier=excluded.tier,ambiguous=excluded.ambiguous,revision=excluded.revision,origin=excluded.origin",
                [
                    (
                        self.scope,
                        task,
                        item,
                        s.turn_id,
                        s.at,
                        s.auth_mode,
                        s.requested_tier,
                        int(s.ambiguous),
                        EVIDENCE_REVISION,
                        s.origin,
                    )
                    for task, item, s in starts
                ],
            )
            self.db.executemany(
                "INSERT OR REPLACE INTO sampling_interruptions VALUES(?,?,?,?,?)",
                [(self.scope, task, turn, at, EVIDENCE_REVISION) for task, turn, at in retries],
            )
            if prune:
                for table in ("stream_origins", "sampling_interruptions"):
                    self.db.execute(
                        f"DELETE FROM {table} WHERE at<? OR revision!=?",
                        (oldest, EVIDENCE_REVISION),
                    )
                # If the retry cap is reached, discard origins from that uncertain
                # prefix as well. Never recover a start after dropping a possible retry.
                boundary = self.db.execute(
                    "SELECT at FROM sampling_interruptions ORDER BY at DESC LIMIT 1 OFFSET ?",
                    (self.capacity,),
                ).fetchone()
                if boundary:
                    self.db.execute("DELETE FROM stream_origins WHERE at<=?", boundary)
                    self.db.execute(
                        "INSERT INTO evidence_retention_floor VALUES(?,?) ON CONFLICT(revision) "
                        "DO UPDATE SET at=max(at,excluded.at)",
                        (EVIDENCE_REVISION, boundary[0]),
                    )
                for table in ("stream_origins", "sampling_interruptions"):
                    self.db.execute(
                        f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} "
                        "ORDER BY at DESC LIMIT -1 OFFSET ?)",
                        (self.capacity,),
                    )
        # Failed commits leave observations pending in the bounded live evidence.
        self.seen_starts = dict(evidence.starts)
        self.seen_retries = set(evidence.interruption_order)
        if prune:
            self.next_prune = now + 60
            self.restore(evidence, now=now)
        else:
            for task, item, start in starts:
                evidence.archived_starts.setdefault(task, {})[item] = start
            for task, turn, at in retries:
                times = evidence.archived_interruptions.setdefault((task, turn), [])
                if at not in times:
                    times.append(at)
                    times.sort()
