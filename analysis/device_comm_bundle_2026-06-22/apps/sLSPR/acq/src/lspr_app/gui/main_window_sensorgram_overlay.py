from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QFont, QFontMetricsF
from PyQt6.QtWidgets import QGraphicsRectItem

from lspr_app.gui.sensorgram_control_step_overlay import (
    build_sensorgram_control_step_overlay_segments,
    normalize_sensorgram_control_step_overlay_color,
    normalize_sensorgram_control_step_overlay_label,
    resolve_sensorgram_control_step_overlay_palette_label,
)


def handle_trace_view_range_changed(window, *_args) -> None:
    if window._trace_view_autoscaling:
        return
    window._trace_view_locked = True
    sync_overlay = getattr(window, "_sync_sensorgram_control_step_overlay", None)
    if callable(sync_overlay):
        try:
            sync_overlay()
        except Exception:
            pass


def sensorgram_control_step_overlay_current_elapsed_s(window) -> float | None:
    if window._measurement_started_at is not None and window._measurement_active:
        try:
            return max((datetime.now(timezone.utc) - window._measurement_started_at).total_seconds(), 0.0)
        except Exception:
            return None
    events = getattr(window, "_sensorgram_control_step_events", None)
    if isinstance(events, list) and events:
        last_event = events[-1]
        try:
            return max(float(last_event.get("elapsed_s", last_event.get("t_ms", 0.0) / 1000.0)), 0.0)
        except Exception:
            return None
    return None


def sensorgram_control_step_overlay_brush(window, segment: dict[str, object]) -> object:
    state = str(segment.get("state") or "").upper()
    color_text = str(segment.get("color") or "").strip()
    style = normalize_sensorgram_control_step_overlay_style(window, getattr(window, "_sensorgram_control_step_overlay_style", "bar"))
    base_color = QColor("#5fa8ff" if state == "RUN" else "#7f8a99")
    if color_text:
        candidate = QColor(color_text)
        if candidate.isValid():
            base_color = candidate
    if style == "background":
        opacity = max(min(float(getattr(window, "_sensorgram_control_step_overlay_opacity", 25.0)), 100.0), 0.0) / 100.0
        alpha = int(round(255 * opacity))
        if bool(segment.get("active")):
            alpha = min(alpha + 24, 255)
        base_color.setAlpha(alpha)
        return pg.mkBrush(base_color)
    if state in {"HOLD", "PAUSE"} and not color_text:
        base_color = QColor("#7f8a99")
    base_color.setAlpha(255)
    return pg.mkBrush(base_color)


def normalize_sensorgram_control_step_overlay_style(window, value: object | None = None) -> str:
    current = window._sensorgram_control_step_overlay_style if value is None else value
    normalized = str(current or "").strip().lower()
    return normalized if normalized in {"background", "bar"} else "bar"


def normalize_sensorgram_control_step_overlay_position(window, value: object | None = None) -> str:
    current = window._sensorgram_control_step_overlay_position if value is None else value
    normalized = str(current or "").strip().lower()
    return normalized if normalized in {"top", "bottom"} else "top"


def sensorgram_control_step_overlay_view_bounds(window) -> tuple[float, float, float, float] | None:
    trace_plot = getattr(window, "trace_plot", None)
    if trace_plot is None:
        return None
    plot_item = getattr(trace_plot, "getPlotItem", lambda: None)()
    if plot_item is None or getattr(plot_item, "vb", None) is None:
        return None
    try:
        x_range, y_range = plot_item.vb.viewRange()
        x_min = float(min(x_range[0], x_range[1]))
        x_max = float(max(x_range[0], x_range[1]))
        y_min = float(min(y_range[0], y_range[1]))
        y_max = float(max(y_range[0], y_range[1]))
    except Exception:
        return None
    if not all(np.isfinite(value) for value in (x_min, x_max, y_min, y_max)):
        return None
    if x_max <= x_min or y_max <= y_min:
        return None
    return x_min, x_max, y_min, y_max


