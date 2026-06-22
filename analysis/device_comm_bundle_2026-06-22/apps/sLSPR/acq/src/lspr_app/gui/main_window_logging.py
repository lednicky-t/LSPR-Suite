from __future__ import annotations

import json
import re
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from html import escape, unescape
from time import perf_counter

import numpy as np
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QApplication

from lspr_app.diagnostics import DiagnosticsConfig
from lspr_app.gui.runtime_diagnostics import SessionDiagnosticsSnapshot, build_session_statistics_lines
from lspr_app.gui.logging_utils import SUCCESS_LOG_LEVEL
from lspr_app.storage.output_paths import (
    build_recording_experiment_base_dir,
    recording_experiment_base_dir_for,
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")

_LOG_VIEW_ALL = "all"
_LOG_VIEW_GUI = "gui"
_LOG_VIEW_DEVICES = "devices"

_DEVICE_KEYWORDS = (
    "pump",
    "valve",
    "m-switch",
    "mswitch",
    "spectrometer",
    "seabreeze",
    "ocean",
    "reglo",
    "itsybitsy",
    "serial controller",
    "controller",
    "port ",
    "connected",
    "disconnected",
    "discovered",
)

_GUI_KEYWORDS = (
    "startup",
    "ui",
    "gui",
    "experiment control",
    "experiment_control",
    "plot",
    "processing",
    "session",
    "acquisition",
    "window",
)


def _is_performance_diagnostic(source: object, text: object) -> bool:
    source_s = str(source or "").lower()
    text_s = str(text or "").lower()
    if "diagnostic" in source_s or "performance" in source_s:
        return True
    if "gui callback" in text_s:
        return True
    if "scheduler dispatch" in text_s:
        return True
    if "paint" in text_s and "ms" in text_s:
        return True
    if "took" in text_s and "ms" in text_s:
        return True
    if "frame skipped" in text_s:
        return True
    return False


def _plain_text(value: object) -> str:
    text = unescape(_HTML_TAG_RE.sub("", str(value)))
    return " ".join(text.split()).strip()


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


def _timing_plain_text(value_ms: float | None) -> str:
    if value_ms is None:
        return "-"
    try:
        value = float(value_ms)
    except (TypeError, ValueError):
        return "-"
    if not np.isfinite(value):
        return "-"
    return f"{value:.1f} ms"


def _timing_value_ms(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric):
        return None
    return numeric


def _frame_gap_reference_ms(window) -> float | None:
    spacing_ms = _timing_value_ms(getattr(window, "_last_spacing_ms", None))
    if spacing_ms is not None and spacing_ms > 0:
        return spacing_ms
    elapsed_ms = _timing_value_ms(getattr(window, "_last_elapsed_ms", None))
    if elapsed_ms is not None and elapsed_ms > 0:
        return elapsed_ms
    return None


def _queue_depth_text(window, attr_name: str) -> str:
    queue_obj = getattr(window, attr_name, None)
    if queue_obj is None:
        return "-"
    try:
        size = queue_obj.qsize()
    except (AttributeError, NotImplementedError, OSError):
        return "-"
    try:
        return str(max(int(size), 0))
    except (TypeError, ValueError):
        return "-"


def _queue_depth_max_text(value: object) -> str:
    if value is None:
        return "-"
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return "-"
    return str(max(numeric, 0))


def build_recording_experiment_log_path_for(
    window,
    *,
    prefix: str = "app_log",
    suffix: str = ".log",
    timestamp: datetime | None = None,
) -> Path:
    base_dir = recording_experiment_base_dir_for(window)
    started_at = timestamp or getattr(window, "_startup_started_at", None) or datetime.now(timezone.utc)
    started_local = started_at.astimezone()
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"{prefix}_{started_local.strftime('%Y%m%d_%H%M%S')}{suffix}"


def build_recording_experiment_log_path(
    project_destination: str,
    experiment_name: str,
    *,
    prefix: str = "app_log",
    suffix: str = ".log",
    timestamp: datetime | None = None,
) -> Path:
    base_dir = build_recording_experiment_base_dir(project_destination, experiment_name)
    started_at = timestamp or datetime.now(timezone.utc)
    started_local = started_at.astimezone()
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"{prefix}_{started_local.strftime('%Y%m%d_%H%M%S')}{suffix}"


def build_diagnostic_export_path_for(window) -> Path:
    base_dir = recording_experiment_base_dir_for(window)
    started_at = getattr(window, "_startup_started_at", None) or datetime.now(timezone.utc)
    started_local = started_at.astimezone()
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"perf_diagnostics_{started_local.strftime('%Y%m%d_%H%M%S')}.jsonl"


def _diagnostic_export_path_for(window) -> Path | None:
    if not bool(getattr(window, "_export_diagnostic_events", True)):
        return None
    path = getattr(window, "_diagnostic_export_path", None)
    if path is None:
        try:
            path = build_diagnostic_export_path_for(window)
        except Exception:
            return None
        window._diagnostic_export_path = path
    return path


def _append_diagnostic_export_event(window, levelno: int, source: str, text: str, *, suppressed: bool) -> None:
    if not bool(getattr(window, "_export_diagnostic_events", True)):
        return
    buffer = getattr(window, "_diagnostic_export_buffer", None)
    if buffer is None:
        return
    buffer.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "levelno": int(levelno),
            "source": str(source),
            "text": str(text),
            "suppressed": bool(suppressed),
        }
    )
    events = getattr(window, "_diagnostic_export_events", None)
    if events is not None:
        events.append((perf_counter(), int(levelno), str(source), str(text)))
    buffer_timer = getattr(window, "_log_buffer_timer", None)
    if buffer_timer is not None and not buffer_timer.isActive():
        window._log_buffer_requested_at = perf_counter()
        buffer_timer.start()


