from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from html import escape
from time import perf_counter

from PyQt6.QtCore import QRectF

import numpy as np
import pyqtgraph as pg
from scipy.interpolate import CubicSpline

from lspr_app.domain.models import Spectrum
from lspr_app.gui.main_window_sensorgram import normalize_sensorgram_metric_y_axis_mode
from lspr_app.gui.plot_view_cache import (
    build_heatmap_arrays,
    build_heatmap_history_token,
    build_metric_series_token,
    derive_heatmap_levels_from_matrix,
    downsample_metric_series_for_view as _downsample_metric_series_for_view,
    expand_heatmap_levels,
    level_raw_weight,
    sample_absolute_metric_series_for_view,
    sample_absolute_heatmap_rows_for_view,
    select_heatmap_rows_for_view,
)


def _spline_render_series(x: np.ndarray, y: np.ndarray, *, max_points: int = 4096) -> tuple[np.ndarray, np.ndarray]:
    x_values = np.asarray(x, dtype=np.float64)
    y_values = np.asarray(y, dtype=np.float64)
    finite = np.isfinite(x_values) & np.isfinite(y_values)
    x_values = x_values[finite]
    y_values = y_values[finite]
    if len(x_values) < 4:
        return x_values, y_values
    unique_x, unique_indices = np.unique(x_values, return_index=True)
    y_values = y_values[unique_indices]
    x_values = unique_x
    if len(x_values) < 4:
        return x_values, y_values
    point_count = min(max(len(x_values) * 3, len(x_values)), max_points)
    if point_count <= len(x_values):
        return x_values, y_values
    spline = CubicSpline(x_values, y_values, bc_type="natural")
    dense_x = np.linspace(float(x_values[0]), float(x_values[-1]), point_count, dtype=np.float64)
    dense_y = spline(dense_x)
    return dense_x, np.asarray(dense_y, dtype=np.float64)


def _current_metric_view_state(window) -> tuple[float | None, float | None, float | None]:
    trace_plot = getattr(window, "trace_plot", None)
    if trace_plot is None:
        return None, None, None
    try:
        plot_item = trace_plot.getPlotItem()
        view_box = plot_item.vb
        view_range = view_box.viewRange()
        x_range = view_range[0]
        view_x_min = float(x_range[0])
        view_x_max = float(x_range[1])
        if not np.isfinite(view_x_min) or not np.isfinite(view_x_max):
            return None, None, None
        scene_rect = view_box.sceneBoundingRect()
        view_width_px = float(scene_rect.width()) if scene_rect is not None else None
        if view_width_px is not None and not np.isfinite(view_width_px):
            view_width_px = None
        return view_x_min, view_x_max, view_width_px
    except Exception:
        return None, None, None


def _sensorgram_time_axis_mode(window) -> str:
    mode = str(getattr(window, "_sensorgram_time_axis_mode", "elapsed") or "elapsed").strip().lower()
    return mode if mode in {"elapsed", "clock"} else "elapsed"


def _sensorgram_axis_start_datetime(window):
    for attr in ("_sensorgram_axis_started_at", "_measurement_started_at", "_live_trace_started_at", "_metric_archive_started_at"):
        started_at = getattr(window, attr, None)
        if started_at is None:
            continue
        if isinstance(started_at, datetime):
            return started_at
    return None


def _sensorgram_time_axis_clock_mode(window) -> bool:
    return _sensorgram_time_axis_mode(window) == "clock"


