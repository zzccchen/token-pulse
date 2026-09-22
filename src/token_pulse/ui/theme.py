from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

INK = "#182B49"
TEAL = "#167D8D"
BLUE = "#456A9C"
AMBER = "#9A5C16"
MUTED = "#65768C"

LIGHT = dict(
    base="#EEF2F6",
    content="#FAFCFE",
    ink="#18232E",
    muted="#455568",
    accent="#075F66",
    chart_standard="#5769AE",
    line="#BDCBD4",
    control="#FFFFFF",
    selection="#CFE9EC",
    glow1="#B7DDE6",
    glow2="#D4CDEC",
    notice="#FBEDD3",
    notice_ink="#765013",
)
DARK = dict(
    base="#171D28",
    content="#242D3A",
    ink="#F1F5FA",
    muted="#C2D0DF",
    accent="#8EE7E9",
    chart_standard="#B2BCF4",
    line="#617185",
    control="#354252",
    selection="#355961",
    glow1="#2C5967",
    glow2="#434364",
    notice="#493A27",
    notice_ink="#F4D098",
)


def colors() -> dict[str, str]:
    app = QApplication.instance()
    return DARK if app and app.property("appearance") == "dark" else LIGHT


def reduced_transparency() -> bool:
    app = QApplication.instance()
    return bool(app and app.property("reducedTransparency"))


def reduced_motion() -> bool:
    app = QApplication.instance()
    return reduced_transparency() or bool(app and app.property("reducedMotion"))


def stylesheet(appearance: str = "light") -> str:
    c = DARK if appearance == "dark" else LIGHT
    return f"""
    QWidget {{ font-family: "Segoe UI", "Microsoft YaHei UI", "Noto Sans CJK SC";
        font-size: 13px; color: {c["ink"]}; }}
    QMainWindow, QDialog {{ background: {c["base"]}; }}
    QLabel {{ background: transparent; }}
    QLabel#brand {{ font-size: 23px; font-weight: 700; }}
    QLabel#muted {{ color: {c["muted"]}; font-size: 12px; }}
    QLabel#metric {{ font-family: "Cascadia Mono", "Consolas", "DejaVu Sans Mono";
        font-size: 58px; font-weight: 600; color: {c["ink"]}; }}
    QLabel#status {{ color: {c["accent"]}; font-weight: 600; }}
    QLabel#notice {{ background: {c["notice"]}; color: {c["notice_ink"]};
        border-radius: 14px; padding: 10px; }}
    QFrame#sheet, QFrame#details {{ background: {c["content"]}; border-radius: 24px; }}
    QPushButton, QComboBox, QLineEdit {{ background: {c["control"]};
        border: 1px solid {c["line"]}; border-radius: 16px;
        padding: 7px 14px; min-height: 24px; }}
    QPushButton:hover {{ background: {c["selection"]}; border-color: {c["accent"]}; }}
    QPushButton:pressed, QPushButton:checked {{ background: {c["selection"]};
        color: {c["accent"]}; border-color: {c["accent"]}; }}
    QPushButton:disabled {{ color: {c["muted"]}; }}
    QPushButton:focus, QComboBox:focus, QLineEdit:focus {{
        border: 2px solid {c["accent"]}; padding: 6px 13px; }}
    QPushButton#glassAction, QComboBox#glassSelector {{
        background: transparent; border: 1px solid transparent; }}
    QPushButton#glassAction:hover, QPushButton#glassAction:pressed,
    QPushButton#glassAction:checked {{ background: transparent; color: {c["accent"]}; }}
    QPushButton#glassAction:focus, QComboBox#glassSelector:focus {{
        border: 2px solid {c["accent"]}; }}
    QComboBox::drop-down {{ border: 0; width: 22px; }}
    QComboBox::down-arrow {{ image: none; }}
    QComboBox {{ padding-right: 24px; }}
    QComboBox QAbstractItemView {{ background: {c["content"]};
        selection-background-color: {c["selection"]}; selection-color: {c["ink"]}; }}
    QTableWidget {{ background: {c["content"]}; alternate-background-color: {c["base"]};
        gridline-color: {c["line"]}; border: 0; selection-background-color: {c["selection"]};
        selection-color: {c["ink"]}; }}
    QHeaderView::section {{ background: {c["content"]}; color: {c["muted"]};
        border: none; padding: 9px 5px; font-weight: 600; }}
    QMenu {{ background: {c["content"]}; border: 1px solid {c["line"]}; padding: 6px; }}
    QMenu::item {{ padding: 9px 20px; }}
    QMenu::item:selected {{ background: {c["selection"]}; border-radius: 8px; }}
    QToolTip {{ background: {c["content"]}; color: {c["ink"]};
        border: 1px solid {c["line"]}; }}
    QCheckBox {{ spacing: 10px; padding: 6px 0; }}
    """


def apply_theme(
    app: QApplication,
    appearance: str = "light",
    reduced: bool = False,
    motion_reduced: bool = False,
) -> None:
    app.setProperty("appearance", appearance)
    app.setProperty("reducedTransparency", reduced)
    app.setProperty("reducedMotion", motion_reduced)
    c = colors()
    palette = QPalette()
    for role, key in [
        ("Window", "base"),
        ("Base", "content"),
        ("AlternateBase", "base"),
        ("WindowText", "ink"),
        ("Text", "ink"),
        ("ButtonText", "ink"),
        ("Button", "control"),
        ("Highlight", "selection"),
        ("HighlightedText", "ink"),
    ]:
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(c[key]))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(appearance))
    for window in app.topLevelWidgets():
        window.update()
        for child in window.findChildren(QWidget):
            child.update()


STYLE = stylesheet()


def pulse_icon(color: str = TEAL, number: str | None = None) -> QIcon:
    if number is not None:
        icon = QIcon()
        for size in (16, 20, 22, 24, 28, 32, 40, 48, 64):
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(color), max(0.75, size / 32)))
            painter.setBrush(QColor("#18232E"))
            painter.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), size * 0.18, size * 0.18)
            font = painter.font()
            font.setPixelSize(64)
            font.setWeight(QFont.Weight.ExtraBold)
            glyphs = QPainterPath()
            glyphs.addText(0, 0, font, number)
            bounds = glyphs.boundingRect()
            if not bounds.isEmpty():
                # Fit the actual glyphs, without the font's unused ascent/descent.
                # Condense wider numbers horizontally to retain their readable height.
                extent = size * 0.80
                scale_y = extent / bounds.height()
                scale_x = min(scale_y, extent / bounds.width())
                painter.translate(
                    (size - bounds.width() * scale_x) / 2,
                    (size - bounds.height() * scale_y) / 2,
                )
                painter.scale(scale_x, scale_y)
                painter.translate(-bounds.left(), -bounds.top())
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#FFFFFF"))
                painter.drawPath(glyphs)
            painter.end()
            icon.addPixmap(pixmap)
        return icon
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(
        QPen(
            QColor(color),
            5,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    path = QPainterPath(QPointF(20, 35))
    for x, y in [(29, 35), (35, 17), (43, 48), (50, 30), (60, 30)]:
        path.lineTo(x, y)
    painter.drawPath(path)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QPointF(8, 35), 4, 4)
    painter.end()
    return QIcon(pixmap)
