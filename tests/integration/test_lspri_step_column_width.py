"""The width of the ROI / Groups table's step columns (2026-11-06): by default each column is as wide as its title and the numbers in its cells need, and follows them.

Pinned: after a calculation every cell (ROI rows and group headers) and the title fit in their column, and the column is not much wider than that; the width follows the display
(a mean alone is narrower than mean ± SD), the title style, the numbers coming and going, and a change made while the panel was hidden; fitting is never mistaken for the user
dragging an edge (nothing is saved for it); dragging an edge gives all step columns that width and stops the fitting, remembered; a double-click on an edge or the header menu's
entry fits again; a comparison column keeps the room for its menu arrow; and what the user chose comes back at the next start (the old automatic width key is not a choice).
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QFont, QFontMetrics, QMouseEvent

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app.panels.roi_table import RoiTablePanel
    from lspr_imaging_app.panels.roi_table.rows import COLUMN_COUNT
    from lspr_imaging_app.panels.roi_table.step_columns import DISPLAY_BOTH, DISPLAY_MEAN, MIN_STEP_WIDTH, STEP_WIDTH_DEFAULT
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.plan.label_rule import MODE_SOLUTION, MODE_WHOLE
    from tests.integration.test_lspri_plan_bar import _pump
    from tests.integration.test_lspri_step_table import StepTableBase
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

KEY = "roi_table/step_width_manual"
HORIZONTAL = Qt.Orientation.Horizontal
SECTION_PADDING = 6  # the header's style sheet; `view.py` copies it
SORT_ROOM, ARROW_ZONE = 12, 20  # what a coloured step title keeps clear for the sort triangle; what a comparison column keeps for its menu arrow
SEPARATORS = ("_-_", "_--_")


class WidthBase(StepTableBase):
    def setUp(self) -> None:
        super().setUp()
        self.ui = UiStateStore({})
        self.view = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry, step_table=self.table)
        self.view.restore_ui_state(self.ui)
        self.group_id = self.m.roi_toolbox.create_group("Pair")
        self.m.roi_toolbox.add_rois_to_group([1], self.group_id)
        self.view.resize(1200, 420)
        self.view.show()
        self.view.refresh_now()
        self.controls = self.view._step_controls
        self.controls.columns_button.click()
        self.assertTrue(_pump(lambda: not self.table.is_running()))
        _APP.processEvents()
        self.model = self.view._model
        self.tree = self.view._tree

    def tearDown(self) -> None:
        self.view.hide()
        self.view.deleteLater()
        super().tearDown()

    def pick(self, mode: str) -> None:
        pick = self.controls.display_pick
        pick.setCurrentIndex(pick.findData(mode))

    def widths(self) -> list[int]:
        return [self.tree.columnWidth(c) for c in range(COLUMN_COUNT, self.model.columnCount())]

    def advance(self, text: str) -> int:
        return self.tree.fontMetrics().horizontalAdvance(text)

    def bold_advance(self, text: str) -> int:
        font = QFont(self.tree.header().font())
        font.setWeight(QFont.Weight.DemiBold)
        return QFontMetrics(font).horizontalAdvance(text)

    def shown(self, column: int) -> list[str]:
        """Everything the column shows, read the way the view reads it: the display text of every ROI row and group header."""
        texts = []
        for header in self.model.group_headers():
            texts.append(str(header.siblingAtColumn(column).data(Qt.ItemDataRole.DisplayRole) or ""))
            texts += [str(self.model.index(row, column, header).data(Qt.ItemDataRole.DisplayRole) or "") for row in range(self.model.rowCount(header))]
        return [t for t in texts if t]

    def title(self, column: int) -> str:
        return str(self.model.headerData(column, HORIZONTAL, Qt.ItemDataRole.DisplayRole))

    def double_click_edge(self, column: int) -> None:
        header = self.tree.header()
        x = header.sectionViewportPosition(column) + header.sectionSize(column) - 1
        point = QPointF(x, 6.0)
        button = Qt.MouseButton.LeftButton
        header.mouseDoubleClickEvent(QMouseEvent(QEvent.Type.MouseButtonDblClick, point, point, button, button, Qt.KeyboardModifier.NoModifier))


class FitToContentTest(WidthBase):
    def test_every_cell_and_the_title_fit_and_the_column_is_not_much_wider_than_that(self) -> None:
        for column in range(COLUMN_COUNT, self.model.columnCount()):
            width = self.tree.columnWidth(column)
            texts = self.shown(column)
            self.assertTrue(texts, "this test needs numbers to fit to")
            widest = max(self.advance(t) for t in texts)
            title_room = self.bold_advance(self.title(column)) + 2 * SECTION_PADDING + SORT_ROOM
            self.assertGreaterEqual(width - 14, widest, "a number does not fit (the cell keeps 6 px on the left and 8 on the right)")
            self.assertGreaterEqual(width, title_room, "the title does not fit")
            self.assertLessEqual(width, max(title_room, widest + 14 + 6), "the column is wider than its content needs")

    def test_the_cells_of_a_group_header_count_too(self) -> None:
        self.assertGreater(len(self.model.group_headers()), 0)
        self.assertEqual(len(self.model.step_texts(0)), len(self.shown(COLUMN_COUNT)))  # the ROI rows and the group headers, as the view shows them

    def test_a_mean_alone_is_narrower_than_mean_and_sd_and_it_comes_back(self) -> None:
        self.table.set_title_style(MODE_SOLUTION, SEPARATORS)  # short titles ("H2O"): the numbers decide the width
        _APP.processEvents()
        self.pick(DISPLAY_BOTH)
        both = self.widths()
        self.pick(DISPLAY_MEAN)
        mean = self.widths()
        self.assertTrue(all(m <= b for m, b in zip(mean, both)), (mean, both))  # (a long title can keep a column as wide in either display)
        self.assertTrue(any(m + 15 < b for m, b in zip(mean, both)), (mean, both))
        self.assertTrue(all(m >= MIN_STEP_WIDTH for m in mean))
        self.pick(DISPLAY_BOTH)
        self.assertEqual(self.widths(), both)

    def test_a_longer_title_makes_a_wider_column(self) -> None:
        self.table.set_title_style(MODE_SOLUTION, SEPARATORS)
        short = self.widths()
        self.table.set_title_style(MODE_WHOLE, SEPARATORS)
        long = self.widths()
        self.assertTrue(all(b >= a for a, b in zip(short, long)))
        self.assertTrue(any(b > a for a, b in zip(short, long)), (short, long))

    def test_a_column_with_no_numbers_yet_keeps_the_old_default_width(self) -> None:
        self.table.clear()  # another dataset: the numbers are dropped
        _APP.processEvents()
        self.assertTrue(all(w >= STEP_WIDTH_DEFAULT for w in self.widths()), self.widths())

    def test_a_change_made_while_the_panel_was_hidden_is_caught_up_when_it_is_shown(self) -> None:
        self.table.set_title_style(MODE_SOLUTION, SEPARATORS)
        self.pick(DISPLAY_BOTH)
        both = self.widths()
        self.view.set_panel_visible(False)
        self.pick(DISPLAY_MEAN)
        self.assertEqual(self.widths(), both, "a hidden panel must not be re-fitted")
        self.view.set_panel_visible(True)
        self.assertTrue(_pump(lambda: self.widths() != both), "the widths never caught up")
        self.assertTrue(all(m <= b for m, b in zip(self.widths(), both)))

    def test_fitting_is_never_taken_for_the_user_dragging_an_edge(self) -> None:
        self.pick(DISPLAY_MEAN)
        self.pick(DISPLAY_BOTH)
        self.table.set_title_style(MODE_SOLUTION, SEPARATORS)
        self.table.add_compare()
        _pump(lambda: False, 0.1)
        self.assertTrue(self.controls.is_fitting())
        self.assertIsNone(self.ui.get(KEY), "a fitted width was saved as if the user had chosen it")

    def test_the_sort_triangle_does_not_touch_the_title_of_the_narrowest_column(self) -> None:
        """A fitted column is only as wide as its title needs, so the title comes as close to the left end as it ever will: the triangle drawn there must still clear it."""
        header = self.tree.header()
        for column in range(COLUMN_COUNT, self.model.columnCount()):
            header.setSortIndicator(column, Qt.SortOrder.AscendingOrder)
            _APP.processEvents()
            image = header.viewport().grab().toImage()
            left, width = header.sectionViewportPosition(column), header.sectionSize(column)
            fill = image.pixelColor(left + 1, 2)
            ink = [
                x for y in range(header.viewport().height() - 2) for x in range(width)
                if max(abs(a - b) for a, b in zip(image.pixelColor(left + x, y).getRgb()[:3], fill.getRgb()[:3])) > 40
            ]
            triangle = [x for x in ink if x < 15]
            title = [x for x in ink if x >= 15]
            self.assertTrue(triangle and title, (column, width))
            self.assertGreaterEqual(min(title) - max(triangle), 3, f"column {column}: the title touches the sort triangle")

    def test_a_comparison_column_keeps_the_room_for_its_menu_arrow(self) -> None:
        self.table.add_compare()
        _pump(lambda: False, 0.1)
        column = self.model.columnCount() - 1
        self.assertGreaterEqual(self.tree.columnWidth(column), self.bold_advance(self.title(column)) + 2 * SECTION_PADDING + ARROW_ZONE)


class ManualWidthTest(WidthBase):
    def test_dragging_an_edge_gives_every_step_column_that_width_stops_the_fitting_and_is_remembered(self) -> None:
        self.tree.setColumnWidth(COLUMN_COUNT, 200)  # (what dragging does)
        self.assertEqual(self.widths(), [200] * (self.model.columnCount() - COLUMN_COUNT))
        self.assertFalse(self.controls.is_fitting())
        self.assertEqual(self.ui.get(KEY), 200)
        self.pick(DISPLAY_MEAN)  # the content changes, the width the user chose does not
        self.assertEqual(self.widths(), [200] * (self.model.columnCount() - COLUMN_COUNT))

    def test_a_double_click_on_an_edge_fits_again_and_forgets_the_manual_width(self) -> None:
        fitted = self.widths()
        self.tree.setColumnWidth(COLUMN_COUNT, 200)
        self.double_click_edge(COLUMN_COUNT)
        self.assertTrue(self.controls.is_fitting())
        self.assertEqual(self.widths(), fitted)
        self.assertIsNone(self.ui.get(KEY), "the width Qt gives the one column on a double-click was saved as a choice")

    def test_a_double_click_on_a_fixed_columns_edge_does_not_touch_the_step_columns(self) -> None:
        self.tree.setColumnWidth(COLUMN_COUNT, 200)
        self.double_click_edge(0)
        self.assertFalse(self.controls.is_fitting())
        self.assertEqual(self.tree.columnWidth(COLUMN_COUNT + 1), 200)

    def test_the_header_menu_offers_to_fit_only_while_there_is_a_manual_width(self) -> None:
        action = self.controls._fit_action
        self.assertIsNotNone(action)
        self.controls.prepare_menu()
        self.assertFalse(action.isVisible())
        self.tree.setColumnWidth(COLUMN_COUNT, 200)
        self.controls.prepare_menu()
        self.assertTrue(action.isVisible())
        action.trigger()
        self.assertTrue(self.controls.is_fitting())
        self.controls.prepare_menu()
        self.assertFalse(action.isVisible())

    def test_the_right_click_on_a_header_prepares_the_entry(self) -> None:
        self.tree.setColumnWidth(COLUMN_COUNT, 200)
        with mock.patch.object(type(self.view._view_menu), "exec", return_value=None):
            self.view._show_header_menu(self.tree.header().rect().center())
        self.assertTrue(self.controls._fit_action.isVisible())

    def test_the_manual_width_is_restored_and_without_one_the_columns_fit(self) -> None:
        self.tree.setColumnWidth(COLUMN_COUNT, 140)
        again = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry, step_table=self.table)
        again.restore_ui_state(self.ui)
        self.assertFalse(again._step_controls.is_fitting())
        self.assertEqual(again._tree.columnWidth(COLUMN_COUNT), 140)
        again.deleteLater()
        self.double_click_edge(COLUMN_COUNT)  # fit again: nothing is remembered
        fresh = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry, step_table=self.table)
        fresh.restore_ui_state(self.ui)
        self.assertTrue(fresh._step_controls.is_fitting())
        fresh.deleteLater()

    def test_the_old_automatic_width_is_not_taken_for_a_choice(self) -> None:
        old = UiStateStore({"roi_table/steps_shown": True, "roi_table/step_width": 140})
        panel = RoiTablePanel(self.m.roi_toolbox, self.m.selection, self.m.geometry, step_table=self.table)
        panel.restore_ui_state(old)
        self.assertTrue(panel._step_controls.is_fitting())
        panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