def sensorgram_control_step_overlay_remove_items(window, trace_plot, items: list[object]) -> None:
    for item in list(items):
        try:
            if isinstance(item, dict):
                for value in item.values():
                    if hasattr(trace_plot, "removeItem") and hasattr(value, "scene"):
                        trace_plot.removeItem(value)
            else:
                trace_plot.removeItem(item)
        except Exception:
            pass
    items.clear()


def sensorgram_control_step_overlay_set_visible(item: object, visible: bool) -> None:
    if isinstance(item, dict):
        for value in item.values():
            if hasattr(value, "setVisible"):
                try:
                    value.setVisible(visible)
                except Exception:
                    pass
        return
    if hasattr(item, "setVisible"):
        try:
            item.setVisible(visible)
        except Exception:
            pass


def sensorgram_control_step_overlay_set_tooltip(item: object, tooltip: str) -> None:
    if isinstance(item, dict):
        for value in item.values():
            if hasattr(value, "setToolTip"):
                try:
                    value.setToolTip(tooltip)
                except Exception:
                    pass
        return
    if hasattr(item, "setToolTip"):
        try:
            item.setToolTip(tooltip)
        except Exception:
            pass


def sensorgram_control_step_overlay_clip_segment(
    window,
    segment: dict[str, object],
    x_min: float,
    x_max: float,
) -> dict[str, object] | None:
    start_s = max(float(segment.get("start_s", 0.0)), x_min)
    end_s = min(float(segment.get("end_s", 0.0)), x_max)
    if end_s <= start_s:
        return None
    clipped = dict(segment)
    clipped["start_s"] = start_s
    clipped["end_s"] = end_s
    return clipped


def sensorgram_control_step_overlay_bar_rect(
    window,
    *,
    segment: dict[str, object],
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
) -> QRectF:
    plot_item = getattr(getattr(window, "trace_plot", None), "getPlotItem", lambda: None)()
    pixel_size_y = 0.0
    try:
        if plot_item is not None and getattr(plot_item, "vb", None) is not None:
            pixel_size = plot_item.vb.viewPixelSize()
            pixel_size_y = abs(float(pixel_size[1]))
    except Exception:
        pixel_size_y = 0.0
    bar_height_px = max(int(getattr(window, "_sensorgram_control_step_overlay_bar_height_px", 8)), 1)
    bar_height_data = pixel_size_y * float(bar_height_px) if pixel_size_y > 0.0 else max((y_max - y_min) * 0.04, 1e-6)
    bar_height_data = max(bar_height_data, max((y_max - y_min) * 0.01, 1e-6))
    position = normalize_sensorgram_control_step_overlay_position(window)
    if position == "bottom":
        y0 = y_min
    else:
        y0 = y_max - bar_height_data
    y1 = y0 + bar_height_data
    x0 = max(float(segment.get("start_s", x_min)), x_min)
    x1 = min(float(segment.get("end_s", x_max)), x_max)
    if x1 <= x0:
        x1 = x0 + max((x_max - x_min) * 0.001, 1e-6)
    return QRectF(x0, y0, x1 - x0, y1 - y0)


def sensorgram_control_step_overlay_label_font(window) -> QFont:
    bar_height_px = max(int(getattr(window, "_sensorgram_control_step_overlay_bar_height_px", 18)), 1)
    font = QFont(window.font())
    font.setPixelSize(max(8, min(40, int(round(float(bar_height_px) * 0.66)))))
    font.setBold(False)
    return font


def sensorgram_control_step_overlay_text_color(window, color_text: str) -> QColor:
    color = QColor(str(color_text or "").strip())
    if not color.isValid():
        return QColor("#f8fbff")
    luminance = 0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF()
    return QColor("#0f1720" if luminance > 0.58 else "#f8fbff")


