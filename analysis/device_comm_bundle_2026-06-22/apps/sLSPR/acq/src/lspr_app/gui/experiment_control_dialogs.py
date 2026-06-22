from __future__ import annotations

import csv
from pathlib import Path

from PyQt6.QtCore import Qt, QSize, QEvent, QModelIndex
from PyQt6.QtGui import QColor, QIcon, QCursor, QGuiApplication, QPalette
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QDialog,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QComboBox,
    QDoubleSpinBox,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QStyledItemDelegate,
    QSizePolicy,
)

from tablerqicon import TablerQIcon

from lspr_app.domain.pump_plan import ACTIVE_PUMP_CHANNELS, PumpChannelStep, PumpPlanStep
from lspr_app.gui.experiment_control_builders import (
    create_table_color_combo,
    create_table_comment_edit,
    create_table_duration_spin,
    create_table_flow_spin,
    create_table_switch_combo,
    create_table_valve_button,
    direction_glyph,
)
from lspr_app.gui.experiment_control_plan_view import (
    build_experiment_control_pause_model,
    configure_experiment_control_plan_preview,
    match_experiment_control_plan_preview_geometry,
    read_experiment_control_pause_step,
    restore_experiment_control_pause_dialog_state,
    save_experiment_control_pause_dialog_state,
)
from lspr_app.gui.flow_plan_model import ExperimentPlanTableModel
from lspr_app.gui.icon_helpers import flow_tabler_icon


class PaletteTableWidget(QTableWidget):
    def keyPressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.key() == Qt.Key.Key_PageUp:
            mover = getattr(self, "_move_selected_row", None)
            if callable(mover):
                mover(-1)
                event.accept()
                return
        if event.key() == Qt.Key.Key_PageDown:
            mover = getattr(self, "_move_selected_row", None)
            if callable(mover):
                mover(1)
                event.accept()
                return
        super().keyPressEvent(event)


class PaletteNameDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):  # type: ignore[override]
        editor = QLineEdit(parent)
        editor.setFrame(False)
        editor.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        background = None
        background_brush = getattr(option, "backgroundBrush", None)
        if background_brush is not None:
            try:
                color = background_brush.color()
            except Exception:
                color = QColor()
            if color.isValid():
                background = color.name()
        if not background:
            brush = index.data(Qt.ItemDataRole.BackgroundRole)
            if hasattr(brush, "color"):
                try:
                    color = brush.color()
                except Exception:
                    color = QColor()
                if color.isValid():
                    background = color.name()
        if not background:
            background = parent.palette().base().color().name()
        text_color = "#0f1720" if QColor(background).lightness() > 150 else "#ffffff"
        editor.setStyleSheet(
            "QLineEdit {"
            f" background: {background};"
            f" color: {text_color};"
            " border: none;"
            " padding: 0px 2px;"
            " margin: 0px;"
            " }"
        )
        editor.setAutoFillBackground(True)
        return editor

    def setEditorData(self, editor, index):  # type: ignore[override]
        if isinstance(editor, QLineEdit):
            editor.setText(index.data(Qt.ItemDataRole.EditRole) or index.data(Qt.ItemDataRole.DisplayRole) or "")
            editor.selectAll()
            return
        super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):  # type: ignore[override]
        if isinstance(editor, QLineEdit):
            model.setData(index, editor.text(), Qt.ItemDataRole.EditRole)
            return
        super().setModelData(editor, model, index)

    def updateEditorGeometry(self, editor, option, index):  # type: ignore[override]
        editor.setGeometry(option.rect.adjusted(1, 0, -1, 0))


