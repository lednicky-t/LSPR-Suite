from __future__ import annotations

import logging
from collections.abc import Callable
from time import perf_counter

from lspr_app.gui.main_window_logging import refresh_session_statistics_for, refresh_session_summary_for
from lspr_app.gui.main_window_plotting import (
    flush_deferred_display_refreshes_for,
    flush_deferred_metric_refreshes_for,
    flush_deferred_stats_refreshes_for,
    flush_plot_refreshes_for,
)
from lspr_app.gui.runtime_probe import (
    build_runtime_drift_lines_for,
    capture_runtime_drift_sample_for,
    initialize_runtime_drift_probe_for,
)


def run_gui_callback_timed(window, label: str, callback: Callable[[], None], *, warn_ms: float = 20.0) -> None:
    started = perf_counter()
    try:
        callback()
    finally:
        elapsed_ms = (perf_counter() - started) * 1000.0
        attr_name = {
            "ui_state_save": "_last_ui_state_total_ms",
            "acquisition_state_save": "_last_acquisition_state_total_ms",
            "session_stats_snapshot": "_last_session_stats_recording_total_ms",
            "session_summary_refresh": "_last_session_summary_refresh_total_ms",
            "session_stats_refresh": "_last_session_stats_refresh_total_ms",
            "deferred_ui_flush": "_last_deferred_ui_refresh_total_ms",
            "deferred_display_flush": "_last_deferred_display_refresh_ms",
            "deferred_metric_flush": "_last_deferred_metric_refresh_ms",
            "deferred_stats_flush": "_last_deferred_stats_refresh_ms",
            "plot_refresh": "_last_plot_refresh_total_ms",
            "log_buffer": "_last_log_buffer_total_ms",
            "ui_heartbeat": "_last_ui_heartbeat_total_ms",
            "gui_housekeeping": "_last_gui_housekeeping_total_ms",
            "gui_housekeeping_log_buffer": "_last_gui_housekeeping_log_buffer_ms",
            "gui_housekeeping_ui_state": "_last_gui_housekeeping_ui_state_ms",
            "gui_housekeeping_acquisition_state": "_last_gui_housekeeping_acquisition_state_ms",
            "gui_housekeeping_session_stats_snapshot": "_last_gui_housekeeping_session_stats_snapshot_ms",
            "gui_housekeeping_session_stats_refresh": "_last_gui_housekeeping_session_stats_refresh_ms",
        }.get(label, f"_last_{label}_total_ms")
        if hasattr(window, attr_name):
            setattr(window, attr_name, elapsed_ms)
        warning_threshold_ms = {
            "ui_heartbeat": 75.0,
            "gui_housekeeping": 150.0,
            "gui_housekeeping_log_buffer": 150.0,
            "gui_housekeeping_ui_state": 100.0,
            "gui_housekeeping_acquisition_state": 100.0,
            "gui_housekeeping_session_stats_snapshot": 100.0,
            "gui_housekeeping_session_stats_refresh": 100.0,
            "deferred_ui_flush": 100.0,
            "plot_refresh": 100.0,
            "session_stats_refresh": 100.0,
            "session_summary_refresh": 100.0,
            "log_buffer": 150.0,
            "ui_state_save": 100.0,
            "acquisition_state_save": 100.0,
            "session_stats_snapshot": 100.0,
            "deferred_display_flush": 100.0,
            "deferred_metric_flush": 100.0,
            "deferred_stats_flush": 100.0,
            "live_visual_refresh": 100.0,
        }.get(label, warn_ms)
        if elapsed_ms >= warning_threshold_ms and not getattr(window, "_quiet_diagnostics_mode", False):
            severe_threshold_ms = max(500.0, warning_threshold_ms * 5.0)
            level = logging.WARNING if elapsed_ms >= severe_threshold_ms else logging.INFO
            window._log_throttled(
                f"gui_callback_{label}",
                f"GUI callback {label} took {elapsed_ms:.2f} ms",
                level=level,
                min_interval=10.0,
            )


