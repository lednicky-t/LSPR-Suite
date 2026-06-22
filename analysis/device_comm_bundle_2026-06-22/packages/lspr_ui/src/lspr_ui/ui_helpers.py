from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap, QPen
from PyQt6.QtWidgets import QAbstractSpinBox, QSlider, QToolButton


def make_sim_slider(minimum: int, maximum: int, value: int) -> QSlider:
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(minimum, maximum)
    slider.setValue(value)
    return slider


def make_compact_spinbox(spinbox: QAbstractSpinBox, *, height: int = 28) -> QAbstractSpinBox:
    spinbox.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    spinbox.setFixedHeight(height)
    spinbox.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return spinbox


def make_info_button(tooltip: str) -> QToolButton:
    button = QToolButton()
    button.setObjectName("infoButton")
    button.setText("i")
    button.setToolTip(tooltip)
    button.setAutoRaise(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFixedSize(18, 18)
    button.setStyleSheet(
        "QToolButton#infoButton {"
        " border: 1px solid rgba(230, 235, 241, 0.22);"
        " border-radius: 9px;"
        " background-color: transparent;"
        " color: #e6ebf1;"
        " font-size: 11px;"
        " font-weight: 700;"
        " padding: 0px;"
        " margin: 0px;"
        "}"
        "QToolButton#infoButton:hover {"
        " background-color: rgba(255, 255, 255, 0.06);"
        "}"
        "QToolButton#infoButton:pressed {"
        " background-color: rgba(255, 255, 255, 0.10);"
        "}"
    )
    return button


def create_status_dot_icon(color: QColor) -> QIcon:
    pixmap = QPixmap(14, 14)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(QColor("#ffffff"), 1))
    painter.setBrush(color)
    painter.drawEllipse(2, 2, 10, 10)
    painter.end()
    return QIcon(pixmap)


def make_window_button(icon: QIcon, tooltip: str, slot) -> QToolButton:
    button = QToolButton()
    button.setObjectName("windowButton")
    button.setIcon(icon)
    button.setToolTip(tooltip)
    button.setAutoRaise(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setIconSize(QSize(18, 18))
    button.setFixedSize(28, 24)
    button.setContentsMargins(0, 0, 0, 0)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.clicked.connect(slot)
    return button


def window_control_icon(kind: str) -> QIcon:
    color = QColor("#f4f8fc")
    if kind in {"minimize", "maximize", "restore", "close"}:
        return _draw_window_control_icon(kind, color)
    raise ValueError(f"Unsupported window control icon kind: {kind}")


def _draw_window_control_icon(kind: str, color: QColor, *, size: int = 18) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "minimize":
        painter.drawLine(4, size - 5, size - 4, size - 5)
    elif kind == "maximize":
        painter.drawRect(4, 4, size - 8, size - 8)
    elif kind == "restore":
        painter.drawRect(5, 4, size - 9, size - 9)
        painter.drawRect(3, 6, size - 9, size - 9)
    elif kind == "close":
        painter.drawLine(4, 4, size - 4, size - 4)
        painter.drawLine(size - 4, 4, 4, size - 4)
    painter.end()
    return QIcon(pixmap)
