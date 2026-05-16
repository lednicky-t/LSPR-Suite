from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap, QPen
from PyQt6.QtWidgets import QApplication, QAbstractSpinBox, QSlider, QStyle, QToolButton


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
    button.setIconSize(QSize(12, 12))
    button.setFixedSize(24, 18)
    button.clicked.connect(slot)
    return button


def window_control_icon(kind: str) -> QIcon:
    style = QApplication.style()
    color = QColor("#f4f8fc")
    if kind == "minimize":
        return _tint_icon(style.standardIcon(QStyle.StandardPixmap.SP_TitleBarMinButton), color)
    if kind == "maximize":
        return _tint_icon(style.standardIcon(QStyle.StandardPixmap.SP_TitleBarMaxButton), color)
    if kind == "restore":
        return _tint_icon(style.standardIcon(QStyle.StandardPixmap.SP_TitleBarNormalButton), color)
    if kind == "close":
        return _tint_icon(style.standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton), color)
    raise ValueError(f"Unsupported window control icon kind: {kind}")


def _tint_icon(icon: QIcon, color: QColor, *, size: int = 16) -> QIcon:
    pixmap = icon.pixmap(size, size)
    if pixmap.isNull():
        return icon
    tinted = QPixmap(pixmap.size())
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), color)
    painter.end()
    return QIcon(tinted)
