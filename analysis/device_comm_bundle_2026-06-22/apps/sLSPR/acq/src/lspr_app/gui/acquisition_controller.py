from __future__ import annotations

import multiprocessing as mp
import queue
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import numpy as np
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFileDialog, QInputDialog

from lspr_app.app import create_spectrometer
from lspr_app.domain.models import Spectrum
from lspr_app.domain.processing import processing_debug_mode_enabled
from lspr_app.domain.session import MeasurementError
from lspr_app.device.simulated import SimulatedSpectrometer
from lspr_app.gui.icon_helpers import flow_tabler_icon, math_function_tab_icon, prism_tab_icon, tint_tabler_icon, transport_icon
from lspr_app.gui.experiment_control_runtime import (
    experiment_runtime_label,
    experiment_runtime_snapshot,
    experiment_runtime_state_name,
)
from lspr_app.gui.main_window_headers import update_source_link_buttons
from lspr_app.gui.workers import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionTask,
    LiveAcquisitionEvent,
    LiveAcquisitionWorker,
    LiveProcessedEvent,
    LiveProcessingWorker,
    MeasurementCompressionResult,
    MeasurementCompressionTask,
)
from lspr_app.storage.hdf5_export import AsyncHDF5MeasurementWriter


def _flush_live_processing_logs(window) -> None:
    log_queue = getattr(window, "_live_processing_log_queue", None)
    if log_queue is None:
        return
    while True:
        try:
            levelno, source, message = log_queue.get_nowait()
        except queue.Empty:
            return
        except Exception:
            return
        try:
            window._log_event(int(levelno), str(message), source=str(source))
        except Exception:
            continue


def _queue_size_safe(queue_obj) -> int | None:
    try:
        size = queue_obj.qsize()
    except (AttributeError, NotImplementedError, OSError):
        return None
    try:
        return max(int(size), 0)
    except (TypeError, ValueError):
        return None


def _log_live_timing(window, key: str, message: str) -> None:
    if not bool(getattr(window, "_deep_timing_enabled", False)):
        return
    window._log_throttled(key, message, level=logging.INFO, min_interval=0.5)


def _request_live_acquisition_poll_for(window, delay_ms: float) -> None:
    request = getattr(window, "_request_live_acquisition_poll", None)
    if callable(request):
        request(delay_ms)


def _update_live_source_rate_from_event(window, event: LiveAcquisitionEvent, now: float) -> None:
    previous_finish = getattr(window, "_raw_last_finish_ts", None)
    previous_sample_index = getattr(window, "_raw_last_sample_index", None)
    current_sample_index = getattr(event, "source_sample_index", None)
    window._raw_last_finish_ts = now
    if (
        previous_finish is not None
        and previous_sample_index is not None
        and current_sample_index is not None
    ):
        try:
            delta_samples = int(current_sample_index) - int(previous_sample_index)
        except (TypeError, ValueError):
            delta_samples = 0
        if delta_samples > 0:
            spacing_ms = max((now - previous_finish) * 1000.0 / float(delta_samples), 0.0)
            window._last_spacing_ms = spacing_ms
            if spacing_ms > 0:
                window._effective_raw_rate_hz = 1000.0 / spacing_ms
            window._raw_last_sample_index = int(current_sample_index)
            return
    window._last_spacing_ms = None if previous_finish is None else (now - previous_finish) * 1000.0
    if window._last_spacing_ms and window._last_spacing_ms > 0:
        window._effective_raw_rate_hz = 1000.0 / window._last_spacing_ms
    if current_sample_index is not None:
        try:
            window._raw_last_sample_index = int(current_sample_index)
        except (TypeError, ValueError):
            pass


def _start_measurement_file_compression(window, path: Path) -> None:
    if hasattr(window, "_set_measurement_compression_busy_indicator"):
        window._set_measurement_compression_busy_indicator(True)
    task = MeasurementCompressionTask(path)
    task.signals.finished.connect(window._handle_measurement_file_compression_finished)
    task.signals.failed.connect(window._handle_measurement_file_compression_failed)
    window._measurement_compression_task = task
    window._thread_pool.start(task)


def _handle_measurement_file_compression_finished(window, result: object) -> None:
    window._measurement_compression_task = None
    if hasattr(window, "_set_measurement_compression_busy_indicator"):
        window._set_measurement_compression_busy_indicator(False)
    if not isinstance(result, MeasurementCompressionResult):
        window._measurement_path = None
        window.status_label.setText("Measurement stopped.")
        window._log_warning("Measurement file compression finished with an unexpected result payload.")
        window._refresh_plot()
        window._refresh_trace_plot("Metric position (nm)")
        window._request_trace_autoscale()
        window._refresh_session_summary(force=True)
        window._update_window_mode_label()
        return
    window._measurement_path = None
    window.status_label.setText("Measurement stopped.")
    window._log_success("Measurement file compression finished.")
    window._refresh_plot()
    window._refresh_trace_plot("Metric position (nm)")
    window._request_trace_autoscale()
    window._refresh_session_summary(force=True)
    window._update_window_mode_label()


def _handle_measurement_file_compression_failed(window, message: str) -> None:
    window._measurement_compression_task = None
    if hasattr(window, "_set_measurement_compression_busy_indicator"):
        window._set_measurement_compression_busy_indicator(False)
    window._measurement_path = None
    window.status_label.setText("Measurement stopped.")
    window._log_warning(f"Measurement file compression failed: {message}")
    window._refresh_plot()
    window._refresh_trace_plot("Metric position (nm)")
    window._request_trace_autoscale()
    window._refresh_session_summary(force=True)
    window._update_window_mode_label()


def _archive_live_sample_if_needed(window, spectrum: Spectrum) -> bool:
    if window._measurement_writer is None:
        return False
    values = np.asarray(spectrum.values, dtype=np.float64)
    wavelengths = np.asarray(spectrum.wavelengths_nm, dtype=np.float64)
    peak_nm = float("nan")
    if len(values) > 0 and len(wavelengths) > 0:
        finite = np.isfinite(values)
        if np.any(finite):
            safe_values = np.where(finite, values, -np.inf)
            peak_index = int(np.argmax(safe_values))
            if 0 <= peak_index < len(wavelengths):
                peak_nm = float(wavelengths[peak_index])
    elapsed_s = 0.0
    if window._measurement_started_at is not None:
        elapsed_s = (spectrum.acquired_at - window._measurement_started_at).total_seconds()
    try:
        window._measurement_writer.append_batch([spectrum], [elapsed_s], [peak_nm])
    except Exception:
        logging.getLogger("lspr_app.storage").exception("Failed to enqueue measurement frame for storage.")
        return False
    return True


def flush_live_recording_results(window) -> None:
    started = perf_counter()
    queue_obj = getattr(window, "_live_recording_queue", None)
    if queue_obj is None:
        return
    drained_events: list[LiveAcquisitionEvent] = []
    while True:
        try:
            event = queue_obj.get_nowait()
        except queue.Empty:
            break
        drained_events.append(event)

    live_recording_depth = _queue_size_safe(queue_obj)
    if live_recording_depth is not None:
        window._live_recording_queue_max_depth = max(
            int(getattr(window, "_live_recording_queue_max_depth", 0)),
            live_recording_depth,
        )

    if not drained_events:
        window._last_live_recording_flush_ms = (perf_counter() - started) * 1000.0
        return

    window._last_live_recording_flush_ms = (perf_counter() - started) * 1000.0
    valid_events = [
        event
        for event in drained_events
        if event.error is None
        and event.result is not None
        and event.source_epoch == window._source_epoch
    ]
    measurement_started_at = getattr(window, "_measurement_started_at", None)
    if measurement_started_at is not None:
        valid_events = [
            event
            for event in valid_events
            if event.result is not None and event.result.spectrum.acquired_at >= measurement_started_at
        ]

    window._raw_acquired_count = int(getattr(window, "_raw_acquired_count", 0)) + len(valid_events)
    window._raw_recording_enqueued_count = int(getattr(window, "_raw_recording_enqueued_count", 0)) + len(valid_events)

    for event in valid_events:
        if _archive_live_sample_if_needed(window, event.result.spectrum):
            window._raw_recording_written_count = int(getattr(window, "_raw_recording_written_count", 0)) + 1


