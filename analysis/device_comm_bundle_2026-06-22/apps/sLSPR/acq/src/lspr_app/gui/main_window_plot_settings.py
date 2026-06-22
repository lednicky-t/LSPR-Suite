from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import pyqtgraph as pg

from PyQt6.QtCore import Qt, QSize, QSignalBlocker, QByteArray, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QColorDialog,
    QFormLayout,
    QHBoxLayout,
    QGridLayout,
    QLineEdit,
    QLabel,
    QVBoxLayout,
    QSlider,
    QDoubleSpinBox,
    QSpinBox,
    QSizePolicy,
    QToolButton,
    QTabWidget,
    QStackedWidget,
    QWidget,
)

from lspr_app.gui.ui_helpers import make_compact_spinbox
from lspr_app.gui.main_window_processing import normalize_sensorgram_metric_name, sensorgram_metric_order
from lspr_app.gui.icon_helpers import flow_tabler_icon, tint_tabler_icon
from lspr_app.storage.app_config import load_app_setting, save_app_setting


_SENSORGRAM_SETTINGS_DIALOG_GEOMETRY_KEY = "sensorgram_plot_settings_dialog_geometry"


@dataclass(frozen=True)
class _LineModeOption:
    label: str
    step_mode: str | None


@dataclass(frozen=True)
class MetricModeSelectionState:
    visible_modes: frozenset[str]
    primary_mode: str


@dataclass(frozen=True)
class MetricModeRow:
    mode: str
    color_button: QToolButton
    checkbox: QCheckBox
    star_label: QToolButton
    label: QLabel


class MetricColorButton(QToolButton):
    colorChanged = pyqtSignal(str)

    def __init__(self, color: str, parent=None) -> None:
        super().__init__(parent)
        self._color = "#444444"
        self.setFixedSize(22, 22)
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("Pick a custom color for this metric.")
        self.clicked.connect(self._pick_color)
        self.set_color(color)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        qcolor = QColor(str(color))
        if not qcolor.isValid():
            qcolor = QColor("#444444")
        self._color = qcolor.name()
        self.setStyleSheet(
            "QToolButton {"
            f" background: {self._color};"
            " border: 1px solid #666666;"
            " border-radius: 3px;"
            " padding: 0px;"
            " margin: 0px;"
            " }"
            "QToolButton:hover { border: 1px solid #d0d0d0; }"
        )
        self.setToolTip(f"Metric color: {self._color}. Click to change.")

    def _pick_color(self) -> None:  # pragma: no cover - GUI runtime path
        selected = QColorDialog.getColor(QColor(self._color), self, "Select metric color")
        if not selected.isValid():
            return
        self.set_color(selected.name())
        self.colorChanged.emit(self._color)


def _make_row_title_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    label.adjustSize()
    label.setMinimumWidth(label.sizeHint().width())
    label.setMaximumWidth(label.sizeHint().width())
    return label


def _make_frameless_icon_button(icon: QIcon, tooltip: str, *, size: int = 28, parent: QWidget | None = None) -> QToolButton:
    button = QToolButton(parent)
    button.setAutoRaise(True)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFixedSize(size, size)
    button.setIcon(icon)
    button.setIconSize(QSize(max(size - 6, 12), max(size - 6, 12)))
    button.setToolTip(tooltip)
    button.setStyleSheet(
        "QToolButton {"
        " border: none;"
        " background: transparent;"
        " padding: 0px;"
        " margin: 0px;"
        " }"
        "QToolButton:hover { background: rgba(127, 127, 127, 0.10); }"
        "QToolButton:pressed { background: rgba(127, 127, 127, 0.18); }"
    )
    return button


_LINE_MODE_OPTIONS: tuple[_LineModeOption, ...] = (
    _LineModeOption("Linear", None),
    _LineModeOption("Step left", "left"),
    _LineModeOption("Step right", "right"),
    _LineModeOption("Step center", "center"),
)

_TIME_AXIS_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Elapsed time", "elapsed"),
    ("Local clock time", "clock"),
)

_ROLLING_WINDOW_OPTIONS: tuple[tuple[str, float], ...] = (
    ("1 min", 60.0),
    ("5 min", 300.0),
    ("15 min", 900.0),
    ("30 min", 1800.0),
    ("60 min", 3600.0),
)

_BUFFER_OPTIONS: tuple[tuple[str, float | None], ...] = (
    ("None", None),
    ("1%", 0.01),
    ("2%", 0.02),
    ("5%", 0.05),
)

_AUTOSCALE_THROTTLE_OPTIONS: tuple[tuple[str, float | None], ...] = (
    ("Off", 0.0),
    ("Light", 0.5),
    ("Medium", 1.0),
    ("Heavy", 2.0),
)


