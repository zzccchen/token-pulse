import csv
from dataclasses import replace

import pytest

from token_pulse.domain import Context, Sample
from token_pulse.history import History, export_csv


def sample(**changes):
    values = dict(
        task_id="PRIVATE TASK ID",
        turn_id="PRIVATE TURN",
        response_id="PRIVATE RESPONSE",
        started_at=100,
        ended_at=102,
        output_tokens=100,
    )
    return Sample(**(values | changes))


def test_identifiers_are_anonymous_persistent_and_deduplicated(tmp_path):
    path = tmp_path / "history.sqlite"
    history = History(path)
    history.append([sample(), sample()], now=110)
    rows = history.read()
    assert len(rows) == 1
    assert rows[0].tps == 50
    assert rows[0].task_id != sample().task_id
    assert history.append([sample()], now=111) == 0
    history.close()
    history = History(path)
    history.append([sample()], now=112)
    assert history.read() == rows
    history.close()
    assert b"PRIVATE" not in path.read_bytes()


def test_late_evidence_repairs_history_but_missing_logs_cannot_destroy_it(tmp_path):
    history = History(tmp_path / "history.sqlite")
    incomplete = replace(sample(), started_at=None)
    history.append([incomplete], now=110)
    assert history.read()[0].tps is None
    history.append([sample()], now=110)
    history.append([incomplete], now=111)
    assert history.read()[0].tps == 50
    history.close()


def test_full_backfill_repairs_unknown_turn_without_duplicate_after_restart(tmp_path):
    path = tmp_path / "history.sqlite"
    partial = replace(sample(), turn_id="", started_at=None)
    history = History(path)
    history.append([partial], now=110)
    history.close()
    history = History(path)
    history.append([sample(), partial], now=111)
    rows = history.read()
    assert len(rows) == 1
    assert rows[0].tps == 50
    assert history.anonymous(partial).key == rows[0].key
    history.close()
    history = History(path)
    history.append([partial], now=112)
    assert history.read() == rows
    assert history.anonymous(partial).key == rows[0].key
    history.close()


def test_unknown_turn_is_not_resolved_when_response_identity_is_ambiguous(tmp_path):
    history = History(tmp_path / "history.sqlite")
    partial = replace(sample(), turn_id="", started_at=None)
    history.append([sample(), replace(sample(), turn_id="another-turn"), partial], now=110)
    assert len(history.read()) == 3
    assert history.anonymous(partial).turn_id == history.empty_turn
    history.close()


def test_confirmed_inherited_output_is_removed_and_cannot_return_after_restart(tmp_path):
    path = tmp_path / "history.sqlite"
    history = History(path)
    history.append([sample()], now=110)
    rejected = replace(sample(), issue="inherited_history", parser_revision=2)
    history.reject([rejected], now=110)
    assert history.read() == []
    history.close()
    history = History(path)
    history.append([sample(), replace(sample(), parser_revision=2)], now=111)
    assert history.read() == []
    assert history.is_rejected(sample())
    history.close()
    assert b"PRIVATE" not in path.read_bytes()


def test_stale_snapshot_is_removed_but_later_validated_same_output_can_return(tmp_path):
    history = History(tmp_path / "history.sqlite")
    history.append([sample()], now=110)
    history.reject([replace(sample(), issue="stale_usage_snapshot", parser_revision=2)], now=110)
    history.append([sample()], now=111)
    assert history.read() == []
    corrected = replace(sample(), output_tokens=200, parser_revision=2)
    history.append([corrected], now=112)
    assert len(history.read()) == 1
    assert history.read()[0].tps == 100
    assert not history.is_rejected(corrected)
    history.close()
    history = History(tmp_path / "history.sqlite")
    history.reject([replace(sample(), issue="stale_usage_snapshot", parser_revision=2)], now=113)
    assert len(history.read()) == 1
    assert history.read()[0].tps == 100
    assert not history.is_rejected(corrected)
    # A newer parser can still explicitly supersede that validated interpretation.
    history.reject([replace(sample(), issue="stale_usage_snapshot", parser_revision=3)], now=114)
    assert history.read() == []
    history.close()


def test_parser_revision_can_correct_old_rate_without_losing_it_to_missing_logs(tmp_path):
    history = History(tmp_path / "history.sqlite")
    history.append([sample()], now=110)
    corrected = replace(sample(), started_at=98, parser_revision=2, timing_source="item-event")
    history.append([corrected], now=111)
    assert history.read()[0].tps == 25
    history.append([replace(corrected, started_at=None)], now=112)
    assert history.read()[0].tps == 25
    assert history.read()[0].timing_source == "item-event"
    history.close()


def test_revised_timing_preserves_previously_confirmed_request_metadata(tmp_path):
    history = History(tmp_path / "history.sqlite")
    old = replace(sample(), context=Context(auth_mode="Chatgpt", requested_tier="priority"))
    history.append([old], now=110)
    revised = replace(sample(), started_at=98, parser_revision=2, timing_source="item-event")
    history.append([revised], now=111)
    saved = history.read()[0]
    assert saved.tps == 25
    assert saved.context.auth_mode == "Chatgpt"
    assert saved.context.requested_tier == "priority"
    history.close()


