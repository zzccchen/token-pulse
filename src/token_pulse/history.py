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
from bisect import bisect_right
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from token_pulse.domain import (
    Context,
    Sample,
    bind_thread_config,
    correct_legacy_lifecycle,
    merge_submitted_tier,
    merge_turn_override,
    reconcile,
    resolve_response_alias,
)
from token_pulse.rollout import PARSER_REVISION
from token_pulse.submissions import ConfigurationTag, SubmittedTier


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
            CREATE TABLE IF NOT EXISTS response_aliases(
                task TEXT NOT NULL, turn TEXT NOT NULL, alias TEXT NOT NULL,
                canonical TEXT NOT NULL, ended_at REAL NOT NULL,
                PRIMARY KEY(task,turn,alias,canonical)
            );
            CREATE TABLE IF NOT EXISTS transport_cursors(
                key TEXT PRIMARY KEY, cursor INTEGER NOT NULL, anchor TEXT NOT NULL,
                identity TEXT NOT NULL, revision INTEGER NOT NULL, checked_at REAL NOT NULL
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
        self.aliases: dict[tuple, set[str]] = {}
        self._index_aliases()
        self._index_turns()
        self.rejections: dict[str, tuple[str, float]] = dict(
            (key, (reason, at))
            for key, reason, at in self.connection.execute("SELECT * FROM rejected_outputs")
        )
        self.rejection_cache: dict[tuple, Sample] = {}
        self._correct_legacy_lifecycle()

    def _correct_legacy_lifecycle(self) -> None:
        # The saved timing_source itself proves use of the old parsing rule.
        # Do not depend on files, diagnostic retention, or live-cache membership.
        changes = []
        for key, payload in self.connection.execute(
            "SELECT key,payload FROM samples WHERE valid=1"
        ):
            original = self._decode(payload)
            corrected = correct_legacy_lifecycle(original)
            if corrected != original:
                changes.append((json.dumps(asdict(corrected), allow_nan=False), key))
        if changes:
            with self.connection:
                self.connection.executemany(
                    "UPDATE samples SET valid=0,payload=? WHERE key=?", changes
                )

    def is_rejected(self, sample: Sample) -> bool:
        return any(
            "|".join(safe.key) in self.rejections
            for safe in (self.anonymous(sample), self.anonymous(sample, aliases={}))
        )

    def reject(self, samples: list[Sample], *, now: float) -> None:
        """Remove proven replay/stale observations using anonymous identities only."""
        rows = []
        changed = []
        for sample in samples:
            if sample.issue not in {
                "inherited_history",
                "stale_usage_snapshot",
                "superseded_usage",
            }:
                raise ValueError("Explicit replay or stale-usage evidence required")
            if not now - self.retention_days * 86400 <= sample.ended_at <= now + 5:
                continue
            if self.rejection_cache.get(sample.key) == sample:
                continue
            # A legacy shadow withdrawal must not delete the canonical response
            # to which a complete cross-source observation linked its old ID.
            safe = self.anonymous(sample, aliases={})
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
                    if (
                        type(validated.output_tokens) is int
                        and validated.output_tokens >= 0
                        and validated.issue
                        not in {
                            "incomplete_boundary",
                            "missing_usage_baseline",
                            "usage_counter_reset",
                            "usage_scope_mismatch",
                            "record_gap",
                            "turn_mismatch",
                        }
                        and validated.parser_revision >= max(2, sample.parser_revision)
                    ):
                        # The legacy shadow can repeat a withdrawal on every restart.
                        # Validated usage already resolved that stale notification,
                        # even when timing remains unavailable. Only a newer
                        # withdrawal rule can supersede that observation.
                        self.rejection_cache[sample.key] = sample
                        continue
            for turn in {safe.turn_id, self.empty_turn}:
                key = "|".join((safe.task_id, turn, safe.response_id))
                reason = sample.issue
                previous_reason = self.rejections.get(key, (None,))[0]
                if previous_reason in {"inherited_history", "superseded_usage"}:
                    reason = previous_reason
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
        for task, turn, alias in self.aliases:
            self.known_turns.setdefault((task, alias), set()).add(turn)

    def _index_aliases(self) -> None:
        self.aliases.clear()
        for task, turn, alias, canonical in self.connection.execute(
            "SELECT task,turn,alias,canonical FROM response_aliases"
        ):
            self.aliases.setdefault((task, turn, alias), set()).add(canonical)

    def anonymous_id(self, kind: str, value: str) -> str:
        return hmac.new(self.salt, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:24]

    def source_key(self, task: str, path: Path) -> str:
        # Include history policy and source location without persisting either path or ID.
        if path.name.endswith(".jsonl.zst"):
            path = path.with_suffix("")
        return self.anonymous_id(
            "source", f"{self.retention_days}:{self.max_samples}:{path}:{task}"
        )

    def source_checkpoints(self) -> dict[str, str]:
        return dict(self.connection.execute("SELECT key, fingerprint FROM completed_sources"))

    def transport_checkpoints(self) -> dict[str, tuple]:
        return {
            row[0]: tuple(row[1:])
            for row in self.connection.execute(
                "SELECT key,cursor,anchor,identity,revision FROM transport_cursors"
            )
        }

    def save_transport_checkpoints(self, values: dict[str, tuple], *, now: float) -> None:
        with self.connection:
            self.connection.executemany(
                "INSERT INTO transport_cursors VALUES(?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                "cursor=excluded.cursor,anchor=excluded.anchor,identity=excluded.identity,"
                "revision=excluded.revision,checked_at=excluded.checked_at",
                [(key, *value) for key, value in values.items()],
            )
            self.connection.execute(
                "DELETE FROM transport_cursors WHERE checked_at < ?",
                (now - self.retention_days * 86400,),
            )
            self.connection.execute(
                "DELETE FROM transport_cursors WHERE key IN "
                "(SELECT key FROM transport_cursors ORDER BY checked_at DESC LIMIT -1 OFFSET 256)"
            )

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

    def anonymous(self, sample: Sample, *, aliases: dict | None = None) -> Sample:
        safe = replace(
            sample,
            task_id=self.anonymous_id("task", sample.task_id),
            turn_id=self.anonymous_id("turn", sample.turn_id),
            response_id=self.anonymous_id("response", sample.response_id),
            response_aliases=tuple(
                self.anonymous_id("response", alias) for alias in sample.response_aliases
            ),
        )
        turns = self.known_turns.get((safe.task_id, safe.response_id), set())
        if not sample.turn_id and len(turns) == 1:
            safe = replace(safe, turn_id=next(iter(turns)))
        return resolve_response_alias(safe, self.aliases if aliases is None else aliases)

    def append(self, samples: list[Sample], *, now: float | None = None) -> int:
        samples = [correct_legacy_lifecycle(sample) for sample in samples]
        now = time.time() if now is None else now
        cutoff = now - self.retention_days * 86400
        alias_map = {key: set(values) for key, values in self.aliases.items()}
        bindings = []
        for sample in samples:
            if sample.turn_id and cutoff <= sample.ended_at <= now + 5:
                safe = self.anonymous(sample, aliases={})
                for alias in safe.response_aliases:
                    if alias != safe.response_id:
                        key = safe.task_id, safe.turn_id, alias
                        alias_map.setdefault(key, set()).add(safe.response_id)
                        bindings.append((*key, safe.response_id, safe.ended_at))
                        self.known_turns.setdefault((safe.task_id, alias), set()).add(safe.turn_id)
        # A tail can begin midway through a turn. Resolve that incomplete observation
        # only when the same task/response has exactly one known turn, before hashing
        # live samples for the UI as well as writing history.
        for sample in samples:
            if sample.turn_id and cutoff <= sample.ended_at <= now + 5:
                safe = self.anonymous(sample, aliases=alias_map)
                self.known_turns.setdefault((safe.task_id, safe.response_id), set()).add(
                    safe.turn_id
                )
        rows = []
        changed = []
        superseded = []
        cleared_rejections = []
        staged: dict[tuple, Sample] = {}
        # A newly conflicting alias also invalidates the existing canonical row,
        # even if that source is not present in this poll. Keep all identities.
        for task, turn, alias, _canonical, _ in bindings:
            targets = alias_map[task, turn, alias]
            if len(targets) > 1:
                for response in targets | {alias}:
                    key = task, turn, response
                    row = self.connection.execute(
                        "SELECT payload FROM samples WHERE key=?", ("|".join(key),)
                    ).fetchone()
                    if row:
                        safe = replace(self._decode(row[0]), issue="ambiguous_response_identity")
                        staged[key] = safe
                        rows.append(("|".join(key), safe.ended_at, 0, json.dumps(asdict(safe))))
        for sample in samples:
            if not math.isfinite(sample.ended_at) or not cutoff <= sample.ended_at <= now + 5:
                continue
            safe = self.anonymous(sample, aliases=alias_map)
            safe_key = "|".join(safe.key)
            raw_key = "|".join(self.anonymous(sample, aliases={}).key)
            rejection = self.rejections.get(raw_key) or self.rejections.get(safe_key)
            if rejection:
                if (
                    rejection[0] in {"inherited_history", "superseded_usage"}
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
                cleared_rejections.extend([(safe_key,), (raw_key,)])
            elif (
                self.cache.get(sample.key) == sample
                and not bindings
                and safe.response_id == self.anonymous_id("response", sample.response_id)
            ):
                continue
            for alias in safe.response_aliases:
                alias_key = safe.task_id, safe.turn_id, alias
                if alias == safe.response_id or alias_map.get(alias_key) != {safe.response_id}:
                    continue
                turns = {safe.turn_id}
                if self.known_turns.get((safe.task_id, alias)) == {safe.turn_id}:
                    turns.add(self.empty_turn)
                for turn in turns:
                    old_alias_key = safe.task_id, turn, alias
                    old_key = "|".join(old_alias_key)
                    row = self.connection.execute(
                        "SELECT payload FROM samples WHERE key=?", (old_key,)
                    ).fetchone()
                    old = staged.pop(old_alias_key, None) or (self._decode(row[0]) if row else None)
                    if old:
                        safe = reconcile(
                            replace(old, turn_id=safe.turn_id, response_id=safe.response_id), safe
                        )
                        superseded.append((old_key,))
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
                "INSERT INTO response_aliases VALUES(?,?,?,?,?) "
                "ON CONFLICT(task,turn,alias,canonical) "
                "DO UPDATE SET ended_at=max(ended_at,excluded.ended_at)",
                bindings,
            )
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
            # A staged legacy row must not be reinserted after its canonical row.
            self.connection.executemany("DELETE FROM samples WHERE key=?", superseded)
            self.connection.execute("DELETE FROM samples WHERE ended_at < ?", (cutoff,))
            self.connection.execute(
                "DELETE FROM samples WHERE key IN "
                "(SELECT key FROM samples ORDER BY ended_at DESC LIMIT -1 OFFSET ?)",
                (self.max_samples,),
            )
            self.connection.execute("DELETE FROM rejected_outputs WHERE ended_at < ?", (cutoff,))
            self.connection.execute("DELETE FROM response_aliases WHERE ended_at < ?", (cutoff,))
            self.connection.execute(
                "DELETE FROM response_aliases WHERE rowid IN "
                "(SELECT rowid FROM response_aliases ORDER BY ended_at DESC LIMIT -1 OFFSET ?)",
                (self.max_samples * 2,),
            )
            self.connection.execute(
                "DELETE FROM rejected_outputs WHERE key IN "
                "(SELECT key FROM rejected_outputs ORDER BY ended_at DESC LIMIT -1 OFFSET ?)",
                (self.max_samples * 2,),
            )
        for (key,) in cleared_rejections:
            self.rejections.pop(key, None)
        self._index_aliases()
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
        for name in ("response_aliases", "evidence_sources"):
            if name in value:
                value[name] = tuple(value[name])
        return Sample(**value)

    def enrich_interruptions(
        self,
        samples: list[Sample],
        interruptions: dict[tuple[str, str], list[float]],
        *,
        archived: dict[tuple[str, str], list[float]] | None = None,
    ) -> list[Sample]:
        # Correct saved intervals even when their conversation is no longer in
        # the live response cache. Only exact task/turn and interior times match.
        indexed = {
            (self.anonymous_id("task", task), self.anonymous_id("turn", turn)): times
            for (task, turn), times in interruptions.items()
        }
        for key, times in (archived or {}).items():
            indexed[key] = sorted(set((*indexed.get(key, []), *times)))
        result, changed = [], []
        for sample in samples:
            times = indexed.get((sample.task_id, sample.turn_id), [])
            if (
                times
                and sample.exclusion is None
                and sample.timing_source != "transport-log"
                and sample.parser_revision <= PARSER_REVISION
            ):
                index = bisect_right(times, sample.started_at)
                if index < len(times) and times[index] < sample.ended_at:
                    sample = replace(
                        sample, issue="interrupted_response", parser_revision=PARSER_REVISION
                    )
                    changed.append((json.dumps(asdict(sample)), "|".join(sample.key)))
            result.append(sample)
        if changed:
            with self.connection:
                self.connection.executemany(
                    "UPDATE samples SET valid=0,payload=? WHERE key=?", changed
                )
        return result

    def enrich_submissions(
        self, samples: list[Sample], submissions: dict[tuple[str, str], SubmittedTier]
    ) -> list[Sample]:
        return self._enrich_tiers(samples, submissions, "submitted_tier", "submitted_tier_state")

    def enrich_config_tags(
        self, samples: list[Sample], tags: dict[tuple[str, str], ConfigurationTag]
    ) -> list[Sample]:
        return self._enrich_tiers(samples, tags, "config_tag_tier", "config_tag_state")

    def _enrich_tiers(self, samples, settings, tier_field: str, state_field: str) -> list[Sample]:
        # History may outlive the collector's current 32 conversations. Match the
        # persisted anonymous task + turn, without needing to load old rollouts.
        indexed = {
            (self.anonymous_id("task", task), self.anonymous_id("turn", turn)): evidence
            for (task, turn), evidence in settings.items()
        }
        result, changed = [], []
        for sample in samples:
            evidence = indexed.get((sample.task_id, sample.turn_id))
            enriched = sample
            if evidence is not None and evidence.at <= sample.ended_at + 0.001:
                fields = {tier_field: evidence.tier, state_field: evidence.state}
                if isinstance(evidence, SubmittedTier):
                    fields.update(
                        merge_submitted_tier(
                            sample.context,
                            Context(
                                submitted_tier=evidence.tier, submitted_tier_state=evidence.state
                            ),
                        )
                    )
                if (
                    isinstance(evidence, SubmittedTier)
                    and evidence.override_state is not None
                    and evidence.override_at is not None
                    and evidence.override_at <= sample.ended_at + 0.001
                ):
                    fields.update(
                        merge_turn_override(
                            sample.context,
                            Context(
                                turn_override_tier=evidence.override_tier,
                                turn_override_state=evidence.override_state,
                                turn_override_at=evidence.override_at,
                            ),
                        )
                    )
                enriched = replace(
                    sample,
                    context=bind_thread_config(
                        replace(
                            sample.context,
                            **fields,
                        ),
                        output_start=sample.started_at,
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
            self.connection.execute("DELETE FROM response_aliases")
            self.connection.execute("DELETE FROM transport_cursors")
        self.rejections.clear()
        self.rejection_cache.clear()
        self.aliases.clear()
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
        "response_aliases",
        "evidence_sources",
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
                json.dumps(sample.response_aliases),
                json.dumps(sample.evidence_sources),
            ]
            writer.writerow([csv_cell(value) for value in values])