def sensorgram_control_step_overlay_reserved_y_padding(window, y_min: float, y_max: float) -> tuple[float, float]:
    enabled = bool(getattr(window, "_sensorgram_control_step_overlay_enabled", True))
    style = normalize_sensorgram_control_step_overlay_style(window, getattr(window, "_sensorgram_control_step_overlay_style", "bar"))
    content_mode = window._normalize_sensorgram_content_mode(getattr(window, "_sensorgram_content_mode", "metric"))
    if not enabled or style != "bar" or content_mode == "heatmap":
        return 0.0, 0.0
    bounds = sensorgram_control_step_overlay_view_bounds(window)
    if bounds is None:
        return 0.0, 0.0
    y_span = max(float(y_max) - float(y_min), 1e-9)
    plot_item = getattr(getattr(window, "trace_plot", None), "getPlotItem", lambda: None)()
    pixel_size_y = 0.0
    try:
        if plot_item is not None and getattr(plot_item, "vb", None) is not None:
            pixel_size = plot_item.vb.viewPixelSize()
            pixel_size_y = abs(float(pixel_size[1]))
    except Exception:
        pixel_size_y = 0.0
    bar_height_px = max(int(getattr(window, "_sensorgram_control_step_overlay_bar_height_px", 18)), 1)
    reserve = pixel_size_y * float(bar_height_px)
    if not np.isfinite(reserve) or reserve <= 0.0:
        reserve = y_span * 0.02
    reserve = max(reserve * 1.15, y_span * 0.02, 1e-6)
    position = normalize_sensorgram_control_step_overlay_position(window)
    if position == "bottom":
        return reserve, 0.0
    return 0.0, reserve


def record_sensorgram_control_step_event(window, payload: dict[str, object]) -> None:
    events = getattr(window, "_sensorgram_control_step_events", None)
    if not isinstance(events, list):
        events = []
        window._sensorgram_control_step_events = events
    try:
        elapsed_s = float(payload.get("t_ms", 0.0)) / 1000.0
    except Exception:
        elapsed_s = 0.0
    step_index = payload.get("step_index")
    try:
        step_index_value = max(int(float(step_index)), 0) if step_index not in {None, ""} else None
    except Exception:
        step_index_value = None
    state = str(payload.get("plan_state") or "").strip().upper()
    if state not in {"RUN", "HOLD", "PAUSE", "STOP"}:
        state = "STOP"
    color = normalize_sensorgram_control_step_overlay_color(payload.get("color", ""))
    label = normalize_sensorgram_control_step_overlay_label(
        payload.get("label", payload.get("description", "")),
        step_index=step_index_value,
    )
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if (not color or not label) and step_index_value is not None and experiment_control_window is not None:
        try:
            steps = experiment_control_window._read_experiment_control_steps()
        except Exception:
            steps = []
        if 1 <= step_index_value <= len(steps):
            step = steps[step_index_value - 1]
            if not color:
                color = normalize_sensorgram_control_step_overlay_color(getattr(step, "color", ""))
            if not label:
                label = resolve_sensorgram_control_step_overlay_palette_label(
                    color,
                    getattr(experiment_control_window, "_color_palette_entries", None),
                    step_index=step_index_value,
                )
    if not label and step_index_value == 0:
        label = normalize_sensorgram_control_step_overlay_label("Pause", step_index=step_index_value)
    if not color:
        palette = getattr(window, "SENSORGRAM_TIME_PLOT_COLORS", {})
        color = normalize_sensorgram_control_step_overlay_color(
            palette.get("smoothed_max", "#5fa8ff"),
            fallback="#5fa8ff",
        )
    if not label:
        label = normalize_sensorgram_control_step_overlay_label("", step_index=step_index_value)
    events.append(
        {
            "elapsed_s": max(float(elapsed_s), 0.0),
            "step_index": step_index_value,
            "state": state,
            "event": str(payload.get("event") or ""),
            "status": str(payload.get("status") or ""),
            "color": color,
            "label": label,
        }
    )


