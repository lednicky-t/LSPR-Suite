"""The real experiment-plan table model and its 8 column delegates.

Moved from sLSPR acq's `gui/flow_plan_model.py` (Phase 2, Tier 3a - full
experiment-control panel consolidation, 2026-08-09 - see
docs/architecture/general/lspri_acq_build_log.md's 2026-08-09 entries for the
full design discussion and the maintainer's explicit goal: one real shared
panel implementation, not two apps each maintaining their own version).

Traced before moving, not assumed portable: `ExperimentPlanTableModel.__init__`
takes no window reference at all - it owns its own display-relevant state
(theme palette, tube diameters, switch/valve/color labels, time-unit mode) via
explicit setters the host window calls, not by reading `self._window` on every
access. The 8 delegates duck-type on a `window` parameter for the pieces that
genuinely need it (the host's `plan_table`, `_theme_palette()`,
`_populate_switch_solution_combo()`, `_populate_color_combo()`,
`_color_combo_popup_width()`, `_update_color_combo_style()`, plus several
optional hooks guarded by `hasattr`/`getattr`) - the same duck-typed-host
pattern already proven for `PlanRunLoopMixin` and `experiment_control_builders`.

One real generalization was needed: `to_core_experiment_plan()` requires
`app_name`/`app_version` (a shared package can't import any one app's version
module) - `ExperimentPlanTableModel` now takes both as required keyword-only
constructor args instead of assuming sLSPR acq's identity, matching the
"no default because a wrong one would misrepresent the data" rule already used
elsewhere in this codebase (e.g. `Camera.capabilities()`).

`set_undo_stack(None)` (the default) makes undo a no-op - `push_snapshot`
already handles a `None` stack gracefully, so a host app that hasn't wired up
`QUndoStack` yet (e.g. LSPRi acq, as of this move) gets ordinary, undo-less
editing rather than an error.
"""

from __future__ import annotations

from copy import deepcopy

from PyQt6.QtCore import QAbstractTableModel, QEvent, QModelIndex, Qt, QTimer
from PyQt6.QtGui import QColor, QBrush, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
)

from lspr_core import ExperimentPlan
from lspr_ui import make_compact_spinbox
from .pump_plan import (
    ACTIVE_PUMP_CHANNELS,
    PumpPlanStep,
    clamped_switch_position,
    from_core_flow_step,
    normalized_pump_direction,
    normalized_valve_state,
    recompute_plan_timing,
    to_core_experiment_plan,
)
from .reglo_icc import PUMP_DISPLAY_MAX_LENGTH
from .experiment_control_builders import direction_glyph
from .undo_support import push_snapshot


def safe_color_name(color: str) -> str:
    qcolor = QColor(str(color or "").strip())
    return qcolor.name().upper() if qcolor.isValid() else ""


def _contrast_text_color(color: str) -> str:
    qcolor = QColor(str(color or "").strip())
    if not qcolor.isValid():
        return "#e6ebf1"
    luminance = 0.299 * qcolor.red() + 0.587 * qcolor.green() + 0.114 * qcolor.blue()
    return "#111111" if luminance > 150 else "#f5f7fb"


# The functions below are the single source of truth for how a raw cell value
# (typed directly into the table, or pasted via a copy/paste controller)
# becomes a valid PumpPlanStep field, and how a step field is read back out
# for display/copy. Used by ExperimentPlanTableModel.data()/setData() below.
#
# Each function implements only the success-path coercion rule and may raise
# ValueError/TypeError for input that can't convert - every call site already
# has its own error-handling around the raw computation.


def seconds_to_display_value(seconds: float, time_unit_mode: str) -> float:
    """Convert seconds to the plan table's currently displayed time unit."""
    if time_unit_mode == "min":
        return float(seconds) / 60.0
    if time_unit_mode == "h":
        return float(seconds) / 3600.0
    return float(seconds)


def display_value_to_seconds(value: float, time_unit_mode: str) -> float:
    """Convert a value in the plan table's displayed time unit back to seconds."""
    if time_unit_mode == "min":
        return float(value) * 60.0
    if time_unit_mode == "h":
        return float(value) * 3600.0
    return float(value)


def clamped_flow_ul_min(value: object) -> int:
    return max(round(float(value)), 0)


