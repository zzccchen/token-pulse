import math
from dataclasses import replace

import pytest

from token_pulse.domain import Sample
from token_pulse.relaxed import ELAPSED_ISSUES, estimate, rate


def test_estimate_precedence_preserves_original_measurements_and_exclusions():
    sample = Sample("task", "turn", "response", 102, 100, 100)
    args = {"elapsed_start": 80, "usage_at": 120, "anchor": "previous_usage"}
    strict = estimate(sample, **args)
    assert strict.basis == "validated_output" and strict.tokens_per_second == 50
    lifecycle = replace(sample, issue="unverified_lifecycle_start")
    relaxed = estimate(lifecycle, **args)
    assert relaxed.basis == "unverified_lifecycle" and relaxed.tokens_per_second == 50
    assert lifecycle.tps is None and lifecycle.issue == "unverified_lifecycle_start"
    for issue in ELAPSED_ISSUES - {"unverified_lifecycle_start"}:
        excluded = replace(sample, started_at=None, issue=issue)
        value = estimate(excluded, **args)
        assert value.basis == "elapsed_previous_usage" and value.tokens_per_second == 2.5
        assert excluded.tps is None


@pytest.mark.parametrize(
    "issue",
    [
        "inherited_history",
        "record_gap",
        "usage_counter_reset",
        "cross_source_conflict",
        "unsupported_output_item",
    ],
)
def test_relaxation_does_not_erase_identity_usage_or_unknown_format_errors(issue):
    sample = Sample("task", "turn", "response", 102, 100, issue=issue)
    assert estimate(sample, elapsed_start=80, usage_at=120, anchor="task_started") is None


@pytest.mark.parametrize(
    "tokens,start,end",
    [
        (None, 1, 2),
        (True, 1, 2),
        (-1, 1, 2),
        (2**63, 1, 2),
        (1, None, 2),
        (1, 1, 1),
        (1, 2, 1),
        (1, math.nan, 2),
        (1, 1, math.inf),
        (1, -1e308, 1e308),
        (1, 0, 5e-324),
    ],
)
def test_invalid_arithmetic_stays_missing(tokens, start, end):
    assert rate(tokens, start, end, "test") is None


def test_no_floor_cap_or_imputed_duration_and_zero_tokens_remain_distinct():
    assert rate(0, 1, 2, "test").tokens_per_second == 0
    assert rate(1_000_000, 1, 1.001, "test").tokens_per_second > 999_000_000
    sample = Sample("task", "turn", "response", 102, 100, issue="compaction_usage")
    assert estimate(sample) is None
    assert estimate(sample, elapsed_start=100, usage_at=101, anchor="task_started") is None
    assert estimate(sample, elapsed_start=100, usage_at=103, anchor="nearby_task") is None
    assert (
        estimate(
            replace(sample, token_scope="visible_output"),
            elapsed_start=100,
            usage_at=103,
            anchor="task_started",
        )
        is None
    )
