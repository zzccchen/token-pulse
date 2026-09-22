from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from token_pulse.i18n import tr
from token_pulse.preferences import Preferences
from token_pulse.ui.glass import MaterialCheckBox, MaterialComboBox
from token_pulse.ui.panel import text_label


class Settings(QDialog):
    language_changed = Signal(str)

    def __init__(self, preferences: Preferences, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("设置 · 词脉"))
        self.resize(540, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        self.heading = text_label(tr("外观与观测"), "brand")
        layout.addWidget(self.heading)
        language_row = QHBoxLayout()
        self.language_label = text_label(tr("界面语言"))
        language_row.addWidget(self.language_label)
        language_row.addStretch()
        self.language = MaterialComboBox()
        self.language.setAccessibleName(tr("界面语言"))
        self.language.addItem(tr("跟随系统"), "system")
        self.language.addItem("简体中文", "zh_CN")
        self.language.addItem("English", "en")
        self.language.setCurrentIndex(max(0, self.language.findData(preferences.language)))
        language_row.addWidget(self.language)
        layout.addLayout(language_row)
        self.language_note = text_label(tr("选择语言后立即预览；保存以保留，取消则恢复。"), "muted")
        self.language_note.setWordWrap(True)
        layout.addWidget(self.language_note)
        appearance = QHBoxLayout()
        self.appearance_label = text_label(tr("界面外观"))
        appearance.addWidget(self.appearance_label)
        appearance.addStretch()
        self.appearance = MaterialComboBox()
        self.appearance.setAccessibleName(tr("界面外观"))
        self.appearance.addItem(tr("浅色"), "light")
        self.appearance.addItem(tr("深色"), "dark")
        self.appearance.setCurrentIndex(self.appearance.findData(preferences.appearance))
        appearance.addWidget(self.appearance)
        layout.addLayout(appearance)
        self.reduce_transparency = MaterialCheckBox(tr("减少透明效果，提高表面与边界的清晰度"))
        self.reduce_transparency.setChecked(preferences.reduce_transparency)
        layout.addWidget(self.reduce_transparency)
        self.reduce_motion = MaterialCheckBox(tr("减少动态效果，直接显示交互结果"))
        self.reduce_motion.setChecked(preferences.reduce_motion)
        layout.addWidget(self.reduce_motion)
        self.source_label = text_label(tr("Codex 数据目录"))
        layout.addWidget(self.source_label)
        row = QHBoxLayout()
        self.source = QLineEdit(preferences.source)
        self.source.setAccessibleName(tr("Codex 数据目录"))
        row.addWidget(self.source, 1)
        browse = QPushButton(tr("选择目录"))
        self.browse_button = browse
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        layout.addLayout(row)
        self.number = MaterialCheckBox(tr("托盘显示平均 TPS"))
        self.number.setToolTip(tr("显示当前模型和时间范围内的平均速度，有有效数据时持续显示"))
        self.number.setChecked(preferences.tray_number)
        layout.addWidget(self.number)
        self.notifications = MaterialCheckBox(tr("明确的请求异常发生时通知一次"))
        self.notifications.setChecked(preferences.notifications)
        layout.addWidget(self.notifications)
        info = text_label(
            tr("仅修改词脉的设置，不修改 Codex 配置。无系统托盘时使用普通窗口。"), "muted"
        )
        self.info = info
        info.setWordWrap(True)
        layout.addWidget(info)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons = buttons
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("保存"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("取消"))
        layout.addWidget(buttons)
        self._previewed_language: str | None = None
        self.language.currentIndexChanged.connect(self.preview_language)
        # Reselecting the saved option can override a different --language value.
        self.language.activated.connect(self.preview_language)
        self._fit_contents()

    def _fit_contents(self) -> None:
        self.ensurePolished()
        # Wrapped labels can squeeze the button box below its children's height
        # during Qt's layout pass. Reserve the styled button height explicitly.
        self.buttons.setMinimumHeight(self.buttons.sizeHint().height())
        layout = self.layout()
        layout.invalidate()
        height = max(layout.minimumSize().height(), layout.totalHeightForWidth(self.width()))
        # Keep the chosen width and any extra space the user has allocated.
        self.resize(self.width(), max(self.height(), height))
        layout.activate()

    def preview_language(self, _index: int) -> None:
        language = self.language.currentData()
        if language != self._previewed_language:
            self._previewed_language = language
            self.language_changed.emit(language)

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("设置 · 词脉"))
        self.heading.setText(tr("外观与观测"))
        self.language_label.setText(tr("界面语言"))
        self.language.setAccessibleName(tr("界面语言"))
        self.language.setItemText(0, tr("跟随系统"))
        self.language_note.setText(tr("选择语言后立即预览；保存以保留，取消则恢复。"))
        self.appearance_label.setText(tr("界面外观"))
        self.appearance.setAccessibleName(tr("界面外观"))
        self.appearance.setItemText(0, tr("浅色"))
        self.appearance.setItemText(1, tr("深色"))
        self.reduce_transparency.setText(tr("减少透明效果，提高表面与边界的清晰度"))
        self.reduce_motion.setText(tr("减少动态效果，直接显示交互结果"))
        self.source_label.setText(tr("Codex 数据目录"))
        self.source.setAccessibleName(tr("Codex 数据目录"))
        self.browse_button.setText(tr("选择目录"))
        self.number.setText(tr("托盘显示平均 TPS"))
        self.number.setToolTip(tr("显示当前模型和时间范围内的平均速度，有有效数据时持续显示"))
        self.notifications.setText(tr("明确的请求异常发生时通知一次"))
        self.info.setText(tr("仅修改词脉的设置，不修改 Codex 配置。无系统托盘时使用普通窗口。"))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("保存"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("取消"))
        self._fit_contents()

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, tr("选择 Codex 数据目录"), self.source.text())
        if path:
            self.source.setText(path)

    def validate(self):
        if not Path(self.source.text()).expanduser().is_dir():
            QMessageBox.warning(self, tr("目录不可用"), tr("请选择一个存在的 Codex 数据目录。"))
            return
        self.accept()

    def preferences(self) -> Preferences:
        return Preferences(
            str(Path(self.source.text()).expanduser().resolve()),
            self.number.isChecked(),
            self.notifications.isChecked(),
            self.appearance.currentData(),
            self.reduce_transparency.isChecked(),
            self.reduce_motion.isChecked(),
            self.language.currentData(),
        )
