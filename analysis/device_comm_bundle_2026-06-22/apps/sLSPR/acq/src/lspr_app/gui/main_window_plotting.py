from __future__ import annotations

import logging
from time import perf_counter
from datetime import datetime

import numpy as np

from lspr_app.domain.models import ProcessingSettings, Spectrum
from lspr_app.domain.processing import fit_processed_spectrum, processing_debug_mode_enabled
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap

from lspr_app.gui.icon_helpers import math_function_tab_icon, prism_tab_icon, transport_icon
from lspr_app.gui.plot_controller import (
    apply_processing_range_to_spectrum_plot as _apply_processing_range_to_spectrum_plot,
    flush_deferred_display_refreshes as _flush_deferred_display_refreshes,
    flush_deferred_metric_refreshes as _flush_deferred_metric_refreshes,
    flush_deferred_stats_refreshes as _flush_deferred_stats_refreshes,
    flush_deferred_ui_refreshes as _flush_deferred_ui_refreshes,
    flush_plot_refreshes as _flush_plot_refreshes,
    refresh_plot as _refresh_plot,
)
from lspr_app.gui.spectrum_plot_controller import (
    autoscale_residual_axis as _autoscale_residual_axis,
    autoscale_spectrum_plot as _autoscale_spectrum_plot,
    handle_spectrum_mouse_moved as _handle_spectrum_mouse_moved,
    update_residual_axis_visibility as _update_residual_axis_visibility,
    update_residual_view_geometry as _update_residual_view_geometry,
    update_spectrum_stats as _update_spectrum_stats,
)
from lspr_app.gui.trace_plot_controller import (
    autoscale_metric_plot as _autoscale_metric_plot,
    handle_metric_mouse_moved as _handle_metric_mouse_moved,
    refresh_metric_plot as _refresh_metric_plot,
    render_metric_series as _render_metric_series,
    request_metric_autoscale as _request_metric_autoscale,
    update_metric_stats as _update_metric_stats,
)
from lspr_app.gui.sensorgram_control_step_overlay import build_sensorgram_control_step_overlay_segments
from lspr_app.gui.processing_helpers import (
    analysis_cache_token as _analysis_cache_token,
    analysis_metrics_cache_token as _analysis_metrics_cache_token,
    compute_centroid_nm as _compute_centroid_nm,
    compute_metric_nm as _compute_metric_nm,
    compute_peak_metric_nm as _compute_peak_metric_nm,
    compute_trace_metrics as _compute_trace_metrics,
    get_analysis_metrics as _get_analysis_metrics,
    get_dense_analysis_curve as _get_dense_analysis_curve,
    get_processed_spectrum as _get_processed_spectrum,
    needs_gaussian_metric as _needs_gaussian_metric,
    processing_cache_token as _processing_cache_token,
)
from lspr_app.gui.main_window_logging import build_pipeline_timing_breakdown_for, _timing_plain_text
from lspr_app.gui.workers import ProcessingRequest, ProcessingResult, ProcessingTask

def get_analysis_processed_spectrum_for(window, signal: Spectrum | None) -> tuple[Spectrum | None, Spectrum | None]:
    if signal is None:
        return None, None
    if window._last_processed_plot is not None and window._last_processed_plot.acquired_at == signal.acquired_at:
        return window._last_processed_plot, window._last_fit_plot
    return _get_processed_spectrum(signal, window._current_processing_settings())


def get_processed_spectrum_for(
    window,
    spectrum: Spectrum | None,
    settings: ProcessingSettings,
) -> tuple[Spectrum | None, Spectrum | None]:
    token = _processing_cache_token(spectrum, settings)
    if token is not None and token == window._processed_cache_key:
        return window._processed_cache_result
    result = _get_processed_spectrum(spectrum, settings)
    window._processed_cache_key = token
    window._processed_cache_result = result
    return result


def compute_peak_metric_nm_for(window, processed: Spectrum, fit: Spectrum | None) -> float:
    return _compute_peak_metric_nm(processed, fit, window._current_processing_settings())


def compute_trace_metrics_for(window, processed: Spectrum, fit: Spectrum | None) -> dict[str, float]:
    return _compute_trace_metrics(processed, fit, window._current_processing_settings(), window._selected_trace_metrics())


def compute_metric_nm_for(window, mode: str, processed: Spectrum, fit: Spectrum | None) -> float:
    return _compute_metric_nm(mode, processed, fit, window._current_processing_settings())


def processing_cache_token_for(window, spectrum: Spectrum | None, settings: ProcessingSettings) -> tuple[object, ...] | None:
    return _processing_cache_token(spectrum, settings)


def analysis_cache_token_for(window, processed: Spectrum | None, fit: Spectrum | None, settings: ProcessingSettings) -> tuple[object, ...] | None:
    return _analysis_cache_token(processed, fit, settings)


