"""Normalized proof that an item was active, never a substitute start timestamp."""

from __future__ import annotations

from dataclasses import dataclass

ACTIVITY_REVISION = 2


@dataclass(frozen=True)
class Activity:
    times: tuple[float, ...]
    ambiguous: bool = False
    completions: tuple[tuple[float, float], ...] = ()


def merge_activity(previous: Activity, incoming: Activity) -> Activity:
    times = sorted(set(previous.times) | set(incoming.times))
    pairs = sorted(set(previous.completions) | set(incoming.completions))
    # Resource bound, not a timing threshold. Overflow cannot certify a start.
    return Activity(
        tuple(times[:64]),
        previous.ambiguous or incoming.ambiguous or len(times) > 64 or len(pairs) > 64,
        tuple(pairs[:64]),
    )
