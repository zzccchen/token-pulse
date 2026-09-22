"""Time-window statistics across all conversations for each model."""

from __future__ import annotations

import math
from dataclasses import dataclass

from token_pulse.domain import Context, Sample
from token_pulse.stats import Summary, summarize, unique

# Duration and fixed bucket width, in seconds. Bucket edges stay stable during refresh.
RANGES = {"30m": (1800, 30), "1d": (86400, 1800), "7d": (604800, 14400)}


def series_key(sample: Sample) -> tuple[Context]:
    return sample.group


def series(samples: list[Sample]) -> dict[tuple, list[Sample]]:
    result: dict[tuple, list[Sample]] = {}
    for sample in unique(samples):
        if math.isfinite(sample.ended_at):
            result.setdefault(series_key(sample), []).append(sample)
    return result


@dataclass(frozen=True)
class Bucket:
    start: float
    end: float
    samples: tuple[Sample, ...]
    summary: Summary

    @property
    def conversations(self) -> int:
        return len({s.task_id for s in self.samples if s.exclusion is None})


@dataclass(frozen=True)
class Timeline:
    start: float
    end: float
    buckets: tuple[Bucket, ...]
    samples: tuple[Sample, ...]
    summary: Summary

    @property
    def conversations(self) -> int:
        return len({s.task_id for s in self.samples if s.exclusion is None})

    @property
    def observed_conversations(self) -> int:
        return len({s.task_id for s in self.samples})


def aggregate(samples: list[Sample]) -> Summary:
    return summarize(samples)


def timeline(samples: list[Sample], *, now: float, duration: int, step: int) -> Timeline:
    if not math.isfinite(now) or duration <= 0 or step <= 0:
        raise ValueError("Finite time and positive duration/step required")
    if len({series_key(s) for s in samples}) > 1:
        raise ValueError("Different models must be summarized separately")
    start = now - duration
    selected = [s for s in unique(samples) if start <= s.ended_at <= now]
    first = math.floor(start / step) * step
    count = max(1, math.ceil((now - first) / step))
    bins: list[list[Sample]] = [[] for _ in range(count)]
    for sample in selected:
        bins[min(count - 1, math.floor((sample.ended_at - first) / step))].append(sample)
    buckets = tuple(
        Bucket(
            max(start, first + i * step),
            min(now, first + (i + 1) * step),
            tuple(values),
            aggregate(values),
        )
        for i, values in enumerate(bins)
    )
    return Timeline(start, now, buckets, tuple(selected), aggregate(selected))
