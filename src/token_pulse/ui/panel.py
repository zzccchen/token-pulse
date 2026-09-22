from __future__ import annotations

import time
from collections import Counter
from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
)

from token_pulse.collector import Snapshot
from token_pulse.domain import Context, Sample, Task
from token_pulse.i18n import tr
from token_pulse.timeline import RANGES, Bucket, aggregate, series, series_key, timeline
from token_pulse.ui.glass import Backdrop, GlassBar, MaterialComboBox
from token_pulse.ui.theme import colors, pulse_icon
from token_pulse.ui.timeline_chart import TimelineChart

STATES = {
    "unknown": "状态未知",
    "idle": "空闲",
    "waiting": "等待响应",
    "generating": "正在生成",
    "tool": "执行工具",
    "error": "请求异常",
    "stale": "状态待确认",
}
ISSUES = {
    "unconfirmed_initial_span": "首项起止时间重合，完整生成起点未确认",
    "unconfirmed_tool_start": "首项为工具调用，完整生成起点未确认",
    "unsupported_token_scope": "token 口径不支持合并测速",
    "missing_start": "缺少首个输出起点",
    "missing_stream_start": "首项仅有完成时刻，缺少生成起点",
    "incomplete_boundary": "输出边界不完整",
    "ambiguous_response": "无法唯一匹配输出",
    "invalid_duration": "时长无效",
    "invalid_tokens": "token 计数无效",
    "invalid_timestamp": "时间无效",
    "damaged_or_retried_response": "记录或输出边界不完整",
    "record_gap": "缺少或跳过了会话记录",
    "invalid_record": "事件结构或时间无法解析",
    "out_of_order": "事件时间顺序冲突",
    "request_error": "响应期间发生明确错误",
    "missing_item_id": "输出缺少关联标识",
    "conflicting_item": "同一输出有冲突的结束时间",
    "turn_mismatch": "用量与输出轮次不匹配",
    "too_many_items": "输出项目超过解析上限",
    "usage_counter_reset": "用量计数在输出期间重置",
    "usage_scope_mismatch": "累计增量与本次用量不一致",
    "missing_usage_baseline": "缺少前一次用量快照",
}
DIAGNOSTICS = {
    "no_sessions": "还没有找到任务。运行一次本机 Codex，或在设置中选择数据目录。",
    "logs_unavailable": "诊断日志暂不可用，仍会尝试从会话输出事件匹配测速边界。",
    "state_unavailable": "任务索引暂不可用，正在读取近三天会话。",
    "source_unavailable": "部分会话文件暂不可读，恢复后将自动继续。",
    "config_unavailable": "全局配置暂不可读，配置层级显示未知。",
    "records_skipped": "跳过了损坏或过大的记录，相关样本可能不完整。",
    "monitor_error": "采集暂时失败，正在重试。可检查数据目录和访问权限。",
    "history_unavailable": "历史暂不可写入，请检查词脉数据目录的访问权限。",
}

RANGE_LABELS = {"30m": "30 分钟", "1d": "1 天", "7d": "7 天"}