def drain_gui_housekeeping_tasks(window) -> None:
    if getattr(window, "_closing", False):
        return
    if not bool(getattr(window, "_gui_housekeeping_enabled", True)):
        return

    now = perf_counter()
    refresh_state = getattr(window, "_ui_refresh_state", None)

    def _due(requested_at: float | None, interval_ms: float) -> bool:
        if requested_at is None:
            return False
        try:
            return (now - float(requested_at)) * 1000.0 >= float(interval_ms)
        except (TypeError, ValueError):
            return False

    log_buffer = getattr(window, "_log_buffer", None)
    log_buffer_timer = getattr(window, "_log_buffer_timer", None)
    if log_buffer and _due(getattr(window, "_log_buffer_requested_at", None), float(log_buffer_timer.interval()) if log_buffer_timer is not None else 75.0):
        window._run_gui_callback_timed("log_buffer", lambda: window._flush_log_buffer())
        window._last_gui_housekeeping_log_buffer_ms = getattr(window, "_last_log_buffer_total_ms", None)
        return

    ui_state_timer = getattr(window, "_ui_state_timer", None)
    if _due(getattr(window, "_ui_state_requested_at", None), float(ui_state_timer.interval()) if ui_state_timer is not None else 250.0):
        window._run_gui_callback_timed("ui_state_save", lambda: window._save_ui_state())
        window._last_gui_housekeeping_ui_state_ms = getattr(window, "_last_ui_state_total_ms", None)
        return

    acquisition_state_timer = getattr(window, "_acquisition_state_timer", None)
    if _due(
        getattr(window, "_acquisition_state_requested_at", None),
        float(acquisition_state_timer.interval()) if acquisition_state_timer is not None else 250.0,
    ):
        window._run_gui_callback_timed("acquisition_state_save", lambda: window._persist_acquisition_state())
        window._last_gui_housekeeping_acquisition_state_ms = getattr(window, "_last_acquisition_state_total_ms", None)
        return

    if bool(getattr(window, "_session_stats_recording_active", False)):
        session_timer = getattr(window, "_session_stats_recording_timer", None)
        interval_ms = float(session_timer.interval()) if session_timer is not None else 1000.0
        if _due(getattr(window, "_session_stats_recording_requested_at", None), interval_ms):
            window._run_gui_callback_timed("session_stats_snapshot", lambda: window._capture_session_stats_recording_snapshot())
            window._last_gui_housekeeping_session_stats_snapshot_ms = getattr(window, "_last_session_stats_recording_total_ms", None)
            return

    if refresh_state is not None and bool(getattr(refresh_state, "session_stats_dirty", False)):
        if window._session_stats_refresh_requested_at is None:
            window._session_stats_refresh_requested_at = now
        elif _due(window._session_stats_refresh_requested_at, window._session_stats_refresh_delay_ms):
            window._run_gui_callback_timed("session_stats_refresh", lambda: window._refresh_session_statistics(force=True))
            window._last_gui_housekeeping_session_stats_refresh_ms = getattr(window, "_last_session_stats_refresh_total_ms", None)
            refresh_state.session_stats_dirty = False
            window._session_stats_refresh_requested_at = None
            return


def request_live_acquisition_poll(window, delay_ms: float | None = None) -> None:
    delay = float(delay_ms if delay_ms is not None else window._live_ui_refresh_delay_ms)
    if not window._ui_task_scheduler.is_pending("live_visual_refresh"):
        now = perf_counter()
        window._live_result_requested_at = now
        window._live_result_requested_delay_ms = delay
        window._live_processed_requested_at = now
        window._live_processed_requested_delay_ms = delay
    window._ui_task_scheduler.request(
        "live_visual_refresh",
        delay,
        lambda: flush_live_visual_refreshes(window),
        priority=-20,
        coalesce="latest",
    )


