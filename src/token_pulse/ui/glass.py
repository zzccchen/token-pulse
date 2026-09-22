"""App-local material rendering. No desktop capture; interaction animations stop when idle."""

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QRegion,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QListView,
    QProxyStyle,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QWidget,
)

from token_pulse.ui.theme import colors, reduced_motion, reduced_transparency


class MaterialCheckBox(QCheckBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = QRectF(
            self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
        ).adjusted(0.5, 0.5, -0.5, -0.5)
        c = colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(c["accent"] if self.isChecked() else c["line"]), 1.2))
        painter.setBrush(QColor(c["accent"] if self.isChecked() else c["control"]))
        painter.drawRoundedRect(rect, 3, 3)
        if self.isChecked():
            painter.setPen(
                QPen(QColor(c["base"]), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            )
            x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
            painter.drawLine(
                QPointF(x + w * 0.22, y + h * 0.5), QPointF(x + w * 0.43, y + h * 0.72)
            )
            painter.drawLine(
                QPointF(x + w * 0.43, y + h * 0.72), QPointF(x + w * 0.79, y + h * 0.27)
            )
        painter.end()


class MaterialComboStyle(QProxyStyle):
    def __init__(self, parent):
        super().__init__("Fusion")
        self.setParent(parent)

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_ComboBox_Popup:
            return 0
        return super().styleHint(hint, option, widget, returnData)


class MaterialChoiceDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width() + 42, max(34, option.fontMetrics.height() + 16))

    def paint(self, painter, option, index):
        c = colors()
        current = index.row() == self.parent().currentIndex()
        highlighted = bool(
            option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver)
        )
        rect = QRectF(option.rect).adjusted(1, 1, -1, -1)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if highlighted:
            painter.setBrush(QColor(c["selection"]))
            painter.drawRoundedRect(rect, 9, 9)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        painter.setPen(
            QColor(c["accent"] if current and enabled else c["ink"] if enabled else c["muted"])
        )
        painter.setFont(option.font)
        text_rect = rect.adjusted(12, 0, -30, 0)
        text = option.fontMetrics.elidedText(
            str(index.data() or ""), Qt.TextElideMode.ElideRight, int(text_rect.width())
        )
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, text)
        if current:
            x, y = rect.right() - 17, rect.center().y()
            painter.setPen(
                QPen(QColor(c["accent"]), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            )
            painter.drawLine(QPointF(x - 4, y), QPointF(x - 1, y + 3))
            painter.drawLine(QPointF(x - 1, y + 3), QPointF(x + 5, y - 3))
        painter.restore()


class MaterialComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup = None
        self.setStyle(MaterialComboStyle(self))
        view = QListView()
        view.setObjectName("materialChoices")
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.setMouseTracking(True)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setView(view)
        self.setItemDelegate(MaterialChoiceDelegate(self))
        self.setMaxVisibleItems(8)

    def eventFilter(self, source, event):
        if source is self._popup and event.type() == QEvent.Type.Show:
            # Native combo fade effects can defer showing the actual container.
            QTimer.singleShot(0, self._position_popup)
        if source is self._popup and event.type() == QEvent.Type.Paint:
            painter = QPainter(source)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(colors()["line"]), 0.8))
            painter.setBrush(QColor(colors()["content"]))
            painter.drawRoundedRect(QRectF(source.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 13, 13)
            painter.end()
            return True
        return super().eventFilter(source, event)

    def showPopup(self):
        view = self.view()
        popup = view.window()
        c = colors()
        self._popup = popup
        popup.installEventFilter(self)
        popup.setObjectName("materialComboPopup")
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        popup.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        if isinstance(popup, QFrame):
            popup.setFrameShape(QFrame.Shape.NoFrame)
        popup.setStyleSheet(
            "QFrame#materialComboPopup { background: transparent; border: none; }"
            "QListView#materialChoices { background: transparent; border: none; outline: none; }"
            "QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }"
            f"QScrollBar::handle:vertical {{ background: {c['line']}; "
            "min-height: 24px; border-radius: 3px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical "
            "{ background: transparent; }"
        )
        popup.layout().setContentsMargins(6, 6, 6, 6)
        super().showPopup()
        self._position_popup()

    def _position_popup(self):
        popup = self._popup
        if popup is None or not popup.isVisible():
            return
        view = self.view()
        screen = self.screen().availableGeometry()
        row_height = max(34, view.sizeHintForRow(0))
        height = min(
            row_height * min(self.count(), self.maxVisibleItems()) + 12, screen.height() - 16
        )
        width = min(self.width(), screen.width() - 16)
        anchor = self.mapToGlobal(QPoint(0, self.height() + 6))
        top = anchor.y()
        if top + height > screen.bottom() - 8:
            top = self.mapToGlobal(QPoint()).y() - height - 6
        top = max(screen.top() + 8, top)
        left = max(screen.left() + 8, min(anchor.x(), screen.right() - width - 8))
        popup.setGeometry(left, top, width, height)
        path = QPainterPath()
        path.addRoundedRect(QRectF(popup.rect()), 13, 13)
        popup.setMask(QRegion(path.toFillPolygon().toPolygon()))
        view.scrollTo(view.currentIndex())

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(
            QPen(QColor(colors()["muted"]), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        x, y = self.width() - 19, self.height() / 2
        painter.drawLine(QPointF(x - 3, y - 1), QPointF(x, y + 2))
        painter.drawLine(QPointF(x, y + 2), QPointF(x + 3, y - 1))
        painter.end()


class Backdrop(QWidget):
    def paintEvent(self, event):
        c = colors()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(c["base"]))
        if not reduced_transparency():
            for x, y, key, radius in [(0.05, 0.15, "glow1", 0.85), (0.95, 0.9, "glow2", 0.8)]:
                glow = QRadialGradient(
                    QPointF(self.width() * x, self.height() * y),
                    max(self.width(), self.height()) * radius,
                )
                glow.setColorAt(0, QColor(c[key]))
                transparent = QColor(c[key])
                transparent.setAlpha(0)
                glow.setColorAt(1, transparent)
                painter.fillRect(self.rect(), glow)
        painter.end()


class GlassBar(QWidget):
    """A single shared optical layer for a group of related controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.controls: list[QWidget] = []
        self.hovered: QWidget | None = None
        self.pressed = False
        self.lens_rect = QRectF()
        self.lens_strength = 0.0
        self._start_rect = self._end_rect = QRectF()
        self._start_strength = self._end_strength = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(160)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._advance)

    def register_controls(self, *controls: QWidget) -> None:
        """Only actual controls opt into pointer/focus reactions."""
        for control in controls:
            if not isinstance(control, (QAbstractButton, QComboBox)) or not self.isAncestorOf(
                control
            ):
                raise ValueError("Glass interactions require a contained button or combo box")
            if control in self.controls:
                continue
            self.controls.append(control)
            control.installEventFilter(self)
            if isinstance(control, QAbstractButton):
                control.toggled.connect(self._refresh)

    def _refresh(self) -> None:
        focus = QApplication.focusWidget()
        target = self.hovered or (focus if focus in self.controls else None)
        if target is None:
            target = next(
                (c for c in self.controls if isinstance(c, QAbstractButton) and c.isChecked()), None
            )
        self._retarget(target if target and target.isEnabled() and target.isVisible() else None)

    def _retarget(self, control: QWidget | None) -> None:
        rect = self.lens_rect
        if control:
            point = control.mapTo(self, QPoint())
            rect = QRectF(point.x(), point.y(), control.width(), control.height()).adjusted(
                1, 1, -1, -1
            )
        strength = 1.0 if control else 0.0
        if rect == self._end_rect and strength == self._end_strength:
            self.update()
            return
        self.animation.stop()
        self._start_rect = QRectF(self.lens_rect if self.lens_strength else rect)
        self._end_rect = rect
        self._start_strength, self._end_strength = self.lens_strength, strength
        if reduced_motion() or not self.isVisible():
            self._advance(1.0)
        else:
            self.animation.start()

    def _advance(self, progress: float) -> None:
        a, b = self._start_rect, self._end_rect
        self.lens_rect = QRectF(
            a.x() + (b.x() - a.x()) * progress,
            a.y() + (b.y() - a.y()) * progress,
            a.width() + (b.width() - a.width()) * progress,
            a.height() + (b.height() - a.height()) * progress,
        )
        self.lens_strength = (
            self._start_strength + (self._end_strength - self._start_strength) * progress
        )
        self.update()

    def eventFilter(self, source, event):
        kind = event.type()
        if source in self.controls:
            if kind == QEvent.Type.Enter:
                self.hovered = source
            elif kind in (QEvent.Type.Leave, QEvent.Type.Hide):
                if self.hovered is source:
                    self.hovered = None
                self.pressed = False
            elif kind == QEvent.Type.MouseButtonPress:
                self.pressed = True
            elif kind in (QEvent.Type.MouseButtonRelease, QEvent.Type.FocusOut):
                self.pressed = False
            elif kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self.pressed = kind == QEvent.Type.KeyPress
            if kind in (
                QEvent.Type.Enter,
                QEvent.Type.Leave,
                QEvent.Type.Hide,
                QEvent.Type.FocusIn,
                QEvent.Type.FocusOut,
                QEvent.Type.Resize,
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
                QEvent.Type.KeyPress,
                QEvent.Type.KeyRelease,
            ):
                self._refresh()
        return super().eventFilter(source, event)

    def changeEvent(self, event):
        if hasattr(self, "animation") and reduced_motion():
            self.animation.stop()
            self._advance(1.0)
        super().changeEvent(event)

    def hideEvent(self, event):
        self.animation.stop()
        self.hovered = None
        self.pressed = False
        self.lens_strength = self._end_strength = 0.0
        super().hideEvent(event)

    def _paint_lens(self, painter, rect, radius):
        if not self.lens_strength:
            return
        c = colors()
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        painter.setClipPath(clip)
        lens = self.lens_rect.adjusted(0.8, 0.8, -0.8, -0.8) if self.pressed else self.lens_rect
        tint = QColor(c["selection"])
        tint.setAlpha(round((245 if self.pressed else 190) * self.lens_strength))
        painter.setBrush(tint)
        rim = QColor(c["accent"])
        rim.setAlpha(round(85 * self.lens_strength))
        painter.setPen(QPen(rim, 1))
        painter.drawRoundedRect(lens, 17, 17)
        painter.restore()

    def paintEvent(self, event):
        c = colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(5, 4, -5, -7)
        radius = min(24, rect.height() / 2)
        if reduced_transparency():
            painter.setBrush(QColor(c["control"]))
            painter.setPen(QPen(QColor(c["line"]), 1.2))
            painter.drawRoundedRect(rect, radius, radius)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            for spread in range(5, 0, -1):
                painter.setBrush(QColor(12, 26, 43, 4))
                painter.drawRoundedRect(
                    rect.adjusted(-spread, 1, spread, spread), radius + spread, radius + spread
                )
            tint = QLinearGradient(rect.topLeft(), rect.bottomRight())
            top = QColor(c["control"])
            top.setAlpha(215)
            bottom = QColor(c["control"])
            bottom.setAlpha(145)
            tint.setColorAt(0, top)
            tint.setColorAt(1, bottom)
            painter.setBrush(tint)
            painter.drawRoundedRect(rect, radius, radius)
            rim = QLinearGradient(rect.topLeft(), rect.bottomRight())
            rim.setColorAt(0, QColor(255, 255, 255, 210))
            rim.setColorAt(0.5, QColor(255, 255, 255, 70))
            rim.setColorAt(1, QColor(255, 255, 255, 140))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(rim, 1.2))
            painter.drawRoundedRect(rect.adjusted(0.6, 0.6, -0.6, -0.6), radius, radius)
        self._paint_lens(painter, rect, radius)
        painter.end()
