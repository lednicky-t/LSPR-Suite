from __future__ import annotations

import json

from PyQt6.QtCore import QEvent, QModelIndex, QObject, QTimer, Qt, QMimeData, QItemSelectionModel, QRect
from PyQt6.QtGui import QColor, QPainter, QPen, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QTableWidget,
    QWidget,
)

from lspr_app.domain.pump_plan import duplicate_plan_step


class _SelectionOverlay(QWidget):
    def __init__(self, parent: QWidget, table: QTableWidget) -> None:
        super().__init__(parent)
        self._table = table
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

    def _paint_rect(
        self,
        painter: QPainter,
        rect,
        *,
        color: QColor,
        dashed: bool = False,
        fill: bool = False,
        pen_width: float = 1.0,
    ) -> None:
        pen = QPen(color)
        pen.setWidthF(max(float(pen_width), 1.0))
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        if fill:
            brush = QColor(color)
            brush.setAlpha(32)
            painter.setBrush(brush)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

    def _row_indexes(self, row: int) -> list[QModelIndex]:
        if row < 0:
            return []
        model = self._table.model()
        if model is None or row >= model.rowCount():
            return []
        return [model.index(row, column) for column in range(model.columnCount()) if model.index(row, column).isValid()]

    def _draw_row(
        self,
        painter: QPainter,
        row: int,
        *,
        color: QColor,
        dashed: bool = False,
        fill: bool = False,
        pen_width: float = 1.0,
    ) -> None:
        indexes = self._row_indexes(row)
        if not indexes:
            return
        rects = []
        for index in indexes:
            rect = self._table.visualRect(index)
            if rect.isValid() and rect.width() > 0 and rect.height() > 0:
                rects.append(rect)
        if not rects:
            return
        merged = rects[0]
        for rect in rects[1:]:
            merged = merged.united(rect)
        merged = merged.adjusted(1, 1, -1, -1)
        if merged.width() <= 0 or merged.height() <= 0:
            return
        self._paint_rect(painter, merged, color=color, dashed=dashed, fill=fill, pen_width=pen_width)

    def _draw_indexes(
        self,
        painter: QPainter,
        indexes: list[QModelIndex],
        *,
        color: QColor,
        dashed: bool = False,
        fill: bool = False,
        pen_width: float = 1.0,
    ) -> None:
        rects = []
        for index in indexes:
            rect = self._table.visualRect(index)
            if rect.isValid() and rect.width() > 0 and rect.height() > 0:
                rects.append(rect)
        if not rects:
            return
        merged = rects[0]
        for rect in rects[1:]:
            merged = merged.united(rect)
        merged = merged.adjusted(1, 1, -1, -1)
        if merged.width() <= 0 or merged.height() <= 0:
            return
        self._paint_rect(painter, merged, color=color, dashed=dashed, fill=fill, pen_width=pen_width)

    def _runtime_row(self) -> int | None:
        active_row = self._table.property("experiment_control_runtime_row")
        if not isinstance(active_row, int):
            active_row = getattr(self._table.window(), "_plan_active_row", None)
        return active_row if isinstance(active_row, int) else None

    def _runtime_active(self) -> bool:
        return bool(
            self._runtime_row() is not None
            or getattr(self._table.window(), "_plan_running", False)
            or getattr(self._table.window(), "_plan_holding", False)
            or getattr(self._table.window(), "_plan_paused", False)
        )

    def _highlight_colors(self) -> tuple[QColor, QColor]:
        active_color = QColor("#d8b44a")
        active_color.setAlpha(220)
        editor_color = QColor("#43b05c")
        editor_color.setAlpha(240)
        return active_color, editor_color

    def paintEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            model = self._table.model()
            if model is None:
                return
            active_row = self._runtime_row()
            runtime_active = self._runtime_active()
            active_color, editor_color = self._highlight_colors()

            if bool(self._table.property("experiment_control_edit_mode")):
                selected = [index for index in self._table.selectedIndexes() if index.isValid()]
                copied_positions = getattr(self._table, "_experiment_control_copied_selection", [])
                copied: list[QModelIndex] = []
                if isinstance(copied_positions, list):
                    for row, column in copied_positions:
                        if isinstance(row, int) and isinstance(column, int):
                            if 0 <= row < model.rowCount() and 0 <= column < model.columnCount():
                                index = model.index(row, column)
                                if index.isValid():
                                    copied.append(index)
                if selected:
                    self._draw_indexes(painter, selected, color=editor_color, dashed=False, fill=False, pen_width=1.4)
                if copied:
                    self._draw_indexes(painter, copied, color=QColor("#d8b44a"), dashed=True, fill=False, pen_width=1.4)
                if active_row is not None:
                    self._draw_row(painter, active_row, color=active_color, dashed=False, fill=True, pen_width=2.0)
                return

            if runtime_active:
                if active_row is not None:
                    self._draw_row(painter, active_row, color=active_color, dashed=False, fill=True, pen_width=2.0)
                return

            row = self._table.currentRow()
            if row < 0 or row >= self._table.rowCount():
                return
            self._draw_row(painter, row, color=editor_color, dashed=False, fill=False)
        finally:
            painter.end()


