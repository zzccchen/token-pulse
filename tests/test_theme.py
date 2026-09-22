import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from token_pulse.demo import demo_snapshot
from token_pulse.timeline import aggregate
from token_pulse.ui.glass import GlassBar
from token_pulse.ui.panel import Panel
from token_pulse.ui.theme import DARK, LIGHT, apply_theme


def luminance(hex_color):
    channels = QColor(hex_color).getRgbF()[:3]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
    return sum(v * w for v, w in zip(linear, [0.2126, 0.7152, 0.0722], strict=True))


@pytest.mark.parametrize("palette", [LIGHT, DARK])
def test_text_contrast_covers_background_and_control_colors(palette):
    for role in ["ink", "muted", "accent"]:
        for background in ["base", "content", "control", "glow1", "glow2", "selection"]:
            light, dark = sorted(
                [luminance(palette[role]), luminance(palette[background])], reverse=True
            )
            assert (light + 0.05) / (dark + 0.05) >= 4.5, (role, background)


@pytest.mark.parametrize("appearance,reduced", [("light", False), ("dark", False), ("dark", True)])
def test_theme_switch_preserves_metrics_and_solid_fallback(appearance, reduced):
    app = QApplication.instance() or QApplication([])
    try:
        panel = Panel()
        panel.apply(demo_snapshot())
        apply_theme(app, appearance, reduced)
        panel.render()
        panel.resize(410, 730)
        panel.show()
        app.processEvents()
        assert panel.width() == 410
        assert panel.speed_value.text() == f"{aggregate(panel.samples).weighted_tps:.1f}"
        assert not panel.grab().isNull()
        assert panel.mark_color == (DARK if appearance == "dark" else LIGHT)["accent"]
        panel.close()
        if reduced:
            bar = GlassBar()
            bar.resize(200, 60)
            bar.show()
            app.processEvents()
            assert bar.grab().toImage().pixelColor(100, 25).name() == DARK["control"].lower()
            bar.close()
    finally:
        apply_theme(app)
