import json

import pytest

from token_pulse.logs import LogEvidence
from token_pulse.submissions import feedback_tier, submitted_tier


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
        ('Some(Some("ultrafast"))', "ultrafast", "set"),
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
    result = submitted_tier("task", 10, body(start='Some("default")'))[1]
    assert (result.tier, result.state) == ("priority", "set")
    assert (result.override_tier, result.override_state) == ("default", "set")


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


def feedback_body(tier="ultrafast", *, task="task", turn="turn", tags=None):
    tags = {"service_tier": tier} if tags is None else tags
    return (
        f"session_loop{{thread_id={task}}}:"
        'submission_dispatch{otel.name="submission" submission.id="submission" codex.op="input"}:'
        f'codex.turn_input{{conversation.id={task} turn.id="{turn}"}}:'
        f'turn{{otel.name="turn" thread.id={task} turn.id={turn} model=model-a}}:'
        "session_task.run:run_turn:"
        f'run_sampling_request{{turn_id={turn} model=model-a cwd="/demo"}}:'
        f"try_run_sampling_request{{turn_id={turn} model=model-a}}: "
        'model="model-a" approval_policy=Never sandbox_policy=WorkspaceWrite '
        f"effort=Ultra auth_mode=Chatgpt tags_json={json.dumps(tags, separators=(',', ':'))}"
    )


@pytest.mark.parametrize("tier", ["default", "priority", "fast", "ultrafast", "future-tier"])
def test_turn_feedback_preserves_explicit_client_setting(tier):
    turn, value = feedback_tier("task", 10, feedback_body(tier))
    assert (turn, value.at, value.tier, value.state) == ("turn", 10, tier, "set")
    logs = LogEvidence()
    logs.accept("task", 10, "feedback_tags", feedback_body(tier))
    assert logs.config_tags["task", "turn"] == value
    assert not logs.submissions
    assert not logs.requests  # Client settings do not prove an outbound request.


@pytest.mark.parametrize(
    "tags",
    [
        {},
        {"service_tier": None},
        {"service_tier": 1},
        {"service_tier": ""},
        {"feature.fast_mode": "true"},
        {"nested": {"service_tier": "priority"}},
    ],
)
def test_turn_feedback_missing_or_nested_tiers_are_unknown(tags):
    assert feedback_tier("task", 10, feedback_body(tags=tags)) is None


def test_turn_feedback_unset_is_unspecified_not_a_clear_or_default():
    turn, value = feedback_tier("task", 10, feedback_body("unset"))
    assert turn == "turn"
    assert value.tier is None
    assert value.state == "unspecified"


@pytest.mark.parametrize(
    "change",
    [
        lambda b: b.replace("thread_id=task", "thread_id=other"),
        lambda b: b.replace("thread.id=task", "thread.id=other"),
        lambda b: b.replace("conversation.id=task", "conversation.id=other"),
        lambda b: b.replace("turn.id=turn", "turn.id=other"),
        lambda b: b.replace("turn_id=turn", "turn_id=other", 1),
        lambda b: b.replace("run_sampling_request{", "unscoped{"),
        lambda b: b.replace("thread.id=task", "thread.id=task thread.id=other"),
        lambda b: b.replace(
            '"service_tier":"ultrafast"', '"service_tier":"ultrafast","service_tier":"default"'
        ),
        lambda b: b[:-1],
        lambda b: "ToolCall: " + b,
        lambda b: b + "x" * 16000,
    ],
)
def test_turn_feedback_rejects_unmatched_conflicting_or_damaged_scope(change):
    assert feedback_tier("task", 10, change(feedback_body())) is None


def test_turn_feedback_quoted_content_cannot_supply_scope_or_tiers():
    fake = 'turn.id=other service_tier=priority tags_json={"service_tier":"default"}'
    message = feedback_body(tags={"note": fake, "service_tier": "ultrafast"})
    message = message.replace('cwd="/demo"', f"cwd={json.dumps(fake)}")
    logs = LogEvidence()
    logs.accept("task", 10, "feedback_tags", message)
    assert logs.config_tags["task", "turn"].tier == "ultrafast"
    assert fake not in repr(logs.__dict__)
    forged = feedback_body().replace('turn.id="turn"', f"ignored={json.dumps('turn.id=turn')}")
    forged = forged.replace("turn.id=turn", "ignored=none")
    assert feedback_tier("task", 10, forged) is None