def analysis_metrics_cache_token_for(window, processed: Spectrum | None, fit: Spectrum | None, settings: ProcessingSettings) -> tuple[object, ...] | None:
    return _analysis_metrics_cache_token(processed, fit, settings)


def needs_gaussian_metric_for(window, settings: ProcessingSettings | None = None) -> bool:
    return _needs_gaussian_metric(settings or window._current_processing_settings())


def get_dense_analysis_curve_for(
    window,
    processed: Spectrum | None,
    fit: Spectrum | None,
    settings: ProcessingSettings,
) -> tuple[np.ndarray, np.ndarray, dict[str, float | str]]:
    token = _analysis_cache_token(processed, fit, settings)
    if token is not None and token == window._analysis_cache_key:
        return window._analysis_cache_result
    result = _get_dense_analysis_curve(processed, fit, settings)
    window._analysis_cache_key = token
    window._analysis_cache_result = result
    return result


def get_analysis_metrics_for(
    window,
    processed: Spectrum | None,
    fit: Spectrum | None,
    settings: ProcessingSettings,
) -> dict[str, float | str]:
    token = _analysis_metrics_cache_token(processed, fit, settings)
    if token is not None and token == window._analysis_metrics_cache_key:
        return window._analysis_metrics_cache_result
    result = _get_analysis_metrics(processed, fit, settings)
    window._analysis_metrics_cache_key = token
    window._analysis_metrics_cache_result = result
    return result


def temporal_history_token_for(window, processed: Spectrum | None) -> tuple[object, ...] | None:
    if processed is None:
        return None
    settings = window._current_processing_settings()
    return (
        window.PLOT_MODES[window.plot_selector.currentText()],
        window._source_mode,
        len(processed.wavelengths_nm),
        float(processed.wavelengths_nm[0]) if len(processed.wavelengths_nm) else None,
        float(processed.wavelengths_nm[-1]) if len(processed.wavelengths_nm) else None,
        settings.temporal_smoothing,
        settings.crop_method,
        settings.crop_fraction,
        settings.fit_method,
        settings.fit_window_width_nm,
    )


def apply_temporal_smoothing_for(window, processed: Spectrum | None) -> Spectrum | None:
    if processed is None:
        window._temporal_processed_history.clear()
        window._temporal_history_key = None
        return None

    settings = window._current_processing_settings()
    window_size = max(int(getattr(settings, "temporal_smoothing", 1)), 1)
    key = temporal_history_token_for(window, processed)
    if key != window._temporal_history_key:
        window._temporal_processed_history.clear()
        window._temporal_history_key = key

    window._temporal_processed_history.append(processed)
    if len(window._temporal_processed_history) > window_size:
        window._temporal_processed_history = window._temporal_processed_history[-window_size:]
    if window_size <= 1 or len(window._temporal_processed_history) == 1:
        return processed

    stack = np.vstack([item.values for item in window._temporal_processed_history])
    averaged_values = np.nanmean(stack, axis=0)
    return Spectrum(
        wavelengths_nm=processed.wavelengths_nm.copy(),
        values=averaged_values,
        y_label=processed.y_label,
        acquired_at=processed.acquired_at,
        metadata={
            **processed.metadata,
            "temporal_smoothing": window_size,
            "temporal_average_count": len(window._temporal_processed_history),
        },
    )


def enqueue_plot_processing_for(window) -> None:
    if window._plots_frozen:
        return
    plot_mode = window.PLOT_MODES[window.plot_selector.currentText()]
    raw_spectrum = window._session.get_plot_data(plot_mode)
    settings = window._current_processing_settings()
    window._plot_processing_epoch += 1
    request = ProcessingRequest(spectrum=raw_spectrum, settings=settings, epoch=window._plot_processing_epoch)
    if window._plot_processing_running:
        window._pending_plot_request = request
        return
    start_plot_processing_task_for(window, request)


def start_plot_processing_task_for(window, request: ProcessingRequest) -> None:
    window._plot_processing_running = True
    window._active_plot_processing_epoch = request.epoch
    task = ProcessingTask(request)
    task.signals.finished.connect(window._handle_plot_processing_result)
    window._thread_pool.start(task)


