"""Hiding and showing columns of the ROI / Groups table (2026-11-06): right-click a column header -> "Hide column", "Show hidden column" (a list), "Show all hidden columns".

Pinned: hiding takes out that column only; the entries follow the state (the title of the column under the pointer, the list of hidden
columns, greyed when nothing is hidden); a view mode never brings a hidden column back, and a hidden column the mode does not show is not offered; a hidden column keeps its width;
a table sorted by a hidden column goes back to the # order; the hidden columns are remembered and come back; a freeze that ends at a hidden column holds the visible ones before it.
With #/ROIs hidden: a group header draws no chevron, chip or count (and has no click zone for them; the arrow keys open and close the group), its name starts where a ROI's label
does, and the colour the dot / chip showed is painted behind the label (a ROI's label cell, a group's header up to the step columns) and goes again when the column is shown.
For the step columns: a hidden step stays hidden when the columns are rebuilt, refitted or renumbered (it is named by what it is), the fitting never shows it, the numbers of
a group header are not drawn for it, it comes back through the menu and after a restart; and a comparison column is hidden by its pair and calculation.
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QEvent, QModelIndex, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QHeaderView, QLineEdit, QStyleOptionViewItem

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app.image_tools import GeometryModule
    from lspr_imaging_app.panels.roi_table import RoiTablePanel
    from lspr_imaging_app.panels.roi_table.delegate import CHEVRON_ZONE, LABEL_WASH_ALPHA, RoiTableDelegate, label_wash_of
    from lspr_imaging_app.panels.roi_table.model import COLOR_ROLE, KIND_ROLE
    from lspr_imaging_app.panels.roi_table.rows import (
        COLUMN_COUNT,
        COLUMN_ID,
        COLUMN_NAME,
        COLUMN_RING_OUT,
        COLUMN_SAMPLE,
        COLUMN_TITLES,
        COLUMN_X,
        COLUMN_Y,
        VIEW_GEOMETRY,
        VIEW_SIMPLE,
    )
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.undo import undo_manager
    from tests._qt_fonts import load_system_fonts
    from tests.integration.test_lspri_plan_bar import _pump
    from tests.integration.test_lspri_step_table import StepTableBase
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

load_system_fonts()

KEY = "roi_table/hidden_columns"
HORIZONTAL = Qt.Orientation.Horizontal


def menu_texts(menu: QtWidgets.QMenu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


class FixedColumnsBase(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        for x in (10.0, 20.0, 30.0):
            self.toolbox.add_roi(x, 5.0, sample_diameter_px=6.0)
        self.toolbox.add_rois_to_group([1], self.toolbox.create_group("Alpha"))
        self.ui = UiStateStore({})
        self.panel = RoiTablePanel(self.toolbox, SelectionModule(), GeometryModule())
        self.panel.restore_ui_state(self.ui)
        self.messages: list[str] = []
        self.panel.status_message.connect(self.messages.append)
        self.tree, self.model, self.hide = self.panel._tree, self.panel._model, self.panel._hide
        self.panel._view_actions[VIEW_GEOMETRY].trigger()
        self.panel.resize(800, 340)
        self.panel.show()
        self.panel.refresh_now()
        _APP.processEvents()

    def tearDown(self) -> None:
        self.panel.hide()
        self.panel.deleteLater()
        undo_manager.clear()

    def hidden(self) -> set[int]:
        """The fixed columns that do not show: hidden for Qt, or (#/ROIs) collapsed to nothing."""
        return {c for c in range(COLUMN_COUNT) if self.tree.isColumnHidden(c) or self.tree.columnWidth(c) == 0}

    def shown_list(self) -> list[str]:
        return menu_texts(self.hide._show_menu)


class HidingTest(FixedColumnsBase):
    def test_hiding_a_column_takes_out_that_column_only(self) -> None:
        before = self.hidden()
        self.hide._hide_section(COLUMN_X)
        self.assertEqual(self.hidden(), before | {COLUMN_X})

    def test_hiding_says_how_to_bring_the_column_back(self) -> None:
        self.hide._hide_section(COLUMN_Y)
        self.assertTrue(any("Hid column" in m and "Show hidden column" in m for m in self.messages), self.messages)

    def test_a_hidden_column_keeps_its_width_for_when_it_is_shown_again(self) -> None:
        self.tree.setColumnWidth(COLUMN_X, 93)
        self.hide._hide_section(COLUMN_X)
        self.hide._show(COLUMN_X)
        self.assertFalse(self.tree.isColumnHidden(COLUMN_X))
        self.assertEqual(self.tree.columnWidth(COLUMN_X), 93)

    def test_a_table_sorted_by_a_column_that_is_hidden_goes_back_to_the_number_order(self) -> None:
        self.panel._rebuild(lambda: self.model.set_sort(COLUMN_X, True), reveal=False)
        self.assertEqual(self.model.sort_state(), (COLUMN_X, True))
        self.hide._hide_section(COLUMN_Y)
        self.assertEqual(self.model.sort_state(), (COLUMN_X, True), "hiding another column must not touch the sort")
        self.hide._hide_section(COLUMN_X)
        self.assertEqual(self.model.sort_state(), (COLUMN_ID, False))
        self.assertEqual(self.ui.get("roi_table/sort"), [COLUMN_ID, False])

    def test_a_freeze_that_ends_at_a_hidden_column_holds_the_visible_ones_before_it(self) -> None:
        self.tree.set_frozen_upto(COLUMN_X)
        self.assertEqual(self.tree.pinned_columns(), [COLUMN_ID, COLUMN_X])
        self.hide._hide_section(COLUMN_X)
        self.assertEqual(self.tree.pinned_columns(), [COLUMN_ID])


class TheMenuTest(FixedColumnsBase):
    def test_the_entry_names_the_column_under_the_pointer(self) -> None:
        self.hide.prepare(COLUMN_Y)
        self.assertEqual(self.hide._hide.text(), f"Hide column “{COLUMN_TITLES[COLUMN_Y]}”")
        self.assertTrue(self.hide._hide.isEnabled())
        self.hide.prepare(-1)  # a right-click that is not on a column
        self.assertFalse(self.hide._hide.isEnabled())

    def test_with_nothing_hidden_the_two_show_entries_are_greyed_and_say_why(self) -> None:
        self.hide.prepare(COLUMN_Y)
        self.assertFalse(self.hide._show_menu.isEnabled())
        self.assertFalse(self.hide._show_all.isEnabled())
        self.assertIn("No column is hidden", self.hide._show_all.toolTip())
        self.assertEqual(self.shown_list(), [])

    def test_the_list_holds_the_hidden_columns_left_to_right_and_a_click_brings_one_back(self) -> None:
        self.hide._hide_section(COLUMN_Y)
        self.hide._hide_section(COLUMN_X)
        self.hide.prepare(COLUMN_SAMPLE)
        self.assertEqual(self.shown_list(), [COLUMN_TITLES[COLUMN_X], COLUMN_TITLES[COLUMN_Y]])
        self.assertTrue(self.hide._show_menu.isEnabled() and self.hide._show_all.isEnabled())
        self.hide._show_menu.actions()[0].trigger()  # x
        self.assertFalse(self.tree.isColumnHidden(COLUMN_X))
        self.assertTrue(self.tree.isColumnHidden(COLUMN_Y))
        self.assertEqual(self.ui.get(KEY), [COLUMN_Y])

    def test_show_all_brings_every_hidden_column_back_and_forgets_them(self) -> None:
        for column in (COLUMN_X, COLUMN_Y, COLUMN_RING_OUT):
            self.hide._hide_section(column)
        self.hide.prepare(COLUMN_SAMPLE)
        self.hide._show_all.trigger()
        self.assertFalse(any(self.tree.isColumnHidden(c) for c in (COLUMN_X, COLUMN_Y, COLUMN_RING_OUT)))
        self.assertEqual(self.ui.get(KEY), [])
        self.hide.prepare(COLUMN_SAMPLE)
        self.assertFalse(self.hide._show_all.isEnabled())

    def test_the_right_click_on_a_header_prepares_the_entries_for_that_column(self) -> None:
        header = self.tree.header()
        point = header.rect().center()
        point.setX(header.sectionViewportPosition(COLUMN_Y) + 5)
        with mock.patch.object(QtWidgets.QMenu, "exec", return_value=None):
            self.panel._show_header_menu(point)
        self.assertEqual(self.hide._hide.text(), f"Hide column “{COLUMN_TITLES[COLUMN_Y]}”")

    def test_the_hide_and_show_entries_sit_between_the_view_choice_and_the_freeze_entries(self) -> None:
        texts = menu_texts(self.panel._view_menu)
        order = ["Geometry view (position, shape, diameters)", "Hide column", "Show hidden column", "Show all hidden columns", "Freeze columns up to here", "Unfreeze columns", "Fit step columns to content"]
        self.assertEqual([t for t in texts if t in order], order)
        self.assertIsNotNone(next(a for a in self.panel._view_menu.actions() if a.text() == "Show hidden column").menu(), "the list is a submenu")


class WithTheViewModeTest(FixedColumnsBase):
    def test_a_view_mode_never_brings_a_hidden_column_back(self) -> None:
        self.hide._hide_section(COLUMN_X)
        self.panel._view_actions[VIEW_SIMPLE].trigger()
        self.assertTrue(self.tree.isColumnHidden(COLUMN_X))
        self.assertFalse(self.tree.isColumnHidden(COLUMN_NAME))
        self.panel._view_actions[VIEW_GEOMETRY].trigger()
        self.assertTrue(self.tree.isColumnHidden(COLUMN_X), "switching the view showed the column the user hid")
        self.assertFalse(self.tree.isColumnHidden(COLUMN_Y))

    def test_a_hidden_column_the_current_view_does_not_have_is_not_offered(self) -> None:
        self.hide._hide_section(COLUMN_X)
        self.panel._view_actions[VIEW_SIMPLE].trigger()  # the Simple view has no x
        self.hide.prepare(COLUMN_NAME)
        self.assertEqual(self.shown_list(), [])
        self.assertFalse(self.hide._show_all.isEnabled())
        self.panel._view_actions[VIEW_GEOMETRY].trigger()
        self.hide.prepare(COLUMN_Y)
        self.assertEqual(self.shown_list(), [COLUMN_TITLES[COLUMN_X]])


class RememberedTest(FixedColumnsBase):
    def test_the_hidden_columns_are_saved_and_come_back_in_a_new_panel(self) -> None:
        self.hide._hide_section(COLUMN_X)
        self.hide._hide_section(COLUMN_RING_OUT)
        self.assertEqual(self.ui.get(KEY), [COLUMN_X, COLUMN_RING_OUT])
        again = RoiTablePanel(self.toolbox, SelectionModule(), GeometryModule())
        again.restore_ui_state(self.ui)
        again._view_actions[VIEW_GEOMETRY].trigger()
        again.refresh_now()
        self.assertTrue(again._tree.isColumnHidden(COLUMN_X) and again._tree.isColumnHidden(COLUMN_RING_OUT))
        self.assertFalse(again._tree.isColumnHidden(COLUMN_Y))
        again.deleteLater()

    def test_what_the_state_holds_that_cannot_be_read_is_ignored(self) -> None:
        junk = UiStateStore({KEY: [99, "x", None, True, {"junk": 1}, COLUMN_Y]})
        panel = RoiTablePanel(self.toolbox, SelectionModule(), GeometryModule())
        panel.restore_ui_state(junk)
        self.assertEqual(panel._tree.hidden_keys(), frozenset({COLUMN_Y}))
        panel.deleteLater()


class HidingTheFirstColumnTest(FixedColumnsBase):
    """#/ROIs can be hidden, and the rest of the table keeps working (2026-11-06). Qt attaches the full-row group header to the first column, so the column is collapsed to
    zero width instead of being hidden: these tests pin that the group headers, clicks, renaming, drops, selection, widths and freezing still behave."""

    def group(self):
        return self.model.group_headers()[0]

    def mouse(self, kind: QEvent.Type, x: float, y: float) -> QMouseEvent:
        point, button = QPointF(x, y), Qt.MouseButton.LeftButton
        return QMouseEvent(kind, point, point, button, button, Qt.KeyboardModifier.NoModifier)

    def painted(self) -> tuple[list[tuple[str, int]], int]:
        """((kind, column) of every cell the delegate painted, how many colour dots were drawn) for one paint of the table."""
        cells, dots = [], []
        real_paint, real_dot = RoiTableDelegate.paint, RoiTableDelegate._paint_dot

        def spy(delegate, painter, option, index):
            cells.append((str(index.data(KIND_ROLE)), index.column()))
            return real_paint(delegate, painter, option, index)

        def spy_dot(*args, **kwargs):
            dots.append(1)
            return real_dot(*args, **kwargs)

        RoiTableDelegate.paint, RoiTableDelegate._paint_dot = spy, staticmethod(spy_dot)
        try:
            self.tree.viewport().grab()
        finally:
            RoiTableDelegate.paint, RoiTableDelegate._paint_dot = real_paint, staticmethod(real_dot)
        return cells, len(dots)

    def test_the_column_has_no_width_and_the_others_keep_theirs(self) -> None:
        widths = {c: self.tree.columnWidth(c) for c in range(COLUMN_COUNT) if c != COLUMN_ID}
        self.hide._hide_section(COLUMN_ID)
        self.assertEqual(self.tree.columnWidth(COLUMN_ID), 0)
        self.assertEqual({c: self.tree.columnWidth(c) for c in widths}, widths)
        self.assertEqual(self.tree.header().sectionViewportPosition(COLUMN_X), 0, "the first visible column starts at the left edge")

    def test_the_group_headers_are_still_drawn_and_the_roi_rows_lose_only_their_dot_and_number(self) -> None:
        _cells, dots = self.painted()
        self.assertGreater(dots, 0)
        self.hide._hide_section(COLUMN_ID)
        cells, dots = self.painted()
        self.assertIn(("group", COLUMN_ID), cells, "the group headers vanished")
        self.assertEqual(dots, 0, "a ROI's dot is drawn over the next column")
        self.assertIn(("roi", COLUMN_X), cells)

    def test_a_group_header_is_still_one_cell_across_the_whole_row(self) -> None:
        self.hide._hide_section(COLUMN_ID)
        rect = self.tree.visualRect(self.group())
        self.assertGreater(rect.width(), 0)
        self.assertEqual(rect.width(), self.tree.header().length())

    # Row heights: Qt asks only the columns from the first one *with width* on, so with #/ROIs collapsed the delegate's "column 0 decides" left every row at height 0 as soon
    # as the table was rebuilt (found by the maintainer 2026-11-06: an empty table at start-up, and everything gone on a click on a group).

    def assertRowsHaveHeight(self) -> None:
        for group in self.model.group_headers():
            self.assertGreater(self.tree.visualRect(group).height(), 0, "a group header has no height")
        cells, _dots = self.painted()
        self.assertIn(("group", COLUMN_ID), cells, "no group header was drawn")
        self.assertTrue([kind for kind, _column in cells if kind == "roi"], "no ROI row was drawn")

    def test_the_rows_keep_their_height_when_the_table_is_rebuilt_with_the_column_hidden(self) -> None:
        for mode in (VIEW_GEOMETRY, VIEW_SIMPLE):
            with self.subTest(mode=mode):
                self.panel._view_actions[mode].trigger()
                self.hide._hide_section(COLUMN_ID)
                self.panel.refresh_now()
                _APP.processEvents()
                self.assertRowsHaveHeight()
                self.hide._show(COLUMN_ID)

    def test_a_click_on_a_group_header_with_the_column_hidden_does_not_empty_the_table(self) -> None:
        self.hide._hide_section(COLUMN_ID)
        self.panel.refresh_now()
        _APP.processEvents()
        for x in (30, 200):  # (over where the chevron / the name is)
            center = self.tree.visualRect(self.group()).center()
            QTest.mouseClick(self.tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, center.y()))
            _APP.processEvents()
            self.panel.refresh_now()  # (what the selection that follows may trigger)
            _APP.processEvents()
            self.assertRowsHaveHeight()
        self.assertEqual(sorted(self.panel._selection.selected_roi_ids()), [1], "the click did not select the group's ROI")

    def test_a_panel_started_with_the_column_hidden_has_rows(self) -> None:
        self.hide._hide_section(COLUMN_ID)
        for show_first in (True, False):  # (laid out before the first fill, or filled before it was ever shown: Qt then asks the model's columns, not the header's)
            with self.subTest(shown_before_the_fill=show_first):
                again = RoiTablePanel(self.toolbox, SelectionModule(), GeometryModule())
                again.restore_ui_state(self.ui)
                again.resize(800, 340)
                if show_first:
                    again.show()
                again.refresh_now()
                again.show()
                _APP.processEvents()
                self.assertEqual(again._tree.columnWidth(COLUMN_ID), 0)
                for group in again._model.group_headers():
                    self.assertGreater(again._tree.visualRect(group).height(), 0)
                self.assertGreater(again._tree.visualRect(again._model.roi_index(2, COLUMN_X)).height(), 0)
                again.hide()
                again.deleteLater()

    def test_the_delegate_gives_a_row_height_from_the_first_column_with_width(self) -> None:
        option = QStyleOptionViewItem()
        option.widget, option.fontMetrics = self.tree, self.tree.fontMetrics()
        delegate, other = self.tree.itemDelegate(), self.model.roi_index(2, COLUMN_X)
        self.assertEqual(delegate.sizeHint(option, other).height(), 0, "the shortcut for the columns Qt does not ask is gone")
        self.hide._hide_section(COLUMN_ID)
        self.assertEqual(delegate.sizeHint(option, other).height(), delegate.sizeHint(option, self.model.roi_index(2, COLUMN_ID)).height())
        self.assertGreater(delegate.sizeHint(option, other).height(), 0)

    def click_left_edge(self) -> None:
        self.tree.mousePressEvent(self.mouse(QEvent.Type.MouseButtonPress, 10, self.tree.visualRect(self.group()).center().y()))

    def test_with_the_column_shown_the_chevron_collapses_and_opens_a_group(self) -> None:
        self.assertTrue(self.tree.isExpanded(self.group()))
        self.click_left_edge()
        self.assertFalse(self.tree.isExpanded(self.group()))
        self.click_left_edge()
        self.assertTrue(self.tree.isExpanded(self.group()))

    def test_with_the_column_hidden_there_is_no_chevron_to_click_and_the_arrow_keys_open_and_close_the_group(self) -> None:
        self.hide._hide_section(COLUMN_ID)
        self.click_left_edge()
        self.assertTrue(self.tree.isExpanded(self.group()), "a click where the chevron was toggled the group")
        self.tree.setCurrentIndex(self.group())
        QTest.keyClick(self.tree, Qt.Key.Key_Left)
        self.assertFalse(self.tree.isExpanded(self.group()))
        QTest.keyClick(self.tree, Qt.Key.Key_Right)
        self.assertTrue(self.tree.isExpanded(self.group()))
        self.hide._show(COLUMN_ID)
        self.click_left_edge()
        self.assertFalse(self.tree.isExpanded(self.group()), "the chevron did not come back with the column")

    def marks_drawn(self) -> int:
        """How many group headers drew their chevron, colour chip and count in one paint of the table."""
        drawn, real = [], RoiTableDelegate._paint_group_marks

        def spy(*args, **kwargs):
            drawn.append(1)
            return real(*args, **kwargs)

        RoiTableDelegate._paint_group_marks = staticmethod(spy)
        try:
            self.tree.viewport().grab()
        finally:
            RoiTableDelegate._paint_group_marks = staticmethod(real)
        return len(drawn)

    def test_a_group_header_draws_no_chevron_chip_or_count_while_the_column_is_hidden(self) -> None:
        shown = self.marks_drawn()
        self.assertEqual(shown, len(self.model.group_headers()))
        self.hide._hide_section(COLUMN_ID)
        self.assertEqual(self.marks_drawn(), 0)
        self.hide._show(COLUMN_ID)
        self.assertEqual(self.marks_drawn(), shown)

    def test_there_is_no_colour_chip_to_double_click_while_the_column_is_hidden(self) -> None:
        index = self.group()
        chip_x = self.tree.visualRect(index).left() + CHEVRON_ZONE + 4
        self.assertTrue(self.tree._on_chip(index, chip_x))
        self.hide._hide_section(COLUMN_ID)
        self.assertFalse(self.tree._on_chip(index, chip_x), "a double-click on the empty place of the chip would open the colour dialog")

    def test_the_name_starts_where_a_roi_label_starts_and_the_editor_follows(self) -> None:
        def name_rect():
            option = QStyleOptionViewItem()
            option.rect, option.widget, option.fontMetrics = self.tree.visualRect(self.group()), self.tree, self.tree.fontMetrics()
            return RoiTableDelegate._group_name_rect(option, self.group(), "1")

        self.assertGreater(name_rect().left(), self.tree.columnWidth(COLUMN_ID))
        self.hide._hide_section(COLUMN_ID)
        label_text_left = self.tree.visualRect(self.group()).left() + 6  # where the text of the first visible column starts (the delegate's cell padding)
        self.assertEqual(name_rect().left(), label_text_left)
        self.tree.edit(self.group())
        self.assertLessEqual(self.tree.indexWidget(self.group()).geometry().left(), label_text_left)

    # The colour a dot / chip showed is painted behind the label instead (`delegate.LABEL_WASH_ALPHA`).

    def blend(self, base: QColor, color_hex: str) -> QColor:
        color = QColor(color_hex)
        alpha = LABEL_WASH_ALPHA
        return QColor(*(round(b + (c - b) * alpha) for b, c in ((base.red(), color.red()), (base.green(), color.green()), (base.blue(), color.blue()))))

    def pixel(self, x: int, y: int) -> QColor:
        self.tree.setCurrentIndex(QModelIndex())  # (the current row's focus highlight is painted over its cells)
        self.tree.clearSelection()
        return self.tree.viewport().grab().toImage().pixelColor(x, y)

    def simple_view(self) -> None:
        """The Label column is only in the Simple view (the table starts in the Geometry view, where a ROI's colour stays in its Shape pictogram)."""
        self.panel._view_actions[VIEW_SIMPLE].trigger()
        self.panel.refresh_now()
        _APP.processEvents()

    def label_pixel(self, roi: int) -> QColor:
        """A pixel at the right end of a ROI's label cell, clear of its text."""
        cell = self.model.roi_index(roi, COLUMN_NAME)
        header = self.tree.header()
        return self.pixel(header.sectionViewportPosition(COLUMN_NAME) + header.sectionSize(COLUMN_NAME) - 8, self.tree.visualRect(cell).center().y())

    def group_pixel(self) -> QColor:
        """A pixel at the far end of the group header, clear of its name."""
        return self.pixel(self.tree.header().length() - 12, self.tree.visualRect(self.group()).center().y())

    def assertClose(self, got: QColor, want: QColor, tolerance: int = 4) -> None:
        self.assertTrue(
            all(abs(a - b) <= tolerance for a, b in ((got.red(), want.red()), (got.green(), want.green()), (got.blue(), want.blue()))),
            f"{got.name()} is not {want.name()}",
        )

    def test_a_roi_label_takes_the_colour_of_the_roi_while_the_column_is_hidden_and_loses_it_when_it_is_shown(self) -> None:
        self.simple_view()
        color = self.model.data(self.model.roi_index(2, COLUMN_NAME), COLOR_ROLE)
        self.assertTrue(color)
        plain = self.label_pixel(2)
        self.hide._hide_section(COLUMN_ID)
        washed = self.label_pixel(2)
        self.assertNotEqual(washed.name(), plain.name())
        self.assertClose(washed, self.blend(plain, color))
        self.hide._show(COLUMN_ID)
        self.assertEqual(self.label_pixel(2).name(), plain.name())

    def test_the_wash_covers_the_label_cell_and_stops_at_its_end(self) -> None:
        self.simple_view()
        self.hide._hide_section(COLUMN_ID)
        y = self.tree.visualRect(self.model.roi_index(2, COLUMN_NAME)).center().y()
        end = self.tree.header().length()  # the Simple view is just #/ROIs (nothing now) and the label
        self.assertLess(end + 20, self.tree.viewport().width())
        self.assertNotEqual(self.pixel(end - 8, y).name(), self.pixel(end + 20, y).name(), "the label cell is not washed, or the wash runs on past it")

    def test_a_group_header_takes_the_colour_of_the_group_behind_its_name(self) -> None:
        color = self.model.data(self.group(), COLOR_ROLE)
        self.assertTrue(color)
        plain = self.group_pixel()
        self.hide._hide_section(COLUMN_ID)
        self.assertClose(self.group_pixel(), self.blend(plain, color))
        self.hide._show(COLUMN_ID)
        self.assertEqual(self.group_pixel().name(), plain.name())

    def test_a_row_with_no_colour_gets_no_wash(self) -> None:
        self.assertIsNone(label_wash_of(None))
        self.assertIsNone(label_wash_of(""))
        self.assertIsNone(label_wash_of("not a colour"))
        wash = label_wash_of("#336699")
        self.assertEqual(wash.name(), "#336699")
        self.assertAlmostEqual(wash.alphaF(), LABEL_WASH_ALPHA, delta=0.01)

    def test_a_group_can_still_be_renamed_and_the_editor_is_in_view(self) -> None:
        self.hide._hide_section(COLUMN_ID)
        self.tree.edit(self.group())
        editor = self.tree.indexWidget(self.group())
        self.assertIsInstance(editor, QLineEdit)
        self.assertGreaterEqual(editor.geometry().left(), 0)
        self.assertGreaterEqual(editor.geometry().width(), 60)
        self.assertLessEqual(editor.geometry().right(), self.tree.viewport().width())

    def test_a_click_at_the_left_edge_finds_the_roi_row_and_the_group_header_for_a_drop(self) -> None:
        self.hide._hide_section(COLUMN_ID)
        for roi in (1, 2, 3):
            y = self.tree.visualRect(self.model.roi_index(roi, COLUMN_X)).center().y()
            found = self.tree.indexAt(QPoint(2, y))
            self.assertTrue(found.isValid() and self.model.roi_id(found) == roi, (roi, found.row()))
        group_row = self.tree.indexAt(QPoint(2, self.tree.visualRect(self.group()).center().y()))
        self.assertEqual(group_row.data(KIND_ROLE), "group")  # where dragged ROIs are dropped

    def test_the_section_cannot_be_dragged_open_and_widths_being_applied_leave_it_alone(self) -> None:
        self.hide._hide_section(COLUMN_ID)
        self.assertEqual(self.tree.header().sectionResizeMode(COLUMN_ID), QHeaderView.ResizeMode.Fixed)
        self.panel._view_actions[VIEW_SIMPLE].trigger()  # applies the saved widths again
        self.panel._view_actions[VIEW_GEOMETRY].trigger()
        self.panel._apply_widths()
        self.tree.apply_default_column_widths()
        self.assertEqual(self.tree.columnWidth(COLUMN_ID), 0)

    def test_showing_it_again_brings_its_width_and_its_drag_handle_back(self) -> None:
        self.tree.setColumnWidth(COLUMN_ID, 93)
        self.hide._hide_section(COLUMN_ID)
        self.hide._show(COLUMN_ID)
        self.assertEqual(self.tree.columnWidth(COLUMN_ID), 93)
        self.assertEqual(self.tree.header().sectionResizeMode(COLUMN_ID), QHeaderView.ResizeMode.Interactive)
        self.assertEqual(self.ui.get("roi_table/column_widths")[COLUMN_ID], 93, "the width the hiding went through was saved as the column's width")

    def test_it_is_in_the_list_of_hidden_columns_and_the_menu_opens_from_anywhere_on_the_header(self) -> None:
        self.hide._hide_section(COLUMN_ID)
        self.hide.prepare(COLUMN_Y)
        self.assertEqual(self.shown_list(), [COLUMN_TITLES[COLUMN_ID]])
        point = self.tree.header().rect().center()
        with mock.patch.object(QtWidgets.QMenu, "exec", return_value=None):
            self.panel._show_header_menu(point)  # (the zero-width section has no place of its own to click)
        self.assertTrue(self.hide._show_all.isEnabled())

    def test_the_order_stays_the_number_order_and_nothing_is_rebuilt_for_it(self) -> None:
        rebuilt = []
        self.model.modelReset.connect(lambda: rebuilt.append(1))
        self.hide._hide_section(COLUMN_ID)
        self.assertEqual(self.model.sort_state(), (COLUMN_ID, False))
        self.assertEqual(rebuilt, [])

    def test_a_freeze_up_to_a_later_column_holds_the_visible_ones(self) -> None:
        self.hide._hide_section(COLUMN_ID)
        self.tree.set_frozen_upto(COLUMN_X)
        self.assertEqual(self.tree.pinned_width(), self.tree.columnWidth(COLUMN_X))  # #/ROIs adds nothing

    def test_it_is_remembered_and_comes_back_collapsed(self) -> None:
        self.hide._hide_section(COLUMN_ID)
        self.assertEqual(self.ui.get(KEY), [COLUMN_ID])
        again = RoiTablePanel(self.toolbox, SelectionModule(), GeometryModule())
        again.restore_ui_state(self.ui)
        again._view_actions[VIEW_GEOMETRY].trigger()
        again.refresh_now()
        self.assertEqual(again._tree.columnWidth(COLUMN_ID), 0)
        again.deleteLater()


