"""Each point is one completed output; modes are evidence labels, not filters."""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from token_pulse.domain import Sample
from token_pulse.i18n import tr
from token_pulse.timeline import Timeline
from token_pulse.ui.theme import colors


def mode_info(sample: Sample) -> tuple[str, str]:
    ctx = sample.context
    if ctx.requested_tier is not None:
        tier, evidence = ctx.requested_tier, tr("请求值")
    elif ctx.submitted_tier_state == "cleared":
        return "standard", tr("普通（提交设置已清除覆盖）")
    elif ctx.submitted_tier_state == "set" and ctx.submitted_tier:
        tier, evidence = ctx.submitted_tier, tr("提交设置")
    elif ctx.submitted_tier_state == "ambiguous":
        return "other", tr("模式证据冲突")
    else:
        return "other", tr("模式未记录或未明确指定")
    if tier in {"priority", "fast"}:
        return "fast", tr("Fast（{evidence}）", evidence=evidence)
    if tier == "default":
        return "standard", tr("普通（{evidence}）", evidence=evidence)
    return "other", tr("其他：{tier}（{evidence}）", tier=tier, evidence=evidence)


def mode_caption(sample: Sample) -> str:
    mode = {"fast": "Fast", "standard": tr("普通"), "other": tr("其他")}[mode_info(sample)[0]]
    ctx = sample.context
    if ctx.requested_tier is not None:
        return mode
    if ctx.submitted_tier_state == "cleared" or (
        ctx.submitted_tier_state == "set" and ctx.submitted_tier
    ):
        return mode
    return tr("模式冲突") if ctx.submitted_tier_state == "ambiguous" else tr("模式未知")


def sample_text(sample: Sample) -> str:
    at = datetime.fromtimestamp(sample.ended_at).strftime("%m-%d %H:%M:%S")
    return tr(
        "{at} · {value}\n{tps:.1f} tokens/s\n{output_tokens:,} 总输出 tokens · {seconds:.2f} 秒",
        at=at,
        value=mode_caption(sample),
        tps=sample.tps,
        output_tokens=sample.output_tokens,
        seconds=sample.seconds,
    )


