import os
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from token_pulse.i18n import set_language
from token_pulse.preferences import Preferences
from token_pulse.ui.settings import Settings
from token_pulse.ui.theme import apply_theme


@pytest.mark.parametrize("width", [400, 540, 720])
@pytest.mark.parametrize("button_font_size", [13, 26])
def test_language_changes_keep_settings_buttons_fully_visible(width, button_font_size):
    app = QApplication.instance() or QApplication([])
    if not QFontDatabase.families() and os.name == "nt":
        # The Windows offscreen plugin does not populate the system font database.
        for name in ("segoeui.ttf", "segoeuib.ttf", "msyh.ttc"):
            path = Path(os.environ["WINDIR"]) / "Fonts" / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    apply_theme(app)
    dialog = Settings(Preferences(source="~/.codex", language="zh_CN"))
    dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dialog.buttons.setStyleSheet(f"QPushButton {{ font-size: {button_font_size}px; }}")
    dialog.resize(width, dialog.height())
    dialog.show()
    app.processEvents()
    try:
        for language in ("zh_CN", "en", "zh_CN", "en"):
            previous_width = dialog.width()
            set_language(language)
            dialog.retranslate_ui()
            app.processEvents()
            assert dialog.width() == max(previous_width, dialog.minimumWidth())
            for standard in (
                QDialogButtonBox.StandardButton.Save,
                QDialogButtonBox.StandardButton.Cancel,
            ):
                button = dialog.buttons.button(standard)
                assert button.height() >= button.sizeHint().height()
                assert button.visibleRegion().boundingRect().contains(button.rect())
                rect = QRect(button.mapTo(dialog, QPoint()), button.size())
                assert dialog.rect().contains(rect)
                assert rect.bottom() < dialog.height() - dialog.layout().contentsMargins().bottom()
    finally:
        dialog.close()
        dialog.deleteLater()