class ExperimentPlanTableModel(QAbstractTableModel):
    def __init__(self, headers: list[str], parent=None, *, app_name: str, app_version: str) -> None:
        super().__init__(parent)
        self._headers = list(headers)
        self._app_name = app_name
        self._app_version = app_version
        self._steps: list[PumpPlanStep] = []
        self._undo_stack = None
        self._tube_mm_by_channel: list[float] = [0.25] * ACTIVE_PUMP_CHANNELS
        self._switch_solution_labels: list[str] = ["empty"] * 12
        self._color_palette_entries: list[tuple[str, str]] = []
        self._color_name_by_value: dict[str, str] = {}
        self._color_value_by_name: dict[str, str] = {}
        self._valve_state_labels: dict[str, str] = {"Open": "Open", "Close": "Close"}
        self._valve_state_colors: dict[str, str] = {"Open": "#4E79A7", "Close": "#B44A4A"}
        self._theme_palette: dict[str, str] = {}
        self._time_unit_mode = "s"

    def rowCount(self, parent=QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._steps)

    def columnCount(self, parent=QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._headers)

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole):  # type: ignore[override]
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self._headers):
                return self._headers[section]
            return None
        return str(section + 1)

    def set_headers(self, headers: list[str]) -> None:
        self._headers = list(headers)
        if self.columnCount() > 0:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, self.columnCount() - 1)

    def set_theme_palette(self, palette: dict[str, str]) -> None:
        self._theme_palette = dict(palette or {})
        if self.rowCount() > 0 and self.columnCount() > 0:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self.rowCount() - 1, self.columnCount() - 1),
                [Qt.ItemDataRole.BackgroundRole, Qt.ItemDataRole.ForegroundRole],
            )

    def set_time_unit_mode(self, mode: str) -> None:
        normalized = str(mode or "s").strip().lower()
        if normalized not in {"s", "min", "h"}:
            normalized = "s"
        if normalized == self._time_unit_mode:
            return
        self._time_unit_mode = normalized
        if self.rowCount() > 0 and self.columnCount() > 0:
            self.dataChanged.emit(
                self.index(0, 1),
                self.index(self.rowCount() - 1, 3),
                [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole],
            )

    def _seconds_to_display(self, seconds: float) -> float:
        return seconds_to_display_value(seconds, self._time_unit_mode)

    def _display_to_seconds(self, value: float) -> float:
        return display_value_to_seconds(value, self._time_unit_mode)

    def flags(self, index: QModelIndex):  # type: ignore[override]
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        editable_columns = {
            1,
            *[4 + channel_index * 3 for channel_index in range(ACTIVE_PUMP_CHANNELS)],
            5 + ACTIVE_PUMP_CHANNELS * 3,
            6 + ACTIVE_PUMP_CHANNELS * 3,
            7 + ACTIVE_PUMP_CHANNELS * 3,
        }
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() in editable_columns:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # type: ignore[override]
        if not index.isValid():
            return None
        row = index.row()
        column = index.column()
        if row < 0 or row >= len(self._steps):
            return None
        step = self._steps[row]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column == 0:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if column in {1, 2, 3}:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if column in {4 + channel_index * 3 for channel_index in range(ACTIVE_PUMP_CHANNELS)}:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if column in {5 + channel_index * 3 for channel_index in range(ACTIVE_PUMP_CHANNELS)}:
                return int(Qt.AlignmentFlag.AlignCenter)
            if column in {6 + channel_index * 3 for channel_index in range(ACTIVE_PUMP_CHANNELS)}:
                return int(Qt.AlignmentFlag.AlignCenter)
            if column in {4 + ACTIVE_PUMP_CHANNELS * 3, 5 + ACTIVE_PUMP_CHANNELS * 3, 6 + ACTIVE_PUMP_CHANNELS * 3}:
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.BackgroundRole:
            valve_column = 4 + ACTIVE_PUMP_CHANNELS * 3
            if column == valve_column:
                normalized = normalized_valve_state(step.valve)
                color = safe_color_name(self._valve_state_colors.get(normalized, ""))
                if color:
                    return QBrush(QColor(color))
            palette = self._theme_palette
            background = palette.get("button") if row % 2 else palette.get("bg")
            if not background:
                return None
            return QBrush(QColor(background))
        if role == Qt.ItemDataRole.ForegroundRole:
            valve_column = 4 + ACTIVE_PUMP_CHANNELS * 3
            if column == valve_column:
                normalized = normalized_valve_state(step.valve)
                color = safe_color_name(self._valve_state_colors.get(normalized, ""))
                if color:
                    return QBrush(QColor(_contrast_text_color(color)))
            foreground = self._theme_palette.get("fg")
            if not foreground:
                return None
            return QBrush(QColor(foreground))
        if role == Qt.ItemDataRole.ToolTipRole:
            if column in {2, 3}:
                return f"Step {step.step} time"
            if column in {6 + channel_index * 3 for channel_index in range(ACTIVE_PUMP_CHANNELS)}:
                channel_index = (column - 6) // 3
                return f"Tubing inner diameter for CH{channel_index + 1} in mm."
            if column in {5 + channel_index * 3 for channel_index in range(ACTIVE_PUMP_CHANNELS)}:
                channel_index = (column - 5) // 3
                return f"Direction for CH{channel_index + 1}."
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if column == 6 + ACTIVE_PUMP_CHANNELS * 3 and role == Qt.ItemDataRole.UserRole:
                return safe_color_name(step.color)
            return None

        if column == 0:
            return str(step.step)
        if column == 1:
            display_value = self._seconds_to_display(step.duration_s)
            return f"{display_value:.{1 if self._time_unit_mode == 'min' else 2 if self._time_unit_mode == 'h' else 0}f}" if self._time_unit_mode != "s" else f"{display_value:g}"
        if column == 2:
            display_value = self._seconds_to_display(step.start_s)
            return f"{display_value:.{1 if self._time_unit_mode == 'min' else 2 if self._time_unit_mode == 'h' else 0}f}" if self._time_unit_mode != "s" else f"{display_value:g}"
        if column == 3:
            display_value = self._seconds_to_display(step.end_s)
            return f"{display_value:.{1 if self._time_unit_mode == 'min' else 2 if self._time_unit_mode == 'h' else 0}f}" if self._time_unit_mode != "s" else f"{display_value:g}"

        flow_start = 4
        channel_count = ACTIVE_PUMP_CHANNELS
        if flow_start <= column < flow_start + channel_count * 3:
            offset = column - flow_start
            channel_index = offset // 3
            field = offset % 3
            channel = step.channels[channel_index]
            if field == 0:
                return f"{clamped_flow_ul_min(channel.flow_ul_min):g}"
            if field == 1:
                return normalized_pump_direction(channel.direction)
            tube_value = self._tube_mm_by_channel[channel_index] if channel_index < len(self._tube_mm_by_channel) else 0.25
            return f"{float(tube_value):.2f}"

        valve_column = flow_start + channel_count * 3
        switch_column = valve_column + 1
        color_column = valve_column + 2
        description_column = valve_column + 3

        if column == valve_column:
            normalized = normalized_valve_state(step.valve)
            if role == Qt.ItemDataRole.EditRole:
                return normalized
            label = str(self._valve_state_labels.get(normalized, normalized)).strip()
            return label or normalized
        if column == switch_column:
            position = clamped_switch_position(step.switch_position)
            if role == Qt.ItemDataRole.EditRole:
                return position
            label = self._switch_solution_labels[position - 1] if position - 1 < len(self._switch_solution_labels) else "empty"
            label = str(label or "").strip() or "empty"
            return f"{position}: {label}"
        if column == color_column:
            color_name = self._color_name_by_value.get(safe_color_name(step.color), "")
            if role == Qt.ItemDataRole.EditRole:
                return safe_color_name(step.color) or "#4E79A7"
            return color_name or safe_color_name(step.color) or "#4E79A7"
        if column == description_column:
            return str(step.description or "")
        return None

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole):  # type: ignore[override]
        if not index.isValid() or role not in (Qt.ItemDataRole.EditRole, Qt.ItemDataRole.DisplayRole):
            return False
        row = index.row()
        column = index.column()
        if row < 0 or row >= len(self._steps):
            return False
        step = deepcopy(self._steps[row])
        changed = False
        field_label = "value"

        if column == 1:
            step.duration_s = max(self._display_to_seconds(float(value)), 0.0)
            changed = True
            field_label = "duration"
        else:
            flow_start = 4
            channel_count = ACTIVE_PUMP_CHANNELS
            if flow_start <= column < flow_start + channel_count * 3:
                offset = column - flow_start
                channel_index = offset // 3
                field = offset % 3
                if field == 0:
                    step.channels[channel_index].flow_ul_min = clamped_flow_ul_min(value)
                    changed = True
                    field_label = f"CH{channel_index + 1} flow"
                elif field == 1:
                    step.channels[channel_index].direction = normalized_pump_direction(value)
                    changed = True
                    field_label = f"CH{channel_index + 1} direction"
            else:
                valve_column = flow_start + channel_count * 3
                switch_column = valve_column + 1
                color_column = valve_column + 2
                description_column = valve_column + 3
                if column == valve_column:
                    step.valve = normalized_valve_state(value)
                    changed = True
                    field_label = "valve"
                elif column == switch_column:
                    step.switch_position = clamped_switch_position(value)
                    changed = True
                    field_label = "switch"
                elif column == color_column:
                    step.color = safe_color_name(str(value)) or "#4E79A7"
                    changed = True
                    field_label = "color"
                elif column == description_column:
                    step.description = str(value or "").strip()
                    changed = True
                    field_label = "comment"

        if not changed:
            return False

        before = deepcopy(self._steps)
        after = list(self._steps)
        after[row] = step
        after = recompute_plan_timing(after)

        if self._undo_stack is not None:
            push_snapshot(self._undo_stack, f"Edit step {step.step} {field_label}", before, after, apply=self._apply_steps_snapshot)
        else:
            self._apply_steps_snapshot(after)
        return True

    def set_undo_stack(self, undo_stack) -> None:
        self._undo_stack = undo_stack

    def _apply_steps_snapshot(self, steps: list[PumpPlanStep]) -> None:
        """Replace the full step list and repaint - the shared apply used both
        for a normal setData() commit and for undo/redo replay of it, so both
        paths stay in sync. Deliberately not a full model reset (unlike
        set_steps()): reusing the model's own index/selection-preserving
        dataChanged emit keeps the current cell/selection intact across an
        undo, matching what a live edit already does."""
        self._steps = steps
        top_left = self.index(0, 0)
        bottom_right = self.index(max(len(self._steps) - 1, 0), max(self.columnCount() - 1, 0))
        self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole, Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.TextAlignmentRole])

    def set_steps(self, steps: list[PumpPlanStep]) -> None:
        self.set_flow_plan(to_core_experiment_plan(steps, app_name=self._app_name, app_version=self._app_version))

    def set_single_step(self, step: PumpPlanStep) -> None:
        self.beginResetModel()
        try:
            preserved = deepcopy(step)
            preserved.step = int(step.step)
            self._steps = recompute_plan_timing([preserved])
            if self._steps:
                self._steps[0].step = int(step.step)
        finally:
            self.endResetModel()

    def set_flow_plan(self, flow_plan: ExperimentPlan) -> None:
        self.beginResetModel()
        try:
            ordered = flow_plan.ordered()
            converted: list[PumpPlanStep] = []
            for index, core_step in enumerate(ordered, start=1):
                template = PumpPlanStep(step=index)
                converted.append(from_core_flow_step(core_step, template=template))
            self._steps = recompute_plan_timing(converted)
        finally:
            self.endResetModel()

    def steps(self) -> list[PumpPlanStep]:
        return deepcopy(self._steps)

    def flow_plan(self) -> ExperimentPlan:
        return to_core_experiment_plan(self._steps, app_name=self._app_name, app_version=self._app_version)

    def step_at(self, row: int) -> PumpPlanStep | None:
        if row < 0 or row >= len(self._steps):
            return None
        return deepcopy(self._steps[row])

    def set_tube_mm_by_channel(self, values: list[float]) -> None:
        updated = [max(float(value), 0.0) for value in values[:ACTIVE_PUMP_CHANNELS]]
        if len(updated) < ACTIVE_PUMP_CHANNELS:
            updated.extend([0.25] * (ACTIVE_PUMP_CHANNELS - len(updated)))
        self._tube_mm_by_channel = updated
        if not self._steps:
            return
        left = self.index(0, 4 + 2)
        right = self.index(max(len(self._steps) - 1, 0), 4 + ACTIVE_PUMP_CHANNELS * 3 - 1)
        self.dataChanged.emit(left, right, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole, Qt.ItemDataRole.ToolTipRole])

    def set_switch_solution_labels(self, labels: list[str], mode: bool | None = None) -> None:
        normalized = [str(label or "").strip() or "empty" for label in labels[:12]]
        if len(normalized) < 12:
            normalized.extend(["empty"] * (12 - len(normalized)))
        self._switch_solution_labels = normalized
        if not self._steps:
            return
        left = self.index(0, 4 + ACTIVE_PUMP_CHANNELS * 3 + 1)
        right = self.index(max(len(self._steps) - 1, 0), 4 + ACTIVE_PUMP_CHANNELS * 3 + 1)
        self.dataChanged.emit(left, right, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])

    def set_color_options(self, entries: list[tuple[str, str]]) -> None:
        self._color_palette_entries = [(str(label), safe_color_name(color) or "#4E79A7") for label, color in entries]
        self._color_name_by_value = {color: label for label, color in self._color_palette_entries}
        self._color_value_by_name = {label: color for label, color in self._color_palette_entries}
        if not self._steps:
            return
        left = self.index(0, 4 + ACTIVE_PUMP_CHANNELS * 3 + 2)
        right = self.index(max(len(self._steps) - 1, 0), 4 + ACTIVE_PUMP_CHANNELS * 3 + 2)
        self.dataChanged.emit(left, right, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole, Qt.ItemDataRole.UserRole])

    def set_valve_state_labels(self, labels: dict[str, str]) -> None:
        normalized = {"Open": "Open", "Close": "Close"}
        for key in ("Open", "Close"):
            normalized[key] = str(labels.get(key, key)).strip() or key
        self._valve_state_labels = normalized
        if not self._steps:
            return
        valve_column = 4 + ACTIVE_PUMP_CHANNELS * 3
        left = self.index(0, valve_column)
        right = self.index(max(len(self._steps) - 1, 0), valve_column)
        self.dataChanged.emit(left, right, [Qt.ItemDataRole.DisplayRole])

    def set_valve_state_colors(self, colors: dict[str, str]) -> None:
        normalized = {"Open": "#4E79A7", "Close": "#B44A4A"}
        for key in ("Open", "Close"):
            color = safe_color_name(colors.get(key, ""))
            if color:
                normalized[key] = color
        self._valve_state_colors = normalized
        if not self._steps:
            return
        valve_column = 4 + ACTIVE_PUMP_CHANNELS * 3
        left = self.index(0, valve_column)
        right = self.index(max(len(self._steps) - 1, 0), valve_column)
        self.dataChanged.emit(left, right, [Qt.ItemDataRole.BackgroundRole, Qt.ItemDataRole.ForegroundRole])

    def toggle_valve(self, row: int) -> bool:
        if row < 0 or row >= len(self._steps):
            return False
        index = self.index(row, 4 + ACTIVE_PUMP_CHANNELS * 3)
        current = str(index.data(Qt.ItemDataRole.EditRole) or index.data(Qt.ItemDataRole.DisplayRole) or "Open")
        new_value = "Close" if current.strip().lower() != "close" else "Open"
        return self.setData(index, new_value, Qt.ItemDataRole.EditRole)

    def cycle_switch(self, row: int, delta: int) -> bool:
        if row < 0 or row >= len(self._steps):
            return False
        index = self.index(row, 4 + ACTIVE_PUMP_CHANNELS * 3 + 1)
        current = index.data(Qt.ItemDataRole.EditRole)
        try:
            current_value = int(current) if current is not None else 1
        except (TypeError, ValueError):
            current_value = 1
        new_value = ((current_value - 1 + int(delta)) % 12) + 1
        return self.setData(index, new_value, Qt.ItemDataRole.EditRole)

    def cycle_color(self, row: int, delta: int) -> bool:
        if row < 0 or row >= len(self._steps):
            return False
        index = self.index(row, 4 + ACTIVE_PUMP_CHANNELS * 3 + 2)
        palette = [color for _label, color in self._color_palette_entries] or ["#4E79A7"]
        current = str(index.data(Qt.ItemDataRole.EditRole) or index.data(Qt.ItemDataRole.DisplayRole) or "").strip()
        if current in palette:
            current_index = palette.index(current)
        else:
            current_index = 0
        new_value = palette[(current_index + int(delta)) % len(palette)]
        return self.setData(index, new_value, Qt.ItemDataRole.EditRole)


