"""Configures a QTableView's look-and-feel/delegates for the real
ExperimentPlanTableModel, plus the pause-state single-row preview model.

Moved from sLSPR acq's `gui/experiment_control_plan_view.py` (Phase 2, Tier 3a
- full experiment-control panel consolidation, 2026-08-09). Genuinely portable
as-is: `configure_experiment_control_plan_view(window, table, model)` duck-types
on `window` via the same contract `experiment_plan_table_model.py`'s delegates
already establish (`plan_table`/column-index helpers/`_theme_palette()`/
`_switch_solution_labels`/etc.) - no sLSPR-acq-specific state.
"""

from __future__ import annotations

from copy import deepcopy

from PyQt6.QtCore import Qt, QByteArray
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QSizePolicy,
    QStyledItemDelegate,
    QStyle,
    QStyleFactory,
    QStyleOptionViewItem,
    QTableView,
)

from .pump_plan import ACTIVE_PUMP_CHANNELS, PumpChannelStep, PumpPlanStep
from .experiment_plan_table_model import (
    ExperimentPlanColorDelegate,
    ExperimentPlanCommentDelegate,
    ExperimentPlanDirectionDelegate,
    ExperimentPlanDurationDelegate,
    ExperimentPlanFlowDelegate,
    ExperimentPlanSwitchDelegate,
    ExperimentPlanTableModel,
    ExperimentPlanValveDelegate,
)


class _NoFocusItemDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        opt.state &= ~QStyle.StateFlag.State_Selected
        opt.state &= ~QStyle.StateFlag.State_MouseOver
        opt.showDecorationSelected = False
        super().paint(painter, opt, index)


def configure_experiment_control_plan_view(window, table, model: ExperimentPlanTableModel) -> None:
    """Apply the plan-table look-and-feel to a QTableView."""
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(20)
    table.horizontalHeader().setMinimumHeight(20)
    table.horizontalHeader().setMaximumHeight(20)
    table.setAlternatingRowColors(True)
    table.horizontalHeader().setStretchLastSection(False)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.setMouseTracking(True)
    table.viewport().setMouseTracking(True)
    table.setEditTriggers(
        QAbstractItemView.EditTrigger.DoubleClicked
        | QAbstractItemView.EditTrigger.SelectedClicked
        | QAbstractItemView.EditTrigger.EditKeyPressed
    )
    table.setItemDelegate(_NoFocusItemDelegate(table))
    table.setItemDelegateForColumn(1, ExperimentPlanDurationDelegate(window))
    for channel_index in range(ACTIVE_PUMP_CHANNELS):
        table.setItemDelegateForColumn(window._flow_rate_column(channel_index), ExperimentPlanFlowDelegate(window))
        table.setItemDelegateForColumn(window._direction_column(channel_index), ExperimentPlanDirectionDelegate(window))
        table.setItemDelegateForColumn(window._tube_column(channel_index), _NoFocusItemDelegate(table))
    table.setItemDelegateForColumn(window._valve_column(), ExperimentPlanValveDelegate(window))
    table.setItemDelegateForColumn(window._switch_column(), ExperimentPlanSwitchDelegate(window))
    table.setItemDelegateForColumn(window._color_column(), ExperimentPlanColorDelegate(window))
    table.setItemDelegateForColumn(window._description_column(), ExperimentPlanCommentDelegate(window))
    model.set_theme_palette(window._theme_palette())
    model.set_time_unit_mode(getattr(window, "_time_unit_mode", "s"))
    model.set_tube_mm_by_channel([spin.value() for spin in window.manual_tube_spins])
    model.set_switch_solution_labels(window._switch_solution_labels, getattr(window, "_switch_solution_mode", None))
    model.set_color_options(window._color_palette_entries)
    model.set_valve_state_labels(window._valve_state_labels)
    if hasattr(window, "_valve_state_colors"):
        model.set_valve_state_colors(window._valve_state_colors)
    plan_palette = table.palette()
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        plan_palette.setColor(group, QPalette.ColorRole.Highlight, QColor(0, 0, 0, 0))
        plan_palette.setColor(group, QPalette.ColorRole.HighlightedText, QColor(window._theme_palette()["fg"]))
    table.setPalette(plan_palette)
    table.viewport().setPalette(plan_palette)
    # The native "windows11" QStyle (Qt's default on Windows 11) draws its own
    # accent-colored "current item" indicator for QAbstractItemView.SelectItems
    # selection - a short vertical bar at the left edge of whichever cell is
    # current, e.g. in edit mode. It's issued by the style's own C++ item-view
    # drawing, not through anything QStyledItemDelegate.paint() controls, so
    # clearing QStyleOptionViewItem.state flags in the delegates above (as
    # _NoFocusItemDelegate/_BaseFlowDelegate already do) cannot suppress it.
    # Fusion is a pure Qt-drawn style with no such native decoration, and
    # everything about this table's appearance is already controlled by its
    # own QSS/palette/delegates, so switching just this widget to Fusion has
    # no other visible effect. The style instance must be kept referenced
    # (QWidget.setStyle() does not take ownership) or it would be garbage
    # collected out from under the table.
    fusion_style = QStyleFactory.create("Fusion")
    if fusion_style is not None:
        table.setStyle(fusion_style)
        window._plan_table_style_refs = getattr(window, "_plan_table_style_refs", [])
        window._plan_table_style_refs.append(fusion_style)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    if hasattr(window, "_schedule_visible_experiment_control_rows_load"):
        table.verticalScrollBar().valueChanged.connect(lambda _value: window._schedule_visible_experiment_control_rows_load())


