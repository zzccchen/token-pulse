"""Local statistics only. Raw task/turn/response identifiers never reach disk."""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
import math
import secrets
import sqlite3
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from token_pulse.domain import Context, Sample, reconcile
from token_pulse.submissions import SubmittedTier


class History:
    """Persist bounded, pseudonymized observations owned by the background worker.

    Evidence enrichment and versioned corrections share stable sample identities;
    a missing source event alone must not erase an existing valid measurement.
    """

    def __init__(self, path: Path, *, retention_days: int = 30, max_samples: int = 10000):
        self.path = path
        self.retention_days, self.max_samples = retention_days, max_samples
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=1)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS samples(
                key TEXT PRIMARY KEY, ended_at REAL NOT NULL, valid INTEGER NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS samples_time ON samples(ended_at);
            CREATE TABLE IF NOT EXISTS rejected_outputs(
                key TEXT PRIMARY KEY, reason TEXT NOT NULL, ended_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS completed_sources(
                key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, checked_at REAL NOT NULL
            );
        """)
        self.connection.execute(
            "INSERT OR IGNORE INTO settings VALUES('salt', ?)", (secrets.token_hex(32),)
        )
        self.connection.commit()
        self.salt = bytes.fromhex(
            self.connection.execute("SELECT value FROM settings WHERE key='salt'").fetchone()[0]
        )
        self.cache: dict[tuple, Sample] = {}
        self.empty_turn = self.anonymous_id("turn", "")
        self.known_turns: dict[tuple[str, str], set[str]] = {}
        self._index_turns()
        self.rejections: dict[str, tuple[str, float]] = dict(
            (key, (reason, at))
            for key, reason, at in self.connection.execute("SELECT * FROM rejected_outputs")
        )
        self.rejection_cache: dict[tuple, Sample] = {}

    def is_rejected(self, sample: Sample) -> bool:
        return "|".join(self.anonymous(sample).key) in self.rejections

    def reject(self, samples: list[Sample], *, now: float) -> None:
        """Remove proven replay/stale observations using anonymous identities only."""
        rows = []
        changed = []
        for sample in samples:
            if sample.issue not in {"inherited_history", "stale_usage_snapshot"}:
                raise ValueError("Explicit replay or stale-usage evidence required")
            if not now - self.retention_days * 86400 <= sample.ended_at <= now + 5:
                continue
            if self.rejection_cache.get(sample.key) == sample:
                continue
            safe = self.anonymous(sample)
            if sample.turn_id:
                self.known_turns.setdefault((safe.task_id, safe.response_id), set()).add(
                    safe.turn_id
                )
            if sample.issue == "stale_usage_snapshot":
                previous = self.connection.execute(
                    "SELECT payload FROM samples WHERE key=?", ("|".join(safe.key),)
                ).fetchone()
                if previous:
                    validated = self._decode(previous[0])
                    if validated.exclusion is None and validated.parser_revision >= max(
                        2, sample.parser_revision
                    ):
                        # The legacy shadow can repeat a withdrawal on every restart.
                        # A full, validated observation from this parser already resolved
                        # that stale notification; only a newer parser may supersede it.
                        self.rejection_cache[sample.key] = sample
                        continue
            for turn in {safe.turn_id, self.empty_turn}:
                key = "|".join((safe.task_id, turn, safe.response_id))
                reason = sample.issue
                if self.rejections.get(key, (None,))[0] == "inherited_history":
                    reason = "inherited_history"
                rows.append((key, reason, safe.ended_at))
            changed.append(sample)
        with self.connection:
            self.connection.executemany(
                "INSERT INTO rejected_outputs VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET "
                "reason=excluded.reason, ended_at=excluded.ended_at",
                rows,
            )
            self.connection.executemany("DELETE FROM samples WHERE key=?", [(r[0],) for r in rows])
        self.rejections.update((key, (reason, at)) for key, reason, at in rows)
        self.rejection_cache.update((s.key, s) for s in changed)
        if len(self.rejection_cache) > self.max_samples:
            self.rejection_cache = dict(list(self.rejection_cache.items())[-self.max_samples :])

    def _index_turns(self) -> None:
        self.known_turns.clear()
        for (key,) in self.connection.execute("SELECT key FROM samples"):
            task, turn, response = key.split("|")
            if turn != self.empty_turn:
                self.known_turns.setdefault((task, response), set()).add(turn)

    def anonymous_id(self, kind: str, value: str) -> str:
        return hmac.new(self.salt, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:24]

    def source_key(self, task: str, path: Path) -> str:
        # Include history policy and source location without persisting either path or ID.
        return self.anonymous_id(
            "source", f"{self.retention_days}:{self.max_samples}:{path}:{task}"
        )

    def source_checkpoints(self) -> dict[str, str]:
        return dict(self.connection.execute("SELECT key, fingerprint FROM completed_sources"))

    def save_checkpoints(self, values: dict[str, tuple[str, float]], *, now: float) -> None:
        # The monitor calls this only after all preceding sample chunks were committed.
        with self.connection:
            self.connection.executemany(
                "INSERT INTO completed_sources VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET "
                "fingerprint=excluded.fingerprint, checked_at=excluded.checked_at",
                [(key, stamp, at) for key, (stamp, at) in values.items()],
            )
            self.connection.execute(
                "DELETE FROM completed_sources WHERE checked_at < ?",
                (now - self.retention_days * 86400,),
            )
            self.connection.execute(
                "DELETE FROM completed_sources WHERE key IN "
                "(SELECT key FROM completed_sources ORDER BY checked_at DESC LIMIT -1 OFFSET ?)",
                (self.max_samples,),
            )

    def anonymous(self, sample: Sample) -> Sample:
        safe = replace(
            sample,
            task_id=self.anonymous_id("task", sample.task_id),
            turn_id=self.anonymous_id("turn", sample.turn_id),
            response_id=self.anonymous_id("response", sample.response_id),
        )
        turns = self.known_turns.get((safe.task_id, safe.response_id), set())
        if not sample.turn_id and len(turns) == 1:
            safe = replace(safe, turn_id=next(iter(turns)))
        return safe

    def append(self, samples: list[Sample], *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        cutoff = now - self.retention_days * 86400
        # A tail can begin midway through a turn. Resolve that incomplete observation
        # only when the same task/response has exactly one known turn, before hashing
        # live samples for the UI as well as writing history.
        for sample in samples:
            if sample.turn_id and cutoff <= sample.ended_at <= now + 5:
                safe = self.anonymous(sample)
                self.known_turns.setdefault((safe.task_id, safe.response_id), set()).add(
                    safe.turn_id
                )
        rows = []
        changed = []
        superseded = []
        cleared_rejections = []
        staged: dict[tuple, Sample] = {}
        for sample in samples:
            if not math.isfinite(sample.ended_at) or not cutoff <= sample.ended_at <= now + 5:
                continue
            safe = self.anonymous(sample)
            safe_key = "|".join(safe.key)
            rejection = self.rejections.get(safe_key)
            if rejection:
                if (
                    rejection[0] == "inherited_history"
                    or sample.parser_revision < 2
                    or sample.issue
                    in {
                        "incomplete_boundary",
                        "missing_usage_baseline",
                        "usage_counter_reset",
                        "usage_scope_mismatch",
                        "record_gap",
                        "turn_mismatch",
                    }
                ):
                    continue
                # A later counter advance/explicit response record can validate the
                # same output items that appeared in an earlier stale notification.
                cleared_rejections.append((safe_key,))
            elif self.cache.get(sample.key) == sample:
                continue
            if len(self.known_turns.get((safe.task_id, safe.response_id), set())) == 1:
                old_key = "|".join((safe.task_id, self.empty_turn, safe.response_id))
                incomplete = self.connection.execute(
                    "SELECT payload FROM samples WHERE key=?", (old_key,)
                ).fetchone()
                if incomplete:
                    safe = reconcile(
                        replace(self._decode(incomplete[0]), turn_id=safe.turn_id), safe
                    )
                    superseded.append((old_key,))
            previous = staged.get(safe.key)
            if previous is None:
                row = self.connection.execute(
                    "SELECT payload FROM samples WHERE key=?", ("|".join(safe.key),)
                ).fetchone()
                previous = self._decode(row[0]) if row else None
            if previous:
                safe = reconcile(previous, safe)
            staged[safe.key] = safe
            data = asdict(safe)
            if type(safe.output_tokens) is not int or not 0 <= safe.output_tokens <= 2**63 - 1:
                data["output_tokens"] = None
                data["issue"] = safe.exclusion or "invalid_tokens"
            if safe.started_at is not None and not math.isfinite(safe.started_at):
                data["started_at"] = None
                data["issue"] = safe.exclusion
            rows.append(
                (
                    "|".join(safe.key),
                    safe.ended_at,
                    int(safe.exclusion is None),
                    json.dumps(data, ensure_ascii=False, allow_nan=False),
                )
            )
            changed.append(sample)
        with self.connection:
            self.connection.executemany(
                "DELETE FROM rejected_outputs WHERE key=?", cleared_rejections
            )
            self.connection.executemany("DELETE FROM samples WHERE key=?", superseded)
            self.connection.executemany(
                """
                INSERT INTO samples VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET
                    ended_at=excluded.ended_at, valid=excluded.valid, payload=excluded.payload
            """,
                rows,
            )
            self.connection.execute("DELETE FROM samples WHERE ended_at < ?", (cutoff,))
            self.connection.execute(
                "DELETE FROM samples WHERE key IN "
                "(SELECT key FROM samples ORDER BY ended_at DESC LIMIT -1 OFFSET ?)",
                (self.max_samples,),
            )
            self.connection.execute("DELETE FROM rejected_outputs WHERE ended_at < ?", (cutoff,))
            self.connection.execute(
                "DELETE FROM rejected_outputs WHERE key IN "
                "(SELECT key FROM rejected_outputs ORDER BY ended_at DESC LIMIT -1 OFFSET ?)",
                (self.max_samples * 2,),
            )
        for (key,) in cleared_rejections:
            self.rejections.pop(key, None)
        if len(self.rejections) > self.max_samples * 2 or any(
            at < cutoff for _, at in self.rejections.values()
        ):
            self.rejections = {
                key: (reason, at)
                for key, reason, at in self.connection.execute("SELECT * FROM rejected_outputs")
            }
        for sample in changed:
            self.cache[sample.key] = sample
        if len(self.cache) > self.max_samples:
            self.cache = dict(list(self.cache.items())[-self.max_samples :])
        if len(self.known_turns) > self.max_samples:
            self._index_turns()
        return len(rows)

    def read(self) -> list[Sample]:
        result = []
        for (payload,) in self.connection.execute(
            "SELECT payload FROM samples ORDER BY ended_at DESC LIMIT ?", (self.max_samples,)
        ):
            result.append(self._decode(payload))
        return result

    @staticmethod
    def _decode(payload: str) -> Sample:
        value = json.loads(payload)
        value["context"] = Context(**value["context"])
        return Sample(**value)

    def enrich_submissions(
        self, samples: list[Sample], submissions: dict[tuple[str, str], SubmittedTier]
    ) -> list[Sample]:
        # History may outlive the collector's current 32 conversations. Match the
        # persisted anonymous task + turn, without needing to load old rollouts.
        indexed = {
            (self.anonymous_id("task", task), self.anonymous_id("turn", turn)): evidence
            for (task, turn), evidence in submissions.items()
        }
        result, changed = [], []
        for sample in samples:
            evidence = indexed.get((sample.task_id, sample.turn_id))
            enriched = sample
            if evidence is not None and evidence.at <= sample.ended_at + 0.001:
                enriched = replace(
                    sample,
                    context=replace(
                        sample.context,
                        submitted_tier=evidence.tier,
                        submitted_tier_state=evidence.state,
                    ),
                )
            result.append(enriched)
            if enriched != sample:
                changed.append(
                    (
                        json.dumps(asdict(enriched), ensure_ascii=False, allow_nan=False),
                        "|".join(sample.key),
                    )
                )
        if changed:
            with self.connection:
                self.connection.executemany("UPDATE samples SET payload=? WHERE key=?", changed)
        return result

    def clear(self) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM samples")
            self.connection.execute("DELETE FROM rejected_outputs")
            self.connection.execute("DELETE FROM completed_sources")
        self.rejections.clear()
        self.rejection_cache.clear()
        # Do not re-import the same currently watched samples on the next poll.

    def close(self) -> None:
        self.connection.close()


def csv_cell(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    if isinstance(value, str) and value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def export_csv(path: Path, samples: list[Sample]) -> None:
    """Call with History.read() results, whose identifiers are already anonymized."""
    context_fields = list(Context.__dataclass_fields__)
    fields = [
        "ended_at_utc",
        "task_id",
        "turn_id",
        "response_id",
        *context_fields,
        "output_tokens",
        "stream_seconds",
        "tps",
        "token_scope",
        "source",
        "exclusion",
        "parser_revision",
        "timing_source",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        for sample in samples:
            values = [
                datetime.fromtimestamp(sample.ended_at, UTC).isoformat(),
                *sample.key,
                *asdict(sample.context).values(),
                sample.output_tokens,
                sample.seconds,
                sample.tps,
                sample.token_scope,
                sample.source,
                sample.exclusion,
                sample.parser_revision,
                sample.timing_source,
            ]
            writer.writerow([csv_cell(value) for value in values])
