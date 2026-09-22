import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from token_pulse.demo import demo_snapshot
from token_pulse.ui.history_window import HistoryWindow


def test_model_summary_pools_modes_and_explicit_filters_preserve_unknown():
    app = QApplication.instance() or QApplication([])
    window = HistoryWindow()
    sample = demo_snapshot().samples[0]
    sample = replace(sample, context=replace(sample.context, requested_tier="priority"))
    unknown = replace(
        sample, response_id="other", context=replace(sample.context, requested_tier=None)
    )
    window.apply([sample, unknown])
    assert "加权平均" in window.summary.text()
    box = window.filters["requested_tier"]
    box.setCurrentIndex(box.findData(""))
    assert window.filtered == [unknown]
    window.apply([sample, unknown])
    assert window.filtered == [unknown]
    assert window.table.rowCount() == 1
    window.show()
    app.processEvents()
    assert not window.grab().isNull()
    window.close()


def test_submission_filter_and_columns_do_not_claim_wire_request_or_confirmation():
    app = QApplication.instance() or QApplication([])
    window = HistoryWindow()
    base = demo_snapshot().samples[0]
    value = replace(
        base,
        context=replace(
            base.context, requested_tier=None, submitted_tier="priority", submitted_tier_state="set"
        ),
    )
    unknown = replace(
        base,
        response_id="unknown",
        context=replace(
            base.context, requested_tier=None, submitted_tier=None, submitted_tier_state=None
        ),
    )
    window.apply([value, unknown])
    box = window.filters["submitted_tier"]
    box.setCurrentIndex(box.findData("priority"))
    assert window.filtered == [value]
    assert window.table.item(0, 5).text() == "priority"
    assert window.table.item(0, 6).text() == "未知"
    assert window.table.item(0, 7).text() == "未确认"
    window.show()
    app.processEvents()
    assert not window.grab().isNull()
    window.close()