class _BaseFlowDelegate(QStyledItemDelegate):
    def __init__(self, window) -> None:
        super().__init__(window.plan_table)
        self._window = window

    def paint(self, painter, option, index):  # type: ignore[override]
        if hasattr(self._window, "_plan_table_is_editing") and self._window._plan_table_is_editing(index.row(), index.column()):
            opt = QStyleOptionViewItem(option)
            opt.state &= ~QStyle.StateFlag.State_HasFocus
            opt.state &= ~QStyle.StateFlag.State_Selected
            opt.state &= ~QStyle.StateFlag.State_MouseOver
            opt.showDecorationSelected = False
            opt.text = ""
            super().paint(painter, opt, index)
            return
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        opt.state &= ~QStyle.StateFlag.State_Selected
        opt.state &= ~QStyle.StateFlag.State_MouseOver
        opt.showDecorationSelected = False
        super().paint(painter, opt, index)

    def _prepare_editor(self, editor, index: QModelIndex) -> None:
        editor.setProperty("flow_navigation", True)
        editor.setProperty("flow_row", int(index.row()))
        editor.setProperty("flow_column", int(index.column()))
        editor.installEventFilter(self._window)
        self._window._install_table_wheel_scroll_filter(editor)
        if hasattr(self._window, "_begin_plan_table_edit"):
            self._window._begin_plan_table_edit(int(index.row()), int(index.column()))
        if hasattr(self._window, "_end_plan_table_edit"):
            editor.destroyed.connect(
                lambda *_args, r=int(index.row()), c=int(index.column()): self._window._end_plan_table_edit(r, c)
            )
        if isinstance(editor, QComboBox):
            editor.setProperty("open_popup_on_click", True)
            # Open immediately so the cell behaves like a dropdown field instead of requiring a second click.
            if getattr(self._window, "_ui_startup_ready", False):
                QTimer.singleShot(0, editor.showPopup)

    def _style_combo_editor(self, editor: QComboBox, *, width: int | None = None) -> None:
        editor.setAutoFillBackground(True)
        editor.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        editor.setFrame(False)
        if width is not None:
            editor.setFixedWidth(width)
        p = self._window._theme_palette()
        bg = p.get("field", "#f4f6f8")
        fg = p.get("fg", "#1d2733")
        hl = p.get("selection", "#dbeafe")
        editor.setStyleSheet(
            f"QComboBox {{ background-color: {bg}; color: {fg}; border: none; padding: 0px 1px; margin: 0px; }}"
            f"QComboBox QLineEdit {{ background-color: {bg}; color: {fg}; border: none; padding: 0px; margin: 0px; }}"
            "QComboBox::drop-down { border: none; width: 0px; }"
            "QComboBox::down-arrow { width: 0px; height: 0px; }"
            f"QComboBox QAbstractItemView {{ selection-background-color: {hl}; }}"
        )

    def _match_editor_height(self, editor, option) -> None:
        try:
            height = max(int(option.rect.height()), 18)
        except Exception:
            height = 20
        editor.setFixedHeight(height)

    def _style_spin_editor(self, editor: QDoubleSpinBox) -> None:
        editor.setAutoFillBackground(True)
        editor.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        p = self._window._theme_palette()
        bg = p.get("field", "#f4f6f8")
        fg = p.get("fg", "#1d2733")
        editor.setStyleSheet(
            f"QDoubleSpinBox {{ background-color: {bg}; color: {fg}; border: none; padding: 0px 2px; margin: 0px; }}"
            f"QDoubleSpinBox QLineEdit {{ background-color: {bg}; color: {fg}; border: none; padding: 0px; margin: 0px; }}"
            "QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0px; border: none; }"
        )

    def _commit_combo_editor_value(self, editor, index: QModelIndex) -> None:
        model = index.model()
        if model is None:
            return
        value = editor.currentData()
        if value is None:
            value = editor.currentIndex()
        model.setData(index, value, Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):  # type: ignore[override]
        rect = option.rect.adjusted(0, 0, 0, 0)
        editor.setGeometry(rect)


