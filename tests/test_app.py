import os
import subprocess
import sys

import pytest


def test_application_starts_and_exits_without_system_tray():
    result = subprocess.run(
        [sys.executable, "-m", "token_pulse", "--demo", "--no-tray", "--hidden", "--smoke-test"],
        env=os.environ | {"QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr


def test_diagnosis_of_missing_source_never_creates_it(tmp_path):
    source = tmp_path / "missing"
    result = subprocess.run(
        [sys.executable, "-m", "token_pulse", "--diagnose", "--codex-home", str(source)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert "no_sessions" in result.stdout
    assert str(source) not in result.stdout
    assert not source.exists()


def test_each_background_chunk_refreshes_main_count_speed_and_chart(tmp_path):
    from dataclasses import replace

    from PySide6.QtWidgets import QApplication

    from token_pulse.app import Controller
    from token_pulse.collector import Snapshot
    from token_pulse.demo import demo_snapshot
    from token_pulse.service import Update

    app = QApplication.instance() or QApplication([])
    controller = Controller(app, tmp_path, demo=True, no_tray=True, language="zh_CN")
    base = demo_snapshot().samples[-1]
    one = replace(base, output_tokens=100, started_at=base.ended_at - 10)
    two = replace(base, response_id="new-output", output_tokens=300, started_at=base.ended_at - 5)
    updates = iter(
        [
            Update(Snapshot(at=base.ended_at, backfill_pending=2, backfill_completed=1), (one,)),
            Update(Snapshot(at=base.ended_at, backfill_completed=3, backfill_reused=2), (one, two)),
        ]
    )

    class Updates:
        def latest(self):
            return next(updates)

        def stop(self):
            pass

    controller.monitor = Updates()
    try:
        controller.tick()
        assert controller.panel.view.summary.count == 1
        assert controller.panel.speed_value.text() == "10.0"
        assert not controller.panel.recovery.isHidden()
        controller.tick()
        assert controller.panel.view.summary.count == 2
        assert controller.panel.speed_value.text() == "26.7"
        assert sum(len(b.samples) for b in controller.panel.view.buckets) == 2
        assert "当前范围 2 段" in controller.panel.activity.toolTip()
        assert controller.panel.recovery.isHidden()
    finally:
        controller.close()


@pytest.mark.parametrize(
    "language,average_label,sample_label",
    [
        ("zh_CN", "近 30 分钟 平均 50.0 TPS", "最近采样"),
        ("en", "Last 30 min: average 50.0 TPS", "Last sample"),
    ],
)
def test_tray_average_survives_idle_until_samples_leave_selected_range(
    tmp_path, monkeypatch, language, average_label, sample_label
):
    from dataclasses import replace

    from PySide6.QtWidgets import QApplication

    from token_pulse.app import Controller
    from token_pulse.collector import Snapshot
    from token_pulse.demo import demo_snapshot

    class Tray:
        def setIcon(self, icon):
            pass

        def setToolTip(self, text):
            self.tooltip = text

        def hide(self):
            pass

    app = QApplication.instance() or QApplication([])
    controller = Controller(app, tmp_path, demo=True, no_tray=True, language=language)
    controller.timer.stop()
    controller.tray = Tray()
    controller.preferences = replace(controller.preferences, tray_number=True)
    base = demo_snapshot().samples[-1]
    sample = replace(base, output_tokens=100, started_at=base.ended_at - 2)
    controller.panel.apply(Snapshot(samples=(sample,)))
    try:
        monkeypatch.setattr("token_pulse.app.time.time", lambda: base.ended_at + 61)
        controller.tick()
        assert controller.icon_state[1] == "50"
        assert average_label in controller.tray.tooltip
        assert sample_label in controller.tray.tooltip
        monkeypatch.setattr("token_pulse.app.time.time", lambda: base.ended_at + 1801)
        controller.tick()
        assert controller.icon_state[1] is None
        controller.panel.set_range("1d")
        controller.tick()
        assert controller.icon_state[1] == "50"
        controller.preferences = replace(controller.preferences, tray_number=False)
        controller.tick()
        assert controller.icon_state[1] is None
    finally:
        controller.close()
