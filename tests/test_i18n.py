import ast
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from string import Formatter

import pytest
from PySide6.QtWidgets import QApplication

from token_pulse import i18n
from token_pulse.collector import Snapshot
from token_pulse.demo import demo_snapshot
from token_pulse.history import export_csv
from token_pulse.locales.en import MESSAGES
from token_pulse.preferences import Preferences, load, save
from token_pulse.ui.history_window import HistoryWindow
from token_pulse.ui.panel import DIAGNOSTICS, ISSUES, RANGE_LABELS, STATES, Panel
from token_pulse.ui.settings import Settings
from token_pulse.ui.timeline_chart import TimelineChart, mode_info, sample_text


def test_system_resolution_and_missing_translation_fallback(monkeypatch):
    from PySide6.QtCore import QLocale

    for locale, expected in [
        ("zh_CN", "zh_CN"),
        ("zh_TW", "zh_CN"),
        ("en_US", "en"),
        ("de_DE", "en"),
    ]:
        monkeypatch.setattr(QLocale, "system", lambda locale=locale: QLocale(locale))
        assert i18n.set_language("system") == expected
    assert i18n.set_language("zh_CN") == "zh_CN"
    assert i18n.tr("未知") == "未知"
    i18n.set_language("en")
    assert i18n.tr("未知") == "Unknown"
    assert i18n.tr("unrecognized {name}", name="{literal}") == "unrecognized {literal}"


def test_catalog_covers_literal_messages_and_preserves_format_specs():
    root = Path(__file__).parents[1] / "src/token_pulse"
    messages = set()
    for path in [root / "app.py", root / "__main__.py", *root.glob("ui/*.py")]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                messages.add(node.args[0].value)
    for mapping in (DIAGNOSTICS, ISSUES, STATES, RANGE_LABELS):
        messages.update(mapping.values())
    messages.add(TimelineChart.DESCRIPTION)
    assert messages <= MESSAGES.keys()
    formatter = Formatter()

    def fields(value):
        return sorted(
            (name, spec, conversion)
            for _, name, spec, conversion in formatter.parse(value)
            if name is not None
        )

    for source, translation in MESSAGES.items():
        assert translation
        assert fields(source) == fields(translation), source


@pytest.mark.parametrize("language", ["system", "zh_CN", "en"])
def test_language_preferences_and_settings(tmp_path, language):
    app = QApplication.instance() or QApplication([])
    i18n.set_language("en")
    path = tmp_path / "settings.json"
    preferences = Preferences(source=str(tmp_path), language=language)
    save(path, preferences)
    assert load(path) == preferences
    dialog = Settings(preferences)
    try:
        assert dialog.windowTitle() == "Settings · TokenPulse"
        assert dialog.language.currentData() == language
        assert dialog.preferences() == preferences
        dialog.language.setCurrentIndex(dialog.language.findData("zh_CN"))
        assert dialog.preferences().language == "zh_CN"
        # The standalone dialog emits intent; the controller owns shared language state.
        assert i18n.tr("设置") == "Settings"
    finally:
        dialog.close()
        app.processEvents()


@pytest.mark.parametrize(
    "value", ["{}", '{"language":"fr"}', '{"language":null}', '{"language":[]}']
)
def test_missing_and_invalid_language_settings_fall_back_to_system(tmp_path, value):
    path = tmp_path / "settings.json"
    path.write_text(value)
    assert load(path).language == "system"


