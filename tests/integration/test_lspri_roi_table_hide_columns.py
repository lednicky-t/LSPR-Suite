"""Hiding and showing columns of the ROI / Groups table (2026-11-06): right-click a column header -> "Hide column", "Show hidden column" (a list), "Show all hidden columns".

Pinned: hiding takes out that column only and the first column cannot be hidden; the entries follow the state (the title of the column under the pointer, the list of hidden
columns, greyed when nothing is hidden); a view mode never brings a hidden column back, and a hidden column the mode does not show is not offered; a hidden column keeps its width;
a table sorted by a hidden column goes back to the # order; the hidden columns are remembered and come back; a freeze that ends at a hidden column holds the visible ones before it.
For the step columns: a hidden step stays hidden when the columns are rebuilt, refitted or renumbered (it is named by what it is), the fitting never shows it, the numbers of
a group header are not drawn for it, it comes back through the menu and after a restart; and a comparison column is hidden by its pair and calculation.
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app.image_tools import GeometryModule
    from lspr_imaging_app.panels.roi_table import RoiTablePanel
    from lspr_imaging_app.panels.roi_table.delegate import RoiTableDelegate
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
        return {c for c in range(COLUMN_COUNT) if self.tree.isColumnHidden(c)}

    def shown_list(self) -> list[str]:
        return menu_texts(self.hide._show_menu)


class HidingTest(FixedColumnsBase):
    def test_hiding_a_column_takes_out_that_column_only(self) -> None:
        before = self.hidden()
        self.hide._hide_section(COLUMN_X)
        self.assertEqual(self.hidden(), before | {COLUMN_X})

    def test_the_first_column_cannot_be_hidden(self) -> None:
        self.hide.prepare(COLUMN_ID)
        self.assertFalse(self.hide._hide.isEnabled())
        self.assertIn("always stays", self.hide._hide.toolTip())
        before = self.hidden()
        self.hide._hide_section(COLUMN_ID)
        self.assertEqual(self.hidden(), before)
        self.assertEqual(self.ui.get(KEY) or [], [])

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

    def test_what_the_state_holds_that_cannot_be_hidden_or_read_is_ignored(self) -> None:
        junk = UiStateStore({KEY: [COLUMN_ID, 99, "x", None, True, {"junk": 1}, COLUMN_Y]})
        panel = RoiTablePanel(self.toolbox, SelectionModule(), GeometryModule())
        panel.restore_ui_state(junk)
        self.assertEqual(panel._tree.hidden_keys(), frozenset({COLUMN_Y}))
        panel.deleteLater()


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


class HiddenComparisonColumnsTest(StepColumnsBase):
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