def handle_plot_processing_result_for(window, result: ProcessingResult) -> None:
    if window._closing:
        window._plot_processing_running = False
        window._pending_plot_request = None
        return

    window._plot_processing_running = False
    if result.epoch != window._active_plot_processing_epoch:
        if window._pending_plot_request is not None:
            pending = window._pending_plot_request
            window._pending_plot_request = None
            start_plot_processing_task_for(window, pending)
        return

    processed = apply_temporal_smoothing_for(window, result.processed)
    fit = result.fit
    if processed is not None and fit is not None and processed is not result.processed:
        fit = fit_processed_spectrum(processed, window._current_processing_settings())
    window._last_processing_ms = result.processing_ms
    if result.processing_ms > 0:
        window._processing_rate_hz = 1000.0 / result.processing_ms
        display_period_ms = max(1000.0 / max(window.live_rate_spin.value(), 1e-9), 1.0)
        window._processing_headroom_ratio = display_period_ms / result.processing_ms
    else:
        window._processing_rate_hz = None
        window._processing_headroom_ratio = None
    window._last_processed_plot = processed
    window._last_fit_plot = fit
    window._processed_cache_key = None
    window._processed_cache_result = (processed, fit)
    window._analysis_cache_key = None
    window._analysis_cache_result = (np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64), {})
    window._analysis_metrics_cache_key = None
    window._analysis_metrics_cache_result = {}
    try:
        window._refresh_spectrum_plot(processed, fit)
        window._autoscale_spectrum_plot()
    except Exception as exc:
        window._log_error(f"Spectrum refresh failed: {exc}")
    window._update_poly_warning_indicator(fit)
    window._request_deferred_ui_refresh(trace_plot=True, live_estimate=True, telemetry=True, trace_label="Metric position (nm)")
    if window._pending_plot_request is not None:
        pending = window._pending_plot_request
        window._pending_plot_request = None
        start_plot_processing_task_for(window, pending)


def flush_deferred_ui_refreshes_for(window) -> None:
    started = perf_counter()
    _flush_deferred_ui_refreshes(window)
    elapsed_ms = (perf_counter() - started) * 1000.0
    window._last_deferred_ui_refresh_ms = elapsed_ms
    if bool(getattr(window, "_deep_timing_enabled", False)):
        if elapsed_ms >= 2.0:
            window._log_throttled(
                "gui_deferred_refresh",
                f"GUI deferred refresh: {elapsed_ms:.2f} ms",
                level=logging.INFO,
                min_interval=0.5,
            )