class ExperimentPlanDurationDelegate(_BaseFlowDelegate):
    def createEditor(self, parent, option, index):  # type: ignore[override]
        editor = QDoubleSpinBox(parent)
        make_compact_spinbox(editor)
        self._style_spin_editor(editor)
        self._match_editor_height(editor, option)
        editor.setRange(0.0, 86400.0)
        decimals = int(self._window._duration_display_decimals()) if hasattr(self._window, "_duration_display_decimals") else 0
        editor.setDecimals(decimals)
        editor.setSingleStep(1.0 if decimals == 0 else (0.1 if decimals == 1 else 0.01))
        editor.setFrame(False)
        editor.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        editor.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._prepare_editor(editor, index)
        editor.editingFinished.connect(lambda ed=editor: self.commitData.emit(ed))
        editor.editingFinished.connect(lambda ed=editor: self.closeEditor.emit(ed))
        return editor

    def setEditorData(self, editor, index):  # type: ignore[override]
        editor.setValue(float(index.data(Qt.ItemDataRole.EditRole) or 0.0))

    def setModelData(self, editor, model, index):  # type: ignore[override]
        model.setData(index, float(editor.value()), Qt.ItemDataRole.EditRole)


class ExperimentPlanFlowDelegate(_BaseFlowDelegate):
    def createEditor(self, parent, option, index):  # type: ignore[override]
        editor = QDoubleSpinBox(parent)
        make_compact_spinbox(editor)
        self._style_spin_editor(editor)
        self._match_editor_height(editor, option)
        editor.setRange(0.0, 9999.0)
        editor.setDecimals(0)
        editor.setSingleStep(1.0)
        editor.setFrame(False)
        editor.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        editor.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._prepare_editor(editor, index)
        editor.editingFinished.connect(lambda ed=editor: self.commitData.emit(ed))
        editor.editingFinished.connect(lambda ed=editor: self.closeEditor.emit(ed))
        return editor

    def setEditorData(self, editor, index):  # type: ignore[override]
        editor.setValue(float(index.data(Qt.ItemDataRole.EditRole) or 0.0))

    def setModelData(self, editor, model, index):  # type: ignore[override]
        model.setData(index, max(round(float(editor.value())), 0), Qt.ItemDataRole.EditRole)


