from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication


_DEFAULT_PROBE_INTERVAL_MS = 60_000
_DEFAULT_PROBE_MAX_SAMPLES = 12


@dataclass(frozen=True, slots=True)
class RuntimeDriftSample:
    captured_at_s: float
    scheduler_lag_ms: float | None
    scheduler_duration_ms: float | None
    scheduler_pending: int
    gui_housekeeping_total_ms: float | None
    log_buffer_total_ms: float | None
    deferred_display_total_ms: float | None
    deferred_stats_total_ms: float | None
    plot_refresh_total_ms: float | None
    live_result_queue_size: int
    live_processed_queue_size: int
    log_history_count: int
    log_buffer_count: int
    session_stats_log_count: int
    metric_history_points: int
    metric_history_buffer_points: int
    temporal_history_count: int
    sensorgram_rows: int
    widget_count: int
    working_set_mb: float | None


def _timing_value_ms(value: object) -> float | None:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if numeric != numeric or numeric in {float("inf"), float("-inf")}:
        return None
    return numeric


def _safe_queue_size(queue_obj: Any) -> int:
    if queue_obj is None:
        return 0
    try:
        size = int(queue_obj.qsize())
    except (AttributeError, NotImplementedError, OSError, ValueError, TypeError):
        return 0
    return max(size, 0)


def _safe_count(value: Any) -> int:
    if value is None:
        return 0
    try:
        return max(int(len(value)), 0)
    except (TypeError, ValueError):
        return 0


def _sum_buffer_lengths(mapping: Any) -> int:
    if not mapping:
        return 0
    try:
        return max(sum(max(int(len(buffer)), 0) for buffer in mapping.values()), 0)
    except Exception:
        return 0


def _widget_count() -> int:
    app = QApplication.instance()
    if app is None or not hasattr(app, "allWidgets"):
        return 0
    try:
        return max(len(app.allWidgets()), 0)
    except Exception:
        return 0


def _current_process_working_set_mb() -> float | None:
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        return None

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if not ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return float(counters.WorkingSetSize) / (1024.0 * 1024.0)
    except Exception:
        return None


def _sample_runtime_drift(window: Any) -> RuntimeDriftSample:
    scheduler = getattr(window, "_ui_task_scheduler", None)
    plot_view_cache = getattr(window, "_plot_view_cache", None)
    metric_display_points = 0
    if plot_view_cache is not None and hasattr(plot_view_cache, "metric_cache_debug_snapshot"):
        try:
            cache_snapshot = plot_view_cache.metric_cache_debug_snapshot()
            metric_display_points = max(
                (
                    int(entry.get("display_points", 0))
                    for entry in cache_snapshot.values()
                ),
                default=0,
            )
        except Exception:
            metric_display_points = 0
    startup_t0 = float(getattr(window, "_startup_t0", perf_counter()))
    return RuntimeDriftSample(
        captured_at_s=float(perf_counter() - startup_t0),
        scheduler_lag_ms=_timing_value_ms(getattr(scheduler, "_last_dispatch_lag_ms", None)),
        scheduler_duration_ms=_timing_value_ms(getattr(scheduler, "_last_dispatch_duration_ms", None)),
        scheduler_pending=int(getattr(scheduler, "pending_count", lambda: 0)() if scheduler is not None else 0),
        gui_housekeeping_total_ms=_timing_value_ms(getattr(window, "_last_gui_housekeeping_total_ms", None)),
        log_buffer_total_ms=_timing_value_ms(getattr(window, "_last_log_buffer_total_ms", None)),
        deferred_display_total_ms=_timing_value_ms(getattr(window, "_last_deferred_display_refresh_ms", None)),
        deferred_stats_total_ms=_timing_value_ms(getattr(window, "_last_deferred_stats_refresh_ms", None)),
        plot_refresh_total_ms=_timing_value_ms(getattr(window, "_last_plot_refresh_total_ms", None)),
        live_result_queue_size=_safe_queue_size(getattr(window, "_live_result_queue", None)),
        live_processed_queue_size=_safe_queue_size(getattr(window, "_live_processed_queue", None)),
        log_history_count=_safe_count(getattr(window, "_log_history", None)),
        log_buffer_count=_safe_count(getattr(window, "_log_buffer", None)),
        session_stats_log_count=_safe_count(getattr(window, "_session_stats_log", None)),
        metric_history_points=0,
        metric_history_buffer_points=metric_display_points,
        temporal_history_count=_safe_count(getattr(window, "_temporal_processed_history", None)),
        sensorgram_rows=_safe_count(getattr(window, "_sensorgram_heatmap_history", None)),
        widget_count=_widget_count(),
        working_set_mb=_current_process_working_set_mb(),
    )