def _flush_diagnostic_export_buffer(window) -> None:
    buffer = getattr(window, "_diagnostic_export_buffer", None)
    if not buffer:
        return
    path = _diagnostic_export_path_for(window)
    if path is None:
        return
    started = perf_counter()
    batch = list(buffer)
    buffer.clear()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for record in batch:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
        events = getattr(window, "_diagnostic_export_events", None)
        if events is not None:
            events.append((perf_counter(), "diagnostic_export", str(len(batch))))
    except Exception:
        buffer[:0] = batch
        return
    window._last_diagnostic_export_flush_ms = (perf_counter() - started) * 1000.0


def _append_diagnostic_snapshot_export(window) -> None:
    if not bool(getattr(window, "_export_diagnostic_events", True)):
        return
    now = perf_counter()
    last_at = float(getattr(window, "_diagnostic_snapshot_export_last_ts", 0.0) or 0.0)
    interval_s = float(getattr(window, "_diagnostic_snapshot_export_interval_s", 2.5) or 2.5)
    if (now - last_at) < max(interval_s, 0.5):
        return
    path = _diagnostic_export_path_for(window)
    if path is None:
        return
    started = perf_counter()
    snapshot = SessionDiagnosticsSnapshot.from_window(window)
    record = {
        "record_type": "diagnostic_snapshot",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "snapshot": asdict(snapshot),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    except Exception:
        return
    window._diagnostic_snapshot_export_last_ts = now
    window._last_diagnostic_snapshot_export_ms = (perf_counter() - started) * 1000.0
    export_events = getattr(window, "_diagnostic_snapshot_export_events", None)
    if export_events is not None:
        export_events.append((perf_counter(), "diagnostic_snapshot", str(len(asdict(snapshot)))))


def build_pipeline_timing_breakdown_for(window) -> dict[str, float | None]:
    reference_ms = _frame_gap_reference_ms(window)
    acquisition_ms = _timing_value_ms(getattr(window, "_last_elapsed_ms", None))
    live_result_delay_ms = _timing_value_ms(getattr(window, "_last_live_result_poll_delay_ms", None))
    live_acquisition_flush_ms = _timing_value_ms(getattr(window, "_last_live_acquisition_flush_ms", None))
    live_visual_flush_ms = _timing_value_ms(getattr(window, "_last_live_processed_flush_ms", None))
    display_refresh_delay_ms = _timing_value_ms(getattr(window, "_last_display_refresh_delay_ms", None))
    stats_refresh_delay_ms = _timing_value_ms(getattr(window, "_last_stats_refresh_delay_ms", None))
    summary_refresh_ms = _timing_value_ms(getattr(window, "_last_summary_refresh_ms", None))
    session_stats_refresh_ms = _timing_value_ms(getattr(window, "_last_session_stats_refresh_ms", None))
    log_buffer_delay_ms = _timing_value_ms(getattr(window, "_last_log_buffer_delay_ms", None))
    log_buffer_flush_ms = _timing_value_ms(getattr(window, "_last_log_buffer_flush_ms", None))
    processing_wait_ms = _timing_value_ms(getattr(window, "_last_processing_queue_wait_ms", None))
    processing_ms = _timing_value_ms(getattr(window, "_last_processing_ms", None))
    plot_refresh_delay_ms = _timing_value_ms(getattr(window, "_last_plot_refresh_delay_ms", None))
    plot_render_ms = _timing_value_ms(getattr(window, "_last_plot_refresh_ms", None))
    sensorgram_render_ms = _timing_value_ms(getattr(window, "_last_sensorgram_render_ms", None))
    sensorgram_heatmap_render_ms = _timing_value_ms(getattr(window, "_last_sensorgram_heatmap_render_ms", None))
    deferred_display_ms = _timing_value_ms(getattr(window, "_last_deferred_display_refresh_ms", None))
    deferred_stats_ms = _timing_value_ms(getattr(window, "_last_deferred_stats_refresh_ms", None))
    known_total_ms = sum(
        value
        for value in (
            acquisition_ms,
            live_result_delay_ms,
            live_acquisition_flush_ms,
            live_visual_flush_ms,
            display_refresh_delay_ms,
            stats_refresh_delay_ms,
            summary_refresh_ms,
            session_stats_refresh_ms,
            log_buffer_delay_ms,
            log_buffer_flush_ms,
            processing_wait_ms,
            processing_ms,
            plot_refresh_delay_ms,
            plot_render_ms,
            sensorgram_render_ms,
            sensorgram_heatmap_render_ms,
            deferred_display_ms,
            deferred_stats_ms,
        )
        if value is not None and value > 0
    )
    idle_ms = None
    if reference_ms is not None:
        idle_ms = max(reference_ms - known_total_ms, 0.0)
    return {
        "reference_ms": reference_ms,
        "acquisition_ms": acquisition_ms,
        "live_result_delay_ms": live_result_delay_ms,
        "live_acquisition_flush_ms": live_acquisition_flush_ms,
        "live_visual_flush_ms": live_visual_flush_ms,
        "display_refresh_delay_ms": display_refresh_delay_ms,
        "stats_refresh_delay_ms": stats_refresh_delay_ms,
        "summary_refresh_ms": summary_refresh_ms,
        "session_stats_refresh_ms": session_stats_refresh_ms,
        "log_buffer_delay_ms": log_buffer_delay_ms,
        "log_buffer_flush_ms": log_buffer_flush_ms,
        "processing_wait_ms": processing_wait_ms,
        "processing_ms": processing_ms,
        "plot_refresh_delay_ms": plot_refresh_delay_ms,
        "plot_render_ms": plot_render_ms,
        "sensorgram_render_ms": sensorgram_render_ms,
        "sensorgram_heatmap_render_ms": sensorgram_heatmap_render_ms,
        "deferred_display_ms": deferred_display_ms,
        "deferred_stats_ms": deferred_stats_ms,
        "idle_ms": idle_ms,
    }


def _pipeline_timing_line(label: str, value_ms: float | None) -> str:
    return f"  {label}: {_timing_plain_text(value_ms)}"


def set_log_following(window, enabled: bool) -> None:
    window._log_follow_enabled = bool(enabled)


def clear_log_terminal(window) -> None:
    if hasattr(window, "log_terminal"):
        window.log_terminal.clear()
    history = getattr(window, "_log_history", None)
    if history is not None:
        history.clear()
    buffer = getattr(window, "_log_buffer", None)
    if buffer is not None:
        buffer.clear()
    for attr_name in (
        "_log_received_events",
        "_log_buffer_enqueue_events",
        "_log_perf_suppressed_events",
        "_log_throttle_suppressed_events",
        "_log_append_events",
    ):
        events = getattr(window, attr_name, None)
        if events is not None:
            events.clear()
    window._log_buffer_requested_at = None
    buffer_timer = getattr(window, "_log_buffer_timer", None)
    if buffer_timer is not None:
        buffer_timer.stop()


def copy_log_terminal(window) -> None:
    if not hasattr(window, "log_terminal"):
        return
    QApplication.clipboard().setText(window.log_terminal.toPlainText())


def _normalize_log_source(source: str) -> str:
    return str(source or "").strip().casefold()


def _normalize_log_text(text: str) -> str:
    return " ".join(str(text or "").strip().casefold().split())


def _log_record_matches_view(levelno: int, source: str, text: str, view_mode: str) -> bool:
    _ = levelno
    mode = _normalize_log_source(view_mode)
    if mode in {"", _LOG_VIEW_ALL}:
        return True
    source_text = _normalize_log_source(source)
    text_text = _normalize_log_text(text)
    if mode == _LOG_VIEW_DEVICES:
        return any(keyword in source_text or keyword in text_text for keyword in _DEVICE_KEYWORDS)
    if mode == _LOG_VIEW_GUI:
        if any(keyword in source_text or keyword in text_text for keyword in _DEVICE_KEYWORDS):
            return False
        return source_text in {"main", "gui", "plot", "processing", "session", "acquisition", "hardware_init", "startup", "experiment_control"} or any(
            keyword in source_text or keyword in text_text for keyword in _GUI_KEYWORDS
        )
    return True


def _append_log_history(window, levelno: int, source: str, text: str) -> None:
    history = getattr(window, "_log_history", None)
    if history is None:
        return
    history.append((int(levelno), str(source), str(text)))
    max_entries = int(getattr(window, "_log_history_max_entries", 2000))
    if len(history) > max_entries:
        del history[: len(history) - max_entries]


def _refresh_log_terminal_view(window) -> None:
    if not hasattr(window, "log_terminal"):
        return
    cursor = window.log_terminal.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.beginEditBlock()
    try:
        window.log_terminal.clear()
        history = getattr(window, "_log_history", None) or []
        view_mode = getattr(window, "_log_view_mode", _LOG_VIEW_ALL)
        for levelno, source, text in history:
            if _log_record_matches_view(levelno, source, text, view_mode):
                insert_log_record(window, cursor, levelno, source, text)
    finally:
        cursor.endEditBlock()
        window.log_terminal.setTextCursor(cursor)


def set_log_view_mode(window, mode: str, *, refresh: bool = True) -> None:
    window._log_view_mode = _normalize_log_source(mode) or _LOG_VIEW_ALL
    if refresh:
        _refresh_log_terminal_view(window)


def copy_session_stats_log_for(window) -> None:
    lines = getattr(window, "_session_stats_log", None)
    if not lines:
        current = build_session_panel_plain_text_for(window)
        QApplication.clipboard().setText(current)
        return
    QApplication.clipboard().setText("\n\n".join(str(line) for line in lines))


def copy_session_stats_snapshot_for(window) -> None:
    QApplication.clipboard().setText(build_session_panel_plain_text_for(window))


def save_session_stats_log_for(window) -> Path | None:
    lines = getattr(window, "_session_stats_log", None)
    text = "\n\n".join(str(line) for line in lines) if lines else build_session_panel_plain_text_for(window)
    if not text.strip():
        return None

    base_dir = recording_experiment_base_dir_for(window)
    started_at = getattr(window, "_session_stats_recording_started_at", None)
    if started_at is None:
        started_at = getattr(window, "_measurement_started_at", None)
    if started_at is None:
        started_at = datetime.now(timezone.utc)
    started_local = started_at.astimezone()
    duration_s = getattr(window, "_session_stats_recording_duration_s", None)
    if duration_s is None:
        current_started_at = getattr(window, "_session_stats_recording_started_at", None)
        if current_started_at is not None:
            duration_s = max((datetime.now(timezone.utc) - current_started_at).total_seconds(), 0.0)
        else:
            duration_s = 0.0
    timestamp = started_local.strftime("%Y%m%d_%H%M%S")
    duration_suffix = f"{int(round(float(duration_s)))}s"
    base_dir.mkdir(parents=True, exist_ok=True)
    destination = base_dir / f"session_stats_{timestamp}_{duration_suffix}.txt"
    if destination.exists():
        stem = destination.stem
        suffix = destination.suffix
        for index in range(1, 100):
            candidate = destination.with_name(f"{stem}_{index:02d}{suffix}")
            if not candidate.exists():
                destination = candidate
                break
    destination.write_text(text, encoding="utf-8")
    return destination


def flush_log_buffer(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_log_buffer_requested_at", None)
    if requested_at is not None:
        try:
            window._last_log_buffer_delay_ms = max((started - float(requested_at)) * 1000.0, 0.0)
        except (TypeError, ValueError):
            window._last_log_buffer_delay_ms = None
    flush_budget_ms = 12.0
    max_records_per_flush = 20
    if not getattr(window, "_log_buffering_enabled", True):
        buffer = getattr(window, "_log_buffer", None)
        if buffer:
            batch = list(buffer)
            buffer.clear()
            for levelno, source, text in collapse_log_batch(batch):
                append_log_record_now(window, levelno, source, text)
        if getattr(window, "_diagnostic_export_buffer", None):
            _flush_diagnostic_export_buffer(window)
        buffer_timer = getattr(window, "_log_buffer_timer", None)
        if buffer_timer is not None:
            buffer_timer.stop()
        window._last_log_buffer_flush_ms = (perf_counter() - started) * 1000.0
        window._log_buffer_requested_at = None
        return
    buffer = getattr(window, "_log_buffer", None)
    batch = list(buffer[:max_records_per_flush]) if buffer else []
    if batch:
        del buffer[: len(batch)]
        cursor = window.log_terminal.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.beginEditBlock()
        try:
            for levelno, source, text in collapse_log_batch(batch):
                _append_log_history(window, levelno, source, text)
                insert_log_record(window, cursor, levelno, source, text)
        finally:
            cursor.endEditBlock()
            window.log_terminal.setTextCursor(cursor)
    if getattr(window, "_diagnostic_export_buffer", None):
        _flush_diagnostic_export_buffer(window)
    buffer_timer = getattr(window, "_log_buffer_timer", None)
    if buffer_timer is not None and not buffer and not getattr(window, "_diagnostic_export_buffer", None):
        buffer_timer.stop()
    if buffer:
        window._log_buffer_requested_at = perf_counter()
    else:
        window._log_buffer_requested_at = None
    window._last_log_buffer_flush_ms = (perf_counter() - started) * 1000.0
    if (perf_counter() - started) * 1000.0 > flush_budget_ms and buffer:
        # Leave remaining records for the next housekeeping pass so the GUI stays responsive.
        return


def collapse_log_batch(batch: list[tuple[int, str, str]]) -> list[tuple[int, str, str]]:
    if not batch:
        return []
    collapsed: list[tuple[int, str, str]] = []
    previous: tuple[int, str, str] | None = None
    repeat_count = 0
    for item in batch:
        if previous is not None and item == previous:
            repeat_count += 1
            continue
        if previous is not None:
            levelno, source, text = previous
            if repeat_count:
                text = f"{text} (x{repeat_count + 1})"
            collapsed.append((levelno, source, text))
        previous = item
        repeat_count = 0
    if previous is not None:
        levelno, source, text = previous
        if repeat_count:
            text = f"{text} (x{repeat_count + 1})"
        collapsed.append((levelno, source, text))
    return collapsed


def append_log_record(window, levelno: int, source: str, text: str) -> None:
    line = str(text).strip()
    if not line or not hasattr(window, "log_terminal"):
        return
    if int(levelno) not in window._log_emit_levels:
        return
    received_events = getattr(window, "_log_received_events", None)
    if received_events is not None:
        received_events.append((perf_counter(), int(levelno), str(source), line))
    diagnostics = DiagnosticsConfig.from_window(window)
    if _is_performance_diagnostic(source, line) and int(levelno) < logging.ERROR and not diagnostics.deep_timing_enabled:
        suppressed_events = getattr(window, "_log_perf_suppressed_events", None)
        if suppressed_events is not None:
            suppressed_events.append((perf_counter(), int(levelno), str(source), line))
        return
    if int(levelno) >= logging.WARNING:
        append_log_record_now(window, levelno, source, line)
        return
    if not getattr(window, "_log_buffering_enabled", True):
        append_log_record_now(window, levelno, source, line)
        return
    buffer = getattr(window, "_log_buffer", None)
    buffer_timer = getattr(window, "_log_buffer_timer", None)
    if buffer is not None and buffer_timer is not None:
        buffer.append((int(levelno), str(source), line))
        buffer_events = getattr(window, "_log_buffer_enqueue_events", None)
        if buffer_events is not None:
            buffer_events.append((perf_counter(), int(levelno), str(source), line))
        if window._log_buffer_requested_at is None:
            window._log_buffer_requested_at = perf_counter()
        return
    append_log_record_now(window, levelno, source, line)


def insert_log_record(window, cursor: QTextCursor, levelno: int, source: str, text: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    color_map = {
        logging.DEBUG: "#5fa8ff",
        logging.INFO: "#c7d2e0",
        SUCCESS_LOG_LEVEL: "#44d07b",
        logging.WARNING: "#f4b23d",
        logging.ERROR: "#ff6b6b",
        logging.CRITICAL: "#f35f8d",
    }
    level_label_map = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        SUCCESS_LOG_LEVEL: "SUCCESS",
        logging.WARNING: "WARN",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRIT",
    }
    level_color = color_map.get(int(levelno), "#c7d2e0")
    level_label = level_label_map.get(int(levelno), "INFO")
    source_label = str(source).split(".")[-1] or "app"
    escaped = str(text).replace("\n", "<br>")
    html = (
        f"<div style='white-space:pre-wrap; margin:0;'>"
        f"<span style='color:#738193;'>{timestamp}</span> "
        f"<span style='color:{level_color}; font-weight:600;'>[{level_label}]</span> "
        f"<span style='color:#94a3b8;'>{source_label}</span> "
        f"<span style='color:#e5edf7;'>{escaped}</span>"
        f"</div>"
    )
    cursor.insertHtml(html)
    cursor.insertHtml("<br>")
    if window._log_follow_enabled:
        scrollbar = window.log_terminal.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


def append_log_record_now(window, levelno: int, source: str, text: str) -> None:
    diagnostics = DiagnosticsConfig.from_window(window)
    if _is_performance_diagnostic(source, text) and int(levelno) < logging.ERROR and not diagnostics.deep_timing_enabled:
        suppressed_events = getattr(window, "_log_perf_suppressed_events", None)
        if suppressed_events is not None:
            suppressed_events.append((perf_counter(), int(levelno), str(source), str(text)))
        _append_diagnostic_export_event(window, levelno, source, text, suppressed=True)
        if not hasattr(window, "log_terminal"):
            return
    if not hasattr(window, "log_terminal"):
        return
    started = perf_counter()
    _append_log_history(window, levelno, source, text)
    cursor = window.log_terminal.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.beginEditBlock()
    try:
        insert_log_record(window, cursor, levelno, source, text)
    finally:
        cursor.endEditBlock()
        window.log_terminal.setTextCursor(cursor)
        elapsed_ms = (perf_counter() - started) * 1000.0
        window._last_log_append_now_ms = elapsed_ms
        events = getattr(window, "_log_append_events", None)
        if events is not None:
            events.append((perf_counter(), int(levelno), float(elapsed_ms)))


def log_event(window, levelno: int, message: str, source: str = "main") -> None:
    logger = getattr(window, "_ui_logger", None)
    if logger is None:
        logging.getLogger("lspr_app.bootstrap").log(int(levelno), str(message))
        return
    logger = logger.getChild(str(source)) if source else logger
    logger.log(int(levelno), str(message))


def log_debug(window, message: str, source: str = "main") -> None:
    log_event(window, logging.DEBUG, message, source=source)


def log_info(window, message: str, source: str = "main") -> None:
    log_event(window, logging.INFO, message, source=source)


def log_success(window, message: str, source: str = "main") -> None:
    log_event(window, SUCCESS_LOG_LEVEL, message, source=source)


def log_warning(window, message: str, source: str = "main") -> None:
    log_event(window, logging.WARNING, message, source=source)


def log_error(window, message: str, source: str = "main") -> None:
    log_event(window, logging.ERROR, message, source=source)


def log_throttled(window, key: str, message: str, *, level: int = logging.DEBUG, min_interval: float = 1.5) -> None:
    line = str(message).strip()
    if not line:
        return
    now = perf_counter()
    state = window._log_throttle_state
    previous = state.get(str(key))
    if previous is not None:
        previous_at, _previous_line = previous
        if (now - previous_at) < float(min_interval):
            suppressed_events = getattr(window, "_log_throttle_suppressed_events", None)
            if suppressed_events is not None:
                suppressed_events.append((now, str(key), line))
            return
    state[str(key)] = (now, line)
    log_event(window, level, line)


def refresh_session_summary_for(window, force: bool = False) -> None:
    target = getattr(window, "session_summary", None) or getattr(window, "session_settings_text", None)
    if target is None:
        return
    now = perf_counter()
    if not force and (now - window._last_summary_refresh_ts) < 1.0:
        return
    scrollbar = target.verticalScrollBar()
    old_value = scrollbar.value()
    old_max = max(scrollbar.maximum(), 1)
    stay_at_bottom = old_value >= max(old_max - 2, 0)
    started = perf_counter()
    text = build_session_panel_html_for(window)
    if force or text != window._last_summary_text:
        target.setHtml(text)
        apply_font_size = getattr(window, "_apply_text_widget_font_size", None)
        if callable(apply_font_size):
            apply_font_size(target, float(getattr(target, "_panel_font_size_pt", 8.0)), minimum=7.0, maximum=16.0)
        window._last_summary_text = text
        scrollbar = target.verticalScrollBar()
        if stay_at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            target_value = int(round((old_value / old_max) * max(scrollbar.maximum(), 0)))
            scrollbar.setValue(max(0, min(target_value, scrollbar.maximum())))
    window._last_summary_refresh_ms = (perf_counter() - started) * 1000.0
    window._last_summary_refresh_ts = now


def refresh_session_statistics_for(window, force: bool = False) -> None:
    target = getattr(window, "session_summary", None) or getattr(window, "session_statistics_text", None)
    if target is None:
        return
    now = perf_counter()
    if not force and (now - window._last_session_stats_refresh_ts) < 0.25:
        return
    scrollbar = target.verticalScrollBar()
    old_value = scrollbar.value()
    old_max = max(scrollbar.maximum(), 1)
    stay_at_bottom = old_value >= max(old_max - 2, 0)
    started = perf_counter()
    text = build_session_panel_html_for(window)
    if force or text != window._last_session_stats_text:
        target.setHtml(text)
        apply_font_size = getattr(window, "_apply_text_widget_font_size", None)
        if callable(apply_font_size):
            apply_font_size(target, float(getattr(target, "_panel_font_size_pt", 8.0)), minimum=7.0, maximum=16.0)
        window._last_session_stats_text = text
        scrollbar = target.verticalScrollBar()
        if stay_at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            target_value = int(round((old_value / old_max) * max(scrollbar.maximum(), 0)))
            scrollbar.setValue(max(0, min(target_value, scrollbar.maximum())))
        append_session_stats_log_snapshot_for(window, text)
        _append_diagnostic_snapshot_export(window)
    window._last_session_stats_refresh_ms = (perf_counter() - started) * 1000.0
    window._last_session_stats_refresh_ts = now


def build_session_panel_plain_text_for(window) -> str:
    summary_text = getattr(window, "_build_summary_text", lambda: "")().strip()
    stats_text = build_session_statistics_text_for(window).strip()
    if summary_text and stats_text:
        return f"{summary_text}\n{stats_text}"
    return summary_text or stats_text


def build_session_panel_html_for(window) -> str:
    summary_text = getattr(window, "_build_summary_text", lambda: "")().strip()
    stats_text = build_session_statistics_text_for(window).strip()
    sections: list[str] = []
    if summary_text:
        sections.append(
            "<div style='margin:0 0 6px 0;'>"
            "<div style='font-weight:700; margin:0 0 2px 0;'>Session settings</div>"
            f"<div style='white-space:pre-wrap; margin:0;'>{escape(summary_text).replace(chr(10), '<br>')}</div>"
            "</div>"
        )
    if stats_text:
        sections.append(
            "<div style='margin:0;'>"
            "<div style='font-weight:700; margin:0 0 2px 0;'>Session statistics</div>"
            f"<div style='white-space:pre-wrap; margin:0;'>{escape(stats_text).replace(chr(10), '<br>')}</div>"
            "</div>"
        )
    return "".join(sections) if sections else "<div></div>"


def build_session_statistics_text_for(window) -> str:
    snapshot = SessionDiagnosticsSnapshot.from_window(window)
    return "\n".join(build_session_statistics_lines(snapshot))


def append_session_stats_log_snapshot_for(window, text: str | None = None) -> None:
    if not getattr(window, "_measurement_active", False) and not getattr(window, "_session_stats_recording_active", False):
        return
    if not bool(getattr(window, "_session_stats_recording_enabled_by_default", False)) and not bool(
        getattr(window, "_session_stats_recording_active", False)
    ):
        return
    log = getattr(window, "_session_stats_log", None)
    if log is None:
        return
    now = perf_counter()
    last_at = float(getattr(window, "_session_stats_log_last_capture_ts", 0.0) or 0.0)
    if text is None:
        text = build_session_panel_plain_text_for(window)
    if text == getattr(window, "_session_stats_log_last_text", None) and (now - last_at) < 1.0:
        return
    timestamp = datetime.now().strftime("%H:%M:%S")
    log.append(f"[{timestamp}] {text}")
    window._session_stats_log_last_text = text
    window._session_stats_log_last_capture_ts = now


def describe_spectrum_for(window, spectrum) -> str:
    if spectrum is None:
        return "not acquired"

    integration_time = spectrum.metadata.get("integration_time_ms", "-")
    averages = spectrum.metadata.get("averages", "-")
    correct_dark = "on" if spectrum.metadata.get("electric_dark_pixel_correction") else "off"
    dark_pixel_count = spectrum.metadata.get("electric_dark_pixel_count", "-")
    correct_nonlinearity = "on" if spectrum.metadata.get("correct_nonlinearity") else "off"
    display_count = spectrum.metadata.get("display_average_count")
    display_window_ms = spectrum.metadata.get("display_window_ms")
    display_text = ""
    if display_count is not None and display_window_ms is not None:
        display_text = f" | displayed {display_count} raw_spectra / {float(display_window_ms):.1f} ms"
    return (
        f"{spectrum.acquired_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"{len(spectrum.wavelengths_nm)} points | "
        f"integration {integration_time} ms | "
        f"accumulation {averages} raw_spectra | "
        f"elec dark corr {correct_dark} ({dark_pixel_count} px) | "
        f"nonlinear corr {correct_nonlinearity}"
        f"{display_text}"
    )