class StepColumnsBase(StepTableBase):
    def setUp(self) -> None:
        super().setUp()
        self.ui = UiStateStore({})
        self.view = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry, step_table=self.table)
        self.view.restore_ui_state(self.ui)
        group_id = self.m.roi_toolbox.create_group("Pair")
        self.m.roi_toolbox.add_rois_to_group([1], group_id)
        self.view.resize(1200, 420)
        self.view.show()
        self.view.refresh_now()
        self.controls = self.view._step_controls
        self.controls.columns_button.click()
        self.assertTrue(_pump(lambda: not self.table.is_running()))
        _APP.processEvents()
        self.model, self.tree, self.hide = self.view._model, self.view._tree, self.view._hide
        self.first, self.second = COLUMN_COUNT, COLUMN_COUNT + 1

    def tearDown(self) -> None:
        self.view.hide()
        self.view.deleteLater()
        super().tearDown()

    def title(self, column: int) -> str:
        return str(self.model.headerData(column, HORIZONTAL, Qt.ItemDataRole.DisplayRole))


class HiddenStepColumnsTest(StepColumnsBase):
    def test_a_step_column_can_be_hidden_and_the_other_stays(self) -> None:
        self.hide._hide_section(self.first)
        self.assertTrue(self.tree.isColumnHidden(self.first))
        self.assertFalse(self.tree.isColumnHidden(self.second))

    def test_neither_fitting_nor_a_manual_width_nor_a_rebuild_shows_it_again(self) -> None:
        self.hide._hide_section(self.first)
        self.controls.fit_now()
        self.assertTrue(self.tree.isColumnHidden(self.first))
        self.tree.setColumnWidth(self.second, 160)  # a drag: every step column gets that width
        self.assertTrue(self.tree.isColumnHidden(self.first))
        self.view.refresh_now()
        self.controls.fit_now()
        self.table.set_title_style("solution", ("_-_", "_--_"))  # retitled: the columns are rebuilt
        self.assertTrue(self.tree.isColumnHidden(self.first))
        self.assertFalse(self.tree.isColumnHidden(self.second))

    def test_it_stays_the_same_step_when_the_columns_are_renumbered(self) -> None:
        self.hide._hide_section(self.second)  # the second step
        self.m.experiment_plan.remove_steps([0])  # the first one goes: the second becomes the first column
        self.assertEqual(self.model.columnCount(), COLUMN_COUNT + 1)
        self.assertTrue(self.tree.isColumnHidden(self.first), "the hidden step was forgotten when it changed its number")

    def test_the_numbers_of_a_group_header_are_not_drawn_for_it(self) -> None:
        self.assertEqual(RoiTableDelegate._step_columns_of(self.tree, self.model), [self.first, self.second])
        self.hide._hide_section(self.first)
        self.assertEqual(RoiTableDelegate._step_columns_of(self.tree, self.model), [self.second])

    def test_it_is_in_the_list_with_its_title_and_comes_back_through_it(self) -> None:
        title = self.title(self.first)
        self.hide._hide_section(self.first)
        self.hide.prepare(self.second)
        self.assertEqual(menu_texts(self.hide._show_menu), [title])
        self.hide._show_menu.actions()[0].trigger()
        self.assertFalse(self.tree.isColumnHidden(self.first))
        self.assertGreater(self.tree.columnWidth(self.first), 0)

    def test_a_table_sorted_by_the_hidden_step_goes_back_to_the_number_order(self) -> None:
        self.view._on_header_clicked(self.first)
        self.assertEqual(self.model.sort_state()[0], self.first)
        self.hide._hide_section(self.first)
        self.assertEqual(self.model.sort_state(), (COLUMN_ID, False))

    def test_it_is_remembered_by_the_step_and_hidden_again_in_a_new_panel(self) -> None:
        self.hide._hide_section(self.second)
        saved = self.ui.get(KEY)
        self.assertEqual(len(saved), 1)
        self.assertIsInstance(saved[0], dict)
        again = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry, step_table=self.table)
        again.restore_ui_state(self.ui)  # (the step columns were on, too)
        self.assertEqual(again._model.columnCount(), COLUMN_COUNT + 2)
        self.assertTrue(again._tree.isColumnHidden(self.second))
        self.assertFalse(again._tree.isColumnHidden(self.first))
        again.deleteLater()

    def test_a_step_hidden_in_another_experiment_hides_nothing_here(self) -> None:
        foreign = UiStateStore({KEY: [{"step": 1, "measured_start_unix_ms": 5, "measured_label": "not this experiment"}]})
        panel = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry, step_table=self.table)
        panel.restore_ui_state(foreign)
        panel._step_controls.set_shown(True, ask=False)
        self.assertEqual(panel._model.columnCount(), COLUMN_COUNT + 2)
        self.assertFalse(panel._tree.isColumnHidden(self.first) or panel._tree.isColumnHidden(self.second))
        panel.deleteLater()


