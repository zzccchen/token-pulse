"""Restore preserved independent measurements from a matching TokenPulse backup."""

from __future__ import annotations

import json
import re
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from token_pulse.domain import resolve_response_alias
from token_pulse.history import History
from token_pulse.logs import read_only


def restore_preserved(history: History, source: Path, *, now: float, apply: bool = False) -> int:
    """Repair only absent, independently timed rows removed by legacy shadows.

    Existing rows, inherited/superseded outputs and unmatched identity salts are
    never overwritten. Callers back up the destination and stop its writer first.
    """
    existing = {s.key for s in history.read()}
    candidates = []
    cutoff = now - history.retention_days * 86400
    if len(existing) >= history.max_samples:
        cutoff = max(
            cutoff, history.connection.execute("SELECT min(ended_at) FROM samples").fetchone()[0]
        )
    with closing(read_only(source)) as backup:
        salt = backup.execute("SELECT value FROM settings WHERE key='salt'").fetchone()
        if salt is None or salt[0] != history.salt.hex():
            raise ValueError("Backup identity salt does not match destination")
        for key, payload in backup.execute(
            "SELECT key,payload FROM samples WHERE valid=1 AND ended_at BETWEEN ? AND ? "
            "ORDER BY ended_at DESC LIMIT ?",
            (cutoff, now, history.max_samples),
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
        history.connection.execute(
            "DELETE FROM samples WHERE key IN "
            "(SELECT key FROM samples ORDER BY ended_at DESC LIMIT -1 OFFSET ?)",
            (history.max_samples,),
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