def text_label(text: str = "", name: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setObjectName(name)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def channel(task: Task) -> str:
    auth = {
        "Chatgpt": tr("ChatGPT 登录"),
        "ChatgptAuthTokens": tr("ChatGPT 登录"),
        "ApiKey": tr("API 密钥"),
    }
    parts = [task.context.provider, auth.get(task.context.auth_mode)]
    return " · ".join(x for x in parts if x) or tr("未知")


def group_title(key: tuple) -> str:
    return key[0].model or tr("模型未知")


def submission_label(ctx: Context) -> str:
    if ctx.submitted_tier_state == "set" and ctx.submitted_tier:
        return ctx.submitted_tier
    return {
        "cleared": tr("普通（已清除层级覆盖）"),
        "unspecified": tr("未指定（不推断继承值）"),
        "ambiguous": tr("存在冲突，未采用"),
    }.get(ctx.submitted_tier_state, tr("未记录"))


class Panel(QMainWindow):
    history_requested = Signal()
    settings_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("TokenPulse · 词脉"))
        self.resize(460, 660)
        self.setMinimumWidth(410)
        self.hide_on_close = False
        self.snapshot = Snapshot()
        self.samples: list[Sample] = []
        self.groups: dict[tuple, list[Sample]] = {}
        self.selected_key: tuple | None = None
        self.view = None
        self._view_cache = None
        self.sample_window = None
        root = Backdrop()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        self.brand_mark = QLabel()
        self.brand_mark.setToolTip(tr("本机只读观测；数据留在本地，不上传统计或日志"))
        self.mark_color = None
        heading.addWidget(self.brand_mark)
        self.brand_label = text_label(tr("TokenPulse  词脉"), "brand")
        heading.addWidget(self.brand_label)
        heading.addStretch()
        self.status = text_label(tr("等待观测"), "status")
        heading.addWidget(self.status)
        layout.addLayout(heading)
        group_bar = GlassBar()
        controls = QHBoxLayout(group_bar)
        controls.setContentsMargins(10, 9, 10, 12)
        self.group_box = MaterialComboBox()
        self.group_box.setObjectName("glassSelector")
        self.group_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.group_box.setMinimumContentsLength(10)
        self.group_box.setAccessibleName(tr("模型"))
        self.group_box.currentIndexChanged.connect(self._select)
        controls.addWidget(self.group_box, 1)
        group_bar.register_controls(self.group_box)
        layout.addWidget(group_bar)
        range_bar = GlassBar()
        ranges = QHBoxLayout(range_bar)
        ranges.setContentsMargins(10, 6, 10, 9)
        self.range_buttons = {}
        self.range_name = "30m"
        for name in RANGES:
            button = QPushButton(tr(RANGE_LABELS[name]))
            button.setCheckable(True)
            button.setChecked(name == self.range_name)
            button.setObjectName("glassAction")
            button.clicked.connect(lambda checked=False, value=name: self.set_range(value))
            ranges.addWidget(button, 1)
            self.range_buttons[name] = button
        range_bar.register_controls(*self.range_buttons.values())
        layout.addWidget(range_bar)
        self.recovery = text_label("", "muted")
        self.recovery.setWordWrap(True)
        self.recovery.hide()
        layout.addWidget(self.recovery)
        sheet = QFrame()
        sheet.setObjectName("sheet")
        metric_layout = QVBoxLayout(sheet)
        metric_layout.setContentsMargins(20, 18, 20, 16)
        metric_layout.setSpacing(8)
        metric_line = QHBoxLayout()
        self.speed_value = text_label("—", "metric")
        self.speed_unit = text_label("tokens/s", "muted")
        metric_line.addWidget(self.speed_value, 0, Qt.AlignmentFlag.AlignBottom)
        metric_line.addWidget(self.speed_unit, 0, Qt.AlignmentFlag.AlignBottom)
        metric_line.addStretch()
        metric_layout.addLayout(metric_line)
        self.summary = text_label(tr("中位数 — · 范围 —"), "muted")
        self.summary.setWordWrap(True)
        metric_layout.addWidget(self.summary)
        self.sample_info = text_label(tr("等待可匹配的输出样本"), "muted")
        self.sample_info.setWordWrap(True)
        sample_line = QHBoxLayout()
        sample_line.addWidget(self.sample_info, 1)
        self.coverage = text_label("", "muted")
        self.coverage.setWordWrap(True)
        self.coverage.hide()
        sample_line.addWidget(self.coverage)
        metric_layout.addLayout(sample_line)
        self.chart = TimelineChart()
        self.chart.sample_activated.connect(self.show_sample)
        metric_layout.addWidget(self.chart)
        layout.addWidget(sheet, 1)
        self.activity = text_label(tr("尚未发现活动对话"), "muted")
        self.activity.setWordWrap(True)
        layout.addWidget(self.activity)
        self.details_button = QPushButton(tr("观测详情"))
        self.details_button.setObjectName("glassAction")
        self.details_button.clicked.connect(self.show_details)
        self.notice = text_label("", "notice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        layout.addWidget(self.notice)
        footer_bar = GlassBar()
        footer = QHBoxLayout(footer_bar)
        footer.setContentsMargins(12, 9, 12, 12)
        history = QPushButton(tr("查看历史"))
        self.history_button = history
        history.setObjectName("glassAction")
        history.clicked.connect(self.history_requested)
        settings = QPushButton(tr("设置"))
        self.settings_button = settings
        settings.setObjectName("glassAction")
        settings.clicked.connect(self.settings_requested)
        footer.addWidget(self.details_button)
        footer.addStretch()
        footer.addWidget(history)
        footer.addStretch()
        footer.addWidget(settings)
        footer_bar.register_controls(self.details_button, history, settings)
        layout.addWidget(footer_bar)

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("TokenPulse · 词脉"))
        self.brand_label.setText(tr("TokenPulse  词脉"))
        self.brand_mark.setToolTip(tr("本机只读观测；数据留在本地，不上传统计或日志"))
        self.group_box.setAccessibleName(tr("模型"))
        for name, button in self.range_buttons.items():
            button.setText(tr(RANGE_LABELS[name]))
        self.details_button.setText(tr("观测详情"))
        self.history_button.setText(tr("查看历史"))
        self.settings_button.setText(tr("设置"))
        self.render()
        self.chart.retranslate_ui()
        if self.sample_window is not None:
            self.sample_window.retranslate_ui()

    def task_key(self, task: Task) -> tuple:
        sample = task.latest or Sample(task.id, "", "", task.updated_at, None)
        return series_key(replace(sample, context=task.context))

    @property
    def range_label(self) -> str:
        return tr(RANGE_LABELS[self.range_name])

    @property
    def selected_tasks(self) -> list[Task]:
        return [
            t
            for t in self.snapshot.tasks
            if self.task_key(t) == self.selected_key
            and (self.view is None or self.view.start <= t.updated_at <= self.view.end)
        ]

    def recent_tasks(self, now: float) -> list[Task]:
        # Chart ranges select historical samples, not the lifetime of live state.
        return [task for task in self.selected_tasks if task.has_recent_state(now)]

    def group_index(self, key: tuple | None) -> int:
        # QVariant's findData does not compare Python dataclasses by value.
        return next(
            (i for i in range(self.group_box.count()) if self.group_box.itemData(i) == key), -1
        )

    def apply(self, snapshot: Snapshot, samples: list[Sample] | None = None) -> None:
        self.snapshot = snapshot
        # The controller supplies one deduplicated anonymous stream including history.
        self.samples = list(snapshot.samples) if samples is None else samples
        self._view_cache = None
        self._refresh_groups(time.time())
        self.render()

    def _refresh_groups(self, now: float) -> None:
        duration, _ = RANGES[self.range_name]
        available = series([s for s in self.samples if now - duration <= s.ended_at <= now])
        latest = {key: values[-1].ended_at for key, values in available.items()}
        for task in self.snapshot.tasks:
            if not now - duration <= task.updated_at <= now:
                continue
            key = self.task_key(task)
            available.setdefault(key, [])
            latest[key] = max(latest.get(key, 0), task.updated_at)
        self.groups = available
        keys = sorted(available, key=lambda key: latest[key], reverse=True)
        if self.selected_key is None:
            self.selected_key = keys[0] if keys else None
        if self.selected_key is not None and self.selected_key not in keys:
            keys.append(self.selected_key)
        labels = [
            group_title(key) + (tr(" · 此范围无记录") if not available.get(key) else "")
            for key in keys
        ]
        old_items = [self.group_box.itemData(i) for i in range(self.group_box.count())]
        old_labels = [self.group_box.itemText(i) for i in range(self.group_box.count())]
        if old_items != (keys or [None]) or old_labels != (labels or [tr("尚未发现模型")]):
            self.group_box.blockSignals(True)
            self.group_box.clear()
            for key, label in zip(keys, labels, strict=True):
                self.group_box.addItem(label, key)
                self.group_box.setItemData(
                    self.group_box.count() - 1, label, Qt.ItemDataRole.ToolTipRole
                )
            if not keys:
                self.group_box.addItem(tr("尚未发现模型"), None)
            self.group_box.blockSignals(False)
        self.group_box.blockSignals(True)
        self.group_box.setCurrentIndex(max(0, self.group_index(self.selected_key)))
        self.group_box.blockSignals(False)
        self.group_box.setToolTip(self.group_box.currentText())

    def _select(self, index: int) -> None:
        self.selected_key = self.group_box.itemData(index)
        self.render()

    def set_range(self, name: str) -> None:
        self.range_name = name
        for title, button in self.range_buttons.items():
            button.setChecked(title == name)
        self.render()

    def render(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        accent = colors()["accent"]
        if self.mark_color != accent:
            self.brand_mark.setPixmap(pulse_icon(accent).pixmap(30, 30))
            self.mark_color = accent
        self._refresh_groups(now)
        pending = self.snapshot.backfill_pending
        completed = self.snapshot.backfill_completed
        self.recovery.setText(
            tr(
                "正在检查近 7 天记录 · {completed}/{value} 个会话文件",
                completed=completed,
                value=completed + pending,
            )
            + (
                tr(" · 复用 {backfill_reused} 个", backfill_reused=self.snapshot.backfill_reused)
                if self.snapshot.backfill_reused
                else ""
            )
            if pending
            else ""
        )
        self.recovery.setVisible(bool(pending))
        duration, step = RANGES[self.range_name]
        signature = (self.selected_key, self.range_name, int(now))
        if signature != self._view_cache:
            self.view = timeline(
                self.groups.get(self.selected_key, []), now=now, duration=duration, step=step
            )
            self.chart.set_data(self.view)
            self._view_cache = signature
        summary = self.view.summary
        self.speed_value.setAccessibleName(
            tr("近 {range_name}平均输出速度，估算值", range_name=self.range_label)
        )
        self.speed_value.setToolTip(
            tr(
                "近 {range_name} · 平均输出速度（估算）\n"
                "有效总输出 tokens ÷ 对应输出流累计秒数；不包含工具执行时间",
                range_name=self.range_label,
            )
        )
        self.speed_value.setText(f"{summary.weighted_tps:.1f}" if summary.count else "—")
        self.speed_value.ensurePolished()
        self.speed_unit.ensurePolished()
        # Align text baselines rather than the two fonts' different descent areas.
        descent = self.speed_value.fontMetrics().descent() - self.speed_unit.fontMetrics().descent()
        self.speed_unit.setContentsMargins(0, 0, 0, max(0, descent))
        self.summary.setText(
            tr(
                "中位数 {median_tps:.1f} · 范围 {low_tps:.1f}–{high_tps:.1f} TPS",
                median_tps=summary.median_tps,
                low_tps=summary.low_tps,
                high_tps=summary.high_tps,
            )
            if summary.count
            else tr("中位数 — · 范围 —")
        )
        evidence = (
            tr("演示样本")
            if self.view.samples and all(s.source == "synthetic-demo" for s in self.view.samples)
            else tr("日志估算")
        )
        self.sample_info.setText(
            tr(
                "{count} 段可测速 · {observed_conversations} 个对话",
                count=summary.count,
                observed_conversations=self.view.observed_conversations,
            )
        )
        self.sample_info.setToolTip(
            tr(
                "共 {value} 段记录 · 汇总此模型全部输出\n"
                "{evidence} · 总输出 tokens · 按输出时长加权",
                value=len(self.view.samples),
                evidence=evidence,
            )
        )
        exclusions = Counter(s.exclusion for s in self.view.samples if s.exclusion)
        reasons = [
            tr("{value} {count} 段", value=tr(ISSUES.get(reason, "证据不足")), count=count)
            for reason, count in exclusions.most_common()
        ]
        self.coverage.setText(tr("{excluded} 段未计入", excluded=summary.excluded))
        self.coverage.setToolTip("\n".join(reasons))
        self.coverage.setVisible(bool(summary.excluded))
        recent = self.recent_tasks(now)
        states = Counter(task.display_state(now) for task in recent)
        active = sum(states[state] for state in ("waiting", "generating", "tool"))
        if states["error"]:
            status = tr("请求异常")
        elif active:
            status = tr("{active} 个活动", active=active)
        elif states["unknown"]:
            status = tr("状态未知")
        elif states["idle"]:
            status = tr("空闲")
        else:
            status = tr("历史观测") if self.view.samples else tr("无近期状态")
        self.status.setText(status if self.selected_key else tr("等待观测"))
        descriptions = [
            tr("{count} 个{value}", count=count, value=tr(STATES[state]))
            for state, count in states.items()
        ]
        activity = tr("暂无输出记录")
        activity_details = " · ".join(descriptions) if recent else tr("此模型没有近期状态证据")
        self.status.setToolTip(activity_details)
        if self.view.samples:
            latest = self.view.samples[-1]
            stamp = datetime.fromtimestamp(latest.ended_at).strftime("%m-%d %H:%M")
            activity = tr("最近输出 {stamp}", stamp=stamp)
            if latest.is_stale(now):
                activity += tr(" · 已过期")
        self.activity.setText(activity)
        if self.snapshot.at:
            checked = datetime.fromtimestamp(self.snapshot.at).strftime("%H:%M:%S")
            activity_details += tr(
                "\n采集检查 {checked} · 当前范围 {value} 段",
                checked=checked,
                value=len(self.view.samples),
            )
        self.activity.setToolTip(activity_details)
        notices = [tr(DIAGNOSTICS.get(d, d)) for d in self.snapshot.diagnostics]
        for task in recent:
            if task.notice:
                notices.append(
                    tr("请求层级被客户端忽略")
                    if task.notice == "tier_ignored"
                    else tr("请求失败或中断")
                )
        self.notice.setText("\n".join(dict.fromkeys(notices)))
        self.notice.setVisible(bool(notices))
        self.details_button.setEnabled(self.selected_key is not None)

    def detail_values(self) -> list[tuple[str, str]]:
        if not self.selected_key:
            return []
        ctx = self.selected_key[0]
        tasks = self.recent_tasks(self.view.end)
        configs = {t.context.configured_tier or tr("未知") for t in tasks}
        efforts = {s.context.effort or tr("未记录") for s in self.view.samples}
        if not efforts:
            efforts = {t.context.effort or tr("未记录") for t in tasks}
        effort_text = " / ".join(sorted(efforts)) or tr("未记录")
        if len(efforts) > 1:
            effort_text += tr("（已合并统计）")
        contexts = [s.context for s in self.view.samples] or [t.context for t in tasks]
        logins = Counter(ctx.auth_mode or tr("未记录") for ctx in contexts)
        login_text = " / ".join(f"{name}（{count}）" for name, count in sorted(logins.items()))

        def observed(field, missing):
            return (
                " / ".join(sorted({getattr(value, field) or missing for value in contexts}))
                or missing
            )

        submitted = " / ".join(sorted({submission_label(value) for value in contexts})) or tr(
            "当前范围无提交记录"
        )
        timing_sources = Counter(
            "synthetic" if sample.source == "synthetic-demo" else sample.timing_source or "legacy"
            for sample in self.view.samples
            if sample.exclusion is None
        )
        timing_labels = {
            "stream-log": tr("诊断日志起点"),
            "item-event": tr("输出事件起点"),
            "legacy": tr("旧版记录"),
            "synthetic": tr("合成边界"),
        }
        timing_text = " / ".join(
            tr("{value} {count} 段", value=timing_labels.get(name, name), count=count)
            for name, count in timing_sources.items()
        ) or tr("暂无可匹配边界")
        return [
            (tr("模型"), ctx.model or tr("未知")),
            (tr("样本覆盖"), self.sample_info.toolTip().split("\n")[0]),
            (tr("统计方式"), self.sample_info.toolTip().split("\n")[-1]),
            (tr("未计入均速"), self.coverage.toolTip() or tr("无")),
            (tr("采集状态"), self.activity.toolTip()),
            (tr("思考强度"), effort_text),
            (tr("接入渠道"), observed("provider", tr("未知"))),
            (tr("登录方式记录"), (login_text or tr("未记录")) + tr("；仅供追溯，不参与分组")),
            (tr("客户端提交层级"), submitted),
            (tr("请求层级"), observed("requested_tier", tr("未记录：未获取请求层级证据"))),
            (tr("实际层级"), observed("actual_tier", tr("未获服务端确认"))),
            (tr("当前全局配置"), " / ".join(sorted(configs)) if configs else tr("无当前快照")),
            (
                tr("数据来源"),
                " / ".join(sorted({s.source for s in self.view.samples})) or tr("未记录"),
            ),
            (tr("测速边界"), timing_text),
            (
                tr("token 口径"),
                " / ".join(sorted({s.token_scope for s in self.view.samples})) or tr("未记录"),
            ),
        ]

    def show_details(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("观测详情 · 词脉"))
        dialog.resize(540, 620)
        layout = QVBoxLayout(dialog)
        fields = QGridLayout()
        fields.setColumnMinimumWidth(0, 105)
        fields.setColumnStretch(1, 1)
        for row, (name, value) in enumerate(self.detail_values()):
            fields.addWidget(text_label(name, "muted"), row, 0)
            label = text_label(value)
            label.setWordWrap(True)
            fields.addWidget(label, row, 1)
        layout.addLayout(fields)

        note = text_label(
            tr(
                "提交层级来自客户端轮次提交日志，不等于出站请求或服务端实际执行层级。主面板按模型汇总全部模式、渠道和思考强度；原始值保留供追溯。全局配置也不"
                "能证明请求实际使用。平均速度按有效总输出 tokens 和对应输出时长加权。"
                "登录方式及其缺失记录不拆组，原始证据保留在历史与 "
                "CSV。历史最多保留 30 天 / 10,000 段；自动分批补读近 7 "
                "天索引中的会话，包括归档任务。索引遗漏、记录损坏或日志已清理时仍可能缺失。"
            ),
            "muted",
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        close = QPushButton(tr("关闭"))
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def show_sample(self, sample: Sample) -> None:
        self.show_bucket(
            Bucket(sample.ended_at, sample.ended_at, (sample,), aggregate([sample])), single=True
        )

    def show_bucket(self, bucket: Bucket, *, single: bool = False) -> None:
        from token_pulse.ui.history_window import HistoryWindow

        if self.sample_window is None:
            self.sample_window = HistoryWindow()
        self.sample_window.set_scope(bucket.start, bucket.end, single=single)
        for box in self.sample_window.filters.values():
            box.blockSignals(True)
            box.setCurrentIndex(0)
            box.blockSignals(False)
        self.sample_window.apply(list(bucket.samples))
        self.sample_window.showNormal()
        self.sample_window.raise_()
        self.sample_window.activateWindow()

    def closeEvent(self, event):
        if self.hide_on_close:
            self.hide()
            event.ignore()
        else:
            if self.sample_window is not None:
                self.sample_window.close()
            super().closeEvent(event)
