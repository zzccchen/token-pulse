import importlib.util
from pathlib import Path

import pytest
from test_collector import make_source

from token_pulse.domain import Sample
from token_pulse.history import History

spec = importlib.util.spec_from_file_location(
    "rebuild_week", Path(__file__).resolve().parents[1] / "scripts/rebuild_week.py"
)
rebuild_week = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rebuild_week)


def test_isolated_rebuild_preserves_sources_and_baseline_and_reports_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("token_pulse.collector.desktop_log_root", lambda: None)
    root = tmp_path / "source"
    root.mkdir()
    make_source(root)
    now = 1767225610
    baseline = tmp_path / "baseline.sqlite"
    history = History(baseline)
    history.append([Sample("task", "turn", "response", now - 8, 100)], now=now)
    history.close()
    original = {p: p.read_bytes() for p in [baseline, *root.iterdir()]}
    output = tmp_path / "isolated"
    report = rebuild_week.rebuild(root, output, end=now, baseline=baseline)
    assert report["all_discovered_session_files_completed"]
    assert report["discovered_session_files"] == report["completed_session_files"] == 1
    assert report["fresh_rebuild"]["records"] == 1
    assert report["comparison"]["matched_identities"] == 1
    assert report["restart_summary_matches"]
    assert all(p.read_bytes() == value for p, value in original.items())
    saved = (output / "report.json").read_text()
    assert "PRIVATE" not in saved and str(root) not in saved
    with pytest.raises(FileExistsError):
        rebuild_week.rebuild(root, output, end=now)
    with pytest.raises(ValueError, match="outside"):
        rebuild_week.rebuild(root, root / "forbidden", end=now)
    assert not (root / "forbidden").exists()


def test_partial_file_does_not_report_complete_rebuild(tmp_path, monkeypatch):
    monkeypatch.setattr("token_pulse.collector.desktop_log_root", lambda: None)
    root = tmp_path / "source"
    root.mkdir()
    path = make_source(root)
    path.write_bytes(path.read_bytes() + b'{"incomplete":')
    report = rebuild_week.rebuild(root, tmp_path / "isolated", end=1767225610)
    assert not report["all_discovered_session_files_completed"]
    assert report["completed_session_files"] == 0
    assert report["fresh_rebuild"]["records"] == 1


@pytest.mark.parametrize("retry", [False, True])
def test_optional_archive_restores_origins_with_retry_protection(tmp_path, monkeypatch, retry):
    from token_pulse.evidence_archive import EvidenceArchive
    from token_pulse.logs import LogEvidence, Start

    monkeypatch.setattr("token_pulse.collector.desktop_log_root", lambda: None)
    root = tmp_path / "source"
    root.mkdir()
    make_source(root)
    now = 1767225610
    baseline = tmp_path / "baseline.sqlite"
    history = History(baseline)
    archive = EvidenceArchive(history.connection, history.anonymous_id, root)
    evidence = LogEvidence()
    archive.restore(evidence, now=now)
    evidence.starts["task", "item"] = Start(now - 9, "turn")
    if retry:
        evidence.record_interruption("task", "turn", now - 8.5)
    archive.save(evidence, now=now)
    assert history.read() == []
    history.close()
    before = baseline.read_bytes()
    cold = rebuild_week.rebuild(root, tmp_path / "cold", end=now, baseline=baseline)
    restored = rebuild_week.rebuild(
        root, tmp_path / "restored", end=now, baseline=baseline, restore_evidence=True
    )
    assert cold["fresh_rebuild"]["measurable"] == 0
    assert restored["fresh_rebuild"]["measurable"] == (0 if retry else 1)
    if retry:
        assert restored["fresh_rebuild"]["exclusions"] == {"interrupted_response": 1}
    assert restored["imported_archived_evidence"]["stream_origins"] == 1
    assert restored["imported_archived_evidence"]["sampling_interruptions"] == int(retry)
    assert restored["restart_summary_matches"]
    assert baseline.read_bytes() == before
