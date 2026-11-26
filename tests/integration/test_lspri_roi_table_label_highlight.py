"""ROI / Groups table (2026-11-26): "Highlight selected rows in the Label column only" (right-click menu) and the group header painting only the step columns on screen.

Pinned: with the choice off a selected row is tinted in every column (as before), with it on only in the Label column (and in the whole row again if Label is hidden); the
choice is in the right-click menu, remembered, and restored; a group header asks for the step columns inside the viewport only (241 of them in every header on every paint made
a paint 1-2 s, `roi_table.slow_paint` in the log of 2026-11-26).
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QColor, QStandardItemModel
from PyQt6.QtWidgets import QTreeView

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_ui import get_active_theme
    from lspr_imaging_app.image_tools import GeometryModule
    from lspr_imaging_app.panels.roi_table import RoiTablePanel
    from lspr_imaging_app.panels.roi_table import view as view_module
    from lspr_imaging_app.panels.roi_table.delegate import RoiTableDelegate
    from lspr_imaging_app.panels.roi_table.rows import COLUMN_COUNT, COLUMN_ID, COLUMN_NAME, COLUMN_X, VIEW_SIMPLE
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.storage.ui_state_keys import ROI_TABLE_LABEL_ONLY_HIGHLIGHT
    from lspr_imaging_app.undo import undo_manager
    from tests._qt_fonts import load_system_fonts
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

load_system_fonts()

MENU_TEXT = "Highlight only label"


class LabelHighlightTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.geometry = GeometryModule()
        for x in (10.0, 20.0, 30.0):
            self.toolbox.add_roi(x, 5.0, sample_diameter_px=6.0)
        self.ui = UiStateStore({})
        self.panel = RoiTablePanel(self.toolbox, self.selection, self.geometry)
        self.panel.restore_ui_state(self.ui)
        self.tree = self.panel._tree
        self.model = self.panel._model
        self.panel.resize(700, 340)
        self.panel.show()
        self.panel.refresh_now()
        _APP.processEvents()

    def tearDown(self) -> None:
        self.panel.hide()
        self.panel.deleteLater()
        undo_manager.clear()

    def corner_color(self, roi_id: int, column: int):
        """The colour of the top-left pixel of a cell (no text or border there), read from what the table really draws."""
        image = self.tree.viewport().grab().toImage()
        rect = self.tree.visualRect(self.model.roi_index(roi_id, column))
        return image.pixelColor(rect.left() + 1, rect.top() + 1).getRgb()

    def select(self, roi_id: int) -> None:
        self.selection.set_roi_selection({roi_id})
        self.panel.refresh_now()
        _APP.processEvents()

    def test_by_default_every_column_of_a_selected_row_is_tinted(self) -> None:
        self.select(1)
        self.assertNotEqual(self.corner_color(1, COLUMN_X), self.corner_color(2, COLUMN_X))
        self.assertNotEqual(self.corner_color(1, COLUMN_ID), self.corner_color(2, COLUMN_ID))

    def test_with_the_choice_on_geometry_view_tints_number_and_label_but_nothing_else(self) -> None:
        self.tree.set_label_only_highlight(True)
        self.select(1)  # (Geometry view, the default: it shows Label too since 2026-11-26)
        self.assertGreater(self.tree.columnWidth(COLUMN_NAME), 0)
        self.assertNotEqual(self.corner_color(1, COLUMN_ID), self.corner_color(2, COLUMN_ID))
        self.assertNotEqual(self.corner_color(1, COLUMN_NAME), self.corner_color(2, COLUMN_NAME))
        self.assertEqual(self.corner_color(1, COLUMN_X), self.corner_color(2, COLUMN_X))

    def test_with_the_choice_on_simple_view_tints_number_and_label_but_nothing_else(self) -> None:
        self.panel._apply_view_mode(VIEW_SIMPLE)
        self.panel.refresh_now()
        self.tree.set_label_only_highlight(True)
        self.select(1)
        self.assertGreater(self.tree.columnWidth(COLUMN_NAME), 0)
        self.assertNotEqual(self.corner_color(1, COLUMN_ID), self.corner_color(2, COLUMN_ID))
        self.assertNotEqual(self.corner_color(1, COLUMN_NAME), self.corner_color(2, COLUMN_NAME))

    def test_with_the_label_cells_hidden_the_row_is_tinted_whole_so_a_selection_still_shows(self) -> None:
        self.tree.set_label_only_highlight(True)
        self.panel._hide._hide_section(COLUMN_ID)  # (the header menu's "Hide column": #/ROIs collapses to nothing)
        self.panel._hide._hide_section(COLUMN_NAME)
        self.assertEqual(self.tree.columnWidth(COLUMN_ID), 0)
        self.select(1)
        self.assertNotEqual(self.corner_color(1, COLUMN_X), self.corner_color(2, COLUMN_X))

    def test_the_right_click_menu_has_the_choice_and_it_is_remembered(self) -> None:
        captured: list = []

        def grab_menu(menu, *_args):
            captured.extend(a for a in menu.actions() if a.text() == MENU_TEXT)

        with mock.patch("PyQt6.QtWidgets.QMenu.exec", grab_menu):
            self.panel._show_context_menu(QPoint(2, 300))  # empty space under the rows
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0].isCheckable())
        self.assertFalse(captured[0].isChecked())
        self.panel._set_label_only_highlight(True)
        self.assertTrue(self.tree.label_only_highlight())
        self.assertIs(self.ui.get(ROI_TABLE_LABEL_ONLY_HIGHLIGHT), True)
        again = RoiTablePanel(self.toolbox, self.selection, self.geometry)
        again.restore_ui_state(self.ui)
        self.assertTrue(again._tree.label_only_highlight())
        again.deleteLater()


    # The ROI selection lock (one flag in the SelectionModule, also the ROIs tab's button): the table's own button follows it, and while it is on a selected row has a frame, no tint.

    def test_the_toolbar_button_and_the_module_flag_follow_each_other(self) -> None:
        button = self.panel._lock_toggle
        self.assertFalse(button.isChecked())
        button.click()
        self.assertTrue(self.selection.roi_selection_locked())
        self.assertTrue(self.tree.selection_locked())
        self.selection.set_roi_selection_locked(False)  # (the ROIs tab, the menus, a loaded session)
        self.assertFalse(button.isChecked())
        self.assertFalse(self.tree.selection_locked())
        self.selection.set_roi_selection_locked(True)
        self.assertTrue(button.isChecked())
        button.click()
        self.assertFalse(self.selection.roi_selection_locked())

    def edge_color(self, roi_id: int, column: int):
        """The colour of the pixel on a cell's top edge (where a row's frame is drawn), read from what the table really draws."""
        image = self.tree.viewport().grab().toImage()
        rect = self.tree.visualRect(self.model.roi_index(roi_id, column))
        return image.pixelColor(rect.left() + 1, rect.top()).getRgb()

    def test_locked_the_selected_row_is_framed_not_tinted(self) -> None:
        self.select(1)
        self.assertNotEqual(self.corner_color(1, COLUMN_X), self.corner_color(2, COLUMN_X))  # (unlocked: a tint across the row)
        self.selection.set_roi_selection_locked(True)
        _APP.processEvents()
        self.assertEqual(self.corner_color(1, COLUMN_X), self.corner_color(2, COLUMN_X))  # no tint inside any more
        frame = QColor(get_active_theme().highlight_color).getRgb()
        self.assertEqual(self.edge_color(1, COLUMN_X), frame)  # the frame's top edge, on the selected row only
        self.assertNotEqual(self.edge_color(2, COLUMN_X), frame)

    def test_locked_the_frame_is_round_the_label_cells_when_only_the_label_is_highlighted(self) -> None:
        self.tree.set_label_only_highlight(True)
        self.select(1)
        self.selection.set_roi_selection_locked(True)
        _APP.processEvents()
        frame = QColor(get_active_theme().highlight_color).getRgb()
        self.assertEqual(self.edge_color(1, COLUMN_ID), frame)
        self.assertNotEqual(self.edge_color(1, COLUMN_X), frame)  # not across the number columns


class LabelFocusFrameTest(unittest.TestCase):
    """With only the label highlighted, the current row's focus frame is kept, round the label cells only (Qt's own spans the whole row and is switched off in that mode)."""

    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.geometry = GeometryModule()
        for x in (10.0, 20.0, 30.0):
            self.toolbox.add_roi(x, 5.0, sample_diameter_px=6.0)
        self.toolbox.add_rois_to_group([1], self.toolbox.create_group("Alpha"))
        self.panel = RoiTablePanel(self.toolbox, self.selection, self.geometry)
        self.panel.restore_ui_state(UiStateStore({}))
        self.tree, self.model = self.panel._tree, self.panel._model
        self.panel.resize(700, 340)
        self.panel.show()
        self.panel.refresh_now()
        self.tree.set_label_only_highlight(True)
        _APP.processEvents()

    def tearDown(self) -> None:
        self.panel.hide()
        self.panel.deleteLater()
        undo_manager.clear()

    def row_rect(self, roi_id: int):
        return self.tree.visualRect(self.model.roi_index(roi_id, COLUMN_ID))

    def frames_drawn(self, current_roi_id: int | None = None, *, focus: bool = True, header: bool = False) -> list:
        """The rectangles the focus frame is drawn at in one paint of the table (QApplication's style is a recorder for the moment)."""
        if header:
            current = self.model.group_index("group_1")
        else:
            current = self.model.roi_index(current_roi_id, COLUMN_ID)
        self.tree.setCurrentIndex(current)
        drawn: list = []
        recorder = mock.MagicMock()
        recorder.style.return_value.drawPrimitive.side_effect = lambda _primitive, option, *_a: drawn.append(option.rect)
        with mock.patch.object(view_module, "QApplication", recorder), mock.patch.object(type(self.tree), "hasFocus", lambda _self: focus):
            self.tree.viewport().grab()
        return drawn

    def test_in_geometry_view_the_frame_is_round_number_and_label_of_the_current_row_only(self) -> None:
        drawn = self.frames_drawn(2)
        self.assertEqual(len(drawn), 1, drawn)
        number, label = (self.tree.visualRect(self.model.roi_index(2, c)) for c in (COLUMN_ID, COLUMN_NAME))
        self.assertEqual((drawn[0].left(), drawn[0].right()), (number.left(), label.right()))
        self.assertLess(drawn[0].width(), self.tree.header().length() // 2)  # not the row

    def test_in_simple_view_it_is_round_number_and_label_together(self) -> None:
        self.panel._apply_view_mode(VIEW_SIMPLE)
        self.panel.refresh_now()
        drawn = self.frames_drawn(2)
        self.assertEqual(len(drawn), 1)
        number, label = (self.tree.visualRect(self.model.roi_index(2, c)) for c in (COLUMN_ID, COLUMN_NAME))
        self.assertEqual((drawn[0].left(), drawn[0].right()), (number.left(), label.right()))

    def test_a_group_header_has_it_round_its_name_stretch_not_across_the_step_numbers(self) -> None:
        drawn = self.frames_drawn(header=True)
        self.assertEqual(len(drawn), 1)
        self.assertGreater(drawn[0].width(), 0)
        self.assertLessEqual(drawn[0].right(), self.tree.viewport().width())

    def test_without_focus_or_with_the_choice_off_nothing_is_drawn_by_us(self) -> None:
        self.assertEqual(self.frames_drawn(2, focus=False), [])
        self.tree.set_label_only_highlight(False)
        self.assertEqual(self.frames_drawn(2), [])  # (Qt draws its own, across the row, then)

    def test_with_no_label_cell_showing_the_frame_is_the_whole_row(self) -> None:
        self.panel._hide._hide_section(COLUMN_ID)
        self.panel._hide._hide_section(COLUMN_NAME)
        self.assertEqual(self.tree.columnWidth(COLUMN_ID), 0)
        drawn = self.frames_drawn(2)
        self.assertEqual(len(drawn), 1)
        self.assertEqual(drawn[0].width(), self.tree.header().length())


class StepColumnsOnScreenTest(unittest.TestCase):
    def test_a_group_header_asks_for_the_step_columns_in_the_viewport_only(self) -> None:
        steps = 240
        model = QStandardItemModel(1, COLUMN_COUNT + steps)
        tree = QTreeView()
        tree.setModel(model)
        for column in range(COLUMN_COUNT + steps):
            tree.setColumnWidth(column, 60)
        tree.resize(500, 200)
        tree.show()
        _APP.processEvents()
        shown = RoiTableDelegate._step_columns_on_screen(tree)
        self.assertTrue(shown == [] or shown[0] == COLUMN_COUNT)  # (12 fixed columns of 60 px may already fill the viewport)
        tree.horizontalScrollBar().setValue(60 * (COLUMN_COUNT + 100))
        _APP.processEvents()
        shown = RoiTableDelegate._step_columns_on_screen(tree)
        self.assertTrue(0 < len(shown) <= 10, shown)
        self.assertGreater(shown[0], COLUMN_COUNT + 50)  # the columns that scrolled into view, not the first ones
        tree.setColumnHidden(shown[1], True)
        self.assertNotIn(shown[1], RoiTableDelegate._step_columns_on_screen(tree))
        tree.hide()
        tree.deleteLater()


if __name__ == "__main__":
    unittest.main()