def test_bilingual_views_keep_statistics_filters_and_exports_identical(tmp_path):
    app = QApplication.instance() or QApplication([])
    snapshot = demo_snapshot()
    base = snapshot.samples[-1]
    unknown = replace(
        base,
        response_id="unknown",
        started_at=None,
        context=replace(
            base.context,
            requested_tier=None,
            submitted_tier=None,
            submitted_tier_state="unspecified",
            actual_tier=None,
        ),
    )
    snapshot = replace(
        snapshot, samples=(*snapshot.samples, unknown), diagnostics=("logs_unavailable",)
    )
    results = []
    for language in ("zh_CN", "en"):
        i18n.set_language(language)
        panel, history = Panel(), HistoryWindow()
        try:
            panel.apply(snapshot)
            panel.set_range("7d")
            panel.render(snapshot.at)
            history.apply(list(snapshot.samples))
            history.filters["requested_tier"].setCurrentIndex(
                history.filters["requested_tier"].findData("")
            )
            results.append((panel.view.summary, [s.key for s in history.filtered]))
            path = tmp_path / f"{language}.csv"
            export_csv(path, history.filtered)
            if language == "en":
                assert panel.range_buttons["7d"].text() == "7 days"
                assert "Last 7 days" in panel.speed_value.toolTip()
                assert "Diagnostic logs unavailable" in panel.notice.text()
                assert "Insufficient" not in panel.speed_value.text()
                assert history.table.horizontalHeaderItem(0).text() == "Time"
                assert "Not confirmed by the server" in dict(panel.detail_values())["Actual tier"]
                assert "Insufficient data" in history.summary.text()
                assert mode_info(unknown)[0] == "other"
                history.apply([unknown])
                assert "Unspecified" in history.table.item(0, 5).text()
                assert "Unconfirmed" == history.table.item(0, 7).text()
                valid = next(s for s in snapshot.samples if s.tps is not None)
                assert "total output tokens" in sample_text(valid)
                panel.show_sample(valid)
                assert panel.sample_window.windowTitle() == "Single output · TokenPulse"
                empty = Panel()
                empty.apply(Snapshot(diagnostics=("no_sessions",)))
                assert empty.speed_value.text() == "—"
                assert empty.group_box.currentText() == "No model found yet"
                empty.close()
        finally:
            panel.close()
            history.close()
            app.processEvents()
    assert results[0] == results[1]
    assert (tmp_path / "zh_CN.csv").read_bytes() == (tmp_path / "en.csv").read_bytes()


def test_cli_language_override_and_saved_language(tmp_path):
    save(tmp_path / "settings.json", Preferences(language="en"))
    command = [sys.executable, "-m", "token_pulse", "--data-dir", str(tmp_path)]
    english = subprocess.run(
        command + ["--help"],
        capture_output=True,
        encoding="utf-8",
        timeout=15,
        env=os.environ | {"PYTHONIOENCODING": "utf-8"},
    )
    chinese = subprocess.run(
        command + ["--language", "zh_CN", "--help"],
        capture_output=True,
        encoding="utf-8",
        timeout=15,
        env=os.environ | {"PYTHONIOENCODING": "utf-8"},
    )
    assert english.returncode == chinese.returncode == 0
    assert "read-only Codex tray monitor" in english.stdout
    assert "本机 Codex 只读托盘监视器" in chinese.stdout
    assert load(tmp_path / "settings.json").language == "en"


