import sqlite3

from token_pulse.logs import LogEvidence, SqliteLogs, read_only


def body(item="a", *, handled=False):
    prefix = "turn{turn.id=turn-a}:sampling"
    if handled:
        prefix += ":handle_output_item_done"
    return prefix + f': Output item item_type="message" item_id="{item}"'


def test_only_actual_start_marker_is_accepted():
    logs = LogEvidence()
    logs.accept("task", 10, "codex_core::stream_events_utils", body(handled=True))
    logs.accept("task", 10, "other_target", body())
    logs.accept("task", 10, "codex_core::stream_events_utils", "ToolCall: " + body())
    assert not logs.starts
    logs.accept("task", 10, "codex_core::stream_events_utils", body())
    assert logs.starts["task", "a"].at == 10
    logs.accept("task", 11, "codex_core::stream_events_utils", body())
    assert logs.starts["task", "a"].ambiguous


def test_request_evidence_does_not_imply_confirmation():
    logs = LogEvidence()
    logs.accept(
        "task",
        9,
        "feedback_tags",
        "turn{turn.id=turn-a}:stream_request{"
        "model=model-a service_tier=priority}: auth_mode=Chatgpt",
    )
    logs.accept("task", 10, "codex_core::stream_events_utils", body())
    assert logs.starts["task", "a"].requested_tier == "priority"
    assert logs.starts["task", "a"].auth_mode == "Chatgpt"


def test_request_without_tier_does_not_invent_default_or_priority():
    logs = LogEvidence()
    logs.accept(
        "task",
        9,
        "feedback_tags",
        "turn{turn.id=turn-a}:stream_request{model=model-a}: auth_mode=Chatgpt",
    )
    logs.accept("task", 10, "codex_core::stream_events_utils", body())
    assert logs.starts["task", "a"].auth_mode == "Chatgpt"
    assert logs.starts["task", "a"].requested_tier is None


def test_memory_is_bounded_and_tasks_do_not_collide():
    logs = LogEvidence(capacity=2)
    for task in ["one", "two", "three"]:
        logs.accept(task, 10, "codex_core::stream_events_utils", body())
    assert list(logs.starts) == [("two", "a"), ("three", "a")]


def test_sqlite_readonly_incremental_and_schema_failure(tmp_path):
    path = tmp_path / "logs.sqlite"
    with sqlite3.connect(path) as c:
        c.execute(
            "CREATE TABLE logs(id INTEGER PRIMARY KEY, ts, ts_nanos, "
            "thread_id, target, feedback_log_body)"
        )
        c.execute(
            "INSERT INTO logs VALUES(1,10,0,?,?,?)",
            ("task", "codex_core::stream_events_utils", body()),
        )
    logs = LogEvidence()
    reader = SqliteLogs(path, logs)
    assert reader.poll() is None
    assert reader.cursor == 1
    assert reader.poll() is None
    assert len(logs.starts) == 1
    connection = read_only(path)
    try:
        connection.execute("DELETE FROM logs")
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("Source database must be read-only")
    connection.close()
    missing = SqliteLogs(tmp_path / "missing", logs)
    assert missing.poll() == "logs_unavailable"


def test_bad_row_cannot_stall_the_log_cursor(tmp_path):
    path = tmp_path / "logs.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE logs(id INTEGER PRIMARY KEY,ts,ts_nanos,"
            "thread_id,target,feedback_log_body)"
        )
        connection.execute(
            "INSERT INTO logs VALUES(1,10,NULL,?,?,?)",
            ("task", "codex_core::stream_events_utils", body("bad")),
        )
        connection.execute(
            "INSERT INTO logs VALUES(2,10,0,?,?,?)",
            ("task", "codex_core::stream_events_utils", body("good")),
        )
    logs = LogEvidence()
    reader = SqliteLogs(path, logs)
    assert reader.poll() is None
    assert reader.cursor == 2
    assert list(logs.starts) == [("task", "good")]


def test_sparse_submissions_are_backfilled_outside_output_id_window(tmp_path):
    from test_submissions import body as submission_body

    path = tmp_path / "logs.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE logs(id INTEGER PRIMARY KEY,ts,ts_nanos,"
            "thread_id,target,feedback_log_body)"
        )
        connection.execute(
            "INSERT INTO logs VALUES(1,9,0,?,?,?)",
            ("task", "codex_core::session::handlers", submission_body()),
        )
        connection.execute(
            "INSERT INTO logs VALUES(100000,10,0,?,?,?)",
            ("task", "codex_core::stream_events_utils", body()),
        )
    evidence = LogEvidence()
    reader = SqliteLogs(path, evidence, backfill=10)
    assert reader.poll() is None
    assert evidence.submissions["task", "turn"].tier == "priority"
    assert reader.poll() is None
    assert len(evidence.submissions) == 1


def test_retained_output_starts_and_auth_are_read_outside_generic_window(tmp_path):
    path = tmp_path / "logs.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE logs(id INTEGER PRIMARY KEY,ts,ts_nanos,"
            "thread_id,target,feedback_log_body)"
        )
        rows = [
            (
                1,
                8,
                0,
                "task",
                "feedback_tags",
                "turn{turn.id=turn-a}:stream_request{model=another-model}: auth_mode=Chatgpt",
            ),
            (2, 10, 0, "task", "codex_core::stream_events_utils", body()),
            (100000, 20, 0, "task", "unrelated", "unrelated event"),
        ]
        db.executemany("INSERT INTO logs VALUES(?,?,?,?,?,?)", rows)
    evidence = LogEvidence()
    reader = SqliteLogs(path, evidence, backfill=10)
    assert reader.poll() is None
    assert evidence.starts["task", "a"].at == 10
    assert evidence.starts["task", "a"].auth_mode == "Chatgpt"
    assert not evidence.starts["task", "a"].ambiguous
    assert reader.cursor == 100000


def test_start_replay_enriches_metadata_but_never_uses_future_request():
    logs = LogEvidence()
    target = "codex_core::stream_events_utils"
    logs.accept("task", 10, target, body())
    logs.accept(
        "task",
        9,
        "feedback_tags",
        "turn{turn.id=turn-a}:stream_request{service_tier=priority}: auth_mode=Chatgpt",
    )
    logs.accept("task", 10, target, body())
    assert logs.starts["task", "a"].requested_tier == "priority"
    assert not logs.starts["task", "a"].ambiguous
    logs.accept(
        "task",
        20,
        "feedback_tags",
        "turn{turn.id=turn-a}:stream_request{service_tier=default}: auth_mode=ApiKey",
    )
    logs.accept("task", 10, target, body())
    assert logs.starts["task", "a"].requested_tier == "priority"
    assert not logs.starts["task", "a"].ambiguous
    logs.accept("task", 15, target, body("before-request"))
    assert logs.starts["task", "before-request"].requested_tier is None
