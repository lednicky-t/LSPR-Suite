"""The ROI/Group table panel, driven through its model, its selection and its
command methods (modal dialogs patched). Offscreen: nothing here judges how
the table *looks*; a paint smoke test only proves painting does not raise.

What it pins: ROIs appear (grouped or flat), selection stays in step with the
shared `SelectionModule` in both directions, a cell edit becomes a toolbox
command (multi-selection edits apply to all selected ROIs in one undo step,
bad values are refused and shown), reordering is only offered where it is
well defined and never while an analysis runs, and the remembered state
(flat/sort/collapsed/widths) is saved and restored.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch.**
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog, QInputDialog, QMenu

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_ui import get_active_theme, set_active_theme

    from lspr_imaging_app.analysis.engine import AnalysisEngine
    from lspr_imaging_app.gui.app_theme import LSPRI_BRIGHT_THEME
    from lspr_imaging_app.image_tools import GeometryModule
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.panels.roi_table import RoiTablePanel
    from lspr_imaging_app.panels.roi_table.dialogs import ShiftDialog
    from lspr_imaging_app.panels.roi_table.model import (
        ALL_SELECTED_ROLE,
        COLOR_ROLE,
        INHERITED_ROLE,
        KIND_ROLE,
    )
    from lspr_imaging_app.panels.roi_table.rows import (
        COLUMN_ID,
        COLUMN_NAME,
        COLUMN_RING_IN,
        COLUMN_RING_OUT,
        COLUMN_SAMPLE,
        COLUMN_X,
        COLUMN_Y,
    )
    from lspr_imaging_app.panels.roi_table.view import suggested_column_widths
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

_SELECT_ROWS = QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows


class _PanelCase(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._theme = get_active_theme()
        self.toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.geometry = GeometryModule()
        self.engine = AnalysisEngine()
        self.toolbox.roi_ids_renumbered.connect(self.selection.remap_roi_ids)  # as the app wires it
        for x in (10.0, 20.0, 30.0, 40.0, 50.0):
            self.toolbox.add_roi(x, 5.0, sample_diameter_px=6.0)
        self.panel = RoiTablePanel(self.toolbox, self.selection, self.geometry, self.engine)
        self.messages: list[str] = []
        self.panel.status_message.connect(self.messages.append)
        self.model = self.panel._model
        self.tree = self.panel._tree

    def tearDown(self) -> None:
        set_active_theme(self._theme)
        undo_manager.clear()
        self.panel.deleteLater()

    # -- helpers --------------------------------------------------------------------
    def refresh(self) -> None:
        self.panel.refresh_now()

    def cell(self, roi_id: int, column: int):
        index = self.model.roi_index(roi_id, column)
        self.assertTrue(index.isValid(), f"ROI {roi_id} is not shown")
        return index

    def text(self, roi_id: int, column: int) -> str:
        return self.model.data(self.cell(roi_id, column), Qt.ItemDataRole.DisplayRole)

    def edit(self, roi_id: int, column: int, text: str) -> None:
        self.model.setData(self.cell(roi_id, column), text, Qt.ItemDataRole.EditRole)

    def shown_ids(self) -> list[int]:
        """ROI ids in the order the table shows them (top to bottom)."""
        ids: list[int] = []
        for row in range(self.model.rowCount()):
            top = self.model.index(row, 0)
            if self.model.is_group(top):
                ids.extend(self.model.roi_id(self.model.index(r, 0, top)) for r in range(self.model.rowCount(top)))
            else:
                ids.append(self.model.roi_id(top))
        return ids

    def xs(self) -> list[float]:
        return [roi.center_x for roi in sorted(self.toolbox.rois(), key=lambda r: r.area_roi_id)]

    def select_in_view(self, *indexes) -> None:
        from PyQt6.QtCore import QItemSelection

        selection = QItemSelection()
        for index in indexes:
            selection.select(index, index)
        self.tree.selectionModel().select(selection, _SELECT_ROWS)

    def header(self, group_id: str):
        index = self.model.group_index(group_id)
        self.assertTrue(index.isValid())
        return index

    def footer(self) -> str:
        return self.panel._footer.text()


class AppearanceTest(_PanelCase):
    def test_no_rois_shows_the_hint_and_adding_one_shows_the_table(self) -> None:
        empty = RoiTablePanel(RoiToolbox(), SelectionModule(), self.geometry, self.engine)
        self.assertEqual(empty._stack.currentIndex(), 1)
        self.assertEqual(empty._footer.text(), "No ROIs")
        self.assertEqual(self.panel._stack.currentIndex(), 0)
        self.assertEqual(self.footer(), "5 ROIs")

    def test_a_change_refreshes_the_table_after_a_short_pause(self) -> None:
        self.toolbox.add_roi(60.0, 5.0)
        self.assertTrue(self.panel._redraw_timer.isActive())
        self.assertEqual(len(self.model.rows()), 5, "not rebuilt yet")
        QTest.qWait(250)
        self.assertEqual(len(self.model.rows()), 6)

    def test_flat_until_there_are_groups_then_headers_then_flat_on_request(self) -> None:
        self.assertFalse(self.model.is_grouped_view())
        self.assertEqual(self.shown_ids(), [1, 2, 3, 4, 5])
        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        self.assertTrue(self.model.is_grouped_view())
        self.assertEqual([self.model.data(h) for h in self.model.group_headers()], ["A", "Ungrouped"])
        self.assertEqual(self.shown_ids(), [1, 2, 3, 4, 5])
        self.panel._flat_button.setChecked(True)
        self.assertFalse(self.model.is_grouped_view())
        self.assertEqual(self.model.group_headers(), [])

    def test_cells_show_the_values_and_mark_inherited_rings(self) -> None:
        self.assertEqual(self.text(1, COLUMN_ID), "1")
        self.assertEqual(self.text(2, COLUMN_X), "20.0")
        self.assertEqual(self.text(2, COLUMN_Y), "5.0")
        self.assertEqual(self.text(2, COLUMN_SAMPLE), "6.0")
        self.assertEqual(self.text(2, COLUMN_RING_IN), "28.0", "the shared default")
        self.assertTrue(self.model.data(self.cell(2, COLUMN_RING_IN), INHERITED_ROLE))
        self.assertFalse(self.model.data(self.cell(2, COLUMN_SAMPLE), INHERITED_ROLE))
        self.toolbox.resize_rois((2,), reference_inner_diameter_px=10.0)
        self.refresh()
        self.assertEqual(self.text(2, COLUMN_RING_IN), "10.0")
        self.assertFalse(self.model.data(self.cell(2, COLUMN_RING_IN), INHERITED_ROLE))
        self.assertTrue(self.model.data(self.cell(2, COLUMN_RING_OUT), INHERITED_ROLE))

    def test_groups_and_tints_show_in_the_model(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        header = self.header(self.toolbox.groups()[0].group_id)
        self.assertEqual(self.model.data(header, KIND_ROLE), "group")
        self.assertEqual(self.model.data(header, COLOR_ROLE), self.toolbox.groups()[0].sample_color_hex)
        self.assertEqual(self.model.data(self.cell(1, COLUMN_ID), COLOR_ROLE), self.toolbox.roi_by_id(1).sample_color_hex)
        self.assertNotEqual(
            self.model.data(self.cell(1, COLUMN_ID), COLOR_ROLE), self.model.data(self.cell(2, COLUMN_ID), COLOR_ROLE)
        )

    def test_micrometers_follow_the_geometry_display_unit(self) -> None:
        self.geometry.restore_settings(
            GeometrySettings(calibration_enabled=True, microns_per_pixel_x=0.5, microns_per_pixel_y=0.5, display_units="um")
        )
        self.refresh()
        self.assertEqual(self.panel._unit_label.text(), "µm")
        self.assertEqual(self.text(2, COLUMN_X), "10.0")  # 20 px * 0.5
        self.edit(2, COLUMN_SAMPLE, "10")  # typed in µm
        self.assertEqual(self.toolbox.roi_by_id(2).sample_diameter_px, 20.0)

    def test_the_footer_counts_rois_and_the_selection(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.assertEqual(self.footer(), "5 ROIs  ·  2 selected")


class SelectionSyncTest(_PanelCase):
    def test_the_shared_selection_selects_rows(self) -> None:
        self.selection.set_roi_selection({2, 4})
        rois, headers = self.panel._view_selection()
        self.assertEqual(sorted(rois), [2, 4])
        self.assertEqual(headers, [])

    def test_selecting_rows_selects_the_same_rois_everywhere_once(self) -> None:
        emitted: list[set] = []
        self.selection.roi_selection_changed.connect(emitted.append)
        self.select_in_view(self.cell(3, 0))
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({3}))
        self.assertEqual(len(emitted), 1, "no echo back into the view")

    def test_a_group_header_selects_its_members(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        self.select_in_view(self.header("group_1"))
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({1, 2}))

    def test_a_header_lights_up_when_all_its_members_are_selected(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        header = self.header("group_1")
        self.selection.set_roi_selection({1})
        self.assertFalse(self.model.data(header, ALL_SELECTED_ROLE))
        self.selection.set_roi_selection({1, 2})
        self.assertTrue(self.model.data(header, ALL_SELECTED_ROLE))

    def test_selecting_into_a_collapsed_group_opens_it(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        header = self.header("group_1")
        self.tree.collapse(header)
        self.assertFalse(self.tree.isExpanded(header))
        self.selection.set_roi_selection({1})
        self.assertTrue(self.tree.isExpanded(header))

    def test_selection_survives_a_rebuild(self) -> None:
        self.selection.set_roi_selection({2, 3})
        self.toolbox.add_roi(60.0, 5.0)
        self.refresh()
        self.assertEqual(sorted(self.panel._view_selection()[0]), [2, 3])


class EditingTest(_PanelCase):
    def test_editing_a_diameter_is_a_toolbox_command_and_undoes(self) -> None:
        self.edit(2, COLUMN_SAMPLE, "9")
        self.assertEqual(self.toolbox.roi_by_id(2).sample_diameter_px, 9.0)
        self.assertEqual(self.toolbox.roi_by_id(3).sample_diameter_px, 6.0)
        undo_manager.undo()
        self.assertEqual(self.toolbox.roi_by_id(2).sample_diameter_px, 6.0)

    def test_an_edit_applies_to_the_whole_selection_in_one_undo_step(self) -> None:
        self.selection.set_roi_selection({1, 2, 3})
        self.edit(2, COLUMN_SAMPLE, "9")
        self.assertEqual([self.toolbox.roi_by_id(i).sample_diameter_px for i in (1, 2, 3, 4)], [9.0, 9.0, 9.0, 6.0])
        self.assertIn("3 ROIs", self.footer())
        undo_manager.undo()
        self.assertEqual([self.toolbox.roi_by_id(i).sample_diameter_px for i in (1, 2, 3)], [6.0, 6.0, 6.0])

    def test_editing_a_roi_outside_the_selection_edits_only_that_roi(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.edit(4, COLUMN_SAMPLE, "9")
        self.assertEqual([self.toolbox.roi_by_id(i).sample_diameter_px for i in (1, 2, 4)], [6.0, 6.0, 9.0])

    def test_editing_x_of_a_selection_lines_them_up(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.edit(1, COLUMN_X, "0")
        self.assertEqual(self.xs()[:3], [0.0, 0.0, 30.0])
        self.assertEqual([roi.center_y for roi in self.toolbox.rois()][:3], [5.0, 5.0, 5.0], "y untouched")
        self.assertIn("Set x of 2 ROIs", self.messages[-1])

    def test_a_bad_value_is_refused_shown_and_changes_nothing(self) -> None:
        self.edit(2, COLUMN_SAMPLE, "1")
        self.assertEqual(self.toolbox.roi_by_id(2).sample_diameter_px, 6.0)
        self.assertIn("at least 2", self.footer())
        self.assertIn("at least 2", self.messages[-1])
        self.edit(2, COLUMN_SAMPLE, "abc")
        self.assertIn("'abc' is not a number", self.footer())
        self.edit(2, COLUMN_RING_IN, "40")  # not below the inherited outer diameter (36)
        self.assertIn("inner < outer", self.footer())
        self.assertIsNone(self.toolbox.roi_by_id(2).reference_inner_diameter_px)

    def test_a_ring_override_is_stored_for_that_roi(self) -> None:
        self.edit(2, COLUMN_RING_IN, "10")
        self.assertEqual(self.toolbox.roi_by_id(2).reference_inner_diameter_px, 10.0)
        self.assertIsNone(self.toolbox.roi_by_id(2).reference_outer_diameter_px)

    def test_names_and_group_names(self) -> None:
        self.edit(1, COLUMN_NAME, "  spot A ")
        self.assertEqual(self.toolbox.roi_by_id(1).label, "spot A")
        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        header = self.header("group_1")
        self.model.setData(header, "Renamed", Qt.ItemDataRole.EditRole)
        self.assertEqual(self.toolbox.groups()[0].name, "Renamed")
        self.model.setData(header, "   ", Qt.ItemDataRole.EditRole)
        self.assertEqual(self.toolbox.groups()[0].name, "Renamed")
        self.assertIn("needs a name", self.footer())

    def test_committing_an_unchanged_cell_does_nothing(self) -> None:
        edits: list[tuple] = []
        self.model.cell_edited.connect(lambda *args: edits.append(args))
        last_step = undo_manager.undo_label
        index = self.cell(2, COLUMN_SAMPLE)
        self.model.setData(index, self.model.data(index, Qt.ItemDataRole.EditRole), Qt.ItemDataRole.EditRole)
        self.assertEqual(edits, [])
        self.assertEqual(undo_manager.undo_label, last_step)

    def test_the_model_never_changes_a_value_itself(self) -> None:
        self.assertFalse(self.model.setData(self.cell(2, COLUMN_SAMPLE), "50", Qt.ItemDataRole.EditRole))
        self.assertEqual(self.text(2, COLUMN_SAMPLE), "6.0")


class SortAndReorderTest(_PanelCase):
    def test_clicking_a_header_sorts_ascending_then_descending(self) -> None:
        self.panel._on_header_clicked(COLUMN_X)
        self.assertEqual(self.model.sort_state(), (COLUMN_X, False))
        self.toolbox.move_roi(1, 99.0, 5.0)
        self.refresh()
        self.assertEqual(self.shown_ids(), [2, 3, 4, 5, 1])
        self.panel._on_header_clicked(COLUMN_X)
        self.assertEqual(self.model.sort_state(), (COLUMN_X, True))
        self.assertEqual(self.shown_ids(), [1, 5, 4, 3, 2])
        self.panel._on_header_clicked(COLUMN_ID)
        self.assertEqual(self.model.sort_state(), (COLUMN_ID, False))

    def test_sorting_changes_the_view_only(self) -> None:
        before = self.xs()
        self.panel._on_header_clicked(COLUMN_X)
        self.panel._on_header_clicked(COLUMN_X)
        self.assertEqual(self.xs(), before)

    def test_reordering_is_only_offered_when_sorted_by_number(self) -> None:
        self.selection.set_roi_selection({3})
        self.assertTrue(self.panel._up_button.isEnabled())
        self.panel._on_header_clicked(COLUMN_X)
        self.assertFalse(self.panel._up_button.isEnabled())
        self.assertIn("Sort by #", self.panel._up_button.toolTip())
        self.panel._move_selected(-1)
        self.assertIn("Sort by #", self.footer())
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0])

    def test_move_up_renumbers_and_the_selection_follows(self) -> None:
        self.selection.set_roi_selection({3})
        self.panel._move_selected(-1)
        self.assertEqual(self.xs(), [10.0, 30.0, 20.0, 40.0, 50.0])
        self.assertEqual(self.selection.selected_roi_ids(), frozenset({2}))
        self.refresh()
        self.assertEqual(self.panel._view_selection()[0], [2])
        self.panel._move_selected(1)
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0])

    def test_move_to_the_edges(self) -> None:
        self.selection.set_roi_selection({4, 5})
        self.panel._move_to_edge(top=True)
        self.assertEqual(self.xs(), [40.0, 50.0, 10.0, 20.0, 30.0])
        self.panel._move_to_edge(top=False)
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0])

    def test_in_the_grouped_view_a_move_stays_inside_the_group(self) -> None:
        self.toolbox.group_rois((1, 3, 5), "odd")
        self.refresh()
        self.selection.set_roi_selection({5})
        self.panel._move_selected(-1)  # within [1, 3, 5]: ROI 5 swaps with ROI 3
        self.assertEqual(self.xs(), [10.0, 20.0, 50.0, 40.0, 30.0])
        self.assertEqual(self.toolbox.groups()[0].area_roi_ids, [1, 3, 5])

    def test_rois_from_different_groups_cannot_be_reordered_together(self) -> None:
        self.toolbox.group_rois((1,), "A")
        self.toolbox.group_rois((2,), "B")
        self.refresh()
        self.selection.set_roi_selection({1, 2})
        self.panel._move_selected(1)
        self.assertIn("single group", self.footer())
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0])

    def test_nothing_is_reordered_or_deleted_while_an_analysis_runs(self) -> None:
        self.selection.set_roi_selection({3})
        with mock.patch.object(self.engine, "is_running", return_value=True):
            self.panel._move_selected(-1)
            self.assertIn("analysis to finish", self.footer())
            self.panel._delete_selected()
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0])
        self.assertEqual(len(self.toolbox.rois()), 5)


class DeleteTest(_PanelCase):
    def test_delete_removes_the_selected_rois(self) -> None:
        self.select_in_view(self.cell(2, 0), self.cell(3, 0))
        self.panel._delete_selected()
        self.assertEqual(self.xs(), [10.0, 40.0, 50.0])

    def test_with_only_a_group_header_selected_delete_removes_the_group_not_its_rois(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        self.select_in_view(self.header("group_1"))
        self.panel._delete_selected()
        self.assertEqual(self.toolbox.groups(), ())
        self.assertEqual(len(self.toolbox.rois()), 5)

    def test_the_delete_key_asks_to_delete(self) -> None:
        asked: list[bool] = []
        self.tree.delete_requested.connect(lambda: asked.append(True))
        self.select_in_view(self.cell(2, 0))
        QTest.keyClick(self.tree, Qt.Key.Key_Delete)
        self.assertEqual(asked, [True])


class CommandTest(_PanelCase):
    def test_group_the_selection_with_a_name(self) -> None:
        self.selection.set_roi_selection({1, 2})
        with mock.patch.object(QInputDialog, "getText", return_value=("Pair", True)):
            self.panel._group_selected_or_new()
        (group,) = self.toolbox.groups()
        self.assertEqual((group.name, group.area_roi_ids), ("Pair", [1, 2]))

    def test_with_nothing_selected_the_plus_makes_an_empty_group(self) -> None:
        with mock.patch.object(QInputDialog, "getText", return_value=("Empty", True)):
            self.panel._group_selected_or_new()
        (group,) = self.toolbox.groups()
        self.assertEqual((group.name, group.area_roi_ids), ("Empty", []))

    def test_cancelling_the_name_dialog_does_nothing(self) -> None:
        with mock.patch.object(QInputDialog, "getText", return_value=("", False)):
            self.panel._group_selected_or_new()
        self.assertEqual(self.toolbox.groups(), ())

    def test_add_to_and_remove_from_a_group(self) -> None:
        group_id = self.toolbox.create_group("G")
        self.selection.set_roi_selection({3, 4})
        self.panel._add_selected_to_group(group_id)
        self.assertEqual(self.toolbox.groups()[0].area_roi_ids, [3, 4])
        self.panel._ungroup_selected()
        self.assertEqual(self.toolbox.groups(), ())

    def test_colour_the_selected_rois(self) -> None:
        self.selection.set_roi_selection({1, 2})
        with mock.patch("lspr_imaging_app.panels.roi_table.panel.QColorDialog.getColor", return_value=QColor("#123456")):
            self.panel._color_selected()
        self.assertEqual([self.toolbox.roi_by_id(i).sample_color_hex for i in (1, 2, 3)], ["#123456", "#123456", None])

    def test_the_colour_button_recolours_a_selected_group(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        self.select_in_view(self.header("group_1"))
        with mock.patch("lspr_imaging_app.panels.roi_table.panel.QColorDialog.getColor", return_value=QColor("#ff0000")):
            self.panel._color_selected()
        self.assertEqual(self.toolbox.groups()[0].sample_color_hex, "#ff0000")
        self.assertEqual(self.toolbox.roi_by_id(1).sample_color_hex, "#ff0000", "the first member shows the base colour")

    def test_double_clicking_a_chip_changes_the_colour(self) -> None:
        with mock.patch("lspr_imaging_app.panels.roi_table.panel.QColorDialog.getColor", return_value=QColor("#00ff00")):
            self.panel._on_chip_double_clicked(self.cell(4, 0))
        self.assertEqual(self.toolbox.roi_by_id(4).sample_color_hex, "#00ff00")

    def test_a_cancelled_colour_dialog_changes_nothing(self) -> None:
        self.selection.set_roi_selection({1})
        with mock.patch("lspr_imaging_app.panels.roi_table.panel.QColorDialog.getColor", return_value=QColor()):
            self.panel._color_selected()
        self.assertIsNone(self.toolbox.roi_by_id(1).sample_color_hex)

    def test_reset_diameters(self) -> None:
        self.toolbox.resize_rois((1, 2), sample_diameter_px=12.0, reference_inner_diameter_px=10.0)
        self.selection.set_roi_selection({1})
        self.panel._reset_diameters_selected()
        self.assertEqual(self.toolbox.roi_by_id(1).sample_diameter_px, 20.0)
        self.assertIsNone(self.toolbox.roi_by_id(1).reference_inner_diameter_px)
        self.assertEqual(self.toolbox.roi_by_id(2).sample_diameter_px, 12.0)

    def test_shift_position_in_the_display_unit(self) -> None:
        self.selection.set_roi_selection({1, 2})
        with mock.patch.object(ShiftDialog, "exec", return_value=QDialog.DialogCode.Accepted), mock.patch.object(
            ShiftDialog, "shift", return_value=(2.0, -1.0)
        ):
            self.panel._shift_selected()
        self.assertEqual(self.xs()[:3], [12.0, 22.0, 30.0])
        self.assertEqual([roi.center_y for roi in self.toolbox.rois()][:3], [4.0, 4.0, 5.0])

    def test_group_menu_offers_group_actions_and_ungrouped_only_select(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        menu = QMenu()
        self.panel._fill_group_menu(menu, self.header("group_1"))
        labels = [a.text() for a in menu.actions() if a.text()]
        self.assertIn("Rename…", labels)
        self.assertIn("Delete group (keeps its ROIs)", labels)
        down = next(a for a in menu.actions() if a.text() == "Move group down")
        self.assertFalse(down.isEnabled(), "only one group")
        ungrouped = self.model.group_index(None)
        menu = QMenu()
        self.panel._fill_group_menu(menu, ungrouped)
        self.assertEqual([a.text() for a in menu.actions() if a.text()], ["Select members"])

    def test_the_roi_menu_disables_moves_when_not_sorted_by_number(self) -> None:
        self.selection.set_roi_selection({2})
        menu = QMenu()
        self.panel._fill_roi_menu(menu)
        up = next(a for a in menu.actions() if a.text() == "Move up")
        self.assertTrue(up.isEnabled())
        self.panel._on_header_clicked(COLUMN_Y)
        menu = QMenu()
        self.panel._fill_roi_menu(menu)
        up = next(a for a in menu.actions() if a.text() == "Move up")
        self.assertFalse(up.isEnabled())

    def test_moving_a_group_up_and_down(self) -> None:
        self.toolbox.group_rois((1,), "A")
        self.toolbox.group_rois((2,), "B")
        self.panel._move_group("group_2", -1)
        self.assertEqual([g.name for g in self.toolbox.groups()], ["B", "A"])


class RememberedStateTest(_PanelCase):
    def test_flat_sort_and_widths_are_restored_and_saved(self) -> None:
        store = UiStateStore({
            "roi_table/flat": True,
            "roi_table/sort": [COLUMN_X, True],
            "roi_table/column_widths": [70, 90, 61, 62, 63, 64, 65],
        })
        self.toolbox.group_rois((1, 2), "A")
        self.panel.restore_ui_state(store)
        self.assertTrue(self.panel._flat_button.isChecked())
        self.assertFalse(self.model.is_grouped_view())
        self.assertEqual(self.model.sort_state(), (COLUMN_X, True))
        self.assertEqual(self.tree.columnWidth(COLUMN_X), 61)
        self.assertEqual(self.tree.columnWidth(COLUMN_RING_OUT), 65)
        self.panel._flat_button.setChecked(False)
        self.assertIs(store.get("roi_table/flat"), False)
        self.panel._on_header_clicked(COLUMN_Y)
        self.assertEqual(store.get("roi_table/sort"), [COLUMN_Y, False])
        self.tree.setColumnWidth(COLUMN_Y, 77)
        self.assertEqual(store.get("roi_table/column_widths")[COLUMN_Y], 77)

    def test_collapsed_groups_are_restored_and_saved(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        self.toolbox.group_rois((3,), "B")
        store = UiStateStore({"roi_table/collapsed": ["group_1"]})
        self.panel.restore_ui_state(store)
        self.refresh()
        self.assertFalse(self.tree.isExpanded(self.header("group_1")))
        self.assertTrue(self.tree.isExpanded(self.header("group_2")))
        self.tree.collapse(self.header("group_2"))
        self.assertEqual(store.get("roi_table/collapsed"), ["group_1", "group_2"])
        self.tree.expand(self.header("group_1"))
        self.assertEqual(store.get("roi_table/collapsed"), ["group_2"])

    def test_rubbish_in_the_saved_state_is_ignored(self) -> None:
        store = UiStateStore({
            "roi_table/sort": [99, "yes"],
            "roi_table/collapsed": [1, 2],
            "roi_table/column_widths": [10, -5],
        })
        self.panel.restore_ui_state(store)
        self.assertEqual(self.model.sort_state(), (COLUMN_ID, False))


class LayoutAndPaintTest(_PanelCase):
    def test_numeric_columns_leave_room_around_their_widest_text(self) -> None:
        """Measured against the font in use here (offscreen Qt may fall back
        to a different font than the app's on a real display)."""
        metrics = QFontMetrics(self.tree.font())
        widths = suggested_column_widths(metrics)
        for column in (COLUMN_X, COLUMN_Y, COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT):
            self.assertGreaterEqual(widths[column] - metrics.horizontalAdvance("9999.9" if column in (COLUMN_X, COLUMN_Y) else "999.9"), 12, column)
        for column, title in ((COLUMN_SAMPLE, "Sample"), (COLUMN_RING_IN, "Ring in"), (COLUMN_RING_OUT, "Ring out")):
            self.assertGreaterEqual(widths[column] - metrics.horizontalAdvance(title), 20, f"{title}: room for the sort arrow")

    def test_painting_does_not_raise_in_either_theme_with_groups_and_selection(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        self.toolbox.resize_rois((3,), reference_inner_diameter_px=10.0)
        self.refresh()
        self.selection.set_roi_selection({2, 3})
        self.tree.resize(360, 260)
        for theme in (self._theme, LSPRI_BRIGHT_THEME):
            set_active_theme(theme)
            self.panel.refresh_theme()
            image = QImage(360, 260, QImage.Format.Format_ARGB32)
            image.fill(QColor("white"))
            self.tree.viewport().render(image)
            colors = {image.pixel(x, y) for x in range(0, 360, 9) for y in range(0, 260, 9)}
            self.assertGreater(len(colors), 3, "something was painted")

    def test_the_toolbar_buttons_get_icons_and_follow_the_theme(self) -> None:
        for button in (self.panel._group_button, self.panel._delete_button, self.panel._flat_button):
            self.assertFalse(button.icon().isNull())
        set_active_theme(LSPRI_BRIGHT_THEME)
        self.panel.refresh_theme()
        self.assertFalse(self.panel._group_button.icon().isNull())


if __name__ == "__main__":
    unittest.main()