class ExperimentPlanValveDelegate(_BaseFlowDelegate):
    def createEditor(self, parent, option, index):  # type: ignore[override]
        _ = parent, option, index
        return None

    def paint(self, painter: QPainter, option, index) -> None:  # pragma: no cover - GUI runtime path
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "Open").strip() or "Open"
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        bg_brush = index.data(Qt.ItemDataRole.BackgroundRole)
        if isinstance(bg_brush, QBrush):
            painter.fillRect(option.rect, bg_brush)
            bg_color = bg_brush.color()
        else:
            bg_color = QColor(self._window._theme_palette().get("bg", "#f4f6f8"))
            painter.fillRect(option.rect, bg_color)

        fg_brush = index.data(Qt.ItemDataRole.ForegroundRole)
        if isinstance(fg_brush, QBrush) and fg_brush.color().isValid():
            text_color = fg_brush.color()
        else:
            text_color = QColor(_contrast_text_color(bg_color.name()))

        painter.setPen(QPen(text_color))
        painter.drawText(option.rect.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def editorEvent(self, event, model, option, index):  # type: ignore[override]
        if event.type() == QEvent.Type.MouseButtonRelease and getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton:
            current = str(index.data(Qt.ItemDataRole.EditRole) or index.data(Qt.ItemDataRole.DisplayRole) or "Open").strip().lower()
            next_value = "Close" if current != "close" else "Open"
            return bool(model.setData(index, next_value, Qt.ItemDataRole.EditRole))
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                current = str(index.data(Qt.ItemDataRole.EditRole) or index.data(Qt.ItemDataRole.DisplayRole) or "Open").strip().lower()
                next_value = "Close" if current != "close" else "Open"
                return bool(model.setData(index, next_value, Qt.ItemDataRole.EditRole))
        return False


class ExperimentPlanDirectionDelegate(_BaseFlowDelegate):
    """Click (or scroll - see the host's wheel-cycle handling) to toggle
    CW/CCW, drawn as the same rotation-arrow glyph as the manual-control
    panel's direction buttons (create_direction_button in
    experiment_control_builders.py) instead of plain "CW"/"CCW" text."""

    def createEditor(self, parent, option, index):  # type: ignore[override]
        _ = parent, option, index
        return None

    def paint(self, painter: QPainter, option, index) -> None:  # pragma: no cover - GUI runtime path
        direction = str(index.data(Qt.ItemDataRole.EditRole) or "CW").strip().upper()
        direction = "CCW" if direction == "CCW" else "CW"
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        bg_brush = index.data(Qt.ItemDataRole.BackgroundRole)
        if isinstance(bg_brush, QBrush):
            painter.fillRect(option.rect, bg_brush)
            bg_color = bg_brush.color()
        else:
            bg_color = QColor(self._window._theme_palette().get("bg", "#f4f6f8"))
            painter.fillRect(option.rect, bg_color)

        fg_brush = index.data(Qt.ItemDataRole.ForegroundRole)
        if isinstance(fg_brush, QBrush) and fg_brush.color().isValid():
            text_color = fg_brush.color()
        else:
            text_color = QColor(_contrast_text_color(bg_color.name()))

        font = painter.font()
        font.setPointSizeF(max(font.pointSizeF(), 10.0) * 1.2)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(text_color))
        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, direction_glyph(direction))
        painter.restore()

    def _toggle(self, model, index: QModelIndex) -> bool:
        current = str(index.data(Qt.ItemDataRole.EditRole) or "CW").strip().upper()
        next_value = "CW" if current == "CCW" else "CCW"
        return bool(model.setData(index, next_value, Qt.ItemDataRole.EditRole))

    def editorEvent(self, event, model, option, index):  # type: ignore[override]
        if event.type() == QEvent.Type.MouseButtonRelease and getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton:
            return self._toggle(model, index)
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                return self._toggle(model, index)
        return False


