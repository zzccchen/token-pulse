import pytest

from token_pulse.logs import LogEvidence, Start
from token_pulse.rollout import REJECTIONS, Rollout, duration


def event(parser, kind, payload, second):
    parser.feed({"type": kind, "timestamp": f"2026-01-01T00:00:{second:02d}Z", "payload": payload})


def ready():
    parser = Rollout("task")
    event(parser, "event_msg", {"type": "task_started", "turn_id": "turn"}, 0)
    event(parser, "turn_context", {"turn_id": "turn", "model": "model-a", "effort": "high"}, 0)
    return parser


def output(parser, item="item", second=12):
    event(
        parser,
        "response_item",
        {"type": "message", "role": "assistant", "id": item, "content": [{"text": "PRIVATE BODY"}]},
        second,
    )


def usage(parser, response="response", second=13):
    event(
        parser,
        "token_usage_record",
        {
            "response_id": response,
            "thread_id": "task",
            "turn_id": "turn",
            "usage": {"output_tokens": 100},
        },
        second,
    )


def evidence(item="item", second=10):
    logs = LogEvidence()
    logs.starts["task", item] = Start(1767225600 + second, "turn", "Chatgpt", "priority")
    return logs


def test_exact_item_pairing_and_usage_are_required():
    parser = ready()
    output(parser)
    usage(parser)
    sample = parser.samples(evidence())[0]
    assert sample.tps == 50
    assert sample.context.requested_tier == "priority"
    assert sample.context.actual_tier is None
    assert "PRIVATE BODY" not in repr(parser.__dict__)
    assert parser.samples(LogEvidence())[0].exclusion == "missing_start"
    assert parser.samples(evidence("another"))[0].tps is None


def test_submitted_tier_requires_exact_turn_and_preceding_timestamp():
    from token_pulse.submissions import SubmittedTier

    parser = ready()
    output(parser)
    usage(parser)
    logs = LogEvidence()
    logs.submissions["task", "another-turn"] = SubmittedTier(1767225600, "priority", "set")
    assert parser.samples(logs)[0].context.submitted_tier is None
    logs.submissions["task", "turn"] = SubmittedTier(1767225700, "priority", "set")
    assert parser.samples(logs)[0].context.submitted_tier is None
    logs.submissions["task", "turn"] = SubmittedTier(1767225600, "priority", "set")
    sample = parser.samples(logs)[0]
    assert sample.context.submitted_tier == "priority"
    assert sample.context.requested_tier is None
    assert sample.context.actual_tier is None
    assert sample.tps is None  # Submission isn't an output start marker.
    assert parser.task(logs).context.submitted_tier == "priority"


def test_model_switch_keeps_each_completed_output_model():
    parser = ready()
    output(parser)
    usage(parser)
    event(parser, "turn_context", {"turn_id": "next", "model": "model-b", "effort": "low"}, 20)
    output(parser, "next-item", 30)
    usage(parser, "next-response", 31)
    samples = parser.samples(LogEvidence())
    assert [s.context.model for s in samples] == ["model-a", "model-b"]
    assert parser.task(LogEvidence()).context.model == "model-b"


def test_duplicate_usage_and_legacy_token_count_do_not_double_count():
    parser = ready()
    output(parser)
    usage(parser)
    usage(parser)
    event(
        parser,
        "event_msg",
        {"type": "token_count", "info": {"last_token_usage": {"output_tokens": 100}}},
        13,
    )
    assert len(parser.samples(evidence())) == 1


def test_legacy_usage_with_output_boundary():
    parser = ready()
    output(parser)
    event(
        parser,
        "event_msg",
        {"type": "token_count", "info": {"last_token_usage": {"output_tokens": 100}}},
        13,
    )
    assert parser.samples(evidence())[0].tps == 50


def test_backfill_without_turn_boundary_is_excluded():
    parser = Rollout("task")
    output(parser)
    usage(parser)
    assert parser.samples(evidence())[0].tps is None