def capture_runtime_drift_sample_for(window) -> None:
    if getattr(window, "_closing", False):
        return
    if not bool(getattr(window, "_runtime_drift_probe_enabled", True)):
        return
    sample = _sample_runtime_drift(window)
    samples = getattr(window, "_runtime_drift_samples", None)
    if not isinstance(samples, list):
        samples = []
        window._runtime_drift_samples = samples
    samples.append(sample)
    max_samples = int(getattr(window, "_runtime_drift_max_samples", _DEFAULT_PROBE_MAX_SAMPLES))
    if max_samples > 0 and len(samples) > max_samples:
        del samples[: len(samples) - max_samples]


def initialize_runtime_drift_probe_for(window) -> None:
    if hasattr(window, "_runtime_drift_probe_timer"):
        return
    window._runtime_drift_samples = []
    window._runtime_drift_max_samples = _DEFAULT_PROBE_MAX_SAMPLES
    window._runtime_drift_probe_interval_ms = _DEFAULT_PROBE_INTERVAL_MS
    window._runtime_drift_probe_timer = QTimer(window)
    window._runtime_drift_probe_timer.setInterval(_DEFAULT_PROBE_INTERVAL_MS)
    window._runtime_drift_probe_timer.timeout.connect(lambda: capture_runtime_drift_sample_for(window))
    window._runtime_drift_probe_timer.start()
    capture_runtime_drift_sample_for(window)


def _format_timing(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.1f} ms"


def _format_count(value: int | None) -> str:
    if value is None:
        return "-"
    return str(max(int(value), 0))


def _format_memory(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.1f} MB"


def _trend_text(first: RuntimeDriftSample, latest: RuntimeDriftSample, attribute: str, formatter) -> str:
    start = getattr(first, attribute)
    end = getattr(latest, attribute)
    try:
        start_value = formatter(start)
        end_value = formatter(end)
    except Exception:
        return "- -> -"
    return f"{start_value} -> {end_value}"


def _numeric_delta(start: int | float | None, end: int | float | None) -> str:
    if start is None or end is None:
        return ""
    try:
        delta = float(end) - float(start)
    except (TypeError, ValueError):
        return ""
    sign = "+" if delta >= 0 else ""
    return f" ({sign}{delta:.1f})"


