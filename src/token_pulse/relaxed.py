"""Explicitly relaxed rate estimates, separate from validated output TPS."""

from __future__ import annotations

import math
from dataclasses import dataclass

from token_pulse.domain import Sample

RELAXED_REVISION = 1
ELAPSED_ISSUES = frozenset(
    {
        "unverified_lifecycle_start",
        "unconfirmed_tool_start",
        "missing_stream_start",
        "missing_start",
        "unconfirmed_initial_span",
        "compaction_usage",
        "missing_output_items",
        "interrupted_response",
    }
)


@dataclass(frozen=True)
class RateEstimate:
    basis: str
    started_at: float
    ended_at: float
    tokens_per_second: float


def rate(tokens, start, end, basis):
    if (
        type(tokens) is not int
        or not 0 <= tokens <= 2**63 - 1
        or type(start) not in (int, float)
        or type(end) not in (int, float)
        or not math.isfinite(start)
        or not math.isfinite(end)
        or end <= start
        or not math.isfinite(end - start)
    ):
        return None
    value = tokens / (end - start)
    if not math.isfinite(value):
        return None
    return RateEstimate(basis, start, end, value)


def estimate(sample: Sample, *, elapsed_start=None, usage_at=None, anchor=None):
    """Prefer matching stream time, then lifecycle time, then observed cycle time.

    The caller must establish exact identity and uninterrupted, same-turn event
    order for elapsed boundaries. Cycle time may contain waiting, tool execution,
    retries and delayed usage. It does not establish output-generation duration.
    """
    if anchor is None:
        elapsed_start, usage_at, anchor = (
            sample.cycle_started_at,
            sample.cycle_ended_at,
            sample.cycle_anchor,
        )
    if sample.tps is not None:
        return rate(sample.output_tokens, sample.started_at, sample.ended_at, "validated_output")
    if sample.token_scope != "total_output" or sample.exclusion not in ELAPSED_ISSUES:
        return None
    if sample.exclusion == "unverified_lifecycle_start":
        observed = rate(
            sample.output_tokens, sample.started_at, sample.ended_at, "unverified_lifecycle"
        )
        if observed:
            return observed
    if anchor not in {"previous_usage", "task_started"}:
        return None
    if (
        type(usage_at) not in (int, float)
        or not math.isfinite(usage_at)
        or usage_at < sample.ended_at
    ):
        return None
    return rate(sample.output_tokens, elapsed_start, usage_at, f"elapsed_{anchor}")
