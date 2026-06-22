from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QFont, QFontMetricsF
from pyqtgraph import exporters as pg_exporters

from lspr_app.gui.main_window_sensorgram import normalize_sensorgram_content_mode
from lspr_app.gui.workers import (
    HeatmapArchiveLoadRequest,
    HeatmapArchiveLoadResult,
    HeatmapArchiveLoadTask,
    MetricArchiveReloadRequest,
    MetricArchiveReloadResult,
    MetricArchiveReloadTask,
)
from lspr_app.gui.sensorgram_control_step_overlay import (
    normalize_sensorgram_control_step_overlay_color,
    normalize_sensorgram_control_step_overlay_label,
    resolve_sensorgram_control_step_overlay_palette_label,
)


def sensorgram_heatmap_lookup_table(window) -> np.ndarray:
    try:
        return pg.colormap.get("viridis").getLookupTable(0.0, 1.0, 256)
    except Exception:
        try:
            color_map = pg.ColorMap(
                [0.0, 0.25, 0.5, 0.75, 1.0],
                [
                    (68, 1, 84, 255),
                    (59, 82, 139, 255),
                    (33, 145, 140, 255),
                    (94, 201, 98, 255),
                    (253, 231, 37, 255),
                ],
            )
            return color_map.getLookupTable(0.0, 1.0, 256)
        except Exception:
            return pg.colormap.get("plasma").getLookupTable(0.0, 1.0, 256)


def apply_sensorgram_display_style(window) -> None:
    mode = normalize_sensorgram_content_mode(getattr(window, "_sensorgram_content_mode", "metric"))
    if mode == "heatmap":
        window.trace_heatmap_image.setLookupTable(sensorgram_heatmap_lookup_table(window))
        for curve in window.trace_curves.values():
            curve.setPen(pg.mkPen("#8A5CFF", width=2.2))
        return
    for metric_name, curve in window.trace_curves.items():
        curve.setPen(pg.mkPen(window.SENSORGRAM_TIME_PLOT_COLORS.get(metric_name, "#1F77B4"), width=2.2))
    for metric_name, band in getattr(window, "trace_metric_envelope_bands", {}).items():
        base_color = QColor(window.SENSORGRAM_TIME_PLOT_COLORS.get(metric_name, "#1F77B4"))
        band_color = QColor(base_color)
        band_alpha = int(round(max(min(float(getattr(window, "_sensorgram_metric_envelope_overlay_alpha", 16)), 100.0), 0.0) * 2.55))
        band_color.setAlpha(max(min(band_alpha, 255), 0))
        band.setBrush(pg.mkBrush(band_color))


def set_sensorgram_heatmap_notice(window, title: str, detail: str) -> None:
    if hasattr(window, "trace_heatmap_notice_item"):
        window.trace_heatmap_notice_item.setText(f"{title}\n{detail}")
        window.trace_heatmap_notice_item.setVisible(True)


def sensorgram_heatmap_archive_request_signature(window) -> tuple[object, ...] | None:
    archive_path = getattr(window, "_metric_archive_path", None)
    if archive_path is None:
        return None
    try:
        archive_path = str(Path(archive_path).expanduser())
    except Exception:
        archive_path = str(archive_path)
    time_range = getattr(window, "_sensorgram_heatmap_requested_time_range_s", None)
    wavelength_range = getattr(window, "_sensorgram_heatmap_requested_wavelength_range_nm", None)
    if isinstance(time_range, (int, float)):
        time_range = float(time_range)
    else:
        time_range = None
    if isinstance(wavelength_range, (list, tuple)) and len(wavelength_range) == 2:
        wavelength_range = tuple(float(value) for value in wavelength_range)
    else:
        wavelength_range = None
    return (
        archive_path,
        int(getattr(window, "_source_epoch", 0)),
        int(getattr(window, "_sensorgram_heatmap_history_max_rows", 800)),
        time_range,
        wavelength_range,
        "sample",
    )