def request_live_visual_refresh(window, delay_ms: float | None = None) -> None:
    delay = float(delay_ms if delay_ms is not None else window._live_ui_refresh_delay_ms)
    window._ui_task_scheduler.request(
        "live_visual_refresh",
        delay,
        lambda: flush_live_visual_refreshes(window),
        priority=-20,
        coalesce="latest",
    )


def flush_live_visual_refreshes(window) -> None:
    started = perf_counter()
    window._live_visual_refresh_in_progress = True
    try:
        window._flush_live_acquisition_results()
        if not window._closing and window._live_processing_worker is not None:
            window._flush_live_processed_results()
        flush_live_visual_state(window)
    finally:
        window._live_visual_refresh_in_progress = False
    window._last_live_visual_refresh_ms = (perf_counter() - started) * 1000.0
    window._live_visual_refresh_count = int(getattr(window, "_live_visual_refresh_count", 0)) + 1


def request_deferred_ui_refresh(
    window,
    *,
    stats: bool = False,
    trace_plot: bool = False,
    summary: bool = False,
    telemetry: bool = False,
    live_estimate: bool = False,
    session_stats: bool = False,
    trace_label: str | None = None,
) -> None:
    if stats:
        window._ui_refresh_state.stats_dirty = True
    if trace_plot:
        window._ui_refresh_state.metric_plot_dirty = True
    if summary:
        window._ui_refresh_state.summary_dirty = True
    if telemetry:
        window._ui_refresh_state.telemetry_dirty = True
    if live_estimate:
        window._ui_refresh_state.live_estimate_dirty = True
    if session_stats or stats or trace_plot or summary or telemetry or live_estimate or trace_label is not None:
        window._ui_refresh_state.session_stats_dirty = True
        if window._session_stats_refresh_requested_at is None:
            window._session_stats_refresh_requested_at = perf_counter()
    if trace_label is not None:
        window._ui_refresh_state.pending_metric_label = trace_label
    display_dirty = summary or telemetry or live_estimate
    metric_dirty = trace_plot or trace_label is not None
    stats_dirty = bool(stats)
    live_visual_dirty = bool(window._live_active and (metric_dirty or telemetry or live_estimate) and not stats_dirty and not summary)
    if live_visual_dirty:
        if not getattr(window, "_live_visual_refresh_in_progress", False):
            request_live_visual_refresh(window, 0.0)
        return
    if session_stats or stats or summary or telemetry or live_estimate or trace_label is not None:
        window._ui_refresh_state.session_stats_dirty = True
        if window._session_stats_refresh_requested_at is None:
            window._session_stats_refresh_requested_at = perf_counter()
    if display_dirty:
        display_delay_ms = window._live_ui_refresh_delay_ms if window._live_active else window._stats_refresh_delay_ms
        if window._live_active and (live_estimate or telemetry):
            display_delay_ms = max(display_delay_ms, 220)
        if not window._ui_task_scheduler.is_pending("deferred_display_flush"):
            window._display_refresh_requested_at = perf_counter()
        window._ui_task_scheduler.request(
            "deferred_display_flush",
            display_delay_ms,
            window._flush_deferred_display_refreshes,
            priority=0,
            coalesce="latest",
        )
    if metric_dirty:
        metric_delay_ms = window._live_ui_refresh_delay_ms if window._live_active else window._stats_refresh_delay_ms
        if window._live_active:
            metric_delay_ms = max(metric_delay_ms, 220)
        if not window._ui_task_scheduler.is_pending("deferred_metric_flush"):
            window._metric_refresh_requested_at = perf_counter()
        window._ui_task_scheduler.request(
            "deferred_metric_flush",
            metric_delay_ms,
            window._flush_deferred_metric_refreshes,
            priority=0,
            coalesce="latest",
        )
    if stats_dirty:
        stats_delay_ms = window._stats_refresh_delay_ms
        if not window._ui_task_scheduler.is_pending("deferred_stats_flush"):
            window._stats_refresh_requested_at = perf_counter()
        window._ui_task_scheduler.request(
            "deferred_stats_flush",
            stats_delay_ms,
            window._flush_deferred_stats_refreshes,
            priority=0,
            coalesce="latest",
        )


