from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox, QSystemTrayIcon

from token_pulse.collector import Snapshot, codex_home
from token_pulse.demo import demo_snapshot
from token_pulse.i18n import set_language, tr
from token_pulse.preferences import Preferences, load, save
from token_pulse.service import Monitor
from token_pulse.ui.history_window import HistoryWindow
from token_pulse.ui.panel import Panel
from token_pulse.ui.settings import Settings
from token_pulse.ui.theme import AMBER, BLUE, MUTED, STYLE, TEAL, apply_theme, pulse_icon


class Controller:
    def __init__(
        self,
        app: QApplication,
        data_dir: Path,
        *,
        source: Path | None = None,
        demo: bool = False,
        no_tray: bool = False,
        language: str | None = None,
    ):
        self.app, self.data_dir, self.demo, self.no_tray = app, data_dir, demo, no_tray
        self.preferences = Preferences() if demo else load(data_dir / "settings.json")
        self.language = set_language(language or self.preferences.language)
        root = source or (
            Path(self.preferences.source) if self.preferences.source else codex_home()
        )
        self.preferences = replace(self.preferences, source=str(root))
        apply_theme(
            app,
            self.preferences.appearance,
            self.preferences.reduce_transparency,
            self.preferences.reduce_motion,
        )
        self.panel = Panel()
        self.panel.setWindowIcon(pulse_icon())
        self.history = HistoryWindow()
        self.history.setWindowIcon(pulse_icon())
        self.panel.history_requested.connect(self.show_history)
        self.panel.settings_requested.connect(self.settings)
        self.history_samples = []
        self.notice_state: dict[str, str | None] = {}
        self.started_at = time.time()
        self.received_first = False
        self.monitor: Monitor | None = None
        self.tray = None
        self.tray_available = False
        self.icon_state = None
        self.tray_actions = []
        if not no_tray:
            self.tray = QSystemTrayIcon(pulse_icon(), self.panel)
            self.menu = QMenu()
            for label, callback in [
                ("打开词脉", self.show),
                ("查看历史", self.show_history),
                ("设置", self.settings),
            ]:
                self.tray_actions.append((label, self.menu.addAction(tr(label), callback)))
            self.menu.addSeparator()
            self.tray_actions.append(
                ("退出词脉", self.menu.addAction(tr("退出词脉"), self.app.quit))
            )
            self.tray.setContextMenu(self.menu)
            self.tray.activated.connect(self.activate)
            if QSystemTrayIcon.isSystemTrayAvailable():
                self.tray.show()
        if demo:
            snapshot = demo_snapshot()
            self.panel.setWindowTitle(tr("TokenPulse · 词脉（演示数据）"))
            self.panel.apply(snapshot)
            self.history_samples = list(snapshot.samples)
        else:
            self.monitor = Monitor(root, data_dir)
            self.monitor.start()
        self.timer = QTimer(self.panel)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.tick()

    def show(self):
        self.panel.showNormal()
        self.panel.raise_()
        self.panel.activateWindow()

    def show_history(self):
        self.history.apply(self.history_samples)
        self.history.showNormal()
        self.history.raise_()
        self.history.activateWindow()

    def activate(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show()

    def settings(self):
        previous_language = self.language
        dialog = Settings(self.preferences, self.panel)

        def preview_language(language):
            self.apply_language(language)
            dialog.retranslate_ui()

        dialog.language_changed.connect(preview_language)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self.apply_language(previous_language)
                return
            preferences = dialog.preferences()
        finally:
            dialog.deleteLater()
        try:
            if not self.demo:
                save(self.data_dir / "settings.json", preferences)
        except OSError:
            self.apply_language(previous_language)
            QMessageBox.warning(
                self.panel, tr("设置未保存"), tr("无法写入词脉数据目录，请检查访问权限。")
            )
            return
        if preferences.source != self.preferences.source and self.monitor:
            self.monitor.stop()
            if self.monitor.thread.is_alive():
                QMessageBox.warning(
                    self.panel, tr("采集仍在退出"), tr("请稍后重启词脉以应用新的数据目录。")
                )
                return
            self.panel.selected_key = None
            self.panel.apply(Snapshot())
            self.monitor = Monitor(Path(preferences.source), self.data_dir)
            self.monitor.start()
        self.preferences = preferences
        apply_theme(
            self.app,
            preferences.appearance,
            preferences.reduce_transparency,
            preferences.reduce_motion,
        )

    def apply_language(self, language: str) -> None:
        resolved = set_language(language)
        if resolved == self.language:
            return
        self.language = resolved
        self.panel.retranslate_ui()
        if self.demo:
            self.panel.setWindowTitle(tr("TokenPulse · 词脉（演示数据）"))
        self.history.retranslate_ui()
        for label, action in self.tray_actions:
            action.setText(tr(label))
        self.refresh_tray(time.time())

    def tick(self):
        now = time.time()
        if self.monitor:
            update = self.monitor.latest()
            if update:
                self.panel.apply(update.snapshot, list(update.history))
                history_changed = tuple(self.history_samples) != update.history
                self.history_samples = list(update.history)
                if history_changed and self.history.isVisible():
                    self.history.apply(self.history_samples)
                for task in update.snapshot.tasks:
                    previous = self.notice_state.get(task.id)
                    if (
                        task.notice
                        and task.notice != previous
                        and self.received_first
                        and task.updated_at >= self.started_at
                        and self.preferences.notifications
                        and self.tray_available
                    ):
                        message = (
                            tr("请求的服务层级被客户端忽略。")
                            if task.notice == "tier_ignored"
                            else tr("请求失败或中断，请在 Codex 中查看详情。")
                        )
                        self.tray.showMessage(tr("词脉 · 请求异常"), message)
                    self.notice_state[task.id] = task.notice
                self.received_first = True
        self.panel.render(now)

        self.refresh_tray(now)

    def refresh_tray(self, now: float) -> None:
        available = not self.no_tray and QSystemTrayIcon.isSystemTrayAvailable()
        if self.tray_available and not available and not self.panel.isVisible():
            self.show()
        self.tray_available = available
        if self.tray and available and not self.tray.isVisible():
            self.tray.show()
        self.panel.hide_on_close = available
        self.app.setQuitOnLastWindowClosed(not available)
        if self.tray:
            states = {t.display_state(now) for t in self.panel.recent_tasks(now)}
            color = (
                AMBER
                if "error" in states
                else (
                    TEAL
                    if "generating" in states
                    else BLUE
                    if states & {"waiting", "tool"}
                    else MUTED
                )
            )
            view = self.panel.view
            rate = view.summary.weighted_tps if view else None
            latest = (
                next(
                    (sample for sample in reversed(view.samples) if sample.exclusion is None), None
                )
                if view
                else None
            )
            number = None
            if self.preferences.tray_number and rate is not None:
                number = f"{rate:.0f}" if rate < 1000 else f"{rate / 1000:.0f}k"
            icon_state = (color, number)
            if self.icon_state != icon_state:
                self.tray.setIcon(pulse_icon(color, number))
                self.icon_state = icon_state
            speed = (
                tr(
                    "近 {range_name} 平均 {rate:.1f} TPS",
                    range_name=self.panel.range_label,
                    rate=rate,
                )
                if rate is not None
                else tr("TPS 数据不足")
            )
            if latest is not None:
                stamp = datetime.fromtimestamp(latest.ended_at).strftime("%m-%d %H:%M:%S")
                speed += tr(" · 最近采样 {stamp}", stamp=stamp)
            model = self.panel.selected_key[0].model if self.panel.selected_key else None
            self.tray.setToolTip(
                f"{model or tr('TokenPulse 词脉')} · {self.panel.status.text()} · {speed}"
            )

    def close(self):
        self.timer.stop()
        if self.tray:
            self.tray.hide()
        if self.monitor:
            self.monitor.stop()
        self.history.hide()
        if self.panel.sample_window is not None:
            self.panel.sample_window.close()
        self.panel.hide()


def run(
    data_dir: Path,
    *,
    source: Path | None = None,
    demo: bool = False,
    no_tray: bool = False,
    hidden: bool = False,
    smoke_test: bool = False,
    language: str | None = None,
) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("TokenPulse")
    app.setOrganizationName("TokenPulse")
    app.setStyleSheet(STYLE)
    set_language(language or ("system" if demo else load(data_dir / "settings.json").language))
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        QMessageBox.critical(
            None, tr("无法启动词脉"), tr("数据目录不可写，请通过 --data-dir 指定可写目录。")
        )
        return 1
    lock = QLockFile(str(data_dir / "instance.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None, tr("词脉已运行"), tr("请从系统托盘打开现有窗口。"))
        return 1
    controller = Controller(
        app, data_dir, source=source, demo=demo, no_tray=no_tray, language=language
    )
    if not hidden or not controller.tray_available:
        controller.show()
    if smoke_test:
        QTimer.singleShot(300, app.quit)
    try:
        return app.exec()
    finally:
        controller.close()
        lock.unlock()