def test_tool_time_and_prefill_wait_are_excluded():
    parser = ready()
    event(parser, "response_item", {"type": "reasoning", "id": "item"}, 11)
    event(
        parser, "response_item", {"type": "custom_tool_call", "id": "tool", "call_id": "call"}, 12
    )
    usage(parser)
    assert parser.task(evidence()).state == "tool"
    event(parser, "response_item", {"type": "custom_tool_call_output", "call_id": "call"}, 40)
    assert parser.task(evidence()).state == "waiting"
    assert parser.samples(evidence())[0].seconds == 2
    event(
        parser,
        "event_msg",
        {"type": "task_complete", "duration_ms": 42000, "time_to_first_token_ms": 10000},
        42,
    )
    task = parser.task(evidence())
    assert task.state == "idle"
    assert (task.turn_seconds, task.wait_seconds) == (42, 10)


def test_retry_and_out_of_order_events_are_not_precise_samples():
    parser = ready()
    output(parser)
    event(parser, "event_msg", {"type": "stream_error"}, 11)
    usage(parser)
    assert parser.samples(evidence())[0].exclusion == "out_of_order"


def test_user_message_does_not_contaminate_output():
    parser = ready()
    event(parser, "response_item", {"type": "message", "id": "user", "role": "user"}, 1)
    output(parser)
    usage(parser)
    assert parser.samples(evidence())[0].tps == 50


def test_malformed_and_reset_then_recovery():
    parser = ready()
    parser.feed({"payload": None})
    output(parser)
    usage(parser)
    assert parser.samples(evidence())[0].tps is None
    output(parser, "next", 22)
    usage(parser, "next", 23)
    assert parser.samples(evidence("next", 20))[-1].tps == 50
    parser.reset()
    assert parser.task(evidence()).state == "unknown"


def test_old_tier_warning_does_not_follow_task_into_a_new_turn():
    parser = ready()
    logs = evidence()
    logs.notices["task"] = (1767225601, "tier_ignored")
    assert parser.task(logs).notice == "tier_ignored"
    event(parser, "event_msg", {"type": "task_started", "turn_id": "next-turn"}, 20)
    assert parser.task(logs).notice is None


@pytest.mark.parametrize("event_type", [[], {}])
def test_invalid_event_type_marks_response_incomplete(event_type):
    parser = ready()
    event(parser, "event_msg", {"type": event_type}, 1)
    output(parser)
    usage(parser)
    assert parser.samples(evidence())[0].tps is None


def test_oversized_duration_is_unknown():
    assert duration(10**1000) is None


def legacy_count(parser, last, total, second):
    event(
        parser,
        "event_msg",
        {
            "type": "token_count",
            "info": {
                "last_token_usage": {"output_tokens": last},
                "total_token_usage": {"output_tokens": total},
            },
        },
        second,
    )


def completed_item(
    parser, item="item", kind="Reasoning", start=10, end=12, thread="task", turn="turn"
):
    event(
        parser,
        "event_msg",
        {
            "type": "item_completed",
            "thread_id": thread,
            "turn_id": turn,
            "started_at_ms": (1767225600 + start) * 1000,
            "completed_at_ms": (1767225600 + end) * 1000,
            "item": {"type": kind, "id": item, "content": "PRIVATE TEXT"},
        },
        end,
    )


def test_legacy_usage_after_tool_result_excludes_the_tool_wait():
    parser = ready()
    event(parser, "response_item", {"type": "reasoning", "id": "item"}, 11)
    event(
        parser, "response_item", {"type": "custom_tool_call", "id": "tool", "call_id": "call"}, 12
    )
    event(parser, "response_item", {"type": "custom_tool_call_output", "call_id": "call"}, 40)
    legacy_count(parser, 100, 100, 41)
    sample = parser.samples(evidence())[0]
    assert sample.tps == 50
    assert sample.seconds == 2
    assert sample.parser_revision == 4


def test_stale_usage_does_not_split_the_next_response_or_reuse_old_tokens():
    parser = ready()
    output(parser)
    legacy_count(parser, 100, 100, 13)
    output(parser, "reasoning-next", 16)
    legacy_count(parser, 100, 100, 17)  # Unchanged rate-limit/resume snapshot.
    event(
        parser,
        "response_item",
        {"type": "custom_tool_call", "id": "tool-next", "call_id": "call"},
        20,
    )
    event(parser, "response_item", {"type": "custom_tool_call_output", "call_id": "call"}, 30)
    legacy_count(parser, 200, 300, 31)
    logs = evidence()
    logs.starts["task", "reasoning-next"] = Start(1767225614, "turn")
    logs.starts["task", "tool-next"] = Start(1767225618, "turn")
    samples = parser.samples(logs)
    observed = [s for s in samples if s.issue not in REJECTIONS]
    assert len(observed) == 2
    assert observed[1].output_tokens == 200
    assert observed[1].seconds == 6
    rejected = [s for s in samples if s.issue in REJECTIONS]
    assert len(rejected) == 2  # The old parser split this response into two bad scopes.
    assert all(s.issue == "stale_usage_snapshot" for s in rejected)
    assert not {s.key for s in rejected} & {s.key for s in observed}