class HiddenFirstColumnWithStepsTest(StepColumnsBase):
    def test_group_headers_keep_their_step_numbers_and_the_step_columns_their_widths(self) -> None:
        widths = [self.tree.columnWidth(c) for c in (self.first, self.second)]
        steps_painted: list[int] = []
        real = RoiTableDelegate._paint_group_steps

        def spy(delegate, painter, option, index, theme):
            steps_painted.append(len(RoiTableDelegate._step_columns_of(option.widget, index.model())))
            return real(delegate, painter, option, index, theme)

        self.hide._hide_section(COLUMN_ID)
        self.assertEqual(self.tree.columnWidth(COLUMN_ID), 0)
        with mock.patch.object(RoiTableDelegate, "_paint_group_steps", spy):
            self.tree.viewport().grab()
        self.assertTrue(steps_painted and all(n == 2 for n in steps_painted), steps_painted)
        self.controls.fit_now()
        self.assertEqual([self.tree.columnWidth(c) for c in (self.first, self.second)], widths)
        self.assertEqual(self.tree.columnWidth(COLUMN_ID), 0)


class HiddenComparisonColumnsTest(StepColumnsBase):
    def test_the_menus_name_a_comparison_column_without_underscores_and_a_step_by_its_own_title(self) -> None:
        self.table.add_compare()
        _pump(lambda: False, 0.1)
        column = self.model.columnCount() - 1
        self.assertEqual(self.title_of(column), "μ_S1 − μ_B1")  # the header draws the subscripts; a menu entry cannot
        self.hide.prepare(column)
        self.assertEqual(self.hide._hide.text(), "Hide column “μS1 − μB1”")
        self.hide._hide_section(column)
        self.hide.prepare(COLUMN_X)
        self.assertEqual(menu_texts(self.hide._show_menu), ["μS1 − μB1"])

    def title_of(self, column: int) -> str:
        return str(self.model.headerData(column, HORIZONTAL, Qt.ItemDataRole.DisplayRole))

    def test_a_comparison_column_is_hidden_by_its_pair_and_calculation(self) -> None:
        spec = self.table.add_compare()
        _pump(lambda: False, 0.1)
        column = self.model.columnCount() - 1
        self.hide._hide_section(column)
        self.assertTrue(self.tree.isColumnHidden(column))
        self.assertEqual(self.ui.get(KEY), [{"compare": [spec.pair, spec.op]}])
        self.table.set_compare_op(spec.uid, "snr")  # another calculation is another column: it shows again
        _pump(lambda: False, 0.1)
        self.assertFalse(self.tree.isColumnHidden(self.model.columnCount() - 1))


if __name__ == "__main__":
    unittest.main()