def flush_deferred_display_refreshes_for(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_display_refresh_requested_at", None)
    if requested_at is not None:
        try:
            window._last_display_refresh_delay_ms = max((started - float(requested_at)) * 1000.0, 0.0)
        except (TypeError, ValueError):
            window._last_display_refresh_delay_ms = None
    refresh_state = getattr(window, "_ui_refresh_state", None)
    summary_dirty = bool(getattr(refresh_state, "summary_dirty", False))
    telemetry_dirty = bool(getattr(refresh_state, "telemetry_dirty", False))
    live_estimate_dirty = bool(getattr(refresh_state, "live_estimate_dirty", False))
    metric_dirty = bool(getattr(refresh_state, "metric_plot_dirty", False))
    _flush_deferred_display_refreshes(window)
    elapsed_ms = (perf_counter() - started) * 1000.0
    window._last_deferred_display_refresh_ms = elapsed_ms
    if bool(getattr(window, "_live_active", False)):
        window._live_mode_deferred_display_flush_count = int(getattr(window, "_live_mode_deferred_display_flush_count", 0)) + 1
    if bool(getattr(window, "_deep_timing_enabled", False)):
        if elapsed_ms >= 2.0:
            window._log_throttled(
                "gui_deferred_display_refresh",
                (
                    f"GUI deferred display refresh: {elapsed_ms:.2f} ms | "
                    f"summary={int(summary_dirty)} telemetry={int(telemetry_dirty)} "
                    f"live_estimate={int(live_estimate_dirty)} trace={int(metric_dirty)}"
                ),
                level=logging.INFO,
                min_interval=0.5,
            )


def flush_deferred_metric_refreshes_for(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_metric_refresh_requested_at", None)
    if requested_at is not None:
        try:
            window._last_metric_refresh_delay_ms = max((started - float(requested_at)) * 1000.0, 0.0)
        except (TypeError, ValueError):
            window._last_metric_refresh_delay_ms = None
    refresh_state = getattr(window, "_ui_refresh_state", None)
    metric_dirty = bool(getattr(refresh_state, "metric_plot_dirty", False))
    pending_label = getattr(refresh_state, "pending_metric_label", None) if refresh_state is not None else None
    if getattr(window, "_metric_flush_in_progress", False):
        window._last_deferred_metric_refresh_ms = (perf_counter() - started) * 1000.0
        window._log_throttled(
            "gui_deferred_metric_refresh_reentry",
            "GUI deferred metric refresh skipped because a previous flush is still in progress",
            level=logging.WARNING,
            min_interval=5.0,
        )
        return
    if metric_dirty and not bool(getattr(window, "_sensorgram_frozen", False)):
        window._metric_flush_in_progress = True
        try:
            _flush_deferred_metric_refreshes(window)
        finally:
            window._metric_flush_in_progress = False
    elapsed_ms = (perf_counter() - started) * 1000.0
    window._last_deferred_metric_refresh_ms = elapsed_ms
    if bool(getattr(window, "_live_active", False)):
        window._live_mode_deferred_metric_flush_count = int(getattr(window, "_live_mode_deferred_metric_flush_count", 0)) + 1
    if bool(getattr(window, "_deep_timing_enabled", False)):
        if elapsed_ms >= 2.0:
            window._log_throttled(
                "gui_deferred_metric_refresh",
                (
                    f"GUI deferred metric refresh: {elapsed_ms:.2f} ms | "
                    f"dirty={int(metric_dirty)} label={pending_label or '-'}"
                ),
                level=logging.INFO,
                min_interval=0.5,
            )


def flush_deferred_stats_refreshes_for(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_stats_refresh_requested_at", None)
    if requested_at is not None:
        try:
            window._last_stats_refresh_delay_ms = max((started - float(requested_at)) * 1000.0, 0.0)
        except (TypeError, ValueError):
            window._last_stats_refresh_delay_ms = None
    refresh_state = getattr(window, "_ui_refresh_state", None)
    stats_dirty = bool(getattr(refresh_state, "stats_dirty", False))
    _flush_deferred_stats_refreshes(window)
    elapsed_ms = (perf_counter() - started) * 1000.0
    window._last_deferred_stats_refresh_ms = elapsed_ms
    if bool(getattr(window, "_live_active", False)):
        window._live_mode_deferred_stats_flush_count = int(getattr(window, "_live_mode_deferred_stats_flush_count", 0)) + 1
    if bool(getattr(window, "_deep_timing_enabled", False)):
        if elapsed_ms >= 2.0:
            window._log_throttled(
                "gui_deferred_stats_refresh",
                f"GUI deferred stats refresh: {elapsed_ms:.2f} ms | stats={int(stats_dirty)}",
                level=logging.INFO,
                min_interval=0.5,
            )


def flush_plot_refreshes_for(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_plot_refresh_requested_at", None)
    if requested_at is not None:
        try:
            window._last_plot_refresh_delay_ms = max((started - float(requested_at)) * 1000.0, 0.0)
        except (TypeError, ValueError):
            window._last_plot_refresh_delay_ms = None
    _flush_plot_refreshes(window)
    if bool(getattr(window, "_deep_timing_enabled", False)):
        elapsed_ms = (perf_counter() - started) * 1000.0
        if elapsed_ms >= 2.0:
            window._log_throttled(
                "gui_plot_refresh",
                f"GUI plot refresh: {elapsed_ms:.2f} ms",
                level=logging.INFO,
                min_interval=0.5,
            )


def refresh_plot_for(window) -> None:
    started = perf_counter()
    _refresh_plot(window)
    if bool(getattr(window, "_deep_timing_enabled", False)):
        elapsed_ms = (perf_counter() - started) * 1000.0
        if elapsed_ms >= 2.0:
            window._log_throttled(
                "gui_refresh_plot",
                f"GUI refresh_plot: {elapsed_ms:.2f} ms",
                level=logging.INFO,
                min_interval=0.5,
            )


def handle_residual_toggle_for(window, visible: bool) -> None:
    if hasattr(window, "residual_view"):
        if not visible:
            current_range = window.residual_view.viewRange()[1]
            y_min = float(current_range[0])
            y_max = float(current_range[1])
            if np.isfinite(y_min) and np.isfinite(y_max) and y_max > y_min:
                window._residual_y_range = [y_min, y_max]
        window.residual_view._manual_y_zoom = False
    window._update_residual_axis_visibility(visible)
    window._update_residual_button_icon()
    if visible:
        window._update_residual_view_geometry()
        saved_range = getattr(window, "_residual_y_range", None)
        if (
            isinstance(saved_range, list)
            and len(saved_range) == 2
            and all(isinstance(item, (int, float)) for item in saved_range)
        ):
            y_min = float(saved_range[0])
            y_max = float(saved_range[1])
            if y_max > y_min:
                window.residual_view.setYRange(y_min, y_max, padding=0.0)
                window._residual_axis_autoscaled = True
            else:
                window._residual_axis_autoscaled = False
        else:
            window._residual_axis_autoscaled = False
    else:
        window._residual_axis_autoscaled = False
    window._refresh_plot()
    window._schedule_acquisition_state_persist()


def update_poly_warning_indicator_for(window, fit: Spectrum | None) -> None:
    spin = getattr(window, "poly_order_spin", None)
    if spin is None:
        return
    default_tooltip = str(getattr(window, "_poly_order_default_tooltip", spin.toolTip()) or "").strip()
    if fit is None:
        spin.setStyleSheet("")
        if default_tooltip:
            spin.setToolTip(default_tooltip)
        return
    reduced = bool(fit.metadata.get("fit_order_reduced"))
    requested = fit.metadata.get("requested_polynomial_order")
    used = fit.metadata.get("polynomial_order")
    if not reduced or requested is None or used is None:
        spin.setStyleSheet("")
        if default_tooltip:
            spin.setToolTip(default_tooltip)
        return
    spin.setStyleSheet(
        """
        QSpinBox {
            color: #d64545;
            border: 1px solid #d64545;
        }
        QSpinBox::up-button, QSpinBox::down-button {
            border-left: 1px solid #d64545;
        }
        """
    )
    spin.setToolTip(
        f"{default_tooltip}\nWarning: the polynomial order was reduced from {requested} to {used} for numerical stability."
    )


def set_plots_frozen_for(window, frozen: bool) -> None:
    window._plots_frozen = frozen
    if hasattr(window, "freeze_plots_button") and window.freeze_plots_button.isChecked() != frozen:
        window.freeze_plots_button.blockSignals(True)
        window.freeze_plots_button.setChecked(frozen)
        window.freeze_plots_button.blockSignals(False)
    window._update_freeze_button_icon()
    if not frozen:
        window._refresh_plot()
    window._schedule_acquisition_state_persist()


def set_sensorgram_frozen_for(window, frozen: bool) -> None:
    window._sensorgram_frozen = frozen
    if hasattr(window, "sensorgram_freeze_button") and window.sensorgram_freeze_button.isChecked() != frozen:
        window.sensorgram_freeze_button.blockSignals(True)
        window.sensorgram_freeze_button.setChecked(frozen)
        window.sensorgram_freeze_button.blockSignals(False)
    window._update_sensorgram_freeze_button_icon()
    if not frozen:
        window._refresh_trace_plot("Metric position (nm)")
        window._ui_refresh_state.metric_plot_dirty = False
        window._request_trace_autoscale()
    if hasattr(window, "status_label"):
        freeze_state = "On" if frozen else "Off"
        window.status_label.setText(f"Sensorgram freeze {freeze_state}.")
    window._schedule_acquisition_state_persist()


def clear_trace_history_for(window) -> None:
    if hasattr(window, "_metric_render_display_cache"):
        window._metric_render_display_cache.clear()
    if hasattr(window, "_metric_render_state_cache"):
        window._metric_render_state_cache.clear()
    plot_view_cache = getattr(window, "_plot_view_cache", None)
    if hasattr(window, "_sensorgram_heatmap_history"):
        window._sensorgram_heatmap_history.clear()
    if hasattr(window, "_sensorgram_heatmap_history_revision"):
        window._sensorgram_heatmap_history_revision += 1
    if hasattr(window, "_plot_view_cache"):
        window._plot_view_cache.clear()
    if hasattr(window, "_sensorgram_heatmap_wavelengths"):
        window._sensorgram_heatmap_wavelengths = None
    if hasattr(window, "_sensorgram_heatmap_axis_key"):
        window._sensorgram_heatmap_axis_key = None
    if hasattr(window, "_sensorgram_heatmap_archive_times"):
        window._sensorgram_heatmap_archive_times = None
    if hasattr(window, "_sensorgram_heatmap_archive_matrix"):
        window._sensorgram_heatmap_archive_matrix = None
    if hasattr(window, "_sensorgram_heatmap_archive_request_token"):
        window._sensorgram_heatmap_archive_request_token = None
    if hasattr(window, "_sensorgram_heatmap_archive_pending_token"):
        window._sensorgram_heatmap_archive_pending_token = None
    if hasattr(window, "_sensorgram_heatmap_archive_loading"):
        window._sensorgram_heatmap_archive_loading = False
    if hasattr(window, "_sensorgram_heatmap_archive_task"):
        window._sensorgram_heatmap_archive_task = None
    if hasattr(window, "_sensorgram_metric_archive_reload_request_token"):
        window._sensorgram_metric_archive_reload_request_token = None
    if hasattr(window, "_sensorgram_metric_archive_reload_pending_token"):
        window._sensorgram_metric_archive_reload_pending_token = None
    if hasattr(window, "_sensorgram_metric_archive_reload_loading"):
        window._sensorgram_metric_archive_reload_loading = False
    if hasattr(window, "_sensorgram_metric_archive_reload_task"):
        window._sensorgram_metric_archive_reload_task = None
    if hasattr(window, "_sensorgram_control_step_events"):
        window._sensorgram_control_step_events.clear()
    if hasattr(window, "_sensorgram_control_step_overlay_items"):
        for item in list(getattr(window, "_sensorgram_control_step_overlay_items", [])):
            try:
                item.setVisible(False)
            except Exception:
                pass
    signal = window._session.state.absorbance or window._session.state.sample
    if signal is not None:
        processed, _ = window._get_analysis_processed_spectrum(signal)
        window._metric_reference_processed = (
            processed.with_metadata(role="metric_reference_reset") if processed is not None else None
        )
    else:
        window._metric_reference_processed = None
    window._refresh_trace_plot("Metric position (nm)")
    window._update_trace_stats()
    freeze_state = "On" if bool(getattr(window, "_sensorgram_frozen", False)) else "Off"
    window.status_label.setText(f"Metric history cleared. Sensorgram freeze {freeze_state}.")


def compute_centroid_nm_for(window, processed: Spectrum, fit: Spectrum | None) -> float:
    return _compute_centroid_nm(processed, fit, window._current_processing_settings())


def reference_peak_nm_for_shift_for(window) -> float | None:
    reference = window._metric_reference_processed
    if reference is None:
        return None
    finite = np.isfinite(reference.values)
    if not np.any(finite):
        return None
    finite_indices = np.flatnonzero(finite)
    peak_index = int(finite_indices[int(np.argmax(np.asarray(reference.values, dtype=np.float64)[finite_indices]))])
    return float(reference.wavelengths_nm[peak_index])


def build_summary_text_for(window) -> str:
    current = window._current_settings()
    processing = window._current_processing_settings()
    project_destination = "-"
    if hasattr(window, "recording_project_destination"):
        project_destination = str(window.recording_project_destination() or "").strip() or "-"
    experiment_name = "-"
    if hasattr(window, "recording_experiment_name"):
        experiment_name = str(window.recording_experiment_name() or "").strip() or "-"
    compression_enabled = "-"
    if hasattr(window, "measurement_hdf5_compression_enabled"):
        compression_enabled = "enabled" if window.measurement_hdf5_compression_enabled() else "disabled"
    smoothing_method = str(processing.smoothing_method).replace("_", " ")
    crop_method = str(getattr(processing, "crop_method", "fixed_width")).replace("_", " ")
    fit_method = str(getattr(processing, "fit_method", "none")).replace("_", " ")
    spectrum_tracking_mode = str(processing.spectrum_tracking_mode).replace("_", " ")
    return "\n".join(
        [
            "Recording settings",
            f"  Project destination: {project_destination}",
            f"  Experiment name: {experiment_name}",
            f"  HDF5 compression after recording: {compression_enabled}",
            "",
            "Source and acquisition settings",
            f"  Source mode: {'Spectrometer' if window._source_mode == 'spectrometer' else 'Simulation'}",
            f"  Backend: {window._spectrometer.device_name()}",
            "",
            "Acquisition settings",
            f"  Integration time: {current.integration_time_ms:.3f} ms",
            f"  Accumulation: {current.averages} raw_spectra",
            f"  Correct dark counts: {'yes' if current.correct_dark_counts else 'no'}",
            f"  Correct nonlinearity: {'yes' if current.correct_nonlinearity else 'no'}",
            "",
            "Processing settings",
            f"  Range: {processing.wavelength_min_nm:.2f}-{processing.wavelength_max_nm:.2f} nm",
            f"  Baseline removal: {processing.baseline_method}",
            f"  Spectral smoothing: {smoothing_method} ({processing.smoothing_window})",
            f"  Temporal smoothing: {getattr(processing, 'temporal_smoothing', 1)}",
            f"  Crop: {crop_method} ({getattr(processing, 'crop_fraction', 0.7):.2f})",
            f"  Fit: {fit_method}",
            f"  Polynomial order: {processing.polynomial_order}",
            f"  Fit width: {processing.fit_window_width_nm:.0f} nm",
            f"  Analysis resolution: {getattr(processing, 'analysis_resolution_nm', 0.001):.6f} nm",
            f"  Noise window: {processing.trace_noise_window_s:.1f} s",
            f"  Spectrum trace: {spectrum_tracking_mode}",
            f"  Metric traces: {', '.join(processing.trace_metrics)}",
        ]
    )


def update_live_estimate_for(window) -> None:
    skipped_rate_hz = live_skip_rate_hz_for(window)
    proc_text = "-" if window._last_processing_ms is None else f"{window._last_processing_ms:.1f} ms"
    headroom_text = headroom_value_text_for(window._processing_headroom_ratio)
    source_rate_text = "-" if window._effective_raw_rate_hz is None else f"{window._effective_raw_rate_hz:.1f} Hz"
    desired_refresh_text = f"{window.live_rate_spin.value():.2f} Hz"
    actual_refresh_text = "-" if getattr(window, "_actual_plot_refresh_rate_hz", None) is None else f"{window._actual_plot_refresh_rate_hz:.2f} Hz"
    wait_text = ""
    if processing_debug_mode_enabled() and window._last_processing_queue_wait_ms is not None:
        wait_text = f" | wait {window._last_processing_queue_wait_ms:.1f} ms"
    window.live_estimate.setText(
        f"src {source_rate_text} | disp target {desired_refresh_text} | recent avg {actual_refresh_text} | "
        f"proc {proc_text}{wait_text} | head {headroom_text} | skip {skipped_rate_hz:.1f} Hz"
    )
    window._ui_refresh_state.live_estimate_dirty = False


def build_spectrometer_stats_text_for(window) -> str:
    spacing_text = _timing_plain_text(getattr(window, "_last_spacing_ms", None))
    rate_text = "-"
    effective_rate = getattr(window, "_effective_raw_rate_hz", None)
    if effective_rate is not None:
        try:
            rate_text = f"{float(effective_rate):.1f} Hz"
        except (TypeError, ValueError):
            rate_text = "-"
    overhead_text = _timing_plain_text(getattr(window, "_last_overhead_ms", None))
    return f"spacing {spacing_text} | rate {rate_text} | ovh {overhead_text}"


def refresh_spectrometer_stats_for(window) -> None:
    if not hasattr(window, "spectrometer_stats_label"):
        return
    window.spectrometer_stats_label.setText(build_spectrometer_stats_text_for(window))


def _timing_share_text(value_ms: float | None, total_ms: float | None) -> str:
    if value_ms is None:
        return "-"
    try:
        value = float(value_ms)
        total = float(total_ms) if total_ms is not None else None
    except (TypeError, ValueError):
        return "-"
    if total is None or not np.isfinite(value) or not np.isfinite(total) or total <= 0:
        return f"{value:.1f} ms"
    return f"{value:.1f} ms ({value / total * 100.0:.1f}%)"


def refresh_telemetry_for(window) -> None:
    refresh_spectrometer_stats_for(window)
    if window._last_elapsed_ms is None:
        if hasattr(window, "telemetry_label"):
            window.telemetry_label.setStyleSheet("")
        window.telemetry_label.setText("waiting for first spectrum")
        window._ui_refresh_state.telemetry_dirty = False
        return
    if hasattr(window, "telemetry_label"):
        window.telemetry_label.setStyleSheet("")
    window.telemetry_label.setText(build_pipeline_telemetry_text_for(window))
    window._ui_refresh_state.telemetry_dirty = False


def build_pipeline_telemetry_text_for(window) -> str:
    breakdown = build_pipeline_timing_breakdown_for(window)
    spacing = _timing_plain_text(getattr(window, "_last_spacing_ms", None))
    overhead = _timing_plain_text(getattr(window, "_last_overhead_ms", None))
    latency = _timing_plain_text(getattr(window, "_last_elapsed_ms", None))
    processing_total_ms = None
    if breakdown["processing_wait_ms"] is not None or breakdown["processing_ms"] is not None:
        processing_total_ms = float(breakdown["processing_wait_ms"] or 0.0) + float(breakdown["processing_ms"] or 0.0)
    processing_text = _timing_plain_text(processing_total_ms)
    plot_text = _timing_plain_text(breakdown["plot_render_ms"])
    display_text = _timing_plain_text(breakdown["deferred_display_ms"])
    stats_text = _timing_plain_text(breakdown["deferred_stats_ms"])
    idle_text = _timing_plain_text(breakdown["idle_ms"])
    live_result_queue = getattr(window, "_live_result_queue", None)
    live_processed_queue = getattr(window, "_live_processed_queue", None)
    live_result_queue_text = "-"
    live_processed_queue_text = "-"
    try:
        if live_result_queue is not None:
            live_result_queue_text = str(max(int(live_result_queue.qsize()), 0))
    except (AttributeError, NotImplementedError, OSError, TypeError, ValueError):
        live_result_queue_text = "-"
    try:
        if live_processed_queue is not None:
            live_processed_queue_text = str(max(int(live_processed_queue.qsize()), 0))
    except (AttributeError, NotImplementedError, OSError, TypeError, ValueError):
        live_processed_queue_text = "-"
    displayed = "-"
    if window._last_display_average_count is not None and window._last_display_period_ms is not None:
        displayed = f"{window._last_display_average_count}/{window._last_display_period_ms:.0f}"
    return (
        f"gap {spacing} | acq {latency} | proc {processing_text} | "
        f"plot {plot_text} | disp {display_text} | stats {stats_text} | idle {idle_text} | "
        f"srcq {live_result_queue_text} | procq {live_processed_queue_text} | "
        f"ovh {overhead} | show {displayed}"
    )


def live_skip_rate_hz_for(window) -> float:
    if window._live_display_started_at is None:
        return 0.0
    elapsed_s = perf_counter() - window._live_display_started_at
    if elapsed_s <= 0:
        return 0.0
    return float(window._live_display_dropped_frames) / elapsed_s


def headroom_value_text_for(headroom_ratio: float | None) -> str:
    if headroom_ratio is None or not np.isfinite(float(headroom_ratio)):
        return "-"
    return f"{headroom_ratio:.2f}x"


def handle_live_setting_change_for(window) -> None:
    window._ui_refresh_state.live_estimate_dirty = True
    if window._live_active:
        window._display_window_ms = 1000.0 / max(window.live_rate_spin.value(), 1e-9)
        window._live_ui_refresh_delay_ms = max(int(round(window._display_window_ms)), 16)
        window._reset_live_accumulator()
        window._live_display_dropped_frames = 0
        if window._live_worker is not None:
            window._live_worker.update_settings(window._current_settings())
        if window._live_processing_worker is not None:
            window._live_processing_worker.update_settings(window._current_processing_settings())
        # Re-arm the live polling cadence so the new refresh rate takes effect immediately.
        if hasattr(window, "_ui_task_scheduler"):
            window._ui_task_scheduler.cancel("live_visual_refresh")
            window._ui_task_scheduler.cancel("live_acquisition")
            window._ui_task_scheduler.cancel("live_processed")
        window._ui_refresh_state.metric_plot_dirty = True
        window._ui_refresh_state.telemetry_dirty = True
        window._ui_refresh_state.pending_metric_label = "Metric position (nm)"
        window._request_live_visual_refresh(0)
        window._request_trace_autoscale()
        window.status_label.setText("Live display window reset after settings change.")
    elif window._source_mode == "simulation":
        window._session.set_sample(window._build_simulation_spectrum("sample"))
        window._schedule_processing_refresh()
        window._request_trace_autoscale()
    window._schedule_acquisition_state_persist()


def handle_simulation_output_rate_change_for(window) -> None:
    if window._live_active and window._source_mode == "simulation" and window._live_worker is not None:
        window._live_worker.update_cycle_period(1.0 / max(window.sim_output_rate_spin.value(), 1e-9))
        window._request_deferred_ui_refresh(trace_plot=True, live_estimate=True)
        window._request_plot_refresh()
        window._request_trace_autoscale()
    window._schedule_acquisition_state_persist()


def update_window_mode_label_for(window) -> None:
    if not hasattr(window, "_window_mode_label"):
        return
    source_name = "Spectrometer" if window._source_mode == "spectrometer" else "Simulation"
    if window._measurement_active:
        text = f"Recording | {source_name}"
        tooltip = "A measurement is currently saving data."
        window._window_mode_label.setStyleSheet("color: #ff5b5b; font-weight: 700;")
        blink_visible = bool(getattr(window, "_recording_blink_visible", True))
    elif window._live_active:
        text = f"Live mode | {source_name}"
        tooltip = "Live acquisition is running."
        window._window_mode_label.setStyleSheet("")
    else:
        text = f"Free mode | {source_name}"
        tooltip = "The app is open but no measurement is running."
        window._window_mode_label.setStyleSheet("")
    window._window_mode_label.setText(text)
    window._window_mode_label.setToolTip(tooltip)
    if hasattr(window, "_window_mode_icon_label"):
        if window._measurement_active:
            if blink_visible:
                source_icon = transport_icon(window._theme_mode, "record")
                window._window_mode_icon_label.setPixmap(source_icon.pixmap(16, 16))
            else:
                blank = QPixmap(16, 16)
                blank.fill(Qt.GlobalColor.transparent)
                window._window_mode_icon_label.setPixmap(blank)
            window._window_mode_icon_label.setToolTip("Recording is active.")
        else:
            source_icon = prism_tab_icon() if window._source_mode == "spectrometer" else math_function_tab_icon()
            window._window_mode_icon_label.setPixmap(source_icon.pixmap(16, 16))
            window._window_mode_icon_label.setToolTip(f"{source_name} source")


# Convenience aliases for window delegation.
update_spectrum_stats_for = _update_spectrum_stats
update_metric_stats_for = _update_metric_stats
update_trace_stats_for = _update_metric_stats
autoscale_spectrum_plot_for = _autoscale_spectrum_plot
apply_processing_range_to_spectrum_plot_for = _apply_processing_range_to_spectrum_plot
autoscale_metric_plot_for = _autoscale_metric_plot
autoscale_trace_plot_for = _autoscale_metric_plot
autoscale_residual_axis_for = _autoscale_residual_axis
update_residual_view_geometry_for = _update_residual_view_geometry
update_residual_axis_visibility_for = _update_residual_axis_visibility
request_metric_autoscale_for = _request_metric_autoscale
request_trace_autoscale_for = _request_metric_autoscale
handle_spectrum_mouse_moved_for = _handle_spectrum_mouse_moved
handle_metric_mouse_moved_for = _handle_metric_mouse_moved
handle_trace_mouse_moved_for = _handle_metric_mouse_moved
refresh_metric_plot_for = _refresh_metric_plot
refresh_trace_plot_for = _refresh_metric_plot
render_metric_series_for = _render_metric_series
render_trace_series_for = _render_metric_series
