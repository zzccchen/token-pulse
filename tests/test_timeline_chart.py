import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from token_pulse.domain import Context, Sample
from token_pulse.timeline import timeline
from token_pulse.ui.timeline_chart import TimelineChart, mode_info, sample_text


def test_points_use_individual_rates_and_click_or_keyboard_open_exact_sample():
    app = QApplication.instance() or QApplication([])
    chart = TimelineChart()
    chart.resize(380, 195)
    one = Sample("a", "b", "c", 101, 100, 91)
    two = replace(one, response_id="second", ended_at=105, started_at=100, output_tokens=300)
    missing = replace(one, response_id="missing", ended_at=102, started_at=None)
    chart.set_data(timeline([one, missing, two], now=110, duration=60, step=30))
    chart.show()
    app.processEvents()
    assert chart.points == (one, two)
    assert chart.point_position(0).x() < chart.point_position(1).x()
    assert chart.point_position(0).y() > chart.point_position(1).y()
    assert chart.rates == (10, 60)
    position = chart.point_position(0).toPoint()
    QTest.mouseMove(chart, position)
    assert chart.active == 0
    assert chart.card_visible
    assert chart.rect().contains(chart.card_rect(chart.point_position(0)).toRect())
    assert "10.0 tokens/s" in sample_text(one)
    opened = []
    chart.sample_activated.connect(opened.append)
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=position)
    assert opened[-1] == one
    assert not chart.card_visible
    QTest.keyClick(chart, Qt.Key.Key_Right)
    assert chart.active == 1
    QTest.keyClick(chart, Qt.Key.Key_Return)
    assert opened[-1] == two
    QTest.keyClick(chart, Qt.Key.Key_Home)
    assert chart.active == 0
    assert not chart.grab().isNull()
    chart.set_data(timeline([one, two], now=110, duration=86400, step=1800))
    assert chart.active is None
    assert not chart.card_visible
    chart.close()


@pytest.mark.parametrize(
    "context,expected",
    [
        (Context(submitted_tier="priority", submitted_tier_state="set"), "fast"),
        (Context(submitted_tier="fast", submitted_tier_state="set"), "fast"),
        (Context(submitted_tier="default", submitted_tier_state="set"), "standard"),
        (Context(submitted_tier_state="cleared"), "standard"),
        (Context(submitted_tier_state="unspecified", configured_tier="priority"), "other"),
        (Context(actual_tier="priority"), "other"),
        (Context(submitted_tier_state="ambiguous"), "other"),
        (Context(requested_tier="priority"), "fast"),
        (Context(requested_tier="flex"), "other"),
        (
            Context(
                requested_tier="default", submitted_tier="priority", submitted_tier_state="set"
            ),
            "standard",
        ),
    ],
)
def test_mode_markers_use_request_or_submission_evidence_and_never_speed(context, expected):
    sample = Sample("a", "b", "c", 100, 50, 99, context)
    assert mode_info(sample)[0] == expected
    assert mode_info(replace(sample, output_tokens=5000))[0] == expected
    if context.requested_tier is not None:
        assert "请求值" in mode_info(sample)[1]
    if context.requested_tier is not None or context.submitted_tier_state in {"set", "cleared"}:
        assert {"fast": "Fast", "standard": "普通", "other": "其他"}[expected] in sample_text(
            sample
        )
    else:
        assert "模式冲突" in sample_text(sample) or "模式未知" in sample_text(sample)


def test_refresh_preserves_sample_selection_but_updates_metadata_and_position():
    app = QApplication.instance() or QApplication([])
    chart = TimelineChart()
    chart.resize(380, 195)
    sample = Sample("a", "b", "c", 101, 100, 91)
    chart.set_data(timeline([sample], now=110, duration=60, step=30))
    chart.active = 0
    changed = replace(
        sample, context=Context(submitted_tier="priority", submitted_tier_state="set")
    )
    earlier = replace(sample, response_id="earlier", ended_at=90, started_at=80)
    chart.set_data(timeline([earlier, changed], now=111, duration=60, step=30))
    assert chart.active == 1
    assert "Fast" in chart.accessibleDescription()
    assert chart.hit_test(QPointF(0, 0)) is None
    chart.set_data(timeline([], now=111, duration=60, step=30))
    assert chart.active is None and not chart.points
    chart.close()
    app.processEvents()


def test_overlapping_samples_and_zero_tps_remain_individually_accessible():
    app = QApplication.instance() or QApplication([])
    chart = TimelineChart()
    chart.resize(380, 195)
    one = Sample("a", "b", "c", 101, 0, 91)
    two = replace(one, response_id="second")
    chart.set_data(timeline([one, two], now=110, duration=60, step=30))
    chart.show()
    app.processEvents()
    assert len(chart.points) == 2
    assert chart.point_position(0).y() == chart.plot_rect().bottom()
    assert chart.hit_test(chart.point_position(0)) == 1
    chart.active = 1
    QTest.keyClick(chart, Qt.Key.Key_Left)
    assert chart.active == 0
    chart.close()
