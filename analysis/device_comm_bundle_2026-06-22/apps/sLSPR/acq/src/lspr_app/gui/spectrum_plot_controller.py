from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPen

from lspr_app.gui.plot_controller import (
    autoscale_spectrum_plot,
    clip_series_to_window,
    downsample_spectrum_series_for_view,
    handle_spectrum_mouse_moved,
    spectrum_render_cache_key,
    update_spectrum_stats,
)

if TYPE_CHECKING:
    from lspr_app.gui.main_window import MainWindow


class ResidualViewBox(pg.ViewBox):
    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__()
        self._main_window = main_window

    def wheelEvent(self, event, axis=None) -> None:  # pragma: no cover - GUI runtime path
        if axis == 0:
            event.ignore()
            return
        angle_delta = getattr(event, "angleDelta", None)
        delta_y = angle_delta().y() if angle_delta is not None else 0
        if delta_y == 0 and hasattr(event, "delta"):
            delta_y = int(event.delta())
        if delta_y == 0:
            event.ignore()
            return
        if axis not in (None, 1):
            event.ignore()
            return
        view_range = self.viewRange()
        y_range = view_range[1]
        y_min = float(y_range[0])
        y_max = float(y_range[1])
        if not np.isfinite(y_min) or not np.isfinite(y_max) or y_max <= y_min:
            event.ignore()
            return
        factor = 1.12 if delta_y > 0 else 1 / 1.12
        center = (y_min + y_max) * 0.5
        half_span = max((y_max - y_min) * 0.5 * factor, 1e-9)
        self.setYRange(center - half_span, center + half_span, padding=0.0)
        self._manual_y_zoom = True
        self._main_window._residual_y_range = [center - half_span, center + half_span]
        self._main_window._residual_axis_autoscaled = True
        if hasattr(self._main_window, "_schedule_ui_state_persist"):
            self._main_window._schedule_ui_state_persist()
        event.accept()


def residual_color_for_value(value: float, scale: float) -> QColor:
    scale = max(float(scale), 1e-9)
    magnitude = min(abs(float(value)) / scale, 1.0)
    green = np.array([46, 125, 50], dtype=np.float64)
    amber = np.array([255, 202, 40], dtype=np.float64)
    red = np.array([211, 47, 47], dtype=np.float64)
    if magnitude <= 0.5:
        t = magnitude / 0.5
        rgb = (green * (1.0 - t)) + (amber * t)
    else:
        t = (magnitude - 0.5) / 0.5
        rgb = (amber * (1.0 - t)) + (red * t)
    return QColor(int(round(rgb[0])), int(round(rgb[1])), int(round(rgb[2])))


def _residual_pen_for_color(window, color: QColor) -> QPen:
    cache = getattr(window, "_residual_pen_cache", None)
    if cache is None:
        cache = {}
        window._residual_pen_cache = cache
    key = (int(color.red()), int(color.green()), int(color.blue()), int(color.alpha()))
    pen = cache.get(key)
    if pen is None:
        pen = pg.mkPen(color, width=1.5)
        cache[key] = pen
    return pen


def _ensure_residual_segment_item(window, index: int) -> pg.PlotCurveItem:
    segment_items = getattr(window, "_residual_segment_items", None)
    if segment_items is None:
        segment_items = []
        window._residual_segment_items = segment_items
    while len(segment_items) <= index:
        item = pg.PlotCurveItem()
        item.setVisible(False)
        window.residual_view.addItem(item)
        segment_items.append(item)
    return segment_items[index]


def _residual_visible_range(window) -> tuple[float, float] | None:
    if not hasattr(window, "residual_view"):
        return None
    try:
        current_range = window.residual_view.viewRange()[1]
    except Exception:
        return None
    if len(current_range) != 2:
        return None
    y_min = float(current_range[0])
    y_max = float(current_range[1])
    if not np.isfinite(y_min) or not np.isfinite(y_max) or y_max <= y_min:
        return None
    return y_min, y_max


def _downsample_residual_series_for_view(
    x: np.ndarray,
    y: np.ndarray,
    *,
    max_points: int = 192,
) -> tuple[np.ndarray, np.ndarray]:
    if len(x) <= max_points:
        return x, y
    if max_points < 2:
        max_points = 2
    indices = np.linspace(0, len(x) - 1, num=max_points, dtype=np.int64)
    indices = np.unique(indices)
    if len(indices) < 2:
        indices = np.asarray([0, len(x) - 1], dtype=np.int64)
    return x[indices], y[indices]


