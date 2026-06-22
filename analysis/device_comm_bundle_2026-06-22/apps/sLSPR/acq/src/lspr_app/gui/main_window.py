from __future__ import annotations

import logging
import math
import os
import queue
import threading
from collections import deque
from html import escape
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from scipy.interpolate import CubicSpline
from pyqtgraph import exporters as pg_exporters

from PyQt6.QtCore import QPoint, QSize, Qt, QThreadPool, QTimer, QUrl, QRectF, pyqtSignal
from PyQt6.QtCore import QEvent
from PyQt6.QtGui import (
    QDesktopServices,
    QFont,
    QColor,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QFontMetricsF,
    QPainter,
    QPixmap,
    QTransform,
    QShortcut,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QMenu,
    QGraphicsRectItem,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QToolBar,
    QTabWidget,
    QSlider,
    QSplitter,
    QSplitterHandle,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lspr_app import __version__
from lspr_app.diagnostics import DiagnosticsConfig
from lspr_app.device.base import Spectrometer, SpectrometerCapabilities
from lspr_app.device.amf_mswitch import detect_amf_mswitch_devices
from lspr_app.device.reglo_icc import RegloICCClient, is_probable_reglo_port
from lspr_app.device.port_assignments import get_port_assignment, should_probe_port_for_role
from lspr_app.device.probe_diagnostics import record_port_probe_event
from lspr_app.device.serial_controllers import SerialController, controller_port_priority
from lspr_app.device.valve_controllers import detect_valve_controller
from lspr_app.device.simulated import SimulationParameters, SimulatedSpectrometer
from lspr_app.domain.models import AcquisitionSettings, ProcessingSettings, Spectrum
from lspr_app.domain.processing import fit_processed_spectrum, set_processing_debug_mode_enabled
from lspr_app.domain.session import MeasurementError, MeasurementSession
from lspr_app.storage.app_config import (
    load_processing_settings,
    load_acquisition_state,
    load_app_setting,
    load_window_ui_state,
    save_acquisition_state,
    save_app_setting,
    save_window_ui_state,
)
from lspr_app.storage.csv_export import export_spectrum_to_csv
from lspr_app.storage.hdf5_export import AsyncHDF5MeasurementWriter, load_processed_metric_history
from lspr_core import (
    DEFAULT_LAUNCH_PROFILE,
    LAUNCH_PROFILE_CONTROL_EDITOR,
    LAUNCH_PROFILE_FULL,
    launch_profile_spec,
    normalize_launch_profile,
)
from lspr_app.gui.chrome import build_menu_bar
from lspr_app.gui.logging_utils import GuiLogBridge, GuiLogHandler, SUCCESS_LOG_LEVEL
from lspr_app.gui.main_window_headers import (
    install_source_link_buttons,
    install_source_tab_headers,
    math_function_tab_icon,
    prism_tab_icon,
    update_source_link_buttons,
    update_source_tab_headers,
)
from lspr_app.gui.main_window_runtime_state import UiRefreshState
from lspr_app.gui.icon_helpers import (
    dark_icon,
    heatmap_icon,
    reference_icon,
    reload_icon,
    residual_icon,
    flow_tabler_icon,
    snowflake_icon,
    storage_compression_icon,
    storage_save_icon,
    transport_icon,
    tint_tabler_icon,
)
from lspr_app.gui.main_window_titlebar import build_title_bar, refresh_hw_device_status_strip, sync_window_control_icons
from lspr_app.gui.main_window_panels import (
    build_spectra_processing_group,
    build_simulation_page,
    build_spectrometer_page,
    configure_spectra_processing_group_controls,
)
from lspr_app.gui.main_window_plot_settings import show_sensorgram_plot_settings_dialog_for
from lspr_app.gui.main_window_preferences import show_preferences_dialog_for
from lspr_app.gui.main_window_runtime import (
    build_runtime_drift_lines as build_runtime_drift_lines_for_runtime,
    drain_gui_housekeeping_tasks as drain_gui_housekeeping_tasks_for,
    flush_deferred_display_refreshes as flush_deferred_display_refreshes_for_runtime,
    flush_deferred_metric_refreshes as flush_deferred_metric_refreshes_for_runtime,
    flush_deferred_ui_refreshes as flush_deferred_ui_refreshes_for_runtime,
    flush_deferred_stats_refreshes as flush_deferred_stats_refreshes_for_runtime,
    flush_plot_refreshes as flush_plot_refreshes_for_runtime,
    initialize_runtime_drift_probe as initialize_runtime_drift_probe_for_runtime,
    refresh_session_statistics as refresh_session_statistics_for_runtime,
    refresh_session_summary as refresh_session_summary_for_runtime,
    request_deferred_ui_refresh as request_deferred_ui_refresh_for_runtime,
    request_live_acquisition_poll as request_live_acquisition_poll_for_runtime,
    request_live_visual_refresh as request_live_visual_refresh_for_runtime,
    request_plot_refresh as request_plot_refresh_for_runtime,
    run_gui_callback_timed as run_gui_callback_timed_for_runtime,
)
from lspr_app.gui.main_window_lifecycle import (
    acquisition_state_payload_for,
    collapsible_section_state_for,
    disconnect_all_devices_for,
    ensure_experiment_control_panel_for,
    finish_hardware_initialization_for,
    handle_hardware_init_finished_for,
    handle_hardware_init_step_for,
    persist_acquisition_state_for,
    restore_collapsible_section_state_for,
    restore_ui_state_for,
    save_ui_state_for,
    schedule_acquisition_state_persist_for,
    schedule_ui_state_persist_for,
    set_acquisition_state_autosave_enabled_for,
    set_log_buffering_enabled_for,
    set_ui_state_autosave_enabled_for,
    start_hardware_initialization_for,
    sync_experiment_control_startup_ports_for,
    sync_hardware_menu_actions_for,
)
from lspr_app.gui.main_window_layout import build_main_layout_for, build_recording_context_row_for
from lspr_app.gui.main_window_logging_ui import initialize_logging_ui_for
from lspr_app.gui.main_window_sensorgram import (
    apply_sensorgram_content_mode,
    apply_sensorgram_metric_selection,
    apply_sensorgram_metric_y_axis_mode,
    apply_sensorgram_time_axis_mode,
    append_sensorgram_heatmap_history,
    cycle_sensorgram_content_mode,
    cycle_sensorgram_metric_y_axis_mode,
    cycle_trace_stats_metric,
    normalize_sensorgram_content_mode,
    normalize_sensorgram_line_mode,
    primary_trace_metric as primary_trace_metric_for_sensorgram,
    sensorgram_content_mode_tooltip as sensorgram_content_mode_tooltip_for_sensorgram,
    sensorgram_metric_selection as sensorgram_metric_selection_for_sensorgram,
    trace_stats_metric as trace_stats_metric_for_sensorgram,
    toggle_sensorgram_time_axis_mode,
    update_sensorgram_content_mode_button,
    update_sensorgram_metric_y_axis_mode_button,
)
from lspr_app.gui.main_window_sensorgram_archive import (
    apply_sensorgram_display_style,
    handle_sensorgram_heatmap_archive_failed,
    handle_sensorgram_heatmap_archive_result,
    sensorgram_heatmap_lookup_table,
    request_absolute_sensorgram_metric_archive_reload,
    request_sensorgram_heatmap_archive,
    request_sensorgram_metric_archive_reload,
    set_sensorgram_heatmap_notice,
    start_sensorgram_heatmap_archive_task,
    start_sensorgram_metric_archive_reload_task,
)
from lspr_app.gui.main_window_sensorgram_overlay import (
    handle_trace_view_range_changed as handle_trace_view_range_changed_for_sensorgram,
    normalize_sensorgram_control_step_overlay_position as normalize_sensorgram_control_step_overlay_position_for_sensorgram,
    normalize_sensorgram_control_step_overlay_style as normalize_sensorgram_control_step_overlay_style_for_sensorgram,
    record_sensorgram_control_step_event as record_sensorgram_control_step_event_for_sensorgram,
    sensorgram_control_step_overlay_bar_rect as sensorgram_control_step_overlay_bar_rect_for_sensorgram,
    sensorgram_control_step_overlay_brush as sensorgram_control_step_overlay_brush_for_sensorgram,
    sensorgram_control_step_overlay_clip_segment as sensorgram_control_step_overlay_clip_segment_for_sensorgram,
    sensorgram_control_step_overlay_current_elapsed_s as sensorgram_control_step_overlay_current_elapsed_s_for_sensorgram,
    sensorgram_control_step_overlay_label_font as sensorgram_control_step_overlay_label_font_for_sensorgram,
    sensorgram_control_step_overlay_remove_items as sensorgram_control_step_overlay_remove_items_for_sensorgram,
    sensorgram_control_step_overlay_reserved_y_padding as sensorgram_control_step_overlay_reserved_y_padding_for_sensorgram,
    sensorgram_control_step_overlay_set_tooltip as sensorgram_control_step_overlay_set_tooltip_for_sensorgram,
    sensorgram_control_step_overlay_set_visible as sensorgram_control_step_overlay_set_visible_for_sensorgram,
    sensorgram_control_step_overlay_text_color as sensorgram_control_step_overlay_text_color_for_sensorgram,
    sensorgram_control_step_overlay_view_bounds as sensorgram_control_step_overlay_view_bounds_for_sensorgram,
    sync_sensorgram_control_step_overlay as sync_sensorgram_control_step_overlay_for_sensorgram,
)
from lspr_app.gui.main_window_state import (
    acquisition_state_payload,
    activate_experimental_control_view,
    activate_experiment_control_view,
    activate_spectra_view,
    apply_acquisition_state_to_widgets,
    apply_layout_preset,
    collapsible_section_state,
    persist_acquisition_state,
    reset_layout_presets_to_defaults,
    restore_collapsible_section_state,
    restore_ui_state,
    save_current_layout_to_preset,
    save_ui_state,
    schedule_acquisition_state_persist,
    set_gui_housekeeping_enabled,
    set_diagnostics_panel_visible,
    set_metric_plot_enabled,
    set_sensorgram_heatmap_enabled,
    show_experimental_control_only,
    show_plots_only,
    show_split_view,
    sync_diagnostics_panel_action,
    sync_main_view_visibility,
    sync_view_actions,
    toggle_diagnostics_panel,
    toggle_experimental_control_panel_visibility,
    toggle_left_controls,
    toggle_sensorgram,
    launch_profile_settings,
    apply_launch_profile_layout,
)
from lspr_app.gui.main_window_processing import (
    apply_processing_settings_to_widgets,
    populate_analysis_resolution_combo,
    current_processing_settings,
    normalize_sensorgram_metric_name,
    load_processing_settings_dialog,
    persist_processing_settings,
    primary_trace_metric,
    save_processing_settings_dialog,
    selected_trace_metrics,
    sensorgram_metric_archive_names,
    sensorgram_metric_order,
    sensorgram_metric_selection,
    schedule_processing_refresh,
    sync_processing_crop_parameter_widget,
    sync_legacy_metric_widgets_from_state,
)
from lspr_app.gui.main_window_plotting import (
    analysis_cache_token_for,
    analysis_metrics_cache_token_for,
    apply_temporal_smoothing_for,
    autoscale_residual_axis_for,
    autoscale_spectrum_plot_for,
    apply_processing_range_to_spectrum_plot_for,
    autoscale_metric_plot_for,
    build_summary_text_for,
    clear_trace_history_for,
    compute_centroid_nm_for,
    compute_metric_nm_for,
    compute_peak_metric_nm_for,
    compute_trace_metrics_for,
    headroom_value_text_for,
    enqueue_plot_processing_for,
    flush_deferred_ui_refreshes_for,
    flush_plot_refreshes_for,
    get_analysis_metrics_for,
    get_analysis_processed_spectrum_for,
    get_dense_analysis_curve_for,
    get_processed_spectrum_for,
    handle_plot_processing_result_for,
    handle_residual_toggle_for,
    handle_live_setting_change_for,
    handle_simulation_output_rate_change_for,
    handle_spectrum_mouse_moved_for,
    handle_metric_mouse_moved_for,
    live_skip_rate_hz_for,
    needs_gaussian_metric_for,
    processing_cache_token_for,
    refresh_plot_for,
    refresh_telemetry_for,
    refresh_spectrometer_stats_for,
    refresh_metric_plot_for,
    start_plot_processing_task_for,
    reference_peak_nm_for_shift_for,
    render_metric_series_for,
    request_metric_autoscale_for,
    set_sensorgram_frozen_for,
    set_plots_frozen_for,
    temporal_history_token_for,
    update_poly_warning_indicator_for,
    update_residual_axis_visibility_for,
    update_residual_view_geometry_for,
    update_spectrum_stats_for,
    update_metric_stats_for,
    update_live_estimate_for,
    update_window_mode_label_for,
)
from lspr_app.gui.sensorgram_control_step_overlay import (
    normalize_sensorgram_control_step_overlay_color,
    normalize_sensorgram_control_step_overlay_label,
    resolve_sensorgram_control_step_overlay_palette_label,
)
from lspr_app.gui.plot_view_cache import (
    PlotViewCache,
    build_active_trace_series_token,
    derive_heatmap_levels_from_matrix,
)
from lspr_app.gui.update_scheduler import GuiTaskScheduler
from lspr_app.resources import app_icon_path
from lspr_app.gui.main_window_logging import (
    append_log_record,
    append_log_record_now,
    clear_log_terminal,
    copy_log_terminal,
    copy_session_stats_log_for,
    copy_session_stats_snapshot_for,
    describe_spectrum_for,
    flush_log_buffer,
    log_debug,
    log_error,
    log_event,
    log_info,
    log_success,
    log_throttled,
    log_warning,
    refresh_session_summary_for,
    refresh_session_statistics_for,
    set_log_following,
    set_log_view_mode,
    save_session_stats_log_for,
)
from lspr_app.gui.acquisition_controller import (
    append_processed_trace_history,
    flush_measurement_frames,
    flush_live_acquisition_results,
    flush_live_recording_results,
    flush_live_processed_results,
    handle_acquisition_error,
    handle_acquisition_success,
    request_manual_acquisition,
    set_manual_acquisition_buttons_enabled,
    set_measurement_buttons_enabled,
    set_measurement_ui_locked,
    start_acquisition,
    start_measurement_run,
    start_live_acquisition,
    stop_measurement_run,
    stop_live_acquisition,
    toggle_measurement_run,
    update_measurement_toggle_button,
    update_window_mode_label,
)
from lspr_app.gui.shortcut_help import build_shortcuts_help_text
from lspr_app.gui.plot_controller import flush_deferred_ui_refreshes, flush_plot_refreshes, refresh_plot
from lspr_app.gui.spectrum_plot_controller import (
    autoscale_residual_axis,
    autoscale_spectrum_plot,
    clear_residual_display,
    clip_series_to_window,
    downsample_spectrum_series_for_view,
    handle_spectrum_mouse_moved,
    ResidualViewBox,
    render_residual_display,
    spectrum_render_cache_key,
    update_residual_axis_visibility,
    update_residual_view_geometry,
    update_spectrum_stats,
)
from lspr_app.gui.trace_plot_controller import (
    autoscale_metric_plot,
    handle_metric_mouse_moved,
    refresh_metric_plot,
    render_metric_series,
    request_metric_autoscale,
    update_metric_stats,
)
from lspr_app.gui.processing_helpers import (
    analysis_cache_token,
    analysis_metrics_cache_token,
    compute_centroid_nm,
    compute_metric_nm,
    compute_peak_metric_nm,
    compute_trace_metrics,
    get_analysis_metrics,
    get_dense_analysis_curve,
    get_processed_spectrum,
    needs_gaussian_metric,
    processing_cache_token,
)
from lspr_app.gui.hardware_initializer import (
    HardwareInitResult,
    HardwareInitStep,
    HardwareInitStepResult,
    HardwareInitTask,
)
from lspr_app.gui.hardware_inventory_dialog import show_hardware_inventory_dialog
from lspr_app.gui.usb_probe_diagnostics_dialog import show_usb_probe_diagnostics_dialog
from lspr_app.gui.workers import (
    AcquisitionResult,
    HeatmapArchiveLoadRequest,
    HeatmapArchiveLoadResult,
    HeatmapArchiveLoadTask,
    MetricArchiveReloadRequest,
    MetricArchiveReloadResult,
    MetricArchiveReloadTask,
    LiveAcquisitionEvent,
    LiveAcquisitionWorker,
    LiveProcessedEvent,
    LiveProcessingWorker,
    ProcessingRequest,
    ProcessingResult,
    ProcessingTask,
)
from lspr_app.gui.widgets import InlineWheelDoubleLabel
from lspr_app.gui.ui_helpers import (
    create_status_dot_icon,
    make_compact_spinbox,
    make_sim_slider,
    make_window_button,
    window_control_icon,
)
from lspr_app.gui.widgets import (
    CollapsibleSection,
    CompactSplitter,
    ElidingLabel,
    FlexibleTimeAxis,
    ScientificAxis,
)
if TYPE_CHECKING:
    from lspr_app.device.reglo_icc import PumpProbe
    from lspr_app.gui.experiment_control_window import ExperimentControlWindow


class LogTerminalTextEdit(QTextEdit):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._min_font_size = 7.0
        self._max_font_size = 16.0

    def wheelEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta_y = event.angleDelta().y()
            if delta_y == 0:
                event.ignore()
                return
            factor = 1.1 if delta_y > 0 else 1 / 1.1
            current_font = QFont(self.font())
            current_size = float(current_font.pointSizeF())
            if current_size <= 0:
                current_size = float(current_font.pointSize()) if current_font.pointSize() > 0 else 9.0
            if current_size <= 0:
                current_size = 9.0
            font = QFont(current_font)
            new_size = max(self._min_font_size, min(current_size * factor, self._max_font_size))
            if new_size <= 0:
                new_size = self._min_font_size
            font.setPointSizeF(new_size)
            self.setFont(font)
            self.document().setDefaultFont(font)
            event.accept()
            return
        super().wheelEvent(event)


class TimedPlotWidget(pg.PlotWidget):
    def __init__(self, *args, paint_owner=None, paint_prefix: str = "", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._paint_owner = paint_owner
        self._paint_prefix = str(paint_prefix)
        self._diagnostics_owner = paint_owner
        self._diagnostics_prefix = str(paint_prefix)

    def _record_operation(self, operation: str) -> None:
        owner = getattr(self, "_diagnostics_owner", None)
        prefix = getattr(self, "_diagnostics_prefix", "")
        if owner is None or not prefix:
            return
        try:
            events = getattr(owner, "_plot_operation_events", None)
            if events is not None:
                events.append((perf_counter(), prefix, str(operation)))
            total_name = f"_{prefix}_{operation}_count"
            setattr(owner, total_name, int(getattr(owner, total_name, 0)) + 1)
        except Exception:
            return

    def paintEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        started = perf_counter()
        super().paintEvent(event)
        elapsed_ms = (perf_counter() - started) * 1000.0
        owner = getattr(self, "_paint_owner", None)
        prefix = getattr(self, "_paint_prefix", "")
        if owner is None or not prefix:
            return
        count_name = f"_{prefix}_paint_count"
        total_name = f"_{prefix}_paint_total_ms"
        max_name = f"_{prefix}_paint_max_ms"
        try:
            setattr(owner, count_name, int(getattr(owner, count_name, 0)) + 1)
            setattr(owner, total_name, float(getattr(owner, total_name, 0.0) or 0.0) + elapsed_ms)
            current_max = float(getattr(owner, max_name, 0.0) or 0.0)
            setattr(owner, max_name, max(current_max, elapsed_ms))
            events = getattr(owner, "_plot_paint_events", None)
            if events is not None:
                now = perf_counter()
                events.append((now, prefix, float(elapsed_ms)))
                recent_values = [value for ts, name, value in events if name == prefix and (now - float(ts)) <= 10.0]
                recent_count_name = f"_{prefix}_paint_recent_count"
                recent_total_name = f"_{prefix}_paint_recent_total_ms"
                recent_max_name = f"_{prefix}_paint_recent_max_ms"
                recent_avg_name = f"_{prefix}_paint_recent_avg_ms"
                if recent_values:
                    recent_total_ms = float(sum(recent_values))
                    setattr(owner, recent_count_name, len(recent_values))
                    setattr(owner, recent_total_name, recent_total_ms)
                    setattr(owner, recent_max_name, max(recent_values))
                    setattr(owner, recent_avg_name, recent_total_ms / len(recent_values))
                else:
                    setattr(owner, recent_count_name, 0)
                    setattr(owner, recent_total_name, 0.0)
                    setattr(owner, recent_max_name, 0.0)
                    setattr(owner, recent_avg_name, 0.0)
            notify_startup_paint = getattr(owner, "_notify_startup_plot_painted", None)
            if callable(notify_startup_paint):
                notify_startup_paint(prefix)
        except Exception:
            return

    def setLabel(self, *args, **kwargs):  # pragma: no cover - GUI runtime path
        self._record_operation("setLabel")
        plot_item = self.getPlotItem()
        return plot_item.setLabel(*args, **kwargs)

    def setXRange(self, *args, **kwargs):  # pragma: no cover - GUI runtime path
        self._record_operation("setXRange")
        return super().setXRange(*args, **kwargs)

    def setYRange(self, *args, **kwargs):  # pragma: no cover - GUI runtime path
        self._record_operation("setYRange")
        return super().setYRange(*args, **kwargs)

    def enableAutoRange(self, *args, **kwargs):  # pragma: no cover - GUI runtime path
        self._record_operation("enableAutoRange")
        return super().enableAutoRange(*args, **kwargs)

    def autoRange(self, *args, **kwargs):  # pragma: no cover - GUI runtime path
        self._record_operation("autoRange")
        return super().autoRange(*args, **kwargs)


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


class MainWindow(QMainWindow):
    hardware_init_progress = pyqtSignal(int, str)
    hardware_init_finished = pyqtSignal()
    PLOT_MODES = {
        "Dark": "dark",
        "Reference": "reference",
        "Sample": "sample",
        "Absorbance": "absorbance",
    }
    TRACE_METRIC_LABELS = {
        "smoothed_max": "Smoothed max",
        "poly_max": "Polynomial max",
        "centroid": "Peak centroid",
        "gaussian_center": "Gaussian center",
    }
    TRACE_METRIC_SHORT_LABELS = {
        "smoothed_max": "S_Max",
        "poly_max": "P_Max",
        "centroid": "Cent",
        "gaussian_center": "G_Ctr",
    }
    TRACE_METRIC_DESCRIPTIONS = {
        "smoothed_max": "Maximum of the smoothed raw data.",
        "poly_max": "Maximum of the fitted polynomial.",
        "centroid": "Centroid of the smoothed peak, estimated from the processed curve within the selected analysis window.",
        "gaussian_center": "Center parameter of the gaussian fit.",
    }
    TRACE_METRIC_COLORS = {
        "smoothed_max": "#0072B2",
        "poly_max": "#E69F00",
        "centroid": "#009E73",
        "gaussian_center": "#D55E00",
    }
    SENSORGRAM_TIME_PLOT_COLORS = {
        "smoothed_max": "#1F77B4",
        "poly_max": "#E69F00",
        "centroid": "#009E73",
        "gaussian_center": "#D55E00",
    }

    def __init__(
        self,
        spectrometer: Spectrometer,
        session: MeasurementSession,
        discovered_pump_probe: "PumpProbe | None" = None,
        launch_profile: str = DEFAULT_LAUNCH_PROFILE,
    ) -> None:
        super().__init__()
        self._launch_profile = normalize_launch_profile(launch_profile)
        self._launch_profile_spec = launch_profile_spec(self._launch_profile)
        self._startup_t0 = perf_counter()
        self._startup_timing_buffer: list[str] = []

        def _startup_mark(label: str) -> None:
            message = f"Startup +{(perf_counter() - self._startup_t0) * 1000.0:.1f} ms: {label}"
            if hasattr(self, "_ui_logger"):
                self._ui_logger.info(message)
            else:
                self._startup_timing_buffer.append(message)

        _startup_mark("QMainWindow base initialized")
        self._spectrometer = spectrometer
        self._hardware_session = session
        self._simulation_session = MeasurementSession()
        self._simulation_backend = SimulatedSpectrometer()
        self._session = self._hardware_session
        self._source_mode = self._launch_profile_spec.source_mode
        self._hardware_available = not isinstance(spectrometer, SimulatedSpectrometer)
        self._thread_pool = QThreadPool.globalInstance()
        self._simulation_refresh_timer = QTimer(self)
        self._simulation_refresh_timer.setSingleShot(True)
        self._simulation_refresh_timer.setInterval(60)
        self._simulation_refresh_timer.timeout.connect(self._flush_simulation_parameter_change)
        self._live_result_requested_at: float | None = None
        self._live_result_requested_delay_ms: float | None = None
        self._last_live_result_poll_delay_ms: float | None = None
        self._live_processed_requested_at: float | None = None
        self._live_processed_requested_delay_ms: float | None = None
        self._last_live_processed_poll_delay_ms: float | None = None
        self._last_live_visual_refresh_ms: float | None = None
        self._live_visual_refresh_count: int = 0
        self._live_visual_refresh_in_progress: bool = False
        self._live_mode_deferred_display_flush_count: int = 0
        self._live_mode_deferred_metric_flush_count: int = 0
        self._live_mode_deferred_stats_flush_count: int = 0
        self._plot_refresh_requested_at: float | None = None
        self._last_plot_refresh_delay_ms: float | None = None
        self._ui_task_scheduler = GuiTaskScheduler(self)
        self._trace_autoscale_timer = QTimer(self)
        self._trace_autoscale_timer.setSingleShot(True)
        self._trace_autoscale_timer.setInterval(500)
        self._trace_autoscale_timer.timeout.connect(self._autoscale_trace_plot)
        self._display_refresh_requested_at: float | None = None
        self._last_display_refresh_delay_ms: float | None = None
        self._stats_refresh_requested_at: float | None = None
        self._last_stats_refresh_delay_ms: float | None = None
        self._session_stats_refresh_requested_at: float | None = None
        self._session_stats_refresh_delay_ms: float = 250.0
        self._ui_heartbeat_timer = QTimer(self)
        self._ui_heartbeat_timer.setInterval(100)
        self._ui_heartbeat_timer.timeout.connect(self._ui_heartbeat_tick)
        self._ui_heartbeat_interval_ms = 100.0
        self._ui_heartbeat_expected_at: float | None = None
        self._last_ui_heartbeat_delay_ms: float | None = None
        self._ui_heartbeat_max_delay_ms: float | None = None
        self._last_ui_heartbeat_total_ms: float | None = None
        self._last_gui_housekeeping_total_ms: float | None = None
        self._busy = False
        self._live_active = False
        self._live_worker: LiveAcquisitionWorker | None = None
        self._live_processing_worker: LiveProcessingWorker | None = None
        self._live_stop_event = threading.Event()
        self._live_result_queue: queue.Queue[LiveAcquisitionEvent] = queue.Queue(maxsize=4)
        self._live_processing_input_queue: queue.Queue[LiveAcquisitionEvent] = queue.Queue(maxsize=16)
        self._live_processed_queue: queue.Queue[LiveProcessedEvent] = queue.Queue(maxsize=4)
        self._live_processing_log_queue: queue.Queue[tuple[int, str, str]] = queue.Queue(maxsize=32)
        self._live_recording_queue = None
        self._live_recording_timer = QTimer(self)
        self._live_recording_timer.setSingleShot(False)
        self._live_recording_timer.setInterval(25)
        self._live_recording_timer.timeout.connect(self._flush_live_recording_results)
        self._measurement_active = False
        self._measurement_paused = False
        self._measurement_writer: AsyncHDF5MeasurementWriter | None = None
        self._measurement_path: Path | None = None
        self._measurement_compression_task: MeasurementCompressionTask | None = None
        self._measurement_signal_mode = "absorbance"
        self._measurement_started_at: datetime | None = None
        self._measurement_experiment_name = ""
        self._measurement_flush_interval_s = 5.0
        self._measurement_axis_lock: np.ndarray | None = None
        self._metric_archive_writer: AsyncHDF5MeasurementWriter | None = None
        self._metric_archive_path: Path | None = None
        self._metric_archive_started_at: datetime | None = None
        self._plot_view_cache = PlotViewCache()
        self._metric_reference_processed: Spectrum | None = None
        self._live_trace_started_at: datetime | None = None
        self._pending_manual_kind: str | None = None
        self._pending_source_mode: str | None = None
        self._resume_live_after_source_switch = False
        self._resume_live_after_manual = False
        self._pending_auto_integration = False
        self._resume_live_after_auto_integration = False
        self._display_window_ms = 0.0
        self._raw_last_finish_ts: float | None = None
        self._raw_last_sample_index: int | None = None
        self._last_elapsed_ms: float | None = None
        self._last_spacing_ms: float | None = None
        self._last_overhead_ms: float | None = None
        self._effective_raw_rate_hz: float | None = None
        self._last_processing_ms: float | None = None
        self._last_processing_queue_wait_ms: float | None = None
        self._processing_rate_hz: float | None = None
        self._processing_headroom_ratio: float | None = None
        self._last_live_acquisition_flush_ms: float | None = None
        self._last_live_processed_flush_ms: float | None = None
        self._last_display_average_count: int | None = None
        self._last_display_period_ms: float | None = None
        self._last_plot_refresh_ms: float | None = None
        self._last_plot_refresh_gap_ms: float | None = None
        self._last_plot_refresh_finished_at: float | None = None
        self._actual_plot_refresh_rate_hz: float | None = None
        self._plot_refresh_timestamps: list[float] = []
        self._plot_refresh_rate_window_frames = 5
        self._last_plot_refresh_total_ms: float | None = None
        self._last_spectrum_curve_update_ms: float | None = None
        self._last_spectrum_fit_update_ms: float | None = None
        self._last_spectrum_marker_update_ms: float | None = None
        self._last_spectrum_residual_update_ms: float | None = None
        self._last_sensorgram_render_ms: float | None = None
        self._last_live_trace_stats_ms: float | None = None
        self._last_sensorgram_heatmap_render_ms: float | None = None
        self._last_sensorgram_heatmap_arrays_ms: float | None = None
        self._last_sensorgram_heatmap_image_ms: float | None = None
        self._last_sensorgram_heatmap_axes_ms: float | None = None
        self._last_metric_autoscale_ms: float | None = None
        self._metric_autoscale_min_interval_s = 1.0
        self._metric_autoscale_throttle_mode = "Medium"
        self._metric_autoscale_skip_tiny_changes_enabled = True
        self._metric_autoscale_x_change_fraction = 0.01
        self._metric_autoscale_y_change_fraction = 0.02
        self._metric_autoscale_follow_latest_cadence_multiplier = 1.5
        self._metric_autoscale_follow_latest_min_buffer_s = 0.25
        self._metric_autoscale_follow_latest_buffer_fraction = 0.05
        self._last_metric_autoscale_range: tuple[float, float, float, float] | None = None
        self._last_metric_plot_disabled_fast_path = False
        self._last_metric_render_series_export_ms: float | None = None
        self._last_metric_render_setdata_calls: int | None = None
        self._last_metric_render_setdata_skips: int | None = None
        self._last_metric_view_cache_modes: dict[str, object] = {}
        self._live_trace_stats_count = 0
        self._trace_plot_paint_count = 0
        self._trace_plot_paint_total_ms: float | None = None
        self._trace_plot_paint_max_ms: float | None = None
        self._trace_plot_paint_recent_count = 0
        self._trace_plot_paint_recent_total_ms: float | None = None
        self._trace_plot_paint_recent_max_ms: float | None = None
        self._trace_plot_paint_recent_avg_ms: float | None = None
        self._spectrum_plot_paint_count = 0
        self._spectrum_plot_paint_total_ms: float | None = None
        self._spectrum_plot_paint_max_ms: float | None = None
        self._spectrum_plot_paint_recent_count = 0
        self._spectrum_plot_paint_recent_total_ms: float | None = None
        self._spectrum_plot_paint_recent_max_ms: float | None = None
        self._spectrum_plot_paint_recent_avg_ms: float | None = None
        self._plot_paint_events = deque(maxlen=2000)
        self._axis_tick_events = deque(maxlen=4000)
        self._plot_operation_events = deque(maxlen=4000)
        self._last_deferred_ui_refresh_ms: float | None = None
        self._last_deferred_ui_refresh_total_ms: float | None = None
        self._last_deferred_display_refresh_ms: float | None = None
        self._last_deferred_stats_refresh_ms: float | None = None
        self._last_deferred_ui_live_estimate_ms: float | None = None
        self._last_deferred_ui_telemetry_ms: float | None = None
        self._last_deferred_ui_trace_plot_ms: float | None = None
        self._last_deferred_ui_summary_ms: float | None = None
        self._last_deferred_ui_stats_ms: float | None = None
        self._accumulator_sum: np.ndarray | None = None
        self._accumulator_count = 0
        self._accumulator_started_ts: float | None = None
        self._accumulator_settings_key: tuple[object, ...] | None = None
        self._accumulator_template: Spectrum | None = None
        self._live_display_dropped_frames = 0
        self._live_display_started_at: float | None = None
        self._last_live_processing_perf: float | None = None
        self._live_plot_update_counter = 0
        self._capabilities: SpectrometerCapabilities = self._spectrometer.capabilities()
        self._processing_settings = load_processing_settings()
        initial_visible = [normalize_sensorgram_metric_name(mode) for mode in getattr(self._processing_settings, "trace_metrics", ["smoothed_max", "centroid"])]
        ordered_visible = [mode for mode in sensorgram_metric_order(self) if mode in set(initial_visible)]
        if not ordered_visible:
            ordered_visible = ["smoothed_max"]
        initial_primary = normalize_sensorgram_metric_name(getattr(self._processing_settings, "spectrum_tracking_mode", ordered_visible[0]))
        if initial_primary not in ordered_visible:
            initial_primary = ordered_visible[0]
        self._sensorgram_metric_visible_modes = set(ordered_visible)
        self._sensorgram_metric_primary_mode = initial_primary
        self._trace_stats_metric_name = initial_primary
        metric_colors = dict(type(self).TRACE_METRIC_COLORS)
        self.TRACE_METRIC_COLORS = metric_colors
        self.SENSORGRAM_TIME_PLOT_COLORS = metric_colors
        self._ui_state = load_window_ui_state("main_window")
        self._experiment_control_window_ui_state = load_window_ui_state("experiment_control_window")
        self._acquisition_state = load_acquisition_state()
        loaded_theme = str(load_app_setting("theme_mode", "dark"))
        self._theme_mode = "dark" if loaded_theme not in {"light", "dark"} else loaded_theme
        if self._theme_mode != "dark":
            self._theme_mode = "dark"
            save_app_setting("theme_mode", self._theme_mode)
        self._suspend_processing_autosave = False
        self._suspend_acquisition_autosave = False
        self._ui_state_persistence_enabled = True
        self._ui_state_autosave_enabled = True
        self._acquisition_state_autosave_enabled = bool(load_app_setting("acquisition_state_autosave_enabled", True))
        self._log_buffering_enabled = bool(load_app_setting("log_buffering_enabled", True))
        self._gui_housekeeping_enabled = bool(load_app_setting("gui_housekeeping_enabled", True))
        self._sensorgram_heatmap_enabled = bool(load_app_setting("sensorgram_heatmap_enabled", True))
        self._sensorgram_metric_envelope_overlay_enabled = False
        self._sensorgram_metric_envelope_overlay_alpha = 16
        self._sensorgram_control_step_overlay_enabled = True
        self._sensorgram_control_step_overlay_style = "bar"
        self._sensorgram_control_step_overlay_position = "top"
        self._sensorgram_control_step_overlay_opacity = 25.0
        self._sensorgram_control_step_overlay_bar_height_px = 18
        self._metric_plot_enabled = bool(load_app_setting("metric_plot_enabled", True))
        self._last_processed_plot: Spectrum | None = None
        self._last_fit_plot: Spectrum | None = None
        self._processed_cache_key: tuple[object, ...] | None = None
        self._processed_cache_result: tuple[Spectrum | None, Spectrum | None] = (None, None)
        self._analysis_cache_key: tuple[object, ...] | None = None
        self._analysis_cache_result: tuple[np.ndarray, np.ndarray, dict[str, float | str]] = (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=np.float64),
            {},
        )
        self._analysis_metrics_cache_key: tuple[object, ...] | None = None
        self._analysis_metrics_cache_result: dict[str, object] = {}
        self._plot_processing_epoch = 0
        self._active_plot_processing_epoch = 0
        self._plot_processing_running = False
        self._pending_plot_request: ProcessingRequest | None = None
        self._temporal_processed_history: list[Spectrum] = []
        self._temporal_history_key: tuple[object, ...] | None = None
        self._trace_display_cursor_s = 0.0
        # Keep the live ring buffer bounded. Absolute mode uses a separate append-only
        # history so it can show the full run without hidden truncation.
        self._metric_live_buffer_max_points = 7200
        self._metric_history_max_points = self._metric_live_buffer_max_points
        self._trace_history_max_points = self._metric_history_max_points
        self._plot_display_points = 512
        self._sensorgram_compression_recent_tail_points = 300
        self._recording_blink_visible = True
        self._processing_debug_mode_enabled = bool(load_app_setting("processing_debug_mode", False))
        set_processing_debug_mode_enabled(self._processing_debug_mode_enabled)
        hdf5_compression_setting = load_app_setting("measurement_hdf5_compression_enabled", True)
        if isinstance(hdf5_compression_setting, str):
            self._hdf5_compression_enabled = hdf5_compression_setting.strip().lower() in {"1", "true", "yes", "on"}
        else:
            self._hdf5_compression_enabled = bool(hdf5_compression_setting)
        self._sensorgram_settings_dialog = None
        self._sensorgram_content_mode = "metric"
        self._sensorgram_time_axis_mode = "elapsed"
        self._sensorgram_metric_y_axis_mode = "auto"
        self._sensorgram_axis_started_at: datetime | None = None
        self._sensorgram_line_step_mode = None
        self._sensorgram_line_width_px = 2.2
        self._plot_antialias_enabled = False
        self.sensorgram_content_mode_button = self._make_frameless_icon_button(
            heatmap_icon(),
            "Toggle the sensorgram between Metric time plot and Heatmap.",
            size=30,
        )
        self.sensorgram_content_mode_button.setCheckable(True)
        self.sensorgram_content_mode_button.setVisible(False)
        self.sensorgram_metric_y_axis_mode_button = QToolButton(self)
        self.sensorgram_metric_y_axis_mode_button.setObjectName("sensorgramYAxisModeButton")
        self.sensorgram_metric_y_axis_mode_button.setAutoRaise(True)
        self.sensorgram_metric_y_axis_mode_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.sensorgram_metric_y_axis_mode_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sensorgram_metric_y_axis_mode_button.clicked.connect(self._cycle_sensorgram_metric_y_axis_mode)
        self._update_sensorgram_metric_y_axis_mode_button()
        self.sensorgram_settings_button = self._make_frameless_icon_button(
            tint_tabler_icon(flow_tabler_icon("settings"), QColor("#f0f3f7")),
            "Open sensorgram plot settings.",
            size=30,
        )
        self._sensorgram_heatmap_history: list[tuple[float, np.ndarray]] = []
        self._sensorgram_heatmap_history_revision = 0
        self._sensorgram_control_step_events: list[dict[str, object]] = []
        self._sensorgram_control_step_overlay_items: list[object] = []
        self._sensorgram_heatmap_history_max_rows = 800
        self._sensorgram_heatmap_wavelengths: np.ndarray | None = None
        self._sensorgram_heatmap_axis_key: tuple[int, float, float] | None = None
        self._sensorgram_heatmap_levels: tuple[float, float] | None = None
        self._sensorgram_heatmap_archive_task: HeatmapArchiveLoadTask | None = None
        self._sensorgram_heatmap_archive_loading = False
        self._sensorgram_heatmap_archive_request_token: tuple[object, ...] | None = None
        self._sensorgram_heatmap_archive_pending_token: tuple[object, ...] | None = None
        self._sensorgram_heatmap_archive_times: np.ndarray | None = None
        self._sensorgram_heatmap_archive_matrix: np.ndarray | None = None
        self._sensorgram_heatmap_archive_rows = 0
        self._sensorgram_heatmap_archive_load_ms: float | None = None
        self._sensorgram_heatmap_archive_build_ms: float | None = None
        self._sensorgram_metric_archive_reload_task: MetricArchiveReloadTask | None = None
        self._sensorgram_metric_archive_reload_loading = False
        self._sensorgram_metric_archive_reload_request_token: tuple[object, ...] | None = None
        self._sensorgram_metric_archive_reload_pending_token: tuple[object, ...] | None = None
        self._last_summary_text: str = ""
        self._last_summary_refresh_ts: float = 0.0
        self._last_summary_refresh_ms: float | None = None
        self._last_session_summary_refresh_total_ms: float | None = None
        self._trace_view_locked = False
        self._trace_view_autoscaling = False
        self._ui_refresh_state = UiRefreshState(pending_metric_label="Metric position (nm)")
        self._visible_processed_plot: Spectrum | None = None
        self._visible_fit_plot: Spectrum | None = None
        self._spectrum_render_cache_key: tuple[object, ...] | None = None
        self._last_spectrum_autoscale_range: tuple[float, float, float, float] | None = None
        self._spectrum_processing_region_bounds: tuple[float, float] | None = None
        self._spectrum_fit_region_bounds: tuple[float, float] | None = None
        self._visible_trace_x: np.ndarray | None = None
        self._visible_trace_y: np.ndarray | None = None
        self._visible_trace_mode = "elapsed"
        self._metric_render_display_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._metric_render_state_cache: dict[str, tuple[object, ...]] = {}
        self._plots_frozen = False
        self._sensorgram_frozen = False
        self._measurement_compression_blink_visible = True
        self._measurement_compression_blink_timer = QTimer(self)
        self._measurement_compression_blink_timer.setInterval(650)
        self._measurement_compression_blink_timer.timeout.connect(self._advance_measurement_compression_blink_indicator)
        self._closing = False
        self._screen_fitted = False
        self._source_epoch = 0
        self._processing_refresh_delay_ms = 120
        self._stats_refresh_delay_ms = 180
        self._live_ui_refresh_delay_ms = 220
        self._acquisition_state_timer = QTimer(self)
        self._acquisition_state_timer.setSingleShot(True)
        self._acquisition_state_timer.setInterval(250)
        self._acquisition_state_timer.timeout.connect(self._persist_acquisition_state)
        self._acquisition_state_requested_at: float | None = None
        self._last_acquisition_state_delay_ms: float | None = None
        self._last_acquisition_state_save_ms: float | None = None
        self._last_acquisition_state_total_ms: float | None = None
        self._last_gui_housekeeping_log_buffer_ms: float | None = None
        self._last_gui_housekeeping_ui_state_ms: float | None = None
        self._last_gui_housekeeping_acquisition_state_ms: float | None = None
        self._last_gui_housekeeping_session_stats_snapshot_ms: float | None = None
        self._last_gui_housekeeping_session_stats_refresh_ms: float | None = None
        self._last_log_append_now_ms: float | None = None
        self._log_received_events = deque(maxlen=4000)
        self._log_buffer_enqueue_events = deque(maxlen=4000)
        self._log_perf_suppressed_events = deque(maxlen=4000)
        self._log_throttle_suppressed_events = deque(maxlen=4000)
        self._diagnostic_export_events = deque(maxlen=4000)
        self._diagnostic_snapshot_export_events = deque(maxlen=4000)
        self._log_append_events = deque(maxlen=4000)
        self._diagnostics = DiagnosticsConfig.from_env()
        self._diagnostics_profile = self._diagnostics.profile
        self._quiet_diagnostics_mode = self._diagnostics.quiet_mode
        self._suppress_diagnostic_info_logs = self._diagnostics.suppress_info_logs
        self._export_diagnostic_events = self._diagnostics.export_diagnostic_events
        self._runtime_drift_probe_enabled = self._diagnostics.runtime_drift_probe_enabled
        self._session_stats_recording_enabled_by_default = (
            self._diagnostics.session_stats_recording_enabled_by_default
        )
        self._debug_timing_enabled = self._diagnostics.debug_timing_enabled
        self._deep_timing_enabled = self._diagnostics.deep_timing_enabled
        self._diagnostics_panel_enabled = self._diagnostics.diagnostics_panel_enabled
        self._ui_state_timer = QTimer(self)
        self._ui_state_timer.setSingleShot(True)
        self._ui_state_timer.setInterval(500)
        self._ui_state_timer.timeout.connect(self._save_ui_state)
        self._ui_state_requested_at: float | None = None
        self._last_ui_state_delay_ms: float | None = None
        self._last_ui_state_save_ms: float | None = None
        self._last_ui_state_total_ms: float | None = None
        self._hardware_init_task: HardwareInitTask | None = None
        self._hardware_init_scheduled = False
        self._spectrum_cursor_text = "cursor: -"
        self._trace_cursor_text = "cursor: -"
        self._start_maximized = False
        self._experiment_control_window: ExperimentControlWindow | None = None
        self._main_content_widget: QWidget | None = None
        self._top_view_mode = "spectra"
        self._trace_stats_metric_name: str | None = None
        self._discovered_pump_probe = discovered_pump_probe
        self._mswitch_probe = None
        self._initial_mswitch_devices: list[object] = []
        self._hardware_init_ready_emitted = False
        self._hardware_status_overrides: dict[str, tuple[bool, str]] = {}
        self.session_statistics_text: QTextEdit | None = None
        self.session_settings_text: QTextEdit | None = None
        self.session_summary: QTextEdit | None = None
        self._last_session_stats_text = ""
        self._last_session_stats_refresh_ts = 0.0
        self._last_session_stats_refresh_ms: float | None = None
        self._last_session_stats_refresh_total_ms: float | None = None
        self._session_stats_log: list[str] = []
        self._session_stats_log_last_text = ""
        self._session_stats_log_last_capture_ts = 0.0
        self._session_stats_recording_active = False
        self._session_stats_recording_started_at: datetime | None = None
        self._session_stats_recording_duration_s: float | None = None
        self._session_stats_recording_blink_visible = True
        self._session_stats_recording_blink_timer = QTimer(self)
        self._session_stats_recording_blink_timer.setInterval(650)
        self._session_stats_recording_blink_timer.timeout.connect(self._advance_session_stats_recording_blink_indicator)
        self._session_stats_recording_timer = QTimer(self)
        self._session_stats_recording_timer.timeout.connect(self._capture_session_stats_recording_snapshot)
        self._session_stats_recording_requested_at: float | None = None
        self._last_session_stats_recording_delay_ms: float | None = None
        self._last_session_stats_recording_snapshot_ms: float | None = None
        self._last_session_stats_recording_total_ms: float | None = None
        self._title_bar_widget: QWidget | None = None
        self._title_bar_drag_active = False
        self._title_bar_drag_offset = QPoint(0, 0)
        self._sensorgram_header_controls_ready = False
        self._brand_icon_path = app_icon_path()
        self._prism_icon_svg = """
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path stroke="none" d="M0 0h24v24H0z" fill="none"/>
  <path d="M2 13h4.45" stroke="#ffffff"/>
  <path d="M18 5l-4.5 6" stroke="#7b3ff2"/>
  <path d="M22 9l-7.75 3.25" stroke="#00c853"/>
  <path d="M22 15l-7 -1.5" stroke="#ff3b30"/>
  <path
    d="M4.731 19h11.539a1 1 0 0 0 .866 -1.5l-5.769 -10a1 1 0 0 0 -1.732 0l-5.769 10a1 1 0 0 0 .865 1.5"
    fill="#8edcff"
    fill-opacity="0.25"
    stroke="#8edcff"
  />
</svg>
"""

        self.setWindowTitle(f"LSPR Acquisition {__version__}")
        self.setWindowIcon(QIcon(str(self._brand_icon_path)))
        self.resize(1380, 920)
        self._startup_window_revealed = False
        self._startup_show_requested = False
        _startup_mark("window flags and icon set")

        # Keep live plots responsive by default; the user can enable smoothing from plot settings.
        pg.setConfigOptions(antialias=self._plot_antialias_enabled)
        self._apply_modern_style()
        _startup_mark("application style applied")

        self._initialize_logging_ui()
        self._processing_debug_mode_enabled = bool(
            self._processing_debug_mode_enabled or self._diagnostics.debug_timing_enabled
        )
        set_processing_debug_mode_enabled(self._processing_debug_mode_enabled)
        if self._startup_timing_buffer:
            for message in self._startup_timing_buffer:
                self._ui_logger.info(message)
            self._startup_timing_buffer.clear()
        _startup_mark("logging UI initialized")
        self._install_shortcuts()
        _startup_mark("shortcuts installed")

        self.status_label = ElidingLabel(f"Connected backend: {self._spectrometer.device_name()}")
        self.status_label.setWordWrap(False)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.status_label.setToolTip("Current source/backend connection and important application status messages.")

        self.project_destination_edit = QLineEdit(str(load_app_setting("recording_project_destination", "")))
        self.project_destination_edit.setObjectName("recordingPathEdit")
        self.project_destination_edit.setPlaceholderText("Project's destination")
        self.project_destination_edit.setToolTip("Root folder where experiment folders and HDF5 files will be saved.")
        self.project_destination_edit.setFrame(False)
        self.project_destination_edit.setFixedHeight(24)
        self.project_destination_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.project_destination_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.project_destination_browse_button = self._make_frameless_icon_button(
            tint_tabler_icon(flow_tabler_icon("folder", "folder_open"), QColor("#8fbaff")),
            "Open the project destination folder in the file explorer.",
            size=24,
        )
        self.experiment_name_edit = QLineEdit(str(load_app_setting("recording_experiment_name", "")))
        self.experiment_name_edit.setObjectName("recordingExperimentEdit")
        self.experiment_name_edit.setPlaceholderText("Experiment name")
        self.experiment_name_edit.setToolTip("Experiment folder name and part of the HDF5 file name.")
        self.experiment_name_edit.setFrame(False)
        self.experiment_name_edit.setFixedHeight(24)
        self.measurement_compression_button = self._make_frameless_icon_button(
            storage_compression_icon(self._hdf5_compression_enabled),
            "Toggle post-recording gzip compression for the finished HDF5 measurement file.",
            size=24,
        )
        self.measurement_compression_button.setCheckable(True)
        self.measurement_compression_button.setChecked(self._hdf5_compression_enabled)
        self.measurement_compression_button.clicked.connect(self._set_measurement_hdf5_compression_enabled)
        self.project_destination_edit.installEventFilter(self)
        self.project_destination_browse_button.clicked.connect(self._open_recording_project_destination_in_explorer)
        self.project_destination_edit.editingFinished.connect(self._remember_recording_project_destination)
        self.experiment_name_edit.editingFinished.connect(self._remember_recording_experiment_name)

        self.measurement_toggle_button = self._make_icon_button(
            transport_icon(self._theme_mode, "play"),
            "Start measurement",
        )
        self.stop_measurement_button = self._make_icon_button(
            transport_icon(self._theme_mode, "stop"),
            "Stop measurement",
        )
        self.stop_measurement_button.setObjectName("dangerIconButton")
        self.next_measurement_button = self._make_icon_button(
            transport_icon(self._theme_mode, "next"),
            "Next",
        )
        self.measurement_toggle_button.clicked.connect(self._toggle_measurement_run)
        self.stop_measurement_button.clicked.connect(self._stop_measurement_run)

        self.integration_spin = QDoubleSpinBox(self)
        make_compact_spinbox(self.integration_spin)
        self.integration_spin.setRange(1.0, 10000.0)
        self.integration_spin.setValue(50.0)
        self.integration_spin.setSuffix(" ms")
        self.integration_spin.setDecimals(3)
        self.integration_spin.setSingleStep(1.0)
        self.integration_spin.setStepType(QAbstractSpinBox.StepType.AdaptiveDecimalStepType)

        self.averages_spin = QSpinBox(self)
        make_compact_spinbox(self.averages_spin)
        self.averages_spin.setRange(1, 1000)
        self.averages_spin.setValue(3)

        self.auto_integration_button = QPushButton("Auto", self)
        self.auto_integration_button.setToolTip("Automatically set integration time.")
        self.auto_integration_button.clicked.connect(self._auto_set_integration_time)

        self.correct_dark_check = QCheckBox("Elec. dark correction", self)
        self.correct_dark_check.setChecked(True)
        self.correct_nonlinearity_check = QCheckBox("Nonlinearity correction", self)
        self.correct_nonlinearity_check.setChecked(True)

        self.acquire_dark_button = self._make_frameless_icon_button(
            dark_icon(False),
            "Acquire dark spectrum",
            size=30,
        )
        self.acquire_reference_button = self._make_frameless_icon_button(
            reference_icon(False),
            "Acquire reference spectrum",
            size=30,
        )

        self.acquire_dark_button.clicked.connect(lambda: self._request_manual_acquisition("dark"))
        self.acquire_reference_button.clicked.connect(lambda: self._request_manual_acquisition("reference"))

        self.live_rate_spin = InlineWheelDoubleLabel(4.0, self)
        self.live_rate_spin.setObjectName("liveRateLabel")
        self.live_rate_spin.setRange(0.1, 200.0)
        self.live_rate_spin.setValue(4.0)
        self.live_rate_spin.setDecimals(2)
        self.live_rate_spin.setSuffix(" Hz")
        self.live_rate_spin.setSingleStep(0.1)
        self.live_rate_spin.setToolTip(
            "GUI display refresh rate for live spectra and sensorgram updates. "
            "Lower values skip more display frames and reduce GUI work."
        )

        self.sim_output_rate_spin = QDoubleSpinBox(self)
        make_compact_spinbox(self.sim_output_rate_spin)
        self.sim_output_rate_spin.setRange(0.1, 200.0)
        self.sim_output_rate_spin.setValue(4.0)
        self.sim_output_rate_spin.setDecimals(2)
        self.sim_output_rate_spin.setSuffix(" Hz")
        self.sim_output_rate_spin.setToolTip(
            "Frame production rate of the synthetic simulation backend. "
            "This does not affect spectrometer hardware."
        )

        self.live_estimate = ElidingLabel()
        self.live_estimate.setWordWrap(False)
        self.live_estimate.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.live_estimate.setToolTip(
            "src = source acquisition rate; disp = GUI display rate; proc = processing time per spectrum; "
            "head = display-period / processing-time; skip = dropped GUI updates per second."
        )
        self.spectrometer_stats_label = QLabel(self)
        self.spectrometer_stats_label.setWordWrap(False)
        self.spectrometer_stats_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.spectrometer_stats_label.setText("spacing - | rate - | ovh -")
        self.spectrometer_stats_label.setToolTip(
            "Spectrometer acquisition stats: last frame spacing, effective source rate, and acquisition overhead."
        )
        self.telemetry_label = ElidingLabel()
        self.telemetry_label.setWordWrap(False)
        self.telemetry_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.telemetry_label.setText("gap - | acq - | proc - | plot - | ui - | idle - | ovh - | show -")
        self.telemetry_label.setToolTip(
            "gap = time between completed source frames; acq = acquisition latency; proc = worker compute + queue wait; "
            "plot = spectrum redraw time; ui = deferred GUI flush; idle = unclassified remainder; ovh = acquisition latency minus expected budget; "
            "% values are shares of the current frame gap when available; show = display-window summary."
        )
        footer_font = QFont("Consolas", 9)
        self.status_label.setFont(footer_font)
        self.live_estimate.setFont(footer_font)
        self.spectrometer_stats_label.setFont(footer_font)
        self.telemetry_label.setFont(footer_font)
        self.spectrum_stats_label = QLabel("peak: - | centroid: - | FWHM: - | MSE: - | R: - | S/N: -", self)
        self.spectrum_stats_label.setWordWrap(True)
        self.spectrum_stats_label.setToolTip(
            "Spectrum stats: peak position, centroid, FWHM, fit error (MSE), fit quality (R), and signal-to-noise."
        )
        self.spectrum_cursor_label = QLabel("cursor: -", self)
        self.spectrum_cursor_label.setToolTip("Spectrum cursor readout under the mouse pointer.")
        self.trace_stats_label = QLabel("latest: - | min/max: - | span: - | dt -", self)
        self.trace_stats_label.setWordWrap(False)
        self.trace_stats_label.setTextFormat(Qt.TextFormat.RichText)
        self.trace_stats_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.trace_stats_label.setToolTip(
            "Metric stats: latest value, min/max, span, and average time step. Click the metric name to cycle the displayed metric."
        )
        self.trace_stats_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.trace_stats_label.installEventFilter(self)
        self.trace_cursor_label = QLabel("cursor: -", self)
        self.trace_cursor_label.setToolTip("Metric cursor readout under the mouse pointer.")
        self.trace_noise_window_spin = InlineWheelDoubleLabel(10.0, self)
        self.trace_noise_window_spin.setObjectName("traceNoiseWindowLabel")
        self.trace_noise_window_spin.setRange(0.5, 600.0)
        self.trace_noise_window_spin.setDecimals(1)
        self.trace_noise_window_spin.setSingleStep(0.5)
        self.trace_noise_window_spin.setSuffix(" s")
        self.trace_noise_window_spin.setToolTip(
            "Noise window in seconds for the sensorgram trace. Click to focus, then use the mouse wheel or arrow keys."
        )
        self.trace_noise_summary_label = QLabel("noise: -", self)
        self.trace_noise_summary_label.setWordWrap(False)
        self.trace_noise_summary_label.setTextFormat(Qt.TextFormat.RichText)
        self.trace_noise_summary_label.setToolTip("Noise estimate for each trace metric over the selected window.")
        self.show_residual_button = self._make_frameless_icon_button(
            residual_icon(False),
            "Show or hide the fit residual on the right axis.",
            size=30,
        )
        self.show_residual_button.setCheckable(True)
        self.show_residual_button.toggled.connect(self._handle_residual_toggle)
        self.freeze_plots_button = self._make_frameless_icon_button(
            snowflake_icon(self._theme_mode, False),
            "Freeze the plots so they stop updating while acquisition continues.",
            size=30,
        )
        self.freeze_plots_button.setCheckable(True)
        self.freeze_plots_button.toggled.connect(self._set_plots_frozen)
        self.sensorgram_freeze_button = self._make_frameless_icon_button(
            snowflake_icon(self._theme_mode, False),
            "Freeze only the sensorgram trace and heatmap so you can inspect it while acquisition continues.",
            size=30,
        )
        self.sensorgram_freeze_button.setCheckable(True)
        self.sensorgram_freeze_button.toggled.connect(self._set_sensorgram_frozen)
        self._update_sensorgram_freeze_button_icon()
        self.clear_trace_button = QToolButton(self)
        self.clear_trace_button.setObjectName("flowStepActionButton")
        self.clear_trace_button.setAutoRaise(True)
        self.clear_trace_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.clear_trace_button.setFixedSize(32, 32)
        self.clear_trace_button.setIconSize(QSize(24, 24))
        self.clear_trace_button.setIcon(
            tint_tabler_icon(flow_tabler_icon("trash"), QColor("#b44a4a"))
        )
        self.clear_trace_button.setToolTip("Clear the sensorgram metric history.")
        self.clear_trace_button.setStyleSheet(
            "QToolButton#flowStepActionButton { background: transparent; border: none; padding: 0px; margin: 0px; }"
            "QToolButton#flowStepActionButton:hover { background: rgba(127, 127, 127, 0.10); border: none; }"
            "QToolButton#flowStepActionButton:pressed { background: rgba(127, 127, 127, 0.18); border: none; }"
        )
        self.clear_trace_button.clicked.connect(self._clear_trace_history)
        self.sensorgram_reload_button = self._make_frameless_icon_button(
            reload_icon(),
            "Reload sensorgram history from the temp measurement file into the live display cache.",
            size=30,
        )
        self._sensorgram_reload_button_base_icon = reload_icon()
        self._sensorgram_reload_button_spinner_angle = 0
        self._sensorgram_reload_button_spinner_timer = QTimer(self)
        self._sensorgram_reload_button_spinner_timer.setInterval(80)
        self._sensorgram_reload_button_spinner_timer.timeout.connect(self._advance_sensorgram_reload_spinner)
        self.sensorgram_reload_button.clicked.connect(self._reload_sensorgram_history)
        self.trace_record_button = self._make_icon_button(
            transport_icon(self._theme_mode, "record"),
            "Start recording sensorgram data",
        )
        self.trace_record_button.clicked.connect(self._toggle_measurement_run)
        self.autoscale_spectrum_button = QPushButton("Auto spectrum", self)
        self.autoscale_spectrum_button.setObjectName("toolbarButton")
        self.autoscale_spectrum_button.clicked.connect(self._autoscale_spectrum_plot)
        self.autoscale_trace_button = QPushButton("Auto metric", self)
        self.autoscale_trace_button.setObjectName("toolbarButton")
        self.autoscale_trace_button.clicked.connect(self._autoscale_trace_plot)

        self.range_min_spin = QSpinBox()
        make_compact_spinbox(self.range_min_spin)
        self.range_min_spin.setRange(0, 5000)
        self.range_min_spin.setToolTip("Minimum wavelength used for processing and fit range.")
        self.range_max_spin = QSpinBox()
        make_compact_spinbox(self.range_max_spin)
        self.range_max_spin.setRange(0, 5000)
        self.range_max_spin.setToolTip("Maximum wavelength used for processing and fit range.")

        self.smoothing_method_combo = QComboBox(self)
        self.smoothing_method_combo.addItem("None", "none")
        self.smoothing_method_combo.addItem("Avg", "moving_average")
        self.smoothing_method_combo.addItem("SG", "savitzky_golay")
        self.smoothing_method_combo.setToolTip("Method used to smooth the spectrum before analysis.")
        self.baseline_method_combo = QComboBox(self)
        self.baseline_method_combo.addItems(["none", "linear"])
        self.baseline_method_combo.setToolTip("Method used to estimate and subtract the baseline.")
        self.smoothing_window_spin = QSpinBox()
        make_compact_spinbox(self.smoothing_window_spin)
        self.smoothing_window_spin.setRange(1, 2147483647)
        self.smoothing_window_spin.setSingleStep(2)
        self.smoothing_window_spin.setToolTip("Smoothing window in data points. Larger values smooth more strongly.")
        self.temporal_smoothing_spin = QSpinBox()
        make_compact_spinbox(self.temporal_smoothing_spin)
        self.temporal_smoothing_spin.setRange(1, 64)
        self.temporal_smoothing_spin.setValue(1)
        self.temporal_smoothing_spin.setToolTip(
            "Average the last N displayed processed spectra before fit and peak extraction. 1 keeps no temporal accumulation."
        )
        self.crop_method_combo = QComboBox(self)
        self.crop_method_combo.addItem("Range", "fixed_width")
        self.crop_method_combo.addItem("Thresh", "threshold")
        self.crop_method_combo.setToolTip(
            "Choose how the fit region is cropped around the detected peak. The parameter control below switches between width and fraction."
        )
        self.crop_fraction_spin = QDoubleSpinBox()
        make_compact_spinbox(self.crop_fraction_spin)
        self.crop_fraction_spin.setRange(0.05, 0.95)
        self.crop_fraction_spin.setDecimals(2)
        self.crop_fraction_spin.setSingleStep(0.05)
        self.crop_fraction_spin.setValue(0.70)
        self.crop_fraction_spin.setToolTip(
            "Threshold fraction of peak height used when the crop method is set to threshold."
        )

        self.fit_method_combo = QComboBox(self)
        self.fit_method_combo.addItem("none", "none")
        self.fit_method_combo.addItem("poly", "poly")
        self.fit_method_combo.addItem("Gauss", "gaussian")
        self.fit_method_combo.setToolTip("Choose the fit model used to estimate the peak position.")
        self.poly_order_spin = QSpinBox()
        make_compact_spinbox(self.poly_order_spin)
        self.poly_order_spin.setRange(1, 999)
        self.poly_order_spin.setToolTip("Polynomial order used when polynomial fit is selected.")
        self._poly_order_default_tooltip = self.poly_order_spin.toolTip()
        self.poly_warning_label = QLabel("!")
        self.poly_warning_label.setStyleSheet("color: #c62828; font-size: 14px; font-weight: 700;")
        self.poly_warning_label.setToolTip("Polynomial order warning.")
        self.poly_warning_label.hide()
        self.fit_window_spin = QSpinBox()
        make_compact_spinbox(self.fit_window_spin)
        self.fit_window_spin.setRange(0, 5000)
        self.fit_window_spin.setValue(120)
        self.fit_window_spin.setSuffix(" nm")
        self.fit_window_spin.setToolTip(
            "Fit range width in nm around the detected local peak maximum. Used when the crop method is set to fixed_width."
        )
        self.crop_parameter_label = QLabel("Range", self)
        self.crop_parameter_label.setToolTip(
            "Fit-range width in nm when the crop method is fixed_width, or threshold fraction when the crop method is threshold."
        )
        self.crop_parameter_label.hide()
        self.crop_parameter_stack = QStackedWidget()
        self.crop_parameter_stack.addWidget(self.fit_window_spin)
        self.crop_parameter_stack.addWidget(self.crop_fraction_spin)
        self.analysis_resolution_spin = QComboBox(self)
        populate_analysis_resolution_combo(self.analysis_resolution_spin)
        self.analysis_resolution_spin.setCurrentIndex(2)
        self.analysis_resolution_spin.setEditable(False)
        self.analysis_resolution_spin.setToolTip(
            "Resolution used for peak and centroid analysis. Lower values improve sub-sample tracking but cost more CPU."
        )
        self.metric_mode_combo = QComboBox(self)
        self.metric_mode_combo.addItems(["smoothed_max", "poly_max", "gaussian_center", "centroid"])
        self.metric_mode_combo.setToolTip("Choose which spectrum tracking metric is shown in the metric plot and summary.")
        self.trace_max_check = QCheckBox("Max", self)
        self.trace_centroid_check = QCheckBox("Centroid", self)
        self.trace_poly_check = QCheckBox("Poly", self)
        self.trace_gaussian_check = QCheckBox("Gauss", self)
        self.trace_max_check.setObjectName("traceMaxCheck")
        self.trace_centroid_check.setObjectName("traceCentroidCheck")
        self.trace_poly_check.setObjectName("tracePolyCheck")
        self.trace_gaussian_check.setObjectName("traceGaussianCheck")
        self.trace_max_check.setToolTip("Show the smoothed maximum trace metric.")
        self.trace_centroid_check.setToolTip("Show the centroid trace metric.")
        self.trace_poly_check.setToolTip("Show the polynomial-fit peak trace metric.")
        self.trace_gaussian_check.setToolTip("Show the Gaussian-fit peak trace metric.")

        self.save_processing_button = self._make_frameless_icon_button(
            storage_save_icon(),
            "Save the current processing configuration to an HDF5 file.",
            size=24,
        )
        self.load_processing_button = self._make_frameless_icon_button(
            tint_tabler_icon(flow_tabler_icon("file_import"), QColor("#4f88ff")),
            "Load processing settings from a compatible HDF5 file.",
            size=24,
        )
        self.save_processing_button.clicked.connect(self._save_processing_settings_dialog)
        self.load_processing_button.clicked.connect(self._load_processing_settings_dialog)

        self.sim_peak_center_slider = make_sim_slider(450, 850, 620)
        self.sim_peak_width_slider = make_sim_slider(10, 120, 35)
        self.sim_peak_height_slider = make_sim_slider(-5000, 5000, 1800)
        self.sim_secondary_peak_offset_slider = make_sim_slider(-300, 300, 130)
        self.sim_secondary_peak_height_slider = make_sim_slider(0, 400, 18)
        self.sim_secondary_peak_width_slider = make_sim_slider(10, 400, 180)
        self.sim_baseline_slider = make_sim_slider(0, 4000, 900)
        self.sim_slope_slider = make_sim_slider(-100, 100, 12)
        self.sim_noise_slider = make_sim_slider(0, 250, 40)
        self.sim_resolution_spin = QDoubleSpinBox()
        make_compact_spinbox(self.sim_resolution_spin)
        self.sim_resolution_spin.setRange(0.01, 10.0)
        self.sim_resolution_spin.setDecimals(3)
        self.sim_resolution_spin.setSingleStep(0.01)
        self.sim_resolution_spin.setSuffix(" nm")
        self.sim_resolution_spin.setValue(self._default_simulation_resolution_nm())
        self.sim_peak_center_value = QLabel(self)
        self.sim_peak_width_value = QLabel(self)
        self.sim_peak_height_value = QLabel(self)
        self.sim_secondary_peak_offset_value = QLabel(self)
        self.sim_secondary_peak_height_value = QLabel(self)
        self.sim_secondary_peak_width_value = QLabel(self)
        self.sim_baseline_value = QLabel(self)
        self.sim_slope_value = QLabel(self)
        self.sim_noise_value = QLabel(self)

        self.plot_selector = QComboBox(self)
        self.plot_selector.addItems(self.PLOT_MODES.keys())
        self.plot_selector.setCurrentText("Sample")
        self.plot_selector.currentTextChanged.connect(self._refresh_plot)

        self.source_tabs = QTabWidget(self)
        _startup_mark("building source tabs")
        self.source_tabs.addTab(self._build_spectrometer_page(), "Spectrometer")
        self.source_tabs.addTab(self._build_simulation_page(), "Simulation")
        self.source_tabs.setDocumentMode(True)
        tab_bar = self.source_tabs.tabBar()
        tab_bar.setExpanding(True)
        tab_bar.setUsesScrollButtons(False)
        tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
        self.source_tabs.setIconSize(QSize(16, 16))
        self.source_tabs.currentChanged.connect(self._handle_source_tab_changed)
        install_source_link_buttons(self)
        install_source_tab_headers(self)
        self._configure_source_tabs()
        _startup_mark("source tabs configured")

        self.trace_time_axis = FlexibleTimeAxis("bottom")
        self.trace_time_axis._diagnostics_owner = self
        self.trace_time_axis._diagnostics_prefix = "trace_time_axis"
        self.trace_time_axis.doubleClicked.connect(self._toggle_sensorgram_time_axis_mode)
        self._apply_sensorgram_time_axis_mode(redraw=False)
        _startup_mark("building spectrum and metric plots")
        self.spectrum_left_axis = ScientificAxis("left")
        self.spectrum_left_axis._diagnostics_owner = self
        self.spectrum_left_axis._diagnostics_prefix = "spectrum_left_axis"
        self.spectrum_plot = TimedPlotWidget(axisItems={"left": self.spectrum_left_axis}, background="w", paint_owner=self, paint_prefix="spectrum_plot")
        if hasattr(self.spectrum_plot, "setMenuEnabled"):
            self.spectrum_plot.setMenuEnabled(False)
        self.spectrum_plot.showGrid(x=True, y=True, alpha=0.18)
        self.spectrum_plot.setLabel("bottom", "Wavelength (nm)")
        self.spectrum_plot.setMouseEnabled(x=True, y=True)
        self.spectrum_plot.getPlotItem().vb.setDefaultPadding(0.0)
        line_width = max(float(getattr(self, "_sensorgram_line_width_px", 2.2)), 0.5)
        self.spectrum_curve = self.spectrum_plot.plot(pen=pg.mkPen("#1f77b4", width=line_width))
        self.fit_curve = self.spectrum_plot.plot(pen=pg.mkPen("#d95f02", width=line_width, style=Qt.PenStyle.DashLine))
        for item in (self.spectrum_curve, self.fit_curve):
            item._diagnostics_owner = self
            item._diagnostics_prefix = "spectrum_plot"
        spectrum_plot_item = self.spectrum_plot.getPlotItem()
        spectrum_plot_item.showAxis("right")
        self.residual_axis = spectrum_plot_item.getAxis("right")
        self.residual_axis.setLabel("Residual (%)")
        self.residual_axis.enableAutoSIPrefix(False)
        self.residual_axis.setTextPen(pg.mkPen("#7a7a7a"))
        self.residual_axis.setPen(pg.mkPen("#7a7a7a"))
        self.residual_view = ResidualViewBox(self)
        self.residual_view._manual_y_zoom = False
        self._residual_axis_autoscaled = False
        self._residual_y_range: list[float] | None = None
        spectrum_plot_item.scene().addItem(self.residual_view)
        self.residual_zero_line = pg.InfiniteLine(
            angle=0,
            pos=0,
            pen=pg.mkPen("#9aa3ad", width=1.0, style=Qt.PenStyle.DashLine),
        )
        self.residual_zero_line.setZValue(-10)
        self.residual_view.addItem(self.residual_zero_line)
        self.residual_curve = pg.PlotDataItem(pen=pg.mkPen("#888888", width=1))
        self.residual_curve._diagnostics_owner = self
        self.residual_curve._diagnostics_prefix = "spectrum_plot"
        self.residual_view.addItem(self.residual_curve)
        self._residual_segment_items: list[pg.PlotCurveItem] = []
        spectrum_plot_item.getAxis("right").linkToView(self.residual_view)
        self.residual_view.setXLink(spectrum_plot_item.vb)
        self.residual_view.setMouseEnabled(x=False, y=False)
        spectrum_plot_item.vb.sigResized.connect(self._update_residual_view_geometry)
        self._update_residual_view_geometry()
        self._update_residual_axis_visibility(False)
        for curve in (self.spectrum_curve, self.fit_curve):
            curve.setClipToView(True)
            curve.setDownsampling(auto=False, ds=1)
            curve.setSkipFiniteCheck(True)
        self.residual_curve.setClipToView(True)
        self.residual_curve.setDownsampling(auto=False, ds=1)
        self.residual_curve.setSkipFiniteCheck(True)
        self.residual_curve.setPen(pg.mkPen("#8a8a8a", width=1.0))
        self.residual_curve.setSymbol(None)
        self.processing_region_item = pg.LinearRegionItem(values=(0, 1), movable=False, brush=pg.mkBrush(90, 160, 255, 30), pen=pg.mkPen(None))
        self.fit_region_item = pg.LinearRegionItem(values=(0, 1), movable=False, brush=pg.mkBrush(255, 180, 80, 40), pen=pg.mkPen(None))
        self.spectrum_plot.addItem(self.processing_region_item)
        self.spectrum_plot.addItem(self.fit_region_item)
        self.processing_region_item.hide()
        self.fit_region_item.hide()
        self.max_marker = pg.ScatterPlotItem(
            size=12,
            symbol="o",
            brush=pg.mkBrush(self.TRACE_METRIC_COLORS["smoothed_max"]),
            pen=pg.mkPen("w", width=1.4),
        )
        self.poly_marker = pg.ScatterPlotItem(
            size=13,
            symbol="star",
            brush=pg.mkBrush(self.TRACE_METRIC_COLORS["poly_max"]),
            pen=pg.mkPen("w", width=1.4),
        )
        self.gaussian_marker = pg.ScatterPlotItem(
            size=12,
            symbol="d",
            brush=pg.mkBrush(self.TRACE_METRIC_COLORS["gaussian_center"]),
            pen=pg.mkPen("w", width=1.4),
        )
        self.centroid_marker = pg.ScatterPlotItem(
            size=14,
            symbol="t",
            brush=pg.mkBrush(self.TRACE_METRIC_COLORS["centroid"]),
            pen=pg.mkPen("w", width=1.4),
        )
        self.spectrum_plot.addItem(self.max_marker)
        self.spectrum_plot.addItem(self.poly_marker)
        self.spectrum_plot.addItem(self.gaussian_marker)
        self.spectrum_plot.addItem(self.centroid_marker)
        self.spectrum_vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#666666", width=1))
        self.spectrum_hline = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#666666", width=1))
        self.spectrum_plot.addItem(self.spectrum_vline, ignoreBounds=True)
        self.spectrum_plot.addItem(self.spectrum_hline, ignoreBounds=True)
        self.spectrum_proxy = pg.SignalProxy(
            self.spectrum_plot.scene().sigMouseMoved,
            rateLimit=180,
            slot=self._handle_spectrum_mouse_moved,
        )
        self.spectrum_plot.viewport().installEventFilter(self)

        self.trace_left_axis = ScientificAxis("left")
        self.trace_left_axis._diagnostics_owner = self
        self.trace_left_axis._diagnostics_prefix = "trace_left_axis"
        self.trace_plot = TimedPlotWidget(
            axisItems={"bottom": self.trace_time_axis, "left": self.trace_left_axis},
            background="w",
            paint_owner=self,
            paint_prefix="trace_plot",
        )
        if hasattr(self.trace_plot, "setMenuEnabled"):
            self.trace_plot.setMenuEnabled(True)
        self.trace_plot.showGrid(x=True, y=True, alpha=0.18)
        self.trace_plot.setMouseEnabled(x=True, y=True)
        self.trace_curves = {
            metric_name: self.trace_plot.plot(
                pen=pg.mkPen(self.TRACE_METRIC_COLORS[metric_name], width=line_width),
                name=self.TRACE_METRIC_LABELS[metric_name],
            )
            for metric_name in self.TRACE_METRIC_LABELS
        }
        for curve in self.trace_curves.values():
            curve._diagnostics_owner = self
            curve._diagnostics_prefix = "trace_plot"
            curve.setClipToView(True)
            curve.setDownsampling(auto=False, ds=1)
            curve.setSkipFiniteCheck(True)
        self.trace_metric_envelope_bands = {}
        self.trace_metric_envelope_curves = {}
        for metric_name in self.TRACE_METRIC_LABELS:
            min_curve = pg.PlotDataItem()
            max_curve = pg.PlotDataItem()
            band = pg.FillBetweenItem(min_curve, max_curve)
            for curve in (min_curve, max_curve):
                curve._diagnostics_owner = self
                curve._diagnostics_prefix = "trace_plot"
                curve.setClipToView(True)
                curve.setDownsampling(auto=False, ds=1)
                curve.setSkipFiniteCheck(True)
                curve.setVisible(False)
                self.trace_plot.addItem(curve)
            band._diagnostics_owner = self
            band._diagnostics_prefix = "trace_plot"
            band.setVisible(False)
            band.setZValue(-2)
            self.trace_plot.addItem(band)
            self.trace_metric_envelope_bands[metric_name] = band
            self.trace_metric_envelope_curves[metric_name] = (min_curve, max_curve)
        self.trace_heatmap_image = pg.ImageItem(axisOrder="row-major")
        self.trace_heatmap_image._diagnostics_owner = self
        self.trace_heatmap_image._diagnostics_prefix = "trace_plot"
        self.trace_heatmap_image.setVisible(False)
        self.trace_heatmap_image.setZValue(-10)
        self.trace_heatmap_image.setAutoDownsample(True)
        self.trace_heatmap_image.setLookupTable(self._sensorgram_heatmap_lookup_table())
        self.trace_plot.addItem(self.trace_heatmap_image, ignoreBounds=True)
        self.trace_heatmap_notice_item = QLabel(self.trace_plot.viewport())
        self.trace_heatmap_notice_item._diagnostics_owner = self
        self.trace_heatmap_notice_item._diagnostics_prefix = "trace_plot"
        self.trace_heatmap_notice_item.setObjectName("traceHeatmapNotice")
        self.trace_heatmap_notice_item.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.trace_heatmap_notice_item.setWordWrap(True)
        self.trace_heatmap_notice_item.setTextFormat(Qt.TextFormat.PlainText)
        self.trace_heatmap_notice_item.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.trace_heatmap_notice_item.setStyleSheet(
            "#traceHeatmapNotice {"
            "  background-color: rgba(18, 24, 31, 210);"
            "  border: 1px solid rgba(120, 130, 145, 160);"
            "  border-radius: 8px;"
            "  color: #d8dee9;"
            "  padding: 12px 16px;"
            "  font-size: 12pt;"
            "  font-weight: 600;"
            "}"
        )
        self.trace_heatmap_notice_item.setMaximumWidth(340)
        self.trace_heatmap_notice_item.setVisible(False)
        self.trace_plot.viewport().installEventFilter(self)
        self.trace_legend = self.trace_plot.addLegend(offset=(10, 10))
        self.trace_legend._diagnostics_owner = self
        self.trace_legend._diagnostics_prefix = "trace_plot"
        self.trace_vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#666666", width=1))
        self.trace_hline = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#666666", width=1))
        self.trace_vline._diagnostics_owner = self
        self.trace_vline._diagnostics_prefix = "trace_plot"
        self.trace_hline._diagnostics_owner = self
        self.trace_hline._diagnostics_prefix = "trace_plot"
        self.trace_plot.addItem(self.trace_vline, ignoreBounds=True)
        self.trace_plot.addItem(self.trace_hline, ignoreBounds=True)
        self.trace_proxy = pg.SignalProxy(
            self.trace_plot.scene().sigMouseMoved,
            rateLimit=180,
            slot=self._handle_trace_mouse_moved,
        )
        self.trace_plot.getPlotItem().vb.sigRangeChanged.connect(self._handle_trace_view_range_changed)
        self.trace_plot.getPlotItem().vb.sigResized.connect(self._sync_sensorgram_control_step_overlay)
        self._style_plot_widgets()
        self._apply_sensorgram_display_style()
        self._sync_trace_heatmap_notice_overlay()
        _startup_mark("plot widgets styled")

        self.session_summary = QTextEdit()
        self.session_summary.setObjectName("sessionSettingsText")
        self.session_summary.setReadOnly(True)
        self.session_summary.setAcceptRichText(True)
        self.session_summary.setUndoRedoEnabled(False)
        self.session_summary.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._apply_text_widget_font_size(self.session_summary, 8.0, minimum=7.0, maximum=16.0)
        self.session_summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.session_summary.setMinimumHeight(150)
        self.session_summary.setToolTip(
            "Current session settings and live performance statistics. Scroll without the view jumping back to the top."
        )
        self.session_settings_text = self.session_summary
        self.session_statistics_text = self.session_summary

        self.session_stats_snapshot_button = self._make_frameless_icon_button(
            tint_tabler_icon(flow_tabler_icon("camera"), QColor("#5fa8ff")),
            "Copy the current session panel snapshot to the clipboard.",
            size=24,
        )
        self.session_stats_snapshot_button.clicked.connect(self._copy_session_stats_snapshot)
        self.session_stats_save_button = self._make_frameless_icon_button(
            storage_save_icon(),
            "Save the current session statistics to the experiment folder.",
            size=24,
        )
        self.session_stats_save_button.clicked.connect(self._save_session_stats_log)
        self.session_stats_record_button = self._make_frameless_icon_button(
            transport_icon(self._theme_mode, "record"),
            "Start session log recording.",
            size=24,
        )
        self.session_stats_record_button.setCheckable(True)
        self.session_stats_record_button.setChecked(False)
        self.session_stats_record_button.clicked.connect(self._toggle_session_stats_recording)
        self.session_font_down_button = self._make_frameless_icon_button(
            tint_tabler_icon(flow_tabler_icon("minus"), QColor("#e6ebf1")),
            "Decrease session panel font size.",
            size=24,
        )
        self.session_font_down_button.clicked.connect(self._decrease_session_summary_font_size)
        self.session_font_up_button = self._make_frameless_icon_button(
            tint_tabler_icon(flow_tabler_icon("plus"), QColor("#8fbaff")),
            "Increase session panel font size.",
            size=24,
        )
        self.session_font_up_button.clicked.connect(self._increase_session_summary_font_size)

        self._apply_control_sizing()
        _startup_mark("control sizing applied")
        self._build_layout()
        _startup_mark("main layout built")
        self._build_menu_bar()
        _startup_mark("menu bar built")
        self._sync_view_actions()
        self._flow_panel_bootstrap_pending = False
        self._experiment_control_panel_bootstrap_pending = False
        _startup_mark("experiment control panel bootstrap deferred")
        self.log_clear_button.clicked.connect(self._clear_log_terminal)
        self.log_copy_button.clicked.connect(self._copy_log_terminal)
        self.log_follow_button.toggled.connect(self._set_log_following)
        self._apply_launch_profile_layout()
        self._restore_ui_state()
        _startup_mark("UI state restored")
        if not self._ui_state:
            self._fit_window_to_available_screen()
        self._apply_processing_settings_to_widgets(self._processing_settings)
        self._apply_acquisition_state_to_widgets(self._acquisition_state)
        _startup_mark("saved settings applied")
        self._connect_processing_widgets()
        self._connect_simulation_widgets()
        _startup_mark("widget signals connected")
        self._update_simulation_labels()
        self._sync_simulation_backend_from_controls()
        self.plot_selector.currentTextChanged.connect(lambda _text: self._schedule_acquisition_state_persist())
        self.live_rate_spin.valueChanged.connect(self._handle_live_setting_change)
        self.sim_output_rate_spin.valueChanged.connect(self._handle_simulation_output_rate_change)
        self.integration_spin.valueChanged.connect(self._handle_live_setting_change)
        self.averages_spin.valueChanged.connect(self._handle_live_setting_change)
        self.correct_dark_check.toggled.connect(self._handle_live_setting_change)
        self.correct_nonlinearity_check.toggled.connect(self._handle_live_setting_change)
        self.show_residual_button.toggled.connect(lambda _checked: self._schedule_acquisition_state_persist())
        self.freeze_plots_button.toggled.connect(lambda _checked: self._schedule_acquisition_state_persist())
        self.trace_noise_window_spin.valueChanged.connect(self._handle_processing_setting_change)
        self._update_live_estimate()
        self._refresh_spectrometer_stats()
        self._refresh_telemetry()
        self._refresh_session_statistics(force=True)
        self._refresh_session_summary(force=True)
        if bool(self._session_stats_recording_enabled_by_default):
            self._set_session_stats_recording_active(True)
        self._log_info(
            f"Ready | source={self._source_mode} | backend={self._spectrometer.device_name()}"
        )
        if isinstance(self._spectrometer, SimulatedSpectrometer) and self._launch_profile_spec.key != LAUNCH_PROFILE_CONTROL_EDITOR:
            self.source_tabs.blockSignals(True)
            self.source_tabs.setCurrentIndex(1)
            self.source_tabs.blockSignals(False)
            self._apply_source_mode("simulation", restart_live=False)
        else:
            self._refresh_plot()
        self._set_measurement_buttons_enabled(True)
        if bool(self._runtime_drift_probe_enabled):
            initialize_runtime_drift_probe_for_runtime(self)
        self.hardware_init_progress.connect(self._update_startup_loading_indicator)
        self.hardware_init_finished.connect(lambda: self._set_startup_loading_indicator(False))
        if self._launch_profile_spec.scan_devices:
            self.hardware_init_finished.connect(self._start_live_acquisition)
        self._ui_startup_ready = True
        if self._experiment_control_window is not None:
            self._experiment_control_window._ui_startup_ready = True
        if self._experiment_control_window is None:
            self._ensure_experimental_control_panel()
        pending_layout_preset = getattr(self, "_pending_layout_preset_selected", None)
        if pending_layout_preset is not None:
            self._pending_layout_preset_selected = None
            QTimer.singleShot(
                0,
                lambda preset_key=pending_layout_preset: apply_layout_preset(self, preset_key, save=False),
            )
        if getattr(self, "_pending_top_view_mode", None) in {"flow", "experimental_control"}:
            self._pending_top_view_mode = None
            QTimer.singleShot(0, self._activate_experimental_control_view)
        _startup_mark("startup wiring complete")
        if bool(getattr(self, "_startup_show_requested", False)):
            QTimer.singleShot(0, self._complete_startup_show)

    def _initialize_logging_ui(self) -> None:
        initialize_logging_ui_for(self)

    def _build_spectrometer_page(self) -> QWidget:
        return build_spectrometer_page(self)

    def _build_simulation_page(self) -> QWidget:
        return build_simulation_page(self)

    def _build_layout(self) -> None:
        build_main_layout_for(self)

    def _build_recording_context_row(self) -> QWidget:
        return build_recording_context_row_for(self)

    def _build_runtime_drift_lines(self) -> list[str]:
        return build_runtime_drift_lines_for_runtime(self)

    def recording_project_destination(self) -> str:
        return self.project_destination_edit.text().strip()

    def recording_experiment_name(self) -> str:
        return self.experiment_name_edit.text().strip()

    def _set_recording_context_controls_enabled(self, enabled: bool) -> None:
        self.project_destination_edit.setEnabled(enabled)
        self.project_destination_browse_button.setEnabled(enabled)
        self.experiment_name_edit.setEnabled(enabled)

    def _choose_recording_project_destination(self) -> None:
        current = self.recording_project_destination()
        start_dir = current if current else str(Path.cwd())
        selected = QFileDialog.getExistingDirectory(self, "Project's destination", start_dir)
        if not selected:
            return
        self.project_destination_edit.setText(selected)
        self._remember_recording_project_destination()

    def _open_recording_project_destination_in_explorer(self) -> None:
        current = self.recording_project_destination()
        folder = Path(current).expanduser() if current else Path.cwd()
        if not folder.exists():
            folder = folder.parent if folder.parent.exists() else Path.cwd()
        if folder.is_file():
            folder = folder.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def _remember_recording_project_destination(self) -> None:
        save_app_setting("recording_project_destination", self.recording_project_destination())

    def _remember_recording_experiment_name(self) -> None:
        save_app_setting("recording_experiment_name", self.recording_experiment_name())

    def measurement_hdf5_compression_enabled(self) -> bool:
        return bool(self._hdf5_compression_enabled)

    def _set_measurement_hdf5_compression_enabled(self, enabled: bool) -> None:
        self._hdf5_compression_enabled = bool(enabled)
        save_app_setting("measurement_hdf5_compression_enabled", self._hdf5_compression_enabled)
        if hasattr(self, "measurement_compression_button"):
            self.measurement_compression_button.blockSignals(True)
            self.measurement_compression_button.setChecked(self._hdf5_compression_enabled)
            self.measurement_compression_button.blockSignals(False)
            if not bool(getattr(self, "_measurement_compression_task", None)):
                self.measurement_compression_button.setIcon(
                    storage_compression_icon(self._hdf5_compression_enabled)
                )
        state_text = "enabled" if self._hdf5_compression_enabled else "disabled"
        self._log_info(f"HDF5 compression {state_text}.")

    def _set_measurement_compression_busy_indicator(self, active: bool) -> None:
        timer = getattr(self, "_measurement_compression_blink_timer", None)
        if timer is None:
            return
        if active:
            if not timer.isActive():
                self._measurement_compression_blink_visible = True
                self._render_measurement_compression_blink_indicator()
                timer.start()
        else:
            timer.stop()
            self._measurement_compression_blink_visible = True
            self._render_measurement_compression_blink_indicator()

    def _advance_measurement_compression_blink_indicator(self) -> None:
        def _callback() -> None:
            timer = getattr(self, "_measurement_compression_blink_timer", None)
            if timer is None or not timer.isActive():
                return
            self._measurement_compression_blink_visible = not bool(getattr(self, "_measurement_compression_blink_visible", True))
            self._render_measurement_compression_blink_indicator()

        self._run_gui_callback_timed("measurement_compression_blink", _callback)

    def _render_measurement_compression_blink_indicator(self) -> None:
        button = getattr(self, "measurement_compression_button", None)
        if button is None:
            return
        if bool(getattr(self, "_measurement_compression_task", None)):
            if bool(getattr(self, "_measurement_compression_blink_visible", True)):
                button.setIcon(storage_compression_icon(self._hdf5_compression_enabled))
            else:
                blank = QIcon()
                button.setIcon(blank)
            button.setToolTip("Compressing finished measurement file...")
            return
        button.setIcon(storage_compression_icon(self._hdf5_compression_enabled))
        button.setToolTip("Toggle post-recording gzip compression for the finished HDF5 measurement file.")

    def _set_status_indicator(
        self,
        dot: QLabel | None,
        text_label: QLabel | None,
        *,
        online: bool,
        online_text: str,
        offline_text: str,
    ) -> None:
        color = "#2e7d32" if online else "#8a98a8"
        if dot is not None:
            dot.setStyleSheet(
                f"background: {color}; border-radius: 5px; border: 1px solid {color};"
            )
        if text_label is not None:
            text_label.setText(online_text if online else offline_text)

    def _configure_source_tabs(self) -> None:
        tab_bar = self.source_tabs.tabBar()
        self.source_tabs.setTabEnabled(0, True)
        tab_bar.setTabToolTip(0, "Ocean spectrometer control" if self._hardware_available else "Spectrometer not connected")
        tab_bar.setTabToolTip(1, "Simulation mode")
        self.source_tabs.setTabText(0, "")
        self.source_tabs.setTabText(1, "")
        self.source_tabs.setTabIcon(0, QIcon())
        self.source_tabs.setTabIcon(1, QIcon())
        update_source_link_buttons(self)
        update_source_tab_headers(self)
        self._update_pump_status()
        self._update_simulation_controls_enabled()

    def _update_pump_status(self, probe: "PumpProbe | None" = None) -> None:
        if probe is not None:
            self._discovered_pump_probe = probe
        refresh_hw_device_status_strip(self)

    def _theme_palette(self) -> dict[str, str]:
        if self._theme_mode == "dark":
            return {
                "bg": "#13161b",
                "fg": "#e6ebf1",
                "muted": "#a8b0ba",
                "panel": "#15191f",
                "field": "#171b21",
                "button": "#20252d",
                "button_hover": "#272d36",
                "button_pressed": "#303640",
                "accent_button": "#5d6876",
                "accent_hover": "#707d8c",
                "checkbox_accent": "#3a86d1",
                "danger_button": "#8f5a61",
                "danger_hover": "#a46a72",
                "border": "#2b3138",
                "border_hover": "#414852",
                "pressed": "#252b33",
                "tab": "#1b2026",
                "tab_selected": "#13161b",
                "accent": "#b2bac4",
                "title": "#b2bac4",
                "scroll": "#49505a",
                "scroll_hover": "#5c6470",
                "splitter": "#2b3138",
                "splitter_hover": "#404854",
                "plot_bg": "#0f1216",
                "plot_border": "#2b3138",
                "axis_text": "#a8b0ba",
                "axis_pen": "#39404a",
                "grid": "#232830",
                "window": "#101318",
            }
        return {
            "bg": "#f4f6f8",
            "fg": "#1d2733",
            "muted": "#243241",
            "panel": "#f4f6f8",
            "field": "#f4f6f8",
            "button": "#eef3f7",
            "button_hover": "#e6edf3",
            "button_pressed": "#dde9f3",
            "accent_button": "#2f80c1",
            "accent_hover": "#3e8dcf",
            "checkbox_accent": "#2f80c1",
            "danger_button": "#d65a63",
            "danger_hover": "#e06a73",
            "border": "#d9e0e7",
            "border_hover": "#9dbbd4",
            "pressed": "#dde9f3",
            "tab": "#e8eef3",
            "tab_selected": "#f4f6f8",
            "accent": "#2f80c1",
            "title": "#2f80c1",
            "scroll": "#bcc9d5",
            "scroll_hover": "#9fb3c5",
            "splitter": "#dde5ec",
            "splitter_hover": "#c7d5e2",
            "plot_bg": "#ffffff",
            "plot_border": "#d9e0e7",
            "axis_text": "#5e7288",
            "axis_pen": "#c8d3dd",
            "grid": "#d8e2ea",
            "window": "#f4f6f8",
        }

    def _apply_modern_style(self) -> None:
        palette = self._theme_palette()
        checkmark_icon = (Path(__file__).resolve().parent / "assets" / "checkmark.svg").as_posix()
        stylesheet = """
            QMainWindow, QWidget {
                background: %(bg)s;
                color: %(fg)s;
                font-size: 12px;
            }
            QToolTip {
                background-color: %(bg)s;
                color: %(fg)s;
                border: 1px solid %(border)s;
                padding: 4px 6px;
            }
            QLabel {
                color: %(muted)s;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QWidget#titleBar {
                background: %(bg)s;
                border-bottom: 1px solid %(border)s;
            }
            QLabel#brandIconLabel {
                background: transparent;
                border: none;
            }
            QLabel#windowModeLabel {
                color: %(fg)s;
                font-weight: 700;
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }
            QLabel#sensorgramHeaderLabel {
                color: %(title)s;
                font-size: 13px;
                font-weight: 800;
                letter-spacing: 0.8px;
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }
            QToolButton#logViewButton {
                color: %(fg)s;
                background: rgba(255, 255, 255, 0.04);
                border: 1px solid %(border)s;
                border-radius: 10px;
                padding: 1px 10px;
                margin: 0px;
                font-size: 11px;
                font-weight: 700;
            }
            QToolButton#logViewButton:hover {
                background: rgba(255, 255, 255, 0.07);
                border-color: %(accent_hover)s;
            }
            QToolButton#logViewButton:checked {
                background: %(accent)s;
                color: %(bg)s;
                border-color: %(accent_hover)s;
            }
            QToolButton#logViewButton:checked:hover {
                background: %(accent_hover)s;
                color: %(bg)s;
                border-color: %(accent_hover)s;
            }
            QToolButton#sensorgramViewModeButton,
            QToolButton#sensorgramContentModeButton,
            QToolButton#sensorgramYAxisModeButton,
            QToolButton#sensorgramWindowButton {
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
                color: #e8d85f;
                font-weight: 600;
            }
            QToolButton#sensorgramViewModeButton:hover,
            QToolButton#sensorgramContentModeButton:hover,
            QToolButton#sensorgramYAxisModeButton:hover,
            QToolButton#sensorgramWindowButton:hover {
                background: transparent;
                border: none;
            }
            QToolButton#sensorgramViewModeButton:pressed,
            QToolButton#sensorgramContentModeButton:pressed,
            QToolButton#sensorgramYAxisModeButton:pressed,
            QToolButton#sensorgramWindowButton:pressed {
                background: transparent;
                border: none;
            }
            QLabel#traceClearLabel {
                color: %(accent)s;
                background: transparent;
                border: none;
                font-size: 11px;
                font-weight: 700;
                padding: 0px 2px;
            }
            QLabel#traceClearLabel:hover {
                color: %(accent_hover)s;
            }
            QToolButton#windowButton {
                background: transparent;
                border: none;
                border-radius: 0px;
                padding: 0px;
                margin: 0px;
                min-width: 0px;
                min-height: 0px;
            }
            QToolButton#windowButton:hover {
                background: %(button_hover)s;
                border: none;
            }
            QToolButton#windowButton:pressed {
                background: %(button_pressed)s;
                border: none;
            }
            QToolButton {
                background: %(button)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                padding: 3px 6px;
            }
            QToolButton:hover, QPushButton:hover {
                background: %(button_hover)s;
                border-color: %(border_hover)s;
            }
            QToolButton:checked {
                background: %(button_pressed)s;
                border-color: %(accent)s;
            }
            QPushButton, QComboBox, QSpinBox, QDoubleSpinBox {
                background: %(field)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                padding: 3px 6px;
                min-width: 0px;
            }
            QSpinBox, QDoubleSpinBox {
                border-radius: 3px;
                padding: 2px 5px;
            }
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 0px;
                border: none;
                background: transparent;
            }
            QSpinBox::up-arrow, QSpinBox::down-arrow,
            QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {
                width: 0px;
                height: 0px;
            }
            QPushButton:pressed {
                background: %(button_pressed)s;
            }
            QPushButton#toolbarButton {
                background: %(button)s;
            }
            QPushButton#toolbarButton:hover {
                background: %(button_hover)s;
            }
            QToolButton#primaryIconButton {
                background: %(accent_button)s;
                border-color: %(accent_button)s;
            }
            QToolButton#primaryIconButton:hover {
                background: %(accent_hover)s;
                border-color: %(accent_hover)s;
            }
            QToolButton#dangerIconButton {
                background: %(danger_button)s;
                border-color: %(danger_button)s;
            }
            QToolButton#dangerIconButton:hover {
                background: %(danger_hover)s;
                border-color: %(danger_hover)s;
            }
            QToolButton#themeToggle {
                min-width: 26px;
                min-height: 26px;
                border-radius: 13px;
                padding: 0px;
                background: %(button)s;
            }
            QComboBox::drop-down {
                border: none;
                width: 16px;
            }
            QLineEdit, QTextEdit {
                background: %(field)s;
                border: 1px solid %(border)s;
                border-radius: 10px;
                padding: 6px;
            }
            QWidget#recordingContextPanel {
                background: transparent;
                border: none;
            }
            QLineEdit#recordingPathEdit, QLineEdit#recordingExperimentEdit {
                background: transparent;
                border: none;
                border-bottom: 1px solid %(border)s;
                border-radius: 0px;
                padding: 0px 2px;
                margin: 0px;
                min-height: 20px;
            }
            QLineEdit#recordingPathEdit:focus, QLineEdit#recordingExperimentEdit:focus {
                border-bottom-color: %(accent)s;
            }
            QToolButton#framelessIconButton {
                min-width: 24px;
                min-height: 24px;
                padding: 0px;
                margin: 0px;
            }
            QMenuBar {
                background: %(bg)s;
                color: %(fg)s;
                border: none;
            }
            QMenuBar::item {
                background: transparent;
                padding: 4px 10px;
                margin: 2px 2px;
                border-radius: 4px;
            }
            QMenuBar::item:selected {
                background: %(button_hover)s;
            }
            QMenuBar::item:pressed {
                background: %(button_pressed)s;
            }
            QMenu {
                background-color: %(field)s;
                color: %(fg)s;
                border: 1px solid %(border)s;
                padding: 4px;
            }
            QMenu::item {
                padding: 4px 20px 4px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: %(button_hover)s;
                color: %(fg)s;
            }
            QMenu::separator {
                height: 1px;
                background: %(border)s;
                margin: 4px 8px;
            }
            QTextEdit {
                color: %(muted)s;
            }
            QTextEdit#logTerminal {
                padding: 4px;
                color: %(fg)s;
                border-radius: 10px;
            }
            QTextEdit#sessionStatisticsText,
            QTextEdit#sessionSettingsText {
                padding: 4px;
                color: %(fg)s;
                border-radius: 10px;
                background: %(field)s;
                border: 1px solid %(border)s;
            }
            QCheckBox {
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid %(border)s;
                background: %(field)s;
            }
            QCheckBox::indicator:checked {
                background: %(checkbox_accent)s;
                border-color: %(checkbox_accent)s;
                image: url(__CHECKMARK_ICON__);
            }
            QCheckBox#traceMaxCheck {
                color: %(trace_max)s;
            }
            QCheckBox#traceMaxCheck::indicator:checked {
                background: %(trace_max)s;
                border-color: %(trace_max)s;
            }
            QCheckBox#traceCentroidCheck {
                color: %(trace_centroid)s;
            }
            QCheckBox#traceCentroidCheck::indicator:checked {
                background: %(trace_centroid)s;
                border-color: %(trace_centroid)s;
            }
            QCheckBox#tracePolyCheck {
                color: %(trace_poly)s;
            }
            QCheckBox#tracePolyCheck::indicator:checked {
                background: %(trace_poly)s;
                border-color: %(trace_poly)s;
            }
            QCheckBox#traceGaussianCheck {
                color: %(trace_gaussian)s;
            }
            QCheckBox#traceGaussianCheck::indicator:checked {
                background: %(trace_gaussian)s;
                border-color: %(trace_gaussian)s;
            }
            QTabWidget::pane {
                border: none;
                border-radius: 0px;
                background: transparent;
                top: 0px;
            }
            QTabBar::tab {
                background: transparent;
                border: none;
                padding: 1px 4px;
                margin-right: 2px;
                border-top-left-radius: 0px;
                border-top-right-radius: 0px;
                color: %(muted)s;
                text-align: left;
            }
            QTabBar::tab:selected {
                background: transparent;
                color: %(fg)s;
                border: none;
            }
            QTabBar::tab:!selected:hover {
                color: %(fg)s;
            }
            QGroupBox {
                background: transparent;
                border: none;
                border-radius: 0px;
                margin-top: 0px;
                padding-top: 0px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 0px;
                padding: 0px;
                color: transparent;
                height: 0px;
            }
            QToolButton#collapseToggle {
                background: transparent;
                border: none;
                border-radius: 0px;
                padding: 2px 0px 4px 0px;
                font-weight: 600;
                color: %(muted)s;
                text-align: left;
            }
            QSlider::groove:horizontal {
                height: 8px;
                background: %(grid)s;
                border: 1px solid %(border)s;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 16px;
                margin: -3px 0;
                border-radius: 2px;
                border: 1px solid %(border)s;
                background: %(accent)s;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 2px 0 2px 0;
            }
            QScrollBar::handle:vertical {
                background: %(scroll)s;
                border-radius: 4px;
                min-height: 28px;
            }
            QScrollBar::handle:vertical:hover {
                background: %(scroll_hover)s;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
                background: transparent;
                border: none;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 8px;
                margin: 0 2px 0 2px;
            }
            QScrollBar::handle:horizontal {
                background: %(scroll)s;
                border-radius: 4px;
                min-width: 28px;
            }
            QScrollBar::handle:horizontal:hover {
                background: %(scroll_hover)s;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
                background: transparent;
                border: none;
            }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                background: transparent;
            }
            QSplitter::handle {
                background: transparent;
            }
            QSplitter::handle:horizontal {
                width: 8px;
                margin: 0px;
            }
            QSplitter::handle:vertical {
                height: 8px;
                margin: 0px;
            }
            QSplitter::handle:hover {
                background: transparent;
            }
            """ % {
                **palette,
                "trace_max": self.TRACE_METRIC_COLORS["smoothed_max"],
                "trace_centroid": self.TRACE_METRIC_COLORS["centroid"],
                "trace_poly": self.TRACE_METRIC_COLORS["poly_max"],
                "trace_gaussian": self.TRACE_METRIC_COLORS["gaussian_center"],
            }
        self.setStyleSheet(stylesheet.replace("__CHECKMARK_ICON__", checkmark_icon))

    def set_theme(self, theme_mode: str) -> None:
        if theme_mode not in {"light", "dark"} or theme_mode == self._theme_mode:
            return
        self._theme_mode = theme_mode
        save_app_setting("theme_mode", self._theme_mode)
        self._apply_modern_style()
        self._style_plot_widgets()
        self._apply_sensorgram_display_style()
        self._update_measurement_toggle_button()
        self._render_session_stats_recording_blink_indicator()
        self._log_info(f"Theme switched to {self._theme_mode}.")
        if self._experiment_control_window is not None:
            self._experiment_control_window.set_theme(self._theme_mode)

    def _style_plot_widgets(self) -> None:
        palette = self._theme_palette()
        for plot in (self.spectrum_plot, self.trace_plot):
            plot.setBackground(palette["plot_bg"])
            plot.getPlotItem().getViewBox().setBorder(pg.mkPen(palette["plot_border"]))
            bottom_axis = plot.getPlotItem().getAxis("bottom")
            left_axis = plot.getPlotItem().getAxis("left")
            bottom_axis.enableAutoSIPrefix(False)
            left_axis.enableAutoSIPrefix(False)
            bottom_axis.setStyle(tickTextOffset=2)
            left_axis.setStyle(tickTextOffset=2)
            bottom_axis.setTextPen(pg.mkPen(palette["axis_text"]))
            left_axis.setTextPen(pg.mkPen(palette["axis_text"]))
            bottom_axis.setPen(pg.mkPen(palette["axis_pen"]))
            left_axis.setPen(pg.mkPen(palette["axis_pen"]))
            plot.getPlotItem().titleLabel.item.setDefaultTextColor(QColor(palette["fg"]))
            plot.getPlotItem().showGrid(x=True, y=True, alpha=0.16 if self._theme_mode == "dark" else 0.18)
        if hasattr(self, "residual_axis"):
            self.residual_axis.setStyle(tickTextOffset=2)
            self.residual_axis.setTextPen(pg.mkPen(palette["axis_text"]))
            self.residual_axis.setPen(pg.mkPen(palette["axis_pen"]))
        crosshair_color = "#7f93a8" if self._theme_mode == "dark" else "#666666"
        self.spectrum_vline.setPen(pg.mkPen(crosshair_color, width=1))
        self.spectrum_hline.setPen(pg.mkPen(crosshair_color, width=1))
        self.trace_vline.setPen(pg.mkPen(crosshair_color, width=1))
        self.trace_hline.setPen(pg.mkPen(crosshair_color, width=1))
        if self._theme_mode == "dark":
            self.processing_region_item.setBrush(pg.mkBrush(111, 179, 255, 26))
            self.fit_region_item.setBrush(pg.mkBrush(255, 190, 92, 28))
            self.max_marker.setPen(pg.mkPen("#11161c", width=1.4))
            self.poly_marker.setPen(pg.mkPen("#11161c", width=1.4))
            self.gaussian_marker.setPen(pg.mkPen("#11161c", width=1.4))
            self.centroid_marker.setPen(pg.mkPen("#11161c", width=1.4))
            self.residual_curve.setPen(pg.mkPen((0, 0, 0, 0), width=0))
        else:
            self.processing_region_item.setBrush(pg.mkBrush(90, 160, 255, 30))
            self.fit_region_item.setBrush(pg.mkBrush(255, 180, 80, 40))
            self.max_marker.setPen(pg.mkPen("w", width=1.4))
            self.poly_marker.setPen(pg.mkPen("w", width=1.4))
            self.gaussian_marker.setPen(pg.mkPen("w", width=1.4))
            self.centroid_marker.setPen(pg.mkPen("w", width=1.4))
            self.residual_curve.setPen(pg.mkPen((0, 0, 0, 0), width=0))
        self._apply_metric_color_styles()
        self._update_freeze_button_icon()
        self._update_residual_button_icon()
        self._update_dark_reference_button_icons()

    def _sensorgram_heatmap_lookup_table(self) -> np.ndarray:
        return sensorgram_heatmap_lookup_table(self)

    def _apply_sensorgram_display_style(self) -> None:
        apply_sensorgram_display_style(self)

    def _set_log_following(self, enabled: bool) -> None:
        set_log_following(self, enabled)

    def _sync_log_view_buttons(self) -> None:
        mode = str(getattr(self, "_log_view_mode", "all") or "all").casefold()
        if hasattr(self, "log_view_all_button"):
            self.log_view_all_button.setChecked(mode == "all")
        if hasattr(self, "log_view_gui_button"):
            self.log_view_gui_button.setChecked(mode == "gui")
        if hasattr(self, "log_view_devices_button"):
            self.log_view_devices_button.setChecked(mode == "devices")

    def _set_log_view_mode(self, mode: str, refresh: bool = True) -> None:
        set_log_view_mode(self, mode, refresh=refresh)
        self._sync_log_view_buttons()

    def _clear_log_terminal(self) -> None:
        clear_log_terminal(self)

    def _copy_log_terminal(self) -> None:
        copy_log_terminal(self)

    def _flush_log_buffer(self) -> None:
        self._run_gui_callback_timed("log_buffer", lambda: flush_log_buffer(self))

    def _ui_heartbeat_tick(self) -> None:
        def _callback() -> None:
            now = perf_counter()
            expected_at = getattr(self, "_ui_heartbeat_expected_at", None)
            interval_ms = float(getattr(self, "_ui_heartbeat_interval_ms", 100.0))
            if expected_at is None:
                self._ui_heartbeat_expected_at = now + interval_ms / 1000.0
                return
            delay_ms = max((now - float(expected_at)) * 1000.0, 0.0)
            self._last_ui_heartbeat_delay_ms = delay_ms
            if self._ui_heartbeat_max_delay_ms is None or delay_ms > self._ui_heartbeat_max_delay_ms:
                self._ui_heartbeat_max_delay_ms = delay_ms
            self._ui_heartbeat_expected_at = now + interval_ms / 1000.0
            # Keep heartbeat diagnostics out of the buffered UI log terminal.
            # The live values are surfaced in the session statistics panel instead.

        self._run_gui_callback_timed("ui_heartbeat", _callback)
        self._run_gui_callback_timed("gui_housekeeping", lambda: self._drain_gui_housekeeping_tasks())

    def _drain_gui_housekeeping_tasks(self) -> None:
        drain_gui_housekeeping_tasks_for(self)

    def _append_log_record(self, levelno: int, source: str, text: str) -> None:
        append_log_record(self, levelno, source, text)

    def _insert_log_record(self, cursor: QTextCursor, levelno: int, source: str, text: str) -> None:
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
        source_label = escape(str(source).split(".")[-1] or "app")
        escaped = escape(text).replace("\n", "<br>")
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
        if self._log_follow_enabled:
            scrollbar = self.log_terminal.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _append_log_record_now(self, levelno: int, source: str, text: str) -> None:
        append_log_record_now(self, levelno, source, text)

    def _log_event(self, levelno: int, message: str, source: str = "main") -> None:
        log_event(self, levelno, message, source=source)

    def _log_debug(self, message: str, source: str = "main") -> None:
        log_debug(self, message, source=source)

    def _log_info(self, message: str, source: str = "main") -> None:
        log_info(self, message, source=source)

    def _log_success(self, message: str, source: str = "main") -> None:
        log_success(self, message, source=source)

    def _log_warning(self, message: str, source: str = "main") -> None:
        log_warning(self, message, source=source)

    def _log_error(self, message: str, source: str = "main") -> None:
        log_error(self, message, source=source)

    def _log_throttled(self, key: str, message: str, *, level: int = logging.DEBUG, min_interval: float = 1.5) -> None:
        log_throttled(self, key, message, level=level, min_interval=min_interval)

    def _fit_window_to_available_screen(self) -> None:
        screen = None
        if self.windowHandle() is not None:
            screen = self.windowHandle().screen()
        if screen is None:
            screen = self.screen()
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        margin = 12
        max_width = max(available.width() - margin * 2, 640)
        max_height = max(available.height() - margin * 2, 480)
        target_width = min(self.width(), max_width)
        target_height = min(self.height(), max_height)
        self.resize(target_width, target_height)

        if self._ui_state:
            x_pos = min(
                max(self.x(), available.x() + margin),
                available.x() + available.width() - self.width() - margin,
            )
            y_pos = min(
                max(self.y(), available.y() + margin),
                available.y() + available.height() - self.height() - margin,
            )
            self.move(x_pos, y_pos)
            return

        frame = self.frameGeometry()
        x_pos = available.x() + max((available.width() - frame.width()) // 2, 0)
        y_pos = available.y() + max((available.height() - frame.height()) // 2, 0)
        self.move(x_pos, y_pos)

    def showEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        super().showEvent(event)
        if not getattr(self, "_startup_show_event_reported", False):
            self._startup_show_event_reported = True
            startup_show_t0 = getattr(self, "_startup_show_requested_t0", None)
            if startup_show_t0 is not None:
                self._log_info(
                    f"Startup +{(perf_counter() - startup_show_t0) * 1000.0:.1f} ms: first showEvent reached"
                )
            self._log_startup_widget_snapshot()
        self._sync_main_view_visibility()
        if not self._screen_fitted:
            self._screen_fitted = True
            if self._start_maximized:
                self.showMaximized()
            elif not self._ui_state:
                self._fit_window_to_available_screen()
        if not self._hardware_init_scheduled:
            self._hardware_init_scheduled = True
            if self._launch_profile_spec.scan_devices:
                QTimer.singleShot(0, self._start_hardware_initialization)
            elif self._launch_profile_spec.start_live_acquisition:
                QTimer.singleShot(0, self._start_live_acquisition)
        self._sync_view_actions()

    def moveEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        super().moveEvent(event)
        if not getattr(self, "_restoring_ui_state", False):
            self._schedule_ui_state_persist()

    def resizeEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        super().resizeEvent(event)
        if not getattr(self, "_restoring_ui_state", False):
            self._schedule_ui_state_persist()

    def paintEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        started = perf_counter()
        super().paintEvent(event)
        elapsed_ms = (perf_counter() - started) * 1000.0
        if not getattr(self, "_startup_paint_event_reported", False):
            self._startup_paint_event_reported = True
            startup_show_t0 = getattr(self, "_startup_show_requested_t0", None)
            if startup_show_t0 is not None:
                self._log_info(
                    f"Startup +{(perf_counter() - startup_show_t0) * 1000.0:.1f} ms: first paintEvent reached | "
                    f"paint={elapsed_ms:.2f} ms"
                )
                self._log_startup_plot_state()

    def _log_startup_widget_snapshot(self) -> None:
        diagnostics = getattr(self, "_diagnostics", None)
        debug_enabled = bool(getattr(diagnostics, "debug_timing_enabled", False))
        deep_enabled = bool(getattr(diagnostics, "deep_timing_enabled", False))
        if not debug_enabled and not deep_enabled:
            return
        try:
            app = QApplication.instance()
            top_level_widgets: list[QWidget] = []
            if app is not None:
                top_level_widgets = list(app.topLevelWidgets())
            current_widget = None
            stack_pages: list[str] = []
            stack_index = -1
            if hasattr(self, "_top_content_stack") and self._top_content_stack is not None:
                stack_index = int(self._top_content_stack.currentIndex())
                try:
                    for page_index in range(self._top_content_stack.count()):
                        page = self._top_content_stack.widget(page_index)
                        if page is None:
                            continue
                        page_label = "page"
                        if page is getattr(self, "_spectra_block", None):
                            page_label = "spectra_block"
                        elif page is getattr(self, "_sensorgram_block", None):
                            page_label = "sensorgram_block"
                        elif page is getattr(self, "_experiment_control_panel_placeholder", None):
                            page_label = "flow_placeholder"
                        page_current = page_index == stack_index
                        stack_pages.append(
                            f"{page_index}:{page_label}:{page.__class__.__name__}(objectName={page.objectName()!r}, "
                            f"visible={page.isVisible()}, current={page_current})"
                        )
                except Exception:
                    stack_pages = []
                stack_widget = self._top_content_stack.currentWidget()
                if stack_widget is not None:
                    current_widget = (
                        f"{stack_widget.__class__.__name__}(objectName={stack_widget.objectName()!r}, "
                        f"visible={stack_widget.isVisible()}, window={stack_widget.isWindow()})"
                    )
            experiment_control_widget = getattr(self, "_experiment_control_window", None)
            experiment_control_widget_text = (
                f"{experiment_control_widget.__class__.__name__}(visible={experiment_control_widget.isVisible()}, window={experiment_control_widget.isWindow()})"
                if experiment_control_widget is not None
                else "None"
            )
            top_level_summary: list[str] = []
            for widget in top_level_widgets:
                try:
                    top_level_summary.append(
                        f"{widget.__class__.__name__}(title={widget.windowTitle()!r}, "
                        f"visible={widget.isVisible()}, window={widget.isWindow()})"
                    )
                except Exception:
                    top_level_summary.append(widget.__class__.__name__)
            self._log_info(
                "Startup widget snapshot | "
                f"main_visible={self.isVisible()} | "
                f"main_content_visible={bool(self._main_content_widget.isVisible()) if self._main_content_widget is not None else False} | "
                f"top_view={getattr(self, '_top_view_mode', '-')} | "
                f"stack_index={stack_index} | "
                f"stack_current={current_widget or '-'} | "
                f"experiment_control_widget={experiment_control_widget_text} | "
                f"experiment_control_bootstrap_pending={bool(getattr(self, '_experiment_control_panel_bootstrap_pending', getattr(self, '_flow_panel_bootstrap_pending', False)))} | "
                f"ui_startup_ready={bool(getattr(self, '_ui_startup_ready', False))} | "
                f"stack_pages=[{' ; '.join(stack_pages) if stack_pages else '-'}] | "
                f"top_levels=[{' ; '.join(top_level_summary) if top_level_summary else '-'}]"
            )
            self._log_info(f"Top content diagnostics | {self._top_content_stack_diagnostics()}")
            if deep_enabled:
                self._log_startup_widget_trace(top_level_widgets)
        except Exception as exc:
            self._log_warning(f"Startup widget snapshot failed: {exc}")

    def _top_content_stack_diagnostics(self) -> dict[str, object]:
        stack = getattr(self, "_top_content_stack", None)
        experiment_control_widget = getattr(self, "_experiment_control_window", None)
        placeholder = getattr(self, "_experiment_control_panel_placeholder", None)
        spectra_block = getattr(self, "_spectra_block", None)
        pages: list[dict[str, object]] = []
        if stack is not None:
            try:
                current_index = int(stack.currentIndex())
                count = int(stack.count())
            except Exception:
                current_index = -1
                count = 0
            for index in range(count):
                page = stack.widget(index)
                if page is None:
                    continue
                if page is spectra_block:
                    label = "spectra"
                elif page is experiment_control_widget:
                    label = "experimental_control"
                elif page is placeholder:
                    label = "placeholder"
                else:
                    label = "unknown"
                pages.append(
                    {
                        "index": index,
                        "label": label,
                        "class": page.__class__.__name__,
                        "visible": bool(page.isVisible()),
                        "is_current": index == current_index,
                        "parent": page.parent().__class__.__name__ if page.parent() is not None else None,
                    }
                )
        else:
            current_index = -1
            count = 0
        experiment_control_index = -1
        placeholder_index = -1
        spectra_index = -1
        if stack is not None:
            try:
                if experiment_control_widget is not None:
                    experiment_control_index = int(stack.indexOf(experiment_control_widget))
                if placeholder is not None:
                    placeholder_index = int(stack.indexOf(placeholder))
                if spectra_block is not None:
                    spectra_index = int(stack.indexOf(spectra_block))
            except Exception:
                pass
        return {
            "top_content_mode": getattr(self, "_top_view_mode", "-"),
            "top_content_stack_current_index": current_index,
            "top_content_stack_current_widget": (
                f"{stack.currentWidget().__class__.__name__}(objectName={stack.currentWidget().objectName()!r})"
                if stack is not None and stack.currentWidget() is not None
                else None
            ),
            "top_content_stack_page_count": count,
            "top_content_stack_pages": pages,
            "experiment_control_window_exists": experiment_control_widget is not None,
            "experiment_control_window_index": experiment_control_index,
            "placeholder_index": placeholder_index,
            "spectra_block_index": spectra_index,
        }

    def _log_startup_widget_trace(self, widgets: list[QWidget]) -> None:
        if not widgets:
            self._log_info("Startup widget trace | count=0")
            return

        def _format_flags(widget: QWidget) -> str:
            try:
                flags = widget.windowFlags()
                raw_value = getattr(flags, "value", flags)
                return f"0x{int(raw_value):x}"
            except Exception:
                return "-"

        def _format_geometry(widget: QWidget) -> str:
            try:
                geo = widget.geometry()
                frame = widget.frameGeometry()
                return (
                    f"geo=({geo.x()},{geo.y()},{geo.width()},{geo.height()}) "
                    f"frame=({frame.x()},{frame.y()},{frame.width()},{frame.height()})"
                )
            except Exception:
                return "geo=- frame=-"

        def _format_parent_chain(widget: QWidget) -> str:
            chain: list[str] = []
            parent = widget.parentWidget()
            depth = 0
            while isinstance(parent, QWidget) and depth < 6:
                try:
                    chain.append(
                        f"{parent.__class__.__name__}(objectName={parent.objectName()!r}, "
                        f"title={parent.windowTitle()!r}, visible={parent.isVisible()}, window={parent.isWindow()})"
                    )
                except Exception:
                    chain.append(parent.__class__.__name__)
                parent = parent.parentWidget()
                depth += 1
            return " -> ".join(chain) if chain else "-"

        visible_count = 0
        self._log_info(f"Startup widget trace | count={len(widgets)}")
        for index, widget in enumerate(widgets, start=1):
            try:
                is_visible = bool(widget.isVisible())
                visible_count += int(is_visible)
                self._log_info(
                    "Startup widget trace | "
                    f"#{index} class={widget.__class__.__name__} "
                    f"objectName={widget.objectName()!r} "
                    f"title={widget.windowTitle()!r} "
                    f"visible={is_visible} "
                    f"window={widget.isWindow()} "
                    f"flags={_format_flags(widget)} "
                    f"{_format_geometry(widget)} "
                    f"parent_chain={_format_parent_chain(widget)}"
                )
            except Exception as exc:
                self._log_warning(f"Startup widget trace entry {index} failed: {exc}")
        self._log_info(f"Startup widget trace | visible_count={visible_count}")

    def _log_startup_plot_state(self) -> None:
        try:
            spectrum_paints = int(getattr(self, "_spectrum_plot_paint_count", 0) or 0)
            trace_paints = int(getattr(self, "_trace_plot_paint_count", 0) or 0)
            spectrum_visible = bool(getattr(self, "spectrum_plot", None).isVisible()) if hasattr(self, "spectrum_plot") else False
            trace_visible = bool(getattr(self, "trace_plot", None).isVisible()) if hasattr(self, "trace_plot") else False
            self._log_info(
                "Startup plot state | "
                f"spectrum_paints={spectrum_paints} | "
                f"trace_paints={trace_paints} | "
                f"spectrum_visible={spectrum_visible} | "
                f"trace_visible={trace_visible}"
            )
        except Exception as exc:
            self._log_warning(f"Startup plot state failed: {exc}")

    def _notify_startup_plot_painted(self, prefix: str) -> None:
        if prefix not in {"spectrum_plot", "trace_plot"}:
            return
        painted = getattr(self, "_startup_plot_painted", None)
        if not isinstance(painted, set):
            painted = set()
            self._startup_plot_painted = painted
        if prefix in painted:
            return
        painted.add(prefix)
        startup_show_t0 = getattr(self, "_startup_show_requested_t0", None)
        if startup_show_t0 is not None:
            self._log_info(f"Startup +{(perf_counter() - startup_show_t0) * 1000.0:.1f} ms: first plot paint on {prefix}")

    def _notify_startup_data_rendered(self, source: str) -> None:
        if source not in {"spectrum", "trace"}:
            return
        rendered = getattr(self, "_startup_data_rendered", None)
        if not isinstance(rendered, set):
            rendered = set()
            self._startup_data_rendered = rendered
        if source in rendered:
            return
        rendered.add(source)
        startup_show_t0 = getattr(self, "_startup_show_requested_t0", None)
        if startup_show_t0 is not None:
            self._log_info(f"Startup +{(perf_counter() - startup_show_t0) * 1000.0:.1f} ms: first data render on {source}")
        if source == "spectrum":
            self._reveal_startup_window()
            splash = getattr(self, "_startup_splash", None)
            if splash is not None and not getattr(self, "_startup_splash_close_scheduled", False):
                self._startup_splash_close_scheduled = True
                self._log_info("Startup data renders complete; closing splash.")
                QTimer.singleShot(0, self._close_startup_splash)

    def _reveal_startup_window(self) -> None:
        self._complete_startup_show()

    def _complete_startup_show(self) -> None:
        if not bool(getattr(self, "_startup_show_requested", False)):
            return
        if getattr(self, "_startup_window_revealed", False):
            return
        startup_show_t0 = getattr(self, "_startup_show_requested_t0", None)
        if startup_show_t0 is not None:
            self._log_info(
                f"Startup +{(perf_counter() - startup_show_t0) * 1000.0:.1f} ms: completing startup show"
            )
        self._startup_window_revealed = True
        if bool(getattr(self, "_start_maximized", False)):
            self._screen_fitted = True
            self.showMaximized()
        else:
            if not getattr(self, "_screen_fitted", False):
                self._fit_window_to_available_screen()
                self._screen_fitted = True
            self.show()
        splash = getattr(self, "_startup_splash", None)
        if splash is not None and not getattr(self, "_startup_splash_close_scheduled", False):
            self._startup_splash_close_scheduled = True
            QTimer.singleShot(200, self._close_startup_splash)

    def _close_startup_splash(self) -> None:
        splash = getattr(self, "_startup_splash", None)
        if splash is None:
            return
        self._startup_splash = None
        app = QApplication.instance()
        tracer = getattr(app, "_startup_widget_tracer", None) if app is not None else None
        if app is not None and tracer is not None:
            try:
                app.removeEventFilter(tracer)
            except Exception:
                pass
            try:
                app._startup_widget_tracer = None
            except Exception:
                pass
        try:
            splash.close()
        except Exception:
            pass

    def _restore_ui_state(self) -> None:
        restore_ui_state_for(self)

    def _save_ui_state(self) -> None:
        save_ui_state_for(self)

    def _schedule_ui_state_persist(self) -> None:
        schedule_ui_state_persist_for(self)

    def _collapsible_section_state(self) -> dict[str, bool]:
        return collapsible_section_state_for(self)

    def _restore_collapsible_section_state(self) -> None:
        restore_collapsible_section_state_for(self)

    def _acquisition_state_payload(self) -> dict[str, object]:
        return acquisition_state_payload_for(self)

    def _persist_acquisition_state(self) -> None:
        persist_acquisition_state_for(self)

    def _schedule_acquisition_state_persist(self) -> None:
        schedule_acquisition_state_persist_for(self)

    def _set_ui_state_autosave_enabled(self, enabled: bool) -> None:
        set_ui_state_autosave_enabled_for(self, enabled)

    def _set_acquisition_state_autosave_enabled(self, enabled: bool) -> None:
        set_acquisition_state_autosave_enabled_for(self, enabled)

    def _set_log_buffering_enabled(self, enabled: bool) -> None:
        set_log_buffering_enabled_for(self, enabled)

    def _set_gui_housekeeping_enabled(self, enabled: bool) -> None:
        set_gui_housekeeping_enabled(self, enabled)

    def _set_sensorgram_heatmap_enabled(self, enabled: bool) -> None:
        set_sensorgram_heatmap_enabled(self, enabled)

    def _set_metric_plot_enabled(self, enabled: bool) -> None:
        set_metric_plot_enabled(self, enabled)

    def _apply_acquisition_state_to_widgets(self, state: dict[str, object]) -> None:
        apply_acquisition_state_to_widgets(self, state)

    def _open_experiment_control_window(self) -> None:
        self._activate_experiment_control_view()

    def _open_experimental_control_window(self) -> None:
        self._open_experiment_control_window()

    def _toggle_flow_panel_visibility(self, checked: bool | None = None) -> None:
        self._toggle_experimental_control_panel_visibility(checked)

    def _toggle_experimental_control_panel_visibility(self, checked: bool | None = None) -> None:
        toggle_experimental_control_panel_visibility(self, checked)

    def _ensure_flow_panel(self) -> None:
        self._ensure_experimental_control_panel()

    def _ensure_experimental_control_panel(self) -> None:
        ensure_experiment_control_panel_for(self)

    def _ensure_experiment_control_panel(self) -> None:
        self._ensure_experimental_control_panel()

    def _sync_main_view_visibility(self) -> None:
        sync_main_view_visibility(self)

    def _show_flow_only(self) -> None:
        self._show_experimental_control_only()

    def _show_experimental_control_only(self) -> None:
        show_experimental_control_only(self)

    def _show_plots_only(self) -> None:
        show_plots_only(self)

    def _show_split_view(self) -> None:
        show_split_view(self)

    def _apply_layout_preset(self, preset_key: str) -> None:
        apply_layout_preset(self, preset_key)

    def _save_current_layout_to_preset(self, preset_key: str | None = None) -> None:
        save_current_layout_to_preset(self, preset_key)

    def _reset_layout_presets_to_defaults(self) -> None:
        reset_layout_presets_to_defaults(self)

    def _activate_spectra_view(self) -> None:
        activate_spectra_view(self)

    def _activate_flow_view(self) -> None:
        self._activate_experimental_control_view()

    def _activate_experiment_control_view(self) -> None:
        self._ensure_experimental_control_panel()
        activate_experiment_control_view(self)

    def _activate_experimental_control_view(self) -> None:
        activate_experimental_control_view(self)

    def _toggle_left_controls(self, checked: bool | None = None) -> None:
        toggle_left_controls(self, checked)

    def _toggle_sensorgram(self, checked: bool | None = None) -> None:
        toggle_sensorgram(self, checked)

    def _set_diagnostics_panel_visible(self, visible: bool) -> None:
        set_diagnostics_panel_visible(self, visible)

    def _toggle_diagnostics_panel(self, checked: bool | None = None) -> None:
        toggle_diagnostics_panel(self, checked)

    def _sync_view_actions(self) -> None:
        sync_view_actions(self)

    def _sync_diagnostics_panel_action(self) -> None:
        sync_diagnostics_panel_action(self)

    def _apply_launch_profile_layout(self) -> None:
        apply_launch_profile_layout(self)

    def _suppress_hardware_disconnect_warning(self) -> bool:
        return str(getattr(self, "_source_mode", "")).strip().lower() == "simulation"

    def _handle_flow_availability_changed(self, probe: object) -> None:
        if probe is None:
            self._discovered_pump_probe = None
            self._update_pump_status(None)
            if not self._closing and not self._suppress_hardware_disconnect_warning():
                self._log_warning("Pump controller disconnected.")
            return
        if hasattr(probe, "port"):
            self._update_pump_status(probe)
            port_name = getattr(getattr(probe, "port", None), "device", "unknown")
            self._log_success(f"Pump controller connected on {port_name}.")

    def _handle_valve_availability_changed(self, probe: object) -> None:
        refresh_hw_device_status_strip(self)
        if probe is None:
            if not self._closing and not self._suppress_hardware_disconnect_warning():
                self._log_warning("Valve controller disconnected.")
            return
        port_name = getattr(probe, "port", "unknown")
        model = getattr(probe, "model", "Valve controller")
        self._log_success(f"Valve controller connected: {model} on {port_name}.")

    def _handle_mswitch_availability_changed(self, probe: object) -> None:
        self._mswitch_probe = probe if probe is not None else None
        refresh_hw_device_status_strip(self)
        if probe is None:
            if not self._closing and not self._suppress_hardware_disconnect_warning():
                self._log_warning("M-Switch disconnected.")
            return
        port_name = getattr(probe, "port", "unknown")
        model = getattr(probe, "model", "AMF switch")
        self._log_success(f"M-Switch connected: {model} on {port_name}.")

    def _handle_flow_recording_control(self, action: str) -> bool:
        action = str(action or "").strip().lower()
        # Recording is intentionally independent from flow HOLD/PAUSE.
        # The experiment controller may request "start" or "stop", but a flow HOLD should not
        # close the writer or freeze acquisition.
        if action == "start":
            if not self._measurement_active:
                self._start_measurement_run()
            return bool(self._measurement_active)
        elif action == "pause":
            return True
        elif action == "stop":
            if self._measurement_active:
                self._stop_measurement_run()
            return True
        return True

    def _handle_experimental_control_state_recorded(self, payload: object) -> None:
        self._update_window_mode_label()
        if self._measurement_writer is None or not self._measurement_active:
            return
        if not isinstance(payload, dict):
            return
        row = dict(payload)
        # Persist the event on an absolute UTC axis for file stability; the GUI still shows relative time.
        timestamp_utc_ms = int(round(datetime.now(timezone.utc).timestamp() * 1000.0))
        row["timestamp_utc_ms"] = max(timestamp_utc_ms, 0)
        if self._measurement_started_at is not None:
            elapsed_ms = int(round((datetime.now(timezone.utc) - self._measurement_started_at).total_seconds() * 1000.0))
        else:
            elapsed_ms = 0
        row["t_ms"] = max(elapsed_ms, 0)
        self._measurement_writer.append_flow_state(row)
        self._record_sensorgram_control_step_event(row)
        self._sync_sensorgram_control_step_overlay()

    def _handle_flow_state_recorded(self, payload: object) -> None:
        self._handle_experimental_control_state_recorded(payload)

    def _apply_control_sizing(self) -> None:
        tall_widgets = [
            self.integration_spin,
            self.averages_spin,
            self.auto_integration_button,
            self.correct_dark_check,
            self.correct_nonlinearity_check,
            self.live_rate_spin,
            self.trace_noise_window_spin,
            self.range_min_spin,
            self.range_max_spin,
            self.baseline_method_combo,
            self.smoothing_method_combo,
            self.smoothing_window_spin,
            self.temporal_smoothing_spin,
            self.crop_method_combo,
            self.fit_method_combo,
            self.poly_order_spin,
            self.fit_window_spin,
            self.analysis_resolution_spin,
            self.metric_mode_combo,
            self.plot_selector,
            self.save_processing_button,
            self.load_processing_button,
            self.clear_trace_button,
            self.trace_record_button,
            self.show_residual_button,
            self.freeze_plots_button,
            self.autoscale_spectrum_button,
            self.autoscale_trace_button,
            self.sim_resolution_spin,
        ]
        for widget in tall_widgets:
            widget.setMinimumHeight(26)

        compact_combos = [
            self.baseline_method_combo,
            self.smoothing_method_combo,
            self.crop_method_combo,
            self.fit_method_combo,
            self.analysis_resolution_spin,
            self.metric_mode_combo,
            self.plot_selector,
        ]
        for combo in compact_combos:
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            combo.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            combo.setMinimumContentsLength(1)
            combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)

        configure_spectra_processing_group_controls(self)

        for button in (
            self.measurement_toggle_button,
            self.stop_measurement_button,
            self.next_measurement_button,
            self.acquire_dark_button,
            self.acquire_reference_button,
        ):
            button.setMinimumSize(QSize(34, 34))

        for slider in (
            self.sim_peak_center_slider,
            self.sim_peak_width_slider,
            self.sim_peak_height_slider,
            self.sim_secondary_peak_offset_slider,
            self.sim_secondary_peak_height_slider,
            self.sim_secondary_peak_width_slider,
            self.sim_baseline_slider,
            self.sim_slope_slider,
            self.sim_noise_slider,
        ):
            slider.setMinimumHeight(18)

        self.source_tabs.setMinimumHeight(220)

    def _connect_simulation_widgets(self) -> None:
        for slider in (
            self.sim_peak_center_slider,
            self.sim_peak_width_slider,
            self.sim_peak_height_slider,
            self.sim_secondary_peak_offset_slider,
            self.sim_secondary_peak_height_slider,
            self.sim_secondary_peak_width_slider,
            self.sim_baseline_slider,
            self.sim_slope_slider,
            self.sim_noise_slider,
        ):
            slider.valueChanged.connect(self._handle_simulation_parameter_change)
        self.sim_resolution_spin.valueChanged.connect(self._handle_simulation_parameter_change)

    def _update_simulation_labels(self) -> None:
        self.sim_peak_center_value.setText(f"{self.sim_peak_center_slider.value()} nm")
        self.sim_peak_width_value.setText(f"{self.sim_peak_width_slider.value()} nm")
        self.sim_peak_height_value.setText(str(self.sim_peak_height_slider.value()))
        self.sim_secondary_peak_offset_value.setText(f"{self.sim_secondary_peak_offset_slider.value():+d} nm")
        self.sim_secondary_peak_height_value.setText(f"{self.sim_secondary_peak_height_slider.value()}%")
        self.sim_secondary_peak_width_value.setText(f"{self.sim_secondary_peak_width_slider.value()}%")
        self.sim_baseline_value.setText(str(self.sim_baseline_slider.value()))
        self.sim_slope_value.setText(f"{self.sim_slope_slider.value() / 100.0:.0%}")
        self.sim_noise_value.setText(str(self.sim_noise_slider.value()))

    def _handle_simulation_parameter_change(self) -> None:
        self._update_simulation_labels()
        self._simulation_refresh_timer.start()
        self._schedule_acquisition_state_persist()

    def _flush_simulation_parameter_change(self) -> None:
        def _callback() -> None:
            self._sync_simulation_backend_from_controls()
            if self._source_mode == "simulation" and not self._live_active:
                self._session.set_sample(self._build_simulation_spectrum("sample"))
                self._refresh_plot()

        self._run_gui_callback_timed("simulation_parameter_flush", _callback)

    def _handle_source_tab_changed(self, index: int) -> None:
        self._configure_source_tabs()

    def _apply_source_mode(self, new_mode: str, restart_live: bool) -> None:
        if self._measurement_active and new_mode != self._source_mode:
            self._log_warning("Source switching is disabled while measurement is running.")
            self.status_label.setText("Source switching is disabled while measurement is running.")
            return
        self._source_mode = new_mode
        self._source_epoch += 1
        self._session = self._hardware_session if new_mode == "spectrometer" else self._simulation_session
        self._raw_last_finish_ts = None
        self._raw_last_sample_index = None
        self._last_elapsed_ms = None
        self._last_spacing_ms = None
        self._last_overhead_ms = None
        self._effective_raw_rate_hz = None
        self._last_display_average_count = None
        self._last_display_period_ms = None
        self._sensorgram_heatmap_history.clear()
        self._sensorgram_heatmap_history_revision += 1
        if hasattr(self, "_plot_view_cache"):
            self._plot_view_cache.clear()
        self._sensorgram_heatmap_wavelengths = None
        self._sensorgram_heatmap_axis_key = None
        self._metric_reference_processed = None
        self._live_trace_started_at = None
        self._reset_live_accumulator()
        if new_mode == "simulation" and self._session.state.sample is None:
            self._session.set_sample(self._build_simulation_spectrum("sample"))
        self._log_success(
            f"Active source set to {'spectrometer' if new_mode == 'spectrometer' else 'simulation'}."
        )
        self._configure_source_tabs()
        self._refresh_plot()
        self._request_deferred_ui_refresh(telemetry=True, live_estimate=True)
        self._update_simulation_controls_enabled()
        self._schedule_acquisition_state_persist()
        if restart_live:
            QTimer.singleShot(0, self._start_live_acquisition)

    def _default_simulation_resolution_nm(self) -> float:
        return float(self._simulation_backend.simulation_parameters().wavelength_resolution_nm)

    def _simulation_wavelength_axis(self) -> np.ndarray:
        if self._measurement_axis_lock is not None:
            return self._measurement_axis_lock.copy()
        return self._simulation_backend.wavelength_axis()

    def _current_simulation_interval_ms(self) -> float:
        process_interval_ms = 1000.0 / max(self.live_rate_spin.value(), 1e-9)
        return max(process_interval_ms, 1.0)

    def _build_simulation_spectrum(self, kind: str) -> Spectrum:
        return self._simulation_backend.acquire_kind_spectrum(kind, self._current_settings())

    def _sync_simulation_backend_from_controls(self) -> None:
        self._simulation_backend.set_simulation_parameters(
            SimulationParameters(
                wavelength_min_nm=400.0,
                wavelength_max_nm=900.0,
                wavelength_resolution_nm=max(self.sim_resolution_spin.value(), 0.001),
                peak_center_nm=float(self.sim_peak_center_slider.value()),
                peak_width_nm=float(max(self.sim_peak_width_slider.value(), 1)),
                peak_height=float(self.sim_peak_height_slider.value()),
                secondary_peak_offset_nm=float(self.sim_secondary_peak_offset_slider.value()),
                secondary_peak_height_percent=float(self.sim_secondary_peak_height_slider.value()),
                secondary_peak_width_percent=float(max(self.sim_secondary_peak_width_slider.value(), 1)),
                baseline=float(self.sim_baseline_slider.value()),
                slope=float(self.sim_slope_slider.value()) / 100.0,
                noise=float(self.sim_noise_slider.value()),
            )
        )
        if (
            self._live_active
            and self._source_mode == "simulation"
            and self._live_worker is not None
            and self._live_worker.is_alive()
        ):
            self._live_worker.update_simulation_parameters(self._simulation_backend.simulation_parameters())

    def _connect_processing_widgets(self) -> None:
        self.range_min_spin.valueChanged.connect(self._handle_processing_setting_change)
        self.range_max_spin.valueChanged.connect(self._handle_processing_setting_change)
        self.baseline_method_combo.currentTextChanged.connect(self._handle_processing_setting_change)
        self.smoothing_method_combo.currentTextChanged.connect(self._handle_processing_setting_change)
        self.smoothing_window_spin.valueChanged.connect(self._handle_processing_setting_change)
        self.temporal_smoothing_spin.valueChanged.connect(self._handle_processing_setting_change)
        self.crop_method_combo.currentTextChanged.connect(self._handle_processing_setting_change)
        self.crop_fraction_spin.valueChanged.connect(self._handle_processing_setting_change)
        self.fit_method_combo.currentTextChanged.connect(self._handle_processing_setting_change)
        self.poly_order_spin.valueChanged.connect(self._handle_processing_setting_change)
        self.fit_window_spin.valueChanged.connect(self._handle_processing_setting_change)
        self.analysis_resolution_spin.currentIndexChanged.connect(self._handle_processing_setting_change)

    def _current_settings(self) -> AcquisitionSettings:
        return AcquisitionSettings(
            integration_time_ms=self.integration_spin.value(),
            averages=self.averages_spin.value(),
            correct_dark_counts=self.correct_dark_check.isChecked() and self._capabilities.supports_dark_correction,
            correct_nonlinearity=(
                self.correct_nonlinearity_check.isChecked()
                and self._capabilities.supports_nonlinearity_correction
            ),
            trigger_mode=0,
        )

    def _current_processing_settings(self) -> ProcessingSettings:
        return current_processing_settings(self)

    def _selected_trace_metrics(self) -> list[str]:
        return selected_trace_metrics(self)

    def _sensorgram_metric_selection(self) -> tuple[list[str], str]:
        return sensorgram_metric_selection_for_sensorgram(self)

    def _apply_sensorgram_metric_selection(self, visible_modes: list[str] | set[str], primary_mode: str, *, save: bool = False) -> None:
        apply_sensorgram_metric_selection(self, visible_modes, primary_mode, save=save)

    def _apply_metric_color_styles(self) -> None:
        colors = getattr(self, "TRACE_METRIC_COLORS", {})
        if not isinstance(colors, dict):
            return
        line_width = max(float(getattr(self, "_sensorgram_line_width_px", 2.2)), 0.5)
        if hasattr(self, "spectrum_curve"):
            self.spectrum_curve.setPen(pg.mkPen("#1f77b4", width=line_width))
        if hasattr(self, "fit_curve"):
            self.fit_curve.setPen(pg.mkPen("#d95f02", width=line_width, style=Qt.PenStyle.DashLine))
        for metric_name, curve in getattr(self, "trace_curves", {}).items():
            if hasattr(curve, "setPen"):
                curve.setPen(pg.mkPen(colors.get(metric_name, "#444444"), width=line_width))
        for metric_name, marker in (
            ("smoothed_max", getattr(self, "max_marker", None)),
            ("centroid", getattr(self, "centroid_marker", None)),
            ("poly_max", getattr(self, "poly_marker", None)),
            ("gaussian_center", getattr(self, "gaussian_marker", None)),
        ):
            if marker is not None and hasattr(marker, "setBrush"):
                marker.setBrush(pg.mkBrush(colors.get(metric_name, "#444444")))
        for metric_name, band in getattr(self, "trace_metric_envelope_bands", {}).items():
            if band is None or not hasattr(band, "setBrush"):
                continue
            base_color = QColor(colors.get(metric_name, "#1F77B4"))
            if not base_color.isValid():
                base_color = QColor("#1F77B4")
            band_alpha = int(round(max(min(float(getattr(self, "_sensorgram_metric_envelope_overlay_alpha", 16)), 100.0), 0.0) * 2.55))
            base_color.setAlpha(band_alpha)
            band.setBrush(pg.mkBrush(base_color))

    def _set_sensorgram_metric_color(self, metric_name: str, color: str, *, save: bool = False) -> None:
        metric_name = normalize_sensorgram_metric_name(metric_name)
        qcolor = QColor(str(color))
        if not qcolor.isValid():
            return
        color_name = qcolor.name()
        self.TRACE_METRIC_COLORS[metric_name] = color_name
        self.SENSORGRAM_TIME_PLOT_COLORS[metric_name] = color_name
        self._apply_metric_color_styles()
        if hasattr(self, "_request_deferred_ui_refresh"):
            self._request_deferred_ui_refresh(trace_plot=True, summary=True)
        if save:
            self._schedule_ui_state_persist()

    def _apply_processing_settings_to_widgets(self, settings: ProcessingSettings) -> None:
        apply_processing_settings_to_widgets(self, settings)

    def _persist_processing_settings(self) -> None:
        persist_processing_settings(self)

    def _save_processing_settings_dialog(self) -> None:
        save_processing_settings_dialog(self)

    def _load_processing_settings_dialog(self) -> None:
        load_processing_settings_dialog(self)

    def _settings_key(self, settings: AcquisitionSettings) -> tuple[object, ...]:
        if self._source_mode == "simulation":
            axis_size = len(self._simulation_wavelength_axis())
        else:
            axis_size = len(getattr(self._spectrometer, "_wavelengths", []))
        return (
            round(settings.integration_time_ms, 6),
            settings.averages,
            settings.correct_dark_counts,
            settings.correct_nonlinearity,
            0,
            axis_size,
        )

    def _auto_set_integration_time(self) -> None:
        if self._source_mode == "simulation":
            self.status_label.setText("Auto integration is only available for the spectrometer source.")
            self._log_warning("Auto integration requested while simulation source is active.")
            return

        if self._busy:
            self._pending_auto_integration = True
            if self._live_active:
                self._resume_live_after_auto_integration = True
                self._live_active = False
            self.status_label.setText("Auto integration queued. Waiting for current acquisition to finish...")
            return

        if self._live_active:
            self._resume_live_after_auto_integration = True
            self._live_active = False
            self._reset_live_accumulator()
            self.status_label.setText("Pausing live acquisition for auto integration...")
            QTimer.singleShot(0, self._auto_set_integration_time)
            return

        try:
            tuned_ms = self._spectrometer.auto_integration_time_ms(self._current_settings())
        except Exception as exc:
            self._show_error(str(exc))
            return

        self.integration_spin.setValue(round(tuned_ms, 3))
        self.status_label.setText(f"Integration time set to {tuned_ms:.3f} ms.")
        self._log_success(f"Integration time tuned to {tuned_ms:.3f} ms.")
        if self._resume_live_after_auto_integration:
            self._resume_live_after_auto_integration = False
            QTimer.singleShot(0, self._start_live_acquisition)

    def _start_acquisition(self, kind: str) -> None:
        start_acquisition(self, kind)

    def _handle_acquisition_success(self, kind: str, result: AcquisitionResult) -> None:
        handle_acquisition_success(self, kind, result)

    def _flush_live_acquisition_results(self) -> None:
        flush_live_acquisition_results(self)

    def _flush_live_recording_results(self) -> None:
        flush_live_recording_results(self)

    def _flush_live_processed_results(self) -> None:
        flush_live_processed_results(self)

    def _request_live_acquisition_poll(self, delay_ms: float | None = None) -> None:
        request_live_acquisition_poll_for_runtime(self, delay_ms=delay_ms)

    def _request_live_visual_refresh(self, delay_ms: float | None = None) -> None:
        request_live_visual_refresh_for_runtime(self, delay_ms=delay_ms)

    def _reset_live_accumulator(self) -> None:
        self._accumulator_sum = None
        self._accumulator_count = 0
        self._accumulator_started_ts = None
        self._accumulator_template = None

    def _handle_acquisition_error(self, source_epoch: int, message: str) -> None:
        handle_acquisition_error(self, source_epoch, message)

    def _set_measurement_buttons_enabled(self, enabled: bool) -> None:
        set_measurement_buttons_enabled(self, enabled)

    def _set_manual_acquisition_buttons_enabled(self, enabled: bool) -> None:
        set_manual_acquisition_buttons_enabled(self, enabled)

    def _set_measurement_ui_locked(self, locked: bool) -> None:
        set_measurement_ui_locked(self, locked)

    def _update_simulation_controls_enabled(self) -> None:
        enabled = self._source_mode == "simulation" and not self._measurement_active
        for widget in (
            self.sim_peak_center_slider,
            self.sim_peak_width_slider,
            self.sim_peak_height_slider,
            self.sim_secondary_peak_offset_slider,
            self.sim_secondary_peak_height_slider,
            self.sim_secondary_peak_width_slider,
            self.sim_baseline_slider,
            self.sim_slope_slider,
            self.sim_noise_slider,
            self.sim_resolution_spin,
            self.sim_output_rate_spin,
        ):
            widget.setEnabled(enabled)

    def _update_measurement_toggle_button(self) -> None:
        update_measurement_toggle_button(self)

    def _toggle_measurement_run(self) -> None:
        toggle_measurement_run(self)

    def _start_measurement_run(self) -> None:
        start_measurement_run(self)

    def _pause_measurement_run(self) -> None:
        pause_measurement_run(self)

    def _stop_measurement_run(self) -> None:
        stop_measurement_run(self)

    def _start_measurement_file_compression(self, path: Path) -> None:
        from lspr_app.gui.acquisition_controller import _start_measurement_file_compression

        _start_measurement_file_compression(self, path)

    def _handle_measurement_file_compression_finished(self, result: object) -> None:
        from lspr_app.gui.acquisition_controller import _handle_measurement_file_compression_finished

        _handle_measurement_file_compression_finished(self, result)

    def _handle_measurement_file_compression_failed(self, message: str) -> None:
        from lspr_app.gui.acquisition_controller import _handle_measurement_file_compression_failed

        _handle_measurement_file_compression_failed(self, message)

    def _append_processed_trace_history(self, processed: Spectrum, fit: Spectrum | None) -> None:
        append_processed_trace_history(self, processed, fit)

    def _get_analysis_processed_spectrum(
        self,
        signal: Spectrum,
    ) -> tuple[Spectrum | None, Spectrum | None]:
        return get_analysis_processed_spectrum_for(self, signal)

    def _flush_measurement_frames(self, force: bool = False) -> None:
        flush_measurement_frames(self, force=force)

    def _compute_peak_metric_nm(self, processed: Spectrum, fit: Spectrum | None) -> float:
        return compute_peak_metric_nm_for(self, processed, fit)

    def _compute_trace_metrics(self, processed: Spectrum, fit: Spectrum | None) -> dict[str, float]:
        return compute_trace_metrics_for(self, processed, fit)

    def _compute_metric_nm(self, mode: str, processed: Spectrum, fit: Spectrum | None) -> float:
        return compute_metric_nm_for(self, mode, processed, fit)

    def _processing_cache_token(self, spectrum: Spectrum | None, settings: ProcessingSettings) -> tuple[object, ...] | None:
        return processing_cache_token_for(self, spectrum, settings)

    def _analysis_cache_token(
        self,
        processed: Spectrum | None,
        fit: Spectrum | None,
        settings: ProcessingSettings,
    ) -> tuple[object, ...] | None:
        return analysis_cache_token_for(self, processed, fit, settings)

    def _analysis_metrics_cache_token(
        self,
        processed: Spectrum | None,
        fit: Spectrum | None,
        settings: ProcessingSettings,
    ) -> tuple[object, ...] | None:
        return analysis_metrics_cache_token_for(self, processed, fit, settings)

    def _needs_gaussian_metric(self, settings: ProcessingSettings | None = None) -> bool:
        return needs_gaussian_metric_for(self, settings)

    def _get_dense_analysis_curve(
        self,
        processed: Spectrum | None,
        fit: Spectrum | None,
        settings: ProcessingSettings,
    ) -> tuple[np.ndarray, np.ndarray, dict[str, float | str]]:
        return get_dense_analysis_curve_for(self, processed, fit, settings)

    def _get_analysis_metrics(
        self,
        processed: Spectrum | None,
        fit: Spectrum | None,
        settings: ProcessingSettings | None = None,
    ) -> dict[str, object]:
        if settings is None:
            settings = self._current_processing_settings()
        return get_analysis_metrics_for(self, processed, fit, settings)

    def _get_processed_spectrum(
        self,
        spectrum: Spectrum | None,
        settings: ProcessingSettings,
    ) -> tuple[Spectrum | None, Spectrum | None]:
        return get_processed_spectrum_for(self, spectrum, settings)

    def _refresh_session_summary(self, force: bool = False) -> None:
        refresh_session_summary_for_runtime(self, force=force)

    def _refresh_session_statistics(self, force: bool = False) -> None:
        refresh_session_statistics_for_runtime(self, force=force)

    def _copy_session_stats_log(self) -> None:
        copy_session_stats_log_for(self)

    def _copy_session_stats_snapshot(self) -> None:
        copy_session_stats_snapshot_for(self)

    def _save_session_stats_log(self) -> None:
        destination = save_session_stats_log_for(self)
        if destination is None:
            self.status_label.setText("No session statistics available to save.")
            self._log_warning("No session statistics available to save.")
            return
        self.status_label.setText(f"Session statistics saved to {destination.name}")
        self._log_success(f"Session statistics saved: {destination.name}")

    def _toggle_session_stats_recording(self) -> None:
        self._set_session_stats_recording_active(not self._session_stats_recording_active)

    def _update_session_stats_recording_timer_interval(self) -> None:
        timer = getattr(self, "_session_stats_recording_timer", None)
        if timer is None:
            return
        rate_hz = float(self.live_rate_spin.value()) if hasattr(self, "live_rate_spin") else 1.0
        timer.setInterval(max(int(round(1000.0 / max(rate_hz, 1e-9))), 1))

    def _set_session_stats_recording_active(self, active: bool) -> None:
        active = bool(active)
        if active == bool(self._session_stats_recording_active):
            self._render_session_stats_recording_blink_indicator()
            return
        if active:
            self._session_stats_log.clear()
            self._session_stats_log_last_text = ""
            self._session_stats_log_last_capture_ts = 0.0
            self._session_stats_recording_started_at = datetime.now(timezone.utc)
            self._session_stats_recording_duration_s = None
            self._session_stats_recording_active = True
            self._session_stats_recording_blink_visible = True
            self._update_session_stats_recording_timer_interval()
            self._session_stats_recording_blink_timer.start()
            self._session_stats_recording_requested_at = perf_counter()
            self._capture_session_stats_recording_snapshot()
            self._log_info("Session log recording started.")
        else:
            self._session_stats_recording_active = False
            self._session_stats_recording_blink_timer.stop()
            self._session_stats_recording_timer.stop()
            self._session_stats_recording_requested_at = None
            started_at = self._session_stats_recording_started_at
            if started_at is not None:
                self._session_stats_recording_duration_s = max(
                    (datetime.now(timezone.utc) - started_at).total_seconds(),
                    0.0,
                )
            destination = save_session_stats_log_for(self)
            self._session_stats_recording_started_at = None
            self._session_stats_recording_blink_visible = True
            self._render_session_stats_recording_blink_indicator()
            if destination is not None:
                self.status_label.setText(f"Session log saved to {destination.name}")
                self._log_success(f"Session log saved: {destination.name}")
            else:
                self._log_warning("Session log recording stopped without data.")
            self._refresh_session_summary(force=True)
            self._refresh_session_statistics(force=True)

    def _capture_session_stats_recording_snapshot(self) -> None:
        if not self._session_stats_recording_active:
            return
        started = perf_counter()
        requested_at = getattr(self, "_session_stats_recording_requested_at", None)
        interval_ms = float(self._session_stats_recording_timer.interval()) if hasattr(self, "_session_stats_recording_timer") else 0.0
        if requested_at is not None:
            try:
                self._last_session_stats_recording_delay_ms = max((started - float(requested_at)) * 1000.0 - interval_ms, 0.0)
            except (TypeError, ValueError):
                self._last_session_stats_recording_delay_ms = None
        self._run_gui_callback_timed("session_stats_snapshot", lambda: self._refresh_session_statistics(force=True))
        self._last_session_stats_recording_snapshot_ms = (perf_counter() - started) * 1000.0
        self._session_stats_recording_requested_at = perf_counter()

    def _advance_session_stats_recording_blink_indicator(self) -> None:
        def _callback() -> None:
            timer = getattr(self, "_session_stats_recording_blink_timer", None)
            if timer is None or not timer.isActive():
                return
            self._session_stats_recording_blink_visible = not bool(getattr(self, "_session_stats_recording_blink_visible", True))
            self._render_session_stats_recording_blink_indicator()

        self._run_gui_callback_timed("session_stats_blink", _callback)

    def _render_session_stats_recording_blink_indicator(self) -> None:
        button = getattr(self, "session_stats_record_button", None)
        if button is None:
            return
        if bool(getattr(self, "_session_stats_recording_active", False)):
            if bool(getattr(self, "_session_stats_recording_blink_visible", True)):
                button.setIcon(transport_icon(self._theme_mode, "record"))
            else:
                button.setIcon(QIcon())
            button.setToolTip("Stop session log recording and save the text log to the project destination.")
            button.setChecked(True)
        else:
            button.setIcon(transport_icon(self._theme_mode, "record"))
            button.setToolTip("Start session log recording and save the text log to the experiment folder.")
            button.setChecked(False)

    def _hardware_init_steps(self) -> list[HardwareInitStep]:
        return [
            HardwareInitStep("spectrometer", "Spectrometer", self._spectrometer_init_step),
            HardwareInitStep("pump", "Pump controller", self._pump_init_step),
            HardwareInitStep("mswitch", "M-Switch", self._mswitch_init_step),
            HardwareInitStep("valve", "Valve controller", self._valve_init_step),
        ]

    def _spectrometer_init_step(self) -> HardwareInitStepResult:
        name = self._spectrometer.device_name()
        if isinstance(self._spectrometer, SimulatedSpectrometer):
            return HardwareInitStepResult(
                key="spectrometer",
                label="Spectrometer",
                state="simulation",
                message="Simulation backend ready.",
                connected=False,
                payload=name,
            )
        return HardwareInitStepResult(
            key="spectrometer",
            label="Spectrometer",
            state="ready",
            message=f"Spectrometer backend active: {name}.",
            connected=True,
            payload=name,
        )

    def _record_port_probe_event(
        self,
        *,
        role: str,
        port: str,
        assignment: str,
        action: str,
        result: str,
        message: str,
        owner: str,
        command: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        record_port_probe_event(
            phase="startup",
            port=port,
            owner=owner,
            role=role,
            assignment=assignment,
            action=action,
            command=command,
            result=result,
            duration_ms=duration_ms,
            message=message,
        )

    def _pump_init_step(self) -> HardwareInitStepResult:
        all_ports = RegloICCClient.list_ports()
        preferred_ports = [
            port
            for port in all_ports
            if get_port_assignment(port.device) == "pump" and should_probe_port_for_role(port.device, "pump")
        ]
        probable_ports = [
            port
            for port in all_ports
            if get_port_assignment(port.device) == "auto"
            and is_probable_reglo_port(port)
            and should_probe_port_for_role(port.device, "pump")
        ]
        ports = preferred_ports + [port for port in probable_ports if port not in preferred_ports]
        if not ports:
            return HardwareInitStepResult(
                key="pump",
                label="Pump controller",
                state="missing",
                message="No pump controller discovered.",
                connected=False,
                payload=[],
            )
        last_error: str | None = None
        assigned_error: str | None = None
        for port in ports:
            assignment = get_port_assignment(port.device)
            if not should_probe_port_for_role(port.device, "pump"):
                self._record_port_probe_event(
                    role="pump",
                    port=port.device,
                    assignment=assignment,
                    action="skip",
                    result="skipped",
                    message=f"Port {port.device} is assigned to {assignment} and is excluded from pump probing.",
                    owner="startup:pump",
                )
                continue
            started = perf_counter()
            try:
                probe = RegloICCClient.probe_port(port.device)
            except Exception as exc:
                last_error = str(exc)
                if assignment == "pump" and assigned_error is None:
                    assigned_error = f"Assigned pump port {port.device} did not respond as Reglo ICC: {exc}"
                self._record_port_probe_event(
                    role="pump",
                    port=port.device,
                    assignment=assignment,
                    action="probe",
                    result="fail",
                    message=str(exc),
                    owner="startup:pump",
                    command="0x!",
                    duration_ms=(perf_counter() - started) * 1000.0,
                )
                continue
            self._record_port_probe_event(
                role="pump",
                port=port.device,
                assignment=assignment,
                action="probe",
                result="success",
                message=probe.model or "Pump controller",
                owner="startup:pump",
                command="0x!",
                duration_ms=(perf_counter() - started) * 1000.0,
            )
            return HardwareInitStepResult(
                key="pump",
                label="Pump controller",
                state="discovered",
                message=f"Pump controller discovered on {probe.port}.",
                connected=False,
                probe=probe,
                payload=probe,
            )
        if assigned_error is not None:
            last_error = assigned_error if last_error is None else f"{assigned_error}; {last_error}"
        return HardwareInitStepResult(
            key="pump",
            label="Pump controller",
            state="error",
            message=last_error or "Pump controller scan completed with no usable device.",
            connected=False,
            error=last_error,
            payload=[],
        )

    def _mswitch_init_step(self) -> HardwareInitStepResult:
        try:
            devices = detect_amf_mswitch_devices()
        except Exception as exc:
            return HardwareInitStepResult(
                key="mswitch",
                label="M-Switch",
                state="error",
                message=f"M-Switch scan failed: {exc}",
                connected=False,
                error=str(exc),
                payload=[],
            )
        if not devices:
            return HardwareInitStepResult(
                key="mswitch",
                label="M-Switch",
                state="missing",
                message="M-Switch not discovered at startup.",
                connected=False,
                payload=[],
            )
        first = devices[0]
        port_name = getattr(first, "port", "unknown")
        return HardwareInitStepResult(
            key="mswitch",
            label="M-Switch",
            state="discovered",
            message=f"M-Switch discovered on {port_name}.",
            connected=False,
            probe=first,
            payload=devices,
        )

    def _valve_init_step(self) -> HardwareInitStepResult:
        all_ports = SerialController.list_ports()
        preferred_ports = [
            port
            for port in all_ports
            if get_port_assignment(port.device) == "valve" and should_probe_port_for_role(port.device, "valve")
        ]
        likely_ports = [
            port
            for port in all_ports
            if get_port_assignment(port.device) == "auto"
            and controller_port_priority(port) > 0
            and should_probe_port_for_role(port.device, "valve")
        ]
        ports = preferred_ports + [port for port in likely_ports if port not in preferred_ports]
        if not ports:
            return HardwareInitStepResult(
                key="valve",
                label="Valve controller",
                state="missing",
                message="No valve controller discovered.",
                connected=False,
                payload=None,
            )
        ports = sorted(ports, key=controller_port_priority, reverse=True)
        last_error: str | None = None
        assigned_error: str | None = None
        for port in ports:
            assignment = get_port_assignment(port.device)
            if not should_probe_port_for_role(port.device, "valve"):
                self._record_port_probe_event(
                    role="valve",
                    port=port.device,
                    assignment=assignment,
                    action="skip",
                    result="skipped",
                    message=f"Port {port.device} is assigned to {assignment} and is excluded from valve probing.",
                    owner="startup:valve",
                )
                continue
            started = perf_counter()
            try:
                client, probe = detect_valve_controller(port.device)
            except Exception as exc:
                last_error = str(exc)
                if assignment == "valve" and assigned_error is None:
                    assigned_error = f"Assigned valve port {port.device} did not respond as a valve controller: {exc}"
                self._record_port_probe_event(
                    role="valve",
                    port=port.device,
                    assignment=assignment,
                    action="probe",
                    result="fail",
                    message=str(exc),
                    owner="startup:valve",
                    duration_ms=(perf_counter() - started) * 1000.0,
                )
                continue
            try:
                client.close()
            except Exception:
                pass
            self._record_port_probe_event(
                role="valve",
                port=port.device,
                assignment=assignment,
                action="probe",
                result="success",
                message=getattr(probe, "model", "Valve controller") or "Valve controller",
                owner="startup:valve",
                duration_ms=(perf_counter() - started) * 1000.0,
            )
            return HardwareInitStepResult(
                key="valve",
                label="Valve controller",
                state="discovered",
                message=f"Valve controller discovered on {probe.port}.",
                connected=False,
                probe=probe,
                payload=probe,
            )
        return HardwareInitStepResult(
            key="valve",
            label="Valve controller",
            state="error",
            message=assigned_error or last_error or "Valve controller scan completed with no usable device.",
            connected=False,
            error=assigned_error or last_error,
            payload=None,
        )

    def _emit_hardware_init_progress(self, percent: int, text: str) -> None:
        self.hardware_init_progress.emit(max(0, min(int(percent), 100)), str(text))

    def _update_startup_loading_indicator(self, percent: int, _text: str) -> None:
        self._set_startup_loading_indicator(int(percent) < 100)

    def _set_startup_loading_indicator(self, active: bool) -> None:
        label = getattr(self, "_startup_loading_label", None)
        timer = getattr(self, "_startup_loading_timer", None)
        if label is None or timer is None:
            return
        if active:
            if not timer.isActive():
                self._startup_loading_frame_index = 0
                label.setFixedSize(22, 22)
                label.setVisible(True)
                self._render_startup_loading_indicator()
                timer.start()
        else:
            timer.stop()
            label.setVisible(False)

    def _set_recording_blink_indicator(self, active: bool) -> None:
        timer = getattr(self, "_recording_blink_timer", None)
        if timer is None:
            return
        if active:
            if not timer.isActive():
                self._recording_blink_visible = True
                self._render_recording_blink_indicator()
                timer.start()
        else:
            timer.stop()
            self._recording_blink_visible = True
            self._render_recording_blink_indicator()

    def _advance_recording_blink_indicator(self) -> None:
        def _callback() -> None:
            timer = getattr(self, "_recording_blink_timer", None)
            if timer is None or not timer.isActive():
                return
            self._recording_blink_visible = not bool(getattr(self, "_recording_blink_visible", True))
            self._render_recording_blink_indicator()

        self._run_gui_callback_timed("recording_blink", _callback)

    def _render_recording_blink_indicator(self) -> None:
        self._update_measurement_toggle_button()
        self._update_window_mode_label()

    def _advance_startup_loading_indicator(self) -> None:
        def _callback() -> None:
            label = getattr(self, "_startup_loading_label", None)
            if label is None:
                return
            frame_index = int(getattr(self, "_startup_loading_frame_index", 0))
            frame_index = (frame_index + 1) % 12
            self._startup_loading_frame_index = frame_index
            self._render_startup_loading_indicator()

        self._run_gui_callback_timed("startup_loading", _callback)

    def _render_startup_loading_indicator(self) -> None:
        label = getattr(self, "_startup_loading_label", None)
        if label is None:
            return
        size = 22
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor(0, 0, 0, 0))

        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            center = size / 2.0
            ring_radius = 8.8
            dot_radius = 2.0
            base_color = QColor("#39c7ba")
            trail_alphas = [255, 234, 206, 176, 146, 120, 96, 76, 58, 44, 32, 22]

            painter.setPen(Qt.PenStyle.NoPen)

            frame_index = int(getattr(self, "_startup_loading_frame_index", 0)) % 12
            for index in range(12):
                distance = (index - frame_index) % 12
                alpha = trail_alphas[distance]
                color = QColor(base_color)
                color.setAlpha(alpha)
                angle = (index / 12.0) * 6.283185307179586 - 1.5707963267948966
                x_pos = center + ring_radius * math.cos(angle)
                y_pos = center + ring_radius * math.sin(angle)
                painter.setBrush(color)
                painter.drawEllipse(int(round(x_pos - dot_radius)), int(round(y_pos - dot_radius)), 4, 4)
        finally:
            painter.end()
        label.setPixmap(pixmap)

    def _finish_hardware_initialization(self, text: str = "Hardware initialization scan finished.") -> None:
        finish_hardware_initialization_for(self, text)

    def _start_hardware_initialization(self) -> None:
        start_hardware_initialization_for(self)

    def _handle_hardware_init_step(self, result: object) -> None:
        handle_hardware_init_step_for(self, result)

    def _handle_hardware_init_finished(self, result: object) -> None:
        handle_hardware_init_finished_for(self, result)

    def _disconnect_all_devices(self) -> None:
        disconnect_all_devices_for(self)

    def _sync_hardware_menu_actions(self) -> None:
        sync_hardware_menu_actions_for(self)

    def _sync_experiment_control_startup_ports(self) -> None:
        sync_experiment_control_startup_ports_for(self)

    def _build_menu_bar(self) -> None:
        menu_bar = build_menu_bar(self)
        title_widget = build_title_bar(self, menu_bar, self._brand_icon_path)
        self._title_bar_widget = title_widget
        self.setMenuWidget(title_widget)
        refresh_hw_device_status_strip(self)
        sync_window_control_icons(self)

    def _update_window_mode_label(self) -> None:
        update_window_mode_label(self)

    def eventFilter(self, obj, event) -> bool:  # pragma: no cover - GUI runtime path
        if obj is getattr(self, "project_destination_edit", None):
            event_type = event.type()
            if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._choose_recording_project_destination()
                return True
        if obj is getattr(self, "trace_stats_label", None):
            event_type = event.type()
            if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._cycle_trace_stats_metric()
                return True
        if obj is getattr(self, "_processed_spectra_header_label", None):
            event_type = event.type()
            if event_type == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                self._activate_experimental_control_view()
                return True
        experiment_header = getattr(getattr(self, "_experiment_control_window", None), "_experiment_control_header_label", None)
        if obj is experiment_header:
            event_type = event.type()
            if event_type == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                self._activate_spectra_view()
                return True
        if event.type() == QEvent.Type.Paint:
            startup_painted = False
            startup_widget_label = None
            if obj is getattr(self, "_main_content_widget", None):
                startup_widget_label = "main_content"
                startup_painted = True
            elif obj is getattr(self, "_spectra_block", None):
                startup_widget_label = "spectra_block"
                startup_painted = True
            elif obj is getattr(self, "_sensorgram_block", None):
                startup_widget_label = "sensorgram_block"
                startup_painted = True
            else:
                spectrum_viewport = getattr(getattr(self, "spectrum_plot", None), "viewport", None)
                if spectrum_viewport is not None and obj is spectrum_viewport():
                    startup_widget_label = "spectrum_viewport"
                    startup_painted = True
                else:
                    trace_viewport = getattr(getattr(self, "trace_plot", None), "viewport", None)
                    if trace_viewport is not None and obj is trace_viewport():
                        startup_widget_label = "trace_viewport"
                        startup_painted = True
            if startup_painted:
                startup_show_t0 = getattr(self, "_startup_show_requested_t0", None)
                if startup_show_t0 is not None:
                    startup_elapsed_ms = (perf_counter() - startup_show_t0) * 1000.0
                    if not getattr(self, "_startup_widget_paint_reported", False):
                        self._startup_widget_paint_reported = set()
                    painted = getattr(self, "_startup_widget_paint_reported", set())
                    if startup_widget_label not in painted:
                        painted.add(startup_widget_label)
                        self._startup_widget_paint_reported = painted
                        self._log_info(
                            f"Startup +{startup_elapsed_ms:.1f} ms: first paint on {startup_widget_label}"
                        )
        spectrum_viewport = getattr(getattr(self, "spectrum_plot", None), "viewport", None)
        if spectrum_viewport is not None and obj is spectrum_viewport():
            event_type = event.type()
        trace_viewport = getattr(getattr(self, "trace_plot", None), "viewport", None)
        if trace_viewport is not None and obj is trace_viewport():
            event_type = event.type()
            if event_type in {QEvent.Type.Resize, QEvent.Type.Show}:
                self._sync_trace_heatmap_notice_overlay()
        if obj is self._title_bar_widget:
            event_type = event.type()
            if event_type == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                self._toggle_window_max_restore()
                return True
            if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._title_bar_drag_active = True
                self._title_bar_drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event_type == QEvent.Type.MouseMove and self._title_bar_drag_active:
                if not self.isMaximized() and event.buttons() & Qt.MouseButton.LeftButton:
                    self.move(event.globalPosition().toPoint() - self._title_bar_drag_offset)
                return True
            if event_type in {QEvent.Type.MouseButtonRelease, QEvent.Type.Leave}:
                self._title_bar_drag_active = False
        return super().eventFilter(obj, event)

    def changeEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.type() == QEvent.Type.WindowStateChange:
            sync_window_control_icons(self)
        super().changeEvent(event)

    def _show_quick_help_dialog(self) -> None:
        QMessageBox.information(
            self,
            "Quick help",
            "Status readouts:\n"
            "src = source acquisition rate\n"
            "disp = GUI display refresh rate\n"
            "proc = processing time per spectrum\n"
            "head = display-period / processing-time\n"
            "skip = dropped GUI updates per second\n"
            "acq = acquisition latency\n"
            "gap = interval between source frames\n"
            "ovh = acquisition latency minus expected budget\n"
            "show = last displayed frame/window summary",
        )

    def _show_sensorgram_plot_settings_dialog(self) -> None:
        show_sensorgram_plot_settings_dialog_for(self)

    def _show_about_dialog(self) -> None:
        QMessageBox.about(
            self,
            "About LSPR Acquisition",
            f"LSPR Acquisition {__version__}\n"
            "Spectrometer acquisition and experiment control UI.\n"
            "This view is optimized for fast acquisition first, display second.",
        )

    def _show_preferences_dialog(self) -> None:
        show_preferences_dialog_for(self)

    def _show_shortcuts_dialog(self) -> None:
        QMessageBox.information(
            self,
            "Shortcuts",
            build_shortcuts_help_text(),
        )

    def _show_diagnostics_legend_dialog(self) -> None:
        QMessageBox.information(
            self,
            "Diagnostics legend",
            f"{self._diagnostics.startup_summary_text()}\n\n"
            "Status strip:\n"
            "src = source acquisition rate\n"
            "disp = GUI display refresh rate\n"
            "proc = processing time per spectrum\n"
            "head = display-period / processing-time\n"
            "skip = dropped GUI updates per second\n\n"
            "Telemetry strip:\n"
            "acq = acquisition latency\n"
            "gap = interval between source frames\n"
            "ovh = acquisition latency minus expected budget\n"
            "show = last displayed frame/window summary",
        )

    def _show_connected_devices_dialog(self) -> None:
        show_hardware_inventory_dialog(self)

    def _show_usb_probe_diagnostics_dialog(self) -> None:
        show_usb_probe_diagnostics_dialog(self)

    def _set_processing_debug_mode_enabled(self, enabled: bool) -> None:
        self._processing_debug_mode_enabled = bool(enabled)
        set_processing_debug_mode_enabled(self._processing_debug_mode_enabled)
        save_app_setting("processing_debug_mode", self._processing_debug_mode_enabled)
        if self._live_processing_worker is not None:
            self._live_processing_worker.update_debug_mode(self._processing_debug_mode_enabled)
        state_text = "enabled" if self._processing_debug_mode_enabled else "disabled"
        self.status_label.setText(f"Debug mode {state_text}.")
        self._log_info(f"Processing debug mode {state_text}.")
        self._request_deferred_ui_refresh(live_estimate=True)

    def _install_shortcuts(self) -> None:
        shortcuts = [
            (QKeySequence("F1"), self._show_quick_help_dialog),
            (QKeySequence("Ctrl+/"), self._show_shortcuts_dialog),
            (QKeySequence("Ctrl+L"), self._clear_trace_history),
            (QKeySequence("Ctrl+S"), self._save_processing_settings_dialog),
            (QKeySequence("Ctrl+O"), self._load_processing_settings_dialog),
            (QKeySequence("Ctrl+E"), self._export_current_plot),
            (QKeySequence("Ctrl+Space"), self._toggle_measurement_run),
            (QKeySequence("Ctrl+Left"), lambda: self._move_flow_step(-1)),
            (QKeySequence("Ctrl+Right"), lambda: self._move_flow_step(1)),
        ]
        self._shortcuts: list[QShortcut] = []
        for sequence, callback in shortcuts:
            shortcut = QShortcut(sequence, self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

    def _move_flow_step(self, delta: int) -> None:
        self._open_experiment_control_window()
        if self._experiment_control_window is None:
            self.status_label.setText("Experiment control window is unavailable.")
            self._log_warning("Experiment control window is unavailable for step navigation.")
            return
        if hasattr(self._experiment_control_window, "_move_to_relative_experiment_control_step"):
            self._experiment_control_window._move_to_relative_experiment_control_step(delta)  # noqa: SLF001
            direction = "next" if delta > 0 else "previous"
            self.status_label.setText(f"Moved to {direction} pump-plan step.")
            self._log_info(f"Moved to {direction} pump-plan step.")

    def _call_experiment_control_window_action(self, method_name: str) -> bool:
        self._open_experiment_control_window()
        if self._experiment_control_window is None:
            self.status_label.setText("Experiment control window is unavailable.")
            self._log_warning("Experiment control window is unavailable.")
            return False
        method = getattr(self._experiment_control_window, method_name, None)
        if not callable(method):
            self.status_label.setText("Experiment control action is unavailable.")
            self._log_warning(f"Experiment control action '{method_name}' is unavailable.")
            return False
        method()  # noqa: SLF001
        return True

    def _toggle_flow_run_hold(self) -> None:
        if self._call_experiment_control_window_action("_toggle_plan_run_hold"):
            self._log_info("Experimental control plan run/hold toggled.")

    def _stop_experiment_plan(self) -> None:
        if self._call_experiment_control_window_action("_stop_plan"):
            self._log_info("Experiment plan stop requested.")

    def _stop_all_flow_channels(self) -> None:
        if self._call_experiment_control_window_action("_stop_all_channels"):
            self._log_info("Experimental control channel stop-all requested.")

    def _read_flow_status(self) -> None:
        if self._call_experiment_control_window_action("_read_live_status"):
            self._log_info("Experimental control status read requested.")

    def _request_deferred_ui_refresh(
        self,
        *,
        stats: bool = False,
        trace_plot: bool = False,
        summary: bool = False,
        telemetry: bool = False,
        live_estimate: bool = False,
        session_stats: bool = False,
        trace_label: str | None = None,
    ) -> None:
        request_deferred_ui_refresh_for_runtime(
            self,
            stats=stats,
            trace_plot=trace_plot,
            summary=summary,
            telemetry=telemetry,
            live_estimate=live_estimate,
            session_stats=session_stats,
            trace_label=trace_label,
        )

    def _request_plot_refresh(self, delay_ms: float | None = None) -> None:
        request_plot_refresh_for_runtime(self, delay_ms=delay_ms)

    def _temporal_history_token(self, processed: Spectrum | None) -> tuple[object, ...] | None:
        if processed is None:
            return None
        settings = self._current_processing_settings()
        return (
            self.PLOT_MODES[self.plot_selector.currentText()],
            self._source_mode,
            len(processed.wavelengths_nm),
            float(processed.wavelengths_nm[0]) if len(processed.wavelengths_nm) else None,
            float(processed.wavelengths_nm[-1]) if len(processed.wavelengths_nm) else None,
            settings.temporal_smoothing,
            settings.crop_method,
            settings.crop_fraction,
            settings.fit_method,
            settings.fit_window_width_nm,
        )

    def _apply_temporal_smoothing(self, processed: Spectrum | None) -> Spectrum | None:
        if processed is None:
            self._temporal_processed_history.clear()
            self._temporal_history_key = None
            return None

        settings = self._current_processing_settings()
        window = max(int(getattr(settings, "temporal_smoothing", 1)), 1)
        key = self._temporal_history_token(processed)
        if key != self._temporal_history_key:
            self._temporal_processed_history.clear()
            self._temporal_history_key = key

        self._temporal_processed_history.append(processed)
        if len(self._temporal_processed_history) > window:
            self._temporal_processed_history = self._temporal_processed_history[-window:]
        if window <= 1 or len(self._temporal_processed_history) == 1:
            return processed

        stack = np.vstack([item.values for item in self._temporal_processed_history])
        averaged_values = np.nanmean(stack, axis=0)
        return Spectrum(
            wavelengths_nm=processed.wavelengths_nm.copy(),
            values=averaged_values,
            y_label=processed.y_label,
            acquired_at=processed.acquired_at,
            metadata={
                **processed.metadata,
                "temporal_smoothing": window,
                "temporal_average_count": len(self._temporal_processed_history),
            },
        )

    def _enqueue_plot_processing(self) -> None:
        enqueue_plot_processing_for(self)

    def _start_plot_processing_task(self, request: ProcessingRequest) -> None:
        start_plot_processing_task_for(self, request)

    def _handle_plot_processing_result(self, result: ProcessingResult) -> None:
        handle_plot_processing_result_for(self, result)

    def _sensorgram_heatmap_archive_request_signature(self) -> tuple[object, ...] | None:
        return sensorgram_heatmap_archive_request_signature(self)

    def _set_sensorgram_heatmap_notice(self, title: str, detail: str) -> None:
        set_sensorgram_heatmap_notice(self, title, detail)

    def _start_sensorgram_reload_spinner(self) -> None:
        timer = getattr(self, "_sensorgram_reload_button_spinner_timer", None)
        if timer is None or not hasattr(self, "sensorgram_reload_button"):
            return
        self._sensorgram_reload_button_spinner_angle = 0
        self._advance_sensorgram_reload_spinner()
        timer.start()

    def _stop_sensorgram_reload_spinner(self) -> None:
        timer = getattr(self, "_sensorgram_reload_button_spinner_timer", None)
        if timer is not None:
            timer.stop()
        base_icon = getattr(self, "_sensorgram_reload_button_base_icon", None)
        if base_icon is not None and hasattr(self, "sensorgram_reload_button"):
            self.sensorgram_reload_button.setIcon(base_icon)

    def _advance_sensorgram_reload_spinner(self) -> None:
        button = getattr(self, "sensorgram_reload_button", None)
        base_icon = getattr(self, "_sensorgram_reload_button_base_icon", None)
        if button is None or base_icon is None:
            return
        try:
            size = max(int(button.iconSize().width()), int(button.iconSize().height()), 24)
            pixmap = base_icon.pixmap(size, size)
            if pixmap.isNull():
                return
            transform = QTransform().rotate(float(getattr(self, "_sensorgram_reload_button_spinner_angle", 0)))
            rotated = pixmap.transformed(transform, Qt.TransformationMode.SmoothTransformation)
            button.setIcon(QIcon(rotated))
            self._sensorgram_reload_button_spinner_angle = (int(self._sensorgram_reload_button_spinner_angle) + 30) % 360
        except Exception:
            pass

    def _start_sensorgram_heatmap_archive_task(self, request_token: tuple[object, ...]) -> None:
        start_sensorgram_heatmap_archive_task(self, request_token)

    def _request_sensorgram_heatmap_archive(self) -> None:
        request_sensorgram_heatmap_archive(self)

    def _handle_sensorgram_heatmap_archive_result(self, result: HeatmapArchiveLoadResult) -> None:
        handle_sensorgram_heatmap_archive_result(self, result)

    def _handle_sensorgram_heatmap_archive_failed(self, message: str) -> None:
        handle_sensorgram_heatmap_archive_failed(self, message)

    def _sensorgram_metric_archive_reload_request_signature(
        self,
    ) -> tuple[object, ...] | None:
        archive_path = getattr(self, "_metric_archive_path", None)
        if archive_path is None:
            return None
        try:
            archive_path = str(Path(archive_path).expanduser())
        except Exception:
            archive_path = str(archive_path)
        selected_metrics = tuple(sorted(str(name) for name in self._selected_trace_metrics()))
        return (
            archive_path,
            int(self._source_epoch),
            selected_metrics,
            int(getattr(self, "_plot_display_points", 0)),
        )

    def _start_sensorgram_metric_archive_reload_task(
        self,
        request_token: tuple[object, ...],
    ) -> None:
        self._start_sensorgram_reload_spinner()
        start_sensorgram_metric_archive_reload_task(self, request_token)

    def _request_sensorgram_metric_archive_reload(self) -> None:
        request_sensorgram_metric_archive_reload(self)

    def _request_absolute_sensorgram_metric_archive_reload(self) -> None:
        request_absolute_sensorgram_metric_archive_reload(self)

    def _start_next_pending_metric_reload_if_any(self, result: MetricArchiveReloadResult) -> None:
        next_token = self._sensorgram_metric_archive_reload_pending_token
        self._sensorgram_metric_archive_reload_pending_token = None
        if next_token is not None and next_token != result.request_token:
            start_sensorgram_metric_archive_reload_task(self, next_token)

    def _handle_absolute_sensorgram_metric_archive_reload_result(self, result: MetricArchiveReloadResult) -> None:
        from lspr_app.gui.main_window_sensorgram_archive import handle_absolute_sensorgram_metric_archive_reload_result

        handle_absolute_sensorgram_metric_archive_reload_result(self, result)

    def _handle_sensorgram_metric_archive_reload_result(self, result: MetricArchiveReloadResult) -> None:
        self._stop_sensorgram_reload_spinner()
        self._sensorgram_metric_archive_reload_loading = False
        self._sensorgram_metric_archive_reload_task = None
        if self._closing:
            self._sensorgram_metric_archive_reload_pending_token = None
            return
        if result.request_token != self._sensorgram_metric_archive_reload_request_token:
            self._start_next_pending_metric_reload_if_any(result)
            return
        self._handle_absolute_sensorgram_metric_archive_reload_result(result)
        self._start_next_pending_metric_reload_if_any(result)

    def _handle_sensorgram_metric_archive_reload_failed(self, message: str) -> None:
        self._stop_sensorgram_reload_spinner()
        self._sensorgram_metric_archive_reload_loading = False
        self._sensorgram_metric_archive_reload_task = None
        self._sensorgram_metric_archive_reload_pending_token = None
        self.status_label.setText(f"Sensorgram reload failed: {message}")
        self._log_error(f"Sensorgram measurement-file reload failed: {message}")

    def _reload_sensorgram_history(self) -> None:
        content_mode = self._normalize_sensorgram_content_mode(getattr(self, "_sensorgram_content_mode", "metric"))
        if content_mode == "heatmap":
            if bool(getattr(self, "_live_active", False)):
                self._set_sensorgram_heatmap_notice(
                    "Heatmap reload",
                    "Live heatmap is disabled for performance. Use Freeze/Review to generate heatmap from the saved measurement file.",
                )
                return
            self._request_sensorgram_heatmap_archive()
            return
        if hasattr(self, "trace_heatmap_notice_item"):
            self.trace_heatmap_notice_item.setVisible(False)
        self._request_absolute_sensorgram_metric_archive_reload()

    def _flush_deferred_ui_refreshes(self) -> None:
        flush_deferred_ui_refreshes_for_runtime(self)

    def _flush_deferred_display_refreshes(self) -> None:
        flush_deferred_display_refreshes_for_runtime(self)

    def _flush_deferred_metric_refreshes(self) -> None:
        flush_deferred_metric_refreshes_for_runtime(self)

    def _flush_deferred_stats_refreshes(self) -> None:
        flush_deferred_stats_refreshes_for_runtime(self)

    def _flush_plot_refreshes(self) -> None:
        flush_plot_refreshes_for_runtime(self)

    def _refresh_plot(self) -> None:
        refresh_plot_for(self)

    def _handle_residual_toggle(self, visible: bool) -> None:
        handle_residual_toggle_for(self, visible)

    def _update_poly_warning_indicator(self, fit: Spectrum | None) -> None:
        update_poly_warning_indicator_for(self, fit)

    def _set_plots_frozen(self, frozen: bool) -> None:
        set_plots_frozen_for(self, frozen)

    def _set_sensorgram_frozen(self, frozen: bool) -> None:
        set_sensorgram_frozen_for(self, frozen)

    def _clear_trace_history(self) -> None:
        clear_trace_history_for(self)

    def _autoscale_spectrum_plot(self) -> None:
        autoscale_spectrum_plot_for(self)
        apply_processing_range_to_spectrum_plot_for(self)

    def _autoscale_trace_plot(self) -> None:
        self._trace_view_locked = False
        autoscale_metric_plot_for(self, force=not bool(getattr(self, "_metric_autoscale_pending", False)))

    def _update_residual_view_geometry(self) -> None:
        update_residual_view_geometry(self)

    def _autoscale_residual_axis(self) -> None:
        autoscale_residual_axis(self)

    def _update_residual_axis_visibility(self, visible: bool | None = None) -> None:
        update_residual_axis_visibility(self, visible)

    def _clear_residual_display(self) -> None:
        clear_residual_display(self)

    def _render_residual_display(self, x_values: np.ndarray, residual_values: np.ndarray) -> None:
        render_residual_display(self, x_values, residual_values)

    def _request_trace_autoscale(self) -> None:
        request_metric_autoscale_for(self)

    def _handle_spectrum_mouse_moved(self, event) -> None:
        handle_spectrum_mouse_moved_for(self, event)

    def _handle_trace_mouse_moved(self, event) -> None:
        handle_metric_mouse_moved_for(self, event)

    def _refresh_spectrum_plot(self, processed: Spectrum | None, fit: Spectrum | None) -> None:
        if self._plots_frozen:
            return
        view_x_min = view_x_max = view_width_px = None
        try:
            view_box = self.spectrum_plot.getPlotItem().vb
            view_range = view_box.viewRange()
            x_range = view_range[0]
            view_x_min = float(x_range[0])
            view_x_max = float(x_range[1])
            scene_rect = view_box.sceneBoundingRect()
            if scene_rect is not None:
                view_width_px = float(scene_rect.width())
                if not np.isfinite(view_width_px):
                    view_width_px = None
        except Exception:
            view_x_min = view_x_max = view_width_px = None

        residual_visible = bool(fit is not None and self.show_residual_button.isChecked())
        show_gaussian = self._needs_gaussian_metric()
        render_key = spectrum_render_cache_key(
            processed,
            fit,
            view_x_min=view_x_min,
            view_x_max=view_x_max,
            view_width_px=view_width_px,
            residual_visible=residual_visible,
            show_gaussian=show_gaussian,
            spectrum_tracking_mode=self._current_processing_settings().spectrum_tracking_mode,
        )
        if render_key == self._spectrum_render_cache_key:
            return
        self._spectrum_render_cache_key = render_key
        self._visible_processed_plot = processed
        self._visible_fit_plot = fit
        if processed is None:
            self.spectrum_curve.setData([], [])
            self.fit_curve.setData([], [])
            self._clear_residual_display()
            self.fit_region_item.hide()
            self.max_marker.setData([], [])
            self.poly_marker.setData([], [])
            self.gaussian_marker.setData([], [])
            self.centroid_marker.setData([], [])
            self.spectrum_plot.setLabel("left", "Signal")
            self._update_residual_axis_visibility(False)
            self._spectrum_processing_region_bounds = None
            self._spectrum_fit_region_bounds = None
            self._last_spectrum_curve_update_ms = None
            self._last_spectrum_fit_update_ms = None
            self._last_spectrum_marker_update_ms = None
            self._last_spectrum_residual_update_ms = None
            return

        low = float(np.min(processed.wavelengths_nm))
        high = float(np.max(processed.wavelengths_nm))
        processing_bounds = (low, high)
        if processing_bounds != self._spectrum_processing_region_bounds:
            self.processing_region_item.setRegion(processing_bounds)
            self._spectrum_processing_region_bounds = processing_bounds

        display_x, display_y = downsample_spectrum_series_for_view(
            np.asarray(processed.wavelengths_nm, dtype=np.float64),
            np.asarray(processed.values, dtype=np.float64),
            view_x_min=view_x_min,
            view_x_max=view_x_max,
            view_width_px=view_width_px,
        )
        line_step_mode = getattr(self, "_sensorgram_line_step_mode", None)
        curve_started = perf_counter()
        if line_step_mode == "spline":
            spline_x, spline_y = _spline_render_series(display_x, display_y)
            self.spectrum_curve.setData(spline_x, spline_y, skipFiniteCheck=True, stepMode=False)
        elif line_step_mode is None:
            self.spectrum_curve.setData(display_x, display_y, skipFiniteCheck=True, stepMode=False)
        else:
            self.spectrum_curve.setData(display_x, display_y, skipFiniteCheck=True, stepMode=line_step_mode)
        self._last_spectrum_curve_update_ms = (perf_counter() - curve_started) * 1000.0
        if fit is not None:
            fit_values = np.asarray(fit.values, dtype=np.float64)
            fit_low = float(fit.metadata.get("fit_window_min_nm", np.min(fit.wavelengths_nm)))
            fit_high = float(fit.metadata.get("fit_window_max_nm", np.max(fit.wavelengths_nm)))
            display_fit_x, _ = clip_series_to_window(
                display_x,
                display_y,
                window_min=fit_low,
                window_max=fit_high,
            )
            if len(display_fit_x) > 0 and len(fit.wavelengths_nm) > 0:
                display_fit_y = np.interp(
                    display_fit_x,
                    np.asarray(fit.wavelengths_nm, dtype=np.float64),
                    fit_values,
                )
            else:
                display_fit_x = np.empty(0, dtype=np.float64)
                display_fit_y = fit_values[:0]
            fit_started = perf_counter()
            if line_step_mode == "spline":
                fit_curve_x, fit_curve_y = _spline_render_series(display_fit_x, display_fit_y)
                self.fit_curve.setData(fit_curve_x, fit_curve_y, skipFiniteCheck=True, stepMode=False)
            elif line_step_mode is None:
                self.fit_curve.setData(display_fit_x, display_fit_y, skipFiniteCheck=True, stepMode=False)
            else:
                self.fit_curve.setData(display_fit_x, display_fit_y, skipFiniteCheck=True, stepMode=line_step_mode)
            self._last_spectrum_fit_update_ms = (perf_counter() - fit_started) * 1000.0
            self.fit_region_item.show()
            residual_started = perf_counter()
            if residual_visible:
                residual_base = np.interp(
                    display_fit_x,
                    np.asarray(processed.wavelengths_nm, dtype=np.float64),
                    np.asarray(processed.values, dtype=np.float64),
                )
                with np.errstate(divide="ignore", invalid="ignore"):
                    residual_values = np.where(
                        np.abs(display_fit_y) > 1e-12,
                        ((residual_base - display_fit_y) / display_fit_y) * 100.0,
                        np.nan,
                    )
                self._render_residual_display(display_fit_x, residual_values)
                if not getattr(self, "_residual_axis_autoscaled", False):
                    self._autoscale_residual_axis()
            else:
                self._clear_residual_display()
            self._last_spectrum_residual_update_ms = (perf_counter() - residual_started) * 1000.0
            fit_bounds = (fit_low, fit_high)
            if fit_bounds != self._spectrum_fit_region_bounds:
                self.fit_region_item.setRegion(fit_bounds)
                self._spectrum_fit_region_bounds = fit_bounds
        else:
            fit_started = perf_counter()
            self.fit_curve.setData([], [])
            self._last_spectrum_fit_update_ms = (perf_counter() - fit_started) * 1000.0
            self.fit_region_item.hide()
            residual_started = perf_counter()
            self._clear_residual_display()
            self._last_spectrum_residual_update_ms = (perf_counter() - residual_started) * 1000.0
            fit_bounds = (low, low)
            if fit_bounds != self._spectrum_fit_region_bounds:
                self.fit_region_item.setRegion(fit_bounds)
                self._spectrum_fit_region_bounds = fit_bounds
        self._update_residual_axis_visibility(residual_visible)
        self.spectrum_plot.setLabel("left", processed.y_label)
        marker_started = perf_counter()
        finite_indices = np.flatnonzero(np.isfinite(processed.values))
        if len(finite_indices) == 0:
            self.max_marker.setData([], [])
            self.poly_marker.setData([], [])
            self.centroid_marker.setData([], [])
            self.gaussian_marker.setData([], [])
            self._last_spectrum_marker_update_ms = (perf_counter() - marker_started) * 1000.0
            return
        max_index = int(finite_indices[int(np.argmax(np.asarray(processed.values, dtype=np.float64)[finite_indices]))])
        max_x = float(processed.wavelengths_nm[max_index])
        max_y = float(processed.values[max_index])
        poly_x = self._compute_metric_nm("poly_max", processed, fit)
        if fit is not None and len(fit.wavelengths_nm) > 0:
            poly_y = float(np.interp(poly_x, fit.wavelengths_nm, fit.values))
        else:
            poly_y = float(np.interp(poly_x, processed.wavelengths_nm, processed.values))
        show_gaussian = self._needs_gaussian_metric()
        if show_gaussian:
            gaussian_x = self._compute_metric_nm("gaussian_center", processed, fit)
            if fit is not None and fit.metadata.get("fit_method") == "gaussian" and len(fit.wavelengths_nm) > 0:
                gaussian_y = float(np.interp(gaussian_x, fit.wavelengths_nm, fit.values))
            else:
                gaussian_y = float(np.interp(gaussian_x, processed.wavelengths_nm, processed.values))
            if np.isfinite(gaussian_x) and np.isfinite(gaussian_y):
                self.gaussian_marker.setData([{"pos": (float(gaussian_x), float(gaussian_y)), "data": "gaussian"}])
                self.gaussian_marker.show()
            else:
                self.gaussian_marker.setData([], [])
                self.gaussian_marker.hide()
        else:
            self.gaussian_marker.setData([], [])
            self.gaussian_marker.hide()
        centroid_x = self._compute_centroid_nm(processed, fit)
        centroid_y = float(np.interp(centroid_x, processed.wavelengths_nm, processed.values))
        self._set_scatter_marker(self.max_marker, max_x, max_y, "max")
        self._set_scatter_marker(self.poly_marker, poly_x, poly_y, "poly")
        self._set_scatter_marker(self.centroid_marker, centroid_x, centroid_y, "centroid")
        self._last_spectrum_marker_update_ms = (perf_counter() - marker_started) * 1000.0
        self._notify_startup_data_rendered("spectrum")
    def _set_scatter_marker(self, marker: pg.ScatterPlotItem, x: float, y: float, label: str) -> None:
        if not (np.isfinite(x) and np.isfinite(y)):
            marker.setData([], [])
            return
        marker.setData([{"pos": (float(x), float(y)), "data": label}])

    def _refresh_trace_plot(self, trace_label: str) -> None:
        refresh_metric_plot_for(self, trace_label)
        self._sync_sensorgram_control_step_overlay()
        self._notify_startup_data_rendered("trace")

    def _render_trace_series(
        self,
        history: dict[str, list[tuple[object, float]]],
        clock_mode: bool,
    ) -> None:
        render_metric_series_for(self, history, clock_mode)

    def _handle_trace_view_range_changed(self, *_args) -> None:
        handle_trace_view_range_changed_for_sensorgram(self, *_args)

    def _sync_trace_heatmap_notice_overlay(self) -> None:
        notice = getattr(self, "trace_heatmap_notice_item", None)
        trace_plot = getattr(self, "trace_plot", None)
        if notice is None or trace_plot is None:
            return
        try:
            viewport = trace_plot.viewport()
            if viewport is None:
                return
            viewport_rect = viewport.rect()
            if viewport_rect.isEmpty():
                return
            max_width = max(min(viewport_rect.width() - 40, 340), 220)
            notice.setMaximumWidth(max_width)
            notice.adjustSize()
            notice_width = notice.width()
            notice_height = notice.height()
            if notice_width <= 0 or notice_height <= 0:
                return
            x_pos = max((viewport_rect.width() - notice_width) // 2, 0)
            y_pos = max((viewport_rect.height() - notice_height) // 2, 0)
            notice.move(int(x_pos), int(y_pos))
            notice.raise_()
        except Exception:
            return

    def _sensorgram_control_step_overlay_current_elapsed_s(self) -> float | None:
        return sensorgram_control_step_overlay_current_elapsed_s_for_sensorgram(self)

    def _sensorgram_control_step_overlay_brush(self, segment: dict[str, object]) -> object:
        return sensorgram_control_step_overlay_brush_for_sensorgram(self, segment)

    def _normalize_sensorgram_control_step_overlay_style(self, value: object | None = None) -> str:
        return normalize_sensorgram_control_step_overlay_style_for_sensorgram(self, value)

    def _normalize_sensorgram_control_step_overlay_position(self, value: object | None = None) -> str:
        return normalize_sensorgram_control_step_overlay_position_for_sensorgram(self, value)

    def _sensorgram_control_step_overlay_view_bounds(self) -> tuple[float, float, float, float] | None:
        return sensorgram_control_step_overlay_view_bounds_for_sensorgram(self)

    def _sensorgram_control_step_overlay_remove_items(self, trace_plot, items: list[object]) -> None:
        sensorgram_control_step_overlay_remove_items_for_sensorgram(self, trace_plot, items)

    def _sensorgram_control_step_overlay_set_visible(self, item: object, visible: bool) -> None:
        sensorgram_control_step_overlay_set_visible_for_sensorgram(item, visible)

    def _sensorgram_control_step_overlay_set_tooltip(self, item: object, tooltip: str) -> None:
        sensorgram_control_step_overlay_set_tooltip_for_sensorgram(item, tooltip)

    def _sensorgram_control_step_overlay_clip_segment(
        self,
        segment: dict[str, object],
        x_min: float,
        x_max: float,
    ) -> dict[str, object] | None:
        return sensorgram_control_step_overlay_clip_segment_for_sensorgram(self, segment, x_min, x_max)

    def _sensorgram_control_step_overlay_bar_rect(
        self,
        *,
        segment: dict[str, object],
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
    ) -> QRectF:
        return sensorgram_control_step_overlay_bar_rect_for_sensorgram(
            self,
            segment=segment,
            x_min=x_min,
            x_max=x_max,
            y_min=y_min,
            y_max=y_max,
        )

    def _sensorgram_control_step_overlay_label_font(self) -> QFont:
        return sensorgram_control_step_overlay_label_font_for_sensorgram(self)

    def _sensorgram_control_step_overlay_text_color(self, color_text: str) -> QColor:
        return sensorgram_control_step_overlay_text_color_for_sensorgram(self, color_text)

    def _sensorgram_control_step_overlay_reserved_y_padding(self, y_min: float, y_max: float) -> tuple[float, float]:
        return sensorgram_control_step_overlay_reserved_y_padding_for_sensorgram(self, y_min, y_max)

    def _record_sensorgram_control_step_event(self, payload: dict[str, object]) -> None:
        record_sensorgram_control_step_event_for_sensorgram(self, payload)

    def _sync_sensorgram_control_step_overlay(self) -> None:
        sync_sensorgram_control_step_overlay_for_sensorgram(self)

    def _primary_trace_metric(self) -> str:
        return primary_trace_metric_for_sensorgram(self)

    def _trace_stats_metric(self) -> str:
        return trace_stats_metric_for_sensorgram(self)

    def _cycle_trace_stats_metric(self) -> None:
        cycle_trace_stats_metric(self)

    def _normalize_sensorgram_line_mode(self, value: object | None = None) -> str | None:
        return normalize_sensorgram_line_mode(self, value)

    def _normalize_sensorgram_content_mode(self, mode: object) -> str:
        return normalize_sensorgram_content_mode(mode)

    def _sensorgram_content_mode_tooltip(self, mode: str | None = None) -> str:
        return sensorgram_content_mode_tooltip_for_sensorgram(self, mode)

    def _update_sensorgram_content_mode_button(self) -> None:
        update_sensorgram_content_mode_button(self)

    def _apply_sensorgram_content_mode(self, *, save: bool = False) -> None:
        apply_sensorgram_content_mode(self, save=save)

    def _cycle_sensorgram_content_mode(self) -> None:
        cycle_sensorgram_content_mode(self)

    def _update_sensorgram_metric_y_axis_mode_button(self) -> None:
        update_sensorgram_metric_y_axis_mode_button(self)

    def _apply_sensorgram_metric_y_axis_mode(self, mode: object, *, save: bool = False) -> None:
        apply_sensorgram_metric_y_axis_mode(self, mode, save=save)

    def _cycle_sensorgram_metric_y_axis_mode(self) -> None:
        cycle_sensorgram_metric_y_axis_mode(self)

    def _append_sensorgram_heatmap_history(self, spectrum: Spectrum | None) -> None:
        append_sensorgram_heatmap_history(self, spectrum)

    def _active_trace_series(self) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        cache = getattr(self, "_plot_view_cache", None)

        def _build_active_series() -> dict[str, tuple[np.ndarray, np.ndarray]]:
            selected_metrics = set(self._selected_trace_metrics())
            if cache is not None:
                active = cache.live_absolute_metric_series(selected_metrics)
                if active:
                    return active
            archive_path = getattr(self, "_metric_archive_path", None)
            if archive_path is not None:
                try:
                    archive_series = load_processed_metric_history(
                        Path(archive_path),
                        sensorgram_metric_archive_names(selected_metrics),
                        time_range_s=None,
                    )
                except Exception:
                    archive_series = {}
                if archive_series:
                    return archive_series
            return {}

        if cache is None:
            return _build_active_series()
        token = build_active_trace_series_token(self)
        return cache.cached_active_trace_series(token, _build_active_series)

    def _update_spectrum_stats(self, processed: Spectrum | None, fit: Spectrum | None) -> None:
        update_spectrum_stats_for(self, processed, fit)

    def _update_trace_stats(self) -> None:
        update_metric_stats_for(self)

    def _compute_centroid_nm(self, processed: Spectrum, fit: Spectrum | None) -> float:
        return compute_centroid_nm_for(self, processed, fit)

    def _reference_peak_nm_for_shift(self) -> float | None:
        reference = self._metric_reference_processed
        if reference is None:
            return None
        finite = np.isfinite(reference.values)
        if not np.any(finite):
            return None
        finite_indices = np.flatnonzero(finite)
        peak_index = int(finite_indices[int(np.argmax(np.asarray(reference.values, dtype=np.float64)[finite_indices]))])
        return float(reference.wavelengths_nm[peak_index])

    def _build_summary_text(self) -> str:
        return build_summary_text_for(self)

    def _describe_spectrum(self, spectrum: Spectrum | None) -> str:
        return describe_spectrum_for(self, spectrum)

    def _update_live_estimate(self) -> None:
        update_live_estimate_for(self)

    def _refresh_telemetry(self) -> None:
        refresh_telemetry_for(self)

    def _refresh_spectrometer_stats(self) -> None:
        refresh_spectrometer_stats_for(self)

    def _live_skip_rate_hz(self) -> float:
        return live_skip_rate_hz_for(self)

    def _headroom_value_text(self, headroom_ratio: float | None) -> str:
        return headroom_value_text_for(headroom_ratio)

    def _handle_live_setting_change(self) -> None:
        handle_live_setting_change_for(self)
        if self._session_stats_recording_active:
            self._update_session_stats_recording_timer_interval()

    def _handle_simulation_output_rate_change(self) -> None:
        handle_simulation_output_rate_change_for(self)

    def _request_manual_acquisition(self, kind: str) -> None:
        request_manual_acquisition(self, kind)

    def _handle_processing_setting_change(self) -> None:
        if self._suspend_processing_autosave:
            return
        sync_processing_crop_parameter_widget(self)
        self._persist_processing_settings()
        apply_processing_range_to_spectrum_plot_for(self)
        if self._live_processing_worker is not None:
            self._live_processing_worker.update_settings(self._current_processing_settings())
        self._update_trace_stats()
        self._schedule_processing_refresh()
        self._request_deferred_ui_refresh(summary=True)

    def _apply_sensorgram_time_axis_mode(self, *, redraw: bool = True) -> None:
        apply_sensorgram_time_axis_mode(self, redraw=redraw)

    def _toggle_sensorgram_time_axis_mode(self) -> None:
        toggle_sensorgram_time_axis_mode(self)

    def _schedule_processing_refresh(self) -> None:
        schedule_processing_refresh(self)

    def _start_live_acquisition(self) -> None:
        start_live_acquisition(self)

    def _stop_live_acquisition(self, message: str = "Live acquisition stopped.") -> None:
        stop_live_acquisition(self, message)

    def _make_icon_button(self, icon: QIcon, tooltip: str, *, parent: QWidget | None = None) -> QToolButton:
        button = QToolButton(parent or self)
        button.setIcon(icon)
        button.setToolTip(tooltip)
        button.setFixedSize(34, 34)
        button.setIconSize(QSize(20, 20))
        button.setAutoRaise(True)
        return button

    def _make_frameless_icon_button(self, icon: QIcon, tooltip: str, *, size: int = 28, parent: QWidget | None = None) -> QToolButton:
        button = QToolButton(parent or self)
        button.setObjectName("framelessIconButton")
        button.setIcon(icon)
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setIconSize(QSize(size - 8, size - 8))
        button.setFixedSize(size, size)
        button.setStyleSheet(
            "QToolButton#framelessIconButton { background: transparent; border: none; padding: 0px; }"
            "QToolButton#framelessIconButton:hover { background: rgba(127, 127, 127, 0.10); border: none; }"
            "QToolButton#framelessIconButton:checked { background: rgba(127, 127, 127, 0.14); border: none; }"
            "QToolButton#framelessIconButton:checked:hover { background: rgba(127, 127, 127, 0.18); border: none; }"
            "QToolButton#framelessIconButton:pressed { background: rgba(127, 127, 127, 0.18); border: none; }"
        )
        return button

    def _apply_text_widget_font_size(
        self,
        widget: QTextEdit | None,
        size_points: float,
        *,
        minimum: float = 7.0,
        maximum: float = 16.0,
    ) -> None:
        if widget is None:
            return
        font = QFont(widget.font())
        new_size = max(minimum, min(float(size_points), maximum))
        font.setPointSizeF(new_size)
        widget.setFont(font)
        try:
            widget.document().setDefaultFont(font)
        except Exception:
            pass
        try:
            setattr(widget, "_panel_font_size_pt", new_size)
        except Exception:
            pass

    def _adjust_text_widget_font_size(
        self,
        widget: QTextEdit | None,
        delta_points: float,
        *,
        minimum: float = 7.0,
        maximum: float = 16.0,
    ) -> None:
        if widget is None:
            return
        current_size = getattr(widget, "_panel_font_size_pt", None)
        if not isinstance(current_size, (int, float)):
            current_font = QFont(widget.font())
            current_size = float(current_font.pointSizeF())
            if current_size <= 0:
                current_size = float(current_font.pointSize()) if current_font.pointSize() > 0 else 9.0
            if current_size <= 0:
                current_size = 9.0
        self._apply_text_widget_font_size(widget, float(current_size) + float(delta_points), minimum=minimum, maximum=maximum)

    def _increase_log_terminal_font_size(self) -> None:
        self._adjust_text_widget_font_size(getattr(self, "log_terminal", None), 1.0)
        self._schedule_acquisition_state_persist()

    def _decrease_log_terminal_font_size(self) -> None:
        self._adjust_text_widget_font_size(getattr(self, "log_terminal", None), -1.0)
        self._schedule_acquisition_state_persist()

    def _increase_session_summary_font_size(self) -> None:
        self._adjust_text_widget_font_size(getattr(self, "session_summary", None), 1.0)
        self._schedule_acquisition_state_persist()

    def _decrease_session_summary_font_size(self) -> None:
        self._adjust_text_widget_font_size(getattr(self, "session_summary", None), -1.0)
        self._schedule_acquisition_state_persist()

    def _run_gui_callback_timed(self, label: str, callback: Callable[[], None], *, warn_ms: float = 20.0) -> None:
        run_gui_callback_timed_for_runtime(self, label, callback, warn_ms=warn_ms)

    def _toggle_window_max_restore(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        sync_window_control_icons(self)

    def _update_freeze_button_icon(self) -> None:
        if not hasattr(self, "freeze_plots_button"):
            return
        self.freeze_plots_button.setIcon(snowflake_icon(self._theme_mode, self._plots_frozen))

    def _update_sensorgram_freeze_button_icon(self) -> None:
        if not hasattr(self, "sensorgram_freeze_button"):
            return
        self.sensorgram_freeze_button.setIcon(snowflake_icon(self._theme_mode, self._sensorgram_frozen))

    def _update_residual_button_icon(self) -> None:
        if not hasattr(self, "show_residual_button"):
            return
        self.show_residual_button.setIcon(residual_icon(self.show_residual_button.isChecked()))

    def _update_dark_reference_button_icons(self) -> None:
        if hasattr(self, "acquire_dark_button"):
            self.acquire_dark_button.setIcon(dark_icon(self._session.state.dark is not None))
        if hasattr(self, "acquire_reference_button"):
            self.acquire_reference_button.setIcon(reference_icon(self._session.state.reference is not None))

    def _export_current_plot(self) -> None:
        plot_mode = self.PLOT_MODES[self.plot_selector.currentText()]
        spectrum = self._last_processed_plot
        if spectrum is None:
            self.status_label.setText("There is no plotted data to export.")
            return

        default_name = f"{plot_mode}_{spectrum.acquired_at.strftime('%Y%m%d_%H%M%S')}.png"
        default_path = Path.cwd() / "data" / default_name
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export graph",
            str(default_path),
            "PNG image (*.png);;SVG vector (*.svg);;CSV data (*.csv)",
        )
        if not file_path:
            self.status_label.setText("Export cancelled.")
            return

        destination = Path(file_path)
        suffix = destination.suffix.lower()
        if suffix == ".csv":
            export_spectrum_to_csv(destination, spectrum)
            self.status_label.setText(f"Exported {plot_mode} data to {file_path}")
            return

        if suffix not in {".png", ".svg"}:
            destination = destination.with_suffix(".png")
            suffix = ".png"

        if suffix == ".png":
            exporter = pg_exporters.ImageExporter(self.spectrum_plot.plotItem)
            exporter.parameters()["width"] = 1600
            exporter.export(str(destination))
        else:
            exporter = pg_exporters.SVGExporter(self.spectrum_plot.plotItem)
            exporter.export(str(destination))
        self.status_label.setText(f"Exported {plot_mode} graph to {destination}")

    def _show_error(self, message: str) -> None:
        self.status_label.setText(message)
        if self._closing:
            return
        self._log_error(message)
        QMessageBox.critical(self, "Acquisition error", message)

    def closeEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        self._closing = True
        self._log_info("Closing application.")
        settings_dialog = getattr(self, "_sensorgram_settings_dialog", None)
        if settings_dialog is not None:
            try:
                settings_dialog.close()
            except Exception:
                pass
            self._sensorgram_settings_dialog = None
        self._acquisition_state_timer.stop()
        self._ui_state_timer.stop()
        self._persist_acquisition_state()
        self._save_ui_state()
        if self._experiment_control_window is not None:
            try:
                self._experiment_control_window.shutdown_devices()
            except Exception as exc:
                self._log_warning(f"Experimental control device shutdown failed: {exc}")
            self._experiment_control_window.save_ui_state()
            self._experiment_control_window.close()
        self._pending_manual_kind = None
        self._pending_source_mode = None
        self._pending_auto_integration = False
        self._resume_live_after_manual = False
        self._resume_live_after_source_switch = False
        self._resume_live_after_auto_integration = False
        self._live_active = False
        self._simulation_refresh_timer.stop()
        self._ui_task_scheduler.clear()
        self._ui_heartbeat_timer.stop()
        self._live_stop_event.set()
        if self._live_worker is not None and self._live_worker.is_alive():
            try:
                self._live_worker.stop()
                self._live_worker.join(timeout=2.0)
                if self._live_worker.is_alive():
                    self._log_warning("Live acquisition worker did not exit cleanly; terminating it.")
                    self._live_worker.terminate()
                    self._live_worker.join(timeout=1.0)
            except Exception as exc:
                self._log_warning(f"Live acquisition worker shutdown failed: {exc}")
        self._live_worker = None
        if self._live_processing_worker is not None:
            try:
                self._live_processing_worker.stop()
                self._live_processing_worker.join(timeout=2.0)
                if self._live_processing_worker.is_alive():
                    self._log_warning("Live processing worker did not exit cleanly; terminating it.")
                    self._live_processing_worker.terminate()
                    self._live_processing_worker.join(timeout=1.0)
            except Exception as exc:
                self._log_warning(f"Live processing worker shutdown failed: {exc}")
        self._live_processing_worker = None
        self._reset_live_accumulator()
        if self._measurement_active:
            self._stop_measurement_run()
        elif self._measurement_writer is not None:
            self._flush_measurement_frames(force=True)
            self._measurement_writer.close()
            self._measurement_writer = None
        self._busy = False
        try:
            self._thread_pool.waitForDone(3000)
        except TypeError:
            self._thread_pool.waitForDone()
        try:
            log_handler = getattr(self, "_log_handler", None)
            if log_handler is not None:
                self._ui_logger.removeHandler(log_handler)
        except Exception:
            pass
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.quit()



