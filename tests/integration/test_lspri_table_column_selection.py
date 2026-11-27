"""Column selection in the ROI / Groups table, as in a spreadsheet (2026-11-26).

Pinned: `click_columns` (plain click, Ctrl, Shift, an anchor, a hidden column); a click on a header selects the column and the sort end of the header (opposite its title's alignment) sorts instead; Ctrl and Shift
extend the selection; the selection is kept by key (it follows a step column that moves, and comes back when a hidden column is shown); the selected columns are painted (header and
cells); `StepLink` announces only the step columns, sets them quietly from the plan bar's selection, and keeps a selected fixed column.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app.panels.roi_table.column_selection import click_columns, is_step_key
    from lspr_imaging_app.panels.roi_table.rows import COLUMN_COUNT, COLUMN_ID, COLUMN_NAME, COLUMN_X
    from lspr_imaging_app.panels.roi_table.view import _SORT_ZONE
    from tests.integration.test_lspri_step_column_width import WidthBase
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

CTRL, SHIFT, NONE = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier, Qt.KeyboardModifier.NoModifier


class ClickColumnsTest(unittest.TestCase):
    SHOWN = [0, 1, ("a",), ("b",), ("c",)]

    def test_a_plain_click_selects_only_that_column(self) -> None:
        chosen, anchor = click_columns({0, 1}, 0, ("b",), self.SHOWN)
        self.assertEqual((chosen, anchor), (frozenset({("b",)}), ("b",)))

    def test_ctrl_adds_a_column_and_takes_a_selected_one_out(self) -> None:
        chosen, anchor = click_columns({("a",)}, ("a",), ("c",), self.SHOWN, ctrl=True)
        self.assertEqual((chosen, anchor), (frozenset({("a",), ("c",)}), ("c",)))
        chosen, _ = click_columns(chosen, anchor, ("a",), self.SHOWN, ctrl=True)
        self.assertEqual(chosen, frozenset({("c",)}))

    def test_shift_selects_the_columns_between_the_anchor_and_the_click_and_keeps_the_anchor(self) -> None:
        chosen, anchor = click_columns({1}, 1, ("b",), self.SHOWN, shift=True)
        self.assertEqual(chosen, frozenset({1, ("a",), ("b",)}))
        self.assertEqual(anchor, 1)

    def test_shift_without_an_anchor_is_a_plain_click(self) -> None:
        chosen, anchor = click_columns(set(), None, ("b",), self.SHOWN, shift=True)
        self.assertEqual((chosen, anchor), (frozenset({("b",)}), ("b",)))

    def test_a_column_that_is_not_shown_changes_nothing_and_drops_out_of_the_selection(self) -> None:
        chosen, anchor = click_columns({0, ("gone",)}, 0, ("hidden",), self.SHOWN)
        self.assertEqual((chosen, anchor), (frozenset({0}), 0))

    def test_which_keys_are_steps(self) -> None:
        self.assertFalse(is_step_key(3))
        self.assertFalse(is_step_key(("cmp", 1, "difference")))
        self.assertTrue(is_step_key(("measured", 12.0, "note")))


class ColumnSelectionBase(WidthBase):
    def setUp(self) -> None:
        super().setUp()
        self.header = self.tree.header()
        self.link = self.view.step_link
        self.columns = self.model.step_columns()
        self.assertGreaterEqual(len(self.columns), 2)
        self.announced: list[tuple] = []
        self.tree.column_selection.changed.connect(self.announced.append)
        self.steps_announced: list[tuple] = []
        self.link.selected.connect(self.steps_announced.append)

    def key(self, position: int):
        return self.tree.column_key(COLUMN_COUNT + position)

    def click(self, column: int, where: str = "middle", modifiers=NONE) -> None:
        """A left click on the header of `column`: in its sort zone ("sort": the end opposite its title's alignment), at its "left_end" or "right_end", or in the middle of it."""
        left, width = self.header.sectionViewportPosition(column), self.header.sectionSize(column)
        if where == "sort":
            where = "right_end" if self.header.sort_zone_on_right(column) else "left_end"
        offset = {"left_end": 4, "right_end": width - 4}.get(where, width // 2 + _SORT_ZONE // 2)
        x = left + offset
        point = QPointF(x, 6.0)
        for kind, buttons in ((QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton), (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)):
            event = QMouseEvent(kind, point, self.header.viewport().mapToGlobal(point), Qt.MouseButton.LeftButton, buttons, modifiers)
            (self.header.mousePressEvent if kind == QEvent.Type.MouseButtonPress else self.header.mouseReleaseEvent)(event)


class HeaderClickTest(ColumnSelectionBase):
    def test_a_click_on_a_step_header_selects_that_column_and_does_not_sort(self) -> None:
        sort_before = self.model.sort_state()
        self.click(COLUMN_COUNT)
        self.assertEqual(self.tree.column_selection.selected_keys(), (self.key(0),))
        self.assertTrue(self.tree.is_column_selected(COLUMN_COUNT))
        self.assertFalse(self.tree.is_column_selected(COLUMN_COUNT + 1))
        self.assertEqual(self.model.sort_state(), sort_before)

    def test_a_click_on_the_sort_end_of_a_header_sorts_and_does_not_select(self) -> None:
        self.click(COLUMN_X, "sort")
        self.assertEqual(self.model.sort_state()[0], COLUMN_X)
        self.assertFalse(self.tree.has_column_selection())
        self.assertEqual(self.announced, [])

    def test_the_sort_zone_is_at_the_end_opposite_the_titles_alignment(self) -> None:
        """Label (left-aligned) sorts at its right end; numbers and steps (right-aligned) at their left end (2026-11-27)."""
        self.assertTrue(self.header.sort_zone_on_right(COLUMN_NAME))
        for column in (COLUMN_X, COLUMN_COUNT):
            self.assertFalse(self.header.sort_zone_on_right(column), column)

    def test_the_label_header_sorts_at_its_right_end_and_selects_elsewhere(self) -> None:
        self.assertGreater(self.header.sectionSize(COLUMN_NAME), 2 * _SORT_ZONE)  # (wide enough that the two ends are different places)
        self.click(COLUMN_NAME, "left_end")  # the left end is where its title is: a click selects
        self.assertNotEqual(self.model.sort_state()[0], COLUMN_NAME)
        self.assertTrue(self.tree.is_column_selected(COLUMN_NAME))
        self.click(COLUMN_NAME, "right_end", modifiers=CTRL)  # (Ctrl would extend a selection, but the sort zone sorts whatever is held)
        self.assertEqual(self.model.sort_state()[0], COLUMN_NAME)

    def test_a_number_header_does_not_sort_at_its_right_end(self) -> None:
        self.assertGreater(self.header.sectionSize(COLUMN_X), 2 * _SORT_ZONE)
        self.click(COLUMN_X, "right_end")
        self.assertNotEqual(self.model.sort_state()[0], COLUMN_X)
        self.assertTrue(self.tree.is_column_selected(COLUMN_X))

    def ink(self, column: int, end: str) -> int:
        """How many pixels of the header of `column`, in its left or right `_SORT_ZONE` px (not the line under it), differ from the section's fill."""
        image = self.header.viewport().grab().toImage()
        left, width = self.header.sectionViewportPosition(column), self.header.sectionSize(column)
        fill = image.pixelColor(left + 1, 2)
        xs = range(_SORT_ZONE) if end == "left" else range(width - _SORT_ZONE, width)
        count = 0
        for y in range(self.header.viewport().height() - 2):
            for x in xs:
                c = image.pixelColor(left + x, y)
                count += max(abs(c.red() - fill.red()), abs(c.green() - fill.green()), abs(c.blue() - fill.blue())) > 40
        return count

    def test_the_sort_triangle_is_drawn_at_the_sort_zones_end(self) -> None:
        for column, end, other in ((COLUMN_NAME, "right", "left"), (COLUMN_X, "left", "right"), (COLUMN_COUNT, "left", "right")):
            self.tree.setColumnWidth(column, max(self.header.sectionSize(column), 120))  # (wide: a title does not come near the triangle)
            self.header.setSortIndicator(COLUMN_ID, Qt.SortOrder.AscendingOrder)
            _APP.processEvents()
            at_end, at_other = self.ink(column, end), self.ink(column, other)
            for order in (Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder):
                self.header.setSortIndicator(column, order)
                _APP.processEvents()
                self.assertGreater(self.ink(column, end), at_end, (column, end, order, "no triangle at the sort end"))
                self.assertEqual(self.ink(column, other), at_other, (column, other, order, "something at the other end changed"))

    def test_a_fixed_column_can_be_selected_too(self) -> None:
        self.click(COLUMN_X)
        self.assertTrue(self.tree.is_column_selected(COLUMN_X))
        self.assertEqual(self.tree.column_selection.selected_keys(), (COLUMN_X,))

    def test_ctrl_adds_and_shift_extends(self) -> None:
        self.click(COLUMN_COUNT)
        self.click(COLUMN_COUNT + 1, modifiers=CTRL)
        self.assertEqual(self.tree.column_selection.selected_keys(), (self.key(0), self.key(1)))
        self.click(COLUMN_COUNT, modifiers=CTRL)
        self.assertEqual(self.tree.column_selection.selected_keys(), (self.key(1),))
        self.click(COLUMN_X)
        self.click(COLUMN_COUNT + 1, modifiers=SHIFT)
        self.assertTrue(all(self.tree.is_column_selected(c) for c in (COLUMN_X, COLUMN_COUNT, COLUMN_COUNT + 1)))

    def test_every_change_by_a_click_is_announced_once(self) -> None:
        self.click(COLUMN_COUNT)
        self.click(COLUMN_COUNT)  # the same selection again: nothing new
        self.assertEqual(self.announced, [(self.key(0),)])

    def test_the_selected_header_and_cells_are_painted_differently(self) -> None:
        before = self.tree.viewport().grab().toImage()
        header_before = self.header.viewport().grab().toImage()
        self.click(COLUMN_COUNT)
        _APP.processEvents()
        self.assertNotEqual(self.tree.viewport().grab().toImage(), before)
        self.assertNotEqual(self.header.viewport().grab().toImage(), header_before)


class FrameTest(ColumnSelectionBase):
    def test_a_lone_column_has_both_sides_and_a_run_only_its_two_ends(self) -> None:
        self.click(COLUMN_COUNT)
        self.assertEqual(self.tree.column_frame(COLUMN_COUNT), (True, True))
        self.click(COLUMN_COUNT + 1, modifiers=SHIFT)
        self.assertEqual(self.tree.column_frame(COLUMN_COUNT), (True, False))
        self.assertEqual(self.tree.column_frame(COLUMN_COUNT + 1), (False, True))
        self.assertIsNone(self.tree.column_frame(COLUMN_X))

    def test_columns_that_are_not_next_to_each_other_have_a_frame_each(self) -> None:
        self.click(COLUMN_X)
        self.click(COLUMN_COUNT + 1, modifiers=CTRL)
        self.assertEqual(self.tree.column_frame(COLUMN_X), (True, True))
        self.assertEqual(self.tree.column_frame(COLUMN_COUNT + 1), (True, True))

    def test_a_hidden_column_does_not_break_a_run(self) -> None:
        self.click(COLUMN_COUNT)
        self.click(COLUMN_COUNT + 1, modifiers=SHIFT)
        self.tree.set_hidden_keys({self.key(0)})
        self.assertEqual(self.tree.column_frame(COLUMN_COUNT + 1), (True, True))  # the left neighbour that was selected is hidden: this one is the run now

    def test_a_click_that_selects_does_not_turn_the_sort_arrow(self) -> None:
        before = (self.header.sortIndicatorSection(), self.header.sortIndicatorOrder())
        self.click(COLUMN_COUNT)
        self.assertEqual((self.header.sortIndicatorSection(), self.header.sortIndicatorOrder()), before)


class KeptByKeyTest(ColumnSelectionBase):
    def test_a_hidden_column_is_not_selected_and_is_again_when_shown(self) -> None:
        self.click(COLUMN_COUNT)
        key = self.key(0)
        self.tree.set_hidden_keys({key})
        self.assertFalse(self.tree.is_column_selected(COLUMN_COUNT))
        self.assertEqual(self.tree.column_selection.selected_keys(), ())
        self.tree.set_hidden_keys(set())
        self.assertTrue(self.tree.is_column_selected(COLUMN_COUNT))

    def test_a_selection_set_before_its_columns_exist_applies_when_they_do(self) -> None:
        self.tree.column_selection.set_keys({("unknown", 1.0, "step")})
        self.assertFalse(self.tree.has_column_selection())
        self.tree.column_selection.set_keys({self.key(1)})
        self.assertTrue(self.tree.is_column_selected(COLUMN_COUNT + 1))


class StepLinkTest(ColumnSelectionBase):
    def test_a_click_announces_the_step_columns_selected(self) -> None:
        self.click(COLUMN_COUNT + 1)
        self.assertEqual(self.steps_announced, [(self.columns[1].ref,)])

    def test_a_fixed_column_announces_no_step(self) -> None:
        self.click(COLUMN_COUNT)
        self.click(COLUMN_X)  # replaces the selection: no step column is selected any more
        self.assertEqual(self.steps_announced, [(self.columns[0].ref,), ()])

    def test_setting_the_steps_from_the_bar_selects_the_columns_and_announces_nothing(self) -> None:
        self.link.set_selected([self.columns[0].ref, self.columns[1].ref])
        self.assertTrue(self.tree.is_column_selected(COLUMN_COUNT) and self.tree.is_column_selected(COLUMN_COUNT + 1))
        self.assertEqual((self.announced, self.steps_announced), ([], []))
        self.link.set_selected([])
        self.assertFalse(self.tree.has_column_selection())

    def test_a_selected_fixed_column_stays_when_the_bar_changes(self) -> None:
        self.click(COLUMN_X)
        self.link.set_selected([self.columns[0].ref])
        self.assertTrue(self.tree.is_column_selected(COLUMN_X) and self.tree.is_column_selected(COLUMN_COUNT))
        self.link.set_selected([])
        self.assertTrue(self.tree.is_column_selected(COLUMN_X))


if __name__ == "__main__":
    unittest.main()
