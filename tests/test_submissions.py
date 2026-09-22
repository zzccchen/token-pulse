import json

import pytest

from token_pulse.logs import LogEvidence
from token_pulse.submissions import submitted_tier


def body(
    tier='Some(Some("priority"))',
    *,
    task="task",
    turn="turn",
    text="private",
    mode="StartOrSteer",
    start="None",
):
    return (
        f'session_loop{{thread_id={task}}}: Submission sub=Submission {{ id: "{turn}", '
        "op: TurnInput { request: TurnInputRequest { "
        f"input: UserInput {{ content: [Text {{ text: {json.dumps(text)}, "
        "text_elements: [] }] }, "
        f"thread_settings: ThreadSettingsOverrides {{ model: None, service_tier: {tier} }}, "
        f"start: TurnStartOptions {{ service_tier: {start} }}, additional_context: {{}} }}, "
        f"mode: {mode}, reply: Sender {{ inner: None }} }}, trace: None }}"
    )


@pytest.mark.parametrize(
    "raw,tier,state",
    [
        ('Some(Some("priority"))', "priority", "set"),
        ('Some(Some("default"))', "default", "set"),
        ('Some(Some("fast"))', "fast", "set"),
        ("Some(None)", None, "cleared"),
        ("None", None, "unspecified"),
    ],
)
def test_explicit_and_unset_values_are_distinct(raw, tier, state):
    turn, value = submitted_tier("task", 10, body(raw))
    assert (turn, value.tier, value.state) == ("turn", tier, state)


def test_string_contents_and_nested_fields_cannot_forge_tier():
    fake = 'ToolCall: service_tier: Some(Some("priority")), " } ] \\ '
    message = body("None", text=fake).replace(
        "model: None,", 'model: None, ignored: Nested { service_tier: Some(Some("priority")) },'
    )
    logs = LogEvidence()
    logs.accept("task", 10, "codex_core::session::handlers", message)
    assert logs.submissions["task", "turn"].state == "unspecified"
    assert fake not in repr(logs.__dict__)
    assert submitted_tier("another-task", 10, message) is None
    assert submitted_tier("task", 10, "ToolCall: " + message) is None


def test_invalid_modes_truncation_duplicate_fields_and_second_override():
    assert submitted_tier("task", 10, body(mode="Steer")) is None
    assert submitted_tier("task", 10, body()[:-2]) is None
    assert submitted_tier("task", 10, body().replace("model: None,", "service_tier: None,")) is None
    assert submitted_tier("task", 10, body(text="x" * 17000)) is None
    assert submitted_tier("task", 10, body(start='Some("default")'))[1].state == "ambiguous"


def test_conflicts_are_not_last_writer_wins_and_storage_is_bounded():
    logs = LogEvidence(capacity=2)
    target = "codex_core::session::handlers"
    logs.accept("task", 10, target, body())
    logs.accept("task", 11, target, body())
    assert logs.submissions["task", "turn"].at == 10
    logs.accept("task", 12, target, body('Some(Some("default"))'))
    assert logs.submissions["task", "turn"].state == "ambiguous"
    logs.accept("task", 13, target, body(turn="second"))
    logs.accept("task", 14, target, body(turn="third"))
    assert len(logs.submissions) == 2
    assert ("task", "turn") not in logs.submissions
