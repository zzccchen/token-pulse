"""Bounded, thread-owned configuration observations from rollout metadata.

Snapshots describe thread configuration, not a sampling request. In particular,
steering can update preferences for later turns while the current turn keeps its
own options. Only an exact submission with explicit absence of a start override
can bind a pre-start snapshot to that turn (see domain.bind_thread_config).
"""

from __future__ import annotations

import math
import re
from collections import OrderedDict, deque
from dataclasses import dataclass, replace

from token_pulse.domain import Context, bind_thread_config
from token_pulse.tailer import AmbiguousObject


@dataclass(frozen=True)
class Configuration:
    at: float
    tier: str | None
    state: str
    sequence: int


@dataclass(frozen=True)
class TurnBoundary:
    earliest: float
    observed: float
    sequence: int
    ambiguous: bool = False


class ThreadSettings:
    def __init__(self, task: str, *, capacity: int = 512):
        self.task = task
        self.capacity = capacity
        self.snapshots: deque[Configuration] = deque(maxlen=capacity)
        self.turns: OrderedDict[str, TurnBoundary] = OrderedDict()
        self.sequence = 0
        self.last_at = 0.0

    def reset(self) -> None:
        self.snapshots.clear()
        self.turns.clear()
        self.last_at = 0.0

    def gap(self) -> None:
        # A missing record could change configuration or ownership. Keep already
        # captured sample evidence, but never carry state across an unread gap.
        self.reset()

    def feed(self, payload: dict, at: float, *, inherited: bool, ambiguous: bool = False) -> None:
        self.sequence += 1
        event = payload.get("type")
        if inherited or event not in {"thread_settings_applied", "task_started"}:
            return
        if ambiguous or isinstance(payload, AmbiguousObject):
            self.gap()
            return
        if at < self.last_at:
            self.gap()
            self.last_at = at
            return  # A backdated record cannot repair an unknown interval.
        self.last_at = at
        if event == "thread_settings_applied":
            # Never borrow a parent's snapshot, even in a child-owned file.
            if payload.get("thread_id") != self.task:
                self.gap()
                return
            settings = payload.get("thread_settings")
            if isinstance(settings, AmbiguousObject):
                self.gap()
                return
            tier = settings.get("service_tier") if isinstance(settings, dict) else None
            state = "unspecified" if isinstance(settings, dict) and tier is None else "invalid"
            if isinstance(tier, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", tier):
                state = "unspecified" if tier == "unset" else "set"
            if state != "set":
                tier = None
            observation = Configuration(at, tier, state, self.sequence)
            if self.snapshots and self.snapshots[-1].at == at:
                previous = self.snapshots.pop()
                if (previous.tier, previous.state) != (tier, state):
                    observation = replace(observation, tier=None, state="ambiguous")
                else:
                    observation = previous
            self.snapshots.append(observation)
            return
        turn = payload.get("turn_id")
        if (
            payload.get("thread_id", self.task) != self.task
            or not isinstance(turn, str)
            or not re.fullmatch(r"[\w-]{1,100}", turn)
        ):
            self.gap()
            return
        # Newer records supply integer Unix seconds. Use the beginning of that
        # precision interval, not the later write time, as the conservative bound.
        explicit = payload.get("started_at")
        earliest = at
        invalid = False
        if explicit is not None:
            if type(explicit) is int and 0 <= explicit <= at and math.isfinite(explicit):
                earliest = float(explicit)
            else:
                invalid = True
        boundary = TurnBoundary(earliest, at, self.sequence, invalid)
        previous = self.turns.get(turn)
        if previous:
            boundary = replace(
                previous,
                ambiguous=previous.ambiguous or invalid or earliest != previous.earliest,
            )
        self.turns[turn] = boundary
        self.turns.move_to_end(turn)
        while len(self.turns) > self.capacity:
            self.turns.popitem(last=False)

    def capture(self, context: Context, turn: str, before: float) -> Context:
        boundary = self.turns.get(turn)
        candidates = [s for s in self.snapshots if s.at <= before]
        if not candidates:
            return context
        # Freeze the latest observation before the turn. Later thread preference
        # updates are for later turns and must not relabel active-turn output.
        preceding = (
            [s for s in candidates if s.at <= boundary.earliest and s.sequence < boundary.sequence]
            if boundary and not boundary.ambiguous
            else []
        )
        snapshot = (preceding or candidates)[-1]
        return bind_thread_config(
            replace(
                context,
                thread_config_tier=snapshot.tier,
                thread_config_state=snapshot.state,
                thread_config_at=snapshot.at,
                thread_config_source="rollout-thread-settings",
                thread_config_turn_at=boundary.earliest if boundary else None,
                thread_config_turn_observed_at=boundary.observed if boundary else None,
                thread_config_relation=(
                    "ambiguous_turn"
                    if boundary and boundary.ambiguous
                    else "before_turn"
                    if preceding
                    else "during_turn"
                    if boundary
                    else "missing_turn"
                ),
            )
        )

    def validate(self, context: Context, turn: str) -> Context:
        # A later conflicting start for the exact same turn invalidates its
        # association, including outputs already captured in the response cache.
        boundary = self.turns.get(turn)
        if context.thread_config_state is not None and boundary and boundary.ambiguous:
            return bind_thread_config(replace(context, thread_config_relation="ambiguous_turn"))
        return context
