from __future__ import annotations

from collections import deque
import logging

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QTextEdit, QToolButton

from lspr_app.diagnostics import DiagnosticsConfig
from lspr_app.gui.logging_utils import GuiLogBridge, GuiLogHandler, SUCCESS_LOG_LEVEL
from lspr_ui import flow_tabler_icon, tint_tabler_icon


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
            try:
                setattr(self, "_panel_font_size_pt", new_size)
            except Exception:
                pass
            event.accept()
            return
        super().wheelEvent(event)


def initialize_logging_ui_for(window) -> None:
    if hasattr(window, "log_terminal"):
        return
    diagnostics = DiagnosticsConfig.from_env()
    window._diagnostics = diagnostics
    window._diagnostics_profile = diagnostics.profile
    window._quiet_diagnostics_mode = diagnostics.quiet_mode
    window._suppress_diagnostic_info_logs = diagnostics.suppress_info_logs
    window._export_diagnostic_events = diagnostics.export_diagnostic_events
    window._runtime_drift_probe_enabled = diagnostics.runtime_drift_probe_enabled
    window._session_stats_recording_enabled_by_default = diagnostics.session_stats_recording_enabled_by_default
    window._debug_timing_enabled = diagnostics.debug_timing_enabled
    window._deep_timing_enabled = diagnostics.deep_timing_enabled
    window._diagnostics_panel_enabled = diagnostics.diagnostics_panel_enabled
    window._gui_log_enabled = diagnostics.gui_log_enabled
    window._gui_log_min_level = int(diagnostics.gui_log_min_level)
    window._file_log_min_level = int(diagnostics.file_log_min_level)
    window.log_terminal = LogTerminalTextEdit(window)
    window.log_terminal.setObjectName("logTerminal")
    window.log_terminal.setReadOnly(True)
    window.log_terminal.setAcceptRichText(True)
    window.log_terminal.setUndoRedoEnabled(False)
    window.log_terminal.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
    window.log_terminal.setMaximumHeight(190)
    window.log_terminal.setMinimumHeight(150)
    window._apply_text_widget_font_size(window.log_terminal, 8.0, minimum=7.0, maximum=16.0)
    window.log_terminal.document().setMaximumBlockCount(300)
    window.log_terminal.setToolTip("Live event log for acquisition, processing, and controller activity.")
    window.log_terminal.setVisible(window._diagnostics_panel_enabled)

    window._log_history: list[tuple[int, str, str]] = []
    window._log_history_max_entries = 1000
    window._log_view_mode = "all"
    window.log_view_all_button = QToolButton(window)
    window.log_view_all_button.setObjectName("logViewButton")
    window.log_view_all_button.setText("All")
    window.log_view_all_button.setCheckable(True)
    window.log_view_all_button.setAutoRaise(False)
    window.log_view_all_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    window.log_view_all_button.setFixedWidth(40)
    window.log_view_all_button.setFixedHeight(22)
    window.log_view_all_button.setToolTip("Show all log entries.")
    window.log_view_all_button.clicked.connect(lambda *_args: window._set_log_view_mode("all"))
    window.log_view_gui_button = QToolButton(window)
    window.log_view_gui_button.setObjectName("logViewButton")
    window.log_view_gui_button.setText("GUI")
    window.log_view_gui_button.setCheckable(True)
    window.log_view_gui_button.setAutoRaise(False)
    window.log_view_gui_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    window.log_view_gui_button.setFixedWidth(42)
    window.log_view_gui_button.setFixedHeight(22)
    window.log_view_gui_button.setToolTip("Show GUI, processing, and analysis messages.")
    window.log_view_gui_button.clicked.connect(lambda *_args: window._set_log_view_mode("gui"))
    window.log_view_devices_button = QToolButton(window)
    window.log_view_devices_button.setObjectName("logViewButton")
    window.log_view_devices_button.setText("Devs")
    window.log_view_devices_button.setCheckable(True)
    window.log_view_devices_button.setAutoRaise(False)
    window.log_view_devices_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    window.log_view_devices_button.setFixedWidth(52)
    window.log_view_devices_button.setFixedHeight(22)
    window.log_view_devices_button.setToolTip("Show spectrometer, pump, valve, and switch messages.")
    window.log_view_devices_button.clicked.connect(lambda *_args: window._set_log_view_mode("devices"))
    window._set_log_view_mode("all", refresh=False)

    window.log_font_down_button = window._make_frameless_icon_button(
        tint_tabler_icon(flow_tabler_icon("minus"), QColor("#e6ebf1")),
        "Decrease log panel font size.",
        size=24,
        parent=window,
    )
    window.log_font_down_button.clicked.connect(window._decrease_log_terminal_font_size)
    window.log_font_up_button = window._make_frameless_icon_button(
        tint_tabler_icon(flow_tabler_icon("plus"), QColor("#8fbaff")),
        "Increase log panel font size.",
        size=24,
        parent=window,
    )
    window.log_font_up_button.clicked.connect(window._increase_log_terminal_font_size)

    window.log_follow_button = window._make_frameless_icon_button(
        tint_tabler_icon(flow_tabler_icon("arrow_autofit_down"), QColor("#e6ebf1")),
        "Keep the log scrolled to the newest entry.",
        size=24,
        parent=window,
    )
    window.log_follow_button.setCheckable(True)
    window.log_follow_button.setChecked(True)
    window.log_copy_button = window._make_frameless_icon_button(
        tint_tabler_icon(flow_tabler_icon("copy"), QColor("#8fbaff")),
        "Copy the visible log text to the clipboard.",
        size=24,
        parent=window,
    )
    window.log_clear_button = window._make_frameless_icon_button(
        tint_tabler_icon(flow_tabler_icon("trash"), QColor("#b44a4a")),
        "Clear the visible log view.",
        size=24,
        parent=window,
    )
    window._ui_logger = logging.getLogger("lspr_app")
    window._ui_logger.setLevel(int(window._gui_log_min_level))
    window._ui_logger.propagate = True
    window._log_follow_enabled = bool(window._gui_log_enabled)
    window._log_bridge = None
    window._log_handler = None
    if window._gui_log_enabled:
        window._log_bridge = GuiLogBridge()
        window._log_bridge.record_received.connect(window._append_log_record)
        window._log_handler = GuiLogHandler(window._log_bridge)
        window._log_handler.setFormatter(logging.Formatter("%(message)s"))
        window._ui_logger.addHandler(window._log_handler)
    window._log_buffer: list[tuple[int, str, str]] = []
    window._log_buffer_timer = QTimer(window)
    window._log_buffer_timer.setInterval(250)
    window._log_buffer_timer.timeout.connect(window._flush_log_buffer)
    window._last_log_buffer_flush_ms: float | None = None
    window._last_log_buffer_delay_ms: float | None = None
    window._last_log_buffer_total_ms: float | None = None
    window._log_buffer_requested_at: float | None = None
    window._diagnostic_export_buffer: list[dict[str, object]] = []
    window._diagnostic_export_path = None
    window._last_diagnostic_export_flush_ms: float | None = None
    window._diagnostic_snapshot_export_events = deque(maxlen=4000)
    window._diagnostic_snapshot_export_last_ts = 0.0
    window._diagnostic_snapshot_export_interval_s = float(window._diagnostics.export_snapshot_interval_s)
    window._last_diagnostic_snapshot_export_ms: float | None = None
    window._log_throttle_state: dict[str, tuple[float, str]] = {}
    window._log_emit_levels = {
        logging.WARNING,
        logging.ERROR,
        logging.CRITICAL,
    }
    if window._gui_log_enabled:
        window._log_emit_levels.update(
            {
                logging.INFO,
                SUCCESS_LOG_LEVEL,
            }
        )
    if int(window._gui_log_min_level) <= logging.DEBUG:
        window._log_emit_levels.add(logging.DEBUG)
    if not window._gui_log_enabled:
        window._log_buffering_enabled = False
    else:
        window._log_buffering_enabled = True
    window._ui_startup_ready = False
    window._ui_heartbeat_expected_at = window._startup_t0 + window._ui_heartbeat_interval_ms / 1000.0
    window._ui_heartbeat_timer.start()
