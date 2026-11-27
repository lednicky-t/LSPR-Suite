"""The ROI / Group table's speed-ups of 2026-11-19 (apps/LSPRi/eva/docs/roi_table_performance_2026-11-19.md), pinned by what they do rather than by how long they take:

* a rebuild that lists the same rows in the same groups **updates the cells in place** (no model reset, no layout of every row again, groups stay open, the selection stays);
* selecting reads the view's selection ranges and selects runs of rows as one range;
* `model.flags` and `PAINT_ROLE` give exactly the answers the slower role-by-role code gave;
* an in-place rebuild paints the same pixels as a reset one.

Real panel and model; no window is shown by the test, and nothing is timed (timings are in the docs, measured by a script).
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import QItemSelectionModel, QModelIndex, Qt
from PyQt6.QtGui import QColor, QImage

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.domain.roi_model import RoiMask
    from lspr_imaging_app.domain.roi_shapes import NO_BORDER, Circle, MaskShape, Rectangle
    from lspr_imaging_app.panels.roi_table.model import (
        INHERITED_ROLE,
        MASK_ROLE,
        PAINT_ROLE,
        STEP_STALE_ROLE,
        STEP_TINT_ROLE,
        VALUES_ONLY_ROLE,
        StepCells,
    )
    from lspr_imaging_app.panels.roi_table.rows import (
        COLUMN_COUNT,
        COLUMN_ID,
        COLUMN_RIM_OFFSET,
        COLUMN_X,
        EDITABLE_COLUMNS,
        RIM_COLUMNS,
        SIZE_COLUMNS,
        has_cell,
    )
    from lspr_imaging_app.selection.step_table_module import CellValue, StepColumn
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_roi_table_panel import _PanelCase
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402


class _Grouped(_PanelCase):
    """Ten more ROIs (a rectangle, a map, a reference, a circle with no rim among them) in three groups; ROIs 1-5 come from the base."""

    def setUp(self) -> None:
        super().setUp()
        for i in range(10):
            x = 60.0 + 10.0 * i
            if i == 2:
                self.toolbox.add_shaped_roi(x, 5.0, Rectangle(12.0, 8.0, 0.0))
            elif i == 4:
                self.toolbox.add_shaped_roi(x, 5.0, MaskShape(RoiMask(int(x) - 3, 2, np.ones((6, 6), dtype=bool))))
            elif i == 6:
                self.toolbox.add_shaped_roi(x, 5.0, Circle(8.0), role="reference")
            elif i == 8:
                self.toolbox.add_shaped_roi(x, 5.0, Circle(8.0), border=NO_BORDER)
            else:
                self.toolbox.add_roi(x, 5.0, sample_diameter_px=6.0)
        ids = [r.area_roi_id for r in self.toolbox.rois()]
        self.toolbox.group_rois(ids[0:5], "A")
        self.toolbox.group_rois(ids[5:10], "B")
        self.toolbox.group_rois(ids[10:13], "C")
        self.refresh()
        self.resets: list[int] = []
        self.model.modelReset.connect(lambda: self.resets.append(1))
        undo_manager.clear()

    def header(self, group_id: str):  # (the base's helper, kept for the call sites below)
        return super().header(group_id)

    def group_ids(self) -> list[str]:
        return [g.group_id for g in self.toolbox.groups()]


class InPlaceRebuildTest(_Grouped):
    def test_a_value_edit_changes_the_cells_without_a_reset_and_keeps_open_groups_and_the_selection(self) -> None:
        first, second = self.group_ids()[:2]
        self.tree.collapse(self.header(second))
        self.selection.set_roi_selection({2, 3})
        before = self.model.rows()
        self.toolbox.place_rois([1], x=77.0)
        self.refresh()
        self.assertEqual(self.resets, [], "the same rows in the same groups: nothing is reset")
        self.assertEqual(self.text(1, COLUMN_X), "77.0", "the number changed")
        self.assertIsNot(self.model.rows(), before)
        self.assertTrue(self.tree.isExpanded(self.header(first)))
        self.assertFalse(self.tree.isExpanded(self.header(second)), "the group the user closed stays closed")
        self.assertEqual(self.panel._view_selected_ids(), {2, 3}, "the selection stays")

    def test_a_cosmetic_change_of_a_group_header_updates_in_place_too(self) -> None:
        group_id = self.group_ids()[0]
        self.toolbox.rename_group(group_id, "Renamed")
        self.refresh()
        self.assertEqual(self.resets, [])
        self.assertEqual(self.model.data(self.header(group_id)), "Renamed")

    def test_the_cells_say_they_changed_with_a_values_only_signal(self) -> None:
        seen: list[list] = []
        self.model.dataChanged.connect(lambda _a, _b, roles: seen.append(list(roles)))
        self.toolbox.place_rois([1], x=70.0)
        self.refresh()
        self.assertTrue(seen)
        self.assertTrue(all(roles == [VALUES_ONLY_ROLE] for roles in seen), seen)

    def test_an_in_place_rebuild_asks_the_delegate_for_no_row_height(self) -> None:
        """Qt measures every cell of a range a `dataChanged` names, unless the view is told it is only values (the reset path measured all of them: 20-30 ms for 160 ROIs)."""
        delegate = self.tree.itemDelegate()
        calls: list[int] = []
        original = delegate.sizeHint
        delegate.sizeHint = lambda option, index: (calls.append(1), original(option, index))[1]
        try:
            self.toolbox.place_rois([1], x=71.0)
            self.refresh()
            _APP.processEvents()
            self.assertEqual(calls, [], "an in-place rebuild measures nothing")
            self.toolbox.add_roi(5.0, 40.0)  # a new row: the layout changes
            self.refresh()
            _APP.processEvents()
            self.assertTrue(calls, "control: a rebuild with a new row does measure")
        finally:
            del delegate.sizeHint

    def test_a_new_row_a_removed_row_a_new_group_a_sort_and_the_linked_view_all_reset(self) -> None:
        self.toolbox.add_roi(5.0, 40.0)
        self.refresh()
        self.assertEqual(len(self.resets), 1, "a new ROI")
        self.toolbox.delete_rois([1])
        self.refresh()
        self.assertEqual(len(self.resets), 2, "a removed ROI")
        self.toolbox.group_rois([2, 3], "D")
        self.refresh()
        self.assertEqual(len(self.resets), 3, "a ROI moved to another group")
        self.model.set_sort(COLUMN_X, True)
        self.assertEqual(len(self.resets), 4, "a sort that changes the order")
        self.model.set_linked_view(True)
        self.assertEqual(len(self.resets), 5, "the other arrangement")

    def test_the_reset_flag_is_given_once(self) -> None:
        self.toolbox.add_roi(5.0, 40.0)
        self.refresh()
        self.assertFalse(self.model.take_reset_flag(), "the panel's rebuild already took it")
        self.model._layout = None
        self.refresh()
        self.assertFalse(self.model.take_reset_flag())

    def test_group_only_still_collapses_every_group_though_no_row_changed(self) -> None:
        self.panel.set_group_only(True)
        _APP.processEvents()
        self.assertEqual([self.tree.isExpanded(self.header(g)) for g in self.group_ids()], [False] * 3)
        self.panel.set_group_only(False)
        _APP.processEvents()
        self.assertEqual([self.tree.isExpanded(self.header(g)) for g in self.group_ids()], [True] * 3)

    def test_an_in_place_rebuild_paints_the_pixels_a_reset_one_does(self) -> None:
        self.tree.resize(560, 400)
        self.tree.show()
        self.selection.set_roi_selection({2, 6})
        _APP.processEvents()

        def picture() -> np.ndarray:
            self.tree.selectionModel().setCurrentIndex(QModelIndex(), QItemSelectionModel.SelectionFlag.NoUpdate)  # (a reset forgets the current row; an update keeps it, as it should)
            _APP.processEvents()
            image = self.tree.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
            return np.frombuffer(image.constBits().asstring(image.sizeInBytes()), dtype=np.uint8).copy()

        self.toolbox.place_rois([1], x=64.0)
        self.refresh()  # in place
        in_place = picture()
        self.model._layout = None
        self.refresh()  # a reset
        reset = picture()
        self.assertEqual(len(self.resets), 1)
        self.assertTrue(np.array_equal(in_place, reset), f"{int((in_place != reset).sum())} bytes differ")
        self.tree.hide()


class SelectionTest(_Grouped):
    def test_the_view_selection_is_read_from_ranges_and_sees_rois_and_group_headers(self) -> None:
        group_id = self.group_ids()[2]
        header = self.header(group_id)
        self.select_in_view(self.cell(2, COLUMN_ID), self.cell(4, COLUMN_ID))  # (replaces the selection)
        self.tree.selectionModel().select(header, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)  # (adds the header)
        rois, headers = self.panel._view_selection()
        self.assertEqual(sorted(rois), [2, 4])
        self.assertEqual([self.model.group_id(h) for h in headers], [group_id])

    def test_a_range_of_several_rows_is_every_row_in_it(self) -> None:
        from PyQt6.QtCore import QItemSelection

        first, last = self.cell(1, COLUMN_ID), self.cell(3, COLUMN_ID)
        selection = QItemSelection(first, last)
        self.tree.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
        self.assertEqual(sorted(self.panel._view_selection()[0]), [1, 2, 3])

    def test_selecting_everything_does_not_ask_the_model_for_the_flags_of_every_cell(self) -> None:
        ids = {r.area_roi_id for r in self.toolbox.rois()}
        calls: list[int] = []
        original = self.model.flags
        self.model.flags = lambda index: (calls.append(1), original(index))[1]
        try:
            self.selection.set_roi_selection(ids)
        finally:
            del self.model.flags
        self.assertEqual(self.panel._view_selected_ids(), ids)
        self.assertLess(len(calls), 20 * len(ids), f"{len(calls)} flag lookups to select {len(ids)} ROIs (`selectedRows` made ~12 per ROI per call)")

    def test_runs_of_rows_under_one_parent_are_one_range(self) -> None:
        runs = self.model.roi_index_runs({1, 2, 3, 5, 6, 8})
        as_ids = [(self.model.roi_id(a), self.model.roi_id(b)) for a, b in runs]
        self.assertEqual(as_ids, [(1, 3), (5, 5), (6, 6), (8, 8)], "group A holds 1-5, group B 6-10: 1-3, then 5 (the end of A), then 6 (the start of B: another parent), then 8")
        self.assertEqual(
            [(self.model.roi_id(a), self.model.roi_id(b)) for a, b in self.model.roi_index_runs({6, 7, 8, 9, 10})], [(6, 10)], "a whole group is one range"
        )
        self.assertEqual(self.model.roi_index_runs(set()), [])
        self.assertEqual(self.model.roi_index_runs({999}), [])

    def test_selecting_the_rois_of_a_run_selects_exactly_those_rows(self) -> None:
        self.selection.set_roi_selection({1, 2, 3, 5, 6, 8})
        self.assertEqual(self.panel._view_selected_ids(), {1, 2, 3, 5, 6, 8})
        self.selection.set_roi_selection({4})
        self.assertEqual(self.panel._view_selected_ids(), {4}, "a new selection replaces the old")

    def test_the_toolbar_state_can_reuse_a_view_selection_it_was_handed(self) -> None:
        self.selection.set_roi_selection({1, 2})
        calls: list[int] = []
        original = self.panel._view_selection
        self.panel._view_selection = lambda: (calls.append(1), original())[1]
        try:
            self.panel._update_toolbar_state(([1, 2], []))
            self.assertEqual(calls, [], "handed one: not read again")
            self.panel._update_toolbar_state()
            self.assertEqual(len(calls), 1, "none handed: read once (it was read three times)")
        finally:
            del self.panel._view_selection


class FlagsTest(_Grouped):
    """`flags` once built the combination with `|` for every cell; the constants must be the same combinations."""

    @staticmethod
    def reference_flags(model, index: QModelIndex):
        """The code `flags` had before 2026-11-19, kept here as the specification."""
        node = index.internalPointer()
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if node.kind == "group":
            if not model._linked:
                flags |= Qt.ItemFlag.ItemIsDropEnabled
            if index.column() == 0 and node.section.group.group_id is not None:
                flags |= Qt.ItemFlag.ItemIsEditable
            return flags
        if not model._linked:
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        column = index.column()
        if column in EDITABLE_COLUMNS and not (column in SIZE_COLUMNS and not has_cell(node.row, column)):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def every_index(self):
        for top in range(self.model.rowCount()):
            top_index = self.model.index(top, 0)
            for column in range(self.model.columnCount()):
                yield self.model.index(top, column)
            for child in range(self.model.rowCount(top_index)):
                for column in range(self.model.columnCount()):
                    yield self.model.index(child, column, top_index)

    def test_every_cell_of_the_grouped_flat_and_linked_tables_has_the_flags_it_had(self) -> None:
        for arrangement in ("grouped", "linked", "flat"):
            if arrangement == "flat":
                self.model.set_linked_view(False)
                self.toolbox.remove_rois_from_groups([r.area_roi_id for r in self.toolbox.rois()])  # no group left: a flat table
                self.refresh()
                self.assertFalse(self.model.is_grouped_view())
            else:
                self.model.set_linked_view(arrangement == "linked")
            checked = 0
            for index in self.every_index():
                self.assertEqual(self.model.flags(index), self.reference_flags(self.model, index), (arrangement, index.row(), index.column()))
                checked += 1
            self.assertGreater(checked, 100, arrangement)
        self.assertEqual(self.model.flags(QModelIndex()), Qt.ItemFlag.NoItemFlags)


class _Cells(StepCells):
    def roi_cell(self, roi_id, ref):
        if roi_id % 5 == 0:
            return None  # no number for this ROI
        return CellValue(n=10, mean=590.0 + roi_id * 0.01 + ref[1], sd=0.05, current=roi_id % 3 != 0, sem=0.015)

    def group_cell(self, key, members, ref):
        return CellValue(n=10, mean=590.0, sd=0.05, current=True, sem=0.015)


class LayoutPassTest(_Grouped):
    """`RoiTreeModel.index` (no `hasIndex`) and `layout_pass` (2026-11-26): Qt asks every cell of every row for its height while it expands the tree, 44 000 Python calls for 160 ROIs and 241
    step columns. The answers must stay what Qt's own bounds check gives, and the row heights what they were."""

    def setUp(self) -> None:
        super().setUp()
        self.model.set_step_columns([StepColumn((1, i), i + 1, f"step {i + 1}", "", (), "#58a6ff") for i in range(6)], _Cells())
        self.refresh()

    def parents(self) -> list[QModelIndex]:
        return [QModelIndex(), *self.model.group_headers()]

    def test_index_gives_what_a_bounds_check_by_has_index_gives(self) -> None:
        checked = 0
        for parent in self.parents():
            for row in range(-1, self.model.rowCount(parent) + 2):
                for column in range(-1, self.model.columnCount() + 2):
                    index = self.model.index(row, column, parent)
                    self.assertEqual(index.isValid(), self.model.hasIndex(row, column, parent), (row, column, parent.row()))
                    if index.isValid():
                        self.assertEqual((index.row(), index.column()), (row, column))
                        self.assertEqual(index.parent(), parent)
                    checked += 1
        self.assertGreater(checked, 200)
        header = self.model.group_headers()[0]
        self.assertFalse(self.model.index(0, 0, header.siblingAtColumn(1)).isValid())  # (a parent in another column has no children)

    def test_every_column_answers_a_row_height_so_a_table_scrolled_sideways_keeps_its_rows(self) -> None:
        """Qt takes a row's height from the columns on screen only. Scrolled sideways those are step columns; if they answer 0 every row is 0 px high after a rebuild (a sort):
        a blank table until the view is scrolled back to the start (2026-11-27)."""
        delegate = self.tree.itemDelegate()
        option = QtWidgets.QStyleOptionViewItem()
        option.initFrom(self.tree)
        option.widget = self.tree
        checked = 0
        for parent in self.parents():
            for row in range(self.model.rowCount(parent)):
                heights = {delegate.sizeHint(option, self.model.index(row, column, parent)).height() for column in range(self.model.columnCount())}
                self.assertEqual(len(heights), 1, (parent.row(), row))  # the same from every column
                self.assertGreater(heights.pop(), 0, (parent.row(), row))
                checked += 1
        self.assertGreater(checked, 10)
        self.assertGreater(len({delegate.sizeHint(option, self.model.index(0, 0, p)).height() for p in self.parents()}), 1, "a group header and a ROI row differ: the check needs both")

    def test_in_a_layout_pass_the_fixed_columns_and_the_first_shown_one_answer_and_it_ends_even_after_an_error(self) -> None:
        first_step, last_step = COLUMN_COUNT, self.model.columnCount() - 1
        self.assertGreater(last_step, first_step + 1)
        self.assertTrue(self.model.index(0, last_step).isValid())
        with self.model.layout_pass(first_step + 1):
            self.assertTrue(self.model.index(0, COLUMN_COUNT - 1).isValid())  # (a fixed column always answers)
            self.assertTrue(self.model.index(0, first_step + 1).isValid())  # (the one named)
            self.assertFalse(self.model.index(0, first_step).isValid())
            self.assertFalse(self.model.index(0, last_step).isValid())
        self.assertTrue(self.model.index(0, last_step).isValid())
        with self.assertRaises(RuntimeError), self.model.layout_pass(first_step):
            raise RuntimeError("boom")
        self.assertTrue(self.model.index(0, last_step).isValid())

    def show_wide(self) -> None:
        self.panel.resize(500, 400)
        self.panel.show()  # (the tree must be visible: a tree that is not shown is asked about every column, and the bug hides)
        _APP.processEvents()
        self.assertTrue(self.tree.isVisible())
        self.assertGreater(self.tree.horizontalScrollBar().maximum(), 0, "the table must be wider than the view for the check to mean anything")

    def assert_rows_have_height(self, label: str) -> None:
        for parent in self.parents():
            for row in range(self.model.rowCount(parent)):
                self.assertGreater(self.tree.visualRect(self.model.index(row, 0, parent)).height(), 0, (label, parent.row(), row))

    def test_a_sort_keeps_the_rows_wherever_the_table_is_scrolled_to_and_asks_the_delegate_once_per_row(self) -> None:
        """The blank table (2026-11-27): a sort while scrolled to the step columns left every row 0 px high. Qt asks the columns from the first one on screen to the last."""
        self.show_wide()
        bar = self.tree.horizontalScrollBar()
        asked: list[int] = []
        delegate_class = type(self.tree.itemDelegate())
        original = delegate_class.sizeHint

        def spy(delegate, option, index):
            asked.append(index.column())
            return original(delegate, option, index)

        delegate_class.sizeHint = spy
        try:
            for label, offset in (("left edge", 0), ("middle", bar.maximum() // 2), ("right end", bar.maximum())):
                bar.setValue(offset)
                _APP.processEvents()
                asked.clear()
                with self.assertNoLogs("lspr_imaging_app.panels.roi_table.panel", level="WARNING"):  # (no fall-back to the slow layout)
                    self.panel._on_header_clicked(COLUMN_X)
                    self.panel._on_header_clicked(COLUMN_X)  # (again: descending)
                self.assert_rows_have_height(label)
                rows = sum(self.model.rowCount(p) for p in self.parents())
                # two sorts: each row asked at most for the fixed columns and one step column, not for every step column (6 here, 241 in the app)
                self.assertLessEqual(len(asked), 2 * rows * (COLUMN_COUNT + 1), (label, len(asked), rows))
        finally:
            delegate_class.sizeHint = original


class PaintRoleTest(_Grouped):
    """`PAINT_ROLE` answers in one call what the delegate asked six or seven times: the text, whether it is dimmed, stale, its alignment and its tint."""

    def setUp(self) -> None:
        super().setUp()
        self.model.set_step_columns([StepColumn((1, i), i + 1, f"step {i + 1}", "", (), "#58a6ff") for i in range(3)], _Cells())
        self.refresh()

    def test_it_is_exactly_what_the_separate_roles_say_for_every_roi_cell(self) -> None:
        checked = 0
        for node_index in self.every_roi_index():
            text, dim, stale, alignment, tint = node_index.data(PAINT_ROLE)
            separate_text = str(node_index.data(Qt.ItemDataRole.DisplayRole) or "")
            self.assertEqual(text, separate_text, (node_index.row(), node_index.column()))
            separate_dim = bool(node_index.data(INHERITED_ROLE) or (node_index.data(MASK_ROLE) and separate_text == "mask"))
            self.assertEqual(bool(dim), separate_dim, (node_index.row(), node_index.column(), "dim"))
            self.assertEqual(bool(stale), bool(node_index.data(STEP_STALE_ROLE)), (node_index.row(), node_index.column(), "stale"))
            align = node_index.data(Qt.ItemDataRole.TextAlignmentRole)
            self.assertEqual(alignment, int(align), (node_index.row(), node_index.column(), "alignment"))
            separate_tint = node_index.data(STEP_TINT_ROLE)
            self.assertEqual(tint is None, not (isinstance(separate_tint, QColor) and separate_tint.isValid()))
            if tint is not None:
                self.assertEqual(tint.name(QColor.NameFormat.HexArgb), separate_tint.name(QColor.NameFormat.HexArgb))
            checked += 1
        self.assertGreater(checked, 13 * COLUMN_COUNT)

    def test_the_cases_that_differ_are_all_present(self) -> None:
        """The test above is only worth something if the table holds each kind of cell: a dimmed shared-default rim, a mask, a stale step number, a step cell with no number, a reference."""
        kinds = set()
        for index in self.every_roi_index():
            text, dim, stale, alignment, tint = index.data(PAINT_ROLE)
            if index.column() in RIM_COLUMNS and dim:
                kinds.add("inherited rim")
            if text == "mask":
                kinds.add("mask")
            if stale:
                kinds.add("stale")
            if index.column() >= COLUMN_COUNT and not text:
                kinds.add("step cell with no number")
            if tint is not None:
                kinds.add("tint")
            if index.column() == COLUMN_RIM_OFFSET and text == "":
                kinds.add("no rim")
        self.assertEqual(kinds, {"inherited rim", "mask", "stale", "step cell with no number", "tint", "no rim"})

    def test_a_group_header_has_none(self) -> None:
        self.assertIsNone(self.header(self.group_ids()[0]).data(PAINT_ROLE))

    def every_roi_index(self):
        for top in range(self.model.rowCount()):
            top_index = self.model.index(top, 0)
            for child in range(self.model.rowCount(top_index)):
                for column in range(self.model.columnCount()):
                    yield self.model.index(child, column, top_index)


if __name__ == "__main__":
    unittest.main()