def test_rejection_ledger_respects_retention_and_clear(tmp_path):
    history = History(tmp_path / "history.sqlite", retention_days=1)
    history.reject([replace(sample(), issue="inherited_history")], now=110)
    assert history.rejections
    history.append([], now=100000)
    assert not history.rejections
    history.reject([replace(sample(), issue="inherited_history", ended_at=99999)], now=100000)
    history.clear()
    assert not history.rejections
    assert history.connection.execute("SELECT count(*) FROM rejected_outputs").fetchone()[0] == 0
    history.close()


def test_retention_and_capacity(tmp_path):
    history = History(tmp_path / "history.sqlite", retention_days=1, max_samples=2)
    history.append([sample(response_id=str(i), ended_at=102 + i) for i in range(4)], now=110)
    assert [s.ended_at for s in history.read()] == [105, 104]
    history.append([], now=100000)
    assert history.read() == []
    history.close()


def test_csv_is_anonymous_and_formula_safe(tmp_path):
    history = History(tmp_path / "history.sqlite")
    history.append(
        [sample(context=Context(model="=BAD()", provider='comma, quote"', effort="\t+X"))], now=110
    )
    path = tmp_path / "export.csv"
    export_csv(path, history.read())
    text = path.read_text(encoding="utf-8-sig")
    assert "PRIVATE" not in text
    rows = list(csv.DictReader(text.splitlines()))
    assert rows[0]["model"] == "'=BAD()"
    assert rows[0]["effort"] == "'\t+X"
    assert rows[0]["provider"] == 'comma, quote"'
    assert rows[0]["actual_tier"] == ""
    history.close()


def test_invalid_token_values_remain_exportable_exclusions(tmp_path):
    history = History(tmp_path / "history.sqlite")
    history.append([sample(output_tokens=float("nan"))], now=110)
    assert history.read()[0].output_tokens is None
    assert history.read()[0].exclusion == "invalid_tokens"
    history.close()


def test_tier_enrichment_preserves_valid_tps_across_restart_and_missing_logs(tmp_path):
    path = tmp_path / "history.sqlite"
    history = History(path)
    history.append([sample()], now=110)
    history.close()
    history = History(path)
    incoming = sample(
        started_at=None, context=Context(submitted_tier="priority", submitted_tier_state="set")
    )
    history.append([incoming], now=110)
    stored = history.read()[0]
    assert stored.tps == 50
    assert stored.context.submitted_tier == "priority"
    history.append([sample()], now=111)
    assert history.read() == [stored]
    history.close()


def test_history_only_turns_gain_anonymous_submission_evidence_without_new_rows(tmp_path):
    from token_pulse.submissions import SubmittedTier

    history = History(tmp_path / "history.sqlite")
    history.append([sample(), sample(turn_id="different", response_id="other")], now=110)
    mapping = {(sample().task_id, sample().turn_id): SubmittedTier(99, "default", "set")}
    enriched = history.enrich_submissions(history.read(), mapping)
    assert len(enriched) == 2
    assert sum(s.context.submitted_tier == "default" for s in enriched) == 1
    assert all(s.tps == 50 for s in enriched)
    assert history.read() == enriched
    # A conflicting observation removes the claimed tier but preserves speed.
    mapping[next(iter(mapping))] = SubmittedTier(99, None, "ambiguous")
    enriched = history.enrich_submissions(history.read(), mapping)
    assert all(s.context.submitted_tier is None for s in enriched)
    assert sum(s.context.submitted_tier_state == "ambiguous" for s in enriched) == 1
    history.close()


def test_old_context_json_remains_readable_and_csv_separates_tier_sources(tmp_path):
    history = History(tmp_path / "history.sqlite")
    history.append(
        [sample(context=Context(submitted_tier="default", submitted_tier_state="set"))], now=110
    )
    path = tmp_path / "export.csv"
    export_csv(path, history.read())
    row = next(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))
    assert row["submitted_tier"] == "default"
    assert row["requested_tier"] == row["actual_tier"] == ""
    history.connection.execute(
        "UPDATE samples SET payload=json_remove(payload,'$.context.submitted_tier',"
        "'$.context.submitted_tier_state')"
    )
    assert history.read()[0].context.submitted_tier is None
    history.close()


@pytest.mark.parametrize("issue", ["unconfirmed_tool_start", "unconfirmed_initial_span"])
def test_new_boundary_withdrawal_replaces_old_speed_and_survives_old_batches(tmp_path, issue):
    path = tmp_path / "history.sqlite"
    old = sample(parser_revision=3)
    corrected = replace(
        old,
        parser_revision=4,
        started_at=None,
        issue=issue,
        timing_source="tool-item-notification",
    )
    history = History(path)
    history.append([old], now=110)
    history.append([corrected, replace(old, turn_id=""), old], now=111)
    assert len(history.read()) == 1
    assert history.read()[0].exclusion == issue
    assert history.connection.execute("SELECT valid FROM samples").fetchone()[0] == 0
    history.close()
    history = History(path)
    history.append([old], now=112)
    assert history.read()[0].tps is None
    assert history.read()[0].output_tokens == 100
    export_csv(tmp_path / "result.csv", history.read())
    with (tmp_path / "result.csv").open(encoding="utf-8-sig") as stream:
        row = next(csv.DictReader(stream))
    assert row["tps"] == "" and row["stream_seconds"] == ""
    assert row["exclusion"] == issue
    # Full evidence from the current parser can still repair a partial observation.
    history.append([replace(old, parser_revision=4)], now=113)
    assert history.read()[0].tps == 50
    history.close()
