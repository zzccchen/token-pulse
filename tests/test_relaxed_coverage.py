import csv
import importlib.util
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_collector import make_source

from token_pulse.cycle_timing import CycleBoundaries
from token_pulse.domain import Context, Sample
from token_pulse.history import History
from token_pulse.rollout import ItemTiming, Response

spec = importlib.util.spec_from_file_location(
    "relaxed_coverage", Path(__file__).resolve().parents[1] / "scripts/relaxed_coverage.py"
)
relaxed_coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relaxed_coverage)


def response(turn="a", identity="response", at=10, **kwargs):
    return Response(turn, identity, at, 100, (("item", at - 1),), Context(), None, **kwargs)


def started(turn, at):
    return {
        "type": "event_msg",
        "timestamp": datetime.fromtimestamp(at, UTC).isoformat(),
        "payload": {"type": "task_started", "turn_id": turn},
    }


def clean_parser(**kwargs):
    return SimpleNamespace(
        **(
            {
                "inherited": False,
                "ordinal_inherited": False,
                "history_ordinal_missing": False,
                "damaged": False,
            }
            | kwargs
        )
    )


def test_cycle_boundaries_scope_concurrency_duplicate_and_delayed_usage():
    cycle = CycleBoundaries()
    cycle.feed(started("a", 1), clean_parser(), 1)
    cycle.feed(started("b", 2), clean_parser(), 2)
    first = response(at=10)
    assert cycle.response(first) == {"elapsed_start": 1, "usage_at": 10, "anchor": "task_started"}
    assert cycle.response(first) is None
    assert cycle.response(response("b", at=20))["elapsed_start"] == 2
    assert cycle.response(response(identity="next", at=30))["elapsed_start"] == 10
    # A preceding response's delayed usage cannot become a start after output.
    assert cycle.response(response(identity="overlap", at=30.5)) is None


def test_known_lifecycle_start_prevents_a_partial_cycle_interval():
    cycle = CycleBoundaries()
    cycle.feed(started("a", 1), clean_parser(), 1)
    cycle.response(response(at=10))
    assert (
        cycle.response(response(identity="next", at=20, timings=(ItemTiming("a", 9, 19),))) is None
    )


def test_gaps_conflicts_resets_and_inherited_prefixes_never_supply_anchors():
    cycle = CycleBoundaries()
    cycle.feed(started("a", 1), clean_parser(), 1)
    cycle.feed(started("a", 2), clean_parser(), 2)
    assert cycle.response(response()) is None
    cycle.clear()
    cycle.feed(started("a", 1), clean_parser(inherited=True), 1)
    assert cycle.response(response()) is None
    cycle.clear()
    cycle.feed(started("a", 1), clean_parser(), 1)
    cycle.feed({}, clean_parser(damaged=True), None)
    assert cycle.response(response()) is None
    cycle.clear()
    cycle.feed(started("a", 1), clean_parser(), 1)
    assert cycle.response(response(usage_issue="usage_counter_reset")) is None
    # A reset record is not promoted to an interval or used as previous usage.
    assert cycle.response(response(identity="after", at=20))["elapsed_start"] == 1


def test_fixed_population_isolated_report_counts_missing_records_in_denominator(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("token_pulse.collector.desktop_log_root", lambda: None)
    root = tmp_path / "source"
    root.mkdir()
    make_source(root)
    end = 1767225610
    reference = tmp_path / "reference/history.sqlite"
    history = History(reference)
    sample = Sample(
        "task", "turn", "response", end - 8, 100, issue="missing_stream_start", parser_revision=13
    )
    history.append([sample, replace(sample, task_id="unavailable")], now=end)
    history.close()
    (reference.parent / "report.json").write_text(
        json.dumps(
            {
                "window_start_utc": datetime.fromtimestamp(end - 604800, UTC).isoformat(),
                "window_end_utc": datetime.fromtimestamp(end, UTC).isoformat(),
            }
        )
    )
    original = {p: p.read_bytes() for p in [reference, *root.iterdir()]}
    output = tmp_path / "relaxed"
    report = relaxed_coverage.run(reference, output, root)
    assert report["records"] == 2 and report["covered"] == 1
    assert report["coverage"] == 0.5 and not report["target_met"]
    assert report["validated_records"] == 0
    assert report["groups"] == {"elapsed_task_started": 1}
    assert report["original_samples_unchanged"] and report["restart_matches"]
    assert all(p.read_bytes() == content for p, content in original.items())
    with sqlite3.connect(output / "history.sqlite") as db:
        assert db.execute("SELECT count(*) FROM samples WHERE valid=1").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM relaxed_estimates").fetchone()[0] == 1
    with (output / "estimates.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2 and all(not r["validated_output_tps"] for r in rows)
    actual = next(r for r in rows if r["basis"] == "elapsed_task_started")
    assert float(actual["seconds"]) == 3  # Usage arrives after output completion.
    assert float(actual["estimated_tokens_per_second"]) == pytest.approx(100 / 3)
    assert "PRIVATE" not in (output / "estimates.csv").read_text(encoding="utf-8-sig")
    assert str(root) not in (output / "report.json").read_text()
    with pytest.raises(ValueError, match="new"):
        relaxed_coverage.run(reference, output, root)
    with pytest.raises(ValueError, match="outside"):
        relaxed_coverage.run(reference, root / "unsafe", root)


@pytest.mark.parametrize(
    "change",
    [
        {"output_tokens": 101},
        {"ended_at": 1767225601},
        {"turn_id": "different"},
    ],
)
def test_changed_identity_tokens_or_completion_cannot_borrow_an_interval(
    tmp_path, monkeypatch, change
):
    monkeypatch.setattr("token_pulse.collector.desktop_log_root", lambda: None)
    root = tmp_path / "source"
    root.mkdir()
    make_source(root)
    reference = tmp_path / "reference/history.sqlite"
    history = History(reference)
    sample = Sample(
        "task",
        "turn",
        "response",
        1767225602,
        100,
        issue="missing_stream_start",
        parser_revision=13,
    )
    history.append([replace(sample, **change)], now=1767225610)
    history.close()
    (reference.parent / "report.json").write_text(
        json.dumps(
            {
                "window_start_utc": "2025-12-25T00:00:10+00:00",
                "window_end_utc": "2026-01-01T00:00:10+00:00",
            }
        )
    )
    report = relaxed_coverage.run(reference, tmp_path / "result", root)
    assert report["covered"] == 0 and report["records"] == 1
    assert report["not_reparsed_records"] == 1
    assert not report["target_met"]