def _suspend_spectrometer_backend_for_live(window) -> None:
    if getattr(window, "_source_mode", "spectrometer") != "spectrometer":
        return
    if getattr(window, "_hardware_backend_suspended_for_live", False):
        return
    spectrometer = getattr(window, "_spectrometer", None)
    close = getattr(spectrometer, "close", None)
    if callable(close):
        try:
            close()
            window._hardware_backend_suspended_for_live = True
            window._log_info("Spectrometer backend released for live acquisition.")
        except Exception as exc:
            window._log_warning(f"Could not release spectrometer backend for live acquisition: {exc}")


def _restore_spectrometer_backend_after_live(window) -> None:
    if not getattr(window, "_hardware_backend_suspended_for_live", False):
        return
    try:
        spectrometer = create_spectrometer(force_simulator=bool(getattr(window._launch_profile_spec, "force_simulator", False)))
    except Exception as exc:
        window._log_warning(f"Could not restore spectrometer backend after live acquisition: {exc}")
        return
    window._spectrometer = spectrometer
    window._capabilities = spectrometer.capabilities()
    window._hardware_available = not isinstance(spectrometer, SimulatedSpectrometer)
    window._hardware_backend_suspended_for_live = False
    window._log_info("Spectrometer backend restored after live acquisition.")


def request_manual_acquisition(window, kind: str) -> None:
    window._log_info(f"Manual {kind} acquisition requested.")
    if window._live_active:
        current_sample = window._session.state.sample
        if current_sample is None:
            window._log_warning(f"No live sample available to cache as {kind}.")
            window.status_label.setText(f"No live sample available for {kind} capture.")
            return
        if kind == "dark":
            window._session.set_dark(current_sample)
        else:
            window._session.set_reference(current_sample)
        from lspr_app.storage.measurement_archive import ensure_temp_measurement_writer
        ensure_temp_measurement_writer(window)


        if window._measurement_writer is not None:
            window._measurement_writer.update_baselines(
                window._session.state.dark,
                window._session.state.reference,
            )
        target_plot = "Dark" if kind == "dark" else "Reference"
        if window.plot_selector.currentText() != target_plot:
            window.plot_selector.blockSignals(True)
            window.plot_selector.setCurrentText(target_plot)
            window.plot_selector.blockSignals(False)
        window._refresh_spectrum_plot(window._session.get_plot_data(window.PLOT_MODES[target_plot]), None)
        window._update_dark_reference_button_icons()
        window._request_deferred_ui_refresh(telemetry=True)
        window._schedule_acquisition_state_persist()
        window.status_label.setText(f"{kind.capitalize()} cached from live sample.")
        return
    if window._live_worker is not None and window._live_worker.is_alive():
        window._pending_manual_kind = kind
        window.status_label.setText(f"{kind.capitalize()} acquisition queued while live worker stops...")
        window._log_info(f"Queued {kind} acquisition until live worker stops.")
        return
    if window._busy:
        window._pending_manual_kind = kind
        window.status_label.setText(f"{kind.capitalize()} acquisition queued...")
        window._log_info(f"Queued {kind} acquisition while busy.")
        return
    window.status_label.setText(f"Acquiring {kind} spectrum...")
    window._start_acquisition(kind)


def handle_acquisition_success(window, kind: str, result: AcquisitionResult) -> None:
    if window._closing:
        window._busy = False
        return
    if result.source_epoch != window._source_epoch:
        window._busy = False
        return
    spectrum = result.spectrum
    should_refresh_plot = kind != "sample" or not window._live_active
    now = perf_counter()
    previous_finish = window._raw_last_finish_ts
    window._raw_last_finish_ts = now
    window._last_elapsed_ms = result.elapsed_ms
    window._last_spacing_ms = None if previous_finish is None else (now - previous_finish) * 1000.0
    if window._source_mode == "simulation":
        window._last_overhead_ms = None
    else:
        expected_budget_ms = spectrum.metadata.get("integration_time_ms", 0.0) * spectrum.metadata.get("averages", 1)
        if isinstance(expected_budget_ms, (int, float)):
            window._last_overhead_ms = max(result.elapsed_ms - float(expected_budget_ms), 0.0)
        else:
            window._last_overhead_ms = None
    if window._last_spacing_ms and window._last_spacing_ms > 0:
        window._effective_raw_rate_hz = 1000.0 / window._last_spacing_ms

    try:
        if kind == "dark":
            window._session.set_dark(spectrum)
            if window._measurement_writer is not None:
                window._measurement_writer.update_baselines(window._session.state.dark, window._session.state.reference)
        elif kind == "reference":
            window._session.set_reference(spectrum)
            if window._measurement_writer is not None:
                window._measurement_writer.update_baselines(window._session.state.dark, window._session.state.reference)
        elif kind == "sample" and window._live_active:
            should_refresh_plot = False
        elif kind == "sample":
            window._session.set_sample(spectrum)
        else:
            raise ValueError(f"Unsupported acquisition kind: {kind}")
    except MeasurementError as exc:
        window._show_error(str(exc))
        return
    finally:
        window._busy = False
        window._set_measurement_buttons_enabled(True)

    window._request_deferred_ui_refresh(telemetry=True)
    if kind in {"dark", "reference"}:
        target_plot = "Dark" if kind == "dark" else "Reference"
        if window.plot_selector.currentText() != target_plot:
            window.plot_selector.blockSignals(True)
            window.plot_selector.setCurrentText(target_plot)
            window.plot_selector.blockSignals(False)
        selected_plot_mode = window.PLOT_MODES[window.plot_selector.currentText()]
        selected_plot = window._session.get_plot_data(selected_plot_mode)
        if selected_plot is not None:
            window._refresh_spectrum_plot(selected_plot, None)
        window._update_dark_reference_button_icons()
        window._log_success(f"{kind.capitalize()} spectrum acquired.")
    elif kind == "sample" and not window._live_active:
        window._log_success("Sample spectrum acquired.")
    elif kind == "sample":
        window._log_throttled(
            "live_sample",
            "Live sample updated.",
            level=logging.DEBUG,
            min_interval=1.0,
        )

    if window._pending_auto_integration:
        window._pending_auto_integration = False
        QTimer.singleShot(0, window._auto_set_integration_time)
        return

    if window._pending_source_mode is not None:
        pending_mode = window._pending_source_mode
        restart_live = window._resume_live_after_source_switch
        window._pending_source_mode = None
        window._resume_live_after_source_switch = False
        window.source_tabs.blockSignals(True)
        window.source_tabs.setCurrentIndex(0 if pending_mode == "spectrometer" else 1)
        window.source_tabs.blockSignals(False)
        window._apply_source_mode(pending_mode, restart_live=restart_live)
        return

    if window._live_active and kind == "sample":
        pass
    else:
        window.status_label.setText(f"{kind.capitalize()} spectrum acquired successfully.")
        if window._pending_manual_kind is not None:
            pending = window._pending_manual_kind
            window._pending_manual_kind = None
            QTimer.singleShot(0, lambda kind=pending: window._start_acquisition(kind))
        elif window._resume_live_after_manual and kind in {"dark", "reference"}:
            window._resume_live_after_manual = False
            QTimer.singleShot(0, window._start_live_acquisition)
    if should_refresh_plot:
        window._refresh_plot()