def sync_sensorgram_control_step_overlay(window) -> None:
    trace_plot = getattr(window, "trace_plot", None)
    if trace_plot is None:
        return
    items = getattr(window, "_sensorgram_control_step_overlay_items", None)
    if not isinstance(items, list):
        items = []
        window._sensorgram_control_step_overlay_items = items
    enabled = bool(getattr(window, "_sensorgram_control_step_overlay_enabled", True))
    style = normalize_sensorgram_control_step_overlay_style(window, getattr(window, "_sensorgram_control_step_overlay_style", "bar"))
    content_mode = window._normalize_sensorgram_content_mode(getattr(window, "_sensorgram_content_mode", "metric"))
    if not enabled or content_mode == "heatmap":
        for item in items:
            try:
                item.setVisible(False)
            except Exception:
                pass
        return
    events = getattr(window, "_sensorgram_control_step_events", [])
    if not isinstance(events, list) or not events:
        for item in items:
            try:
                item.setVisible(False)
            except Exception:
                pass
        return
    bounds = sensorgram_control_step_overlay_view_bounds(window)
    if bounds is None:
        for item in items:
            try:
                item.setVisible(False)
            except Exception:
                pass
        return
    x_min, x_max, y_min, y_max = bounds
    current_elapsed_s = sensorgram_control_step_overlay_current_elapsed_s(window)
    segments = build_sensorgram_control_step_overlay_segments(
        events,
        current_elapsed_s=current_elapsed_s,
    )
    if not segments:
        for item in items:
            try:
                item.setVisible(False)
            except Exception:
                pass
        return

    visible_segments: list[dict[str, object]] = []
    for segment in segments:
        clipped = sensorgram_control_step_overlay_clip_segment(window, segment, x_min, x_max)
        if clipped is not None:
            visible_segments.append(clipped)
    if not visible_segments:
        for item in items:
            try:
                item.setVisible(False)
            except Exception:
                pass
        return

    if items:
        current_style = getattr(items[0], "_sensorgram_control_step_overlay_style", None)
        if current_style != style:
            sensorgram_control_step_overlay_remove_items(window, trace_plot, items)

    while len(items) < len(visible_segments):
        if style == "background":
            region = pg.LinearRegionItem(
                values=(0.0, 0.0),
                orientation=pg.LinearRegionItem.Vertical,
                movable=False,
                brush=pg.mkBrush(0, 0, 0, 0),
                pen=pg.mkPen(None),
            )
            region._sensorgram_control_step_overlay_style = "background"
            region.setZValue(-30)
            region._diagnostics_owner = window
            region._diagnostics_prefix = "trace_plot"
            trace_plot.addItem(region, ignoreBounds=True)
            label_item = pg.TextItem(text="", anchor=(0.5, 0.5), color="#ffffff")
            label_item.setZValue(-29)
            label_item.setFont(sensorgram_control_step_overlay_label_font(window))
            label_item._diagnostics_owner = window
            label_item._diagnostics_prefix = "trace_plot"
            trace_plot.addItem(label_item, ignoreBounds=True)
            items.append({"bar": region, "label": label_item})
            continue

        rect_item = QGraphicsRectItem(0.0, 0.0, 0.0, 0.0)
        rect_item._sensorgram_control_step_overlay_style = "bar"
        rect_item.setPen(pg.mkPen(None))
        rect_item.setBrush(pg.mkBrush(0, 0, 0, 0))
        rect_item.setZValue(-31)
        rect_item._diagnostics_owner = window
        rect_item._diagnostics_prefix = "trace_plot"
        trace_plot.addItem(rect_item, ignoreBounds=True)

        label_item = pg.TextItem(text="", anchor=(0.5, 0.5), color="#ffffff")
        label_item.setZValue(-29)
        label_item.setFont(sensorgram_control_step_overlay_label_font(window))
        label_item._diagnostics_owner = window
        label_item._diagnostics_prefix = "trace_plot"
        trace_plot.addItem(label_item, ignoreBounds=True)

        items.append({"bar": rect_item, "label": label_item})

    for index, segment in enumerate(visible_segments):
        item = items[index]
        try:
            if style == "background":
                bar_item = item.get("bar") if isinstance(item, dict) else item
                tooltip = (
                    " | ".join(
                        part
                        for part in (
                            f"Step {int(segment['step_index'])}" if segment.get("step_index") is not None else "Step -",
                            str(segment.get("label") or "").strip(),
                            str(segment.get("state") or "").title(),
                            str(segment.get("event") or "").replace("_", " "),
                            str(segment.get("status") or "").strip(),
                        )
                        if part
                    )
                )
                if bar_item is not None:
                    bar_item.setRegion((float(segment["start_s"]), float(segment["end_s"])))
                    bar_item.setBrush(sensorgram_control_step_overlay_brush(window, segment))
                    bar_item.setToolTip(tooltip)
                    bar_item.setVisible(True)

            bar_item = item.get("bar") if isinstance(item, dict) else None
            label_item = item.get("label") if isinstance(item, dict) else None
            bar_rect = sensorgram_control_step_overlay_bar_rect(
                window,
                segment=segment,
                x_min=x_min,
                x_max=x_max,
                y_min=y_min,
                y_max=y_max,
            )
            tooltip = " | ".join(
                part
                for part in (
                    f"Step {int(segment['step_index'])}" if segment.get("step_index") is not None else "Step -",
                    str(segment.get("label") or "").strip(),
                    str(segment.get("state") or "").title(),
                    str(segment.get("event") or "").replace("_", " "),
                    str(segment.get("status") or "").strip(),
                )
                if part
            )
            if bar_item is not None:
                bar_item.setRect(bar_rect)
                bar_item.setBrush(sensorgram_control_step_overlay_brush(window, segment))
                bar_item.setToolTip(tooltip)
                bar_item.setVisible(True)
            if label_item is not None:
                label_text = str(segment.get("label") or "").strip()
                if not label_text and segment.get("step_index") is not None:
                    experiment_control_window = getattr(window, "_experiment_control_window", None)
                    if experiment_control_window is not None:
                        label_text = resolve_sensorgram_control_step_overlay_palette_label(
                            segment.get("color"),
                            getattr(experiment_control_window, "_color_palette_entries", None),
                            step_index=int(segment["step_index"]),
                        )
                    if not label_text:
                        label_text = f"Step {int(segment['step_index'])}"
                label_font = sensorgram_control_step_overlay_label_font(window)
                label_item.setFont(label_font)
                label_item.setToolTip(tooltip)
                if not label_text:
                    label_item.setVisible(False)
                    continue
                try:
                    plot_item = trace_plot.getPlotItem()
                    pixel_size = plot_item.vb.viewPixelSize() if plot_item is not None and getattr(plot_item, "vb", None) is not None else None
                    pixel_width = abs(float(pixel_size[0])) if pixel_size is not None else 0.0
                except Exception:
                    pixel_width = 0.0
                available_width = max(float(bar_rect.width()) / pixel_width - 8.0, 0.0) if pixel_width > 0.0 else 0.0
                if available_width < 40.0:
                    label_item.setVisible(False)
                    continue
                metrics = QFontMetricsF(label_font)
                fitted_text = metrics.elidedText(label_text, Qt.TextElideMode.ElideRight, int(available_width))
                label_item.setText(fitted_text)
                center_x = bar_rect.left() + bar_rect.width() / 2.0
                center_y = bar_rect.top() + bar_rect.height() / 2.0
                label_item.setPos(center_x, center_y)
                label_item.setColor(sensorgram_control_step_overlay_text_color(window, str(segment.get("color") or "")))
                label_item.setVisible(True)
        except Exception:
            sensorgram_control_step_overlay_set_visible(item, False)

    for item in items[len(visible_segments):]:
        sensorgram_control_step_overlay_set_visible(item, False)