def start_sensorgram_heatmap_archive_task(window, request_token: tuple[object, ...]) -> None:
    archive_path = getattr(window, "_metric_archive_path", None)
    if archive_path is None:
        return
    archive_path = Path(archive_path).expanduser()
    window._sensorgram_heatmap_archive_request_token = request_token
    window._sensorgram_heatmap_archive_loading = True
    window._sensorgram_heatmap_archive_times = None
    window._sensorgram_heatmap_archive_matrix = None
    window._sensorgram_heatmap_archive_rows = 0
    window._sensorgram_heatmap_archive_load_ms = None
    window._sensorgram_heatmap_archive_build_ms = None
    set_sensorgram_heatmap_notice(window, "Heatmap loading", "Preparing heatmap from the saved measurement file in the background.")
    task = HeatmapArchiveLoadTask(
        HeatmapArchiveLoadRequest(
            path=archive_path,
            source_epoch=int(getattr(window, "_source_epoch", 0)),
            request_token=request_token,
            max_rows=int(getattr(window, "_sensorgram_heatmap_history_max_rows", 800)),
            time_range_s=getattr(window, "_sensorgram_heatmap_requested_time_range_s", None),
            wavelength_range_nm=getattr(window, "_sensorgram_heatmap_requested_wavelength_range_nm", None),
            spectrum_group_name="sample",
        )
    )
    window._sensorgram_heatmap_archive_task = task
    task.signals.finished.connect(window._handle_sensorgram_heatmap_archive_result)
    task.signals.failed.connect(window._handle_sensorgram_heatmap_archive_failed)
    window._thread_pool.start(task)


def request_sensorgram_heatmap_archive(window) -> None:
    if window._closing or bool(getattr(window, "_live_active", False)):
        return
    request_token = sensorgram_heatmap_archive_request_signature(window)
    if request_token is None:
        return
    window._sensorgram_heatmap_archive_pending_token = request_token
    if window._sensorgram_heatmap_archive_loading:
        return
    start_sensorgram_heatmap_archive_task(window, request_token)


def handle_sensorgram_heatmap_archive_result(window, result: HeatmapArchiveLoadResult) -> None:
    window._sensorgram_heatmap_archive_loading = False
    window._sensorgram_heatmap_archive_task = None
    if window._closing:
        window._sensorgram_heatmap_archive_pending_token = None
        return
    if result.request_token != window._sensorgram_heatmap_archive_request_token:
        next_token = window._sensorgram_heatmap_archive_pending_token
        if next_token is not None and next_token != result.request_token:
            window._sensorgram_heatmap_archive_pending_token = None
            start_sensorgram_heatmap_archive_task(window, next_token)
        return
    window._sensorgram_heatmap_wavelengths = np.asarray(result.wavelengths, dtype=np.float64)
    window._sensorgram_heatmap_archive_times = np.asarray(result.times, dtype=np.float64)
    window._sensorgram_heatmap_archive_matrix = np.asarray(result.matrix, dtype=np.float64)
    window._sensorgram_heatmap_archive_rows = int(result.row_count)
    window._sensorgram_heatmap_archive_load_ms = float(result.load_ms)
    window._sensorgram_heatmap_archive_build_ms = float(result.build_ms)
    window._sensorgram_heatmap_history.clear()
    window._sensorgram_heatmap_history_revision += 1
    if hasattr(window, "_plot_view_cache"):
        try:
            window._plot_view_cache.clear()
        except Exception:
            pass
    set_sensorgram_heatmap_notice(window, "Heatmap ready", "Use Freeze/Review to inspect the saved-file heatmap.")
    window._request_deferred_ui_refresh(trace_plot=True, trace_label="Metric position (nm)")
    next_token = window._sensorgram_heatmap_archive_pending_token
    window._sensorgram_heatmap_archive_pending_token = None
    if next_token is not None and next_token != result.request_token:
        start_sensorgram_heatmap_archive_task(window, next_token)


def handle_sensorgram_heatmap_archive_failed(window, message: str) -> None:
    window._sensorgram_heatmap_archive_loading = False
    window._sensorgram_heatmap_archive_task = None
    window._sensorgram_heatmap_archive_pending_token = None
    set_sensorgram_heatmap_notice(window, "Heatmap unavailable", f"Saved-file heatmap load failed: {message}")
    window._log_error(f"Heatmap saved-file load failed: {message}")


def sensorgram_metric_archive_reload_request_signature(window) -> tuple[object, ...] | None:
    archive_path = getattr(window, "_metric_archive_path", None)
    if archive_path is None:
        return None
    try:
        archive_path = str(Path(archive_path).expanduser())
    except Exception:
        archive_path = str(archive_path)
    selected_metrics = tuple(sorted(str(name) for name in window._selected_trace_metrics()))
    return (
        archive_path,
        int(getattr(window, "_source_epoch", 0)),
        selected_metrics,
        int(getattr(window, "_plot_display_points", 0)),
    )