@pytest.mark.parametrize(
    "total,reason", [(50, "usage_counter_reset"), (250, "usage_scope_mismatch")]
)
def test_legacy_counter_conflict_is_specific_and_not_precise(total, reason):
    parser = ready()
    legacy_count(parser, 100, 100, 1)
    output(parser)
    legacy_count(parser, 100, total, 13)
    assert parser.samples(evidence())[0].exclusion == reason


def test_first_cumulative_snapshot_cannot_prove_a_partially_read_response():
    parser = ready()
    output(parser)
    legacy_count(parser, 100, 500, 13)
    assert parser.samples(evidence())[0].exclusion == "missing_usage_baseline"


def test_copied_parent_history_is_rejected_until_a_child_owned_boundary():
    parser = Rollout("task")
    event(
        parser,
        "session_meta",
        {"id": "task", "forked_from_id": "parent", "model_provider": "openai"},
        0,
    )
    event(parser, "session_meta", {"id": "parent", "model_provider": "foreign"}, 0)
    event(parser, "turn_context", {"turn_id": "inherited-turn", "model": "parent-model"}, 0)
    output(parser, "inherited-item", 0)
    legacy_count(parser, 100, 100, 0)
    assert parser.samples(LogEvidence())[0].issue == "inherited_history"
    assert parser.task(LogEvidence()).state == "unknown"
    event(parser, "event_msg", {"type": "thread_settings_applied", "thread_id": "parent"}, 1)
    assert parser.inherited
    event(parser, "event_msg", {"type": "thread_settings_applied", "thread_id": "task"}, 2)
    event(parser, "turn_context", {"turn_id": "turn", "model": "child-model"}, 3)
    output(parser)
    usage(parser)
    samples = parser.samples(evidence())
    assert len(samples) == 2
    assert samples[-1].tps == 50
    assert samples[-1].context.model == "child-model"
    assert samples[-1].context.provider == "openai"


def test_own_session_metadata_also_ends_older_copied_prefixes():
    parser = ready()
    event(parser, "session_meta", {"id": "parent"}, 1)
    output(parser, "inherited-item", 2)
    legacy_count(parser, 100, 100, 3)
    event(parser, "session_meta", {"id": "task"}, 4)
    event(parser, "turn_context", {"turn_id": "own-turn", "model": "own-model"}, 5)
    assert not parser.inherited
    assert parser.boundary
    assert not parser.damaged


def test_inherited_model_and_error_do_not_become_current_child_state():
    parser = ready()
    event(parser, "session_meta", {"id": "parent"}, 1)
    event(parser, "turn_context", {"turn_id": "parent-turn", "model": "parent-model"}, 2)
    event(parser, "event_msg", {"type": "turn_aborted"}, 3)
    task = parser.task(LogEvidence())
    assert task.state == "unknown"
    assert task.notice is None
    assert task.context.model == "model-a"
    assert task.updated_at == 0
    event(parser, "session_meta", {"id": "task"}, 4)
    assert parser.context.model == "model-a"
    assert parser.notice is None


def test_lifecycle_start_recovers_a_response_when_diagnostic_logs_are_gone():
    parser = ready()
    completed_item(parser)
    output(parser)
    event(
        parser, "response_item", {"type": "custom_tool_call", "id": "tool", "call_id": "call"}, 20
    )
    event(parser, "response_item", {"type": "custom_tool_call_output", "call_id": "call"}, 40)
    legacy_count(parser, 100, 100, 41)
    sample = parser.samples(LogEvidence())[0]
    assert sample.tps == 10
    assert sample.timing_source == "item-event"
    assert "PRIVATE TEXT" not in repr(parser.__dict__)