@pytest.mark.parametrize(
    "cwd", [r"C:\demo\Project Folder", "/demo/Project Folder", r"\\demo\share\Project Folder"]
)
def test_sampling_display_path_with_spaces_does_not_hide_ultrafast_tag(cwd):
    message = feedback_body().replace('cwd="/demo"', f"cwd={cwd}")
    turn, tag = feedback_tier("task", 10, message)
    assert (turn, tag.tier, tag.state) == ("turn", "ultrafast", "set")
    logs = LogEvidence()
    logs.accept("task", 10, "feedback_tags", message)
    assert logs.config_tags["task", "turn"] == tag
    assert not logs.submissions and not logs.requests
    assert cwd not in repr(logs.__dict__)
    assert feedback_tier("other", 10, message) is None
    assert feedback_tier("task", 10, message.replace("turn_id=turn", "turn_id=other", 1)) is None


@pytest.mark.parametrize(
    "cwd",
    [
        r"C:\demo\Project Folder turn_id=other",
        r"C:\demo\Project Folder turn_id=turn",
        r"C:\demo\Project Folder thread.id=other",
        r"C:\demo\Project Folder cwd=other",
        "relative unquoted path",
        "C:\\demo\\Project\nFolder",
    ],
)
def test_display_path_never_supplies_or_hides_identity_fields(cwd):
    message = feedback_body().replace('cwd="/demo"', f"cwd={cwd}")
    assert feedback_tier("task", 10, message) is None
    assert feedback_tier("task", 10, message.replace("turn_id=turn", "ignored=none")) is None


def test_turn_feedback_is_scoped_and_independent_of_submissions():
    logs = LogEvidence(capacity=2)
    logs.accept("task", 9, "codex_core::session::handlers", body())
    logs.accept("task", 10, "feedback_tags", feedback_body("default"))
    logs.accept("task", 11, "feedback_tags", feedback_body("default"))
    assert logs.submissions["task", "turn"].tier == "priority"
    assert logs.submissions["task", "turn"].at == 9
    assert logs.config_tags["task", "turn"].tier == "default"
    assert logs.config_tags["task", "turn"].at == 10
    logs.accept("task", 9.5, "feedback_tags", feedback_body("priority"))
    logs.accept("task", 12, "feedback_tags", feedback_body("default"))
    assert logs.config_tags["task", "turn"].state == "ambiguous"
    assert logs.config_tags["task", "turn"].at == 9.5
    logs.accept("other", 11, "feedback_tags", feedback_body(task="other"))
    assert logs.config_tags["other", "turn"].tier == "ultrafast"
    logs.accept("task", 12, "feedback_tags", feedback_body(turn="next"))
    assert ("task", "turn") not in logs.config_tags


def test_issue_three_minimal_sampling_shape_and_scope_requirements():
    message = (
        'session_loop{thread_id=demo-task}:codex.turn_input{turn.id="demo-turn"}:'
        'try_run_sampling_request{turn_id=demo-turn}: model="demo-model" '
        'auth_mode=Some(Chatgpt) tags_json={"service_tier":"priority"}'
    )
    turn, tag = feedback_tier("demo-task", 10, message)
    assert (turn, tag.tier, tag.state) == ("demo-turn", "priority", "set")
    assert feedback_tier("other", 10, message) is None
    assert feedback_tier("demo-task", 10, message.replace("codex.turn_input", "other")) is None
    assert (
        feedback_tier("demo-task", 10, message.replace("turn_id=demo-turn", "turn_id=other"))
        is None
    )
    assert (
        feedback_tier("demo-task", 10, message.replace("try_run_sampling_request", "other")) is None
    )