def start_sensorgram_metric_archive_reload_task(window, request_token: tuple[object, ...]) -> None:
    archive_path = getattr(window, "_metric_archive_path", None)
    if archive_path is None:
        return
    archive_path = Path(archive_path).expanduser()
    window._sensorgram_metric_archive_reload_request_token = request_token
    window._sensorgram_metric_archive_reload_loading = True
    window._sensorgram_metric_archive_reload_pending_token = None
    task = MetricArchiveReloadTask(
        MetricArchiveReloadRequest(
            path=archive_path,
            source_epoch=int(getattr(window, "_source_epoch", 0)),
            request_token=request_token,
            metric_names=tuple(window._selected_trace_metrics()),
        )
    )
    window._sensorgram_metric_archive_reload_task = task
    task.signals.finished.connect(window._handle_sensorgram_metric_archive_reload_result)
    task.signals.failed.connect(window._handle_sensorgram_metric_archive_reload_failed)
    window._thread_pool.start(task)


def request_absolute_sensorgram_metric_archive_reload(window) -> None:
    if window._closing:
        return
    request_token = sensorgram_metric_archive_reload_request_signature(window)
    if request_token is None:
        window.status_label.setText("No saved measurement file is available to reload.")
        return
    archive_path = Path(request_token[0])
    if not archive_path.exists():
        window.status_label.setText("Sensorgram measurement file is missing.")
        window._log_warning(f"Sensorgram measurement file not found: {archive_path}")
        return
    window._sensorgram_metric_archive_reload_pending_token = request_token
    if window._sensorgram_metric_archive_reload_loading:
        return
    start_sensorgram_metric_archive_reload_task(window, request_token)


def request_sensorgram_metric_archive_reload(window) -> None:
    request_absolute_sensorgram_metric_archive_reload(window)


def handle_absolute_sensorgram_metric_archive_reload_result(window, result: MetricArchiveReloadResult) -> None:
    if int(result.point_count) <= 0:
        window.status_label.setText("Reloaded sensorgram measurement file returned no data; keeping the current absolute cache.")
        window._log_warning(
            f"Sensorgram measurement file reload returned no data | purpose=absolute_reload | path={result.path}"
        )
        return
    plot_view_cache = getattr(window, "_plot_view_cache", None)
    display_points = max(int(getattr(window, "_plot_display_points", 512)), 1)
    if plot_view_cache is not None and hasattr(plot_view_cache, "clear_live_absolute_metric_cache"):
        try:
            plot_view_cache.clear_live_absolute_metric_cache()
        except Exception:
            pass
    if plot_view_cache is not None and hasattr(plot_view_cache, "clear_active_trace_series_cache"):
        try:
            plot_view_cache.clear_active_trace_series_cache()
        except Exception:
            pass
    total_points = 0
    for metric_name, (times, values) in result.series.items():
        x_values = np.asarray(times, dtype=np.float64)
        y_values = np.asarray(values, dtype=np.float64)
        if len(x_values) == 0 or len(y_values) == 0:
            continue
        total_points += int(min(len(x_values), len(y_values)))
        if plot_view_cache is not None and hasattr(plot_view_cache, "seed_live_absolute_metric_cache"):
            try:
                plot_view_cache.seed_live_absolute_metric_cache(
                    metric_name,
                    x_values,
                    y_values,
                    target_points=display_points,
                    recent_tail_points=max(int(getattr(window, "_sensorgram_compression_recent_tail_points", 300)), 0),
                )
            except Exception:
                pass
    if hasattr(window, "_metric_render_display_cache"):
        window._metric_render_display_cache.clear()
    if hasattr(window, "_metric_render_state_cache"):
        window._metric_render_state_cache.clear()
    window._trace_view_locked = False
    window._last_metric_absolute_source_text = "saved_file_reload"
    window._last_metric_absolute_cache_invalidation_text = "reload"
    window._last_metric_absolute_cache_rebuild_count_text = str(int(result.metric_count))
    window._last_metric_absolute_hdf5_read_count_text = "1"
    window._last_metric_absolute_archive_points_text = str(int(result.point_count))
    window._last_metric_absolute_display_points_text = str(int(total_points))
    window._last_metric_absolute_view_prep_text = f"{float(result.load_ms):.2f} ms"
    window._last_metric_absolute_append_text = "-"
    window.status_label.setText(
        f"Reloaded sensorgram measurement file ({result.metric_count} metrics, {result.point_count} points)."
    )
    window._log_success(
        f"Sensorgram measurement file reloaded | purpose=absolute_reload | metrics={result.metric_count} | points={result.point_count} | load_ms={float(result.load_ms):.2f}"
    )
    window._refresh_trace_plot("Metric position (nm)")
    window._request_trace_autoscale()
    window._update_trace_stats()
