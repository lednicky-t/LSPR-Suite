from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap, QPen
from PyQt6.QtWidgets import QAbstractSpinBox, QSlider, QToolButton, QWidget


def make_sim_slider(minimum: int, maximum: int, value: int) -> QSlider:
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(minimum, maximum)
    slider.setValue(value)
    return slider


class CompactWedgeSlider(QWidget):
    """A compact opacity/transparency slider drawn as a filling wedge
    (triangle) instead of a standard QSlider track+handle - originally
    built for apps/LSPRi/eva's overlay-opacity controls
    (gui/widgets.py's own CompactWedgeSlider); shared here so other apps
    can reuse the same look instead of re-implementing it. LSPRi/eva's
    existing local copy is untouched - only new call sites should import
    this one.
    """

    valueChanged = pyqtSignal(int)

    def __init__(self, orientation: Qt.Orientation = Qt.Orientation.Horizontal, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orientation = orientation
        self._minimum = 0
        self._maximum = 100
        self._value = 0
        self.setMinimumSize(24, 12)
        self.setMaximumHeight(12)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Opacity")

    def setRange(self, minimum: int, maximum: int) -> None:
        self._minimum = int(minimum)
        self._maximum = max(int(maximum), self._minimum + 1)
        self.setValue(self._value)

    def setValue(self, value: int) -> None:
        clamped = int(np.clip(int(value), self._minimum, self._maximum))
        if clamped == self._value:
            self.update()
            return
        self._value = clamped
        self.valueChanged.emit(self._value)
        self.update()

    def value(self) -> int:
        return int(self._value)

    def sizeHint(self) -> QSize:
        return QSize(24, 12)

    def paintEvent(self, _event) -> None:  # pragma: no cover - GUI runtime path
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)

        base_path = QPainterPath()
        base_path.moveTo(rect.left(), rect.bottom())
        base_path.lineTo(rect.right(), rect.top())
        base_path.lineTo(rect.right(), rect.bottom())
        base_path.closeSubpath()
        painter.setPen(QPen(QColor("#475569"), 1.0))
        painter.setBrush(QColor("#0f172a"))
        painter.drawPath(base_path)

        fraction = 0.0 if self._maximum <= self._minimum else (self._value - self._minimum) / float(self._maximum - self._minimum)
        fraction = float(np.clip(fraction, 0.0, 1.0))
        fill_right = rect.left() + rect.width() * fraction
        if fill_right > rect.left() + 0.5:
            fill_path = QPainterPath()
            fill_path.moveTo(rect.left(), rect.bottom())
            fill_path.lineTo(fill_right, rect.bottom())
            fill_path.lineTo(fill_right, rect.bottom() - rect.height() * fraction)
            fill_path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#38bdf8"))
            painter.drawPath(fill_path)

        handle_x = rect.left() + rect.width() * fraction
        painter.setPen(QPen(QColor("#f8fafc"), 1.4))
        painter.drawLine(
            QPointF(handle_x, rect.bottom()),
            QPointF(handle_x, rect.bottom() - rect.height() * max(fraction, 0.18)),
        )
        painter.end()

    def _set_value_from_pos(self, position: QPointF) -> None:
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        if rect.width() <= 1.0:
            return
        fraction = (float(position.x()) - rect.left()) / rect.width()
        fraction = float(np.clip(fraction, 0.0, 1.0))
        self.setValue(int(round(self._minimum + fraction * (self._maximum - self._minimum))))

    def mousePressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_value_from_pos(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._set_value_from_pos(event.position())
            event.accept()
            return
        super().mouseMoveEvent(event)


class DualHandleRangeSlider(QWidget):
    """A horizontal slider with two draggable handles marking a [low, high]
    sub-range of an integer [minimum, maximum] span - e.g. "which spectral
    cubes/wavelengths to include" rather than a single value. Qt has no
    built-in range slider, so this fills that gap the same way
    CompactWedgeSlider above fills in for a shape QSlider can't draw.

    Callers typically pair this with two spin boxes (one per handle) kept in
    sync via valuesChanged, plus a "select all" button that calls
    setValues(minimum, maximum).
    """

    valuesChanged = pyqtSignal(int, int)

    _HANDLE_RADIUS = 7.0
    _GROOVE_HEIGHT = 4.0

    def __init__(self, orientation: Qt.Orientation = Qt.Orientation.Horizontal, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orientation = orientation
        self._minimum = 0
        self._maximum = 100
        self._low = 0
        self._high = 100
        self._dragging: str | None = None  # "low" | "high" | None
        self.setMinimumSize(60, 20)
        self.setFixedHeight(20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def setRange(self, minimum: int, maximum: int) -> None:
        self._minimum = int(minimum)
        self._maximum = max(int(maximum), self._minimum)
        self._low = int(np.clip(self._low, self._minimum, self._maximum))
        self._high = int(np.clip(self._high, self._minimum, self._maximum))
        if self._low > self._high:
            self._low = self._high
        self.update()

    def minimum(self) -> int:
        return int(self._minimum)

    def maximum(self) -> int:
        return int(self._maximum)

    def setValues(self, low: int, high: int) -> None:
        clamped_low = int(np.clip(int(low), self._minimum, self._maximum))
        clamped_high = int(np.clip(int(high), self._minimum, self._maximum))
        if clamped_low > clamped_high:
            clamped_low, clamped_high = clamped_high, clamped_low
        if clamped_low == self._low and clamped_high == self._high:
            return
        self._low = clamped_low
        self._high = clamped_high
        self.valuesChanged.emit(self._low, self._high)
        self.update()

    def values(self) -> tuple[int, int]:
        return int(self._low), int(self._high)

    def sizeHint(self) -> QSize:
        return QSize(140, 20)

    def _groove_rect(self) -> QRectF:
        margin = self._HANDLE_RADIUS + 1.0
        y = self.height() / 2.0
        return QRectF(margin, y - self._GROOVE_HEIGHT / 2.0, max(self.width() - 2 * margin, 1.0), self._GROOVE_HEIGHT)

    def _fraction_for_value(self, value: int) -> float:
        span = self._maximum - self._minimum
        if span <= 0:
            return 0.0
        return float(np.clip((value - self._minimum) / float(span), 0.0, 1.0))

    def _handle_x(self, value: int) -> float:
        groove = self._groove_rect()
        return groove.left() + groove.width() * self._fraction_for_value(value)

    def _value_for_x(self, x: float) -> int:
        groove = self._groove_rect()
        if groove.width() <= 0:
            return self._minimum
        fraction = float(np.clip((x - groove.left()) / groove.width(), 0.0, 1.0))
        return int(round(self._minimum + fraction * (self._maximum - self._minimum)))

    def paintEvent(self, _event) -> None:  # pragma: no cover - GUI runtime path
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        groove = self._groove_rect()
        center_y = groove.center().y()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#243041"))
        painter.drawRoundedRect(groove, groove.height() / 2.0, groove.height() / 2.0)

        low_x = self._handle_x(self._low)
        high_x = self._handle_x(self._high)
        fill_rect = QRectF(low_x, groove.top(), max(high_x - low_x, 0.0), groove.height())
        if fill_rect.width() > 0.5:
            painter.setBrush(QColor("#38bdf8"))
            painter.drawRoundedRect(fill_rect, groove.height() / 2.0, groove.height() / 2.0)

        for value in (self._low, self._high):
            handle_center = QPointF(self._handle_x(value), center_y)
            painter.setPen(QPen(QColor("#0b1220"), 1.4))
            painter.setBrush(QColor("#f8fafc"))
            painter.drawEllipse(handle_center, self._HANDLE_RADIUS, self._HANDLE_RADIUS)
        painter.end()

    def _handle_at(self, x: float) -> str:
        """Which handle a press near x should drag: the nearer one, with the
        high handle winning ties so a fully-collapsed range (low == high)
        can still be pulled back open from either side."""
        low_distance = abs(x - self._handle_x(self._low))
        high_distance = abs(x - self._handle_x(self._high))
        return "low" if low_distance < high_distance else "high"

    def mousePressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        x = float(event.position().x())
        self._dragging = self._handle_at(x)
        self._drag_to(x)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if self._dragging is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._drag_to(float(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        self._dragging = None
        super().mouseReleaseEvent(event)

    def _drag_to(self, x: float) -> None:
        value = self._value_for_x(x)
        if self._dragging == "low":
            self.setValues(min(value, self._high), self._high)
        elif self._dragging == "high":
            self.setValues(self._low, max(value, self._low))


def _content_based_minimum_width(spinbox: QAbstractSpinBox) -> int:
    """Widest text the box can ever show (its min/max, not just the current value),
    plus breathing room for the shared theme's ~1px border and ~5px side padding."""
    metrics = spinbox.fontMetrics()
    text_from_value = getattr(spinbox, "textFromValue", None)
    minimum = getattr(spinbox, "minimum", None)
    maximum = getattr(spinbox, "maximum", None)
    if callable(text_from_value) and callable(minimum) and callable(maximum):
        prefix = spinbox.prefix() if hasattr(spinbox, "prefix") else ""
        suffix = spinbox.suffix() if hasattr(spinbox, "suffix") else ""
        candidates = [
            f"{prefix}{text_from_value(minimum())}{suffix}",
            f"{prefix}{text_from_value(maximum())}{suffix}",
        ]
    else:
        candidates = [spinbox.text()]
    text_width = max((metrics.horizontalAdvance(text) for text in candidates), default=0)
    return text_width + 16


# By the time _apply_content_based_minimum_width runs, Qt's own style polish has
# typically already given the widget a frame-only minimum (border + padding, ~12px
# with this theme) even though no one asked for one - that's well below any minimum
# an app would deliberately set (40px+ in every real caller seen in this codebase),
# so treat only values above this threshold as "already defined otherwise."
_EXPLICIT_MINIMUM_WIDTH_THRESHOLD = 24


def _apply_content_based_minimum_width(spinbox: QAbstractSpinBox) -> None:
    if spinbox.minimumWidth() > _EXPLICIT_MINIMUM_WIDTH_THRESHOLD:
        return  # caller already set a real explicit minimum/fixed width - leave it alone
    try:
        spinbox.setMinimumWidth(_content_based_minimum_width(spinbox))
    except RuntimeError:
        pass  # widget was deleted before this deferred call ran


def make_compact_spinbox(spinbox: QAbstractSpinBox, *, height: int = 28) -> QAbstractSpinBox:
    spinbox.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    spinbox.setFixedHeight(height)
    spinbox.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    # Deferred so it runs after the caller finishes setRange()/setSuffix()/setPrefix() -
    # make_compact_spinbox() is conventionally called right after construction, before
    # those, so computing the minimum width immediately would measure Qt's default
    # 0-99 range instead of the field's real one.
    QTimer.singleShot(0, lambda: _apply_content_based_minimum_width(spinbox))
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