class SwitchSolutionTableWidget(QTableWidget):
    def keyPressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            row = self.currentRow()
            if row < 0:
                row = 0
            if event.key() == Qt.Key.Key_PageUp:
                step = -1
            elif event.key() == Qt.Key.Key_PageDown:
                step = 1
            else:
                step = -1 if event.key() == Qt.Key.Key_Backtab else 1
            next_row = (row + step) % max(self.rowCount(), 1)
            self.setCurrentCell(next_row, 1)
            widget = self.cellWidget(next_row, 1)
            if isinstance(widget, QLineEdit):
                self.scrollToItem(self.item(next_row, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
                widget.setFocus()
                widget.selectAll()
            event.accept()
            return
        super().keyPressEvent(event)


class SwitchSolutionEdit(QLineEdit):
    def __init__(self, on_tab, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_tab = on_tab
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def focusInEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        super().focusInEvent(event)
        self.selectAll()

    def keyPressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            if callable(self._on_tab):
                if event.key() == Qt.Key.Key_PageUp:
                    step = -1
                elif event.key() == Qt.Key.Key_PageDown:
                    step = 1
                else:
                    step = -1 if event.key() == Qt.Key.Key_Backtab else 1
                self._on_tab(step)
                event.accept()
                return
        super().keyPressEvent(event)


class ValveLabelEdit(QLineEdit):
    def __init__(self, on_tab, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_tab = on_tab

    def focusInEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        super().focusInEvent(event)
        self.selectAll()

    def keyPressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            if callable(self._on_tab):
                self._on_tab(-1 if event.key() == Qt.Key.Key_Backtab else 1)
                event.accept()
                return
        super().keyPressEvent(event)


class ValveLabelTableWidget(QTableWidget):
    def keyPressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            row = self.currentRow()
            if row < 0:
                row = 0
            step = -1 if event.key() == Qt.Key.Key_Backtab else 1
            next_row = (row + step) % max(self.rowCount(), 1)
            self.setCurrentCell(next_row, 1)
            widget = self.cellWidget(next_row, 1)
            if isinstance(widget, QLineEdit):
                widget.setFocus()
                widget.selectAll()
            event.accept()
            return
        super().keyPressEvent(event)


class PauseStateTableView(QTableView):
    def __init__(self, plan_window, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plan_window = plan_window

    def _cycle_pause_cell_by_wheel(self, index: QModelIndex, wheel_delta: int) -> bool:
        if not index.isValid() or wheel_delta == 0:
            return False
        current_row = self.currentIndex().row()
        if current_row >= 0 and index.row() != current_row:
            return False
        model = self.model()
        if model is None:
            return False
        cycle_delta = 1 if wheel_delta > 0 else -1
        if index.column() == 1:
            step = 10.0
            unit = getattr(self._plan_window, "_time_unit_mode", "s")
            if unit == "min":
                step = 0.1
            elif unit == "h":
                step = 0.1
            raw_value = index.data(Qt.ItemDataRole.EditRole)
            try:
                value = float(raw_value) if raw_value is not None else 0.0
            except (TypeError, ValueError):
                value = 0.0
            return bool(model.setData(index, max(value + (cycle_delta * step), 0.0), Qt.ItemDataRole.EditRole))
        flow_start = 4
        channel_count = ACTIVE_PUMP_CHANNELS
        if flow_start <= index.column() < flow_start + channel_count * 3:
            offset = index.column() - flow_start
            if offset % 3 == 0:
                raw_value = index.data(Qt.ItemDataRole.EditRole)
                try:
                    value = int(float(raw_value)) if raw_value is not None else 0
                except (TypeError, ValueError):
                    value = 0
                return bool(model.setData(index, max(value + cycle_delta, 0), Qt.ItemDataRole.EditRole))
        if index.column() == self._plan_window._switch_column() and hasattr(model, "cycle_switch"):
            handled = bool(model.cycle_switch(index.row(), cycle_delta))
        elif index.column() == self._plan_window._color_column() and hasattr(model, "cycle_color"):
            handled = bool(model.cycle_color(index.row(), cycle_delta))
        else:
            handled = False
        if handled:
            self.viewport().update()
        return handled

    def viewportEvent(self, event):  # pragma: no cover - GUI runtime path
        if event.type() == QEvent.Type.Wheel:
            index = self.indexAt(event.position().toPoint())
            if self._cycle_pause_cell_by_wheel(index, event.angleDelta().y()):
                event.accept()
                return True
        return super().viewportEvent(event)


class ExperimentControlDialogs:
    def __init__(
        self,
        parent: QWidget,
        theme_palette: dict[str, str],
        contrast_text_color,
        tint_icon,
    ) -> None:
        self._parent = parent
        self._theme_palette = theme_palette
        self._contrast_text_color = contrast_text_color
        self._tint_icon = tint_icon

    def _dialog_style(self, extra: str) -> str:
        return extra % self._theme_palette

    def _color_button_style(self, color: str) -> tuple[str, str]:
        qcolor = QColor(color)
        if not qcolor.isValid():
            qcolor = QColor("#4E79A7")
        return qcolor.name().upper(), self._contrast_text_color(qcolor.name())

    def _make_close_button(self, parent: QWidget) -> QToolButton:
        button = QToolButton(parent)
        button.setObjectName("paletteDialogClose")
        button.setAutoRaise(True)
        button.setIcon(self._tint_icon(TablerQIcon().get_qicon("x"), QColor("#e6ebf1")))
        button.setIconSize(QSize(14, 14))
        button.setToolTip("Close")
        return button

    def _position_dialog_near_anchor(self, dialog: QDialog, anchor: QWidget | None, *, y_offset: int = 8) -> None:
        if anchor is None:
            return
        try:
            anchor_point = anchor.mapToGlobal(anchor.rect().bottomLeft())
        except Exception:
            anchor_point = QCursor.pos()
        dialog.adjustSize()
        screen = QGuiApplication.screenAt(anchor_point) or QGuiApplication.primaryScreen()
        if screen is None:
            dialog.move(anchor_point.x(), anchor_point.y() + y_offset)
            return
        available = screen.availableGeometry()
        x = anchor_point.x()
        y = anchor_point.y() + y_offset
        if x + dialog.width() > available.right():
            x = max(available.right() - dialog.width() - 8, available.left())
        if y + dialog.height() > available.bottom():
            top_anchor = anchor.mapToGlobal(anchor.rect().topLeft()).y()
            y = max(top_anchor - dialog.height() - y_offset, available.top())
        dialog.move(max(x, available.left()), max(y, available.top()))

    def _set_palette_table_row(self, table: QTableWidget, row: int, name: str, color: str) -> None:
        name_item = QTableWidgetItem(name)
        name_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, 0, name_item)

        color_button = QToolButton(table)
        color_button.setObjectName("paletteColorButton")
        color_button.setAutoRaise(True)
        color_button.setToolTip("Click to change the color.")
        color_button.setProperty("palette_row", row)
        color_button.setProperty("palette_color", color)
        color_button.clicked.connect(lambda _checked=False, btn=color_button, tbl=table: self._choose_palette_row_color(tbl, btn))
        self._style_palette_color_button(color_button, color)
        table.setCellWidget(row, 1, color_button)

    def _style_palette_color_button(self, button: QToolButton, color: str) -> None:
        qcolor = QColor(color)
        if not qcolor.isValid():
            qcolor = QColor("#4E79A7")
        text_color = self._contrast_text_color(qcolor.name())
        button.setText(qcolor.name().upper())
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setIcon(QIcon())
        button.setIconSize(QSize(0, 0))
        button.setStyleSheet(
            f"""
            QToolButton#paletteColorButton {{
                background: {qcolor.name().upper()};
                color: {text_color};
                border: none;
                border-radius: 4px;
                padding: 0px 6px;
                margin: 0px;
            }}
            QToolButton#paletteColorButton:hover {{
                border: 1px solid rgba(255, 255, 255, 0.22);
            }}
            """
        )

    def _style_valve_color_button(self, button: QToolButton, color: str) -> None:
        qcolor = QColor(color)
        if not qcolor.isValid():
            qcolor = QColor("#4E79A7")
        text_color = self._contrast_text_color(qcolor.name())
        button.setText(qcolor.name().upper())
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setIcon(QIcon())
        button.setIconSize(QSize(0, 0))
        button.setStyleSheet(
            f"""
            QToolButton#valveColorButton {{
                background: {qcolor.name().upper()};
                color: {text_color};
                border: none;
                border-radius: 4px;
                padding: 0px 6px;
                margin: 0px;
            }}
            QToolButton#valveColorButton:hover {{
                border: 1px solid rgba(255, 255, 255, 0.22);
            }}
            """
        )

    def _set_valve_table_row(self, table: QTableWidget, row: int, value: str, label: str, color: str) -> None:
        value_item = QTableWidgetItem(value)
        value_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
        table.setItem(row, 0, value_item)

        label_edit = ValveLabelEdit(lambda step, r=row: self._move_valve_focus(table, r, step), table)
        label_edit.setText(label)
        label_edit.setFrame(False)
        label_edit.setPlaceholderText("Label")
        label_edit.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        label_edit.setStyleSheet(
            "QLineEdit {"
            " background: transparent;"
            " border: none;"
            " padding: 0px 2px;"
            " margin: 0px;"
            " }"
        )
        table.setCellWidget(row, 1, label_edit)

        color_button = QToolButton(table)
        color_button.setObjectName("valveColorButton")
        color_button.setAutoRaise(True)
        color_button.setToolTip("Click to change the valve background color.")
        color_button.setProperty("valve_row", row)
        color_button.setProperty("valve_color", color)
        color_button.clicked.connect(lambda _checked=False, btn=color_button, tbl=table: self._choose_valve_row_color(tbl, btn))
        self._style_valve_color_button(color_button, color)
        table.setCellWidget(row, 2, color_button)

    def _choose_valve_row_color(self, table: QTableWidget, button: QToolButton) -> None:
        row_value = button.property("valve_row")
        row = int(row_value) if row_value is not None else -1
        if row < 0 or row >= table.rowCount():
            return
        current = str(button.property("valve_color") or "#4E79A7")
        chosen = QColorDialog.getColor(QColor(current), self._parent, "Pick valve color")
        if not chosen.isValid():
            return
        color = chosen.name().upper()
        button.setProperty("valve_color", color)
        self._style_valve_color_button(button, color)

    def _move_valve_focus(self, table: QTableWidget, row: int, step: int) -> None:
        next_row = (row + step) % max(table.rowCount(), 1)
        table.setCurrentCell(next_row, 1)
        widget = table.cellWidget(next_row, 1)
        if isinstance(widget, QLineEdit):
            widget.setFocus()
            widget.selectAll()

    def _populate_valve_label_table(self, table: QTableWidget, entries: list[tuple[str, str, str]]) -> None:
        table.setRowCount(0)
        for row, (value, label, color) in enumerate(entries):
            table.insertRow(row)
            self._set_valve_table_row(table, row, value, label, color)
        if table.rowCount() > 0:
            table.setCurrentCell(0, 1)

    def _read_valve_label_table(self, table: QTableWidget) -> list[tuple[str, str, str]]:
        entries: list[tuple[str, str, str]] = []
        for row in range(table.rowCount()):
            value_item = table.item(row, 0)
            label_widget = table.cellWidget(row, 1)
            color_widget = table.cellWidget(row, 2)
            value = value_item.text().strip() if value_item is not None else ""
            label = label_widget.text().strip() if isinstance(label_widget, QLineEdit) else ""
            color = ""
            if isinstance(color_widget, QToolButton):
                color = str(color_widget.property("valve_color") or "").strip()
            if value:
                entries.append((value, label, color))
        return entries

    def _normalize_color_entry(self, name: object, color: object, fallback_index: int) -> tuple[str, str] | None:
        label = str(name).strip() if isinstance(name, str) else ""
        color_text = str(color).strip().upper() if isinstance(color, str) else ""
        if not label:
            label = f"Custom {fallback_index + 1}"
        qcolor = QColor(color_text)
        if not qcolor.isValid():
            return None
        return label, qcolor.name().upper()

    def _choose_palette_row_color(self, table: QTableWidget, button: QToolButton) -> None:
        row_value = button.property("palette_row")
        row = int(row_value) if row_value is not None else -1
        if row < 0 or row >= table.rowCount():
            return
        current = str(button.property("palette_color") or "#4E79A7")
        chosen = QColorDialog.getColor(QColor(current), self._parent, "Pick palette color")
        if not chosen.isValid():
            return
        color = chosen.name().upper()
        button.setProperty("palette_color", color)
        self._style_palette_color_button(button, color)

    def _populate_color_palette_table(self, table: QTableWidget, entries: list[tuple[str, str]]) -> None:
        table.setRowCount(0)
        for row, (name, color) in enumerate(entries):
            table.insertRow(row)
            self._set_palette_table_row(table, row, name, color)
        if table.rowCount() > 0:
            table.setCurrentCell(0, 0)

    def _read_color_palette_table(self, table: QTableWidget) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        for row in range(table.rowCount()):
            name_item = table.item(row, 0)
            color_widget = table.cellWidget(row, 1)
            name = name_item.text().strip() if name_item is not None else ""
            color_text = ""
            if isinstance(color_widget, QToolButton):
                color_text = str(color_widget.property("palette_color") or "").strip()
            entry = self._normalize_color_entry(name, color_text, row)
            if entry is not None:
                entries.append(entry)
        return entries

    def edit_valve_labels(
        self,
        current_labels: dict[str, str],
        current_colors: dict[str, str] | None = None,
        anchor: QWidget | None = None,
    ) -> tuple[dict[str, str], dict[str, str]] | None:
        dialog = QDialog(self._parent)
        dialog.setObjectName("valveDialog")
        dialog.setWindowTitle("Valve labels")
        dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        dialog.resize(352, 154)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        dialog.setStyleSheet(
            """
            QDialog {
                background: %(bg)s;
                color: %(fg)s;
            }
            QDialog#valveDialog {
                border: 1px solid #58a06a;
                border-radius: 10px;
            }
            QToolTip {
                background-color: %(bg)s;
                color: %(fg)s;
                border: 1px solid %(border)s;
                padding: 4px 6px;
            }
            QLabel#valveDialogTitle {
                color: %(fg)s;
                font-size: 10px;
                font-weight: 700;
            }
            QTableWidget#valveTable {
                background: %(bg)s;
                color: %(fg)s;
                border: none;
                gridline-color: %(border)s;
                alternate-background-color: %(button)s;
                selection-background-color: %(selection)s;
                selection-color: %(fg)s;
                font-size: 10px;
            }
            QTableWidget#valveTable::viewport {
                background: %(bg)s;
                border: none;
            }
            QTableWidget#valveTable::item {
                border: none;
                padding: 0px 2px;
            }
            QTableWidget#valveTable::item:selected {
                background: %(selection)s;
            }
            QToolButton#valveColorButton {
                background: transparent;
                border: none;
                padding: 0px;
                min-height: 18px;
                min-width: 112px;
            }
            QToolButton#valveColorButton:hover {
                border: 1px solid %(border_hover)s;
            }
            QHeaderView::section {
                background: %(header)s;
                color: %(fg)s;
                border: none;
                border-bottom: 1px solid %(border)s;
                padding: 0px 1px;
                font-size: 8px;
            }
            QPushButton#valveDialogAction {
                background: %(button)s;
                color: %(fg)s;
                border: 1px solid %(border)s;
                border-radius: 7px;
                padding: 1px 7px;
            }
            QPushButton#valveDialogAction:hover {
                background: %(button_hover)s;
                border-color: %(border_hover)s;
            }
            """ % self._theme_palette
        )
        top_label = QLabel("Valve labels")
        top_label.setObjectName("valveDialogTitle")
        top_label.setToolTip("Define the display labels for Open and Close valve states.")
        layout.addWidget(top_label)

        table = ValveLabelTableWidget(2, 3, dialog)
        table.setObjectName("valveTable")
        table.setHorizontalHeaderLabels(["Value", "Label", "Color"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        table.horizontalHeader().resizeSection(2, 124)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(True)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.verticalHeader().setDefaultSectionSize(17)
        table.horizontalHeader().setMinimumHeight(16)
        table.horizontalHeader().setMaximumHeight(16)

        def _focus_label_cell(row: int, column: int, previous_row: int, previous_column: int) -> None:
            if row < 0:
                return
            if column != 1:
                table.blockSignals(True)
                table.setCurrentCell(row, 1)
                table.blockSignals(False)
            widget = table.cellWidget(row, 1)
            if isinstance(widget, QLineEdit):
                widget.setFocus()
                widget.selectAll()

        entries = [
            (
                "Open",
                current_labels.get("Open", "Open"),
                (current_colors or {}).get("Open", "#4E79A7"),
            ),
            (
                "Close",
                current_labels.get("Close", "Close"),
                (current_colors or {}).get("Close", "#B44A4A"),
            ),
        ]
        self._populate_valve_label_table(table, entries)
        table.currentCellChanged.connect(_focus_label_cell)
        table.setCurrentCell(0, 1)
        table.setFocus()
        table.setFixedHeight(table.horizontalHeader().height() + table.verticalHeader().defaultSectionSize() * 2 + 8)
        layout.addWidget(table)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addStretch(1)
        apply_button = QPushButton("Apply")
        apply_button.setObjectName("valveDialogAction")
        button_row.addWidget(apply_button)
        layout.addLayout(button_row)

        result: dict[str, str] | None = None
        result_colors: dict[str, str] | None = None

        def _apply() -> None:
            valve_entries = self._read_valve_label_table(table)
            open_label = "Open"
            close_label = "Close"
            open_color = "#4E79A7"
            close_color = "#B44A4A"
            for value, label, color in valve_entries:
                normalized = value.strip().lower()
                if normalized == "open":
                    open_label = label or "Open"
                    if color:
                        open_color = color
                elif normalized == "close":
                    close_label = label or "Close"
                    if color:
                        close_color = color
            nonlocal result
            nonlocal result_colors
            result = {"Open": open_label, "Close": close_label}
            result_colors = {"Open": open_color, "Close": close_color}
            dialog.accept()

        apply_button.clicked.connect(_apply)
        self._position_dialog_near_anchor(dialog, anchor)
        dialog.adjustSize()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        if result is None or result_colors is None:
            return None
        return result, result_colors

    def edit_switch_solution_labels(self, current_labels: list[str], anchor: QWidget | None = None) -> list[str] | None:
        dialog = QDialog(self._parent)
        dialog.setObjectName("switchDialog")
        dialog.setWindowTitle("Switch solutions")
        dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        dialog.resize(340, 440)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        dialog.setStyleSheet(
            """
            QDialog {
                background: %(bg)s;
                color: %(fg)s;
            }
            QDialog#switchDialog {
                border: 1px solid #5b87c9;
                border-radius: 10px;
            }
            QToolTip {
                background-color: %(bg)s;
                color: %(fg)s;
                border: 1px solid %(border)s;
                padding: 4px 6px;
            }
            QLabel#switchDialogTitle {
                color: %(fg)s;
                font-size: 10px;
                font-weight: 700;
            }
            QTableWidget#switchTable {
                background: %(bg)s;
                color: %(fg)s;
                border: none;
                gridline-color: %(border)s;
                alternate-background-color: %(button)s;
                selection-background-color: %(selection)s;
                selection-color: %(fg)s;
                font-size: 10px;
            }
            QTableWidget#switchTable::viewport {
                background: %(bg)s;
                border: none;
            }
            QTableWidget#switchTable::item {
                border: none;
                padding: 0px 4px;
            }
            QTableWidget#switchTable::item:selected {
                background: %(selection)s;
            }
            QHeaderView::section {
                background: %(header)s;
                color: %(fg)s;
                border: none;
                border-bottom: 1px solid %(border)s;
                padding: 0px 2px;
                font-size: 9px;
            }
            QPushButton#switchDialogAction {
                background: %(button)s;
                color: %(fg)s;
                border: 1px solid %(border)s;
                border-radius: 7px;
                padding: 2px 8px;
            }
            QPushButton#switchDialogAction:hover {
                background: %(button_hover)s;
                border-color: %(border_hover)s;
            }
            """ % self._theme_palette
        )
        title = QLabel("Switch solutions")
        title.setObjectName("switchDialogTitle")
        title.setToolTip("Edit the labels shown for each multiport switch position.")
        layout.addWidget(title)

        table = SwitchSolutionTableWidget(12, 2, dialog)
        table.setObjectName("switchTable")
        table.setHorizontalHeaderLabels(["Port", "Solution"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(True)
        table.verticalHeader().setDefaultSectionSize(18)
        table.horizontalHeader().setMinimumHeight(18)
        table.horizontalHeader().setMaximumHeight(18)
        table.setTabKeyNavigation(False)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        def _move_focus(row: int, step: int) -> None:
            next_row = (row + step) % max(table.rowCount(), 1)
            table.blockSignals(True)
            table.setCurrentCell(next_row, 1)
            table.blockSignals(False)
            widget = table.cellWidget(next_row, 1)
            if isinstance(widget, QLineEdit):
                widget.setFocus()
                widget.selectAll()

        def _focus_solution_cell(row: int, column: int, previous_row: int, previous_column: int) -> None:
            if row < 0:
                return
            if column != 1:
                table.blockSignals(True)
                table.setCurrentCell(row, 1)
                table.blockSignals(False)
            widget = table.cellWidget(row, 1)
            if isinstance(widget, QLineEdit):
                widget.setFocus()
                widget.selectAll()

        for position in range(1, 13):
            port_item = QTableWidgetItem(str(position))
            port_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(position - 1, 0, port_item)
            label_edit = SwitchSolutionEdit(lambda step, r=position - 1: _move_focus(r, step), table)
            label_edit.setText(current_labels[position - 1] if position - 1 < len(current_labels) else "empty")
            label_edit.setFrame(False)
            label_edit.setPlaceholderText("Solution")
            label_edit.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            label_edit.setStyleSheet(
                "QLineEdit {"
                " background: transparent;"
                " border: none;"
                " padding: 0px 2px;"
                " margin: 0px;"
                " }"
            )
            table.setCellWidget(position - 1, 1, label_edit)
        table.currentCellChanged.connect(_focus_solution_cell)
        table.setCurrentCell(0, 1)
        table.setFixedHeight(table.horizontalHeader().height() + table.verticalHeader().defaultSectionSize() * 12 + 4)
        layout.addWidget(table)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(6)
        wait_button = QToolButton()
        wait_button.setObjectName("switchDialogAction")
        wait_button.setAutoRaise(True)
        wait_button.setCheckable(True)
        wait_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        wait_button.setFixedSize(24, 24)
        wait_button.setIconSize(QSize(16, 16))
        wait_button.setCursor(Qt.CursorShape.PointingHandCursor)
        wait_button.setStyleSheet(
            "QToolButton#switchDialogAction {"
            " background: transparent;"
            " border: none;"
            " padding: 0px;"
            " margin: 0px;"
            "}"
            "QToolButton#switchDialogAction:hover {"
            " background: rgba(127, 127, 127, 0.10);"
            " border: none;"
            "}"
            "QToolButton#switchDialogAction:checked {"
            " background: rgba(71, 168, 97, 0.12);"
            " border: none;"
            "}"
        )
        wait_button.setChecked(bool(getattr(self._parent, "_wait_for_mswitch_first", False)))

        def _sync_wait_button(checked: bool) -> None:
            wait_button.setIcon(
                self._tint_icon(
                    flow_tabler_icon("clock_pause"),
                    QColor("#47a861" if checked else "#8a93a0"),
                )
            )
            wait_button.setToolTip(
                "M-Switch-first mode enabled. Pump and valve wait until the switch move finishes."
                if checked
                else "M-Switch-first mode disabled. Pump and valve can apply without waiting."
            )
            try:
                setattr(self._parent, "_wait_for_mswitch_first", bool(checked))
            except Exception:
                pass

        wait_button.toggled.connect(_sync_wait_button)
        _sync_wait_button(wait_button.isChecked())
        button_row.addWidget(wait_button)
        button_row.addStretch(1)
        empty_button = QPushButton("All empty")
        empty_button.setObjectName("switchDialogAction")
        apply_button = QPushButton("Apply")
        apply_button.setObjectName("switchDialogAction")
        button_row.addWidget(empty_button)
        button_row.addWidget(apply_button)
        layout.addLayout(button_row)

        def _fill_empty() -> None:
            for position in range(12):
                widget = table.cellWidget(position, 1)
                if isinstance(widget, QLineEdit):
                    widget.setText("empty")
            table.setCurrentCell(0, 1)
            widget = table.cellWidget(0, 1)
            if isinstance(widget, QLineEdit):
                widget.setFocus()
                widget.selectAll()

        empty_button.clicked.connect(_fill_empty)
        apply_button.clicked.connect(dialog.accept)
        self._position_dialog_near_anchor(dialog, anchor)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        updated_labels: list[str] = []
        for position in range(1, 13):
            widget = table.cellWidget(position - 1, 1)
            if isinstance(widget, QLineEdit):
                updated_labels.append((widget.text().strip() or "empty"))
            else:
                updated_labels.append("empty")
        return updated_labels

    def edit_color_palette_entries(self, entries: list[tuple[str, str]], anchor: QWidget | None = None) -> list[tuple[str, str]] | None:
        dialog = QDialog(self._parent)
        dialog.setObjectName("paletteDialog")
        dialog.setWindowTitle("Color palette")
        dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        dialog.setStyleSheet(
            """
            QDialog {
                background: %(bg)s;
                color: %(fg)s;
            }
            QDialog#paletteDialog {
                border: 2px solid transparent;
                border-image: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #8f64ff,
                    stop: 0.17 #4f8dff,
                    stop: 0.34 #35c9ff,
                    stop: 0.51 #42d17a,
                    stop: 0.68 #ffd34d,
                    stop: 0.84 #ff9b42,
                    stop: 1 #ff5f73
                ) 1;
                border-radius: 10px;
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
            QWidget#paletteDialogBar {
                background: %(bg)s;
            }
            QLabel#paletteDialogTitle {
                color: %(fg)s;
                font-size: 10px;
                font-weight: 700;
            }
            QTableWidget#paletteTable {
                background: %(bg)s;
                color: %(fg)s;
                border: none;
                gridline-color: %(border)s;
                alternate-background-color: %(button)s;
                selection-background-color: %(selection)s;
                selection-color: %(fg)s;
                font-size: 11px;
            }
            QTableWidget#paletteTable::viewport {
                background: %(bg)s;
                border: none;
            }
            QTableWidget#paletteTable::item {
                border: none;
                padding: 1px 4px;
            }
            QTableWidget#paletteTable::item:selected {
                background: %(selection)s;
            }
            QTableWidget#paletteTable QLineEdit {
                background: %(bg)s;
                border: none;
                padding: 0px 2px;
                margin: 0px;
            }
            QTableWidget#paletteTable QLineEdit:focus {
                background: %(bg)s;
                border: none;
                outline: none;
            }
            QToolButton#paletteColorButton {
                background: transparent;
                border: none;
                padding: 0px;
                min-height: 18px;
                min-width: 112px;
            }
            QToolButton#paletteColorButton:hover {
                border: 1px solid %(border_hover)s;
            }
            QToolButton#paletteRowActionButton {
                background: transparent;
                border: none;
                padding: 0px;
                min-width: 18px;
                min-height: 18px;
            }
            QToolButton#paletteRowActionButton:hover {
                background: rgba(127, 127, 127, 0.10);
            }
            QToolButton#paletteRowActionButton:pressed {
                background: rgba(127, 127, 127, 0.18);
            }
            QHeaderView::section {
                background: %(header)s;
                color: %(fg)s;
                border: none;
                border-bottom: 1px solid %(border)s;
                padding: 0px 2px;
                font-size: 9px;
            }
            QToolButton#paletteDialogClose {
                background: transparent;
                border: none;
                padding: 0px;
                min-width: 18px;
                min-height: 18px;
                color: %(fg)s;
            }
            QToolButton#paletteDialogClose:hover {
                background: rgba(127, 127, 127, 0.10);
            }
            QPushButton#paletteDialogAction {
                background: %(button)s;
                color: %(fg)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                padding: 2px 8px;
            }
            QPushButton#paletteDialogAction:hover {
                background: %(button_hover)s;
                border-color: %(border_hover)s;
            }
            """ % self._theme_palette
        )

        top_bar = QWidget(dialog)
        top_bar.setObjectName("paletteDialogBar")
        top_bar_layout = QHBoxLayout()
        top_bar_layout.setContentsMargins(0, 0, 0, 0)
        top_bar_layout.setSpacing(2)
        title_label = QLabel("Color palette")
        title_label.setObjectName("paletteDialogTitle")
        title_label.setToolTip("Edit the palette used by the color dropdown. Save to CSV to share, or load a CSV to overwrite the current palette.")
        close_button = self._make_close_button(dialog)
        close_button.clicked.connect(dialog.reject)
        top_bar_layout.addWidget(title_label)
        top_bar_layout.addStretch(1)
        top_bar_layout.addWidget(close_button)
        top_bar.setLayout(top_bar_layout)
        layout.addWidget(top_bar)

        table = PaletteTableWidget(0, 2, dialog)
        table.setObjectName("paletteTable")
        table.setHorizontalHeaderLabels(["Name", "Color"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        table.horizontalHeader().resizeSection(1, 124)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.SelectedClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        table.setItemDelegateForColumn(0, PaletteNameDelegate(table))
        table.setAlternatingRowColors(True)
        table.setShowGrid(True)
        table.verticalHeader().setDefaultSectionSize(18)
        table.horizontalHeader().setMinimumHeight(18)
        table.horizontalHeader().setMaximumHeight(18)
        table.setTabKeyNavigation(True)
        table.setFixedHeight(table.horizontalHeader().height() + table.verticalHeader().defaultSectionSize() * max(len(entries), 1) + 4)
        self._populate_color_palette_table(table, entries)
        layout.addWidget(table)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(4)
        add_button = QToolButton()
        add_button.setObjectName("paletteRowActionButton")
        add_button.setAutoRaise(True)
        add_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        add_button.setIcon(self._tint_icon(TablerQIcon().get_qicon("plus"), QColor("#47a861")))
        add_button.setIconSize(QSize(14, 14))
        add_button.setToolTip("Add a palette row")
        remove_button = QToolButton()
        remove_button.setObjectName("paletteRowActionButton")
        remove_button.setAutoRaise(True)
        remove_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        remove_button.setIcon(self._tint_icon(TablerQIcon.x, QColor("#b44a4a")))
        remove_button.setIconSize(QSize(14, 14))
        remove_button.setToolTip("Remove the selected palette row")
        move_up_button = QToolButton()
        move_up_button.setObjectName("paletteRowActionButton")
        move_up_button.setAutoRaise(True)
        move_up_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        move_up_button.setIcon(self._tint_icon(TablerQIcon().get_qicon("chevron_up"), QColor("#e6ebf1")))
        move_up_button.setIconSize(QSize(14, 14))
        move_up_button.setToolTip("Move selected row up (Page Up)")
        move_down_button = QToolButton()
        move_down_button.setObjectName("paletteRowActionButton")
        move_down_button.setAutoRaise(True)
        move_down_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        move_down_button.setIcon(self._tint_icon(TablerQIcon().get_qicon("chevron_down"), QColor("#e6ebf1")))
        move_down_button.setIconSize(QSize(14, 14))
        move_down_button.setToolTip("Move selected row down (Page Down)")
        load_button = QPushButton("Load CSV")
        save_button = QPushButton("Save CSV")
        apply_button = QPushButton("Apply")
        cancel_button = QPushButton("Cancel")
        for button in (load_button, save_button, apply_button, cancel_button):
            button.setObjectName("paletteDialogAction")
        button_row.addWidget(add_button)
        button_row.addWidget(remove_button)
        button_row.addWidget(move_up_button)
        button_row.addWidget(move_down_button)
        button_row.addStretch(1)
        button_row.addWidget(load_button)
        button_row.addWidget(save_button)
        button_row.addWidget(cancel_button)
        button_row.addWidget(apply_button)
        layout.addLayout(button_row)

        def _add_row() -> None:
            row = table.rowCount()
            table.insertRow(row)
            self._set_palette_table_row(table, row, f"Custom {row + 1}", "#4E79A7")
            table.setCurrentCell(row, 0)

        def _remove_row() -> None:
            row = table.currentRow()
            if row < 0:
                return
            table.removeRow(row)
            if table.rowCount() > 0:
                table.setCurrentCell(min(row, table.rowCount() - 1), 0)

        def _move_row(delta: int) -> None:
            row = table.currentRow()
            if row < 0:
                return
            next_row = row + delta
            if next_row < 0 or next_row >= table.rowCount():
                return
            items = self._read_color_palette_table(table)
            if row >= len(items) or next_row >= len(items):
                return
            items[row], items[next_row] = items[next_row], items[row]
            self._populate_color_palette_table(table, items)
            table.setCurrentCell(next_row, 0)

        def _load_csv() -> None:
            file_path, _filter = QFileDialog.getOpenFileName(
                self._parent,
                "Load color palette",
                str(Path.home()),
                "Palette files (*.csv *.tsv);;CSV files (*.csv);;TSV files (*.tsv);;All files (*)",
            )
            if not file_path:
                return
            try:
                loaded = self._read_color_palette_file(Path(file_path))
            except Exception as exc:
                QMessageBox.critical(self._parent, "Load color palette", f"Could not load palette:\n{exc}")
                return
            self._populate_color_palette_table(table, loaded)

        def _save_csv() -> None:
            file_path, _filter = QFileDialog.getSaveFileName(
                self._parent,
                "Save color palette",
                str(Path.home() / "color_palette.csv"),
                "Palette files (*.csv *.tsv);;CSV files (*.csv);;TSV files (*.tsv);;All files (*)",
            )
            if not file_path:
                return
            try:
                self._write_color_palette_file(Path(file_path), self._read_color_palette_table(table))
            except Exception as exc:
                QMessageBox.critical(self._parent, "Save color palette", f"Could not save palette:\n{exc}")

        result: list[tuple[str, str]] | None = None

        def _apply() -> None:
            nonlocal result
            items = self._read_color_palette_table(table)
            if not items:
                QMessageBox.warning(self._parent, "Color palette", "Palette is empty. Add at least one color entry.")
                return
            result = items
            dialog.accept()

        add_button.clicked.connect(_add_row)
        remove_button.clicked.connect(_remove_row)
        move_up_button.clicked.connect(lambda: _move_row(-1))
        move_down_button.clicked.connect(lambda: _move_row(1))
        load_button.clicked.connect(_load_csv)
        save_button.clicked.connect(_save_csv)
        apply_button.clicked.connect(_apply)
        cancel_button.clicked.connect(dialog.reject)
        table._move_selected_row = _move_row  # type: ignore[attr-defined]

        self._position_dialog_near_anchor(dialog, anchor)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return result

    def edit_pause_state(self, step: PumpPlanStep, anchor: QWidget | None = None) -> PumpPlanStep | None:
        dialog = QDialog(self._parent)
        dialog.setObjectName("pauseStateDialog")
        dialog.setWindowTitle("Pause state")
        dialog.setWindowFlags(Qt.WindowType.Dialog)
        dialog.setSizeGripEnabled(True)
        dialog.resize(1120, 240)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        dialog.setStyleSheet(
            """
            QDialog {
                background: %(bg)s;
                color: %(fg)s;
            }
            QDialog#pauseStateDialog {
                border: 1px solid %(border)s;
                border-radius: 10px;
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
            QWidget#pauseStateDialogBar {
                background: %(bg)s;
            }
            QLabel#pauseStateDialogTitle {
                color: %(fg)s;
                font-size: 10px;
                font-weight: 700;
            }
            QTableView#pauseStateTable {
                background: %(bg)s;
                color: %(fg)s;
                border: none;
                gridline-color: %(border)s;
                alternate-background-color: %(button)s;
                selection-background-color: %(selection)s;
                selection-color: %(fg)s;
                font-size: 11px;
            }
            QTableView#pauseStateTable::viewport {
                background: %(bg)s;
                border: none;
            }
            QTableView#pauseStateTable::item {
                border: none;
                padding: 1px 4px;
            }
            QTableView#pauseStateTable::item:selected {
                background: %(selection)s;
            }
            QTableView#pauseStateTable QLineEdit {
                background: %(bg)s;
                border: none;
                padding: 0px 2px;
                margin: 0px;
            }
            QTableView#pauseStateTable QLineEdit:focus {
                background: %(bg)s;
                border: none;
                outline: none;
            }
            QPushButton#pauseStateDialogAction {
                background: %(button)s;
                color: %(fg)s;
                border: 1px solid %(border)s;
                border-radius: 8px;
                padding: 2px 8px;
            }
            QPushButton#pauseStateDialogAction:hover {
                background: %(button_hover)s;
                border-color: %(border_hover)s;
            }
            """ % self._theme_palette
        )

        top_bar = QWidget(dialog)
        top_bar.setObjectName("pauseStateDialogBar")
        top_bar_layout = QHBoxLayout()
        top_bar_layout.setContentsMargins(0, 0, 0, 0)
        top_bar_layout.setSpacing(2)
        title_label = QLabel("Pause state")
        title_label.setObjectName("pauseStateDialogTitle")
        title_label.setToolTip("Edit the state applied when the plan enters hold/pause mode.")
        top_bar_layout.addWidget(title_label)
        top_bar_layout.addStretch(1)
        top_bar.setLayout(top_bar_layout)
        layout.addWidget(top_bar)

        table = PauseStateTableView(self._parent, dialog)
        table.setObjectName("pauseStateTable")
        pause_model = build_experiment_control_pause_model(self._parent, step)
        configure_experiment_control_plan_preview(self._parent, table, pause_model)
        table.setModel(pause_model)
        table.setCurrentIndex(pause_model.index(0, 0))
        column_count = pause_model.columnCount()
        for column in range(column_count):
            table.setColumnHidden(column, bool(self._parent.plan_table.isColumnHidden(column)))
            width = int(self._parent.plan_table.columnWidth(column))
            if width > 0:
                table.setColumnWidth(column, width)
        header_height = max(table.horizontalHeader().sizeHint().height(), 24) + 4
        table.horizontalHeader().setMinimumHeight(header_height)
        table.horizontalHeader().setMaximumHeight(header_height)
        row_height = 20
        table.setRowHeight(0, row_height)
        table.setMinimumHeight(header_height + row_height + 8)
        layout.addWidget(table, 1)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(4)
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        apply_button = QPushButton("Apply")
        cancel_button.setObjectName("pauseStateDialogAction")
        apply_button.setObjectName("pauseStateDialogAction")
        button_row.addWidget(cancel_button)
        button_row.addWidget(apply_button)
        layout.addLayout(button_row, 0)

        result: PumpPlanStep | None = None

        def _apply() -> None:
            nonlocal result
            result = read_experiment_control_pause_step(self._parent, pause_model)
            dialog.accept()

        apply_button.clicked.connect(_apply)
        cancel_button.clicked.connect(dialog.reject)

        restored_geometry = restore_experiment_control_pause_dialog_state(
            dialog,
            table,
            getattr(self._parent, "_pause_state_dialog_state", {}),
        )
        if not restored_geometry:
            match_experiment_control_plan_preview_geometry(dialog, table, self._parent.plan_table)
            self._position_dialog_near_anchor(dialog, anchor)

        def _save_dialog_state(_result: int) -> None:
            self._parent._pause_state_dialog_state = save_experiment_control_pause_dialog_state(dialog, table)
            try:
                self._parent.save_ui_state()
            except Exception:
                pass

        dialog.finished.connect(_save_dialog_state)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return result

    def _write_color_palette_file(self, path: Path, entries: list[tuple[str, str]]) -> None:
        delimiter = "\t" if path.suffix.lower() in {".tsv", ".tab"} else ","
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, delimiter=delimiter)
            writer.writerow(["name", "color"])
            for name, color in entries:
                writer.writerow([name, color])

    def _read_color_palette_file(self, path: Path) -> list[tuple[str, str]]:
        delimiter = "\t" if path.suffix.lower() in {".tsv", ".tab"} else ","
        entries: list[tuple[str, str]] = []
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            rows = list(reader)
        if not rows:
            return entries
        start_index = 1 if rows[0] and [cell.strip().lower() for cell in rows[0][:2]] == ["name", "color"] else 0
        for index, row in enumerate(rows[start_index:]):
            if not row:
                continue
            name = row[0] if len(row) > 0 else ""
            color = row[1] if len(row) > 1 else ""
            entry = self._normalize_color_entry(name, color, index)
            if entry is not None:
                entries.append(entry)
        return entries


FlowControlDialogs = ExperimentControlDialogs
