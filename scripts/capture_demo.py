"""Render synthetic UI evidence; never opens a real Codex source."""

import argparse
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from token_pulse.collector import Snapshot
from token_pulse.demo import demo_snapshot
from token_pulse.i18n import LANGUAGES, set_language, tr
from token_pulse.preferences import Preferences
from token_pulse.ui.history_window import HistoryWindow
from token_pulse.ui.panel import Panel
from token_pulse.ui.settings import Settings
from token_pulse.ui.theme import apply_theme


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/ui"))
    parser.add_argument("--appearance", choices=["light", "dark"], default="light")
    parser.add_argument("--reduce-transparency", action="store_true")
    parser.add_argument("--reduce-motion", action="store_true")
    parser.add_argument("--width", type=int, default=460)
    parser.add_argument("--language", choices=LANGUAGES, default="zh_CN")
    parser.add_argument("--range", choices=["30m", "1d", "7d"], default="30m")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    set_language(args.language)
    if not QFontDatabase.families() and os.name == "nt":
        # The offscreen Windows plugin has no system font database by default.
        for name in ["segoeui.ttf", "segoeuib.ttf", "msyh.ttc", "consola.ttf"]:
            path = Path(os.environ["WINDIR"]) / "Fonts" / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    apply_theme(app, args.appearance, args.reduce_transparency, args.reduce_motion)
    snapshot = demo_snapshot()
    panel = Panel()
    panel.resize(args.width, panel.height())
    panel.setWindowTitle(tr("TokenPulse · 词脉（演示数据）"))
    panel.apply(snapshot)
    panel.set_range(args.range)
    history = HistoryWindow()
    history.apply(list(snapshot.samples))
    settings = Settings(
        Preferences(
            "~/.codex",
            appearance=args.appearance,
            reduce_transparency=args.reduce_transparency,
            reduce_motion=args.reduce_motion,
        )
    )
    empty = Panel()
    empty.apply(Snapshot(diagnostics=("no_sessions",)))
    for name, widget in [
        ("panel", panel),
        ("history", history),
        ("settings", settings),
        ("empty", empty),
    ]:
        widget.show()
        app.processEvents()
        QTest.qWait(180)
        if not widget.grab().save(str(args.output / f"{name}.png")):
            raise OSError("Could not save preview")
        widget.hide()


if __name__ == "__main__":
    main()