@pytest.mark.parametrize(
    "kind,thread,turn",
    [
        ("CommandExecution", "task", "turn"),
        ("Reasoning", "parent", "turn"),
        ("Reasoning", "task", "another-turn"),
    ],
)
def test_tool_or_foreign_lifecycle_timestamps_cannot_measure_this_response(kind, thread, turn):
    parser = ready()
    completed_item(parser, kind=kind, thread=thread, turn=turn)
    output(parser)
    usage(parser)
    assert parser.samples(LogEvidence())[0].tps is None


def test_lifecycle_end_avoids_a_delayed_rollout_write_timestamp():
    parser = ready()
    completed_item(parser)
    output(parser, second=15)
    usage(parser, second=16)
    sample = parser.samples(LogEvidence())[0]
    assert sample.seconds == 2
    assert sample.ended_at == 1767225612


def test_completion_only_zero_length_lifecycle_cannot_supply_generation_start():
    parser = ready()
    completed_item(parser, start=12, end=12)
    output(parser)
    event(parser, "response_item", {"type": "custom_tool_call", "id": "tool"}, 20)
    legacy_count(parser, 100, 100, 21)
    assert parser.samples(LogEvidence())[0].exclusion == "missing_stream_start"


def test_conflicting_or_out_of_sequence_boundaries_are_not_precise():
    parser = ready()
    completed_item(parser)
    completed_item(parser, start=9)
    output(parser)
    usage(parser)
    assert parser.samples(LogEvidence())[0].exclusion == "ambiguous_response"
    parser = ready()
    output(parser)
    output(parser, "later", 14)
    usage(parser, second=15)
    logs = evidence()
    logs.starts["task", "later"] = Start(1767225608, "turn")
    assert parser.samples(logs)[0].tps is None


@pytest.mark.parametrize("kind", ["function_call", "custom_tool_call"])
@pytest.mark.parametrize("tokens,start", [(14, 12.922), (10000, 1)])
def test_tool_first_notification_does_not_cover_full_response_tokens(kind, tokens, start):
    parser = ready()
    event(parser, "response_item", {"type": kind, "id": "item", "arguments": "{}"}, 13)
    event(
        parser,
        "token_usage_record",
        {"response_id": "response", "usage": {"output_tokens": tokens}},
        14,
    )
    value = parser.samples(evidence(second=start))[0]
    assert value.output_tokens == tokens
    assert value.started_at is None and value.tps is None
    assert value.issue == "unconfirmed_tool_start"
    assert value.timing_source == "tool-item-notification"
    assert value.parser_revision == 4


def test_short_text_with_matching_start_is_not_rejected_based_on_its_speed():
    parser = ready()
    output(parser, second=13)
    event(
        parser,
        "token_usage_record",
        {"response_id": "response", "usage": {"output_tokens": 14}},
        14,
    )
    value = parser.samples(evidence(second=12.922))[0]
    assert value.exclusion is None
    assert value.tps == pytest.approx(14 / 0.078, rel=1e-5)


@pytest.mark.parametrize(
    "kind,lifecycle", [("reasoning", "Reasoning"), ("message", "AgentMessage")]
)
def test_coincident_first_item_cannot_measure_a_later_tool_output(kind, lifecycle):
    parser = ready()
    completed_item(parser, kind=lifecycle, start=12, end=12)
    event(parser, "response_item", {"type": kind, "role": "assistant", "id": "item"}, 12)
    event(parser, "response_item", {"type": "custom_tool_call", "id": "tool"}, 13)
    usage(parser, second=14)
    result = parser.samples(evidence(second=12.0002))[0]
    assert result.output_tokens == 100
    assert result.tps is None and result.started_at is None
    assert result.exclusion == "unconfirmed_initial_span"
    assert result.timing_source == "coincident-item-boundaries"
    # A distinct earlier diagnostic start can still supply a usable boundary.
    restored = parser.samples(evidence(second=10))[0]
    assert restored.exclusion is None and restored.tps == pytest.approx(100 / 3)
    for start in (
        Start(1767225612, "another-turn"),
        Start(1767225612, "turn", ambiguous=True),
    ):
        logs = evidence()
        logs.starts["task", "item"] = start
        assert parser.samples(logs)[0].exclusion == "ambiguous_response"