class TimelineChart(QWidget):
    sample_activated = Signal(object)
    DESCRIPTION = (
        "每点是一段已完成输出；左右方向键浏览样本，Enter 查看记录，Home/End 跳至首末样本。"
    )

    def __init__(self):
        super().__init__()
        self.data: Timeline | None = None
        self.points: tuple[Sample, ...] = ()
        self.rates: tuple[float, ...] = ()
        self.maximum = 10
        self.active: int | None = None
        self.card_visible = False
        self._positions: list[QPointF] | None = None
        self._xs: list[float] = []
        self.setMinimumHeight(190)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(tr("输出速度时间散点图"))
        self.setAccessibleDescription(tr(self.DESCRIPTION))

    def retranslate_ui(self) -> None:
        self.setAccessibleName(tr("输出速度时间散点图"))
        self.setAccessibleDescription(
            sample_text(self.points[self.active])
            if self.active is not None
            else tr(self.DESCRIPTION)
        )
        self.update()

    def set_data(self, data: Timeline) -> None:
        old = self.points[self.active] if self.active is not None else None
        same_range = self.data and self.data.end - self.data.start == data.end - data.start
        self.data = data
        self.points = tuple(s for s in data.samples if s.exclusion is None)
        self.rates = tuple(s.tps for s in self.points)
        self.maximum = max(10, math.ceil(max(self.rates, default=0) / 10) * 10)
        self._positions = None
        self.active = next(
            (i for i, s in enumerate(self.points) if same_range and old and s.key == old.key), None
        )
        if self.active is None:
            self.card_visible = False
            self.setAccessibleDescription(tr(self.DESCRIPTION))
        elif old != self.points[self.active]:
            self.show_active()
        self.update()

    def plot_rect(self) -> QRectF:
        return QRectF(34, 12, max(1, self.width() - 42), max(1, self.height() - 80))

    def point_position(self, index: int) -> QPointF:
        if self._positions is None:
            plot = self.plot_rect()
            self._positions = [
                QPointF(
                    plot.left()
                    + (s.ended_at - self.data.start)
                    / (self.data.end - self.data.start)
                    * plot.width(),
                    plot.bottom() - rate / self.maximum * plot.height(),
                )
                for s, rate in zip(self.points, self.rates, strict=True)
            ]
            self._xs = [point.x() for point in self._positions]
        return self._positions[index]

    def hit_test(self, point: QPointF) -> int | None:
        if not self.points or not self.plot_rect().adjusted(-9, -9, 9, 9).contains(point):
            return None
        self.point_position(0)  # Build coordinates once per data/size change.
        nearest, distance = None, 9.0**2
        first = bisect_left(self._xs, point.x() - 9)
        last = bisect_right(self._xs, point.x() + 9)
        for index in range(first, last):
            position = self.point_position(index)
            delta = (point.x() - position.x()) ** 2 + (point.y() - position.y()) ** 2
            # Ties select the last point painted. Keyboard navigation reaches overlaps.
            if delta <= distance:
                nearest, distance = index, delta
        return nearest

    def show_active(self) -> None:
        if self.active is not None:
            text = sample_text(self.points[self.active])
            self.setAccessibleDescription(text)
            self.card_visible = True
        else:
            self.card_visible = False
            self.setAccessibleDescription(tr(self.DESCRIPTION))
        self.update()

    def mouseMoveEvent(self, event):
        self.active = self.hit_test(event.position())
        self.show_active()

    def leaveEvent(self, event):
        self.active = None
        self.show_active()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.hit_test(event.position())
            if index is not None:
                self.active = index
                self.card_visible = False
                self.update()
                self.sample_activated.emit(self.points[index])
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        if self.points and key in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
        ):
            if key == Qt.Key.Key_Home or (self.active is None and key == Qt.Key.Key_Right):
                self.active = 0
            elif key == Qt.Key.Key_End or self.active is None:
                self.active = len(self.points) - 1
            else:
                self.active = max(
                    0,
                    min(len(self.points) - 1, self.active + (1 if key == Qt.Key.Key_Right else -1)),
                )
            self.show_active()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.active is not None:
            self.card_visible = False
            self.update()
            self.sample_activated.emit(self.points[self.active])
        elif key == Qt.Key.Key_Escape:
            self.active = None
            self.show_active()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):
        self._positions = None
        super().resizeEvent(event)

    def focusOutEvent(self, event):
        self.active = None
        self.show_active()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self.active = None
        self.show_active()
        super().hideEvent(event)

    def card_rect(self, position: QPointF) -> QRectF:
        width = 120
        if self.active is not None:
            sample = self.points[self.active]
            font = self.font()
            font.setPixelSize(10)
            font.setBold(False)
            metrics = QFontMetricsF(font)
            header = (
                metrics.horizontalAdvance(self.card_time(sample))
                + 8
                + metrics.horizontalAdvance(mode_caption(sample))
            )
            detail = metrics.horizontalAdvance(
                tr(
                    "{output_tokens:,} tokens · {seconds:.2f} 秒",
                    output_tokens=sample.output_tokens,
                    seconds=sample.seconds,
                )
            )
            unit = metrics.horizontalAdvance("tokens/s") + 6
            font.setPixelSize(18)
            font.setBold(True)
            rate = QFontMetricsF(font).horizontalAdvance(f"{sample.tps:.1f}") + unit
            width = math.ceil(max(header, detail, rate)) + 12
        width, height = min(width, self.width() - 16), 70
        left = max(8, min(position.x() - width / 2, self.width() - width - 8))
        top = position.y() - height - 14
        if top < 8:
            top = position.y() + 14
        top = max(8, min(top, self.height() - height - 8))
        return QRectF(left, top, width, height)

    def card_time(self, sample: Sample) -> str:
        long_range = self.data and self.data.end - self.data.start >= 86400
        return datetime.fromtimestamp(sample.ended_at).strftime(
            "%m-%d %H:%M:%S" if long_range else "%H:%M:%S"
        )

    def paint_card(self, painter: QPainter) -> None:
        sample = self.points[self.active]
        rect = self.card_rect(self.point_position(self.active))
        c = colors()
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 24))
        painter.drawRoundedRect(rect.translated(0, 2), 10, 10)
        painter.setPen(QPen(QColor(c["line"]), 0.8))
        painter.setBrush(QColor(c["content"]))
        painter.drawRoundedRect(rect, 10, 10)
        text_rect = rect.adjusted(6, 6, -6, -6)
        font = painter.font()
        font.setPixelSize(10)
        painter.setFont(font)
        painter.setPen(QColor(c["muted"]))
        at = self.card_time(sample)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft, at)
        mode = mode_info(sample)[0]
        color = c[{"fast": "accent", "standard": "chart_standard", "other": "muted"}[mode]]
        painter.setPen(QColor(color))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignRight, mode_caption(sample))
        unit_width = QFontMetricsF(font).horizontalAdvance("tokens/s") + 6
        rate = f"{sample.tps:.1f}"
        font.setPixelSize(18)
        font.setBold(True)
        painter.setFont(font)
        rate_width = QFontMetricsF(font).horizontalAdvance(rate)
        if rate_width > text_rect.width() - unit_width:
            font.setPixelSize(max(10, int(18 * (text_rect.width() - unit_width) / rate_width)))
            painter.setFont(font)
            rate_width = QFontMetricsF(font).horizontalAdvance(rate)
        painter.setPen(QColor(c["ink"]))
        baseline = QPointF(text_rect.left(), rect.top() + 40)
        painter.drawText(baseline, rate)
        font.setPixelSize(10)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(c["muted"]))
        painter.drawText(baseline + QPointF(rate_width + 6, 0), "tokens/s")
        detail = tr(
            "{output_tokens:,} tokens · {seconds:.2f} 秒",
            output_tokens=sample.output_tokens,
            seconds=sample.seconds,
        )
        detail = QFontMetricsF(font).elidedText(
            detail, Qt.TextElideMode.ElideRight, text_rect.width()
        )
        painter.drawText(QPointF(text_rect.left(), rect.top() + 60), detail)
        painter.restore()

    @staticmethod
    def marker(painter: QPainter, position: QPointF, mode: str, radius: float, color: QColor):
        painter.setPen(QPen(color, 1.2))
        painter.setBrush(color if mode != "other" else Qt.BrushStyle.NoBrush)
        x, y = position.x(), position.y()
        if mode == "fast":
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(x, y - radius),
                        QPointF(x + radius, y),
                        QPointF(x, y + radius),
                        QPointF(x - radius, y),
                    ]
                )
            )
        elif mode == "standard":
            painter.drawEllipse(position, radius, radius)
        else:
            painter.drawLine(QPointF(x - radius, y - radius), QPointF(x + radius, y + radius))
            painter.drawLine(QPointF(x - radius, y + radius), QPointF(x + radius, y - radius))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = colors()
        plot = self.plot_rect()
        font = painter.font()
        font.setPixelSize(10)
        painter.setFont(font)
        palette = {"fast": c["accent"], "standard": c["chart_standard"], "other": c["muted"]}
        for x, mode, label in [
            (34, "fast", "Fast"),
            (110, "standard", tr("普通")),
            (181, "other", tr("其他 / 未记录")),
        ]:
            legend_top = plot.bottom() + 48
            self.marker(painter, QPointF(x + 3, legend_top + 9), mode, 3, QColor(palette[mode]))
            painter.setPen(QColor(c["muted"]))
            painter.drawText(QRectF(x + 13, legend_top, 105, 18), Qt.AlignmentFlag.AlignLeft, label)
        for rate in (0, self.maximum / 2, self.maximum):
            y = plot.bottom() - rate / self.maximum * plot.height()
            painter.setPen(QPen(QColor(c["line"]), 0.7))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(c["muted"]))
            painter.drawText(QRectF(0, y - 8, 28, 16), Qt.AlignmentFlag.AlignRight, f"{rate:g}")
        radius = 3 if len(self.points) < 400 else 2
        for index, sample in enumerate(self.points):
            mode = mode_info(sample)[0]
            color = QColor(palette[mode])
            color.setAlpha(195)
            self.marker(painter, self.point_position(index), mode, radius, color)
        if self.active is not None:
            position = self.point_position(self.active)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(c["ink"]), 1.5))
            painter.drawEllipse(position, radius + 3, radius + 3)
        if self.data:
            painter.setPen(QColor(c["muted"]))
            long_range = self.data.end - self.data.start >= 86400
            for fraction in (0, 0.5, 1):
                stamp = self.data.start + fraction * (self.data.end - self.data.start)
                label = datetime.fromtimestamp(stamp).strftime(
                    "%m-%d\n%H:%M" if long_range else "%H:%M"
                )
                left = plot.left() + fraction * (plot.width() - 66)
                alignment = (
                    Qt.AlignmentFlag.AlignLeft
                    if fraction == 0
                    else Qt.AlignmentFlag.AlignRight
                    if fraction == 1
                    else Qt.AlignmentFlag.AlignHCenter
                )
                painter.drawText(QRectF(left, plot.bottom() + 7, 66, 33), alignment, label)
            if not self.points:
                message = (
                    tr("已发现输出记录，缺少可测速证据")
                    if self.data.samples
                    else tr("此范围没有输出记录")
                )
                painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, message)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(c["accent"]), 1, Qt.PenStyle.DotLine))
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))
        if self.card_visible and self.active is not None:
            self.paint_card(painter)
        painter.end()
