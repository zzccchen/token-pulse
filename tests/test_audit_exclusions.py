import csv
import importlib.util
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_collector import make_source

from token_pulse.domain import Sample
from token_pulse.history import History

spec = importlib.util.spec_from_file_location(
    "audit_exclusions", Path(__file__).resolve().parents[1] / "scripts/audit_exclusions.py"
)
audit_exclusions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_exclusions)


def test_fixed_exclusion_audit_repairs_only_isolated_copy(tmp_path, monkeypatch):
    monkeypatch.setattr("token_pulse.collector.desktop_log_root", lambda: None)
    root = tmp_path / "source"
    root.mkdir()
    make_source(root)
    end = 1767225610
    reference = tmp_path / "reference/history.sqlite"
    history = History(reference)
    missing = Sample(
        "task",
        "turn",
        "response",
        end - 8,
        100,
        end - 9,
        issue="unverified_lifecycle_start",
        timing_source="item-event",
        parser_revision=12,
    )
    history.append([missing], now=end)
    salt = history.salt
    history.close()
    (reference.parent / "report.json").write_text(
        json.dumps(
            {
                "window_start_utc": datetime.fromtimestamp(end - 604800, UTC).isoformat(),
                "window_end_utc": datetime.fromtimestamp(end, UTC).isoformat(),
            }
        )
    )
    baseline = tmp_path / "baseline.sqlite"
    history = History(baseline)
    history.salt = salt
    history.empty_turn = history.anonymous_id("turn", "")
    with history.connection:
        history.connection.execute("UPDATE settings SET value=? WHERE key='salt'", (salt.hex(),))
    history.append(
        [replace(missing, issue=None, timing_source="stream-log", parser_revision=9)], now=end
    )
    history.close()
    original = {p: p.read_bytes() for p in [reference, baseline, *root.iterdir()]}
    destination = tmp_path / "repaired"
    report = audit_exclusions.audit(
        reference, baseline, destination / "report.json", root, repaired_output=destination
    )
    assert report["fixed_exclusions"] == report["reparsed_exclusions"] == 1
    assert report["repair"]["restored_saved_independent"] == 1
    assert report["repair"]["valid_after"] == 1
    assert report["repair"]["records_before"] == report["repair"]["records_after"] == 1
    assert report["repair"]["restart_matches"]
    assert all(p.read_bytes() == contents for p, contents in original.items())
    assert "PRIVATE" not in (destination / "report.json").read_text()
    assert str(root) not in (destination / "report.json").read_text()
    with pytest.raises(ValueError, match="new"):
        audit_exclusions.audit(
            reference, baseline, destination / "report.json", root, repaired_output=destination
        )


def test_relaxed_arithmetic_is_separate_and_does_not_promote_invalid_samples(tmp_path):
    sample = Sample(
        "=synthetic",
        "turn",
        "response",
        102,
        100,
        100,
        issue="unverified_lifecycle_start",
        timing_source="item-event",
    )
    ignored = [
        replace(sample, issue=issue)
        for issue in ["unconfirmed_tool_start", "interrupted_response", "compaction_usage"]
    ]
    report = audit_exclusions.lifecycle_arithmetic([sample, *ignored])
    assert report["records"] == 1 and report["median"] == 50
    assert report["promoted_to_valid"] == 0 and sample.tps is None
    path = tmp_path / "reference.csv"
    audit_exclusions.write_lifecycle_reference(path, [sample, *ignored])
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1 and rows[0]["task_hmac"] == "'=synthetic"
    assert "tps" not in rows[0]
    assert rows[0]["assessment"] == "unverified_lifecycle_not_output_tps"
    assert sample.exclusion == "unverified_lifecycle_start"
