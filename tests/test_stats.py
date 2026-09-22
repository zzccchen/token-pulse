from dataclasses import replace

import pytest

from token_pulse.domain import Context, Sample
from token_pulse.stats import CounterDelta, grouped, summarize


def sample(**kwargs):
    values = dict(
        task_id="task-a",
        turn_id="turn-a",
        response_id="r1",
        started_at=10,
        ended_at=12,
        output_tokens=100,
    )
    return Sample(**(values | kwargs))


def test_weighted_rate_is_not_arithmetic_average():
    result = summarize([sample(), sample(response_id="r2", started_at=20, ended_at=30)])
    assert result.weighted_tps == pytest.approx(200 / 12)
    assert result.median_tps == 30
    assert (result.low_tps, result.high_tps) == (10, 50)


def test_history_statistics_ignore_login_metadata_but_keep_original_records():
    values = [
        sample(context=Context(auth_mode="Chatgpt")),
        sample(response_id="r2", context=Context(auth_mode="ApiKey"), ended_at=20),
        sample(response_id="r3"),
    ]
    assert len(grouped(values)) == 1
    assert summarize(values).weighted_tps == pytest.approx(300 / 14)
    assert [s.context.auth_mode for s in values] == ["Chatgpt", "ApiKey", None]


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"started_at": None}, "missing_start"),
        ({"ended_at": 10}, "invalid_duration"),
        ({"ended_at": 9}, "invalid_duration"),
        ({"ended_at": float("nan")}, "invalid_timestamp"),
        ({"started_at": float("inf")}, "invalid_duration"),
        ({"output_tokens": -1}, "invalid_tokens"),
        ({"output_tokens": True}, "invalid_tokens"),
        ({"output_tokens": 1.5}, "invalid_tokens"),
        ({"issue": "ambiguous_response"}, "ambiguous_response"),
    ],
)
def test_invalid_sample_is_never_zero_tps(changes, reason):
    value = sample(**changes)
    assert value.exclusion == reason
    assert value.tps is None
    assert summarize([value]).count == 0


def test_dedupe_and_late_evidence_repair():
    complete = sample()
    result = summarize([replace(complete, started_at=None), complete, complete])
    assert result.count == 1
    assert result.tokens == 100


def test_deduplication_refreshes_tier_without_losing_complete_measurements():
    from token_pulse.stats import unique

    complete = sample()
    incoming = replace(
        complete,
        started_at=None,
        context=Context(submitted_tier="priority", submitted_tier_state="set"),
    )
    merged = unique([complete, incoming, complete])
    assert len(merged) == 1
    assert merged[0].tps == 50
    assert merged[0].context.submitted_tier == "priority"


def test_tasks_with_identical_response_ids_are_independent():
    assert summarize([sample(), sample(task_id="task-b")]).count == 2


def test_requested_and_confirmed_tiers_remain_raw_evidence_within_model():
    samples = [
        sample(),
        sample(response_id="r2", context=Context(requested_tier="priority")),
        sample(response_id="r3", context=Context(actual_tier="priority")),
    ]
    assert len(grouped(samples)) == 1
    assert summarize(samples).count == 3
    assert samples[0].context.actual_tier is None
    assert samples[2].context.actual_tier == "priority"


def test_window_uses_recent_valid_samples_and_actual_count():
    samples = [sample(response_id=str(i), ended_at=12 + i) for i in range(5)]
    samples.append(sample(response_id="bad", ended_at=100, started_at=None))
    assert summarize(samples, window=2).count == 2
    assert summarize(samples, window=20).count == 5
    assert summarize(samples, window=2).excluded == 1


def test_empty_is_unknown_and_zero_tokens_can_be_real():
    assert summarize([]).weighted_tps is None
    assert sample(output_tokens=0).tps == 0
    assert sample().is_stale(100)
    assert sample().is_stale(0)
    assert not sample().is_stale(20)


def test_cumulative_baseline_repeat_and_reset():
    counter = CounterDelta()
    assert counter.observe(100) == (None, "missing_baseline")
    assert counter.observe(130) == (30, None)
    assert counter.observe(130) == (0, None)
    assert counter.observe(10) == (None, "counter_reset")
    assert counter.observe(20) == (10, None)
    assert counter.observe(-1) == (None, "invalid_tokens")


def test_incompatible_token_scope_is_counted_but_excluded_from_model_speed():
    values = [
        sample(),
        sample(response_id="visible", token_scope="visible_output", output_tokens=999),
    ]
    summary = summarize(values)
    assert summary.count == 1 and summary.excluded == 1
    assert summary.weighted_tps == 50
    assert values[1].exclusion == "unsupported_token_scope"
