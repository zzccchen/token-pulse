"""Restore preserved independent measurements from a matching TokenPulse backup."""

from __future__ import annotations

import json
import re
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from token_pulse.domain import reconcile, resolve_response_alias
from token_pulse.history import History
from token_pulse.logs import read_only


def restore_independent(history: History, source: Path, *, now: float, apply: bool = False) -> int:
    """Restore exact saved independent timing to lifecycle-only exclusions.

    This never creates an output, guesses a boundary or relaxes timing semantics.
    Identical identity, token scope/count and completion are required. Current
    explicit exclusions, alias conflicts and known sampling retries still win.
    """
    existing = {s.key: s for s in history.read(now=now)}
    tables = {
        r[0]
        for r in history.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    retries: dict[tuple, list[float]] = {}
    if "sampling_interruptions" in tables:
        for task, turn, at in history.connection.execute(
            "SELECT task,turn,at FROM sampling_interruptions"
        ):
            retries.setdefault((task, turn), []).append(at)
    changes = []
    with closing(read_only(source)) as backup:
        backup.execute("BEGIN")
        salt = backup.execute("SELECT value FROM settings WHERE key='salt'").fetchone()
        if salt is None or salt[0] != history.salt.hex():
            raise ValueError("Backup identity salt does not match destination")
        for key, payload in backup.execute(
            "SELECT key,payload FROM samples WHERE valid=1 AND ended_at BETWEEN ? AND ?",
            (now - history.retention_days * 86400, now),
        ):
            saved = History._decode(payload)
            current = existing.get(saved.key)
            resolved = resolve_response_alias(saved, history.aliases)
            if (
                current is None
                or current.issue != "unverified_lifecycle_start"
                or saved.tps is None
                or saved.parser_revision < 7
                or saved.timing_source
                not in {"stream-log", "recovered-stream-log", "transport-log"}
                or key != "|".join(saved.key)
                or not all(re.fullmatch(r"[0-9a-f]{24}", value) for value in saved.key)
                or saved.turn_id == history.empty_turn
                or key in history.rejections
                or history.rejections.get(
                    "|".join((saved.task_id, history.empty_turn, saved.response_id)), (None,)
                )[0]
                in {"inherited_history", "superseded_usage"}
                or saved.output_tokens != current.output_tokens
                or saved.token_scope != current.token_scope
                or saved.ended_at != current.ended_at
                or resolved.key != saved.key
                or resolved.exclusion is not None
            ):
                continue
            repaired = reconcile(saved, current)
            if repaired.tps is None:
                continue
            if repaired.timing_source != "transport-log" and any(
                repaired.started_at < at < repaired.ended_at
                for at in retries.get((saved.task_id, saved.turn_id), ())
            ):
                continue
            changes.append((json.dumps(asdict(repaired), allow_nan=False), key))
    if apply:
        with history.connection:
            history.connection.executemany(
                "UPDATE samples SET payload=?,valid=1 WHERE key=?", changes
            )
        history.cache.clear()
    return len(changes)


def restore_preserved(history: History, source: Path, *, now: float, apply: bool = False) -> int:
    """Repair only absent, independently timed rows removed by legacy shadows.

    Existing rows, inherited/superseded outputs and unmatched identity salts are
    never overwritten. Callers back up the destination and stop its writer first.
    """
    existing = {s.key for s in history.read()}
    candidates = []
    cutoff = now - history.retention_days * 86400
    with closing(read_only(source)) as backup:
        salt = backup.execute("SELECT value FROM settings WHERE key='salt'").fetchone()
        if salt is None or salt[0] != history.salt.hex():
            raise ValueError("Backup identity salt does not match destination")
        for key, payload in backup.execute(
            "SELECT key,payload FROM samples WHERE valid=1 AND ended_at BETWEEN ? AND ? "
            "ORDER BY ended_at DESC",
            (cutoff, now),
        ):
            sample = History._decode(payload)
            if (
                sample.tps is None
                or sample.parser_revision < 2
                or sample.timing_source
                not in {"stream-log", "recovered-stream-log", "transport-log"}
                or key != "|".join(sample.key)
                or not all(re.fullmatch(r"[0-9a-f]{24}", v) for v in sample.key)
                or sample.turn_id == history.empty_turn
                or sample.key in existing
                or resolve_response_alias(sample, history.aliases).key != sample.key
            ):
                continue
            rejection = history.rejections.get(key)
            if rejection is None or rejection[0] != "stale_usage_snapshot":
                continue
            # An existing alias owner blocks restoration instead of introducing
            # a second canonical output or silently changing its identity.
            if any(
                owners != {sample.response_id}
                for alias in sample.response_aliases
                if (owners := history.aliases.get((sample.task_id, sample.turn_id, alias)))
            ):
                continue
            candidates.append(sample)
    if not apply:
        return len(candidates)
    with history.connection:
        for sample in candidates:
            key = "|".join(sample.key)
            history.connection.execute(
                "INSERT INTO samples VALUES(?,?,1,?)",
                (key, sample.ended_at, json.dumps(asdict(sample), allow_nan=False)),
            )
            history.connection.execute("DELETE FROM rejected_outputs WHERE key=?", (key,))
            empty_key = "|".join((sample.task_id, history.empty_turn, sample.response_id))
            if history.rejections.get(empty_key, (None,))[0] == "stale_usage_snapshot":
                history.connection.execute("DELETE FROM rejected_outputs WHERE key=?", (empty_key,))
            for alias in sample.response_aliases:
                if alias != sample.response_id:
                    history.connection.execute(
                        "INSERT OR IGNORE INTO response_aliases VALUES(?,?,?,?,?)",
                        (
                            sample.task_id,
                            sample.turn_id,
                            alias,
                            sample.response_id,
                            sample.ended_at,
                        ),
                    )
    history.rejections = dict(
        (key, (reason, at))
        for key, reason, at in history.connection.execute("SELECT * FROM rejected_outputs")
    )
    history.rejection_cache.clear()
    history.cache.clear()
    history._index_aliases()
    history._index_turns()
    return len(candidates)