class ExperimentControlEditingController(QObject):
    def __init__(self, window, table: QTableWidget, button) -> None:
        super().__init__(window)
        self._window = window
        self._table = table
        self._button = button
        self._edit_mode = False
        self._selection_dragging = False
        self._selection_anchor: tuple[int, int] | None = None
        self._overlay = _SelectionOverlay(table.viewport() if table.viewport() is not None else window, table)
        self._table._experiment_control_copied_selection = []
        self._table.setProperty("flow_navigation", True)
        self._table.viewport().setProperty("flow_navigation", True)
        self._table.selectionModel().selectionChanged.connect(lambda _sel, _desel: self._sync_overlay())
        self._table.installEventFilter(window)
        self._table.viewport().installEventFilter(window)
        QTimer.singleShot(0, self._sync_overlay)

    @property
    def edit_mode(self) -> bool:
        return self._edit_mode

    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = bool(enabled)
        self._table.setProperty("experiment_control_edit_mode", self._edit_mode)
        self._table.viewport().setProperty("experiment_control_edit_mode", self._edit_mode)
        if self._edit_mode:
            self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
            self._window._plan_table_layout_locked = True
            self._release_table_focus_if_runtime_active()
        else:
            self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self._window._plan_table_layout_locked = False
            self._table.clearSelection()
            self.clear_copied_selection()
            self._selection_anchor = None
        self._window._update_experiment_control_edit_mode_button()
        self._sync_overlay()

    def _release_table_focus_if_runtime_active(self) -> None:
        if not self._runtime_active():
            return
        self._table.clearFocus()
        viewport = self._table.viewport()
        if viewport is not None:
            viewport.clearFocus()

    def toggle_edit_mode(self, enabled: bool) -> None:
        self.set_edit_mode(enabled)
        self._window._set_status_message("Table edit mode enabled." if enabled else "Table edit mode disabled.")

    def _selected_indexes(self) -> list[QModelIndex]:
        indexes = [index for index in self._table.selectedIndexes() if index.isValid()]
        if indexes:
            return sorted(indexes, key=lambda item: (item.row(), item.column()))
        row = self._window._selected_table_row()
        if row is None:
            return []
        column = self._table.currentColumn()
        if column < 0 or column >= self._table.columnCount():
            column = 0
        model = self._table.model()
        if model is None:
            return []
        index = model.index(row, column)
        return [index] if index.isValid() else []

    def _selected_rows(self) -> list[int]:
        rows = sorted({index.row() for index in self._selected_indexes()})
        if rows:
            return rows
        row = self._window._selected_table_row()
        return [row] if row is not None else []

    def _set_selection(self, indexes: list[tuple[int, int]]) -> None:
        model = self._table.model()
        selection_model = self._table.selectionModel()
        if model is None or selection_model is None:
            return
        valid_indexes = [
            (row, column)
            for row, column in indexes
            if 0 <= row < model.rowCount() and 0 <= column < model.columnCount()
        ]
        selection_model.clearSelection()
        if not valid_indexes:
            return
        first_row, first_column = valid_indexes[0]
        first_index = model.index(first_row, first_column)
        if first_index.isValid():
            self._table.setCurrentIndex(first_index)
            selection_model.select(first_index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        for row, column in valid_indexes[1:]:
            index = model.index(row, column)
            if index.isValid():
                selection_model.select(index, QItemSelectionModel.SelectionFlag.Select)

    def _select_rows(self, rows: list[int]) -> None:
        model = self._table.model()
        if model is None:
            return
        indexes: list[tuple[int, int]] = []
        for row in rows:
            if row < 0 or row >= model.rowCount():
                continue
            for column in range(model.columnCount()):
                indexes.append((row, column))
        self._set_selection(indexes)

    def _select_range(self, start_row: int, start_column: int, end_row: int, end_column: int) -> None:
        model = self._table.model()
        if model is None:
            return
        row_start = min(start_row, end_row)
        row_end = max(start_row, end_row)
        column_start = min(start_column, end_column)
        column_end = max(start_column, end_column)
        indexes = [
            (row, column)
            for row in range(row_start, row_end + 1)
            for column in range(column_start, column_end + 1)
            if 0 <= row < model.rowCount() and 0 <= column < model.columnCount()
        ]
        self._set_selection(indexes)

    def _set_copied_selection(self, indexes: list[QModelIndex]) -> None:
        copied = [(index.row(), index.column()) for index in indexes if index.isValid()]
        self._table._experiment_control_copied_selection = copied
        self._sync_overlay()

    def clear_copied_selection(self) -> None:
        self._table._experiment_control_copied_selection = []
        self._sync_overlay()

    def _selection_payload(self) -> dict[str, object] | None:
        indexes = self._selected_indexes()
        if not indexes:
            return None
        selected_positions = {(index.row(), index.column()) for index in indexes}
        row_min = min(index.row() for index in indexes)
        row_max = max(index.row() for index in indexes)
        column_min = min(index.column() for index in indexes)
        column_max = max(index.column() for index in indexes)
        cells: list[dict[str, object]] = []
        text_rows: list[str] = []
        for row in range(row_min, row_max + 1):
            row_text: list[str] = []
            for column in range(column_min, column_max + 1):
                if (row, column) not in selected_positions:
                    row_text.append("")
                    continue
                kind = self._window._experiment_control_column_kind(column)
                if kind is None or kind == "step":
                    row_text.append("")
                    continue
                value = self._window._experiment_control_read_cell_value(row, column)
                text_value = self._window._experiment_control_value_to_text(kind, value)
                row_text.append(text_value)
                cells.append(
                    {
                        "row_offset": row - row_min,
                        "column_offset": column - column_min,
                        "column": column,
                        "kind": kind,
                        "value": value,
                        "text": text_value,
                    }
                )
            text_rows.append("\t".join(row_text))
        return {
            "row_count": row_max - row_min + 1,
            "column_count": column_max - column_min + 1,
            "row_min": row_min,
            "column_min": column_min,
            "cells": cells,
            "text": "\n".join(text_rows),
        }

    def copy_selection(self) -> None:
        payload = self._selection_payload()
        if payload is None:
            return
        self._set_copied_selection(self._selected_indexes())
        mime = QMimeData()
        mime.setText(str(payload.get("text", "")))
        mime.setData("application/x-lspr-experiment-control-selection", json.dumps(payload).encode("utf-8"))
        QApplication.clipboard().setMimeData(mime)
        self._window._set_status_message("Copied selection.")

    def paste_selection(self) -> None:
        mime = QApplication.clipboard().mimeData()
        if mime is None:
            return
        raw = bytes(mime.data("application/x-lspr-experiment-control-selection"))
        if not raw:
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return
        cells = payload.get("cells", [])
        if not isinstance(cells, list) or not cells:
            return
        target_indexes = self._selected_indexes()
        if target_indexes:
            target_row = min(index.row() for index in target_indexes)
            target_column = min(index.column() for index in target_indexes)
        else:
            current = self._table.currentIndex()
            target_row = current.row() if current.isValid() else 0
            target_column = current.column() if current.isValid() else 0
        steps = self._window._read_experiment_control_steps()
        if not steps:
            return
        rows_to_refresh: set[int] = set()
        skipped_incompatible = False
        single_value = len(cells) == 1
        if single_value and target_indexes:
            value = cells[0].get("value")
            source_kind = str(cells[0].get("kind") or "")
            for index in target_indexes:
                target_kind = self._window._experiment_control_column_kind(index.column())
                if target_kind is None or target_kind in {"step", "time"}:
                    skipped_incompatible = True
                    continue
                if target_kind != source_kind and not (source_kind == "time" and target_kind == "time"):
                    skipped_incompatible = True
                    continue
                if self._window._experiment_control_write_row_value(steps, index.row(), index.column(), value):
                    rows_to_refresh.add(index.row())
                else:
                    skipped_incompatible = True
        else:
            for cell in cells:
                if not isinstance(cell, dict):
                    continue
                row_offset = int(cell.get("row_offset", 0))
                col_offset = int(cell.get("column_offset", 0))
                value = cell.get("value")
                source_kind = str(cell.get("kind") or "")
                target_row_index = target_row + row_offset
                target_column_index = target_column + col_offset
                if not (0 <= target_row_index < len(steps)):
                    continue
                target_kind = self._window._experiment_control_column_kind(target_column_index)
                if target_kind is None or target_kind in {"step", "time"}:
                    skipped_incompatible = True
                    continue
                if target_kind != source_kind and not (source_kind == "time" and target_kind == "time"):
                    skipped_incompatible = True
                    continue
                if self._window._experiment_control_write_row_value(steps, target_row_index, target_column_index, value):
                    rows_to_refresh.add(target_row_index)
                else:
                    skipped_incompatible = True
        if not rows_to_refresh:
            if skipped_incompatible:
                self._window._set_status_message("Paste skipped incompatible cells.")
            return
        self._window._populate_experiment_control_table(steps, selected_row=min(rows_to_refresh))
        self._select_rows(sorted(rows_to_refresh))
        self._window.save_ui_state()
        self._window._set_status_message(
            "Pasted selection." if not skipped_incompatible else "Pasted compatible cells; skipped incompatible cells."
        )

    def move_selected_rows(self, delta: int) -> None:
        steps = self._window._read_experiment_control_steps()
        rows = self._selected_rows()
        rows = sorted({row for row in rows if row is not None and 0 <= row < len(steps)})
        if not rows or not steps:
            return
        first_row = rows[0]
        last_row = rows[-1]
        target = min(max(first_row + delta, 0), len(steps) - (last_row - first_row + 1))
        if target == first_row:
            return
        block = [steps[row] for row in range(first_row, last_row + 1)]
        remaining = steps[:first_row] + steps[last_row + 1 :]
        target = min(max(target, 0), len(remaining))
        steps = remaining[:target] + block + remaining[target:]
        self._window._populate_experiment_control_table(steps, selected_row=target)
        self._select_rows(list(range(target, target + len(block))))
        self.clear_copied_selection()
        self._window.save_ui_state()
        direction = "up" if delta < 0 else "down"
        self._window._set_status_message(f"Moved {len(block)} step(s) {direction} to position {target + 1}.")

    def duplicate_selected_rows(self) -> None:
        steps = self._window._read_experiment_control_steps()
        rows = self._selected_rows()
        rows = sorted({row for row in rows if row is not None and 0 <= row < len(steps)})
        if not rows or not steps:
            self._window._show_info("Select a plan row to duplicate.")
            return
        first_row = rows[0]
        last_row = rows[-1]
        duplicates = [duplicate_plan_step(steps[row]) for row in range(first_row, last_row + 1)]
        insert_at = last_row + 1
        steps = steps[:insert_at] + duplicates + steps[insert_at:]
        self._window._populate_experiment_control_table(steps, selected_row=insert_at)
        self._select_rows(list(range(insert_at, insert_at + len(duplicates))))
        self.clear_copied_selection()

    def remove_selected_rows(self) -> None:
        steps = self._window._read_experiment_control_steps()
        rows = self._selected_rows()
        rows = sorted({row for row in rows if row is not None and 0 <= row < len(steps)})
        if not rows or not steps:
            self._window._show_info("Select a plan row to remove.")
            return
        first_row = rows[0]
        last_row = rows[-1]
        del steps[first_row : last_row + 1]
        replacement_row = min(first_row, len(steps) - 1) if steps else None
        self._window._populate_experiment_control_table(steps, selected_row=replacement_row)
        self.clear_copied_selection()

    def _index_from_mouse_event(self, obj: object, event) -> QModelIndex | None:
        model = self._table.model()
        if model is None:
            return None
        viewport = self._table.viewport()
        if viewport is None:
            return None
        index = QModelIndex()
        if obj is viewport:
            index = self._table.indexAt(event.position().toPoint())
        else:
            global_pos = getattr(event, "globalPosition", None)
            if callable(global_pos):
                index = self._table.indexAt(viewport.mapFromGlobal(global_pos().toPoint()))
        if index.isValid():
            return index
        cell = self._window._flow_table_cell_for_widget(obj)
        if cell is None:
            return None
        row, column = cell
        if 0 <= row < model.rowCount() and 0 <= column < model.columnCount():
            candidate = model.index(row, column)
            if candidate.isValid():
                return candidate
        return None

    def _select_from_anchor(self, anchor: QModelIndex, current: QModelIndex) -> None:
        if not anchor.isValid() or not current.isValid():
            return
        if anchor.column() == 0 or current.column() == 0:
            self._select_rows(list(range(min(anchor.row(), current.row()), max(anchor.row(), current.row()) + 1)))
            return
        self._select_range(anchor.row(), anchor.column(), current.row(), current.column())

    def _begin_drag(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        self._selection_anchor = (index.row(), index.column())
        self._selection_dragging = False
        if index.column() == 0:
            self._select_rows([index.row()])
        else:
            self._set_selection([(index.row(), index.column())])

    def _update_drag(self, index: QModelIndex) -> None:
        if self._selection_anchor is None or not index.isValid():
            return
        anchor_row, anchor_column = self._selection_anchor
        anchor = self._table.model().index(anchor_row, anchor_column) if self._table.model() is not None else QModelIndex()
        if not anchor.isValid():
            return
        self._selection_dragging = True
        self._select_from_anchor(anchor, index)

    def _end_drag(self, index: QModelIndex | None = None) -> None:
        if self._selection_anchor is None:
            return
        if index is not None and index.isValid():
            self._update_drag(index)
        self._selection_anchor = None
        self._selection_dragging = False

    def _move_selection_by_key(self, key: int) -> None:
        model = self._table.model()
        if model is None or model.rowCount() <= 0 or model.columnCount() <= 0:
            return
        current = self._table.currentIndex()
        if not current.isValid():
            rows = self._selected_rows()
            if rows:
                row = rows[0]
                column = self._table.currentColumn()
                if column < 0 or column >= model.columnCount():
                    column = 0
                current = model.index(row, column)
        if not current.isValid():
            current = model.index(0, 0)
        if not current.isValid():
            return
        if self._selection_anchor is None:
            selected = self._selected_indexes()
            if selected:
                first = selected[0]
                self._selection_anchor = (first.row(), first.column())
            else:
                self._selection_anchor = (current.row(), current.column())
        row = current.row()
        column = current.column()
        if key == Qt.Key.Key_Left:
            column = max(column - 1, 0)
        elif key == Qt.Key.Key_Right:
            column = min(column + 1, model.columnCount() - 1)
        elif key == Qt.Key.Key_Up:
            row = max(row - 1, 0)
        elif key == Qt.Key.Key_Down:
            row = min(row + 1, model.rowCount() - 1)
        else:
            return
        next_index = model.index(row, column)
        if not next_index.isValid():
            return
        anchor_row, anchor_column = self._selection_anchor
        anchor_index = model.index(anchor_row, anchor_column)
        if not anchor_index.isValid():
            return
        self._select_from_anchor(anchor_index, next_index)
        self._table.setCurrentIndex(next_index)
        self._sync_overlay()

    def event_filter(self, obj, event) -> bool:
        if not self._edit_mode:
            return False
        if event.type() == QEvent.Type.KeyPress and getattr(obj, "property", None) is not None:
            if bool(obj.property("flow_navigation")):
                key = event.key()
                if (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) and key in (
                    Qt.Key.Key_Left,
                    Qt.Key.Key_Right,
                    Qt.Key.Key_Up,
                    Qt.Key.Key_Down,
                ):
                    self._move_selection_by_key(key)
                    return True
                if event.matches(QKeySequence.StandardKey.Copy):
                    self.copy_selection()
                    return True
                if event.matches(QKeySequence.StandardKey.Paste):
                    self.paste_selection()
                    return True
                if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                    self.move_selected_rows(-1 if key == Qt.Key.Key_PageUp else 1)
                    return True
        if event.type() == QEvent.Type.MouseButtonPress and getattr(obj, "property", None) is not None:
            if bool(obj.property("flow_navigation")) and event.button() == Qt.MouseButton.LeftButton:
                index = self._index_from_mouse_event(obj, event)
                if index is not None and index.isValid():
                    self._begin_drag(index)
                    self._table.setCurrentIndex(index)
                    return True
            if bool(obj.property("open_popup_on_click")):
                combo = self._window._combo_popup_target(obj)
                if combo is not None and getattr(self._window, "_ui_startup_ready", False):
                    QTimer.singleShot(0, combo.showPopup)
        if event.type() == QEvent.Type.MouseMove and getattr(obj, "property", None) is not None:
            if bool(obj.property("flow_navigation")) and (event.buttons() & Qt.MouseButton.LeftButton):
                index = self._index_from_mouse_event(obj, event)
                if index is not None and index.isValid():
                    self._update_drag(index)
                    return True
        if event.type() == QEvent.Type.MouseButtonRelease and getattr(obj, "property", None) is not None:
            if bool(obj.property("flow_navigation")) and event.button() == Qt.MouseButton.LeftButton:
                index = self._index_from_mouse_event(obj, event)
                self._end_drag(index)
                return True
        if event.type() == QEvent.Type.Wheel and getattr(obj, "property", None) is not None:
            if bool(obj.property("flow_wheel_scroll")):
                if obj.__class__.__name__ == "QDoubleSpinBox":
                    if obj.hasFocus():
                        return False
                    return self._window._scroll_plan_table_by_wheel(event.angleDelta().y())
                if obj.__class__.__name__ == "QComboBox":
                    if obj.hasFocus():
                        return False
                    return self._window._scroll_plan_table_by_wheel(event.angleDelta().y())
                if obj.__class__.__name__ == "QLineEdit":
                    if self._window._widget_or_ancestor_has_focus(obj, (type(obj),)):
                        return False
                    return self._window._scroll_plan_table_by_wheel(event.angleDelta().y())
        if event.type() == QEvent.Type.MouseButtonPress and getattr(obj, "property", None) is not None:
            if bool(obj.property("flow_navigation")):
                cell = self._window._flow_table_cell_for_widget(obj)
                if cell is not None:
                    row, column = cell
                    if row != self._table.currentRow() or column != self._table.currentColumn():
                        self._table.setCurrentCell(row, column)
                    self._table.horizontalScrollBar().setValue(0)
                    if not self._window._plan_table_layout_locked:
                        self._window._fit_plan_table_columns_to_viewport()
        if event.type() == QEvent.Type.Resize and (obj is self._table or obj is self._table.viewport()):
            self._sync_overlay()
        return False

    def _sync_overlay(self) -> None:
        if self._overlay is None:
            return
        viewport = self._table.viewport()
        if (
            viewport is None
            or not self._window.isVisible()
            or not self._table.isVisible()
            or not viewport.isVisible()
        ):
            self._overlay.hide()
            return
        self._overlay.setGeometry(QRect(0, 0, viewport.width(), viewport.height()))
        self._overlay.show()
        self._overlay.raise_()
        self._overlay.update()

    def sync_overlay(self) -> None:
        self._sync_overlay()