def request_plot_refresh(window, delay_ms: float | None = None) -> None:
    delay = float(delay_ms if delay_ms is not None else 33.0)
    if not window._ui_task_scheduler.is_pending("plot_refresh"):
        window._plot_refresh_requested_at = perf_counter()
    window._ui_task_scheduler.request(
        "plot_refresh",
        delay,
        window._flush_plot_refreshes,
        priority=1,
        coalesce="latest",
    )


def refresh_session_summary(window, force: bool = False) -> None:
    window._run_gui_callback_timed("session_summary_refresh", lambda: refresh_session_summary_for(window, force=force))


def refresh_session_statistics(window, force: bool = False) -> None:
    window._run_gui_callback_timed("session_stats_refresh", lambda: refresh_session_statistics_for(window, force=force))
    if force:
        refresh_state = getattr(window, "_ui_refresh_state", None)
        if refresh_state is not None:
            refresh_state.session_stats_dirty = False
        window._session_stats_refresh_requested_at = None


def flush_live_visual_state(window) -> None:
    refresh_state = getattr(window, "_ui_refresh_state", None)
    if refresh_state is None:
        return
    metric_dirty = bool(getattr(refresh_state, "metric_plot_dirty", False)) or getattr(refresh_state, "pending_metric_label", None) is not None
    if metric_dirty and not bool(getattr(window, "_sensorgram_frozen", False)):
        label = getattr(refresh_state, "pending_metric_label", None) or "Metric position (nm)"
        try:
            window._refresh_trace_plot(label)
            started = perf_counter()
            window._update_trace_stats()
            window._last_live_trace_stats_ms = (perf_counter() - started) * 1000.0
            window._live_trace_stats_count = int(getattr(window, "_live_trace_stats_count", 0)) + 1
            refresh_state.metric_plot_dirty = False
            refresh_state.pending_metric_label = None
        except Exception as exc:
            window._log_error(f"Live trace refresh failed: {exc}")
    if bool(getattr(refresh_state, "live_estimate_dirty", False)):
        window._update_live_estimate()
        refresh_state.live_estimate_dirty = False
    if bool(getattr(refresh_state, "telemetry_dirty", False)):
        window._refresh_telemetry()
        refresh_state.telemetry_dirty = False


def flush_deferred_ui_refreshes(window) -> None:
    window._run_gui_callback_timed("deferred_ui_flush", lambda: flush_deferred_display_refreshes_for(window))
    window._run_gui_callback_timed("deferred_stats_flush", lambda: flush_deferred_stats_refreshes_for(window))


def flush_deferred_display_refreshes(window) -> None:
    window._run_gui_callback_timed("deferred_display_flush", lambda: flush_deferred_display_refreshes_for(window))


def flush_deferred_metric_refreshes(window) -> None:
    window._run_gui_callback_timed("deferred_metric_flush", lambda: flush_deferred_metric_refreshes_for(window))


def flush_deferred_stats_refreshes(window) -> None:
    window._run_gui_callback_timed("deferred_stats_flush", lambda: flush_deferred_stats_refreshes_for(window))


def flush_plot_refreshes(window) -> None:
    window._run_gui_callback_timed("plot_refresh", lambda: flush_plot_refreshes_for(window))


def initialize_runtime_drift_probe(window) -> None:
    initialize_runtime_drift_probe_for(window)


def capture_runtime_drift_sample(window) -> None:
    capture_runtime_drift_sample_for(window)


def build_runtime_drift_lines(window) -> list[str]:
    return build_runtime_drift_lines_for(window)