def configure_experiment_control_plan_preview(window, table, model: ExperimentPlanTableModel) -> None:
    """Apply the shared plan-table configuration to a popup preview view."""
    configure_experiment_control_plan_view(window, table, model)


def build_experiment_control_headers(window) -> list[str]:
    """Build the user-facing plan-table header labels for the current mode."""
    headers = list(window.PLAN_COLUMNS)
    unit = getattr(window, "_time_unit_mode", "s")
    headers[0] = "Step"
    headers[1] = f"Duration [{unit}]"
    headers[2] = f"start_{unit}"
    headers[3] = f"end_{unit}"
    for channel_index in range(ACTIVE_PUMP_CHANNELS):
        headers[window._flow_rate_column(channel_index)] = f"CH{channel_index + 1}"
        headers[window._direction_column(channel_index)] = f"CH{channel_index + 1} Dir"
        headers[window._tube_column(channel_index)] = f"CH{channel_index + 1} Tube"
    headers[window._valve_column()] = "Valve"
    headers[window._switch_column()] = "Switch"
    headers[window._color_column()] = "Color"
    headers[window._description_column()] = "Comment"
    return headers


def match_experiment_control_plan_preview_geometry(
    dialog: QDialog,
    table: QTableView,
    reference_table: QTableView,
    *,
    minimum_width: int = 960,
    width_padding: int = 24,
) -> int:
    """Resize a plan-table popup so it mirrors the reference control-table width.

    The helper keeps the popup framing logic in one place so pause-state and
    future preview dialogs can reuse the same sizing behavior.
    """
    column_count = table.model().columnCount() if table.model() is not None else 0
    visible_width = sum(
        table.columnWidth(column)
        for column in range(column_count)
        if not table.isColumnHidden(column)
    )
    reference_width = int(reference_table.width()) if reference_table is not None else 0
    table_width = max(visible_width + width_padding, reference_width, minimum_width)
    table.setMinimumWidth(table_width)
    if table.layout() is not None:
        table.layout().activate()
    margins = dialog.layout().contentsMargins() if dialog.layout() is not None else dialog.contentsMargins()
    dialog.setMinimumWidth(table_width + margins.left() + margins.right())
    dialog.resize(max(table_width + width_padding, reference_width, minimum_width), dialog.height())
    return table_width


def build_experiment_control_pause_model(window, step: PumpPlanStep, *, app_name: str, app_version: str) -> ExperimentPlanTableModel:
    """Create a one-row model preloaded with the pause-state values."""
    model = ExperimentPlanTableModel(list(window.PLAN_COLUMNS), window, app_name=app_name, app_version=app_version)
    model.set_headers(build_experiment_control_headers(window))
    model.set_theme_palette(window._theme_palette())
    model.set_time_unit_mode(getattr(window, "_time_unit_mode", "s"))
    model.set_tube_mm_by_channel([spin.value() for spin in window.manual_tube_spins])
    model.set_switch_solution_labels(window._switch_solution_labels, getattr(window, "_switch_solution_mode", None))
    model.set_color_options(window._color_palette_entries)
    model.set_valve_state_labels(window._valve_state_labels)
    if hasattr(window, "_valve_state_colors"):
        model.set_valve_state_colors(window._valve_state_colors)
    model.set_single_step(step)
    return model


def read_experiment_control_pause_step(window, model: ExperimentPlanTableModel) -> PumpPlanStep:
    """Read the edited pause-state row back into a PumpPlanStep."""
    step_row = model.step_at(0)
    if step_row is None:
        return PumpPlanStep(
            step=0,
            duration_s=0.0,
            color=window._default_experiment_control_color(0),
            valve="Open",
            switch_position=1,
            description="Pause",
            channels=[PumpChannelStep() for _ in range(ACTIVE_PUMP_CHANNELS)],
        )
    result = deepcopy(step_row)
    result.step = 0
    if not result.description:
        result.description = "Pause"
    return result


def restore_experiment_control_pause_dialog_state(
    dialog: QDialog,
    table: QTableView,
    saved_state: dict[str, object] | None,
) -> bool:
    """Restore pause-dialog geometry and column widths from a saved state."""
    if not isinstance(saved_state, dict):
        return False
    restored_geometry = False
    geometry = saved_state.get("geometry")
    if isinstance(geometry, str) and geometry:
        try:
            dialog.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
            restored_geometry = True
        except Exception:
            pass
    column_count = table.model().columnCount() if table.model() is not None else 0
    column_widths = saved_state.get("column_widths")
    if isinstance(column_widths, list):
        for column, width in enumerate(column_widths[:column_count]):
            if isinstance(width, int) and width > 0:
                table.setColumnWidth(column, width)
    if not restored_geometry and isinstance(saved_state.get("width"), int) and isinstance(saved_state.get("height"), int):
        dialog.resize(max(int(saved_state["width"]), 640), max(int(saved_state["height"]), 240))
    table.viewport().update()
    return restored_geometry


def save_experiment_control_pause_dialog_state(
    dialog: QDialog,
    table: QTableView,
) -> dict[str, object]:
    """Serialize pause-dialog geometry and table widths for persistence."""
    column_count = table.model().columnCount() if table.model() is not None else 0
    return {
        "geometry": bytes(dialog.saveGeometry().toBase64()).decode("ascii"),
        "column_widths": [int(table.columnWidth(column)) for column in range(column_count)],
    }