def _per_minute_delta(start: int | float | None, end: int | float | None, span_s: float, *, precision: int = 2) -> str:
    if start is None or end is None or span_s <= 0:
        return "-"
    try:
        delta_per_min = (float(end) - float(start)) / (span_s / 60.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return "-"
    sign = "+" if delta_per_min >= 0 else ""
    return f"{sign}{delta_per_min:.{precision}f}/min"


def _per_minute_delta_value(start: int | float | None, end: int | float | None, span_s: float) -> float | None:
    if start is None or end is None or span_s <= 0:
        return None
    try:
        return (float(end) - float(start)) / (span_s / 60.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _growth_line(label: str, start: int | float | None, end: int | float | None, span_s: float, *, unit: str, precision: int = 1) -> tuple[float, str] | None:
    delta_per_min = _per_minute_delta_value(start, end, span_s)
    if delta_per_min is None:
        return None
    sign = "+" if delta_per_min >= 0 else ""
    return (
        abs(delta_per_min),
        f"  {label}: {sign}{delta_per_min:.{precision}f} {unit}/min ({start} -> {end})",
    )


def _top_growth_contributors(first: RuntimeDriftSample, latest: RuntimeDriftSample, span_s: float) -> list[str]:
    candidates: list[tuple[float, str]] = []
    for label, start, end, unit, precision in (
        ("Working set", first.working_set_mb, latest.working_set_mb, "MB", 2),
        ("Scheduler lag", first.scheduler_lag_ms, latest.scheduler_lag_ms, "ms", 1),
        ("Log history", first.log_history_count, latest.log_history_count, "count", 1),
        ("Metric history", first.metric_history_points, latest.metric_history_points, "count", 1),
        ("Metric buffers", first.metric_history_buffer_points, latest.metric_history_buffer_points, "count", 1),
        ("Sensorgram rows", first.sensorgram_rows, latest.sensorgram_rows, "count", 1),
        ("Widget count", first.widget_count, latest.widget_count, "count", 1),
        ("Scheduler pending", first.scheduler_pending, latest.scheduler_pending, "count", 1),
        ("Log buffer", first.log_buffer_count, latest.log_buffer_count, "count", 1),
        ("Live result queue", first.live_result_queue_size, latest.live_result_queue_size, "count", 1),
        ("Live processed queue", first.live_processed_queue_size, latest.live_processed_queue_size, "count", 1),
        ("GUI housekeeping", first.gui_housekeeping_total_ms, latest.gui_housekeeping_total_ms, "ms", 1),
        ("Deferred display", first.deferred_display_total_ms, latest.deferred_display_total_ms, "ms", 1),
        ("Deferred stats", first.deferred_stats_total_ms, latest.deferred_stats_total_ms, "ms", 1),
    ):
        item = _growth_line(label, start, end, span_s, unit=unit, precision=precision)
        if item is not None:
            candidates.append(item)
    candidates.sort(key=lambda item: item[0], reverse=True)
    top = [line for _, line in candidates[:3]]
    if not top:
        return ["  No measurable growth."]
    return top


def build_runtime_drift_lines_for(window) -> list[str]:
    if not bool(getattr(window, "_runtime_drift_probe_enabled", True)):
        return [
            "  Diagnostics profile has runtime drift probe disabled.",
        ]
    samples = list(getattr(window, "_runtime_drift_samples", []) or [])
    interval_ms = int(getattr(window, "_runtime_drift_probe_interval_ms", _DEFAULT_PROBE_INTERVAL_MS))
    if not samples:
        return [
            f"  Samples: 0 | interval: {interval_ms / 1000.0:.0f}s",
            "  No runtime drift samples collected yet.",
        ]

    first = samples[0]
    latest = samples[-1]
    span_s = max(float(latest.captured_at_s) - float(first.captured_at_s), 0.0)
    max_scheduler_lag = max((sample.scheduler_lag_ms or 0.0) for sample in samples)
    max_scheduler_duration = max((sample.scheduler_duration_ms or 0.0) for sample in samples)
    max_gui_housekeeping = max((sample.gui_housekeeping_total_ms or 0.0) for sample in samples)
    max_log_buffer = max((sample.log_buffer_total_ms or 0.0) for sample in samples)
    max_deferred_display = max((sample.deferred_display_total_ms or 0.0) for sample in samples)
    max_deferred_stats = max((sample.deferred_stats_total_ms or 0.0) for sample in samples)
    max_plot_refresh = max((sample.plot_refresh_total_ms or 0.0) for sample in samples)
    max_metric_history_points = max(sample.metric_history_points for sample in samples)
    max_metric_history_buffer_points = max(sample.metric_history_buffer_points for sample in samples)
    max_sensorgram_rows = max(sample.sensorgram_rows for sample in samples)
    max_widget_count = max(sample.widget_count for sample in samples)
    max_working_set_mb = max((sample.working_set_mb or 0.0) for sample in samples)
    top_growth = _top_growth_contributors(first, latest, span_s)
    lines = [
        f"  Samples: {len(samples)} | interval: {interval_ms / 1000.0:.0f}s | span: {span_s / 60.0:.1f} min",
        f"  Scheduler lag: {_format_timing(first.scheduler_lag_ms)} -> {_format_timing(latest.scheduler_lag_ms)} | max {_format_timing(max_scheduler_lag)}",
        f"  Scheduler duration: {_format_timing(first.scheduler_duration_ms)} -> {_format_timing(latest.scheduler_duration_ms)} | max {_format_timing(max_scheduler_duration)}",
        f"  Scheduler pending: {_format_count(first.scheduler_pending)} -> {_format_count(latest.scheduler_pending)}",
        f"  GUI housekeeping total: {_format_timing(first.gui_housekeeping_total_ms)} -> {_format_timing(latest.gui_housekeeping_total_ms)} | max {_format_timing(max_gui_housekeeping)}",
        f"  Log buffer total: {_format_timing(first.log_buffer_total_ms)} -> {_format_timing(latest.log_buffer_total_ms)} | max {_format_timing(max_log_buffer)}",
        f"  Deferred display total: {_format_timing(first.deferred_display_total_ms)} -> {_format_timing(latest.deferred_display_total_ms)} | max {_format_timing(max_deferred_display)}",
        f"  Deferred stats total: {_format_timing(first.deferred_stats_total_ms)} -> {_format_timing(latest.deferred_stats_total_ms)} | max {_format_timing(max_deferred_stats)}",
        f"  Plot refresh total: {_format_timing(first.plot_refresh_total_ms)} -> {_format_timing(latest.plot_refresh_total_ms)} | max {_format_timing(max_plot_refresh)}",
        f"  Log history entries: {_format_count(first.log_history_count)} -> {_format_count(latest.log_history_count)}{_numeric_delta(first.log_history_count, latest.log_history_count)}",
        f"  Log buffer entries: {_format_count(first.log_buffer_count)} -> {_format_count(latest.log_buffer_count)}{_numeric_delta(first.log_buffer_count, latest.log_buffer_count)}",
        f"  Session stats log entries: {_format_count(first.session_stats_log_count)} -> {_format_count(latest.session_stats_log_count)}{_numeric_delta(first.session_stats_log_count, latest.session_stats_log_count)}",
        f"  Metric history points: {_format_count(first.metric_history_points)} -> {_format_count(latest.metric_history_points)} | max {_format_count(max_metric_history_points)}",
        f"  Metric buffer points: {_format_count(first.metric_history_buffer_points)} -> {_format_count(latest.metric_history_buffer_points)} | max {_format_count(max_metric_history_buffer_points)}",
        f"  Temporal history points: {_format_count(first.temporal_history_count)} -> {_format_count(latest.temporal_history_count)}",
        f"  Sensorgram rows: {_format_count(first.sensorgram_rows)} -> {_format_count(latest.sensorgram_rows)} | max {_format_count(max_sensorgram_rows)}",
        f"  Live result queue: {_format_count(first.live_result_queue_size)} -> {_format_count(latest.live_result_queue_size)}",
        f"  Live processed queue: {_format_count(first.live_processed_queue_size)} -> {_format_count(latest.live_processed_queue_size)}",
        f"  Widget count: {_format_count(first.widget_count)} -> {_format_count(latest.widget_count)} | max {_format_count(max_widget_count)}",
        f"  Working set: {_format_memory(first.working_set_mb)} -> {_format_memory(latest.working_set_mb)} | max {_format_memory(max_working_set_mb)}",
        f"  Per-minute scheduler lag: {_per_minute_delta(first.scheduler_lag_ms, latest.scheduler_lag_ms, span_s, precision=1)}",
        f"  Per-minute log history: {_per_minute_delta(first.log_history_count, latest.log_history_count, span_s, precision=1)}",
        f"  Per-minute metric history: {_per_minute_delta(first.metric_history_points, latest.metric_history_points, span_s, precision=1)}",
        f"  Per-minute metric buffers: {_per_minute_delta(first.metric_history_buffer_points, latest.metric_history_buffer_points, span_s, precision=1)}",
        f"  Per-minute sensorgram rows: {_per_minute_delta(first.sensorgram_rows, latest.sensorgram_rows, span_s, precision=1)}",
        f"  Per-minute working set: {_per_minute_delta(first.working_set_mb, latest.working_set_mb, span_s, precision=2)} MB/min",
        "  Top growth contributors:",
        *top_growth,
    ]
    return lines
