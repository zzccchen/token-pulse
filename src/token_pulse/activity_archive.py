"""HMAC-only storage for normalized, host-validated streaming activity."""

from __future__ import annotations

import json

from token_pulse.stream_activity import ACTIVITY_REVISION, Activity, merge_activity


class ActivityArchive:
    def __init__(self, db, anon, scope, *, retention_days, capacity):
        self.db, self.anon, self.scope = db, anon, scope
        self.retention_days, self.capacity = retention_days, capacity
        self.seen = {}
        self.next_prune = 0.0
        db.execute("""
            CREATE TABLE IF NOT EXISTS stream_activity(
                scope TEXT NOT NULL, task TEXT NOT NULL, turn TEXT NOT NULL,
                item TEXT NOT NULL, times TEXT NOT NULL, ambiguous INTEGER NOT NULL,
                last_at REAL NOT NULL, revision INTEGER NOT NULL,
                completions TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY(scope,task,turn,item)
            )
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(stream_activity)")}
        with db:
            if "completions" not in columns:
                db.execute(
                    "ALTER TABLE stream_activity ADD COLUMN completions TEXT NOT NULL DEFAULT '[]'"
                )
            # Revision 1's strict interior observations remain valid evidence.
            db.execute(
                "UPDATE stream_activity SET revision=? WHERE revision=1", (ACTIVITY_REVISION,)
            )

    def restore(self, evidence, *, now):
        values = {}
        for task, turn, item, times, ambiguous, completions in self.db.execute(
            "SELECT task,turn,item,times,ambiguous,completions FROM stream_activity "
            "WHERE scope=? AND revision=? AND last_at BETWEEN ? AND ? "
            "ORDER BY last_at DESC LIMIT ?",
            (
                self.scope,
                ACTIVITY_REVISION,
                now - self.retention_days * 86400,
                now + 5,
                self.capacity,
            ),
        ):
            values.setdefault(task, {})[turn, item] = Activity(
                tuple(json.loads(times)),
                bool(ambiguous),
                tuple(tuple(p) for p in json.loads(completions)),
            )
        evidence.archived_activities = values

    def save(self, evidence, *, now):
        rows = []
        oldest = now - self.retention_days * 86400
        for key, value in evidence.activities.items():
            if self.seen.get(key) == value:
                continue
            times = tuple(at for at in value.times if oldest <= at <= now + 5)
            if not times:
                continue
            task, turn, item = (
                self.anon(kind, raw)
                for kind, raw in zip(("task", "turn", "item"), key, strict=True)
            )
            previous = evidence.archived_activities.get(task, {}).get((turn, item))
            value = Activity(
                times,
                value.ambiguous,
                tuple(p for p in value.completions if p[0] in times and p[0] <= p[1] <= now + 5),
            )
            if previous:
                value = merge_activity(previous, value)
            rows.append(
                (
                    self.scope,
                    task,
                    turn,
                    item,
                    json.dumps(value.times),
                    int(value.ambiguous),
                    max(value.times),
                    ACTIVITY_REVISION,
                    json.dumps(value.completions),
                )
            )
        if not rows and now < self.next_prune:
            return
        with self.db:
            self.db.executemany(
                "INSERT INTO stream_activity VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(scope,task,turn,item) DO UPDATE SET times=excluded.times,"
                "ambiguous=excluded.ambiguous,last_at=excluded.last_at,revision=excluded.revision,completions=excluded.completions",
                rows,
            )
            self.db.execute(
                "DELETE FROM stream_activity WHERE last_at<? OR revision!=?",
                (oldest, ACTIVITY_REVISION),
            )
            self.db.execute(
                "DELETE FROM stream_activity WHERE rowid IN (SELECT rowid FROM stream_activity "
                "ORDER BY last_at DESC LIMIT -1 OFFSET ?)",
                (self.capacity,),
            )
        self.seen = dict(evidence.activities)
        self.next_prune = now + 60
        self.restore(evidence, now=now)
