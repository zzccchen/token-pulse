import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from token_pulse.collector import Snapshot
from token_pulse.demo import demo_snapshot
from token_pulse.timeline import aggregate, series_key
from token_pulse.ui.panel import Panel


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_dropdown_selection_survives_new_models_and_missing_samples(app):
    panel = Panel()
    snapshot = demo_snapshot()
    panel.apply(snapshot)
    assert panel.group_box.count() == 1
    assert panel.view.conversations == 2
    assert panel.speed_value.text() == f"{aggregate(list(snapshot.samples)).weighted_tps:.1f}"
    original = panel.selected_key
    other = replace(
        snapshot.samples[-1],
        response_id="different",
        context=replace(snapshot.samples[-1].context, model="other-model"),
    )
    panel.apply(replace(snapshot, samples=(*snapshot.samples, other)))
    assert panel.selected_key == original
    panel.group_box.setCurrentIndex(panel.group_index(series_key(other)))
    assert panel.selected_key == series_key(other)
    assert panel.view.summary.count == 1
    panel.apply(replace(snapshot, samples=()))
    assert panel.speed_value.text() == "—"
    assert panel.selected_key == series_key(other)
    assert "此范围无记录" in panel.group_box.currentText()
    panel.group_box.setCurrentIndex(panel.group_index(original))
    assert panel.selected_key == original
    panel.close()


def test_channel_is_hidden_in_normal_selector_and_unknown_confirmation_in_details(app):
    panel = Panel()
    snapshot = demo_snapshot()
    panel.apply(snapshot)
    assert "openai" not in panel.group_box.currentText()
    assert dict(panel.detail_values())["实际层级"] == "未获服务端确认"
    assert "openai" in dict(panel.detail_values())["接入渠道"]
    panel.render(snapshot.at + 300)
    assert "没有近期状态证据" in panel.activity.toolTip()
    assert panel.status.text() == "历史观测"
    changed = replace(
        snapshot.samples[0],
        response_id="channel",
        context=replace(snapshot.samples[0].context, provider="another"),
    )
    panel.apply(replace(snapshot, samples=(*snapshot.samples, changed)))
    assert panel.group_box.count() == 1
    assert "openai" not in panel.group_box.currentText()
    assert panel.view.summary.count == len(snapshot.samples) + 1
    details = dict(panel.detail_values())
    assert "another" in details["接入渠道"] and "openai" in details["接入渠道"]
    assert panel.group_box.count() == 1
    panel.close()


def test_login_metadata_is_visible_without_splitting_samples(app):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QComboBox, QDialog

    panel = Panel()
    snapshot = demo_snapshot()
    missing = replace(
        snapshot.samples[-1],
        response_id="no-auth",
        context=replace(snapshot.samples[-1].context, auth_mode=None),
    )
    panel.apply(replace(snapshot, samples=(*snapshot.samples, missing)))
    assert panel.group_box.count() == 1
    errors = []

    def inspect():
        dialog = app.activeModalWidget()
        try:
            assert isinstance(dialog, QDialog)
            box = dialog.findChild(QComboBox)
            assert box is None
            assert panel.selected_key == series_key(missing)
            assert panel.view.summary.count == len(snapshot.samples) + 1
            assert len(panel.selected_tasks) == len(snapshot.tasks)
            login = dict(panel.detail_values())["登录方式记录"]
            assert "Chatgpt" in login and "未记录（1）" in login
            assert "不参与分组" in login
            assert panel.group_box.count() == 1
        except Exception as error:
            errors.append(error)
        finally:
            if dialog:
                dialog.accept()

    QTimer.singleShot(20, inspect)
    panel.show_details()
    assert not errors, errors
    panel.close()


def test_range_uses_supplied_history_without_readding_live_samples(app):
    panel = Panel()
    snapshot = demo_snapshot()
    old = replace(
        snapshot.samples[0],
        response_id="old",
        ended_at=snapshot.at - 2 * 86400,
        started_at=snapshot.at - 2 * 86400 - 10,
    )
    panel.apply(snapshot, [old])
    assert panel.view.summary.count == 0
    panel.set_range("1d")
    assert panel.view.summary.count == 0
    panel.set_range("7d")
    assert panel.view.summary.count == 1
    assert panel.view.samples == (old,)
    assert panel.speed_value.toolTip().startswith("近 7 天")
    panel.show_bucket(next(b for b in panel.view.buckets if b.samples))
    assert panel.sample_window.table.rowCount() == 1
    panel.close()


