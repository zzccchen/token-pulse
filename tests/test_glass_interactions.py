import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QAbstractAnimation, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton

from token_pulse.ui.glass import GlassBar, MaterialComboBox
from token_pulse.ui.theme import apply_theme


def enter(widget):
    QApplication.sendEvent(widget, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))


@pytest.fixture
def surface():
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    bar = GlassBar()
    row = QHBoxLayout(bar)
    one, two = QPushButton("历史"), QPushButton("设置")
    row.addWidget(one)
    row.addWidget(two)
    bar.register_controls(one, two)
    bar.resize(300, 60)
    bar.show()
    app.processEvents()
    yield app, bar, one, two
    bar.close()
    apply_theme(app)


def test_shared_container_requires_explicit_interactive_controls(surface):
    _, bar, one, two = surface
    assert bar.controls == [one, two]
    with pytest.raises(ValueError):
        bar.register_controls(QLabel("静态数据", bar))
    bar.register_controls(one)
    assert len(bar.controls) == 2


def test_morph_retargets_from_current_visible_position(surface):
    _, bar, one, two = surface
    enter(one)
    QTest.qWait(210)
    enter(two)
    bar.animation.setCurrentTime(60)
    visible = bar.lens_rect
    enter(one)
    assert bar.lens_rect == visible
    QTest.qWait(210)
    assert bar.animation.state() == QAbstractAnimation.State.Stopped
    assert abs(bar.lens_rect.center().x() - one.geometry().center().x()) <= 1


def test_press_feedback_preserves_button_click(surface):
    _, bar, one, _ = surface
    clicks = []
    one.clicked.connect(lambda: clicks.append(True))
    enter(one)
    QTest.qWait(210)
    QTest.mousePress(one, Qt.MouseButton.LeftButton)
    assert bar.pressed
    assert not bar.grab().isNull()
    QTest.mouseRelease(one, Qt.MouseButton.LeftButton)
    assert not bar.pressed
    assert clicks == [True]


def test_reduced_motion_finishes_transition_immediately(surface):
    app, bar, one, two = surface
    enter(one)
    QTest.qWait(210)
    enter(two)
    apply_theme(app, motion_reduced=True)
    assert bar.animation.state() == QAbstractAnimation.State.Stopped
    assert abs(bar.lens_rect.center().x() - two.geometry().center().x()) <= 1
    enter(one)
    assert bar.animation.state() == QAbstractAnimation.State.Stopped


def test_hiding_surface_stops_paint_animation(surface):
    _, bar, one, two = surface
    enter(one)
    QTest.qWait(210)
    enter(two)
    bar.hide()
    assert bar.animation.state() == QAbstractAnimation.State.Stopped
    assert bar.lens_strength == 0


def test_material_popup_preserves_keyboard_mouse_and_scroll_selection(surface):
    app, bar, _, _ = surface
    combo = MaterialComboBox(bar)
    bar.layout().addWidget(combo)
    combo.addItems([f"model-{i}" for i in range(12)])
    combo.setCurrentIndex(4)
    combo.show()
    app.processEvents()
    combo.showPopup()
    app.processEvents()
    view = combo.view()
    popup = view.window()
    assert popup.isVisible() and view.verticalScrollBar().maximum() > 0
    assert combo.screen().availableGeometry().contains(popup.geometry())
    QTest.keyClick(view, Qt.Key.Key_Down)
    QTest.keyClick(view, Qt.Key.Key_Return)
    assert combo.currentIndex() == 5 and not popup.isVisible()
    combo.showPopup()
    QTest.keyClick(view, Qt.Key.Key_Down)
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert combo.currentIndex() == 5 and not popup.isVisible()
    combo.showPopup()
    view.scrollToTop()
    app.processEvents()
    first = view.model().index(0, 0)
    QTest.mouseClick(
        view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(first).center()
    )
    assert combo.currentIndex() == 0 and not popup.isVisible()
    combo.showPopup()
    QTest.mouseClick(popup, Qt.MouseButton.LeftButton, pos=QPoint(-4, -4))
    assert not popup.isVisible()
