from dataclasses import replace

import pytest

from token_pulse.domain import Context, Sample
from token_pulse.timeline import RANGES, series, timeline


def sample(task="a", end=101, tokens=100, seconds=10, **context):
    return Sample(task, "turn", str(end), end, tokens, end - seconds, Context(**context))


def test_cross_conversation_weighting_and_deduplication():
    a, b = sample(), sample("b", tokens=300, seconds=5)
    view = timeline([a, a, b], now=110, duration=60, step=30)
    assert view.summary.weighted_tps == pytest.approx(400 / 15)
    assert view.summary.median_tps == 35
    assert (view.summary.low_tps, view.summary.high_tps) == (10, 60)
    assert view.conversations == 2
    assert view.buckets[-1].conversations == 2


def test_different_models_never_merge():
    a, b = sample(), sample("b", model="specified")
    assert len(series([a, b])) == 2
    with pytest.raises(ValueError):
        timeline([a, b], now=110, duration=60, step=30)


def test_config_snapshot_does_not_split_identical_requests():
    a, b = sample(configured_tier="default"), sample("b", configured_tier="priority")
    assert len(series([a, b])) == 1
    assert timeline([a, b], now=110, duration=60, step=30).summary.count == 2


def test_login_changes_and_missing_metadata_pool_without_rewriting_evidence():
    values = [
        sample("a", auth_mode="Chatgpt"),
        sample("b", auth_mode="ChatgptAuthTokens", tokens=300, seconds=5),
        sample("c", auth_mode="ApiKey"),
        sample("d"),
    ]
    assert len(series(values)) == 1
    view = timeline(values, now=110, duration=60, step=30)
    assert view.summary.count == 4
    assert view.summary.weighted_tps == pytest.approx(600 / 35)
    assert {s.context.auth_mode for s in view.samples} == {
        "Chatgpt",
        "ChatgptAuthTokens",
        "ApiKey",
        None,
    }


def test_effort_levels_pool_without_changing_original_samples():
    values = [
        sample("a", effort="high"),
        sample("b", tokens=600, seconds=20, effort="max"),
        sample("c"),
    ]
    assert len(series(values)) == 1
    view = timeline(values, now=110, duration=60, step=30)
    assert view.summary.weighted_tps == 20
    assert view.summary.count == 3
    assert {s.context.effort for s in view.samples} == {"high", "max", None}


def test_cleared_override_has_standard_client_mode_and_keeps_raw_evidence():
    values = [sample(), sample("b", submitted_tier_state="cleared")]
    assert len(series(values)) == 1
    default = sample("c", submitted_tier="default", submitted_tier_state="set")
    view = timeline([values[1], default], now=110, duration=60, step=30)
    assert view.summary.count == 2
    assert {s.context.submitted_tier_state for s in view.samples} == {"set", "cleared"}
    assert all(
        s.context.requested_tier is None and s.context.actual_tier is None for s in view.samples
    )


def test_fast_aliases_share_client_mode_without_rewriting_raw_tiers():
    values = [
        sample("a", submitted_tier="priority", submitted_tier_state="set"),
        sample("b", submitted_tier="fast", submitted_tier_state="set"),
    ]
    assert len(series(values)) == 1
    view = timeline(values, now=110, duration=60, step=30)
    assert {s.context.submitted_tier for s in view.samples} == {"priority", "fast"}


def test_bucket_boundaries_missing_data_future_and_repair():
    broken = replace(sample(end=90), started_at=None)
    repaired = sample(end=90)
    excluded = replace(sample(end=61), started_at=None)
    values = [
        sample(end=49),
        sample(end=50),
        broken,
        repaired,
        excluded,
        sample(end=110),
        sample(end=111),
    ]
    view = timeline(values, now=110, duration=60, step=30)
    assert [s.ended_at for s in view.samples] == [50, 61, 90, 110]
    assert view.summary.count == 3
    assert view.summary.excluded == 1
    assert view.buckets[1].summary.weighted_tps is None
    assert view.buckets[1].summary.excluded == 1
    assert view.buckets[2].summary.count == 2


@pytest.mark.parametrize("duration,step", RANGES.values())
def test_ranges_include_historical_samples_and_bound_bucket_count(duration, step):
    view = timeline(
        [sample(end=1_000_000 - duration + 1)], now=1_000_000, duration=duration, step=step
    )
    assert view.summary.count == 1
    assert len(view.buckets) <= 61
    assert any(b.summary.weighted_tps is None for b in view.buckets)


@pytest.mark.parametrize("field", ["provider", "requested_tier", "actual_tier", "submitted_tier"])
def test_model_summary_combines_context_changes_without_rewriting_evidence(field):
    a, b = sample(), sample("b", tokens=300, seconds=5, **{field: "specified"})
    view = timeline([a, b], now=110, duration=60, step=30)
    assert len(series([a, b])) == 1
    assert view.summary.weighted_tps == pytest.approx(400 / 15)
    assert getattr(view.samples[0].context, field) is None
    assert getattr(view.samples[1].context, field) == "specified"