def clear_residual_display(window) -> None:
    window.residual_curve.setData([], [])
    for item in getattr(window, "_residual_segment_items", []):
        try:
            item.setData([], [])
            item.setVisible(False)
        except Exception:
            pass


def render_residual_display(window, x_values: np.ndarray, residual_values: np.ndarray) -> None:
    if len(x_values) == 0 or len(residual_values) == 0:
        clear_residual_display(window)
        return
    finite = np.isfinite(x_values) & np.isfinite(residual_values)
    if not np.any(finite):
        clear_residual_display(window)
        return
    x = np.asarray(x_values[finite], dtype=np.float64)
    y = np.asarray(residual_values[finite], dtype=np.float64)
    if len(x) == 0:
        clear_residual_display(window)
        return
    x, y = _downsample_residual_series_for_view(x, y)
    scale = float(np.percentile(np.abs(y), 95)) if len(y) > 1 else float(np.max(np.abs(y)))
    scale = max(scale, float(np.max(np.abs(y))), 1e-9)
    segment_count = max(len(x) - 1, 0)
    if len(x) >= 2:
        for idx in range(len(x) - 1):
            value_scale = max(abs(float(y[idx])), abs(float(y[idx + 1])))
            color = residual_color_for_value(value_scale, scale)
            segment = _ensure_residual_segment_item(window, idx)
            segment.setData(x=x[idx : idx + 2], y=y[idx : idx + 2])
            segment.setPen(_residual_pen_for_color(window, color))
            segment.setVisible(True)
        for idx in range(segment_count, len(getattr(window, "_residual_segment_items", []))):
            try:
                item = window._residual_segment_items[idx]
                item.setData([], [])
                item.setVisible(False)
            except Exception:
                pass
    window.residual_curve.setData(x=x, y=y)
    window.residual_curve.setPen(pg.mkPen("#8a8a8a", width=1.0))
    window.residual_curve.setSymbol(None)


def update_residual_view_geometry(window) -> None:
    if not hasattr(window, "residual_view"):
        return
    spectrum_vb = window.spectrum_plot.getPlotItem().vb
    window.residual_view.setGeometry(spectrum_vb.sceneBoundingRect())
    window.residual_view.linkedViewChanged(spectrum_vb, pg.ViewBox.XAxis)


def autoscale_residual_axis(window) -> None:
    if not hasattr(window, "residual_view") or not window.show_residual_button.isChecked():
        return
    if bool(getattr(window.residual_view, "_manual_y_zoom", False)):
        return
    if bool(getattr(window, "_residual_axis_autoscaled", False)):
        current_range = _residual_visible_range(window)
    else:
        current_range = None
    residual = window.residual_curve.yData
    if residual is None or len(residual) == 0:
        window.residual_view.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
        window._residual_axis_autoscaled = True
        return
    y = np.asarray(residual, dtype=np.float64)
    finite = np.isfinite(y)
    if not np.any(finite):
        return
    y = y[finite]
    amplitude = float(np.percentile(np.abs(y), 95))
    amplitude = max(amplitude, float(np.max(np.abs(y))), 1e-6)
    pad = max(amplitude * 0.2, 0.05)
    if not np.isfinite(pad):
        pad = 0.05
    target_half_span = min(amplitude + pad, 100.0)
    target_min = -target_half_span
    target_max = target_half_span
    should_update = current_range is None
    if current_range is not None:
        current_min, current_max = current_range
        current_half_span = max((current_max - current_min) * 0.5, 1e-9)
        current_center = (current_min + current_max) * 0.5
        if abs(current_center) > max(target_half_span * 0.15, 0.1):
            should_update = True
        elif current_half_span < target_half_span * 0.75 or current_half_span > target_half_span * 1.75:
            should_update = True
    if should_update:
        window.residual_view.setYRange(target_min, target_max, padding=0.0)
        window._residual_axis_autoscaled = True


def update_residual_axis_visibility(window, visible: bool | None = None) -> None:
    if visible is None:
        visible = window.show_residual_button.isChecked()
    if hasattr(window, "residual_axis"):
        window.residual_axis.setVisible(visible)
    if hasattr(window, "residual_view"):
        window.residual_view.setVisible(visible)
