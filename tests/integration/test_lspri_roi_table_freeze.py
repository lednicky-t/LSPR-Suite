"""Frozen columns of the ROI / Groups table (2026-11-06): the first columns, up to a chosen one, stay at the left edge while the table is scrolled sideways.

`QTreeView` cannot do this, so `view.py` paints the frozen cells (and header sections) a second time unscrolled and moves hit-testing, cell rectangles and the header's mouse
events to match. What is pinned here, offscreen with real painting: which columns are held (screen order, hidden ones skipped, a freeze that fills too much of the panel waits for
a wider one); that what is *drawn* in the frozen region is the same pixels scrolled or not, for the table and for its header (with the control that it is not when nothing is
frozen); that clicks, cell rectangles, editors and the header's clicks and menu land on what is drawn; and the right-click entries and the remembered state.
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QImage, QMouseEvent
from PyQt6.QtWidgets import QLineEdit, QTreeView

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app.image_tools import GeometryModule
    from lspr_imaging_app.panels.roi_table import RoiTablePanel
    from lspr_imaging_app.panels.roi_table.rows import (
        COLUMN_COUNT,
        COLUMN_ID,
        COLUMN_NAME,
        COLUMN_RING_OUT,
        COLUMN_SHAPE,
        COLUMN_TITLES,
        COLUMN_X,
        COLUMN_Y,
    )
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.storage.ui_state_keys import ROI_TABLE_FROZEN_UPTO
    from lspr_imaging_app.undo import undo_manager
    from tests._qt_fonts import load_system_fonts
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

load_system_fonts()

ID_WIDTH, X_WIDTH, Y_WIDTH = 80, 70, 70
SCROLLED = 120


class FreezeBase(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.geometry = GeometryModule()
        for x in (10.0, 20.0, 30.0, 40.0, 50.0, 60.0):
            self.toolbox.add_roi(x, 5.0, sample_diameter_px=6.0)
        self.toolbox.add_rois_to_group([1, 2], self.toolbox.create_group("Alpha"))
        self.toolbox.add_rois_to_group([3], self.toolbox.create_group("Beta"))
        self.ui = UiStateStore({})
        self.panel = RoiTablePanel(self.toolbox, self.selection, self.geometry)
        self.panel.restore_ui_state(self.ui)
        self.messages: list[str] = []
        self.panel.status_message.connect(self.messages.append)
        self.tree = self.panel._tree
        self.model = self.panel._model
        self.panel.resize(300, 340)
        self.panel.show()
        for column, width in ((COLUMN_ID, ID_WIDTH), (COLUMN_X, X_WIDTH), (COLUMN_Y, Y_WIDTH)):
            self.tree.setColumnWidth(column, width)
        self.panel.refresh_now()
        _APP.processEvents()

    def tearDown(self) -> None:
        self.panel.hide()
        self.panel.deleteLater()
        undo_manager.clear()

    def scroll_to(self, value: int) -> None:
        bar = self.tree.horizontalScrollBar()
        bar.setValue(value)
        _APP.processEvents()
        self.assertEqual(bar.value(), value, "the table is not wide enough to scroll that far")

    def row_y(self, roi_id: int = 1) -> int:
        return self.tree.visualRect(self.model.roi_index(roi_id, COLUMN_ID)).center().y()

    def mouse(self, kind: QEvent.Type, point: QPointF, *, pressed: bool) -> QMouseEvent:
        button = Qt.MouseButton.LeftButton
        return QMouseEvent(kind, point, point, button, button if pressed else Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)

    def click_header(self, x: float) -> None:
        header = self.tree.header()
        point = QPointF(x, 6.0)
        header.mousePressEvent(self.mouse(QEvent.Type.MouseButtonPress, point, pressed=True))
        header.mouseReleaseEvent(self.mouse(QEvent.Type.MouseButtonRelease, point, pressed=False))


class WhichColumnsAreHeldTest(FreezeBase):
    def test_nothing_is_frozen_at_first_and_the_view_answers_as_qt_does(self) -> None:
        self.assertEqual(self.tree.frozen_upto(), -1)
        self.assertEqual(self.tree.pinned_columns(), [])
        self.scroll_to(SCROLLED)
        point = QPoint(10, self.row_y())
        self.assertEqual(self.tree.indexAt(point), QTreeView.indexAt(self.tree, point))

    def test_the_columns_from_the_first_up_to_the_chosen_one_are_held_in_screen_order(self) -> None:
        self.tree.set_frozen_upto(COLUMN_X)
        self.assertEqual(self.tree.pinned_columns(), [COLUMN_ID, COLUMN_X])
        self.assertEqual(self.tree.pinned_width(), ID_WIDTH + X_WIDTH)

    def test_a_freeze_that_would_fill_too_much_of_the_panel_waits_for_a_wider_one(self) -> None:
        self.tree.set_frozen_upto(COLUMN_Y)  # 80 + 70 + 70 = 220 of 300
        self.assertEqual(self.tree.pinned_columns(), [])
        self.panel.resize(450, 340)
        _APP.processEvents()
        self.assertEqual(self.tree.pinned_columns(), [COLUMN_ID, COLUMN_X, COLUMN_Y])
        self.panel.resize(300, 340)
        _APP.processEvents()
        self.assertEqual(self.tree.pinned_columns(), [])

    def test_the_shape_column_counts_where_it_is_shown_which_is_after_y(self) -> None:
        self.panel.resize(700, 340)
        _APP.processEvents()
        self.tree.set_frozen_upto(COLUMN_SHAPE)
        self.assertEqual(self.tree.pinned_columns()[:4], [COLUMN_ID, COLUMN_X, COLUMN_Y, COLUMN_SHAPE])

    def test_a_hidden_last_column_freezes_the_visible_ones_before_it(self) -> None:
        self.tree.set_frozen_upto(COLUMN_NAME)  # the geometry view hides the label column
        self.assertEqual(self.tree.pinned_columns(), [COLUMN_ID])
        self.tree.setColumnWidth(COLUMN_NAME, 70)
        self.panel._view_actions["simple"].trigger()
        self.assertEqual(self.tree.pinned_columns(), [COLUMN_ID, COLUMN_NAME])


class WhatIsDrawnTest(FreezeBase):
    """The frozen region shows the same pixels whether the table is scrolled or not."""

    TOLERANCE = 4  # colour levels: the rounded corners of Qt's focus frame are antialiased and land a level or two apart when drawn a second time; the differences that matter are tens

    def region(self, widget: QtWidgets.QWidget, width: int) -> np.ndarray:
        image = widget.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
        rows = np.frombuffer(image.constBits().asstring(image.sizeInBytes()), dtype=np.uint8).reshape(image.height(), image.bytesPerLine())
        return rows[:, : (width - 1) * 4].astype(int)  # (the last column of pixels is the freeze's edge line)

    def biggest_difference(self, a: np.ndarray, b: np.ndarray) -> int:
        return int(np.abs(a - b).max())

    def test_without_a_freeze_scrolling_changes_that_region_this_is_what_the_next_tests_prevent(self) -> None:
        width = ID_WIDTH + X_WIDTH
        before = self.region(self.tree.viewport(), width)
        self.scroll_to(SCROLLED)
        self.assertGreater(self.biggest_difference(before, self.region(self.tree.viewport(), width)), 30)

    def test_with_a_freeze_the_rows_and_the_group_headers_look_the_same_scrolled_or_not(self) -> None:
        self.tree.set_frozen_upto(COLUMN_X)
        width = self.tree.pinned_width()
        unscrolled = self.region(self.tree.viewport(), width)
        self.scroll_to(SCROLLED)
        self.assertLessEqual(self.biggest_difference(unscrolled, self.region(self.tree.viewport(), width)), self.TOLERANCE, "the frozen cells changed when the table was scrolled")

    def test_the_current_row_of_a_view_with_focus_is_framed_over_the_frozen_cells_too(self) -> None:
        """Qt frames the current row of a view with focus (all columns show focus); the second drawing of the frozen cells must carry that frame as well."""
        self.tree.setFocus()
        self.tree.setCurrentIndex(self.model.roi_index(1, COLUMN_ID))
        self.tree.set_frozen_upto(COLUMN_X)
        width = self.tree.pinned_width()
        unscrolled = self.region(self.tree.viewport(), width)
        self.scroll_to(SCROLLED)
        self.assertLessEqual(self.biggest_difference(unscrolled, self.region(self.tree.viewport(), width)), self.TOLERANCE)

    def test_with_a_freeze_the_header_sections_look_the_same_scrolled_or_not(self) -> None:
        header = self.tree.header()
        width = ID_WIDTH + X_WIDTH
        self.scroll_to(SCROLLED)
        loose = self.region(header.viewport(), width)
        self.scroll_to(0)
        self.tree.set_frozen_upto(COLUMN_X)
        unscrolled = self.region(header.viewport(), width)
        self.scroll_to(SCROLLED)
        self.assertLessEqual(self.biggest_difference(unscrolled, self.region(header.viewport(), width)), self.TOLERANCE, "the frozen header sections changed when the table was scrolled")
        self.assertGreater(self.biggest_difference(loose, unscrolled), 30)  # and it is not just the scrolled header that happens to look alike

    def test_the_scrolled_columns_still_scroll(self) -> None:
        self.tree.set_frozen_upto(COLUMN_X)
        outside = self.model.roi_index(1, COLUMN_COUNT - 1)  # the last fixed column, far right
        before = self.tree.visualRect(outside).left()
        self.scroll_to(SCROLLED)
        self.assertEqual(self.tree.visualRect(outside).left(), before - SCROLLED)


class WhatAnswersToThePointerTest(FreezeBase):
    def test_a_point_over_the_frozen_region_finds_the_frozen_cell_a_point_beyond_it_the_scrolled_one(self) -> None:
        self.tree.set_frozen_upto(COLUMN_X)
        self.scroll_to(SCROLLED)
        y = self.row_y()
        self.assertEqual(self.tree.indexAt(QPoint(10, y)).column(), COLUMN_ID)
        self.assertEqual(self.tree.indexAt(QPoint(ID_WIDTH + 10, y)).column(), COLUMN_X)
        beyond = QPoint(ID_WIDTH + X_WIDTH + 20, y)
        found = self.tree.indexAt(beyond)
        self.assertEqual(found, QTreeView.indexAt(self.tree, beyond))
        self.assertNotIn(found.column(), (COLUMN_ID, COLUMN_X))

    def test_the_rectangle_of_a_frozen_cell_is_where_it_is_drawn(self) -> None:
        self.tree.set_frozen_upto(COLUMN_X)
        self.scroll_to(SCROLLED)
        self.assertEqual(self.tree.visualRect(self.model.roi_index(1, COLUMN_ID)).left(), 0)
        self.assertEqual(self.tree.visualRect(self.model.roi_index(1, COLUMN_X)).left(), ID_WIDTH)
        self.assertEqual(self.tree.column_at(10), COLUMN_ID)
        self.assertEqual(self.tree.column_at(ID_WIDTH + 10), COLUMN_X)

    def test_the_editor_of_a_frozen_cell_opens_over_it(self) -> None:
        self.tree.set_frozen_upto(COLUMN_X)
        self.scroll_to(SCROLLED)
        self.tree.edit(self.model.roi_index(1, COLUMN_X))
        _APP.processEvents()
        editors = self.tree.viewport().findChildren(QLineEdit)
        self.assertEqual(len(editors), 1)
        self.assertEqual(editors[0].geometry().left(), ID_WIDTH + 2)  # the cell's left edge plus the delegate's margin, not a position scrolled off to the left

    def test_a_click_on_a_frozen_header_section_is_a_click_on_that_section(self) -> None:
        self.tree.set_frozen_upto(COLUMN_X)
        self.scroll_to(SCROLLED)
        clicked: list[int] = []
        self.tree.header().sectionClicked.connect(clicked.append)
        self.click_header(10)
        self.click_header(ID_WIDTH + 10)
        self.assertEqual(clicked, [COLUMN_ID, COLUMN_X])
        header = self.tree.header()
        self.assertEqual(header.section_at(10), COLUMN_ID)
        beyond = ID_WIDTH + X_WIDTH + 20
        self.assertEqual(header.section_at(beyond), header.logicalIndexAt(beyond))


class RightClickAndMemoryTest(FreezeBase):
    def open_menu(self, x: float) -> None:
        header = self.tree.header()
        with mock.patch.object(self.panel._view_menu, "exec"):
            header.customContextMenuRequested.emit(QPoint(int(x), 5))

    def test_the_menu_offers_a_freeze_up_to_the_column_that_was_clicked(self) -> None:
        freeze = self.panel._freeze
        self.open_menu(ID_WIDTH + 10)
        self.assertEqual(freeze._freeze.text(), f"Freeze columns up to “{COLUMN_TITLES[COLUMN_X]}”")
        self.assertTrue(freeze._freeze.isVisible() and freeze._freeze.isEnabled())
        self.assertFalse(freeze._unfreeze.isVisible())
        freeze._freeze.trigger()
        self.assertEqual(self.tree.frozen_upto(), COLUMN_X)
        self.assertEqual(self.ui.get(ROI_TABLE_FROZEN_UPTO), COLUMN_X)

    def test_the_column_the_freeze_ends_at_offers_only_to_unfreeze_and_other_columns_both(self) -> None:
        freeze = self.panel._freeze
        self.tree.set_frozen_upto(COLUMN_X)
        self.open_menu(ID_WIDTH + 10)
        self.assertFalse(freeze._freeze.isVisible())
        self.assertTrue(freeze._unfreeze.isVisible())
        self.open_menu(5)  # the first column: the freeze could be moved there, or removed
        self.assertTrue(freeze._freeze.isVisible() and freeze._unfreeze.isVisible())
        freeze._unfreeze.trigger()
        self.assertEqual(self.tree.frozen_upto(), -1)
        self.assertEqual(self.ui.get(ROI_TABLE_FROZEN_UPTO), -1)

    def test_a_right_click_over_a_frozen_section_names_that_section_while_the_table_is_scrolled(self) -> None:
        self.tree.set_frozen_upto(COLUMN_X)
        self.scroll_to(SCROLLED)
        self.open_menu(10)
        self.assertIn(COLUMN_TITLES[COLUMN_ID], self.panel._freeze._freeze.text())

    def test_the_other_entries_of_the_menu_are_unchanged(self) -> None:
        checkable = [a for a in self.panel._view_menu.actions() if a.isCheckable()]
        self.assertEqual([a.isChecked() for a in checkable], [False, True])

    def test_a_freeze_too_wide_for_the_panel_says_so_in_the_title_bar(self) -> None:
        self.panel._freeze._choose(COLUMN_Y)
        self.assertTrue(any("too much of the panel" in m for m in self.messages), self.messages)
        self.panel._freeze._choose(COLUMN_X)
        self.assertEqual(self.tree.pinned_columns(), [COLUMN_ID, COLUMN_X])

    def test_the_freeze_is_remembered_and_comes_back_and_nonsense_is_ignored(self) -> None:
        self.panel._freeze._choose(COLUMN_X)
        again = RoiTablePanel(self.toolbox, self.selection, self.geometry)
        again.restore_ui_state(self.ui)
        self.assertEqual(again._tree.frozen_upto(), COLUMN_X)
        again.deleteLater()
        for bad in ("x", 99, -7, True, None, 2.5):
            fresh = RoiTablePanel(self.toolbox, self.selection, self.geometry)
            fresh.restore_ui_state(UiStateStore({ROI_TABLE_FROZEN_UPTO: bad}))
            self.assertEqual(fresh._tree.frozen_upto(), -1, bad)
            fresh.deleteLater()

    def test_the_last_fixed_column_can_be_the_end_of_a_freeze_but_a_step_column_has_no_such_entry(self) -> None:
        freeze = self.panel._freeze
        freeze.prepare(COLUMN_COUNT)  # the first step column (none exist here, but the header may be asked)
        self.assertFalse(freeze._freeze.isEnabled())
        freeze.prepare(COLUMN_RING_OUT)
        self.assertTrue(freeze._freeze.isEnabled())


if __name__ == "__main__":
    unittest.main()