def _normalize_line_mode(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in {"left", "right", "center"} else None


def _log_dialog_timing(window, key: str, message: str) -> None:
    if not bool(getattr(window, "_deep_timing_enabled", False)):
        return
    logger = getattr(window, "_log_throttled", None)
    if callable(logger):
        logger(key, message, level=20, min_interval=0.5)


class MetricModeSelector(QWidget):
    selectionChanged = pyqtSignal(object, str)

    def __init__(self, window) -> None:
        super().__init__()
        self._window = window
        self._ordered_modes = sensorgram_metric_order(window)
        if not self._ordered_modes:
            self._ordered_modes = ["smoothed_max"]
        self._rows: dict[str, MetricModeRow] = {}
        self._state = self._make_valid_state(self._ordered_modes[:1], self._ordered_modes[0])
        self._build_ui()

    def _make_valid_state(self, visible_modes, primary_mode: str) -> MetricModeSelectionState:
        visible_set = {mode for mode in self._ordered_modes if mode in {normalize_sensorgram_metric_name(name) for name in visible_modes}}
        if not visible_set:
            fallback = self._ordered_modes[0]
            return MetricModeSelectionState(frozenset({fallback}), fallback)
        primary = normalize_sensorgram_metric_name(primary_mode)
        if primary not in visible_set:
            primary = next(mode for mode in self._ordered_modes if mode in visible_set)
        return MetricModeSelectionState(frozenset(visible_set), primary)

    def _build_ui(self) -> None:
        layout = QGridLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(2)
        layout.setColumnStretch(0, 0)
        layout.setColumnStretch(1, 0)
        layout.setColumnStretch(2, 0)
        layout.setColumnStretch(3, 0)

        metric_header = QLabel("Metric")
        metric_header.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(metric_header, 0, 0)

        color_header = QLabel("Color")
        color_header.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(color_header, 0, 1)

        show_header = QLabel("Show")
        show_header.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(show_header, 0, 2)

        primary_header = QLabel("Primary")
        primary_header.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(primary_header, 0, 3)

        labels = getattr(self._window, "TRACE_METRIC_LABELS", {})
        descriptions = getattr(self._window, "TRACE_METRIC_DESCRIPTIONS", {})
        colors = getattr(self._window, "TRACE_METRIC_COLORS", {})
        for row_index, mode in enumerate(self._ordered_modes, start=1):
            metric_color = str(colors.get(mode, "#444444"))

            label = QLabel(labels.get(mode, mode))
            label.setMinimumHeight(22)
            label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            description = descriptions.get(mode, mode)
            label.setToolTip(f"{mode}: {description}")
            label.setStyleSheet(f"color: {metric_color};")

            color_button = MetricColorButton(metric_color)
            color_button.colorChanged.connect(lambda color, metric=mode: self._on_color_changed(metric, color))

            star_label = QToolButton(self)
            star_label.setFixedSize(22, 22)
            star_label.setAutoRaise(True)
            star_label.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            star_label.setText("\u2606")
            star_label.setCursor(Qt.CursorShape.PointingHandCursor)
            star_label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            star_label.setToolTip("Set this visible metric as the primary metric.")
            star_label.setStyleSheet(
                "QToolButton { background: transparent; border: none; padding: 0px; margin: 0px; color: #8a8a8a; }"
            )
            star_label.clicked.connect(lambda _checked=False, metric=mode: self._on_primary_clicked(metric))

            checkbox = QCheckBox(self)
            checkbox.setFixedHeight(22)
            checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
            checkbox.setToolTip("Include this metric in the sensorgram display and statistics.")
            checkbox.setStyleSheet(
                f"QCheckBox {{ color: {metric_color}; }}"
                f"QCheckBox::indicator {{ width: 12px; height: 12px; border: 1px solid {metric_color}; border-radius: 2px; }}"
                f"QCheckBox::indicator:unchecked {{ background: transparent; }}"
                f"QCheckBox::indicator:checked {{ background: {metric_color}; }}"
            )
            checkbox.toggled.connect(lambda checked, metric=mode: self._on_visible_toggled(metric, checked))

            self._rows[mode] = MetricModeRow(
                mode=mode,
                color_button=color_button,
                checkbox=checkbox,
                star_label=star_label,
                label=label,
            )
            layout.addWidget(label, row_index, 0)
            layout.addWidget(color_button, row_index, 1, alignment=Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(checkbox, row_index, 2, alignment=Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(star_label, row_index, 3, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._sync_widgets()

    def state(self) -> MetricModeSelectionState:
        return MetricModeSelectionState(frozenset(self._state.visible_modes), self._state.primary_mode)

    def set_state(self, visible_modes, primary_mode: str) -> None:
        self._state = self._make_valid_state(visible_modes, primary_mode)
        self._sync_widgets()

    def _show_status(self, message: str) -> None:
        status_label = getattr(self._window, "status_label", None)
        if status_label is not None:
            status_label.setText(message)

    def _validate_state(self) -> None:
        self._state = self._make_valid_state(self._state.visible_modes, self._state.primary_mode)

    def _sync_widgets(self) -> None:
        visible_modes = set(self._state.visible_modes)
        primary_mode = self._state.primary_mode
        for mode, row in self._rows.items():
            visible = mode in visible_modes
            primary = mode == primary_mode
            with QSignalBlocker(row.checkbox), QSignalBlocker(row.star_label):
                row.checkbox.setChecked(visible)
                row.star_label.setText("\u2605" if primary else "\u2606")
                row.star_label.setEnabled(visible)
                row.star_label.setToolTip(
                    "Primary metric" if primary else "Set this visible metric as the primary metric."
                )
            metric_color = getattr(self._window, "TRACE_METRIC_COLORS", {}).get(mode, "#444444")
            description = getattr(self._window, "TRACE_METRIC_DESCRIPTIONS", {}).get(mode, mode)
            row.color_button.set_color(metric_color)
            row.label.setStyleSheet(f"font-weight: 700; color: {metric_color};" if primary else f"color: {metric_color};")
            row.label.setToolTip(f"{mode}: {description}")
            row.star_label.setStyleSheet(
                "QToolButton { background: transparent; border: none; padding: 0px; margin: 0px; "
                f"color: {'#f2c94c' if primary else '#8a8a8a'}; font-size: 16px; font-weight: 700; }}"
            )

    def _emit_state_changed(self) -> None:
        self.selectionChanged.emit(frozenset(self._state.visible_modes), self._state.primary_mode)

    def _on_visible_toggled(self, mode: str, checked: bool) -> None:
        mode = normalize_sensorgram_metric_name(mode)
        visible = set(self._state.visible_modes)
        primary = self._state.primary_mode
        if checked:
            if mode in visible:
                self._sync_widgets()
                return
            self._state = self._make_valid_state(visible | {mode}, primary)
            self._sync_widgets()
            self._emit_state_changed()
            return
        if mode not in visible:
            self._sync_widgets()
            return
        if mode == primary:
            self._show_status("Select another primary metric before hiding this one.")
            self._sync_widgets()
            return
        if len(visible) <= 1:
            self._sync_widgets()
            return
        visible.discard(mode)
        self._state = self._make_valid_state(visible, primary)
        self._sync_widgets()
        self._emit_state_changed()

    def _on_primary_clicked(self, mode: str) -> None:
        mode = normalize_sensorgram_metric_name(mode)
        if mode not in self._state.visible_modes:
            self._sync_widgets()
            return
        if mode == self._state.primary_mode:
            return
        self._state = self._make_valid_state(self._state.visible_modes, mode)
        self._sync_widgets()
        self._emit_state_changed()

    def _on_color_changed(self, mode: str, color: str) -> None:
        window = self._window
        if hasattr(window, "_set_sensorgram_metric_color"):
            window._set_sensorgram_metric_color(mode, color, save=False)
        else:
            colors = getattr(window, "TRACE_METRIC_COLORS", None)
            if isinstance(colors, dict):
                colors[normalize_sensorgram_metric_name(mode)] = str(color)
            time_plot_colors = getattr(window, "SENSORGRAM_TIME_PLOT_COLORS", None)
            if isinstance(time_plot_colors, dict):
                time_plot_colors[normalize_sensorgram_metric_name(mode)] = str(color)
            if hasattr(window, "_apply_metric_color_styles"):
                window._apply_metric_color_styles()
            if hasattr(window, "_request_deferred_ui_refresh"):
                window._request_deferred_ui_refresh(trace_plot=True, summary=True)
        self._sync_widgets()


class SensorgramPlotSettingsDialog(QDialog):
    def __init__(self, window) -> None:
        started_total = perf_counter()
        super().__init__(window)
        self._window = window
        self.setWindowTitle("Sensogram settings")
        self.setModal(True)
        self.setMinimumWidth(430)

        layout = QVBoxLayout()
        tabs = QTabWidget(self)
        tabs.setDocumentMode(False)
        tabs.setStyleSheet(
            "QTabWidget::pane {"
            "  border: 1px solid #3a4250;"
            "  border-radius: 8px;"
            "  background: #10151d;"
            "}"
            "QTabBar::tab {"
            "  background: #1a2029;"
            "  color: #d7dde7;"
            "  border: 1px solid #3a4250;"
            "  border-bottom: none;"
            "  padding: 8px 14px;"
            "  margin-right: 4px;"
            "  border-top-left-radius: 8px;"
            "  border-top-right-radius: 8px;"
            "}"
            "QTabBar::tab:selected {"
            "  background: #10151d;"
            "  color: #ffffff;"
            "  margin-bottom: -1px;"
            "}"
            "QTabBar::tab:!selected {"
            "  margin-top: 2px;"
            "}"
        )
        live_started = perf_counter()
        tabs.addTab(self._build_live_tab(), "Live mode")
        _log_dialog_timing(
            self._window,
            "sensorgram_settings.live_tab",
            f"Sensorgram settings live tab built in {(perf_counter() - live_started) * 1000.0:.2f} ms",
        )
        preview_started = perf_counter()
        tabs.addTab(self._build_preview_tab(), "Preview mode")
        _log_dialog_timing(
            self._window,
            "sensorgram_settings.preview_tab",
            f"Sensorgram settings preview tab built in {(perf_counter() - preview_started) * 1000.0:.2f} ms",
        )
        tabs.setCurrentIndex(0)
        layout.addWidget(tabs)

        buttons_row = QHBoxLayout()
        buttons_row.setContentsMargins(0, 0, 0, 0)
        buttons_row.setSpacing(8)
        buttons_row.addStretch(1)

        self.apply_icon_button = _make_frameless_icon_button(
            tint_tabler_icon(flow_tabler_icon("checkbox"), QColor("#50d890")),
            "Apply settings",
            size=30,
            parent=self,
        )
        self.apply_icon_button.clicked.connect(self.apply_settings)
        buttons_row.addWidget(self.apply_icon_button)

        self.close_icon_button = _make_frameless_icon_button(
            tint_tabler_icon(flow_tabler_icon("x"), QColor("#e46a6a")),
            "Close dialog",
            size=30,
            parent=self,
        )
        self.close_icon_button.clicked.connect(self.reject)
        buttons_row.addWidget(self.close_icon_button)

        buttons_widget = QWidget(self)
        buttons_widget.setLayout(buttons_row)
        layout.addWidget(buttons_widget)
        self.setLayout(layout)
        geometry_started = perf_counter()
        self._restore_dialog_geometry()
        _log_dialog_timing(
            self._window,
            "sensorgram_settings.geometry",
            f"Sensorgram settings geometry restored in {(perf_counter() - geometry_started) * 1000.0:.2f} ms",
        )
        _log_dialog_timing(
            self._window,
            "sensorgram_settings.construct",
            f"Sensorgram settings dialog constructed in {(perf_counter() - started_total) * 1000.0:.2f} ms",
        )

    def refresh_from_window(self) -> None:
        started = perf_counter()
        selector = getattr(self, "metric_mode_selector", None)
        if selector is not None:
            selector.set_state(
                getattr(self._window, "_sensorgram_metric_visible_modes", {"smoothed_max"}),
                getattr(self._window, "_sensorgram_metric_primary_mode", "smoothed_max"),
            )
        if hasattr(self, "control_overlay_check"):
            self.control_overlay_check.setChecked(bool(getattr(self._window, "_sensorgram_control_step_overlay_enabled", True)))
        if hasattr(self, "control_overlay_style_combo"):
            self.control_overlay_style_combo.setCurrentIndex(
                0 if str(getattr(self._window, "_sensorgram_control_step_overlay_style", "bar")) == "bar" else 1
            )
        if hasattr(self, "control_overlay_position_button"):
            self._set_control_overlay_position(str(getattr(self._window, "_sensorgram_control_step_overlay_position", "top")))
        if hasattr(self, "control_overlay_thickness_spin"):
            self.control_overlay_thickness_spin.setValue(
                int(max(min(float(getattr(self._window, "_sensorgram_control_step_overlay_bar_height_px", 18)), 72.0), 8.0))
            )
        if hasattr(self, "control_overlay_opacity_spin"):
            self.control_overlay_opacity_spin.setValue(
                int(max(min(float(getattr(self._window, "_sensorgram_control_step_overlay_opacity", 25.0)), 100.0), 0.0))
            )
        if hasattr(self, "antialias_check"):
            self.antialias_check.setChecked(bool(getattr(self._window, "_plot_antialias_enabled", False)))
        if hasattr(self, "line_mode_combo"):
            current_step_mode = _normalize_line_mode(getattr(self._window, "_sensorgram_line_step_mode", None))
            current_index = 0
            for index in range(self.line_mode_combo.count()):
                if self.line_mode_combo.itemData(index) == current_step_mode:
                    current_index = index
                    break
            self.line_mode_combo.setCurrentIndex(current_index)
        if hasattr(self, "line_width_spin"):
            self.line_width_spin.setValue(float(getattr(self._window, "_sensorgram_line_width_px", 2.2)))
        if hasattr(self, "follow_latest_buffer_combo"):
            current_buffer_fraction = getattr(self._window, "_metric_autoscale_follow_latest_buffer_fraction", 0.05)
            if current_buffer_fraction is None:
                current_buffer_fraction = 0.0
            try:
                current_buffer_fraction = float(current_buffer_fraction)
            except (TypeError, ValueError):
                current_buffer_fraction = 0.05
            current_index = 0
            for index, (_label, fraction) in enumerate(_BUFFER_OPTIONS):
                if fraction is None:
                    match = current_buffer_fraction <= 0.0
                else:
                    match = abs(float(fraction) - current_buffer_fraction) < abs(
                        float(_BUFFER_OPTIONS[current_index][1] or 0.0) - current_buffer_fraction
                    )
                if match:
                    current_index = index
            self.follow_latest_buffer_combo.setCurrentIndex(current_index)
        if hasattr(self, "autoscale_throttle_combo"):
            current_throttle_s = getattr(self._window, "_metric_autoscale_min_interval_s", 1.0)
            try:
                current_throttle_s = float(current_throttle_s)
            except (TypeError, ValueError):
                current_throttle_s = 1.0
            current_index = 0
            for index, (_label, seconds) in enumerate(_AUTOSCALE_THROTTLE_OPTIONS):
                if abs(float(seconds or 0.0) - current_throttle_s) < abs(float(_AUTOSCALE_THROTTLE_OPTIONS[current_index][1] or 0.0) - current_throttle_s):
                    current_index = index
            self.autoscale_throttle_combo.setCurrentIndex(current_index)
        if hasattr(self, "skip_tiny_changes_check"):
            self.skip_tiny_changes_check.setChecked(bool(getattr(self._window, "_metric_autoscale_skip_tiny_changes_enabled", True)))
        if hasattr(self, "metric_display_points_spin"):
            self.metric_display_points_spin.setValue(int(getattr(self._window, "_plot_display_points", 512)))
        if hasattr(self, "recent_tail_points_spin"):
            self.recent_tail_points_spin.setValue(int(getattr(self._window, "_sensorgram_compression_recent_tail_points", 300)))
        if hasattr(self, "envelope_overlay_check"):
            self.envelope_overlay_check.setChecked(bool(getattr(self._window, "_sensorgram_metric_envelope_overlay_enabled", False)))
        if hasattr(self, "envelope_overlay_alpha_spin"):
            current_alpha = int(getattr(self._window, "_sensorgram_metric_envelope_overlay_alpha", 16))
            self.envelope_overlay_alpha_spin.setValue(max(0, min(current_alpha, 100)))
        _log_dialog_timing(
            self._window,
            "sensorgram_settings.refresh",
            f"Sensorgram settings refresh_from_window completed in {(perf_counter() - started) * 1000.0:.2f} ms",
        )

    def _restore_dialog_geometry(self) -> None:
        geometry = load_app_setting(_SENSORGRAM_SETTINGS_DIALOG_GEOMETRY_KEY, "")
        if isinstance(geometry, str) and geometry:
            try:
                if self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii"))):
                    return
            except Exception:
                pass

    def _save_dialog_geometry(self) -> None:
        try:
            geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        except Exception:
            return
        if geometry:
            save_app_setting(_SENSORGRAM_SETTINGS_DIALOG_GEOMETRY_KEY, geometry)

    def _control_overlay_position(self) -> str:
        button = getattr(self, "control_overlay_position_button", None)
        if button is None:
            return "top"
        return "bottom" if bool(button.isChecked()) else "top"

    def _set_control_overlay_position(self, position: str) -> None:
        button = getattr(self, "control_overlay_position_button", None)
        if button is None:
            return
        button.blockSignals(True)
        button.setChecked(str(position or "top").strip().lower() == "bottom")
        button.blockSignals(False)
        self._sync_control_overlay_position_button()

    def _sync_control_overlay_position_button(self) -> None:
        button = getattr(self, "control_overlay_position_button", None)
        if button is None:
            return
        position = self._control_overlay_position()
        icon_name = "arrow_bar_to_up" if position == "bottom" else "arrow_bar_to_down"
        button.setIcon(tint_tabler_icon(flow_tabler_icon(icon_name), QColor("#f0f3f7")))
        button.setToolTip(
            "Timeline at the bottom. Click to move it to the top."
            if position == "bottom"
            else "Timeline at the top. Click to move it to the bottom."
        )

    def _sync_control_overlay_widgets(self) -> None:
        style = str(self.control_overlay_style_combo.currentData() or "bar")
        stack = getattr(self, "control_overlay_param_stack", None)
        if stack is not None:
            stack.setCurrentIndex(0 if style == "bar" else 1)

    def _build_live_tab(self) -> QWidget:
        page = QWidget(self)
        outer = QVBoxLayout(page)
        outer.setSpacing(12)
        outer.setContentsMargins(12, 12, 12, 12)

        self.metric_mode_selector = MetricModeSelector(self._window)
        self.metric_mode_selector.set_state(
            getattr(self._window, "_sensorgram_metric_visible_modes", {"smoothed_max"}),
            getattr(self._window, "_sensorgram_metric_primary_mode", "smoothed_max"),
        )
        self.metric_mode_selector.selectionChanged.connect(self._on_metric_mode_selection_changed)
        outer.addWidget(self.metric_mode_selector)

        self.control_overlay_style_combo = QComboBox(self)
        self.control_overlay_style_combo.addItem("Compact", "bar")
        self.control_overlay_style_combo.addItem("Full", "background")
        self.control_overlay_style_combo.setCurrentIndex(
            0 if str(getattr(self._window, "_sensorgram_control_step_overlay_style", "bar")) == "bar" else 1
        )
        self.control_overlay_style_combo.setToolTip(
            "Choose whether the timeline is shown as compact bars or full-height background bands."
        )

        self.control_overlay_check = QCheckBox("")
        self.control_overlay_check.setChecked(
            bool(getattr(self._window, "_sensorgram_control_step_overlay_enabled", True))
        )
        self.control_overlay_check.setToolTip(
            "Overlay the real experiment-control steps on the sensorgram plot. The live runtime state is used, so jumps, pauses, and step changes all update the overlay."
        )
        self.control_overlay_check.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.control_overlay_check.setStyleSheet(
            "QCheckBox { margin: 0px; padding: 0px; spacing: 4px; }"
            "QCheckBox::indicator { width: 14px; height: 14px; }"
        )

        self.control_overlay_position_button = _make_frameless_icon_button(
            tint_tabler_icon(flow_tabler_icon("arrow_bar_to_down"), QColor("#8fbaff")),
            "Timeline at the top. Click to move it to the bottom.",
            size=24,
            parent=self,
        )
        self.control_overlay_position_button.setCheckable(True)
        self.control_overlay_position_button.setChecked(
            str(getattr(self._window, "_sensorgram_control_step_overlay_position", "top")) == "bottom"
        )
        self.control_overlay_position_button.clicked.connect(lambda *_args: self._sync_control_overlay_position_button())
        self._sync_control_overlay_position_button()

        self.control_overlay_thickness_spin = QSpinBox()
        make_compact_spinbox(self.control_overlay_thickness_spin)
        self.control_overlay_thickness_spin.setRange(8, 72)
        self.control_overlay_thickness_spin.setSuffix(" px")
        self.control_overlay_thickness_spin.setValue(
            int(max(min(float(getattr(self._window, "_sensorgram_control_step_overlay_bar_height_px", 18)), 72.0), 8.0))
        )
        self.control_overlay_thickness_spin.setToolTip(
            "Timeline size in pixels for compact mode. The label text scales with this value so the bar stays tall enough to contain it."
        )
        self.control_overlay_thickness_spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.control_overlay_thickness_spin.setFixedWidth(74)

        self.control_overlay_opacity_spin = QSpinBox()
        make_compact_spinbox(self.control_overlay_opacity_spin)
        self.control_overlay_opacity_spin.setRange(0, 100)
        self.control_overlay_opacity_spin.setSuffix("%")
        self.control_overlay_opacity_spin.setValue(
            int(max(min(float(getattr(self._window, "_sensorgram_control_step_overlay_opacity", 25.0)), 100.0), 0.0))
        )
        self.control_overlay_opacity_spin.setToolTip("Opacity used by the full timeline background.")
        self.control_overlay_opacity_spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.control_overlay_opacity_spin.setFixedWidth(74)

        self.control_overlay_size_label = QLabel("Size")
        self.control_overlay_size_label.setToolTip("Timeline size in pixels for compact mode.")
        self.control_overlay_size_widget = QWidget(self)
        control_overlay_size_row = QHBoxLayout(self.control_overlay_size_widget)
        control_overlay_size_row.setContentsMargins(0, 0, 0, 0)
        control_overlay_size_row.setSpacing(8)
        control_overlay_size_row.addWidget(self.control_overlay_size_label)
        control_overlay_size_row.addWidget(self.control_overlay_thickness_spin)
        self.control_overlay_size_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.control_overlay_opacity_label = QLabel("Opacity")
        self.control_overlay_opacity_label.setToolTip("Opacity used by the full timeline background.")
        self.control_overlay_opacity_widget = QWidget(self)
        control_overlay_opacity_row = QHBoxLayout(self.control_overlay_opacity_widget)
        control_overlay_opacity_row.setContentsMargins(0, 0, 0, 0)
        control_overlay_opacity_row.setSpacing(8)
        control_overlay_opacity_row.addWidget(self.control_overlay_opacity_label)
        control_overlay_opacity_row.addWidget(self.control_overlay_opacity_spin)
        self.control_overlay_opacity_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.control_overlay_style_combo.currentIndexChanged.connect(lambda *_args: self._sync_control_overlay_widgets())
        self._sync_control_overlay_widgets()

        self.control_overlay_param_stack = QStackedWidget(self)
        self.control_overlay_param_stack.addWidget(self.control_overlay_size_widget)
        self.control_overlay_param_stack.addWidget(self.control_overlay_opacity_widget)
        self.control_overlay_param_stack.setCurrentIndex(0 if str(self.control_overlay_style_combo.currentData() or "bar") == "bar" else 1)

        self.antialias_check = QCheckBox("")
        self.antialias_check.setChecked(bool(getattr(self._window, "_plot_antialias_enabled", False)))
        self.antialias_check.setToolTip(
            "Enable anti-aliasing for sensorgram drawing."
        )

        self.line_mode_combo = QComboBox(self)
        for option in _LINE_MODE_OPTIONS:
            self.line_mode_combo.addItem(option.label, option.step_mode)
        current_step_mode = _normalize_line_mode(getattr(self._window, "_sensorgram_line_step_mode", None))
        current_index = 0
        for index in range(self.line_mode_combo.count()):
            if self.line_mode_combo.itemData(index) == current_step_mode:
                current_index = index
                break
        self.line_mode_combo.setCurrentIndex(current_index)
        self.line_mode_combo.setToolTip(
            "Choose how points are connected in the sensorgram line: straight or stepped."
        )

        self.line_width_spin = QDoubleSpinBox()
        self.line_width_spin.setRange(0.5, 10.0)
        self.line_width_spin.setSingleStep(0.1)
        self.line_width_spin.setDecimals(1)
        self.line_width_spin.setSuffix(" px")
        self.line_width_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.line_width_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.line_width_spin.setValue(float(getattr(self._window, "_sensorgram_line_width_px", 2.2)))
        self.line_width_spin.setToolTip("Set the thickness of the sensorgram line in pixels.")
        self.line_width_spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.line_width_spin.setFixedWidth(62)

        self.follow_latest_buffer_combo = QComboBox(self)
        current_buffer_fraction = getattr(self._window, "_metric_autoscale_follow_latest_buffer_fraction", 0.05)
        if current_buffer_fraction is None:
            current_buffer_fraction = 0.0
        try:
            current_buffer_fraction = float(current_buffer_fraction)
        except (TypeError, ValueError):
            current_buffer_fraction = 0.05
        current_index = 0
        for index, (label, fraction) in enumerate(_BUFFER_OPTIONS):
            self.follow_latest_buffer_combo.addItem(label, fraction)
            if fraction is None:
                match = current_buffer_fraction <= 0.0
            else:
                match = abs(float(fraction) - current_buffer_fraction) < abs(
                    float(_BUFFER_OPTIONS[current_index][1] or 0.0) - current_buffer_fraction
                )
            if match:
                current_index = index
        self.follow_latest_buffer_combo.setCurrentIndex(current_index)
        self.follow_latest_buffer_combo.setToolTip(
            "Keep a blank buffer on the right side of the sensorgram plot so new data does not immediately touch the edge."
        )

        self.autoscale_throttle_combo = QComboBox(self)
        current_throttle_s = getattr(self._window, "_metric_autoscale_min_interval_s", 1.0)
        try:
            current_throttle_s = float(current_throttle_s)
        except (TypeError, ValueError):
            current_throttle_s = 1.0
        current_index = 0
        for index, (label, seconds) in enumerate(_AUTOSCALE_THROTTLE_OPTIONS):
            self.autoscale_throttle_combo.addItem(label, seconds)
            if abs(float(seconds or 0.0) - current_throttle_s) < abs(float(_AUTOSCALE_THROTTLE_OPTIONS[current_index][1] or 0.0) - current_throttle_s):
                current_index = index
        self.autoscale_throttle_combo.setCurrentIndex(current_index)
        self.autoscale_throttle_combo.setToolTip(
            "Limit how often the sensorgram plot may rescale its axes. Higher throttling reduces redraw churn, but the view follows new data more slowly."
        )

        self.skip_tiny_changes_check = QCheckBox("Gate")
        self.skip_tiny_changes_check.setChecked(
            bool(getattr(self._window, "_metric_autoscale_skip_tiny_changes_enabled", True))
        )
        self.skip_tiny_changes_check.setToolTip(
            "Skip autoscale updates when the visible range change is very small. This reduces unnecessary redraws in both display modes."
        )

        self.metric_display_points_spin = QSpinBox()
        make_compact_spinbox(self.metric_display_points_spin)
        self.metric_display_points_spin.setRange(16, 100000)
        self.metric_display_points_spin.setValue(int(getattr(self._window, "_plot_display_points", 512)))
        self.metric_display_points_spin.setToolTip(
            "Maximum number of points kept visible in the sensorgram time plot."
        )

        self.recent_tail_points_spin = QSpinBox()
        make_compact_spinbox(self.recent_tail_points_spin)
        self.recent_tail_points_spin.setRange(0, 10000)
        self.recent_tail_points_spin.setSingleStep(50)
        self.recent_tail_points_spin.setValue(
            int(getattr(self._window, "_sensorgram_compression_recent_tail_points", 300))
        )
        self.recent_tail_points_spin.setToolTip(
            "Number of newest points kept uncompressed in the absolute sensorgram plot. Older data is shown using compression."
        )

        self.envelope_overlay_check = QCheckBox("")
        self.envelope_overlay_check.setChecked(
            bool(getattr(self._window, "_sensorgram_metric_envelope_overlay_enabled", False))
        )
        self.envelope_overlay_check.setToolTip(
            "Draw the min/max envelope as a faint secondary layer over the trend line."
        )
        self.envelope_overlay_check.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.envelope_overlay_check.setStyleSheet(
            "QCheckBox { margin: 0px; padding: 0px; spacing: 4px; }"
            "QCheckBox::indicator { width: 14px; height: 14px; }"
        )

        self.envelope_overlay_alpha_spin = QSpinBox()
        make_compact_spinbox(self.envelope_overlay_alpha_spin)
        self.envelope_overlay_alpha_spin.setRange(0, 100)
        self.envelope_overlay_alpha_spin.setSuffix("%")
        current_alpha = int(getattr(self._window, "_sensorgram_metric_envelope_overlay_alpha", 16))
        current_alpha = max(0, min(current_alpha, 100))
        self.envelope_overlay_alpha_spin.setValue(current_alpha)
        self.envelope_overlay_alpha_spin.setToolTip(
            "Control how visible the envelope band is. Lower values are more subtle; higher values make the band more obvious."
        )
        self.envelope_overlay_alpha_spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.envelope_overlay_alpha_spin.setFixedWidth(74)

        plot_grid = QGridLayout()
        plot_grid.setContentsMargins(0, 0, 0, 0)
        plot_grid.setHorizontalSpacing(8)
        plot_grid.setVerticalSpacing(6)
        plot_grid.setColumnMinimumWidth(0, 136)
        plot_grid.setColumnMinimumWidth(1, 88)
        plot_grid.setColumnMinimumWidth(2, 88)
        plot_grid.setColumnMinimumWidth(3, 84)
        plot_grid.setColumnStretch(4, 1)
        plot_grid.addWidget(_make_row_title_label("Display points"), 0, 0, Qt.AlignmentFlag.AlignVCenter)
        plot_grid.addWidget(self.metric_display_points_spin, 0, 1, Qt.AlignmentFlag.AlignVCenter)
        plot_grid.addWidget(_make_row_title_label("Live tail"), 0, 2, Qt.AlignmentFlag.AlignVCenter)
        plot_grid.addWidget(self.recent_tail_points_spin, 0, 3, Qt.AlignmentFlag.AlignVCenter)
        plot_grid.addWidget(_make_row_title_label("Line"), 1, 0, Qt.AlignmentFlag.AlignVCenter)
        plot_grid.addWidget(self.line_mode_combo, 1, 1, Qt.AlignmentFlag.AlignVCenter)
        plot_grid.addWidget(self.line_width_spin, 1, 2, Qt.AlignmentFlag.AlignVCenter)
        plot_grid.addWidget(_make_row_title_label("Envelope"), 2, 0, Qt.AlignmentFlag.AlignVCenter)
        plot_grid.addWidget(self.envelope_overlay_check, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        plot_grid.addWidget(self.envelope_overlay_alpha_spin, 2, 2, Qt.AlignmentFlag.AlignVCenter)
        plot_grid.addWidget(_make_row_title_label("Right buffer"), 3, 0, Qt.AlignmentFlag.AlignVCenter)
        plot_grid.addWidget(self.follow_latest_buffer_combo, 3, 1, 1, 3, Qt.AlignmentFlag.AlignVCenter)

        timeline_grid = QGridLayout()
        timeline_grid.setContentsMargins(0, 0, 0, 0)
        timeline_grid.setHorizontalSpacing(8)
        timeline_grid.setVerticalSpacing(6)
        timeline_grid.setColumnMinimumWidth(0, 136)
        timeline_grid.setColumnMinimumWidth(1, 24)
        timeline_grid.setColumnMinimumWidth(2, 92)
        timeline_grid.setColumnMinimumWidth(3, 28)
        timeline_grid.setColumnMinimumWidth(4, 92)
        timeline_grid.setColumnStretch(5, 1)
        timeline_grid.addWidget(_make_row_title_label("Timeline"), 0, 0, Qt.AlignmentFlag.AlignVCenter)
        timeline_grid.addWidget(self.control_overlay_check, 0, 1, Qt.AlignmentFlag.AlignVCenter)
        timeline_grid.addWidget(self.control_overlay_style_combo, 0, 2, Qt.AlignmentFlag.AlignVCenter)
        timeline_grid.addWidget(self.control_overlay_position_button, 0, 3, Qt.AlignmentFlag.AlignVCenter)
        timeline_grid.addWidget(self.control_overlay_param_stack, 0, 4, 1, 2, Qt.AlignmentFlag.AlignVCenter)

        performance_grid = QGridLayout()
        performance_grid.setContentsMargins(0, 0, 0, 0)
        performance_grid.setHorizontalSpacing(8)
        performance_grid.setVerticalSpacing(6)
        performance_grid.setColumnMinimumWidth(0, 136)
        performance_grid.setColumnMinimumWidth(1, 24)
        performance_grid.setColumnMinimumWidth(2, 88)
        performance_grid.setColumnStretch(3, 1)
        performance_grid.addWidget(_make_row_title_label("Anti-aliasing"), 0, 0, Qt.AlignmentFlag.AlignVCenter)
        performance_grid.addWidget(self.antialias_check, 0, 1, 1, 2, Qt.AlignmentFlag.AlignVCenter)
        performance_grid.addWidget(_make_row_title_label("Autoscale"), 1, 0, Qt.AlignmentFlag.AlignVCenter)
        performance_grid.addWidget(self.skip_tiny_changes_check, 1, 1, Qt.AlignmentFlag.AlignVCenter)
        performance_grid.addWidget(_make_row_title_label("Throttling"), 1, 2, Qt.AlignmentFlag.AlignVCenter)
        performance_grid.addWidget(self.autoscale_throttle_combo, 1, 3, Qt.AlignmentFlag.AlignVCenter)

        def _section_title(text: str) -> QLabel:
            label = QLabel(text)
            label.setStyleSheet("font-weight: 700; margin-top: 2px; margin-bottom: 0px;")
            label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            return label

        sections_grid = QGridLayout()
        sections_grid.setContentsMargins(0, 0, 0, 0)
        sections_grid.setHorizontalSpacing(0)
        sections_grid.setVerticalSpacing(6)
        sections_grid.addWidget(_section_title("Sensorgram plot"), 0, 0, Qt.AlignmentFlag.AlignLeft)
        sections_grid.addLayout(plot_grid, 1, 0)
        sections_grid.addWidget(_section_title("Timeline"), 2, 0, Qt.AlignmentFlag.AlignLeft)
        sections_grid.addLayout(timeline_grid, 3, 0)
        sections_grid.addWidget(_section_title("Performance"), 4, 0, Qt.AlignmentFlag.AlignLeft)
        sections_grid.addLayout(performance_grid, 5, 0)
        outer.addLayout(sections_grid)
        return page

    def _build_preview_tab(self) -> QWidget:
        page = QWidget(self)
        outer = QVBoxLayout(page)
        outer.setSpacing(12)
        outer.setContentsMargins(12, 12, 12, 12)

        preview_form = QFormLayout()
        preview_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        heatmap_points_row = QHBoxLayout()
        heatmap_points_row.setContentsMargins(0, 0, 0, 0)
        heatmap_points_row.setSpacing(8)

        heatmap_label = QLabel("Heatmap")
        heatmap_label.setToolTip("Display-point cap for the preview heatmap row history.")
        heatmap_points_row.addWidget(heatmap_label)

        self.heatmap_rows_spin = QSpinBox()
        make_compact_spinbox(self.heatmap_rows_spin)
        self.heatmap_rows_spin.setRange(16, 100000)
        self.heatmap_rows_spin.setValue(int(getattr(self._window, "_sensorgram_heatmap_history_max_rows", 800)))
        self.heatmap_rows_spin.setToolTip("Maximum number of heatmap rows kept in the preview history.")
        heatmap_points_row.addWidget(self.heatmap_rows_spin)
        heatmap_points_row.addStretch(1)

        heatmap_points_row_widget = QWidget(self)
        heatmap_points_row_widget.setLayout(heatmap_points_row)
        preview_form.addRow("Points rendered", heatmap_points_row_widget)

        preview_note = QLabel("Coming soon: preview mode settings are still in development.")
        preview_note.setWordWrap(True)
        preview_form.addRow("", preview_note)

        outer.addLayout(preview_form)
        outer.addStretch(1)
        return page

    def apply_settings(self) -> None:
        window = self._window
        selector = getattr(self, "metric_mode_selector", None)
        if selector is not None:
            state = selector.state()
            if hasattr(window, "_apply_sensorgram_metric_selection"):
                window._apply_sensorgram_metric_selection(state.visible_modes, state.primary_mode, save=False)
            else:
                window._sensorgram_metric_visible_modes = set(state.visible_modes)
                window._sensorgram_metric_primary_mode = state.primary_mode
        window._plot_antialias_enabled = bool(self.antialias_check.isChecked())
        pg.setConfigOptions(antialias=window._plot_antialias_enabled)
        window._plot_display_points = int(self.metric_display_points_spin.value())
        window._sensorgram_line_step_mode = _normalize_line_mode(self.line_mode_combo.currentData())
        window._sensorgram_line_width_px = max(float(self.line_width_spin.value()), 0.5)
        window._sensorgram_heatmap_history_max_rows = int(self.heatmap_rows_spin.value())
        buffer_fraction = self.follow_latest_buffer_combo.currentData()
        window._metric_autoscale_follow_latest_buffer_fraction = 0.0 if buffer_fraction is None else float(buffer_fraction)
        throttle_s = self.autoscale_throttle_combo.currentData()
        window._metric_autoscale_min_interval_s = float(throttle_s or 0.0)
        window._metric_autoscale_throttle_mode = str(self.autoscale_throttle_combo.currentText())
        window._metric_autoscale_skip_tiny_changes_enabled = bool(self.skip_tiny_changes_check.isChecked())
        window._sensorgram_compression_recent_tail_points = max(int(self.recent_tail_points_spin.value()), 0)
        window._sensorgram_metric_envelope_overlay_enabled = bool(self.envelope_overlay_check.isChecked())
        window._sensorgram_metric_envelope_overlay_alpha = max(int(self.envelope_overlay_alpha_spin.value()), 0)
        window._sensorgram_control_step_overlay_enabled = bool(self.control_overlay_check.isChecked())
        window._sensorgram_control_step_overlay_style = str(self.control_overlay_style_combo.currentData() or "bar")
        window._sensorgram_control_step_overlay_position = self._control_overlay_position()
        window._sensorgram_control_step_overlay_opacity = float(self.control_overlay_opacity_spin.value())
        window._sensorgram_control_step_overlay_bar_height_px = int(self.control_overlay_thickness_spin.value())
        window._spectrum_render_cache_key = None
        if hasattr(window, "_plot_view_cache") and hasattr(window._plot_view_cache, "refresh_live_absolute_metric_cache"):
            try:
                if bool(getattr(window, "_live_active", False)):
                    selected_metrics = set(getattr(window, "_selected_trace_metrics", lambda: [])())
                    archive_path = getattr(window, "_metric_archive_path", None)
                    if hasattr(window._plot_view_cache, "set_live_absolute_metric_tail_size"):
                        window._plot_view_cache.set_live_absolute_metric_tail_size(
                            selected_metrics,
                            recent_tail_points=int(window._sensorgram_compression_recent_tail_points),
                        )
                    window._plot_view_cache.refresh_live_absolute_metric_cache(
                        selected_metrics,
                        target_points=int(window._plot_display_points),
                        archive_path=Path(archive_path) if archive_path else None,
                    )
            except Exception:
                pass
        if hasattr(window, "_apply_sensorgram_display_style"):
            try:
                window._apply_sensorgram_display_style()
            except Exception:
                pass
        if hasattr(window, "_sync_sensorgram_control_step_overlay"):
            try:
                window._sync_sensorgram_control_step_overlay()
            except Exception:
                pass
        if hasattr(window, "_update_trace_stats"):
            try:
                window._update_trace_stats()
            except Exception:
                pass
        if hasattr(window, "_apply_metric_color_styles"):
            try:
                window._apply_metric_color_styles()
            except Exception:
                pass
        if hasattr(window, "_apply_sensorgram_time_axis_mode"):
            try:
                window._apply_sensorgram_time_axis_mode(redraw=False)
            except Exception:
                pass
        if hasattr(window, "_save_ui_state"):
            window._save_ui_state()
        elif hasattr(window, "_schedule_ui_state_persist"):
            window._schedule_ui_state_persist()
        if hasattr(window, "_request_deferred_ui_refresh"):
            window._request_deferred_ui_refresh(trace_plot=True, telemetry=True, live_estimate=True, summary=True)

    def _on_metric_mode_selection_changed(self, visible_modes, primary_mode: str) -> None:
        window = self._window
        if hasattr(window, "_apply_sensorgram_metric_selection"):
            window._apply_sensorgram_metric_selection(visible_modes, primary_mode, save=False)
        else:
            window._sensorgram_metric_visible_modes = set(visible_modes)
            window._sensorgram_metric_primary_mode = primary_mode

    def accept(self) -> None:
        self.apply_settings()
        self._save_dialog_geometry()
        super().accept()

    def reject(self) -> None:
        self._save_dialog_geometry()
        super().reject()

    def closeEvent(self, event) -> None:
        self._save_dialog_geometry()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            focus_widget = self.focusWidget()
            if isinstance(focus_widget, (QComboBox, QAbstractSpinBox, QLineEdit)):
                event.ignore()
                return
        super().keyPressEvent(event)


def show_sensorgram_plot_settings_dialog_for(window) -> None:
    existing = getattr(window, "_sensorgram_settings_dialog", None)
    if existing is not None:
        try:
            if existing.isVisible():
                _log_dialog_timing(window, "sensorgram_settings.reuse", "Sensorgram settings dialog reused while already visible.")
                existing.raise_()
                existing.activateWindow()
                return
            if hasattr(existing, "refresh_from_window"):
                reuse_started = perf_counter()
                existing.refresh_from_window()
                _log_dialog_timing(
                    window,
                    "sensorgram_settings.reuse",
                    f"Sensorgram settings dialog refreshed for reuse in {(perf_counter() - reuse_started) * 1000.0:.2f} ms",
                )
            existing.exec()
            return
        except Exception:
            pass

    started = perf_counter()
    dialog = SensorgramPlotSettingsDialog(window)
    window._sensorgram_settings_dialog = dialog

    def _clear_dialog_reference(*_args) -> None:
        if getattr(window, "_sensorgram_settings_dialog", None) is dialog:
            window._sensorgram_settings_dialog = None

    dialog.destroyed.connect(_clear_dialog_reference)
    _log_dialog_timing(
        window,
        "sensorgram_settings.open",
        f"Sensorgram settings dialog open path completed in {(perf_counter() - started) * 1000.0:.2f} ms before exec()",
    )
    dialog.exec()