@pytest.mark.parametrize("outcome", ["save", "cancel", "failure"])
def test_language_preview_preserves_views_and_commits_only_on_save(tmp_path, monkeypatch, outcome):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QSystemTrayIcon

    from token_pulse.app import Controller

    class Monitor:
        def __init__(self, *_):
            self.polls = 0

        def start(self):
            pass

        def stop(self):
            pass

        def latest(self):
            self.polls += 1
            return None

    class PreviewSettings(Settings):
        def exec(self):
            self.language.setCurrentIndex(self.language.findData("en"))
            assert self.windowTitle() == "Settings · TokenPulse"
            assert self.heading.text() == "Appearance & monitoring"
            assert self.appearance.itemText(0) == "Light"
            assert self.buttons.button(self.buttons.StandardButton.Save).text() == "Save"
            assert controller.panel.windowTitle() == "TokenPulse"
            assert controller.panel.settings_button.text() == "Settings"
            assert controller.panel.range_buttons["7d"].text() == "7 days"
            assert controller.panel.chart.accessibleName() == "Output speed over time"
            assert "total output tokens" in controller.panel.chart.accessibleDescription()
            assert controller.history.heading.text() == "Output history"
            assert controller.history.filters["requested_tier"].currentText() == "Unknown"
            assert controller.history.table.horizontalHeaderItem(0).text() == "Time"
            assert controller.panel.sample_window is sample_window
            assert sample_window.windowTitle() == "Single output · TokenPulse"
            assert sample_window.isVisible()
            assert [action.text() for _, action in controller.tray_actions] == [
                "Open TokenPulse",
                "History",
                "Settings",
                "Quit TokenPulse",
            ]
            assert "Last 7 days" in controller.tray.toolTip()
            assert load(path).language == "zh_CN"
            assert controller.monitor.polls == polls
            return (
                QDialog.DialogCode.Rejected if outcome == "cancel" else QDialog.DialogCode.Accepted
            )

    monkeypatch.setattr("token_pulse.app.Monitor", Monitor)
    monkeypatch.setattr("token_pulse.app.Settings", PreviewSettings)
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    if outcome == "failure":

        def fail_save(*_):
            raise OSError("synthetic write failure")

        monkeypatch.setattr("token_pulse.app.save", fail_save)
        monkeypatch.setattr("token_pulse.app.QMessageBox.warning", lambda *_: None)
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "settings.json"
    save(path, Preferences(source=str(tmp_path), language="zh_CN"))
    controller = Controller(app, tmp_path)
    try:
        controller.timer.stop()
        snapshot = demo_snapshot()
        controller.panel.apply(snapshot)
        controller.panel.set_range("7d")
        controller.history.apply(list(snapshot.samples))
        controller.history.filters["requested_tier"].setCurrentIndex(
            controller.history.filters["requested_tier"].findData("")
        )
        controller.history.table.sortItems(10, Qt.SortOrder.DescendingOrder)
        controller.history.table.selectRow(1)
        row_key = tuple(controller.history.table.item(1, 0).data(Qt.ItemDataRole.UserRole))
        controller.panel.show_sample(next(s for s in snapshot.samples if s.tps is not None))
        sample_window = controller.panel.sample_window
        controller.panel.chart.active = 0
        controller.panel.chart.show_active()
        selected_key, summary = controller.panel.selected_key, controller.panel.view.summary
        polls = controller.monitor.polls
        controller.settings()
        assert controller.panel.selected_key == selected_key
        assert controller.panel.range_name == "7d"
        assert controller.panel.view.summary == summary
        assert controller.history.filters["requested_tier"].currentData() == ""
        assert controller.history.table.horizontalHeader().sortIndicatorSection() == 10
        assert (
            controller.history.table.horizontalHeader().sortIndicatorOrder()
            == Qt.SortOrder.DescendingOrder
        )
        assert {
            tuple(controller.history.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole))
            for index in controller.history.table.selectionModel().selectedRows()
        } == {row_key}
        expected = "en" if outcome == "save" else "zh_CN"
        assert load(path).language == expected
        assert controller.preferences.language == expected
        assert controller.language == expected
        if outcome != "save":
            assert controller.panel.settings_button.text() == "设置"
            assert controller.history.heading.text() == "输出历史"
            assert sample_window.windowTitle() == "单段输出 · 词脉"
            assert controller.tray_actions[0][1].text() == "打开词脉"
        else:
            controller.apply_language("zh_CN")
            assert controller.panel.settings_button.text() == "设置"
            assert controller.history.heading.text() == "输出历史"
            assert sample_window.windowTitle() == "单段输出 · 词脉"
    finally:
        controller.close()
    restarted = Controller(app, tmp_path)
    try:
        assert restarted.language == expected
    finally:
        restarted.close()


@pytest.mark.parametrize("reselect_system", [False, True])
def test_cancel_preview_restores_cli_override_and_demo_title(
    tmp_path, monkeypatch, reselect_system
):
    from PySide6.QtWidgets import QDialog

    from token_pulse.app import Controller

    class PreviewSettings(Settings):
        def exec(self):
            if reselect_system:
                self.language.activated.emit(self.language.currentIndex())
            else:
                self.language.setCurrentIndex(self.language.findData("zh_CN"))
            assert controller.panel.windowTitle() == "TokenPulse · 词脉（演示数据）"
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr("token_pulse.app.Settings", PreviewSettings)
    monkeypatch.setattr(i18n, "system_language", lambda: "zh_CN")
    app = QApplication.instance() or QApplication([])
    controller = Controller(app, tmp_path, demo=True, no_tray=True, language="en")
    try:
        controller.settings()
        assert controller.language == "en"
        assert controller.panel.windowTitle() == "TokenPulse (demo data)"
        assert controller.preferences.language == "system"
        assert not (tmp_path / "settings.json").exists()
    finally:
        controller.close()


@pytest.mark.parametrize("language", ["zh_CN", "en"])
def test_bilingual_application_smoke(language):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "token_pulse",
            "--demo",
            "--no-tray",
            "--smoke-test",
            "--language",
            language,
        ],
        capture_output=True,
        timeout=15,
        env=os.environ | {"QT_QPA_PLATFORM": "offscreen"},
    )
    assert result.returncode == 0, result.stderr