def test_empty_panel_explains_next_step_and_titles_are_plain_text(app):
    panel = Panel()
    panel.apply(Snapshot(diagnostics=("no_sessions",)))
    assert "运行一次本机 Codex" in panel.notice.text()
    assert panel.speed_value.text() == "—"
    assert panel.group_box.currentText() == "尚未发现模型"
    panel.render()
    assert panel.group_box.count() == 1
    panel.close()


def test_panel_actually_renders_with_timeline(app):
    panel = Panel()
    panel.apply(demo_snapshot())
    panel.show()
    app.processEvents()
    assert not panel.grab().isNull()
    assert panel.width() >= 410
    panel.close()


def test_selector_merges_effort_and_puts_unrecorded_tier_in_details(app):
    panel = Panel()
    snapshot = demo_snapshot()
    one = replace(
        snapshot.samples[0],
        context=replace(
            snapshot.samples[0].context,
            requested_tier=None,
            submitted_tier=None,
            submitted_tier_state=None,
        ),
    )
    two = replace(one, response_id="max-effort", context=replace(one.context, effort="max"))
    panel.apply(replace(snapshot, tasks=(), samples=(one, two)))
    assert panel.group_box.count() == 1
    assert panel.group_box.currentText() == "demo-model"
    assert panel.view.summary.count == 2
    details = dict(panel.detail_values())
    assert details["思考强度"] == "high / max（已合并统计）"
    assert "未获取请求层级证据" in details["请求层级"]
    panel.close()


def test_submission_modes_pool_without_claiming_confirmation(app):
    panel = Panel()
    snapshot = demo_snapshot()
    base = snapshot.samples[-1]
    values = [
        replace(
            base,
            response_id=str(i),
            context=replace(
                base.context,
                requested_tier=None,
                submitted_tier=tier,
                submitted_tier_state="set" if tier else None,
            ),
        )
        for i, tier in enumerate(["priority", "default", None])
    ]
    panel.apply(replace(snapshot, tasks=(), samples=tuple(values)))
    assert panel.group_box.count() == 1
    assert panel.group_box.currentText() == "demo-model"
    assert panel.view.summary.count == 3
    details = dict(panel.detail_values())
    assert details["实际层级"] == "未获服务端确认"
    assert "未获取请求层级证据" in details["请求层级"]
    assert "priority" in details["客户端提交层级"]
    assert "default" in details["客户端提交层级"]
    assert "未记录" in details["客户端提交层级"]
    panel.close()


def test_selector_and_activity_respect_range_instead_of_seven_day_history(app):
    panel = Panel()
    snapshot = demo_snapshot()
    recent = replace(
        snapshot.samples[-1],
        context=replace(
            snapshot.samples[-1].context,
            model="recent-model",
            submitted_tier="default",
            submitted_tier_state="set",
        ),
    )
    old = replace(snapshot.samples[0], ended_at=snapshot.at - 7200, started_at=snapshot.at - 7210)
    old_task = replace(snapshot.tasks[0], updated_at=snapshot.at - 7200, latest=old)
    panel.apply(replace(snapshot, tasks=(old_task,), samples=(old, recent)))
    assert panel.group_box.count() == 1
    assert panel.group_box.currentText() == "recent-model"
    assert panel.selected_tasks == []
    panel.set_range("1d")
    assert panel.group_box.count() == 2
    panel.group_box.setCurrentIndex(panel.group_index(series_key(old)))
    panel.set_range("30m")
    assert "此范围无记录" in panel.group_box.currentText()
    assert panel.view.summary.count == 0
    panel.set_range("1d")
    assert "此范围无记录" not in panel.group_box.currentText()
    assert panel.view.summary.count == 1
    panel.close()


def test_unchanged_standard_mode_does_not_split_null_and_default(app):
    panel = Panel()
    snapshot = demo_snapshot()
    base = snapshot.samples[-1]
    default = replace(
        base, context=replace(base.context, submitted_tier="default", submitted_tier_state="set")
    )
    cleared = replace(
        base,
        response_id="clear",
        context=replace(base.context, submitted_tier=None, submitted_tier_state="cleared"),
    )
    panel.apply(replace(snapshot, tasks=(), samples=(default, cleared)))
    assert panel.group_box.count() == 1
    assert panel.view.summary.count == 2
    assert panel.group_box.currentText() == "demo-model"
    assert "清除" in dict(panel.detail_values())["客户端提交层级"]
    assert all(
        s.context.requested_tier is None and s.context.actual_tier is None
        for s in panel.view.samples
    )
    panel.close()


