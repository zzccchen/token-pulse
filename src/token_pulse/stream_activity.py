"""Normalized proof that an item was active, never a substitute start timestamp."""

from __future__ import annotations

from dataclasses import dataclass

ACTIVITY_REVISION = 1


@dataclass(frozen=True)
class Activity:
    times: tuple[float, ...]
    ambiguous: bool = False


def merge_activity(previous: Activity, incoming: Activity) -> Activity:
    times = sorted(set(previous.times) | set(incoming.times))
    # Resource bound, not a timing threshold. Overflow cannot certify a start.
    return Activity(tuple(times[:64]), previous.ambiguous or incoming.ambiguous or len(times) > 64)