def _sensorgram_format_elapsed(seconds: float) -> str:
    total_seconds = max(int(round(float(seconds))), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _sensorgram_format_clock(window, elapsed_seconds: float) -> str:
    start_datetime = _sensorgram_axis_start_datetime(window)
    if start_datetime is None:
        return _sensorgram_format_elapsed(elapsed_seconds)
    try:
        clock_dt = start_datetime + timedelta(seconds=float(elapsed_seconds))
        return clock_dt.astimezone().strftime("%H:%M:%S")
    except Exception:
        return _sensorgram_format_elapsed(elapsed_seconds)


def _sensorgram_display_x_values(window, x_values: np.ndarray, *, clock_mode: bool) -> tuple[np.ndarray, bool]:
    return np.asarray(x_values, dtype=np.float64), bool(clock_mode)


def _set_trace_time_axis_mode(window, *, axis_mode: str) -> str:
    axis_mode = axis_mode if axis_mode in {"elapsed", "clock"} else "elapsed"
    axis = getattr(window, "trace_time_axis", None)
    if axis is not None and hasattr(axis, "set_time_mode"):
        axis.set_time_mode(axis_mode, start_datetime=_sensorgram_axis_start_datetime(window))
    if hasattr(window, "trace_plot"):
        window.trace_plot.setLabel("bottom", "Time (local)" if axis_mode == "clock" else "Elapsed time")
    return axis_mode


def _nearest_sorted_index(values: np.ndarray, target: float) -> int:
    if len(values) <= 1:
        return 0
    index = int(np.searchsorted(values, target))
    if index <= 0:
        return 0
    if index >= len(values):
        return len(values) - 1
    left_index = index - 1
    right_index = index
    left_distance = abs(float(values[left_index]) - target)
    right_distance = abs(float(values[right_index]) - target)
    return right_index if right_distance < left_distance else left_index


def _peak_preserving_downsample_indices(y: np.ndarray, target_bins: int) -> np.ndarray:
    if target_bins <= 0 or len(y) == 0:
        return np.empty(0, dtype=np.int64)
    if len(y) <= target_bins:
        return np.arange(len(y), dtype=np.int64)

    bins = max(int(target_bins), 1)
    edges = np.linspace(0, len(y), num=bins + 1, dtype=np.int64)
    keep: list[int] = []
    for start, stop in zip(edges[:-1], edges[1:]):
        if stop <= start:
            continue
        segment = y[start:stop]
        finite = np.isfinite(segment)
        if not np.any(finite):
            keep.append(start + (stop - start - 1) // 2)
            continue
        finite_segment = segment[finite]
        finite_positions = np.flatnonzero(finite) + start
        keep.append(int(finite_positions[int(np.argmin(finite_segment))]))
        keep.append(int(finite_positions[int(np.argmax(finite_segment))]))
    if not keep:
        return np.arange(len(y), dtype=np.int64)
    return np.unique(np.asarray(keep, dtype=np.int64))


def _curve_data_arrays(curve) -> tuple[np.ndarray, np.ndarray]:
    x_values = getattr(curve, "xData", None)
    y_values = getattr(curve, "yData", None)
    if x_values is None or y_values is None:
        data = getattr(curve, "data", None)
        if isinstance(data, tuple) and len(data) == 2:
            x_values, y_values = data
    if x_values is None or y_values is None:
        get_data = getattr(curve, "getData", None)
        if callable(get_data):
            try:
                x_values, y_values = get_data()
            except Exception:
                return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
    if x_values is None or y_values is None:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
    x = np.asarray(x_values, dtype=np.float64)
    y = np.asarray(y_values, dtype=np.float64)
    if len(x) == 0 or len(y) == 0:
        return x[:0], y[:0]
    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        return x[:0], y[:0]
    return x[finite], y[finite]


def _series_point_count(series: object) -> int:
    if series is None:
        return 0
    if hasattr(series, "to_arrays"):
        try:
            arrays = series.to_arrays()  # type: ignore[no-any-return]
            return int(len(arrays[0]))
        except Exception:
            return 0
    if isinstance(series, tuple) and len(series) == 2:
        try:
            return int(len(series[0]))
        except Exception:
            return 0
    try:
        return int(len(series))
    except Exception:
        return 0


def _format_hhmmss(seconds: float) -> str:
    total_seconds = max(int(round(float(seconds))), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _metric_compression_level_text(window, metric_name: str) -> str:
    plot_cache = getattr(window, "_plot_view_cache", None)
    if plot_cache is not None and bool(getattr(window, "_live_active", False)):
        live_cache_map = getattr(plot_cache, "_live_absolute_metric_cache", None)
        if isinstance(live_cache_map, dict):
            live_cache = live_cache_map.get(str(metric_name or "").strip())
            if live_cache is not None:
                try:
                    selected_level = int(getattr(live_cache, "last_display_level", -1))
                except (TypeError, ValueError):
                    selected_level = -1
                if selected_level >= 0:
                    try:
                        selected_weight = int(level_raw_weight(live_cache, selected_level))
                    except (TypeError, ValueError, Exception):
                        selected_weight = 0
                    if selected_weight > 0:
                        return f"L{selected_level} ({selected_weight} pts)"
                    return f"L{selected_level}"
    cache_modes = getattr(window, "_last_metric_view_cache_modes", None)
    if not isinstance(cache_modes, dict) or not cache_modes:
        return "-"
    metric_name = str(metric_name or "").strip()
    best_entry: dict[str, object] | None = None
    for key, entry in cache_modes.items():
        if not isinstance(entry, dict):
            continue
        if not str(key).startswith("absolute:"):
            continue
        key_text = str(key)
        if metric_name and metric_name not in key_text:
            continue
        if best_entry is None:
            best_entry = entry
            continue
        try:
            current_src = int(entry.get("source_points", entry.get("source_len", 0)) or 0)
            best_src = int(best_entry.get("source_points", best_entry.get("source_len", 0)) or 0)
        except (TypeError, ValueError):
            current_src = best_src = 0
        if current_src >= best_src:
            best_entry = entry
    if best_entry is None:
        return "-"
    try:
        selected_level = int(best_entry.get("selected_level", best_entry.get("display_level", -1)) or -1)
    except (TypeError, ValueError):
        selected_level = -1
    if selected_level < 0:
        return "-"
    try:
        selected_weight = int(best_entry.get("selected_level_raw_weight", 0) or 0)
    except (TypeError, ValueError):
        selected_weight = 0
    if selected_weight > 0:
        return f"L{selected_level} ({selected_weight} pts)"
    return f"L{selected_level}"


def _metric_plot_refresh_min_interval_ms(window) -> float:
    interval_ms = float(getattr(window, "_metric_plot_refresh_min_interval_ms", 250.0))
    last_trace_ms = getattr(window, "_last_deferred_ui_trace_plot_ms", None)
    if last_trace_ms is not None:
        try:
            last_trace_ms = float(last_trace_ms)
        except (TypeError, ValueError):
            last_trace_ms = None
    recent_trace_paint_ms = getattr(window, "_trace_plot_paint_recent_avg_ms", None)
    if recent_trace_paint_ms is not None:
        try:
            recent_trace_paint_ms = float(recent_trace_paint_ms)
        except (TypeError, ValueError):
            recent_trace_paint_ms = None
    pressure_ms = max(
        [
            value
            for value in (last_trace_ms, recent_trace_paint_ms)
            if value is not None and np.isfinite(value)
        ],
        default=0.0,
    )
    if pressure_ms >= 40.0:
        interval_ms = max(interval_ms, min(1000.0, pressure_ms * 4.0))
    return interval_ms


def _metric_display_target_points(window) -> int:
    return max(int(getattr(window, "_plot_display_points", 512)), 1)


def _metric_label_span(label: str, color: str, *, bold: bool = False) -> str:
    font_weight = "700" if bold else "600"
    return f"<span style='color: {escape(color)}; font-weight: {font_weight};'>{escape(label)}</span>"


def _set_plot_label_if_changed(plot, axis: str, text: str, window=None, cache_attr: str | None = None) -> None:
    if window is not None and cache_attr is not None:
        current = getattr(window, cache_attr, None)
        if current == text:
            owner = getattr(plot, "_diagnostics_owner", None)
            prefix = getattr(plot, "_diagnostics_prefix", "")
            if owner is not None and prefix:
                events = getattr(owner, "_plot_operation_events", None)
                if events is not None:
                    events.append((perf_counter(), prefix, "setLabel_skipped"))
            return
        setattr(window, cache_attr, text)
    plot.setLabel(axis, text)


def _set_visible_if_changed(widget, visible: bool) -> None:
    try:
        current_visible = bool(widget.isVisible()) if hasattr(widget, "isVisible") else None
        if current_visible is not None and current_visible == bool(visible):
            owner = getattr(widget, "_diagnostics_owner", None)
            prefix = getattr(widget, "_diagnostics_prefix", "")
            if owner is not None and prefix:
                events = getattr(owner, "_plot_operation_events", None)
                if events is not None:
                    events.append((perf_counter(), prefix, "setVisible_skipped"))
            return
        owner = getattr(widget, "_diagnostics_owner", None)
        prefix = getattr(widget, "_diagnostics_prefix", "")
        if owner is not None and prefix:
            events = getattr(owner, "_plot_operation_events", None)
            if events is not None:
                events.append((perf_counter(), prefix, "setVisible"))
        widget.setVisible(bool(visible))
    except Exception:
        try:
            widget.setVisible(bool(visible))
        except Exception:
            pass


def downsample_metric_series_for_view(
    x: np.ndarray,
    y: np.ndarray,
    *,
    view_x_min: float | None = None,
    view_x_max: float | None = None,
    view_width_px: float | None = None,
    enabled: bool = True,
    minimum_points: int = 128,
    oversample: float = 1.0,
    default_points: int = 2048,
) -> tuple[np.ndarray, np.ndarray]:
    return _downsample_metric_series_for_view(
        x,
        y,
        view_x_min=view_x_min,
        view_x_max=view_x_max,
        view_width_px=view_width_px,
        enabled=enabled,
        minimum_points=minimum_points,
        oversample=oversample,
        default_points=default_points,
    )


def downsample_spectrum_series_for_view(
    x: np.ndarray,
    y: np.ndarray,
    *,
    view_x_min: float | None = None,
    view_x_max: float | None = None,
    view_width_px: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    return downsample_metric_series_for_view(
        x,
        y,
        view_x_min=view_x_min,
        view_x_max=view_x_max,
        view_width_px=view_width_px,
        minimum_points=128,
        oversample=1.0,
        default_points=1024,
    )


def clip_series_to_window(
    x: np.ndarray,
    y: np.ndarray,
    *,
    window_min: float | None,
    window_max: float | None,
) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if len(x) == 0 or len(y) == 0:
        return x[:0], y[:0]
    if window_min is None or window_max is None or not np.isfinite(window_min) or not np.isfinite(window_max):
        return x, y
    low = float(window_min)
    high = float(window_max)
    if high <= low:
        return x[:0], y[:0]
    mask = np.isfinite(x) & np.isfinite(y) & (x >= low) & (x <= high)
    if not np.any(mask):
        return x[:0], y[:0]
    return x[mask], y[mask]


def spectrum_render_cache_key(
    processed: Spectrum | None,
    fit: Spectrum | None,
    *,
    view_x_min: float | None,
    view_x_max: float | None,
    view_width_px: float | None,
    residual_visible: bool,
    show_gaussian: bool,
    spectrum_tracking_mode: str,
) -> tuple[object, ...]:
    def _spec_key(spectrum: Spectrum | None) -> tuple[object, ...] | None:
        if spectrum is None:
            return None
        wavelengths = np.asarray(spectrum.wavelengths_nm, dtype=np.float64)
        values = np.asarray(spectrum.values, dtype=np.float64)
        finite_values = values[np.isfinite(values)]
        if len(finite_values) > 0:
            mid_index = len(finite_values) // 2
            content_signature = (
                int(len(finite_values)),
                float(finite_values[0]),
                float(finite_values[mid_index]),
                float(finite_values[-1]),
                float(np.nanmean(finite_values)),
                float(np.nanstd(finite_values)),
            )
        else:
            content_signature = (0, None)
        return (
            id(spectrum),
            spectrum.acquired_at,
            len(wavelengths),
            float(wavelengths[0]) if len(wavelengths) else None,
            float(wavelengths[-1]) if len(wavelengths) else None,
            content_signature,
            spectrum.y_label,
            spectrum.metadata.get("fit_method"),
            spectrum.metadata.get("fit_window_min_nm"),
            spectrum.metadata.get("fit_window_max_nm"),
        )

    return (
        _spec_key(processed),
        _spec_key(fit),
        round(view_x_min, 6) if view_x_min is not None else None,
        round(view_x_max, 6) if view_x_max is not None else None,
        round(view_width_px, 3) if view_width_px is not None else None,
        bool(residual_visible),
        bool(show_gaussian),
        spectrum_tracking_mode,
    )


def downsample_sensorgram_history_for_view(
    history: list[tuple[float, np.ndarray]],
    *,
    view_x_min: float | None = None,
    view_x_max: float | None = None,
    max_rows: int = 2000,
    view_height_px: float | None = None,
    oversample: float = 2.0,
    minimum_rows: int = 256,
    enabled: bool = True,
) -> list[tuple[float, np.ndarray]]:
    if not history:
        return history
    if view_x_min is not None and view_x_max is not None and view_x_max > view_x_min:
        visible = [item for item in history if view_x_min <= float(item[0]) <= view_x_max]
        if visible:
            history = visible
    if not enabled:
        return history
    if view_height_px is not None and view_height_px > 0:
        target_rows = max(minimum_rows, int(view_height_px * oversample))
        max_rows = min(max_rows, target_rows)
    if max_rows <= 0 or len(history) <= max_rows:
        return history

    indices = np.linspace(0, len(history) - 1, num=max_rows, dtype=np.int64)
    indices = np.unique(indices)
    return [history[int(index)] for index in indices]


def flush_deferred_ui_refreshes(window) -> None:
    flush_deferred_display_refreshes(window)
    flush_deferred_stats_refreshes(window)


def flush_deferred_display_refreshes(window) -> None:
    did_work = False
    refresh_state = getattr(window, "_ui_refresh_state", None)
    if getattr(refresh_state, "live_estimate_dirty", False):
        started = perf_counter()
        window._update_live_estimate()
        window._last_deferred_ui_live_estimate_ms = (perf_counter() - started) * 1000.0
        refresh_state.live_estimate_dirty = False
        did_work = True
    if getattr(refresh_state, "telemetry_dirty", False):
        started = perf_counter()
        window._refresh_telemetry()
        window._last_deferred_ui_telemetry_ms = (perf_counter() - started) * 1000.0
        refresh_state.telemetry_dirty = False
        did_work = True
    if getattr(refresh_state, "summary_dirty", False):
        started = perf_counter()
        window._refresh_session_summary()
        window._last_deferred_ui_summary_ms = (perf_counter() - started) * 1000.0
        refresh_state.summary_dirty = False
        did_work = True
    if did_work:
        window._log_throttled(
            "ui_flush",
            (
                "UI flush | trace_dirty="
                f"{int(bool(getattr(refresh_state, 'metric_plot_dirty', False)))} | "
                f"stats_dirty={int(bool(getattr(refresh_state, 'stats_dirty', False)))} | "
                f"display_rate={window.live_rate_spin.value():.2f} Hz"
            ),
            level=logging.DEBUG,
                min_interval=1.0,
            )


def flush_deferred_metric_refreshes(window) -> None:
    did_work = False
    refresh_state = getattr(window, "_ui_refresh_state", None)
    if getattr(refresh_state, "metric_plot_dirty", False) and not bool(getattr(window, "_sensorgram_frozen", False)):
        last_metric_refresh_at = getattr(window, "_last_metric_plot_refresh_at", None)
        throttle_ms = _metric_plot_refresh_min_interval_ms(window)
        if last_metric_refresh_at is not None:
            try:
                elapsed_since_metric_ms = max((perf_counter() - float(last_metric_refresh_at)) * 1000.0, 0.0)
            except (TypeError, ValueError):
                elapsed_since_metric_ms = None
        else:
            elapsed_since_metric_ms = None
        if elapsed_since_metric_ms is not None and elapsed_since_metric_ms < throttle_ms:
            remaining_ms = max(throttle_ms - elapsed_since_metric_ms, 1.0)
            if not window._ui_task_scheduler.is_pending("deferred_metric_flush"):
                window._ui_task_scheduler.request(
                    "deferred_metric_flush",
                    remaining_ms,
                    window._flush_deferred_metric_refreshes,
                    priority=0,
                    coalesce="latest",
                )
            window._last_metric_plot_refresh_throttled_ms = remaining_ms
        else:
            started = perf_counter()
            window._refresh_trace_plot(refresh_state.pending_metric_label or "Metric position (nm)")
            elapsed_ms = (perf_counter() - started) * 1000.0
            window._last_deferred_ui_trace_plot_ms = elapsed_ms
            window._last_metric_plot_refresh_at = perf_counter()
            refresh_state.metric_plot_dirty = False
            did_work = True
    if did_work:
        window._log_throttled(
            "ui_flush",
            (
                "UI flush | trace_dirty="
                f"{int(bool(getattr(refresh_state, 'metric_plot_dirty', False)))} | "
                f"stats_dirty={int(bool(getattr(refresh_state, 'stats_dirty', False)))} | "
                f"display_rate={window.live_rate_spin.value():.2f} Hz"
            ),
            level=logging.DEBUG,
            min_interval=1.0,
        )


def flush_deferred_stats_refreshes(window) -> None:
    did_work = False
    refresh_state = getattr(window, "_ui_refresh_state", None)
    if getattr(refresh_state, "stats_dirty", False):
        started = perf_counter()
        window._update_spectrum_stats(window._last_processed_plot, window._last_fit_plot)
        window._update_trace_stats()
        window._last_deferred_ui_stats_ms = (perf_counter() - started) * 1000.0
        refresh_state.stats_dirty = False
        did_work = True
    if did_work:
        window._log_throttled(
            "ui_flush",
            (
                "UI flush | trace_dirty="
                f"{int(bool(getattr(refresh_state, 'metric_plot_dirty', False)))} | "
                f"stats_dirty={int(bool(getattr(refresh_state, 'stats_dirty', False)))} | "
                f"display_rate={window.live_rate_spin.value():.2f} Hz"
            ),
            level=logging.DEBUG,
            min_interval=1.0,
        )


def flush_plot_refreshes(window) -> None:
    return


def refresh_plot(window) -> None:
    window._enqueue_plot_processing()


def autoscale_spectrum_plot(window) -> None:
    processed = window._last_processed_plot
    if processed is None or len(processed.wavelengths_nm) == 0:
        window.spectrum_plot.enableAutoRange(axis=pg.ViewBox.XYAxes, enable=True)
        window.spectrum_plot.autoRange()
        return
    x = np.asarray(processed.wavelengths_nm, dtype=np.float64)
    y = np.asarray(processed.values, dtype=np.float64)
    finite = np.isfinite(y)
    if not np.any(finite):
        return
    y = y[finite]
    x_finite = x[finite]
    y_span = float(np.max(y) - np.min(y))
    y_pad = max(y_span * 0.08, 1e-6)
    new_range = (
        float(np.min(x_finite)),
        float(np.max(x_finite)),
        float(np.min(y) - y_pad),
        float(np.max(y) + y_pad),
    )
    last_range = getattr(window, "_last_spectrum_autoscale_range", None)
    if isinstance(last_range, tuple) and len(last_range) == 4:
        try:
            old_x0, old_x1, old_y0, old_y1 = (float(v) for v in last_range)
            new_x0, new_x1, new_y0, new_y1 = new_range
            old_x_span = max(abs(old_x1 - old_x0), 1e-9)
            old_y_span = max(abs(old_y1 - old_y0), 1e-9)
            x_delta = max(abs(new_x0 - old_x0), abs(new_x1 - old_x1)) / old_x_span
            y_delta = max(abs(new_y0 - old_y0), abs(new_y1 - old_y1)) / old_y_span
            if x_delta < 0.01 and y_delta < 0.02:
                window._autoscale_residual_axis()
                return
        except Exception:
            pass
    window.spectrum_plot.setXRange(new_range[0], new_range[1], padding=0.0)
    window.spectrum_plot.setYRange(new_range[2], new_range[3], padding=0.0)
    window._last_spectrum_autoscale_range = new_range
    window._autoscale_residual_axis()


def _metric_follow_latest_buffer_s(
    window,
    *,
    clock_mode: bool,
    span_s: float | None = None,
) -> float:
    refresh_hz = float(getattr(window, "_actual_plot_refresh_rate_hz", 0.0) or 0.0)
    if not np.isfinite(refresh_hz) or refresh_hz <= 0.0:
        live_rate_spin = getattr(window, "live_rate_spin", None)
        if live_rate_spin is not None and hasattr(live_rate_spin, "value"):
            try:
                refresh_hz = float(live_rate_spin.value())
            except Exception:
                refresh_hz = 0.0
    if not np.isfinite(refresh_hz) or refresh_hz <= 0.0:
        refresh_hz = 4.0
    cadence_multiplier = float(getattr(window, "_metric_autoscale_follow_latest_cadence_multiplier", 1.5))
    min_buffer_s = float(getattr(window, "_metric_autoscale_follow_latest_min_buffer_s", 0.25))
    fraction_buffer = float(getattr(window, "_metric_autoscale_follow_latest_buffer_fraction", 0.05))
    if not np.isfinite(cadence_multiplier) or cadence_multiplier <= 0.0:
        cadence_multiplier = 1.5
    if not np.isfinite(min_buffer_s) or min_buffer_s <= 0.0:
        min_buffer_s = 0.25
    if not np.isfinite(fraction_buffer) or fraction_buffer < 0.0:
        fraction_buffer = 0.05
    buffer_s = max(min_buffer_s, cadence_multiplier / max(refresh_hz, 0.1))
    if span_s is not None:
        try:
            span_s = float(span_s)
        except (TypeError, ValueError):
            span_s = None
    if span_s is not None and np.isfinite(span_s) and span_s > 0.0:
        buffer_s = max(buffer_s, span_s * fraction_buffer)
    if clock_mode:
        return max(buffer_s, 0.25)
    return buffer_s


def _metric_autoscale_min_interval_from_mode(window) -> float:
    mode = str(getattr(window, "_metric_autoscale_throttle_mode", "Medium") or "Medium").strip().lower()
    mapping = {
        "off": 0.0,
        "light": 0.5,
        "medium": 1.0,
        "heavy": 2.0,
    }
    interval_s = mapping.get(mode, float(getattr(window, "_metric_autoscale_min_interval_s", 1.0)))
    return max(float(interval_s), 0.0)


def _sensorgram_metric_y_axis_step_size(window) -> float | None:
    mode = normalize_sensorgram_metric_y_axis_mode(getattr(window, "_sensorgram_metric_y_axis_mode", "auto"))
    return {
        "step_2nm": 2.0,
        "step_5nm": 5.0,
        "step_10nm": 10.0,
        "step_50nm": 50.0,
    }.get(mode)


def _sensorgram_metric_y_axis_is_auto(window) -> bool:
    return normalize_sensorgram_metric_y_axis_mode(getattr(window, "_sensorgram_metric_y_axis_mode", "auto")) == "auto"


def _quantize_sensorgram_metric_y_range(window, y_min: float, y_max: float) -> tuple[float, float]:
    step = _sensorgram_metric_y_axis_step_size(window)
    if step is None or not np.isfinite(step) or step <= 0.0:
        return y_min, y_max
    center = (float(y_min) + float(y_max)) * 0.5
    half_step = float(step) * 0.5
    lower = math.floor(center - half_step)
    upper = float(lower) + float(step)
    if not np.isfinite(lower) or not np.isfinite(upper):
        return y_min, y_max
    if center < float(lower) or center > float(upper):
        lower = math.floor(center - half_step)
        upper = float(lower) + float(step)
    if upper < float(y_max):
        lower = math.ceil(center - half_step)
        upper = float(lower) + float(step)
    return float(lower), float(upper)


def apply_processing_range_to_spectrum_plot(window) -> None:
    settings = window._current_processing_settings()
    low = float(min(settings.wavelength_min_nm, settings.wavelength_max_nm))
    high = float(max(settings.wavelength_min_nm, settings.wavelength_max_nm))
    if not np.isfinite(low) or not np.isfinite(high):
        return
    if high <= low:
        high = low + 1e-6
    window.spectrum_plot.setXRange(low, high, padding=0.0)
    window._spectrum_render_cache_key = None


def autoscale_metric_plot(window, *, force: bool = True) -> None:
    started = perf_counter()
    content_mode = getattr(window, "_sensorgram_content_mode", "metric")
    axis_mode = _sensorgram_time_axis_mode(window)
    clock_mode = axis_mode == "clock"
    metric_y_axis_auto = _sensorgram_metric_y_axis_is_auto(window)
    if content_mode == "heatmap":
        if bool(getattr(window, "_live_active", False)):
            _show_sensorgram_heatmap_unavailable(
                window,
                clock_mode=clock_mode,
            )
            window._metric_autoscale_pending = False
            window._last_metric_autoscale_ms = (perf_counter() - started) * 1000.0
            return
        window._trace_view_autoscaling = True
        try:
            render_sensorgram_heatmap(window, window._sensorgram_heatmap_history, clock_mode=clock_mode)
        finally:
            window._trace_view_autoscaling = False
            window._last_metric_autoscale_at = perf_counter()
        window._metric_autoscale_pending = False
        window._last_metric_autoscale_ms = (perf_counter() - started) * 1000.0
        return
    series = window._active_trace_series()
    if not series:
        window.trace_plot.enableAutoRange(axis=pg.ViewBox.XYAxes, enable=True)
        window.trace_plot.autoRange()
        window._metric_autoscale_pending = False
        return
    visible_series = [tuple(np.asarray(item, dtype=np.float64) for item in series_values) for series_values in series.values()]
    if not visible_series:
        window.trace_plot.enableAutoRange(axis=pg.ViewBox.XYAxes, enable=True)
        window.trace_plot.autoRange()
        window._metric_autoscale_pending = False
        return
    display_series: list[tuple[np.ndarray, np.ndarray]] = []
    for x_values, y_values in visible_series:
        display_x_values, _ = _sensorgram_display_x_values(window, x_values, clock_mode=clock_mode)
        display_series.append((display_x_values, y_values))
    x = np.concatenate([item[0] for item in display_series])
    y = np.concatenate([item[1] for item in visible_series])
    if bool(getattr(window, "_sensorgram_metric_envelope_overlay_enabled", False)):
        overlay_min_candidates: list[np.ndarray] = []
        overlay_max_candidates: list[np.ndarray] = []
        cache = getattr(window, "_plot_view_cache", None)
        if cache is not None:
            target_points = _metric_display_target_points(window)
            trace_view_locked = bool(getattr(window, "_trace_view_locked", False))
            live_active = bool(getattr(window, "_live_active", False))
            for metric_name in series.keys():
                try:
                    overlay_data = None
                    if not trace_view_locked and live_active and hasattr(cache, "live_absolute_metric_envelope_view"):
                        overlay_data = cache.live_absolute_metric_envelope_view(
                            metric_name,
                            target_points=target_points,
                            recent_tail_points=recent_tail_points,
                        )
                    elif not trace_view_locked and hasattr(cache, "absolute_metric_envelope_view"):
                        metric_series = series.get(metric_name)
                        if metric_series is not None:
                            series_token = build_metric_series_token(window, metric_name)
                            mx, my = tuple(np.asarray(item, dtype=np.float64) for item in metric_series)
                            overlay_data = cache.absolute_metric_envelope_view(
                                series_token,
                                mx,
                                my,
                                view_width_px=None,
                                enabled=True,
                                minimum_points=128,
                                oversample=1.0,
                                default_points=target_points,
                            )
                    if overlay_data is not None:
                        min_x, min_y, max_x, max_y = overlay_data
                        if len(min_y) > 0:
                            overlay_min_candidates.append(np.asarray(min_y, dtype=np.float64))
                        if len(max_y) > 0:
                            overlay_max_candidates.append(np.asarray(max_y, dtype=np.float64))
                except Exception:
                    continue
    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        window._metric_autoscale_pending = False
        return
    x = x[finite]
    y = y[finite]
    latest_x = float(np.max(x))
    x_span = float(np.max(x) - np.min(x))
    y_min = float(np.min(y))
    y_max = float(np.max(y))
    if bool(getattr(window, "_sensorgram_metric_envelope_overlay_enabled", False)):
        if overlay_min_candidates:
            overlay_min_values = np.concatenate([arr[np.isfinite(arr)] for arr in overlay_min_candidates if np.any(np.isfinite(arr))]) if any(np.any(np.isfinite(arr)) for arr in overlay_min_candidates) else np.empty(0, dtype=np.float64)
        else:
            overlay_min_values = np.empty(0, dtype=np.float64)
        if overlay_max_candidates:
            overlay_max_values = np.concatenate([arr[np.isfinite(arr)] for arr in overlay_max_candidates if np.any(np.isfinite(arr))]) if any(np.any(np.isfinite(arr)) for arr in overlay_max_candidates) else np.empty(0, dtype=np.float64)
        else:
            overlay_max_values = np.empty(0, dtype=np.float64)
        if len(overlay_min_values) > 0:
            overlay_min = float(np.min(overlay_min_values))
            if np.isfinite(overlay_min):
                if y_min <= 0.0:
                    y_min = min(y_min, overlay_min)
                elif overlay_min > 0.0:
                    y_min = min(y_min, overlay_min)
        if len(overlay_max_values) > 0:
            overlay_max = float(np.max(overlay_max_values))
            if np.isfinite(overlay_max):
                y_max = max(y_max, overlay_max)
    y_span = float(y_max - y_min)
    clock_mode = bool(getattr(window.trace_time_axis, "_mode", "elapsed") == "clock")
    follow_latest_buffer_s = _metric_follow_latest_buffer_s(
        window,
        clock_mode=clock_mode,
        span_s=x_span,
    )
    last_range = getattr(window, "_last_metric_autoscale_range", None)
    if not force:
        last_autoscale_at = float(getattr(window, "_last_metric_autoscale_at", 0.0))
        min_interval_s = _metric_autoscale_min_interval_from_mode(window)
        if last_autoscale_at > 0.0 and (perf_counter() - last_autoscale_at) < min_interval_s:
            must_follow_latest = False
            if isinstance(last_range, tuple) and len(last_range) == 4:
                try:
                    must_follow_latest = latest_x + follow_latest_buffer_s >= float(last_range[1])
                except Exception:
                    must_follow_latest = True
            if not must_follow_latest:
                window._metric_autoscale_pending = False
                window._last_metric_autoscale_ms = (perf_counter() - started) * 1000.0
                return
    x_min = float(np.min(x))
    x_max = latest_x + follow_latest_buffer_s
    if getattr(window, "_trace_view_locked", False):
        window._metric_autoscale_pending = False
        return
    current_y_range = None
    if not metric_y_axis_auto:
        try:
            current_y_range = window.trace_plot.getViewBox().viewRange()[1]
        except Exception:
            current_y_range = None
    if metric_y_axis_auto:
        y_pad = max(y_span * 0.12, 1e-6)
        overlay_y_pad_low = 0.0
        overlay_y_pad_high = 0.0
        reserved_y_padding = getattr(window, "_sensorgram_control_step_overlay_reserved_y_padding", None)
        if callable(reserved_y_padding):
            try:
                overlay_y_pad_low, overlay_y_pad_high = reserved_y_padding(float(y_min), float(y_max))
            except Exception:
                overlay_y_pad_low, overlay_y_pad_high = 0.0, 0.0
        y_low = float(y_min - y_pad - overlay_y_pad_low)
        y_high = float(y_max + y_pad + overlay_y_pad_high)
        y_low, y_high = _quantize_sensorgram_metric_y_range(window, y_low, y_high)
    else:
        y_low = y_high = None
        if isinstance(current_y_range, (list, tuple)) and len(current_y_range) == 2:
            try:
                candidate_low = float(current_y_range[0])
                candidate_high = float(current_y_range[1])
                if np.isfinite(candidate_low) and np.isfinite(candidate_high) and candidate_high > candidate_low:
                    y_low, y_high = candidate_low, candidate_high
            except Exception:
                y_low = y_high = None
        if y_low is None or y_high is None:
            y_pad = max(y_span * 0.12, 1e-6)
            y_low = float(y_min - y_pad)
            y_high = float(y_max + y_pad)
    new_range = (
        float(x_min),
        float(x_max),
        float(y_low),
        float(y_high),
    )
    should_apply = True
    if isinstance(last_range, tuple) and len(last_range) == 4:
        try:
            tiny_changes_enabled = bool(getattr(window, "_metric_autoscale_skip_tiny_changes_enabled", True))
            if tiny_changes_enabled:
                old_x0, old_x1, old_y0, old_y1 = (float(v) for v in last_range)
                new_x0, new_x1, new_y0, new_y1 = new_range
                old_x_span = max(abs(old_x1 - old_x0), 1e-9)
                old_y_span = max(abs(old_y1 - old_y0), 1e-9)
                x_delta = max(abs(new_x0 - old_x0), abs(new_x1 - old_x1)) / old_x_span
                y_delta = max(abs(new_y0 - old_y0), abs(new_y1 - old_y1)) / old_y_span
                x_threshold = float(getattr(window, "_metric_autoscale_x_change_fraction", 0.01))
                y_threshold = float(getattr(window, "_metric_autoscale_y_change_fraction", 0.02))
                should_apply = x_delta >= x_threshold or y_delta >= y_threshold or latest_x + follow_latest_buffer_s >= old_x1
        except Exception:
            should_apply = True
    if not should_apply:
        window._metric_autoscale_pending = False
        window._last_metric_autoscale_ms = (perf_counter() - started) * 1000.0
        return
    window._trace_view_autoscaling = True
    try:
        window.trace_plot.setXRange(new_range[0], new_range[1], padding=0.0)
        window.trace_plot.setYRange(new_range[2], new_range[3], padding=0.0)
        window._last_metric_autoscale_range = new_range
    finally:
        window._trace_view_autoscaling = False
        window._last_metric_autoscale_at = perf_counter()
        window._metric_autoscale_pending = False
        window._last_metric_autoscale_ms = (perf_counter() - started) * 1000.0
        if bool(getattr(window, "_deep_timing_enabled", False)):
            elapsed_ms = (perf_counter() - started) * 1000.0
            if elapsed_ms >= 2.0:
                window._log_throttled(
                    "metric_autoscale",
                    f"Metric autoscale: {elapsed_ms:.2f} ms | points={len(x)}",
                    level=logging.INFO,
                    min_interval=0.5,
                )


def update_residual_view_geometry(window) -> None:
    if not hasattr(window, "residual_view"):
        return
    spectrum_vb = window.spectrum_plot.getPlotItem().vb
    window.residual_view.setGeometry(spectrum_vb.sceneBoundingRect())
    window.residual_view.linkedViewChanged(spectrum_vb, pg.ViewBox.XAxis)


def autoscale_residual_axis(window) -> None:
    if not hasattr(window, "residual_view") or not window.show_residual_button.isChecked():
        return
    if bool(getattr(window, "_residual_axis_autoscaled", False)):
        return
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
    amplitude = max(amplitude, float(np.max(np.abs(y))), 1e-9)
    pad = max(amplitude * 0.15, 1e-6)
    if not np.isfinite(pad):
        pad = 1e-6
    half_span = min(amplitude + pad, 100.0)
    window.residual_view.setYRange(-half_span, half_span, padding=0.0)
    window._residual_axis_autoscaled = True


def update_residual_axis_visibility(window, visible: bool | None = None) -> None:
    if visible is None:
        visible = window.show_residual_button.isChecked()
    if hasattr(window, "residual_axis"):
        window.residual_axis.setVisible(visible)
    if hasattr(window, "residual_view"):
        window.residual_view.setVisible(visible)


def request_metric_autoscale(window) -> None:
    if bool(getattr(window, "_sensorgram_frozen", False)):
        return
    window._metric_autoscale_pending = True
    timer = getattr(window, "_trace_autoscale_timer", None)
    if timer is None:
        return
    if timer.isActive():
        return
    timer.start()


def refresh_metric_plot(window, trace_label: str) -> None:
    if window._plots_frozen:
        return
    started = perf_counter()
    axis_mode = _sensorgram_time_axis_mode(window)
    clock_mode = axis_mode == "clock"
    content_mode = getattr(window, "_sensorgram_content_mode", "metric")
    metric_plot_enabled = bool(getattr(window, "_metric_plot_enabled", True))
    window._last_metric_plot_disabled_fast_path = False
    if content_mode != "heatmap":
        window._last_sensorgram_heatmap_render_ms = None
    try:
        if not metric_plot_enabled:
            _show_metric_plot_unavailable(window, clock_mode)
            window._last_metric_plot_disabled_fast_path = True
            sync_control_step_overlay = getattr(window, "_sync_sensorgram_control_step_overlay", None)
            if callable(sync_control_step_overlay):
                try:
                    sync_control_step_overlay()
                except Exception:
                    pass
            return
        active_series = window._active_trace_series()
        if content_mode == "heatmap":
            _set_plot_label_if_changed(window.trace_plot, "left", "Wavelength (nm)", window, "_trace_left_label_text")
            _set_trace_time_axis_mode(window, axis_mode=axis_mode)
            for curve in window.trace_curves.values():
                if hasattr(curve, "setData"):
                    curve.setData([], [])
                _set_visible_if_changed(curve, False)
            for band in getattr(window, "trace_metric_envelope_bands", {}).values():
                if hasattr(band, "setVisible"):
                    band.setVisible(False)
            if hasattr(window, "trace_legend"):
                _set_visible_if_changed(window.trace_legend, False)
            render_sensorgram_heatmap(window, window._sensorgram_heatmap_history, clock_mode=clock_mode)
            if _sensorgram_metric_y_axis_is_auto(window):
                request_metric_autoscale(window)
            sync_control_step_overlay = getattr(window, "_sync_sensorgram_control_step_overlay", None)
            if callable(sync_control_step_overlay):
                try:
                    sync_control_step_overlay()
                except Exception:
                    pass
            return
        if active_series:
            _set_plot_label_if_changed(window.trace_plot, "left", trace_label, window, "_trace_left_label_text")
            previous_visible_mode = getattr(window, "_visible_trace_mode", None)
            visible_mode = _set_trace_time_axis_mode(window, axis_mode=axis_mode)
            metric_changed = render_metric_series(window, active_series, clock_mode=clock_mode)
            window._visible_trace_mode = visible_mode
            _set_visible_if_changed(window.trace_heatmap_image, False)
            if hasattr(window, "trace_heatmap_notice_item"):
                _set_visible_if_changed(window.trace_heatmap_notice_item, False)
            if hasattr(window, "trace_legend"):
                _set_visible_if_changed(window.trace_legend, True)
            for curve in window.trace_curves.values():
                _set_visible_if_changed(curve, True)
            if (metric_changed or previous_visible_mode != visible_mode) and _sensorgram_metric_y_axis_is_auto(window):
                request_metric_autoscale(window)
            sync_control_step_overlay = getattr(window, "_sync_sensorgram_control_step_overlay", None)
            if callable(sync_control_step_overlay):
                try:
                    sync_control_step_overlay()
                except Exception:
                    pass
            return

        if content_mode == "heatmap":
            _set_plot_label_if_changed(window.trace_plot, "left", "Wavelength (nm)", window, "_trace_left_label_text")
        else:
            _set_plot_label_if_changed(window.trace_plot, "left", trace_label, window, "_trace_left_label_text")
        _set_trace_time_axis_mode(window, axis_mode=axis_mode)
        for curve in window.trace_curves.values():
            curve.setData([], [])
            _set_visible_if_changed(curve, content_mode != "heatmap")
        for band in getattr(window, "trace_metric_envelope_bands", {}).values():
            if hasattr(band, "setVisible"):
                band.setVisible(False)
        if hasattr(window, "trace_heatmap_image"):
            _set_visible_if_changed(window.trace_heatmap_image, False)
        if hasattr(window, "trace_heatmap_notice_item"):
            _set_visible_if_changed(window.trace_heatmap_notice_item, False)
        if hasattr(window, "trace_legend"):
            _set_visible_if_changed(window.trace_legend, content_mode != "heatmap")
        window._visible_trace_x = None
        window._visible_trace_y = None
        window._visible_trace_mode = axis_mode
        if _sensorgram_metric_y_axis_is_auto(window):
            request_metric_autoscale(window)
        window._log_throttled(
            "trace_refresh",
            f"Metric updated | {trace_label}",
            level=logging.DEBUG,
            min_interval=1.5,
        )
        sync_control_step_overlay = getattr(window, "_sync_sensorgram_control_step_overlay", None)
        if callable(sync_control_step_overlay):
            try:
                sync_control_step_overlay()
            except Exception:
                pass
    finally:
        window._last_sensorgram_render_ms = (perf_counter() - started) * 1000.0


def render_metric_series(
    window,
    history: dict[str, tuple[np.ndarray, np.ndarray] | list[tuple[object, float]]],
    clock_mode: bool,
) -> bool:
    started = perf_counter()
    series_export_ms = 0.0
    view_prep_ms = 0.0
    setdata_ms = 0.0
    setdata_calls = 0
    setdata_skips = 0
    display_points = 0
    raw_points = 0

    def _trace_x_value(raw_x: object) -> float:
        if hasattr(raw_x, "timestamp"):
            return float(raw_x.timestamp())
        return float(raw_x)

    def _series_to_arrays(series: tuple[np.ndarray, np.ndarray] | list[tuple[object, float]]) -> tuple[np.ndarray, np.ndarray]:
        if isinstance(series, tuple) and len(series) == 2:
            return np.asarray(series[0], dtype=np.float64), np.asarray(series[1], dtype=np.float64)
        if not series:
            return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
        return (
            np.asarray([_trace_x_value(item[0]) for item in series], dtype=np.float64),
            np.asarray([item[1] for item in series], dtype=np.float64),
        )

    trace_view_locked = bool(getattr(window, "_trace_view_locked", False))
    _, _, view_width_px = _current_metric_view_state(window)
    view_x_min = view_x_max = None
    if trace_view_locked:
        view_x_min, view_x_max, view_width_px = _current_metric_view_state(window)
    active_series = {}
    render_state_cache = getattr(window, "_metric_render_state_cache", None)
    if not isinstance(render_state_cache, dict):
        render_state_cache = {}
        window._metric_render_state_cache = render_state_cache
    downsampling_enabled = True
    step_mode = getattr(window, "_sensorgram_line_step_mode", None)
    curve_downsampling_factor = 1
    cache = getattr(window, "_plot_view_cache", None)
    live_absolute_source_used = "-"
    live_absolute_invalidation_reason = "-"
    live_absolute_archive_points = 0
    live_absolute_rebuild_count = 0
    live_absolute_archive_reads = 0
    live_absolute_append_ms = None
    recent_tail_points = max(int(getattr(window, "_sensorgram_compression_recent_tail_points", 300)), 0)
    envelope_bands = getattr(window, "trace_metric_envelope_bands", None)
    overlay_enabled = bool(getattr(window, "_sensorgram_metric_envelope_overlay_enabled", False))

    def _update_metric_envelope_overlay(
        metric_name: str,
        overlay_data: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None,
    ) -> None:
        if not isinstance(envelope_bands, dict):
            return
        band = envelope_bands.get(metric_name)
        envelope_curves = getattr(window, "trace_metric_envelope_curves", None)
        curve_pair = None
        if isinstance(envelope_curves, dict):
            curve_pair = envelope_curves.get(metric_name)
        if overlay_data is None or not overlay_enabled:
            if band is not None:
                if hasattr(band, "setVisible"):
                    band.setVisible(False)
            if curve_pair is not None:
                for curve in curve_pair:
                    if hasattr(curve, "setData"):
                        curve.setData([], [])
            return
        min_x, min_y, max_x, max_y = overlay_data
        if len(min_x) == 0 or len(min_y) == 0 or len(max_x) == 0 or len(max_y) == 0:
            if band is not None:
                if hasattr(band, "setVisible"):
                    band.setVisible(False)
            if curve_pair is not None:
                for curve in curve_pair:
                    if hasattr(curve, "setData"):
                        curve.setData([], [])
            return
        if curve_pair is not None:
            min_curve, max_curve = curve_pair
            if hasattr(min_curve, "setData"):
                min_curve.setData(min_x, min_y)
            if hasattr(max_curve, "setData"):
                max_curve.setData(max_x, max_y)
        if band is not None and hasattr(band, "setVisible"):
            band.setVisible(True)

    for metric_name, curve in window.trace_curves.items():
        series = history.get(metric_name, [])
        if metric_name not in window._selected_trace_metrics() or series is None:
            curve.setDownsampling(auto=False, ds=curve_downsampling_factor)
            render_state_key = (metric_name, "empty", downsampling_enabled)
            if render_state_cache.get(metric_name) != render_state_key:
                curve.setData([], [])
                render_state_cache[metric_name] = render_state_key
            _update_metric_envelope_overlay(metric_name, None)
            continue
        raw_points += _series_point_count(series)
        series_started = perf_counter()
        x, y = _series_to_arrays(series)
        series_export_ms += (perf_counter() - series_started) * 1000.0
        if len(x) == 0 or len(y) == 0:
            curve.setDownsampling(auto=False, ds=curve_downsampling_factor)
            render_state_key = (metric_name, "empty", downsampling_enabled)
            if render_state_cache.get(metric_name) != render_state_key:
                curve.setData([], [])
                render_state_cache[metric_name] = render_state_key
            _update_metric_envelope_overlay(metric_name, None)
            continue
        series_token = build_metric_series_token(window, metric_name)
        curve.setDownsampling(auto=False, ds=curve_downsampling_factor)
        display_x = x
        display_y = y
        overlay_data = None
        clock_offset_s = None
        view_started = perf_counter()
        view_mode = "absolute"
        if cache is not None and view_mode == "absolute" and not trace_view_locked and bool(getattr(window, "_live_active", False)):
            live_display = cache.live_absolute_metric_view(
                metric_name,
                target_points=_metric_display_target_points(window),
                recent_tail_points=recent_tail_points,
            )
            if live_display is None:
                cache.seed_live_absolute_metric_cache(
                    metric_name,
                    x,
                    y,
                    target_points=_metric_display_target_points(window),
                    recent_tail_points=recent_tail_points,
                )
                live_display = cache.live_absolute_metric_view(
                    metric_name,
                    target_points=_metric_display_target_points(window),
                    recent_tail_points=recent_tail_points,
                )
            if live_display is not None:
                display_x, display_y = live_display
            else:
                display_x, display_y = x, y
            live_cache = getattr(cache, "_live_absolute_metric_cache", {}).get(metric_name)
            if live_cache is not None:
                live_absolute_source_used = str(getattr(live_cache, "last_source_used", live_absolute_source_used))
                live_absolute_invalidation_reason = str(getattr(live_cache, "last_invalidation_reason", live_absolute_invalidation_reason))
                live_absolute_archive_points += int(getattr(live_cache, "last_archive_points", 0))
                live_absolute_rebuild_count += int(getattr(live_cache, "rebuild_count", 0))
                append_ms = getattr(live_cache, "last_append_ms", None)
                if append_ms is not None and np.isfinite(float(append_ms)):
                        live_absolute_append_ms = (
                            float(append_ms)
                            if live_absolute_append_ms is None
                            else max(float(live_absolute_append_ms), float(append_ms))
                        )
            if overlay_enabled and cache is not None:
                try:
                    overlay_data = cache.live_absolute_metric_envelope_view(
                        metric_name,
                        target_points=_metric_display_target_points(window),
                        recent_tail_points=recent_tail_points,
                    )
                except Exception:
                    overlay_data = None
        elif cache is not None:
            if view_mode == "absolute" and not trace_view_locked:
                display_x, display_y = cache.absolute_metric_view(
                    series_token,
                    x,
                    y,
                    view_width_px=view_width_px,
                    enabled=downsampling_enabled,
                    minimum_points=128,
                    oversample=1.0,
                    default_points=_metric_display_target_points(window),
                    recent_tail_points=recent_tail_points,
                )
                if overlay_enabled:
                    try:
                        overlay_data = cache.absolute_metric_envelope_view(
                            series_token,
                            x,
                            y,
                            view_width_px=view_width_px,
                            enabled=downsampling_enabled,
                            minimum_points=128,
                            oversample=1.0,
                            default_points=_metric_display_target_points(window),
                            recent_tail_points=recent_tail_points,
                        )
                    except Exception:
                        overlay_data = None
            else:
                display_x, display_y = cache.metric_view(
                    series_token,
                    x,
                    y,
                    view_x_min=view_x_min,
                    view_x_max=view_x_max,
                    view_width_px=view_width_px,
                    enabled=downsampling_enabled,
                    minimum_points=128,
                    oversample=1.0,
                    default_points=_metric_display_target_points(window),
                )
        elif view_mode == "absolute" and not trace_view_locked:
            display_x, display_y = sample_absolute_metric_series_for_view(
                x,
                y,
                view_width_px=view_width_px,
                enabled=downsampling_enabled,
                minimum_points=128,
                oversample=1.0,
                default_points=_metric_display_target_points(window),
            )
        else:
            display_x, display_y = downsample_metric_series_for_view(
                x,
                y,
                view_x_min=view_x_min,
                view_x_max=view_x_max,
                view_width_px=view_width_px,
                enabled=downsampling_enabled,
                minimum_points=128,
                oversample=1.0,
                default_points=_metric_display_target_points(window),
            )
        view_prep_ms += (perf_counter() - view_started) * 1000.0
        display_x, _ = _sensorgram_display_x_values(window, display_x, clock_mode=clock_mode)
        display_state = None
        if cache is not None and not trace_view_locked and bool(getattr(window, "_live_active", False)):
            display_state = cache.live_absolute_metric_state(metric_name)
        elif cache is not None and not trace_view_locked:
            display_state = cache.absolute_metric_display_state(series_token)
        render_state_key = (
            metric_name,
            downsampling_enabled,
            "absolute",
            bool(trace_view_locked),
            step_mode,
            bool(overlay_enabled),
            int(getattr(window, "_plot_display_points", 0)),
            display_state if display_state is not None else (id(display_x), id(display_y), int(len(display_x))),
        )
        if render_state_cache.get(metric_name) == render_state_key:
            display_points += int(len(display_x))
            setdata_skips += 1
            active_series[metric_name] = (display_x, display_y)
            _update_metric_envelope_overlay(metric_name, overlay_data)
            continue
        setdata_started = perf_counter()
        if step_mode == "spline":
            spline_x, spline_y = _spline_render_series(display_x, display_y)
            curve.setData(spline_x, spline_y, stepMode=False)
        elif step_mode is None:
            curve.setData(display_x, display_y, stepMode=False)
        else:
            curve.setData(display_x, display_y, stepMode=step_mode)
        setdata_ms += (perf_counter() - setdata_started) * 1000.0
        setdata_calls += 1
        display_points += int(len(display_x))
        render_state_cache[metric_name] = render_state_key
        active_series[metric_name] = (display_x, display_y)
        _update_metric_envelope_overlay(metric_name, overlay_data)

    primary_name = window._primary_trace_metric()
    if primary_name in active_series:
        window._visible_trace_x = active_series[primary_name][0]
        window._visible_trace_y = active_series[primary_name][1]
    elif active_series:
        first_series = next(iter(active_series.values()))
        window._visible_trace_x = first_series[0]
        window._visible_trace_y = first_series[1]
    else:
        window._visible_trace_x = None
        window._visible_trace_y = None

    if cache is not None and hasattr(cache, "metric_cache_debug_snapshot"):
        try:
            window._last_metric_view_cache_modes = cache.metric_cache_debug_snapshot()
        except Exception:
            window._last_metric_view_cache_modes = {}
    else:
        window._last_metric_view_cache_modes = {}
    if view_mode == "absolute" and bool(getattr(window, "_live_active", False)):
        live_absolute_archive_reads = int(getattr(cache, "_live_absolute_archive_read_count", 0)) if cache is not None else 0
        window._last_metric_absolute_source_text = live_absolute_source_used
        window._last_metric_absolute_cache_invalidation_text = live_absolute_invalidation_reason
        window._last_metric_absolute_cache_rebuild_count_text = str(max(live_absolute_rebuild_count, 0))
        window._last_metric_absolute_hdf5_read_count_text = str(max(live_absolute_archive_reads, 0))
        window._last_metric_absolute_archive_points_text = str(max(live_absolute_archive_points, 0))
        window._last_metric_absolute_display_points_text = str(max(display_points, 0))
        window._last_metric_absolute_view_prep_text = f"{view_prep_ms:.2f} ms"
        window._last_metric_absolute_append_text = "-" if live_absolute_append_ms is None else f"{float(live_absolute_append_ms):.2f} ms"
    else:
        window._last_metric_absolute_source_text = "-"
        window._last_metric_absolute_cache_invalidation_text = "-"
        window._last_metric_absolute_cache_rebuild_count_text = "-"
        window._last_metric_absolute_hdf5_read_count_text = "-"
        window._last_metric_absolute_archive_points_text = "-"
        window._last_metric_absolute_display_points_text = "-"
        window._last_metric_absolute_view_prep_text = "-"
        window._last_metric_absolute_append_text = "-"

    elapsed_ms = (perf_counter() - started) * 1000.0
    window._last_metric_render_series_export_ms = series_export_ms
    window._last_metric_render_series_extract_ms = series_export_ms
    window._last_metric_render_view_prep_ms = view_prep_ms
    window._last_metric_render_setdata_ms = setdata_ms
    window._last_metric_render_setdata_calls = setdata_calls
    window._last_metric_render_setdata_skips = setdata_skips
    window._last_metric_render_raw_points = raw_points
    window._last_metric_render_display_points = display_points
    changed = setdata_calls > 0 or setdata_skips > 0
    if bool(getattr(window, "_deep_timing_enabled", False)):
        if elapsed_ms >= 2.0:
            window._log_throttled(
                "metric_render",
                (
                    "Metric render: "
                    f"{elapsed_ms:.2f} ms | series={series_export_ms:.2f} ms | "
                    f"view={view_prep_ms:.2f} ms | setData={setdata_ms:.2f} ms | "
                    f"raw_n={raw_points} | display_n={display_points}"
                ),
                level=logging.INFO,
                min_interval=0.5,
            )
    sync_control_step_overlay = getattr(window, "_sync_sensorgram_control_step_overlay", None)
    if callable(sync_control_step_overlay):
        try:
            sync_control_step_overlay()
        except Exception:
            pass
    return changed


def render_sensorgram_heatmap(
    window,
    history: list[tuple[float, np.ndarray]],
    clock_mode: bool,
) -> None:
    window._last_sensorgram_heatmap_arrays_ms = None
    window._last_sensorgram_heatmap_image_ms = None
    window._last_sensorgram_heatmap_axes_ms = None
    if not hasattr(window, "trace_heatmap_image"):
        return
    if not bool(getattr(window, "_sensorgram_heatmap_enabled", True)):
        _show_sensorgram_heatmap_unavailable(window, clock_mode)
        return
    if bool(getattr(window, "_live_active", False)):
        _show_sensorgram_heatmap_unavailable(window, clock_mode)
        return
    request_signature_fn = getattr(window, "_sensorgram_heatmap_archive_request_signature", None)
    request_signature = request_signature_fn() if callable(request_signature_fn) else None
    archive_times = getattr(window, "_sensorgram_heatmap_archive_times", None)
    archive_matrix = getattr(window, "_sensorgram_heatmap_archive_matrix", None)
    archive_request_token = getattr(window, "_sensorgram_heatmap_archive_request_token", None)
    archive_ready = (
        isinstance(archive_times, np.ndarray)
        and isinstance(archive_matrix, np.ndarray)
        and len(archive_times) > 0
        and archive_matrix.ndim == 2
        and getattr(window, "_sensorgram_heatmap_wavelengths", None) is not None
        and request_signature is not None
        and archive_request_token == request_signature
    )
    if not archive_ready and getattr(window, "_metric_archive_path", None) and not bool(
        getattr(window, "_sensorgram_heatmap_archive_loading", False)
    ):
        request_archive = getattr(window, "_request_sensorgram_heatmap_archive", None)
        if callable(request_archive):
            request_archive()
    if not archive_ready and getattr(window, "_metric_archive_path", None):
        _show_sensorgram_heatmap_loading(window, clock_mode)
        return
    if not archive_ready and (not history or getattr(window, "_sensorgram_heatmap_wavelengths", None) is None):
        _set_visible_if_changed(window.trace_heatmap_image, False)
        if hasattr(window, "trace_heatmap_notice_item"):
            _set_visible_if_changed(window.trace_heatmap_notice_item, False)
        for curve in window.trace_curves.values():
            _set_visible_if_changed(curve, False)
        if hasattr(window, "trace_legend"):
            _set_visible_if_changed(window.trace_legend, False)
        return

    started = perf_counter()
    view_mode = "absolute"
    trace_view_locked = bool(getattr(window, "_trace_view_locked", False))
    _, _, _view_width_px = _current_metric_view_state(window)
    view_x_min = view_x_max = None
    if trace_view_locked:
        view_x_min, view_x_max, _view_width_px = _current_metric_view_state(window)
    requested_view_x_min = view_x_min
    requested_view_x_max = view_x_max
    view_height_px = None
    try:
        scene_rect = window.trace_plot.getPlotItem().vb.sceneBoundingRect()
        if scene_rect is not None:
            view_height_px = float(scene_rect.height())
            if not np.isfinite(view_height_px):
                view_height_px = None
    except Exception:
        view_height_px = None
    cache = getattr(window, "_plot_view_cache", None)
    heatmap_token = build_heatmap_history_token(window)
    arrays_started = perf_counter()
    if archive_ready:
        times = np.asarray(archive_times, dtype=np.float64)
        matrix = np.asarray(archive_matrix, dtype=np.float64)
    elif cache is not None:
        times, matrix = cache.heatmap_arrays_from_history(heatmap_token, history, build_heatmap_arrays)
    else:
        times, matrix = build_heatmap_arrays(history)
    if cache is not None:
        if view_mode == "absolute" and not trace_view_locked:
            times, matrix = cache.absolute_heatmap_view(
                heatmap_token,
                times,
                matrix,
                max_rows=int(getattr(window, "_sensorgram_heatmap_history_max_rows", 2000)),
                view_height_px=view_height_px,
                enabled=True,
            )
        else:
            times, matrix = cache.heatmap_view(
                heatmap_token,
                times,
                matrix,
                view_x_min=view_x_min,
                view_x_max=view_x_max,
                max_rows=int(getattr(window, "_sensorgram_heatmap_history_max_rows", 2000)),
                view_height_px=view_height_px,
                enabled=True,
            )
    elif view_mode == "absolute" and not trace_view_locked:
        times, matrix = sample_absolute_heatmap_rows_for_view(
            times,
            matrix,
            max_rows=int(getattr(window, "_sensorgram_heatmap_history_max_rows", 2000)),
            view_height_px=view_height_px,
            enabled=True,
        )
    else:
        times, matrix = select_heatmap_rows_for_view(
            times,
            matrix,
            view_x_min=view_x_min,
            view_x_max=view_x_max,
            max_rows=int(getattr(window, "_sensorgram_heatmap_history_max_rows", 2000)),
            view_height_px=view_height_px,
            enabled=True,
        )
    window._last_sensorgram_heatmap_arrays_ms = (perf_counter() - arrays_started) * 1000.0
    wavelengths = np.asarray(window._sensorgram_heatmap_wavelengths, dtype=np.float64)
    if len(times) == 0 or len(wavelengths) == 0:
        _set_visible_if_changed(window.trace_heatmap_image, False)
        if hasattr(window, "trace_heatmap_notice_item"):
            _set_visible_if_changed(window.trace_heatmap_notice_item, False)
        return

    expected_length = len(wavelengths)
    if matrix.shape[1] != expected_length:
        _set_visible_if_changed(window.trace_heatmap_image, False)
        if hasattr(window, "trace_heatmap_notice_item"):
            _set_visible_if_changed(window.trace_heatmap_notice_item, False)
        return

    finite = np.isfinite(matrix)
    if not np.any(finite):
        _set_visible_if_changed(window.trace_heatmap_image, False)
        if hasattr(window, "trace_heatmap_notice_item"):
            _set_visible_if_changed(window.trace_heatmap_notice_item, False)
        return

    image_data = matrix.T
    image_started = perf_counter()
    levels = getattr(window, "_sensorgram_heatmap_levels", None)
    if levels is None:
        levels = derive_heatmap_levels_from_matrix(matrix)
        window._sensorgram_heatmap_levels = levels
    else:
        # Keep the displayed range stable, but expand it if new rows exceed the cached bounds.
        latest_row = matrix[-1] if len(matrix) > 0 else matrix
        updated_levels = expand_heatmap_levels(levels, latest_row)
        if updated_levels != levels:
            levels = updated_levels
            window._sensorgram_heatmap_levels = levels
    window.trace_heatmap_image.setImage(image_data, autoLevels=False)
    if hasattr(window.trace_heatmap_image, "setLevels"):
        window.trace_heatmap_image.setLevels(levels)
    left = float(times[0])
    right = float(times[-1])
    if right <= left:
        right = left + 1e-6
    bottom = float(np.min(wavelengths))
    top = float(np.max(wavelengths))
    if top <= bottom:
        top = bottom + 1e-6
    window.trace_heatmap_image.setRect(QRectF(left, bottom, right - left, top - bottom))
    window._last_sensorgram_heatmap_image_ms = (perf_counter() - image_started) * 1000.0
    axes_started = perf_counter()
    if requested_view_x_min is not None and requested_view_x_max is not None and requested_view_x_max > requested_view_x_min:
        view_span = requested_view_x_max - requested_view_x_min
    else:
        view_span = right - left
    x_pad = _metric_follow_latest_buffer_s(window, clock_mode=clock_mode, span_s=view_span)
    if not getattr(window, "_trace_view_locked", False):
        window._trace_view_autoscaling = True
        try:
            if requested_view_x_min is not None and requested_view_x_max is not None and requested_view_x_max > requested_view_x_min:
                window.trace_plot.setXRange(requested_view_x_min, requested_view_x_max + x_pad, padding=0.0)
            else:
                window.trace_plot.setXRange(left, right + x_pad, padding=0.0)
            window.trace_plot.setYRange(bottom, top, padding=0.0)
        finally:
            window._trace_view_autoscaling = False
    _set_trace_time_axis_mode(window, axis_mode="clock" if clock_mode else "elapsed")
    _set_visible_if_changed(window.trace_heatmap_image, True)
    if hasattr(window, "trace_heatmap_notice_item"):
        _set_visible_if_changed(window.trace_heatmap_notice_item, False)
    for curve in window.trace_curves.values():
        _set_visible_if_changed(curve, False)
    for band in getattr(window, "trace_metric_envelope_bands", {}).values():
        if hasattr(band, "setVisible"):
            band.setVisible(False)
    if hasattr(window, "trace_legend"):
        _set_visible_if_changed(window.trace_legend, False)
    window._visible_trace_x = times
    safe_matrix = np.where(np.isfinite(matrix), matrix, -np.inf)
    row_max = np.max(safe_matrix, axis=1)
    row_max[~np.isfinite(row_max)] = np.nan
    window._visible_trace_y = row_max
    window._visible_trace_mode = "clock" if clock_mode else "elapsed"
    window._last_sensorgram_heatmap_axes_ms = (perf_counter() - axes_started) * 1000.0
    elapsed_ms = (perf_counter() - started) * 1000.0
    window._last_sensorgram_heatmap_render_ms = elapsed_ms
    if elapsed_ms >= 2.0:
        window._log_throttled(
            "sensorgram_heatmap_render",
            (
                "Sensorgram heatmap render "
                f"| rows={len(history)} | cols={len(wavelengths)} | "
                f"mode={view_mode} | {elapsed_ms:.2f} ms"
            ),
            level=logging.INFO,
            min_interval=1.0,
        )


def _show_sensorgram_heatmap_unavailable(window, clock_mode: bool) -> None:
    detail = (
        "Live heatmap is disabled for performance. Use Freeze/Review to generate heatmap from recorded data."
        if bool(getattr(window, "_live_active", False))
        else "Enable it in Help > Performance switches."
    )
    _show_trace_plot_unavailable(
        window,
        "Heatmap unavailable",
        detail,
        clock_mode=clock_mode,
    )


def _show_sensorgram_heatmap_loading(window, clock_mode: bool) -> None:
    _show_trace_plot_unavailable(
        window,
        "Heatmap loading",
        "Preparing archive-backed heatmap in the background.",
        clock_mode=clock_mode,
    )


def _show_metric_plot_unavailable(window, clock_mode: bool) -> None:
    _show_trace_plot_unavailable(
        window,
        "Metric plot unavailable",
        "Enable it in Help > Performance switches.",
        clock_mode=clock_mode,
    )


def _show_trace_plot_unavailable(window, title: str, detail: str, *, clock_mode: bool) -> None:
    if hasattr(window, "trace_heatmap_image"):
        _set_visible_if_changed(window.trace_heatmap_image, False)
    _set_trace_time_axis_mode(window, axis_mode="clock" if clock_mode else "elapsed")
    if hasattr(window, "trace_heatmap_notice_item"):
        notice = window.trace_heatmap_notice_item
        if hasattr(notice, "setText"):
            notice.setText(f"{title}\n{detail}")
        notice.setVisible(True)
        sync_overlay = getattr(window, "_sync_trace_heatmap_notice_overlay", None)
        if callable(sync_overlay):
            sync_overlay()
    for curve in window.trace_curves.values():
        _set_visible_if_changed(curve, False)
    for band in getattr(window, "trace_metric_envelope_bands", {}).values():
        if hasattr(band, "setVisible"):
            band.setVisible(False)
    if hasattr(window, "trace_legend"):
        _set_visible_if_changed(window.trace_legend, False)
    window._visible_trace_mode = "clock" if clock_mode else "elapsed"


def update_spectrum_stats(window, processed: Spectrum | None, fit: Spectrum | None) -> None:
    if processed is None:
        window.spectrum_stats_label.setText("peak: - | centroid: - | FWHM: - | MSE: - | R: - | S/N: -")
        window.spectrum_cursor_label.setText("cursor: -")
        return

    peak_mode = window._current_processing_settings().spectrum_tracking_mode
    label_map = {
        "smoothed_max": "S_Max",
        "poly_max": "P_Max",
        "gaussian_center": "G_Ctr",
        "centroid": "Cent",
    }
    analysis = window._get_analysis_metrics(processed, fit)
    primary_peak = analysis.get("primary_peak_nm", float("nan"))
    centroid = analysis.get("centroid_nm", float("nan"))
    fit_r = analysis.get("fit_r")
    mse = analysis.get("mse", "-")
    snr = analysis.get("snr")
    fwhm = analysis.get("fwhm", "-")
    if not isinstance(primary_peak, (int, float)) or not np.isfinite(float(primary_peak)):
        primary_peak = window._compute_peak_metric_nm(processed, fit)
    if not isinstance(centroid, (int, float)) or not np.isfinite(float(centroid)):
        centroid = window._compute_centroid_nm(processed, fit)
    window.spectrum_stats_label.setText(
        f"{label_map.get(peak_mode, 'peak')}: {float(primary_peak):.3f} nm"
        f" | centroid: {float(centroid):.3f} nm"
        f" | FWHM: {fwhm}"
        f" | MSE: {mse}"
        f" | R: {fit_r if fit_r is not None else '-'}"
        f" | S/N: {snr if snr is not None else '-'}"
    )
    window.spectrum_cursor_label.setText(window._spectrum_cursor_text)


def update_metric_stats(window) -> None:
    started = perf_counter()
    series = window._active_trace_series()
    metric_name = window._trace_stats_metric()
    metric_label = getattr(window, "TRACE_METRIC_SHORT_LABELS", {}).get(metric_name, metric_name)
    metric_color = window.TRACE_METRIC_COLORS.get(metric_name, "#444444")

    visible_series: list[tuple[np.ndarray, np.ndarray]] = []
    trace_curves = getattr(window, "trace_curves", None)
    if isinstance(trace_curves, dict):
        for curve in trace_curves.values():
            if hasattr(curve, "isVisible") and not bool(curve.isVisible()):
                continue
            x_values, y_values = _curve_data_arrays(curve)
            if len(x_values) > 0 and len(y_values) > 0:
                visible_series.append((x_values, y_values))

    if not visible_series:
        if not series:
            window.trace_stats_label.setText(f"{_metric_label_span(metric_label, metric_color)}: - | min/max: - | span: - | dt -")
            window.trace_noise_summary_label.setText("noise: -")
            window.trace_cursor_label.setText("cursor: -")
            return
        visible_series = [tuple(np.asarray(item, dtype=np.float64) for item in series_values) for series_values in series.values()]

    if not visible_series:
        window.trace_stats_label.setText(f"{_metric_label_span(metric_label, metric_color)}: - | min/max: - | span: - | dt -")
        window.trace_noise_summary_label.setText("noise: -")
        window.trace_cursor_label.setText("cursor: -")
        return

    selected_metrics = list(window._selected_trace_metrics())
    if metric_name in selected_metrics:
        matched_curve = None
        if isinstance(trace_curves, dict):
            matched_curve = trace_curves.get(metric_name)
        if matched_curve is not None:
            x_values, y_values = _curve_data_arrays(matched_curve)
        else:
            x_values, y_values = visible_series[0]
    else:
        x_values, y_values = visible_series[0]
        metric_name = selected_metrics[0] if selected_metrics else metric_name
        metric_label = getattr(window, "TRACE_METRIC_SHORT_LABELS", {}).get(metric_name, metric_name)
        metric_color = window.TRACE_METRIC_COLORS.get(metric_name, "#444444")
    if len(x_values) == 0 or len(y_values) == 0:
        window.trace_stats_label.setText(f"{_metric_label_span(metric_label, metric_color)}: - | min/max: - | span: - | dt -")
        window.trace_noise_summary_label.setText("noise: -")
        window.trace_cursor_label.setText("cursor: -")
        return

    y_min = float(np.min(y_values))
    y_max = float(np.max(y_values))
    latest_y = float(y_values[-1])
    elapsed_from_start_s = max(float(x_values[-1]) - float(x_values[0]), 0.0)
    axis_mode = _sensorgram_time_axis_mode(window)
    if axis_mode == "clock":
        latest_time_text = _sensorgram_format_clock(window, float(x_values[-1]))
    else:
        latest_time_text = _sensorgram_format_elapsed(float(x_values[-1]))

    dt_values = np.diff(x_values)
    dt_text = "-" if len(dt_values) == 0 else f"{float(np.nanmean(dt_values)):.2f} s"
    compression_level_text = _metric_compression_level_text(window, metric_name)
    primary_metric_name = window._primary_trace_metric() if hasattr(window, "_primary_trace_metric") else None
    primary_metric_name = str(primary_metric_name or "").strip()
    metric_is_primary = bool(primary_metric_name) and metric_name == primary_metric_name
    metric_value_color = metric_color
    window_s = window.trace_noise_window_spin.value()
    noise_chunks: list[str] = []
    metric_order = list(getattr(window, "TRACE_METRIC_LABELS", {}).keys())
    ordered_metric_names = [name for name in metric_order if name in series]
    if not ordered_metric_names:
        ordered_metric_names = list(series.keys())
    for metric_name in ordered_metric_names:
        metric_x, metric_y = series[metric_name]
        tail_start = float(metric_x[-1]) - window_s
        tail_index = int(np.searchsorted(metric_x, tail_start, side="left"))
        metric_window = metric_y[tail_index:]
        if len(metric_window) >= 2:
            metric_noise = f"{float(np.nanstd(metric_window)):.4f} nm"
        else:
            metric_noise = "-"
        label = getattr(window, "TRACE_METRIC_SHORT_LABELS", {}).get(metric_name, metric_name)
        color = window.TRACE_METRIC_COLORS.get(metric_name, "#444444")
        noise_chunks.append(f"{_metric_label_span(label, color)} {escape(metric_noise)}")

    window.trace_stats_label.setText(
        f"{_metric_label_span(metric_label, metric_color, bold=metric_is_primary)}: {escape(latest_time_text)} (+{_format_hhmmss(elapsed_from_start_s)}), "
        f"<span style='color: {escape(metric_value_color)}; font-weight: 700;'>{latest_y:.3f} nm</span>"
        f" | min/max: {y_min:.3f} / {y_max:.3f} nm"
        f" | span: {y_max - y_min:.3f} nm"
        f" | dt {dt_text}"
        f" | compr.: {escape(compression_level_text)}"
    )
    window.trace_noise_summary_label.setText(" | ".join(noise_chunks))
    window.trace_cursor_label.setText(window._trace_cursor_text)

    if bool(getattr(window, "_deep_timing_enabled", False)):
        elapsed_ms = (perf_counter() - started) * 1000.0
        if elapsed_ms >= 2.0:
            visible_points = 0
            for x_values, _y_values in visible_series:
                visible_points += int(len(x_values))
            raw_points = sum(_series_point_count(series) for series in series.values()) if series else 0
            window._log_throttled(
                "metric_stats",
                (
                    "Metric stats: "
                    f"{elapsed_ms:.2f} ms | raw_n={raw_points} | "
                    f"display_n={visible_points}"
                ),
                level=logging.INFO,
                min_interval=0.5,
            )


def handle_spectrum_mouse_moved(window, event) -> None:
    pos = event[0]
    if not window.spectrum_plot.sceneBoundingRect().contains(pos):
        return
    mouse_point = window.spectrum_plot.plotItem.vb.mapSceneToView(pos)
    x = float(mouse_point.x())
    processed = window._visible_processed_plot if window._visible_processed_plot is not None else window._last_processed_plot
    if processed is not None and len(processed.wavelengths_nm) > 0:
        wavelengths = np.asarray(processed.wavelengths_nm, dtype=np.float64)
        values = np.asarray(processed.values, dtype=np.float64)
        index = _nearest_sorted_index(wavelengths, x)
        x = float(wavelengths[index])
        y = float(values[index])
    else:
        y = float(mouse_point.y())
    window.spectrum_vline.setPos(x)
    window.spectrum_hline.setPos(y)
    window._spectrum_cursor_text = f"cursor: {x:.3f} nm, {y:.3f}"
    window.spectrum_cursor_label.setText(window._spectrum_cursor_text)


def handle_metric_mouse_moved(window, event) -> None:
    pos = event[0]
    if not window.trace_plot.sceneBoundingRect().contains(pos):
        return
    mouse_point = window.trace_plot.plotItem.vb.mapSceneToView(pos)
    mouse_x = float(mouse_point.x())
    mouse_y = float(mouse_point.y())
    x = mouse_x
    y = mouse_y
    if getattr(window, "_sensorgram_content_mode", "metric") == "heatmap":
        window.trace_vline.setPos(x)
        window.trace_hline.setPos(y)
        cursor_x = _sensorgram_format_clock(window, float(x)) if _sensorgram_time_axis_clock_mode(window) else _sensorgram_format_elapsed(float(x))
        window._trace_cursor_text = f"cursor: {cursor_x}, {y:.3f} nm"
        window.trace_cursor_label.setText(window._trace_cursor_text)
        return

    best_match: tuple[float, float, float] | None = None
    for x_values, y_values in window._active_trace_series().values():
        if len(x_values) == 0:
            continue
        x_array = np.asarray(x_values, dtype=np.float64)
        index = _nearest_sorted_index(x_array, mouse_x)
        candidate_x = float(x_values[index])
        candidate_y = float(y_values[index])
        distance_sq = (candidate_x - mouse_x) ** 2 + (candidate_y - mouse_y) ** 2
        if best_match is None or distance_sq < best_match[2]:
            best_match = (candidate_x, candidate_y, distance_sq)

    if best_match is not None:
        x, y, _ = best_match
    window.trace_vline.setPos(x)
    window.trace_hline.setPos(y)
    cursor_x = _sensorgram_format_clock(window, float(x)) if _sensorgram_time_axis_clock_mode(window) else _sensorgram_format_elapsed(float(x))
    window._trace_cursor_text = f"cursor: {cursor_x}, {y:.3f} nm"
    window.trace_cursor_label.setText(window._trace_cursor_text)


_current_trace_view_state = _current_metric_view_state
downsample_trace_series_for_view = downsample_metric_series_for_view
autoscale_trace_plot = autoscale_metric_plot
request_trace_autoscale = request_metric_autoscale
refresh_trace_plot = refresh_metric_plot
render_trace_series = render_metric_series
update_trace_stats = update_metric_stats
handle_trace_mouse_moved = handle_metric_mouse_moved