def test_historical_errors_are_not_current_and_incomplete_conversations_are_counted(app):
    panel = Panel()
    snapshot = demo_snapshot()
    at = snapshot.at - 2 * 86400
    values = tuple(
        replace(
            snapshot.samples[-1],
            task_id=f"task-{i}",
            response_id=str(i),
            ended_at=at,
            started_at=None,
        )
        for i in range(5)
    )
    tasks = tuple(
        replace(
            snapshot.tasks[0],
            id=s.task_id,
            updated_at=at,
            state="error" if i == 0 else "idle",
            latest=s,
            notice="request_failed" if i == 0 else None,
        )
        for i, s in enumerate(values)
    )
    panel.apply(replace(snapshot, tasks=tasks, samples=values))
    panel.set_range("7d")
    assert panel.status.text() == "历史观测"
    assert "5 个对话" in panel.sample_info.text()
    assert "0 段可测速" in panel.sample_info.text()
    assert "缺少首个输出起点" in panel.coverage.toolTip()
    assert "请求失败" not in panel.notice.text()
    assert "空闲" not in panel.activity.text()
    assert panel.speed_value.text() == "—"
    panel.close()


def test_recent_error_expires_without_becoming_an_idle_claim(app):
    panel = Panel()
    snapshot = demo_snapshot()
    task = replace(snapshot.tasks[0], state="error", notice="request_failed")
    panel.apply(replace(snapshot, tasks=(task,)))
    assert panel.status.text() == "请求异常"
    assert "请求失败" in panel.notice.text()
    panel.render(snapshot.at + 121)
    assert panel.status.text() == "历史观测"
    assert "请求失败" not in panel.notice.text()
    assert panel.recent_tasks(snapshot.at + 121) == []
    panel.close()


def test_recovery_progress_and_other_model_conditions_are_visible(app):
    panel = Panel()
    snapshot = demo_snapshot()
    sample = snapshot.samples[-1]
    other = replace(
        sample, response_id="other-mode", context=replace(sample.context, requested_tier="priority")
    )
    panel.apply(
        replace(snapshot, samples=(sample, other), backfill_pending=7, backfill_completed=3)
    )
    assert "3/10" in panel.recovery.text()
    assert not panel.recovery.isHidden()
    assert "共 2 段记录" in panel.sample_info.toolTip()
    assert panel.view.summary.count == 2
    panel.apply(replace(snapshot, backfill_completed=10))
    assert panel.recovery.isHidden()
    panel.close()


def test_empty_task_channel_does_not_hide_populated_mode(app):
    panel = Panel()
    snapshot = demo_snapshot()
    sample = snapshot.samples[-1]
    task = replace(
        snapshot.tasks[0],
        context=replace(sample.context, provider="another"),
        latest=sample,
    )
    panel.apply(replace(snapshot, tasks=(task,), samples=(sample,)))
    assert panel.selected_key == series_key(sample)
    assert panel.view.summary.count == 1
    assert panel.group_box.count() == 1
    assert panel.selected_tasks == [task]
    assert "此范围无记录" not in panel.group_box.currentText()
    panel.close()


def test_model_summary_includes_every_mode_and_channel_without_group_controls(app):
    from PySide6.QtWidgets import QPushButton

    panel = Panel()
    snapshot = demo_snapshot()
    base = snapshot.samples[-1]
    other = replace(
        base,
        response_id="other",
        source="another-log-source",
        context=replace(base.context, provider="another", auth_mode=None),
    )
    standard = replace(
        base,
        response_id="standard",
        context=replace(base.context, submitted_tier="default", submitted_tier_state="set"),
    )
    incomplete = replace(standard, response_id="incomplete", started_at=None)
    panel.apply(replace(snapshot, tasks=(), samples=(base, other, standard, incomplete)))
    assert panel.group_box.count() == 1
    assert panel.view.summary.count == 3
    assert panel.view.summary.excluded == 1
    assert "共 4 段记录" in panel.sample_info.toolTip()
    assert "当前组" not in panel.sample_info.text()
    assert all("分组" not in button.text() for button in panel.findChildren(QPushButton))
    assert {s.context.provider for s in panel.view.samples} == {"openai", "another"}
    panel.close()


def test_scatter_click_opens_one_output_and_preserves_mode_evidence(app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    panel = Panel()
    panel.apply(demo_snapshot())
    panel.show()
    app.processEvents()
    sample = panel.chart.points[1]
    QTest.mouseClick(
        panel.chart, Qt.MouseButton.LeftButton, pos=panel.chart.point_position(1).toPoint()
    )
    assert panel.sample_window.table.rowCount() == 1
    assert panel.sample_window.filtered == [sample]
    assert panel.sample_window.heading.text() == "单段输出"
    assert sample.context.submitted_tier == "default"
    assert panel.view.summary.count == 20
    panel.close()
