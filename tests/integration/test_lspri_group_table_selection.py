"""Integration tests for the Group table (GroupTableController) and the
ROI/Group panel toggle it lives behind.

The main behavior under test: selecting a group's row must select all of its
member ROIs *and* actually refresh the spectra/sensorgram plots, not just the
image overlay. A plain click on an existing ROI-table row only refreshes
overlays/summary (RoiTableController.on_selection_changed never calls
_update_selection_dependent_plots) - the Group table's selection handler must
follow MainWindow._apply_roi_selection's path instead, or group selection
would silently fail to update the sensorgram/spectra. See
MainWindow._apply_roi_selection and GroupTableController.on_selection_changed.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QItemSelectionModel, QPoint

# Must exist before any lspr_imaging_app.gui module is imported below - Qt
# objects get built at import time in some of those modules.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiGroup  # noqa: E402
from lspr_imaging_app.gui.main_window import MainWindow  # noqa: E402


@contextmanager
def _open_window(folder: Path):
    """Matches test_lspri_roi_table_diameter_edit.py's MainWindow
    construction/teardown convention: dataset is cleared before close() so
    the closeEvent's processing-state save is a no-op instead of racing the
    temp dir cleanup."""
    window = MainWindow(folder, fast_startup=True)
    try:
        yield window
    finally:
        window._state.dataset = None
        window.close()
        window.deleteLater()


def _select_table_row(table: QtWidgets.QTableWidget, row: int) -> None:
    """Selects a whole row the same way a real click would - no screen
    coordinates, just the same selection-model call the app's own table-row
    selection code uses (see RoiTableController.sync_selection)."""
    table.selectionModel().select(
        table.model().index(row, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )


def _row_named(table: QtWidgets.QTableWidget, name: str) -> int:
    for row in range(table.rowCount()):
        if table.item(row, 0).text() == name:
            return row
    raise AssertionError(f"no row named {name!r} in table")


class TestGroupTableSelection(unittest.TestCase):
    def _three_rois_one_group(self, window: MainWindow) -> AreaRoiGroup:
        window._state.area_rois = [
            AreaRoi(area_roi_id=roi_id, center_x=float(roi_id), center_y=float(roi_id), sample_radius_px=5.0)
            for roi_id in (1, 2, 3)
        ]
        group = AreaRoiGroup(
            group_id="group_1",
            name="Sensors",
            sample_color_hex="#ff0000",
            reference_color_hex="#00ff00",
            area_roi_ids=[1, 2],
        )
        window._state.area_roi_groups = [group]
        window._group_table_controller.update_table()
        return group

    def test_group_table_has_one_row_per_group_plus_ungrouped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                self._three_rois_one_group(window)
                self.assertEqual(window.group_table.rowCount(), 2)
                names = {window.group_table.item(row, 0).text() for row in range(2)}
                self.assertEqual(names, {"Sensors", "Ungrouped"})

    def test_selecting_group_row_selects_its_members_and_refreshes_plots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                self._three_rois_one_group(window)
                _select_table_row(window.group_table, _row_named(window.group_table, "Sensors"))
                window._group_table_controller.on_selection_changed()

                self.assertEqual(window._selected_roi_ids, {1, 2})
                # Proves _update_selection_dependent_plots actually ran (past
                # its own skip-guard) rather than only overlays/summary
                # having refreshed - it unconditionally records the new
                # selection signature at that point.
                self.assertEqual(window._selection_plot_highlight_signature, (1, 2))

    def test_selecting_ungrouped_row_selects_the_remaining_roi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                self._three_rois_one_group(window)
                _select_table_row(window.group_table, _row_named(window.group_table, "Ungrouped"))
                window._group_table_controller.on_selection_changed()
                self.assertEqual(window._selected_roi_ids, {3})

    def test_new_empty_group_is_not_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                window._state.area_rois = [
                    AreaRoi(area_roi_id=1, center_x=0.0, center_y=0.0, sample_radius_px=5.0)
                ]
                group = AreaRoiGroup(
                    group_id="group_1",
                    name="Empty",
                    sample_color_hex="#ff0000",
                    reference_color_hex="#00ff00",
                    area_roi_ids=[],
                )
                window._state.area_roi_groups.append(group)
                window._group_table_controller.update_table()

                self.assertIn(group, window._state.area_roi_groups)
                self.assertEqual(_row_named(window.group_table, "Empty"), 0)
                self.assertEqual(window.group_table.item(0, 3).text(), "0")

    def test_add_selected_rois_to_group_moves_them_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                group = self._three_rois_one_group(window)
                window._selected_roi_ids = {3}
                window._group_table_controller.add_selected_rois_to_group(group.group_id)
                self.assertEqual(set(group.area_roi_ids), {1, 2, 3})

    def test_remove_last_roi_from_group_prunes_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                window._state.area_rois = [
                    AreaRoi(area_roi_id=1, center_x=0.0, center_y=0.0, sample_radius_px=5.0)
                ]
                group = AreaRoiGroup(
                    group_id="group_1",
                    name="Solo",
                    sample_color_hex="#ff0000",
                    reference_color_hex="#00ff00",
                    area_roi_ids=[1],
                )
                window._state.area_roi_groups = [group]
                window._selected_roi_ids = {1}
                window._group_table_controller.remove_selected_rois_from_group("group_1")
                self.assertNotIn(group, window._state.area_roi_groups)


class TestRoiGroupTitleToggle(unittest.TestCase):
    """The ROI/Group panel title toggle itself (PanelContainer.title_options)
    - a bespoke widget with no other precedent in this app, so its click
    wiring is worth covering directly rather than only via a manual pass."""

    def test_clicking_the_group_segment_swaps_which_table_is_visible(self) -> None:
        # isHidden() (an explicit "was setVisible(False) called on this exact
        # widget") rather than isVisible() (also false for everyone when the
        # top-level window was never shown() - true in this headless test,
        # matching this repo's own no-.show()/.exec() GUI-test convention).
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                self.assertEqual(window._roi_list_view_mode, "roi")
                self.assertFalse(window.roi_table.isHidden())
                self.assertTrue(window.group_table.isHidden())

                roi_button, group_button = window.roi_list_panel._title_option_buttons
                self.assertEqual(group_button.text(), "Group")
                group_button.click()

                self.assertEqual(window._roi_list_view_mode, "group")
                self.assertTrue(window.roi_table.isHidden())
                self.assertFalse(window.group_table.isHidden())
                self.assertEqual(window.roi_list_panel._active_title_index, 1)

                roi_button.click()
                self.assertEqual(window._roi_list_view_mode, "roi")
                self.assertFalse(window.roi_table.isHidden())
                self.assertTrue(window.group_table.isHidden())


class TestImagePanelAddToExistingGroup(unittest.TestCase):
    """The image canvas's right-click ROI menu (_show_analysis_roi_context_menu
    in roi_geometry_mixin.py) gained an "Add to group" submenu listing every
    existing group, so a selection made on the image can be added to one
    without retyping its name into the "Group..." dialog. Reuses
    GroupTableController.add_selected_rois_to_group - same operation as the
    Group table's own "Add selected ROIs to this group" menu item."""

    def test_add_to_group_submenu_moves_selection_into_existing_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                window._state.area_rois = [
                    AreaRoi(area_roi_id=i, center_x=float(i), center_y=float(i), sample_radius_px=5.0)
                    for i in (1, 2, 3)
                ]
                group_a = AreaRoiGroup(
                    group_id="group_1", name="Alpha", sample_color_hex="#ff0000",
                    reference_color_hex="#00ff00", area_roi_ids=[1],
                )
                group_b = AreaRoiGroup(
                    group_id="group_2", name="Beta", sample_color_hex="#0000ff",
                    reference_color_hex="#ffff00", area_roi_ids=[],
                )
                window._state.area_roi_groups = [group_a, group_b]
                window._selected_roi_ids = {2, 3}

                captured = {}

                def fake_exec(menu_self, *args, **kwargs):
                    add_to_group_action = next(a for a in menu_self.actions() if a.text() == "Add to group")
                    submenu = add_to_group_action.menu()
                    captured["submenu_items"] = [a.text() for a in submenu.actions()]
                    return next(a for a in submenu.actions() if a.text() == "Beta")

                with mock.patch("PyQt6.QtWidgets.QMenu.exec", fake_exec):
                    window._show_analysis_roi_context_menu(2, QPoint(0, 0))

                self.assertEqual(captured["submenu_items"], ["Alpha", "Beta"])
                self.assertEqual(set(group_b.area_roi_ids), {2, 3})
                self.assertEqual(group_a.area_roi_ids, [1])

    def test_no_add_to_group_submenu_when_no_groups_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                window._state.area_rois = [
                    AreaRoi(area_roi_id=1, center_x=0.0, center_y=0.0, sample_radius_px=5.0)
                ]
                window._selected_roi_ids = {1}

                captured = {}

                def fake_exec(menu_self, *args, **kwargs):
                    captured["top_level"] = [a.text() for a in menu_self.actions()]
                    return None

                with mock.patch("PyQt6.QtWidgets.QMenu.exec", fake_exec):
                    window._show_analysis_roi_context_menu(1, QPoint(0, 0))

                self.assertNotIn("Add to group", captured["top_level"])


if __name__ == "__main__":
    unittest.main()