def flush_live_acquisition_results(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_live_result_requested_at", None)
    if requested_at is not None:
        interval_ms = float(getattr(window, "_live_result_requested_delay_ms", 0.0) or 0.0)
        try:
            window._last_live_result_poll_delay_ms = max((started - float(requested_at)) * 1000.0 - interval_ms, 0.0)
        except (TypeError, ValueError):
            window._last_live_result_poll_delay_ms = None
    window._live_result_requested_at = started
    _flush_live_processing_logs(window)
    flush_live_recording_results(window)
    drained_events: list[LiveAcquisitionEvent] = []
    dropped_events = 0
    while True:
        try:
            event = window._live_result_queue.get_nowait()
        except queue.Empty:
            break
        if drained_events:
            dropped_events += 1
        drained_events.append(event)

    live_result_depth = _queue_size_safe(window._live_result_queue)
    if live_result_depth is not None:
        window._live_result_queue_max_depth = max(int(getattr(window, "_live_result_queue_max_depth", 0)), live_result_depth)

    if not drained_events:
        window._last_live_acquisition_flush_ms = (perf_counter() - started) * 1000.0
        if window._live_worker is not None and not window._live_worker.is_alive():
            window._live_worker = None
            if window._pending_manual_kind is not None:
                pending = window._pending_manual_kind
                window._pending_manual_kind = None
                QTimer.singleShot(0, lambda kind=pending: window._start_acquisition(kind))
        elif window._live_active and window._live_worker is not None and window._live_worker.is_alive():
            _request_live_acquisition_poll_for(window, window._live_ui_refresh_delay_ms)
        return

    drain_ms = (perf_counter() - started) * 1000.0
    window._last_live_acquisition_flush_ms = drain_ms

    window._ui_preview_replaced_count = int(getattr(window, "_ui_preview_replaced_count", 0)) + dropped_events

    latest_event: LiveAcquisitionEvent | None = None
    for event in reversed(drained_events):
        if event.error is None and event.result is not None:
            latest_event = event
            break

    if latest_event.error is not None:
        if latest_event.source_epoch != window._source_epoch:
            return
        window._live_active = False
        window._live_worker = None
        window._handle_acquisition_error(latest_event.source_epoch, latest_event.error)
        return

    if latest_event.result is not None:
        if latest_event.source_epoch != window._source_epoch:
            return
        result = latest_event.result
        spectrum = result.spectrum
        now = latest_event.produced_at_perf if latest_event.produced_at_perf is not None else perf_counter()
        window._last_elapsed_ms = result.elapsed_ms
        _update_live_source_rate_from_event(window, latest_event, now)
        if window._source_mode == "simulation":
            window._last_overhead_ms = None
        else:
            expected_budget_ms = spectrum.metadata.get("integration_time_ms", 0.0) * spectrum.metadata.get("averages", 1)
            if isinstance(expected_budget_ms, (int, float)):
                window._last_overhead_ms = max(result.elapsed_ms - float(expected_budget_ms), 0.0)
            else:
                window._last_overhead_ms = None
        if window._effective_raw_rate_hz is not None and bool(getattr(window, "_deep_timing_enabled", False)):
            window._log_throttled(
                "raw_rate",
                (
                    "Raw source rate "
                    f"{window._effective_raw_rate_hz:.2f} Hz | "
                    f"display_rate={window.live_rate_spin.value():.2f} Hz | "
                    f"sample={getattr(latest_event, 'source_sample_index', 0)}"
                ),
                level=logging.DEBUG,
                min_interval=1.0,
            )
        spacing_text = "-" if window._last_spacing_ms is None else f"{window._last_spacing_ms:.1f} ms"
        _log_live_timing(
            window,
            "live_acquisition_queue_drain",
            (
                "Live acquisition queue drain: "
                f"{drain_ms:.2f} ms | acq={result.elapsed_ms:.1f} ms | "
                f"gap={spacing_text} | dropped={dropped_events}"
            ),
        )
        if bool(getattr(window, "_deep_timing_enabled", False)):
            window._log_throttled(
                "live_acq_flush",
                f"Live acquisition flush: {drain_ms:.2f} ms | dropped={dropped_events}",
                level=logging.DEBUG,
                min_interval=1.0,
            )
        refresh_state = window._ui_refresh_state
        refresh_state.telemetry_dirty = True
        refresh_state.live_estimate_dirty = True
        if window._live_active:
            _request_live_acquisition_poll_for(window, window._live_ui_refresh_delay_ms)
        window._ui_preview_displayed_count = int(getattr(window, "_ui_preview_displayed_count", 0)) + 1

    if dropped_events > 0:
        window._live_display_dropped_frames += dropped_events

    if window._live_worker is not None and not window._live_worker.is_alive():
        window._live_worker = None
        if window._pending_manual_kind is not None and not window._live_active and not window._busy:
            pending = window._pending_manual_kind
            window._pending_manual_kind = None
            QTimer.singleShot(0, lambda kind=pending: window._start_acquisition(kind))
    elif window._live_active and window._live_worker is not None and window._live_worker.is_alive():
        _request_live_acquisition_poll_for(window, window._live_ui_refresh_delay_ms)


def flush_live_processed_results(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_live_processed_requested_at", None)
    if requested_at is not None:
        try:
            requested_delay_ms = float(getattr(window, "_live_processed_requested_delay_ms", 0.0) or 0.0)
            window._last_live_processed_poll_delay_ms = max((started - float(requested_at)) * 1000.0 - requested_delay_ms, 0.0)
        except (TypeError, ValueError):
            window._last_live_processed_poll_delay_ms = None
    _flush_live_processing_logs(window)
    latest_event: LiveProcessedEvent | None = None
    dropped_events = 0
    while True:
        try:
            event = window._live_processed_queue.get_nowait()
        except queue.Empty:
            break
        if latest_event is not None:
            dropped_events += 1
        latest_event = event

    live_processed_depth = _queue_size_safe(window._live_processed_queue)
    if live_processed_depth is not None:
        window._live_processed_queue_max_depth = max(int(getattr(window, "_live_processed_queue_max_depth", 0)), live_processed_depth)

    if latest_event is None:
        window._last_live_processed_flush_ms = (perf_counter() - started) * 1000.0
        if window._live_processing_worker is not None and window._live_processing_worker.is_alive() and window._live_active:
            _request_live_acquisition_poll_for(window, window._live_ui_refresh_delay_ms)
        return

    drain_ms = (perf_counter() - started) * 1000.0
    window._last_live_processed_flush_ms = drain_ms

    if latest_event.error is not None:
        if latest_event.source_epoch != window._source_epoch:
            return
        window._live_active = False
        window._live_processing_worker = None
        window._handle_acquisition_error(latest_event.source_epoch, latest_event.error)
        return

    if latest_event.result is None or latest_event.result.processed is None:
        if window._live_processing_worker is not None and window._live_processing_worker.is_alive() and window._live_active:
            _request_live_acquisition_poll_for(window, window._live_ui_refresh_delay_ms)
        return

    if latest_event.source_epoch != window._source_epoch:
        return

    result = latest_event.result
    processed = result.processed
    fit = result.fit
    if processed is None:
        return

    if window._source_mode == "simulation":
        display_window_ms = max(window._display_window_ms, window._current_simulation_interval_ms())
    else:
        display_window_ms = window._display_window_ms
    window._session.set_sample(
        processed.with_metadata(
            display_average_count=1,
            display_window_ms=display_window_ms if display_window_ms > 0 else 0.0,
            display_refresh_hz=window.live_rate_spin.value(),
        )
    )
    window._last_display_average_count = 1
    window._last_display_period_ms = display_window_ms
    window._last_processing_ms = result.processing_ms
    window._last_processing_queue_wait_ms = result.queue_wait_ms
    _log_live_timing(
        window,
        "live_processing_queue_drain",
        (
            "Live processing queue drain: "
            f"{drain_ms:.2f} ms | proc={result.processing_ms:.1f} ms | "
                f"wait={result.queue_wait_ms:.1f} ms | dropped={dropped_events}"
        ),
    )
    if bool(getattr(window, "_deep_timing_enabled", False)):
        window._log_throttled(
            "live_processed_flush",
            f"Live processing flush: {drain_ms:.2f} ms | dropped={dropped_events}",
            level=logging.DEBUG,
            min_interval=1.0,
        )
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
    window._analysis_cache_result = (
        np.empty(0, dtype=np.float64),
        np.empty(0, dtype=np.float64),
        {},
    )
    window._analysis_metrics_cache_key = None
    window._analysis_metrics_cache_result = {}
    window._update_poly_warning_indicator(fit)
    window._live_plot_update_counter = int(getattr(window, "_live_plot_update_counter", 0)) + 1
    spectrum_render_started = perf_counter()
    try:
        window._refresh_spectrum_plot(processed, fit)
        window._autoscale_spectrum_plot()
        window._update_spectrum_stats(processed, fit)
        spectrum_render_finished = perf_counter()
        window._last_plot_refresh_ms = (spectrum_render_finished - spectrum_render_started) * 1000.0
        previous_finish = getattr(window, "_last_plot_refresh_finished_at", None)
        window._last_plot_refresh_gap_ms = (
            None if previous_finish is None else (spectrum_render_finished - float(previous_finish)) * 1000.0
        )
        window._last_plot_refresh_finished_at = spectrum_render_finished
        refresh_timestamps = getattr(window, "_plot_refresh_timestamps", None)
        if refresh_timestamps is None:
            refresh_timestamps = []
            window._plot_refresh_timestamps = refresh_timestamps
        refresh_timestamps.append(spectrum_render_finished)
        window_frames = max(2, int(getattr(window, "_plot_refresh_rate_window_frames", 5)))
        if len(refresh_timestamps) > window_frames:
            refresh_timestamps = refresh_timestamps[-window_frames:]
        window._plot_refresh_timestamps = refresh_timestamps
        if len(window._plot_refresh_timestamps) >= 2:
            first_timestamp = float(window._plot_refresh_timestamps[0])
            last_timestamp = float(window._plot_refresh_timestamps[-1])
            span_s = last_timestamp - first_timestamp
            if span_s > 0:
                window._actual_plot_refresh_rate_hz = (len(window._plot_refresh_timestamps) - 1) / span_s
            else:
                window._actual_plot_refresh_rate_hz = None
        else:
            window._actual_plot_refresh_rate_hz = None
    except Exception as exc:
        window._log_error(f"Spectrum refresh failed: {exc}")
    refresh_state = window._ui_refresh_state
    refresh_state.live_estimate_dirty = True
    refresh_state.telemetry_dirty = True
    window._append_processed_trace_history(processed, fit)
    if window._live_processing_worker is not None and window._live_processing_worker.is_alive() and window._live_active:
        _request_live_acquisition_poll_for(window, window._live_ui_refresh_delay_ms)


def handle_acquisition_error(window, source_epoch: int, message: str) -> None:
    if window._closing:
        window._busy = False
        return
    if source_epoch != window._source_epoch:
        window._busy = False
        return
    window._busy = False
    window._set_measurement_buttons_enabled(True)
    if window._pending_source_mode is not None:
        pending_mode = window._pending_source_mode
        restart_live = window._resume_live_after_source_switch
        window._pending_source_mode = None
        window._resume_live_after_source_switch = False
        window.source_tabs.blockSignals(True)
        window.source_tabs.setCurrentIndex(0 if pending_mode == "spectrometer" else 1)
        window.source_tabs.blockSignals(False)
        window._apply_source_mode(pending_mode, restart_live=restart_live)
        return
    if window._live_active:
        window._stop_live_acquisition(f"Live acquisition stopped: {message}")
        return
    window._pending_manual_kind = None
    window._show_error(message)


def start_live_acquisition(window) -> None:
    if window._busy or window._live_active:
        return
    if window._live_worker is not None and window._live_worker.is_alive():
        return

    window._live_active = True
    window._display_window_ms = 1000.0 / max(window.live_rate_spin.value(), 1e-9)
    window._live_ui_refresh_delay_ms = max(int(round(window._display_window_ms)), 16)
    window._raw_last_finish_ts = None
    window._raw_last_sample_index = None
    window._last_display_average_count = None
    window._last_display_period_ms = None
    window._last_plot_refresh_ms = None
    window._last_plot_refresh_gap_ms = None
    window._last_plot_refresh_finished_at = None
    window._actual_plot_refresh_rate_hz = None
    window._plot_refresh_timestamps = []
    window._plot_refresh_requested_at = None
    window._last_plot_refresh_delay_ms = None
    window._last_spectrum_curve_update_ms = None
    window._last_spectrum_fit_update_ms = None
    window._last_spectrum_marker_update_ms = None
    window._last_spectrum_residual_update_ms = None
    window._last_sensorgram_render_ms = None
    window._last_sensorgram_heatmap_render_ms = None
    window._last_sensorgram_heatmap_arrays_ms = None
    window._last_sensorgram_heatmap_image_ms = None
    window._last_sensorgram_heatmap_axes_ms = None
    window._last_metric_autoscale_ms = None
    window._last_metric_plot_disabled_fast_path = False
    window._last_metric_render_series_export_ms = None
    window._last_metric_render_setdata_calls = None
    window._last_metric_render_setdata_skips = None
    window._last_metric_view_cache_modes = {}
    window._plot_display_points = max(int(getattr(window, "_plot_display_points", 512)), 1)
    window._trace_plot_paint_count = 0
    window._trace_plot_paint_total_ms = None
    window._trace_plot_paint_max_ms = None
    window._spectrum_plot_paint_count = 0
    window._spectrum_plot_paint_total_ms = None
    window._spectrum_plot_paint_max_ms = None
    window._last_deferred_ui_refresh_ms = None
    window._last_deferred_ui_refresh_total_ms = None
    window._last_deferred_display_refresh_ms = None
    window._last_deferred_stats_refresh_ms = None
    window._display_refresh_requested_at = None
    window._stats_refresh_requested_at = None
    window._last_deferred_ui_live_estimate_ms = None
    window._last_deferred_ui_telemetry_ms = None
    window._last_deferred_ui_trace_plot_ms = None
    window._last_deferred_ui_summary_ms = None
    window._last_deferred_ui_stats_ms = None
    window._last_stats_refresh_delay_ms = None
    window._last_processing_ms = None
    window._last_processing_queue_wait_ms = None
    window._processing_rate_hz = None
    window._processing_headroom_ratio = None
    window._last_live_acquisition_flush_ms = None
    window._last_live_processed_flush_ms = None
    window._last_live_visual_refresh_ms = None
    window._live_visual_refresh_count = 0
    window._live_visual_refresh_in_progress = False
    window._live_mode_deferred_display_flush_count = 0
    window._live_mode_deferred_metric_flush_count = 0
    window._live_mode_deferred_stats_flush_count = 0
    window._last_live_result_poll_delay_ms = None
    window._last_live_processed_poll_delay_ms = None
    window._last_summary_refresh_ms = None
    window._last_session_summary_refresh_total_ms = None
    window._last_session_stats_refresh_ms = None
    window._last_session_stats_refresh_total_ms = None
    window._last_log_buffer_flush_ms = None
    window._last_log_buffer_delay_ms = None
    window._last_log_buffer_total_ms = None
    window._log_buffer_requested_at = None
    window._live_result_requested_at = None
    window._live_result_requested_delay_ms = None
    window._live_processed_requested_at = None
    window._live_processed_requested_delay_ms = None
    window._last_ui_heartbeat_delay_ms = None
    window._ui_heartbeat_max_delay_ms = None
    window._last_ui_heartbeat_total_ms = None
    window._live_plot_update_counter = 0
    window._live_display_dropped_frames = 0
    window._raw_acquired_count = 0
    window._raw_recording_enqueued_count = 0
    window._raw_recording_written_count = 0
    window._raw_recording_backpressure_count = 0
    window._raw_recording_failed_count = 0
    window._ui_preview_enqueued_count = 0
    window._ui_preview_replaced_count = 0
    window._ui_preview_displayed_count = 0
    window._processing_enqueued_count = 0
    window._processing_replaced_count = 0
    window._processing_completed_count = 0
    window._processing_displayed_count = 0
    window._live_display_started_at = perf_counter()
    window._live_trace_started_at = datetime.now(timezone.utc)
    window._metric_archive_started_at = window._live_trace_started_at
    window._sensorgram_axis_started_at = window._live_trace_started_at
    window._last_live_processing_perf = None
    if hasattr(window, "_plot_view_cache") and hasattr(window._plot_view_cache, "clear_live_absolute_metric_cache"):
        window._plot_view_cache.clear_live_absolute_metric_cache()
    window._reset_live_accumulator()
    ctx = mp.get_context("spawn")
    window._live_stop_event = ctx.Event()
    window._live_result_queue = ctx.Queue(maxsize=4)
    window._live_processing_input_queue = ctx.Queue(maxsize=16)
    window._live_processed_queue = ctx.Queue(maxsize=4)
    window._live_recording_queue = ctx.Queue(maxsize=2048)
    window._live_processing_log_queue = ctx.Queue(maxsize=32)
    window._live_result_queue_max_depth = 0
    window._live_processed_queue_max_depth = 0
    window._live_recording_queue_max_depth = 0
    simulation_parameters = None
    if window._source_mode == "simulation":
        simulation_parameters = window._simulation_backend.simulation_parameters()
        window._log_info("Continuous simulation acquisition started.")
    else:
        window._log_info("Continuous spectrometer acquisition started.")
        _suspend_spectrometer_backend_for_live(window)
    window._set_measurement_buttons_enabled(False)
    window._set_manual_acquisition_buttons_enabled(True)
    refresh_state = window._ui_refresh_state
    refresh_state.live_estimate_dirty = True
    request = AcquisitionRequest(
        kind="sample",
        settings=window._current_settings(),
        source_epoch=window._source_epoch,
        archive_writer=window._measurement_writer,
        archive_enabled=window._measurement_active,
        measurement_started_at=window._measurement_started_at,
    )
    window._live_worker = LiveAcquisitionWorker(
        request,
        window._live_result_queue,
        window._live_processing_input_queue,
        window._live_recording_queue,
        window._live_stop_event,
        source_mode=window._source_mode,
        simulation_parameters=simulation_parameters,
        debug_mode_enabled=window._processing_debug_mode_enabled,
        log_queue=window._live_processing_log_queue,
    )
    window._live_processing_worker = LiveProcessingWorker(
        window._live_processed_queue,
        window._live_processing_input_queue,
        window._live_stop_event,
        window._current_processing_settings(),
        debug_mode_enabled=window._processing_debug_mode_enabled,
        log_queue=window._live_processing_log_queue,
    )
    if window._source_mode == "simulation":
        window._live_worker.update_cycle_period(1.0 / max(window.sim_output_rate_spin.value(), 1e-9))
    window._live_worker.start()
    window._live_processing_worker.start()
    if hasattr(window, "_live_recording_timer"):
        window._live_recording_timer.start()
    flush_live_recording_results(window)
    _request_live_acquisition_poll_for(window, window._live_ui_refresh_delay_ms)
    live_result_depth = _queue_size_safe(window._live_result_queue)
    live_processed_depth = _queue_size_safe(window._live_processed_queue)
    if live_result_depth is not None:
        window._live_result_queue_max_depth = max(int(getattr(window, "_live_result_queue_max_depth", 0)), live_result_depth)
    if live_processed_depth is not None:
        window._live_processed_queue_max_depth = max(int(getattr(window, "_live_processed_queue_max_depth", 0)), live_processed_depth)
    window._update_window_mode_label()


def stop_live_acquisition(window, message: str = "Live acquisition stopped.") -> None:
    window._live_active = False
    window._live_stop_event.set()
    if hasattr(window, "_live_recording_timer"):
        window._live_recording_timer.stop()
    window._ui_task_scheduler.cancel("live_visual_refresh")
    window._ui_task_scheduler.cancel("live_acquisition")
    window._live_result_requested_at = None
    window._live_result_requested_delay_ms = None
    window._live_display_started_at = None
    window._live_trace_started_at = None
    window._trace_display_cursor_s = 0.0
    window._last_live_processing_perf = None
    window._last_plot_refresh_ms = None
    window._last_plot_refresh_gap_ms = None
    window._last_plot_refresh_finished_at = None
    window._actual_plot_refresh_rate_hz = None
    window._plot_refresh_timestamps = []
    window._plot_refresh_requested_at = None
    window._last_spectrum_curve_update_ms = None
    window._last_spectrum_fit_update_ms = None
    window._last_spectrum_marker_update_ms = None
    window._last_spectrum_residual_update_ms = None
    window._last_sensorgram_render_ms = None
    window._last_sensorgram_heatmap_render_ms = None
    window._last_sensorgram_heatmap_arrays_ms = None
    window._last_sensorgram_heatmap_image_ms = None
    window._last_sensorgram_heatmap_axes_ms = None
    window._last_metric_autoscale_ms = None
    window._last_metric_plot_disabled_fast_path = False
    window._last_metric_render_series_export_ms = None
    window._last_metric_render_setdata_calls = None
    window._last_metric_render_setdata_skips = None
    window._last_metric_view_cache_modes = {}
    window._plot_display_points = max(int(getattr(window, "_plot_display_points", 512)), 1)
    window._trace_plot_paint_count = 0
    window._trace_plot_paint_total_ms = None
    window._trace_plot_paint_max_ms = None
    window._spectrum_plot_paint_count = 0
    window._spectrum_plot_paint_total_ms = None
    window._spectrum_plot_paint_max_ms = None
    window._last_deferred_ui_refresh_ms = None
    window._last_deferred_ui_refresh_total_ms = None
    window._last_deferred_display_refresh_ms = None
    window._last_deferred_stats_refresh_ms = None
    window._display_refresh_requested_at = None
    window._stats_refresh_requested_at = None
    window._last_deferred_ui_live_estimate_ms = None
    window._last_deferred_ui_telemetry_ms = None
    window._last_deferred_ui_trace_plot_ms = None
    window._last_deferred_ui_summary_ms = None
    window._last_deferred_ui_stats_ms = None
    window._last_stats_refresh_delay_ms = None
    window._last_processing_ms = None
    window._last_processing_queue_wait_ms = None
    window._processing_rate_hz = None
    window._processing_headroom_ratio = None
    window._last_live_acquisition_flush_ms = None
    window._last_live_processed_flush_ms = None
    window._last_live_visual_refresh_ms = None
    window._live_visual_refresh_count = 0
    window._live_visual_refresh_in_progress = False
    window._live_mode_deferred_display_flush_count = 0
    window._live_mode_deferred_metric_flush_count = 0
    window._live_mode_deferred_stats_flush_count = 0
    window._last_live_result_poll_delay_ms = None
    window._last_live_processed_poll_delay_ms = None
    window._last_summary_refresh_ms = None
    window._last_session_summary_refresh_total_ms = None
    window._last_session_stats_refresh_ms = None
    window._last_session_stats_refresh_total_ms = None
    window._last_log_buffer_flush_ms = None
    window._last_log_buffer_delay_ms = None
    window._last_log_buffer_total_ms = None
    window._last_ui_heartbeat_delay_ms = None
    window._ui_heartbeat_max_delay_ms = None
    window._last_ui_heartbeat_total_ms = None
    window._live_plot_update_counter = 0
    window._reset_live_accumulator()
    window._live_result_queue_max_depth = None
    window._live_processed_queue_max_depth = None
    window._set_measurement_buttons_enabled(True)
    window._set_manual_acquisition_buttons_enabled(True)
    window.status_label.setText(message)
    _flush_live_processing_logs(window)
    window._request_deferred_ui_refresh(telemetry=True)
    window._log_info(message)
    window._update_window_mode_label()
    if window._live_worker is not None and not window._live_worker.is_alive():
        window._live_worker = None
    if window._live_worker is not None:
        try:
            window._live_worker.stop()
            window._live_worker.join(timeout=2.0)
            if window._live_worker.is_alive():
                window._log_warning("Live acquisition worker did not exit cleanly; terminating it.")
                window._live_worker.terminate()
                window._live_worker.join(timeout=1.0)
        except Exception as exc:
            window._log_warning(f"Live acquisition worker shutdown failed: {exc}")
        window._live_worker = None
    flush_live_recording_results(window)
    if window._live_processing_worker is not None:
        try:
            window._live_processing_worker.join(timeout=0.25)
        except Exception:
            pass
        if not window._live_processing_worker.is_alive():
            window._live_processing_worker = None
    _flush_live_processing_logs(window)
    flush_live_recording_results(window)
    window._live_recording_queue = None
    _restore_spectrometer_backend_after_live(window)


def start_acquisition(window, kind: str) -> None:
    if window._closing:
        return
    if window._busy:
        return
    if window._live_worker is not None and window._live_worker.is_alive():
        return

    if window._source_mode == "simulation":
        if kind != "sample":
            spectrum = window._build_simulation_spectrum(kind)
            window._log_info(f"Generated simulation {kind} spectrum.")
            result = AcquisitionResult(
                spectrum=spectrum,
                elapsed_ms=0.0,
                settings=window._current_settings(),
                source_epoch=window._source_epoch,
            )
            handle_acquisition_success(window, kind, result)
            window._refresh_plot()
            return
        window._live_active = True
        set_measurement_buttons_enabled(window, False)
        window._log_info("Continuous simulation started.")
        window._request_deferred_ui_refresh(live_estimate=True)
        return

    window._busy = True
    if not (window._live_active and kind == "sample"):
        set_measurement_buttons_enabled(window, False)
    window.status_label.setText(f"Acquiring {kind} spectrum...")
    window._log_info(f"Acquiring {kind} spectrum.")

    settings = window._current_settings()
    task = AcquisitionTask(
        window._spectrometer,
        AcquisitionRequest(
            kind=kind,
            settings=settings,
            source_epoch=window._source_epoch,
            archive_writer=window._measurement_writer,
            archive_enabled=window._measurement_active and kind == "sample",
            measurement_started_at=window._measurement_started_at,
        ),
    )
    task.signals.finished.connect(window._handle_acquisition_success)
    task.signals.failed.connect(window._handle_acquisition_error)
    window._thread_pool.start(task)


def set_measurement_buttons_enabled(window, enabled: bool) -> None:
    window.acquire_dark_button.setEnabled(enabled)
    window.acquire_reference_button.setEnabled(enabled)
    window.auto_integration_button.setEnabled(enabled and window._source_mode == "spectrometer")
    window.measurement_toggle_button.setEnabled(enabled)
    window.stop_measurement_button.setEnabled(window._measurement_active)
    window.next_measurement_button.setEnabled(enabled)
    update_measurement_toggle_button(window)


def set_manual_acquisition_buttons_enabled(window, enabled: bool) -> None:
    window.acquire_dark_button.setEnabled(enabled)
    window.acquire_reference_button.setEnabled(enabled)


def set_measurement_ui_locked(window, locked: bool) -> None:
    window.sim_resolution_spin.setEnabled(not locked)
    window.sim_output_rate_spin.setEnabled(not locked and window._source_mode == "simulation")
    if hasattr(window, "_set_recording_context_controls_enabled"):
        window._set_recording_context_controls_enabled(not locked)
    if locked:
        window.source_tabs.setToolTip("Switch tabs without changing the active feed.")
        window.sim_resolution_spin.setToolTip(
            "Locked while measurement is running so the recording wavelength axis stays fixed."
        )
        window.sim_output_rate_spin.setToolTip(
            "Locked while measurement is running so simulation pacing stays consistent."
        )
    else:
        window.source_tabs.setToolTip("Switch tabs without changing the active feed.")
        window.sim_resolution_spin.setToolTip("Resolution of the simulation wavelength axis.")
        window.sim_output_rate_spin.setToolTip("Simulation frame production rate.")
    update_source_link_buttons(window)


def update_measurement_toggle_button(window) -> None:
    if window._measurement_active:
        icon = transport_icon(window._theme_mode, "stop")
        tooltip = "Stop measurement"
    else:
        icon = transport_icon(window._theme_mode, "play")
        tooltip = "Start measurement"
    window.measurement_toggle_button.setIcon(icon)
    window.measurement_toggle_button.setToolTip(tooltip)
    trace_button = getattr(window, "trace_record_button", None)
    if trace_button is not None:
        if window._measurement_active:
            record_color = QColor("#d84d4d" if window._theme_mode == "dark" else "#a11d1d")
            record_color.setAlpha(255 if bool(getattr(window, "_recording_blink_visible", True)) else 90)
            trace_icon = tint_tabler_icon(flow_tabler_icon("player_record"), record_color)
        else:
            trace_icon = transport_icon(window._theme_mode, "record")
        trace_tooltip = "Stop sensorgram recording" if window._measurement_active else "Start sensorgram recording"
        trace_button.setIcon(trace_icon)
        trace_button.setToolTip(trace_tooltip)
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if experiment_control_window is not None and hasattr(experiment_control_window, "_set_record_with_flow_recording_active"):
        experiment_control_window._set_record_with_flow_recording_active(bool(window._measurement_active))


def toggle_measurement_run(window) -> None:
    if window._measurement_active:
        stop_measurement_run(window)
        return
    start_measurement_run(window)


def start_measurement_run(window) -> None:
    if window._measurement_active:
        return

    signal_mode = window.plot_selector.currentText().lower()
    if signal_mode not in {"sample", "absorbance"}:
        signal_mode = "sample"

    anchor = window._session.get_plot_data(signal_mode)
    if anchor is None:
        anchor = window._session.state.sample or window._session.state.absorbance
    if anchor is None:
        window.status_label.setText("Measurement needs at least one live spectrum before starting.")
        window._log_warning("Measurement start blocked: no live spectrum available yet.")
        return

    project_destination = ""
    if hasattr(window, "recording_project_destination"):
        project_destination = str(window.recording_project_destination() or "").strip()
    if not project_destination:
        experiment_control_window = getattr(window, "_experiment_control_window", None)
        if experiment_control_window is not None and hasattr(experiment_control_window, "recording_project_destination"):
            project_destination = str(experiment_control_window.recording_project_destination() or "").strip()
    experiment_name = ""
    if hasattr(window, "recording_experiment_name"):
        experiment_name = str(window.recording_experiment_name() or "").strip()
    if not experiment_name:
        experiment_control_window = getattr(window, "_experiment_control_window", None)
        if experiment_control_window is not None and hasattr(experiment_control_window, "recording_experiment_name"):
            experiment_name = str(experiment_control_window.recording_experiment_name() or "").strip()
    if not experiment_name:
        experiment_name = str(getattr(window, "_measurement_experiment_name", "") or "").strip()
    if not experiment_name:
        experiment_name, accepted = QInputDialog.getText(
            window,
            "Experiment name",
            "Experiment name:",
            text=datetime.now().strftime("experiment_%Y%m%d"),
        )
        if not accepted:
            window.status_label.setText("Measurement start cancelled.")
            window._log_info("Measurement start cancelled.")
            return
    experiment_name = str(experiment_name).strip() or "experiment"
    file_experiment_name = _sanitize_experiment_name(experiment_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"{timestamp}_{file_experiment_name}.h5"
    if project_destination:
        destination = Path(project_destination) / _safe_folder_name(experiment_name) / default_name
    else:
        default_path = Path.cwd() / "data" / _safe_folder_name(experiment_name) / default_name
        file_path, _ = QFileDialog.getSaveFileName(
            window,
            "Start measurement",
            str(default_path),
            "HDF5 files (*.h5)",
        )
        if not file_path:
            window.status_label.setText("Measurement start cancelled.")
            window._log_info("Measurement start cancelled.")
            return
        destination = Path(file_path)
        if hasattr(window, "project_destination_edit") and not str(window.recording_project_destination() or "").strip():
            window.project_destination_edit.setText(str(destination.parent))
            if hasattr(window, "_remember_recording_project_destination"):
                window._remember_recording_project_destination()
        if hasattr(window, "experiment_name_edit") and not str(window.recording_experiment_name() or "").strip():
            window.experiment_name_edit.setText(experiment_name)
            if hasattr(window, "_remember_recording_experiment_name"):
                window._remember_recording_experiment_name()
    original_destination = destination
    destination = _reserve_unique_measurement_destination(destination)
    if destination != original_destination:
        window._log_warning(
            f"Measurement file already existed; saved as {destination.name} instead of {original_destination.name}."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    # Recording follows the execution state, but it is not stopped by flow HOLD.
    window._measurement_axis_lock = np.asarray(anchor.wavelengths_nm, dtype=np.float64).copy()
    window._measurement_writer = AsyncHDF5MeasurementWriter(
        destination,
        signal_mode,
        window._measurement_axis_lock,
        window._current_processing_settings(),
        experiment_name=experiment_name,
        started_at_utc=started_at,
        flush_interval_s=window._measurement_flush_interval_s,
    )
    window._measurement_writer.update_acquisition_state(window._acquisition_state_payload())
    window._measurement_writer.update_baselines(window._session.state.dark, window._session.state.reference)
    window._measurement_active = True
    window._measurement_paused = False
    window._measurement_signal_mode = signal_mode
    window._measurement_path = destination
    window._measurement_started_at = started_at
    window._metric_archive_started_at = started_at
    window._sensorgram_axis_started_at = started_at
    window._measurement_experiment_name = experiment_name
    window._session_stats_log.clear()
    window._session_stats_log_last_text = ""
    window._session_stats_log_last_capture_ts = 0.0
    if hasattr(window, "_sensorgram_control_step_events"):
        window._sensorgram_control_step_events.clear()
    if hasattr(window, "_sync_sensorgram_control_step_overlay"):
        window._sync_sensorgram_control_step_overlay()
    if window._live_worker is not None and window._live_worker.is_alive():
        window._live_worker.update_archive_context(window._measurement_writer, True, window._measurement_started_at)
    set_measurement_ui_locked(window, True)
    if hasattr(window, "_set_recording_blink_indicator"):
        window._set_recording_blink_indicator(True)
    if hasattr(window, "_apply_sensorgram_content_mode"):
        window._apply_sensorgram_content_mode(save=False)
    if hasattr(window, "_plot_view_cache") and hasattr(window._plot_view_cache, "clear_live_absolute_metric_cache"):
        window._plot_view_cache.clear_live_absolute_metric_cache()
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
    if hasattr(window, "_sensorgram_heatmap_levels"):
        window._sensorgram_heatmap_levels = None
    window._trace_display_cursor_s = 0.0
    window._live_trace_started_at = None
    window._metric_reference_processed = None
    window._refresh_session_statistics(force=True)
    window._refresh_trace_plot("Metric position (nm)")
    window._request_trace_autoscale()
    set_measurement_buttons_enabled(window, True)
    window.status_label.setText(f"Recording to {destination.name}")
    window._log_success(f"Measurement recording started: {destination.name}.")
    window._update_window_mode_label()

    if not window._live_active and not window._busy:
        window._start_live_acquisition()


def pause_measurement_run(window) -> None:
    if not window._measurement_active:
        return
    if window._live_worker is not None and window._live_worker.is_alive():
        window._live_worker.update_archive_context(window._measurement_writer, True, window._measurement_started_at)
    update_measurement_toggle_button(window)
    window._update_window_mode_label()


def stop_measurement_run(window) -> None:
    if not window._measurement_active:
        return
    # STOP is the only state that finalizes the recording file.
    started_at = window._measurement_started_at
    measurement_path = window._measurement_path
    if window._live_worker is not None and window._live_worker.is_alive():
        window._live_worker.update_archive_context(None, False, None)
    flush_live_recording_results(window)
    flush_measurement_frames(window, force=True)
    if window._measurement_writer is not None:
        window._measurement_writer.close()
    window._measurement_writer = None
    window._measurement_active = False
    window._measurement_paused = False
    window._measurement_started_at = None
    window._measurement_axis_lock = None
    set_measurement_ui_locked(window, False)
    if hasattr(window, "_set_recording_blink_indicator"):
        window._set_recording_blink_indicator(False)
    window._sync_simulation_backend_from_controls()
    window._trace_view_locked = False
    window._live_trace_started_at = None
    window._metric_reference_processed = None
    window._refresh_session_statistics(force=True)
    window._refresh_trace_plot("Metric position (nm)")
    window._request_trace_autoscale()
    set_measurement_buttons_enabled(window, True)
    if measurement_path is not None and bool(getattr(window, "measurement_hdf5_compression_enabled", lambda: True)()):
        window.status_label.setText("Measurement stopped. Compressing file...")
        window._log_success("Measurement recording stopped. Compressing file...")
        if hasattr(window, "_start_measurement_file_compression"):
            window._start_measurement_file_compression(measurement_path)
    else:
        window._measurement_path = None
        window.status_label.setText("Measurement stopped.")
        window._log_success("Measurement recording stopped.")
        window._refresh_plot()
        window._refresh_session_summary(force=True)
        window._update_window_mode_label()


def append_processed_trace_history(window, processed: Spectrum, fit: Spectrum | None) -> None:
    if not window._live_active:
        return

    measurement_active = bool(window._measurement_active)
    if measurement_active:
        started_at = window._measurement_started_at
        if started_at is not None:
            elapsed_s = max((processed.acquired_at - started_at).total_seconds(), 0.0)
        else:
            elapsed_s = 0.0
    else:
        started_at = window._live_trace_started_at
        if started_at is not None:
            elapsed_s = max((processed.acquired_at - started_at).total_seconds(), 0.0)
        else:
            elapsed_s = float(getattr(window, "_trace_display_cursor_s", 0.0) or 0.0)

    display_step_s = max(1.0 / max(window.live_rate_spin.value(), 1e-9), 1e-3)
    metrics = window._get_analysis_metrics(processed, fit)
    updated = False
    for metric_name in window.TRACE_METRIC_LABELS:
        value = metrics.get(metric_name)
        if not isinstance(value, (int, float)) or not np.isfinite(float(value)):
            continue
        plot_view_cache = getattr(window, "_plot_view_cache", None)
        if plot_view_cache is not None and hasattr(plot_view_cache, "append_live_absolute_metric_point"):
            try:
                plot_view_cache.append_live_absolute_metric_point(
                    metric_name,
                    float(elapsed_s),
                    float(value),
                    target_points=max(int(getattr(window, "_plot_display_points", 512)), 1),
                    recent_tail_points=max(int(getattr(window, "_sensorgram_compression_recent_tail_points", 300)), 0),
                )
            except Exception:
                pass
        updated = True
        last_metric_points = max(int(getattr(window, "_trace_display_cursor_s", 0.0) / max(display_step_s, 1e-9)), 0)

    window._trace_display_cursor_s = elapsed_s + display_step_s

    if updated:
        # Store the derived metric so the live archive and the recording file share the same format.
        acquired_at_unix_ms = int(round(processed.acquired_at.astimezone(timezone.utc).timestamp() * 1000.0))
        metric_row = {
            "acquired_at_unix_ms": acquired_at_unix_ms,
            "t_ms": int(round(elapsed_s * 1000.0)),
            "sample_index": -1,
            "centroid_nm": metrics.get("centroid", np.nan),
            "smoothed_max_nm": metrics.get("smoothed_max", np.nan),
            "poly_max_nm": metrics.get("poly_max", np.nan),
            "gaussian_center_nm": metrics.get("gaussian_center", np.nan),
            "fwhm_nm": fit.metadata.get("fwhm_nm", np.nan) if fit is not None else processed.metadata.get("fwhm_nm", np.nan),
            "mse": fit.metadata.get("mse", np.nan) if fit is not None else np.nan,
            "snr": metrics.get("snr", np.nan),
        }
        from lspr_app.storage.measurement_archive import ensure_temp_measurement_writer

        writer = window._measurement_writer
        if writer is None:
            writer = getattr(window, "_metric_archive_writer", None)
        if writer is None:
            writer = ensure_temp_measurement_writer(window)
        writer.append_metrics([metric_row])
        window._request_deferred_ui_refresh(trace_plot=True, trace_label="Metric position (nm)")
        window._request_trace_autoscale()
        notify_startup_data_rendered = getattr(window, "_notify_startup_data_rendered", None)
        if callable(notify_startup_data_rendered):
            try:
                notify_startup_data_rendered("trace")
            except Exception:
                pass
        window._log_throttled(
            "trace_append",
            (
                "Metric point appended | points="
                f"{last_metric_points if 'last_metric_points' in locals() else 0} | "
                f"rate={window.live_rate_spin.value():.2f} Hz"
            ),
            level=logging.DEBUG,
            min_interval=1.0,
        )


def flush_measurement_frames(window, force: bool = False) -> None:
    if window._measurement_writer is None:
        return
    window._measurement_writer.flush()
    if window._measurement_path is not None:
        window.status_label.setText(f"Recording to {window._measurement_path.name}")


def _experiment_runtime_flags(window) -> tuple[bool, bool, bool]:
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if experiment_control_window is None:
        return False, False, False
    return (
        bool(getattr(experiment_control_window, "_plan_running", False)),
        bool(getattr(experiment_control_window, "_plan_holding", False)),
        bool(getattr(experiment_control_window, "_plan_paused", False)),
    )


def _experiment_runtime_state(window) -> str:
    running, holding, paused = _experiment_runtime_flags(window)
    return experiment_runtime_state_name(running=running, holding=holding, paused=paused)


def _experiment_runtime_label(window) -> str:
    running, holding, paused = _experiment_runtime_flags(window)
    return experiment_runtime_label(
        running=running,
        holding=holding,
        paused=paused,
        recording=bool(getattr(window, "_measurement_active", False)),
    )


def update_window_mode_label(window) -> None:
    if not hasattr(window, "_window_mode_label"):
        return
    profile = getattr(window, "_launch_profile_spec", None)
    if profile is None:
        profile = launch_profile_spec(DEFAULT_LAUNCH_PROFILE)
    source_name = "Spectrometer" if window._source_mode == "spectrometer" else "Simulation"
    if getattr(profile, "window_mode_label_text", None):
        text = str(profile.window_mode_label_text)
        tooltip = (
            "Control Plan Editor mode: edit experiment plans without hardware discovery, live acquisition, "
            "or recording controls."
        )
        window._window_mode_label.setText(text)
        window._window_mode_label.setToolTip(tooltip)
        window._window_mode_label.setStyleSheet(
            f"color: {str(getattr(profile, 'window_mode_label_color', '') or '#9FD7A6')}; font-weight: 700;"
        )
        if hasattr(window, "_window_mode_icon_label"):
            window._window_mode_icon_label.clear()
            window._window_mode_icon_label.setVisible(False)
        return
    running, holding, paused = _experiment_runtime_flags(window)
    snapshot = experiment_runtime_snapshot(
        running=running,
        holding=holding,
        paused=paused,
        recording=bool(getattr(window, "_measurement_active", False)),
        has_steps=bool(getattr(getattr(window, "_experiment_control_window", None), "_read_experiment_control_steps", lambda: [])()),
    )
    text = snapshot.label()
    tooltip = snapshot.tooltip(source_name=source_name)
    window._window_mode_label.setText(text)
    window._window_mode_label.setToolTip(tooltip)
    window._window_mode_label.setStyleSheet("")
    if hasattr(window, "_window_mode_icon_label"):
        if bool(getattr(profile, "show_source_icon", True)):
            source_icon = prism_tab_icon() if window._source_mode == "spectrometer" else math_function_tab_icon()
            window._window_mode_icon_label.setPixmap(source_icon.pixmap(16, 16))
            window._window_mode_icon_label.setToolTip(f"{source_name} source")
            window._window_mode_icon_label.setVisible(True)
        else:
            window._window_mode_icon_label.clear()
            window._window_mode_icon_label.setToolTip("")
            window._window_mode_icon_label.setVisible(False)


def _sanitize_experiment_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value).strip())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned or "experiment"


def _safe_folder_name(value: str) -> str:
    cleaned = "".join("_" if ch in '<>:"/\\|?*' else ch for ch in str(value).strip())
    cleaned = cleaned.rstrip(" .")
    return cleaned or "experiment"


def _reserve_unique_measurement_destination(destination: Path) -> Path:
    destination = Path(destination).expanduser()
    if destination.suffix.lower() != ".h5":
        destination = destination.with_suffix(".h5")
    if not destination.exists():
        return destination
    stem = destination.stem
    suffix = destination.suffix
    for index in range(1, 1000):
        candidate = destination.with_name(f"{stem}_{index:02d}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Unable to reserve a unique measurement file name under {destination.parent}")

from lspr_app.storage.measurement_archive import close_temp_measurement_writer, ensure_temp_measurement_writer


def _is_recording_active(window: Any) -> bool:
    for attr in (
        "_measurement_active",
        "_measurement_running",
        "_recording_active",
        "_recording_running",
        "_session_recording_active",
    ):
        if bool(getattr(window, attr, False)):
            return True
    return False


_original_start_live_acquisition = start_live_acquisition


def start_live_acquisition(*args: Any, **kwargs: Any):
    window = args[0] if args else kwargs.get("window")
    if window is not None:
        ensure_temp_measurement_writer(window)
    return _original_start_live_acquisition(*args, **kwargs)


_original_stop_live_acquisition = stop_live_acquisition


def stop_live_acquisition(*args: Any, **kwargs: Any):
    window = args[0] if args else kwargs.get("window")
    try:
        return _original_stop_live_acquisition(*args, **kwargs)
    finally:
        if window is not None and not _is_recording_active(window):
            close_temp_measurement_writer(window)
