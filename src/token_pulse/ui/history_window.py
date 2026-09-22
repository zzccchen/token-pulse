from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from token_pulse.domain import Sample
from token_pulse.history import export_csv
from token_pulse.i18n import tr
from token_pulse.stats import grouped
from token_pulse.ui.glass import Backdrop, GlassBar, MaterialComboBox
from token_pulse.ui.panel import ISSUES, submission_label, text_label


class HistoryWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("输出历史 · TokenPulse 词脉"))
        self.resize(1120, 570)
        self.samples: list[Sample] = []
        self.filtered: list[Sample] = []
        self.scope: tuple[float, float] | None = None
        self.single_output = False
        root = Backdrop()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QHBoxLayout()
        self.heading = text_label(tr("输出历史"), "brand")
        title.addWidget(self.heading)
        title.addStretch()
        self.export_button = QPushButton(tr("导出当前筛选 CSV"))
        self.export_button.clicked.connect(self.export)
        title.addWidget(self.export_button)
        layout.addLayout(title)
        self.scope_label = text_label(
            tr("仅本机统计 · 保留 30 天 / 最多 10,000 段 · 任务标识已匿名化"), "muted"
        )
        layout.addWidget(self.scope_label)
        filter_bar = GlassBar()
        controls = QHBoxLayout(filter_bar)
        controls.setContentsMargins(12, 10, 12, 13)
        self.filters: dict[str, QComboBox] = {}
        for key, title in self.filter_labels().items():
            box = MaterialComboBox()
            box.setObjectName("glassSelector")
            box.addItem(tr("全部{title}", title=title), None)
            box.setAccessibleName(title)
            box.currentIndexChanged.connect(self.render)
            self.filters[key] = box
            controls.addWidget(box, 1)
        filter_bar.register_controls(*self.filters.values())
        layout.addWidget(filter_bar)
        self.summary = text_label(tr("还没有历史样本"), "muted")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 13)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.translate_headers()
        layout.addWidget(self.table, 1)

    @staticmethod
    def filter_labels() -> dict[str, str]:
        return {
            "model": tr("模型"),
            "effort": tr("推理强度"),
            "provider": tr("服务商"),
            "submitted_tier": tr("提交层级"),
            "requested_tier": tr("请求层级"),
            "actual_tier": tr("实际层级"),
        }

    def translate_headers(self) -> None:
        self.table.setHorizontalHeaderLabels(
            [
                tr("时间"),
                tr("任务"),
                tr("模型"),
                tr("强度"),
                tr("渠道"),
                tr("提交设置"),
                tr("请求层级"),
                tr("实际层级"),
                tr("输出 tokens"),
                tr("流秒数"),
                "TPS",
                tr("口径"),
                tr("说明"),
            ]
        )
        for column, width in enumerate([115, 75, 100, 50, 130, 110, 75, 75, 90, 65, 60, 65, 130]):
            header = self.table.horizontalHeaderItem(column)
            header.setToolTip(header.text())
            text_width = (
                self.table.horizontalHeader().fontMetrics().horizontalAdvance(header.text())
            )
            self.table.setColumnWidth(
                column, max(width, self.table.columnWidth(column), text_width + 32)
            )

    def set_scope(self, start: float, end: float, *, single: bool = False) -> None:
        self.scope = (start, end)
        self.single_output = single
        self.translate_scope()

    def translate_scope(self) -> None:
        if self.single_output:
            self.setWindowTitle(tr("单段输出 · 词脉"))
            self.heading.setText(tr("单段输出"))
            self.scope_label.setText(tr("所选散点对应的已完成输出 · 任务标识已匿名化"))
        elif self.scope is not None:
            stamp, end = (
                datetime.fromtimestamp(value).strftime("%m-%d %H:%M") for value in self.scope
            )
            self.setWindowTitle(tr("{stamp} 区间样本 · 词脉", stamp=stamp))
            self.heading.setText(tr("区间样本"))
            self.scope_label.setText(
                tr("{stamp} — {end} · 按完成时间归档 · 对话标识已匿名化", stamp=stamp, end=end)
            )
        else:
            self.setWindowTitle(tr("输出历史 · TokenPulse 词脉"))
            self.heading.setText(tr("输出历史"))
            self.scope_label.setText(
                tr("仅本机统计 · 保留 30 天 / 最多 10,000 段 · 任务标识已匿名化")
            )

    def retranslate_ui(self) -> None:
        self.translate_scope()
        self.export_button.setText(tr("导出当前筛选 CSV"))
        for key, title in self.filter_labels().items():
            box = self.filters[key]
            box.setAccessibleName(title)
            box.setItemText(0, tr("全部{title}", title=title))
            for index in range(1, box.count()):
                box.setItemText(index, box.itemData(index) or tr("未知"))
        selected = {
            tuple(self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole))
            for index in self.table.selectionModel().selectedRows()
        }
        vertical = self.table.verticalScrollBar().value()
        horizontal = self.table.horizontalScrollBar().value()
        self.translate_headers()
        self.render()
        self.table.clearSelection()
        for row in range(self.table.rowCount()):
            if tuple(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) in selected:
                self.table.selectionModel().select(
                    self.table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
        self.table.verticalScrollBar().setValue(vertical)
        self.table.horizontalScrollBar().setValue(horizontal)

    def apply(self, samples: list[Sample]) -> None:
        self.samples = samples
        for key, box in self.filters.items():
            selected = box.currentData()
            title = box.itemText(0)
            values = sorted({getattr(s.context, key) or "" for s in samples})
            box.blockSignals(True)
            box.clear()
            box.addItem(title, None)
            for value in values:
                box.addItem(value or tr("未知"), value)
            index = box.findData(selected)
            box.setCurrentIndex(max(0, index))
            box.blockSignals(False)
        self.render()

    def render(self, *_):
        selected = {key: box.currentData() for key, box in self.filters.items()}
        self.filtered = [
            s
            for s in self.samples
            if all(
                value is None or (getattr(s.context, key) or "") == value
                for key, value in selected.items()
            )
        ]
        groups = grouped(self.filtered)
        valid = sum(s.exclusion is None for s in self.filtered)
        text = tr(
            "{value} 段 · {valid} 段有效 · {value2} 段数据不足",
            value=len(self.filtered),
            valid=valid,
            value2=len(self.filtered) - valid,
        )
        if self.filtered and all(s.source == "synthetic-demo" for s in self.filtered):
            text = tr("演示数据 · ") + text
        if len(groups) == 1:
            summary = next(iter(groups.values()))
            if summary.count:
                text += tr(
                    " · 加权平均 {weighted_tps:.1f} TPS · 中位数 {median_tps:.1f} TPS",
                    weighted_tps=summary.weighted_tps,
                    median_tps=summary.median_tps,
                )
        elif len(groups) > 1:
            text += tr(" · 包含多个模型，选择一个模型查看平均速度")
        if not self.filtered:
            text += tr("。完成一次本机输出后查看，或调整筛选。")
        self.summary.setText(text)
        self.export_button.setEnabled(bool(self.filtered))
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.filtered))
        for row, sample in enumerate(self.filtered):
            ctx = sample.context
            channel = " · ".join(x for x in [ctx.provider, ctx.auth_mode] if x) or tr("未知")
            values = [
                datetime.fromtimestamp(sample.ended_at).strftime("%m-%d %H:%M:%S"),
                sample.task_id[:8],
                ctx.model or tr("未知"),
                ctx.effort or tr("未知"),
                channel,
                submission_label(ctx),
                ctx.requested_tier or tr("未知"),
                ctx.actual_tier or tr("未确认"),
                sample.output_tokens,
                round(sample.seconds, 3) if sample.seconds else None,
                round(sample.tps, 1) if sample.tps is not None else None,
                tr("总输出") if sample.token_scope == "total_output" else sample.token_scope,
                tr(ISSUES.get(sample.exclusion, "数据不足"))
                if sample.exclusion
                else (tr("演示样本") if sample.source == "synthetic-demo" else tr("日志估算")),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem()
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, sample.key)
                item.setData(Qt.ItemDataRole.DisplayRole, "—" if value is None else value)
                item.setToolTip("—" if value is None else str(value))
                if column == 12 and sample.timing_source:
                    boundary = {
                        "stream-log": tr("诊断日志起点"),
                        "item-event": tr("输出事件起点"),
                        "tool-item-notification": tr("仅有工具调用项通知，不是完整生成计时"),
                        "coincident-item-boundaries": tr(
                            "首项起止时间重合，日志通知未提供更早起点"
                        ),
                    }.get(sample.timing_source, sample.timing_source)
                    item.setToolTip(
                        tr(
                            "{value}\n{boundary} · 解析版本 {parser_revision}",
                            value=value,
                            boundary=boundary,
                            parser_revision=sample.parser_revision,
                        )
                    )
                if 8 <= column <= 10:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)

    def export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, tr("导出统计"), "token-pulse-history.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            export_csv(Path(path), self.filtered)
            self.summary.setText(
                tr("已导出 {value} 段统计，任务标识已匿名化。", value=len(self.filtered))
            )
        except (OSError, ValueError):
            QMessageBox.warning(self, tr("导出失败"), tr("无法写入所选文件，请检查路径与权限。"))
