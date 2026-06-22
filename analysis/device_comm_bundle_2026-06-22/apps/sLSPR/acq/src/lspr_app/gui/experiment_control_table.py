from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics, QColor
from PyQt6.QtWidgets import QHeaderView

from lspr_app.domain.pump_plan import ACTIVE_PUMP_CHANNELS
from lspr_app.gui.experiment_control_plan_view import (
    configure_experiment_control_plan_preview as _configure_experiment_control_plan_preview,
    configure_experiment_control_plan_view,
)
from lspr_app.gui.flow_plan_model import ExperimentPlanTableModel
from lspr_app.gui.icon_helpers import flow_tabler_icon, tint_tabler_icon


def _experiment_control_visible_columns(table) -> list[int]:
    return [column for column in range(table.columnCount()) if not table.isColumnHidden(column)]


def _experiment_control_content_width(table, column: int, *, padding: int = 18) -> int:
    model = table.model()
    header_text = ""
    if model is not None:
        header_text = str(model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or "")
    metrics = QFontMetrics(table.font())
    width = metrics.horizontalAdvance(header_text)
    width = max(width, int(table.sizeHintForColumn(column)))
    row_count = model.rowCount() if model is not None else 0
    for row in range(row_count):
        index = model.index(row, column)
        if not index.isValid():
            continue
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or index.data(Qt.ItemDataRole.EditRole) or "")
        if text:
            width = max(width, metrics.horizontalAdvance(text))
    return max(int(width) + padding, int(table.horizontalHeader().minimumSectionSize()))


def _experiment_control_shrink_widths(
    table,
    columns: list[int],
    target_total: int,
    minimum_width: int,
) -> dict[int, int]:
    if not columns:
        return {}
    current_total = sum(int(table.columnWidth(column)) for column in columns)
    if current_total <= target_total:
        return {column: int(table.columnWidth(column)) for column in columns}

    scale = target_total / max(current_total, 1)
    widths = {column: max(minimum_width, int(table.columnWidth(column) * scale)) for column in columns}
    overflow = sum(widths.values()) - target_total
    while overflow > 0:
        changed = False
        for column in sorted(columns, key=lambda item: widths[item], reverse=True):
            if widths[column] <= minimum_width:
                continue
            widths[column] -= 1
            overflow -= 1
            changed = True
            if overflow <= 0:
                break
        if not changed:
            break
    return widths


def configure_experiment_control_table_columns(window) -> None:
    header = window.plan_table.horizontalHeader()
    header.setStretchLastSection(False)
    header.setSectionsMovable(False)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setMinimumHeight(20)
    header.setMinimumSectionSize(20)
    window.plan_table.setColumnHidden(2, True)
    window.plan_table.setColumnHidden(3, True)
    for channel_index in range(ACTIVE_PUMP_CHANNELS):
        window.plan_table.setColumnHidden(window._direction_column(channel_index), not window._show_plan_details)
        window.plan_table.setColumnHidden(window._tube_column(channel_index), not window._show_plan_details)
    for column in range(len(window.PLAN_COLUMNS)):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
    header.setSectionResizeMode(window._description_column(), QHeaderView.ResizeMode.Interactive)
    window.plan_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    fit_plan_table_columns_to_viewport(window)
    update_plan_table_height(window)


def fit_plan_table_columns_to_viewport(window) -> None:
    header = window.plan_table.horizontalHeader()
    table = window.plan_table
    step_column = 0
    description_column = window._description_column()
    visible_columns = _experiment_control_visible_columns(table)
    if not visible_columns:
        return

    viewport_width = max(table.viewport().width(), 1)
    min_width = int(header.minimumSectionSize())
    preferred_description_width = 140
    step_width = _experiment_control_content_width(table, step_column)
    table.setColumnWidth(step_column, step_width)
    other_columns = [column for column in visible_columns if column not in {description_column, step_column}]
    available_for_other = max(viewport_width - preferred_description_width - step_width, min_width * len(other_columns))

    window._suppress_plan_table_layout_save = True
    header.blockSignals(True)
    try:
        for column, width in _experiment_control_shrink_widths(table, other_columns, available_for_other, min_width).items():
            table.setColumnWidth(column, width)

        non_description_total = sum(int(table.columnWidth(column)) for column in other_columns)
        if description_column in visible_columns:
            description_width = max(min_width, viewport_width - step_width - non_description_total)
            table.setColumnWidth(description_column, description_width)

        visible_total = sum(int(table.columnWidth(column)) for column in visible_columns)
        extra = viewport_width - visible_total
        if extra > 0:
            target_column = description_column if description_column in visible_columns else visible_columns[-1]
            table.setColumnWidth(target_column, table.columnWidth(target_column) + extra)
    finally:
        header.blockSignals(False)
        window._suppress_plan_table_layout_save = False
    table.horizontalScrollBar().setValue(0)
    table.viewport().update()


def update_plan_table_height(window) -> None:
    if not hasattr(window, "plan_table") or not hasattr(window, "_flow_editor_splitter"):
        return
    if window._flow_editor_splitter_initialized:
        return
    row_count = window.plan_table.rowCount()
    table_height = window.plan_table.horizontalHeader().height() + 4
    if row_count > 0:
        total_rows = sum(window.plan_table.rowHeight(row) for row in range(row_count))
        table_height = max(table_height, window.plan_table.horizontalHeader().height() + total_rows + 4)
    timeline_height = max(window.timeline_widget.minimumHeight(), window.timeline_widget.sizeHint().height(), 64)
    window._suppress_plan_table_layout_save = True
    try:
        window._flow_editor_splitter.setSizes([table_height, timeline_height])
    finally:
        window._suppress_plan_table_layout_save = False
    window._flow_editor_splitter_initialized = True
    window._flow_editor_splitter.setHandleWidth(4)


def configure_experiment_control_plan_table(window) -> ExperimentPlanTableModel:
    table = window.plan_table
    model = ExperimentPlanTableModel(list(window.PLAN_COLUMNS), window)
    window._plan_model = model
    table.setModel(model)
    configure_experiment_control_plan_view(window, table, model)
    return model


def configure_experiment_control_plan_preview(window, table, model: ExperimentPlanTableModel) -> None:
    _configure_experiment_control_plan_preview(window, table, model)


def sync_experiment_control_tube_columns(window) -> None:
    window._sync_experiment_control_table_derived_columns(window._read_experiment_control_steps())
    window.save_ui_state()


def update_plan_detail_toggle_icon(window) -> None:
    if not hasattr(window, "plan_detail_toggle"):
        return
    if window._show_plan_details:
        icon = tint_tabler_icon(flow_tabler_icon("icons"), QColor("#e86ea8"))
        tooltip = "Switch to compact mode and hide the per-channel direction and tube columns."
    else:
        icon = tint_tabler_icon(flow_tabler_icon("icons_off"), QColor("#8a98a8"))
        tooltip = "Show the per-channel direction and tube columns."
    window.plan_detail_toggle.setIcon(icon)
    window.plan_detail_toggle.setToolTip(tooltip)