class _ColorPopupDelegate(QStyledItemDelegate):
    def __init__(self, window) -> None:
        super().__init__(window.plan_table)
        self._window = window

    def paint(self, painter: QPainter, option, index) -> None:  # pragma: no cover - GUI runtime path
        painter.save()
        bg_brush = index.data(Qt.ItemDataRole.BackgroundRole)
        if isinstance(bg_brush, QBrush) and bg_brush.color().isValid():
            bg_color = bg_brush.color()
        elif isinstance(bg_brush, QColor) and bg_brush.isValid():
            bg_color = bg_brush
        else:
            bg_color = QColor(self._window._theme_palette().get("field", "#f4f6f8"))
        painter.fillRect(option.rect, bg_color)

        text = str(index.data(Qt.ItemDataRole.DisplayRole) or index.data(Qt.ItemDataRole.EditRole) or "").strip()
        fg_brush = index.data(Qt.ItemDataRole.ForegroundRole)
        if isinstance(fg_brush, QBrush) and fg_brush.color().isValid():
            text_color = fg_brush.color()
        elif isinstance(fg_brush, QColor) and fg_brush.isValid():
            text_color = fg_brush
        else:
            text_color = QColor(_contrast_text_color(bg_color.name()))

        if option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver):
            accent = QColor(self._window._theme_palette().get("selection", "#d8b44a"))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(accent, 1.4))
            painter.drawRect(option.rect.adjusted(0, 0, -1, -1))

        painter.setPen(QPen(text_color))
        painter.drawText(option.rect.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class ExperimentPlanSwitchDelegate(_BaseFlowDelegate):
    def createEditor(self, parent, option, index):  # type: ignore[override]
        editor = QComboBox(parent)
        editor.setEditable(False)
        self._style_combo_editor(editor, width=max(int(option.rect.width()), 96))
        self._match_editor_height(editor, option)
        editor.setEditable(True)
        line_edit = editor.lineEdit()
        if line_edit is not None:
            line_edit.setReadOnly(True)
            line_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            line_edit.setFrame(False)
            line_edit.setAutoFillBackground(True)
            line_edit.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._window._populate_switch_solution_combo(
            editor,
            int(index.data(Qt.ItemDataRole.EditRole) or 1),
        )
        self._prepare_editor(editor, index)
        editor.activated.connect(lambda _index, ed=editor, ix=index: self._commit_combo_editor_value(ed, ix))
        editor.activated.connect(lambda _index, ed=editor: self.closeEditor.emit(ed))
        return editor

    def setEditorData(self, editor, index):  # type: ignore[override]
        current_position = max(min(int(index.data(Qt.ItemDataRole.EditRole) or 1), 12), 1)
        combo_index = editor.findData(current_position)
        if combo_index >= 0:
            editor.blockSignals(True)
            editor.setCurrentIndex(combo_index)
            editor.blockSignals(False)
        line_edit = editor.lineEdit()
        if line_edit is not None:
            line_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def setModelData(self, editor, model, index):  # type: ignore[override]
        current_data = editor.currentData()
        try:
            value = int(current_data) if current_data is not None else int(editor.currentIndex() + 1)
        except (TypeError, ValueError):
            value = 1
        model.setData(index, value, Qt.ItemDataRole.EditRole)


class ExperimentPlanColorDelegate(_BaseFlowDelegate):
    def paint(self, painter: QPainter, option, index) -> None:  # pragma: no cover - GUI runtime path
        value = str(index.data(Qt.ItemDataRole.EditRole) or index.data(Qt.ItemDataRole.UserRole) or index.data(Qt.ItemDataRole.DisplayRole) or "").strip()
        color = QColor(value)
        if not color.isValid():
            color = QColor("#4E79A7")

        painter.save()
        painter.fillRect(option.rect, color)

        luminance = (0.2126 * color.redF()) + (0.7152 * color.greenF()) + (0.0722 * color.blueF())
        text_color = QColor("#111111" if luminance > 0.62 else "#f5f7fb")
        if option.state & QStyle.StateFlag.State_Selected:
            accent = QColor("#d8b44a")
            accent.setAlpha(220)
            painter.setPen(QPen(accent, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(option.rect.adjusted(0, 0, -1, -1))

        painter.setPen(QPen(text_color))
        text_rect = option.rect.adjusted(4, 0, -4, 0)
        label = painter.fontMetrics().elidedText(
            str(index.data(Qt.ItemDataRole.DisplayRole) or value or "#4E79A7"),
            Qt.TextElideMode.ElideRight,
            max(text_rect.width(), 10),
        )
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()

    def createEditor(self, parent, option, index):  # type: ignore[override]
        editor = QComboBox(parent)
        editor.setEditable(True)
        self._style_combo_editor(editor, width=max(int(option.rect.width()), 96))
        self._match_editor_height(editor, option)
        editor.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        editor.setMinimumContentsLength(5)
        line_edit = editor.lineEdit()
        if line_edit is not None:
            line_edit.setAutoFillBackground(True)
            line_edit.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            line_edit.setReadOnly(True)
            line_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            line_edit.setFrame(False)
            line_edit.setFixedHeight(editor.height())
        self._window._populate_color_combo(editor)
        current_color = str(index.data(Qt.ItemDataRole.EditRole) or index.data(Qt.ItemDataRole.UserRole) or "").strip()
        current_index = editor.findData(current_color)
        if current_index >= 0:
            editor.setCurrentIndex(current_index)
        self._prepare_editor(editor, index)
        popup_width = max(int(option.rect.width()), self._window._color_combo_popup_width(editor))
        popup = editor.view()
        popup.setMinimumWidth(popup_width)
        popup.setObjectName("flowColorPopup")
        popup.setMouseTracking(True)
        popup.viewport().setMouseTracking(True)
        popup.setItemDelegate(_ColorPopupDelegate(self._window))
        palette = self._window._theme_palette()
        popup.setStyleSheet(
            "QListView#flowColorPopup {"
            f" background: {palette['field']};"
            f" color: {palette['fg']};"
            f" border: 1px solid {palette['border']};"
            " border-radius: 0px;"
            " padding: 0px;"
            " outline: none;"
            "}"
            "QListView#flowColorPopup::item:hover {"
            " background: transparent;"
            f" border: 1px solid {palette['selection']};"
            "}"
        )
        self._window._update_color_combo_style(editor)
        editor.currentIndexChanged.connect(lambda _index, ed=editor: self._window._update_color_combo_style(ed))
        editor.activated.connect(lambda _index, ed=editor, ix=index: self._commit_combo_editor_value(ed, ix))
        editor.activated.connect(lambda _index, ed=editor: self.closeEditor.emit(ed))
        return editor

    def setEditorData(self, editor, index):  # type: ignore[override]
        current_color = str(index.data(Qt.ItemDataRole.EditRole) or index.data(Qt.ItemDataRole.UserRole) or "").strip()
        current_index = editor.findData(current_color)
        if current_index >= 0:
            editor.blockSignals(True)
            editor.setCurrentIndex(current_index)
            editor.blockSignals(False)
        self._window._update_color_combo_style(editor)

    def setModelData(self, editor, model, index):  # type: ignore[override]
        color = str(editor.currentData() or "").strip()
        model.setData(index, color, Qt.ItemDataRole.EditRole)


_PUMP_DISPLAY_OVERFLOW_COLOR = "#c97a7a"


def _draw_split_comment_text(
    painter: QPainter,
    rect,
    text: str,
    limit: int,
    fits_color: QColor,
    overflow_color: QColor,
    alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
) -> None:
    """Paint *text* with the part past *limit* characters in *overflow_color* and
    the rest in *fits_color*, side by side from rect's left edge. Shared by the
    plan table's Comment cell paint() and the inline editor's live paintEvent
    (below) so the two stay visually identical."""
    fits_text = text[:limit]
    overflow_text = text[limit:]
    painter.setPen(QPen(fits_color))
    painter.drawText(rect, alignment, fits_text)
    if overflow_text:
        fits_width = QFontMetrics(painter.font()).horizontalAdvance(fits_text)
        painter.setPen(QPen(overflow_color))
        painter.drawText(rect.adjusted(fits_width, 0, 0, 0), alignment, overflow_text)


class _HighlightingCommentLineEdit(QLineEdit):
    """A QLineEdit that live-splits its own text at the pump display's
    16-character limit while the user is actively typing, the same way a
    committed cell is painted by ExperimentPlanCommentDelegate.paint().

    Only takes over painting while the full text fits the field without
    horizontal scrolling: QLineEdit's internal scroll offset isn't public
    API, so re-deriving it for the custom-painted case isn't attempted here.
    Once a comment is long enough to need scrolling, this falls back to
    normal single-color native rendering while you're actively editing it -
    the split still applies correctly once the edit is committed, since the
    plan table's own cell painting (unlike this inline editor) has no such
    limitation.
    """

    def __init__(
        self,
        parent=None,
        *,
        highlight_active: bool = False,
        bg_color: QColor = QColor("#f4f6f8"),
        fits_color: QColor = QColor("#1d2733"),
        overflow_color: QColor = QColor(_PUMP_DISPLAY_OVERFLOW_COLOR),
        selection_color: QColor = QColor("#dbeafe"),
    ) -> None:
        super().__init__(parent)
        self._highlight_active = highlight_active
        self._bg_color = QColor(bg_color)
        self._fits_color = QColor(fits_color)
        self._overflow_color = QColor(overflow_color)
        self._selection_color = QColor(selection_color)
        self._cursor_blink_visible = True
        app = QApplication.instance()
        blink_ms = max(int(app.cursorFlashTime() // 2), 250) if app is not None else 500
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(blink_ms)
        self._blink_timer.timeout.connect(self._toggle_cursor_blink)
        self.cursorPositionChanged.connect(lambda *_args: self.update())
        self.selectionChanged.connect(self.update)
        self.textChanged.connect(lambda *_args: self.update())

    def _toggle_cursor_blink(self) -> None:
        self._cursor_blink_visible = not self._cursor_blink_visible
        self.update()

    def focusInEvent(self, event) -> None:  # type: ignore[override]
        super().focusInEvent(event)
        self._cursor_blink_visible = True
        self._blink_timer.start()

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        self._blink_timer.stop()
        self._cursor_blink_visible = False
        super().focusOutEvent(event)
        self.update()

    def _text_rect(self):
        return self.rect().adjusted(4, 0, -4, 0)

    def _fits_without_scrolling(self) -> bool:
        return QFontMetrics(self.font()).horizontalAdvance(self.text()) <= self._text_rect().width()

    def paintEvent(self, event) -> None:  # type: ignore[override]  # pragma: no cover - GUI runtime path
        text = self.text()
        if not self._highlight_active or len(text) <= PUMP_DISPLAY_MAX_LENGTH or not self._fits_without_scrolling():
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), self._bg_color)

        metrics = QFontMetrics(self.font())
        rect = self._text_rect()

        sel_start = self.selectionStart()
        if sel_start >= 0:
            sel_end = sel_start + len(self.selectedText())
            x1 = rect.left() + metrics.horizontalAdvance(text[:sel_start])
            x2 = rect.left() + metrics.horizontalAdvance(text[:sel_end])
            painter.fillRect(x1, rect.top(), x2 - x1, rect.height(), self._selection_color)

        _draw_split_comment_text(painter, rect, text, PUMP_DISPLAY_MAX_LENGTH, self._fits_color, self._overflow_color)

        if self.hasFocus() and self._cursor_blink_visible:
            cursor_x = rect.left() + metrics.horizontalAdvance(text[: self.cursorPosition()])
            painter.setPen(QPen(self._fits_color))
            painter.drawLine(cursor_x, rect.top() + 1, cursor_x, rect.bottom() - 1)

        painter.end()


class ExperimentPlanCommentDelegate(_BaseFlowDelegate):
    def createEditor(self, parent, option, index):  # type: ignore[override]
        background = index.data(Qt.ItemDataRole.BackgroundRole)
        if isinstance(background, QBrush) and background.color().isValid():
            bg_color = background.color().name()
        else:
            bg_color = self._window._theme_palette().get("bg", "#f4f6f8")
        fg_color = self._window._theme_palette().get("fg", "#1d2733")
        selection_color = self._window._theme_palette().get("selection", "#dbeafe")
        highlight_active = bool(
            getattr(self._window, "_pump_display_enabled", False)
            and getattr(self._window, "_pump_display_highlight_enabled", False)
        )

        editor = _HighlightingCommentLineEdit(
            parent,
            highlight_active=highlight_active,
            bg_color=QColor(bg_color),
            fits_color=QColor(fg_color),
            overflow_color=QColor(_PUMP_DISPLAY_OVERFLOW_COLOR),
            selection_color=QColor(selection_color),
        )
        editor.setFrame(False)
        editor.setAutoFillBackground(True)
        editor.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        editor.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        editor.setPlaceholderText("")
        self._match_editor_height(editor, option)
        self._prepare_editor(editor, index)

        editor.setStyleSheet(
            "QLineEdit {"
            f" background: {bg_color};"
            f" color: {fg_color};"
            " border: none;"
            " padding: 0px 4px;"
            " margin: 0px;"
            " }"
            "QLineEdit:focus {"
            f" background: {bg_color};"
            f" color: {fg_color};"
            " border: none;"
            " outline: none;"
            " }"
        )
        editor.editingFinished.connect(lambda ed=editor: self.commitData.emit(ed))
        editor.editingFinished.connect(lambda ed=editor: self.closeEditor.emit(ed))
        return editor

    def setEditorData(self, editor, index):  # type: ignore[override]
        if isinstance(editor, QLineEdit):
            editor.setText(str(index.data(Qt.ItemDataRole.EditRole) or index.data(Qt.ItemDataRole.DisplayRole) or ""))
            editor.selectAll()
            return
        super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):  # type: ignore[override]
        if isinstance(editor, QLineEdit):
            model.setData(index, editor.text(), Qt.ItemDataRole.EditRole)
            return
        super().setModelData(editor, model, index)

    def paint(self, painter: QPainter, option, index) -> None:  # pragma: no cover - GUI runtime path
        if hasattr(self._window, "_plan_table_is_editing") and self._window._plan_table_is_editing(index.row(), index.column()):
            super().paint(painter, option, index)
            return

        highlight = bool(
            getattr(self._window, "_pump_display_enabled", False)
            and getattr(self._window, "_pump_display_highlight_enabled", False)
        )
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")

        if not highlight or len(text) <= PUMP_DISPLAY_MAX_LENGTH:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        bg_brush = index.data(Qt.ItemDataRole.BackgroundRole)
        if isinstance(bg_brush, QBrush):
            painter.fillRect(option.rect, bg_brush)
            bg_color = bg_brush.color()
        else:
            bg_color = QColor(self._window._theme_palette().get("bg", "#f4f6f8"))
            painter.fillRect(option.rect, bg_color)

        fg_brush = index.data(Qt.ItemDataRole.ForegroundRole)
        if isinstance(fg_brush, QBrush) and fg_brush.color().isValid():
            fits_color = fg_brush.color()
        else:
            fits_color = QColor(_contrast_text_color(bg_color.name()))

        rect = option.rect.adjusted(4, 0, -4, 0)
        _draw_split_comment_text(
            painter, rect, text, PUMP_DISPLAY_MAX_LENGTH, fits_color, QColor(_PUMP_DISPLAY_OVERFLOW_COLOR)
        )

        painter.restore()
