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
from PyQt6.QtCore import QEvent, QItemSelectionModel, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QMouseEvent

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
        if table.item(row, 1).text() == name:
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
                names = {window.group_table.item(row, 1).text() for row in range(2)}
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
                self.assertEqual(window.group_table.item(0, 0).text(), "1")
                self.assertEqual(window.group_table.item(0, 4).text(), "0")

    def test_add_selected_rois_to_group_moves_them_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                group = self._three_rois_one_group(window)
                window._selected_roi_ids = {3}
                window._group_table_controller.add_selected_rois_to_group(group.group_id)
                self.assertEqual(set(group.area_roi_ids), {1, 2, 3})

    def test_create_group_with_selection_moves_rois_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                group = self._three_rois_one_group(window)
                window._selected_roi_ids = {2, 3}
                with (
                    mock.patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=("New", True)),
                    mock.patch("PyQt6.QtWidgets.QColorDialog.getColor", return_value=QColor("#123456")),
                ):
                    window._group_table_controller.create_group()

                new_group = next(g for g in window._state.area_roi_groups if g.name == "New")
                self.assertEqual(set(new_group.area_roi_ids), {2, 3})
                # ROI 2 was moved out of the pre-existing "Sensors" group.
                self.assertEqual(group.area_roi_ids, [1])

    def test_create_group_without_selection_starts_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                self._three_rois_one_group(window)
                window._selected_roi_ids = set()
                with (
                    mock.patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=("Empty New", True)),
                    mock.patch("PyQt6.QtWidgets.QColorDialog.getColor", return_value=QColor("#123456")),
                ):
                    window._group_table_controller.create_group()

                new_group = next(g for g in window._state.area_roi_groups if g.name == "Empty New")
                self.assertEqual(new_group.area_roi_ids, [])

    def test_delete_selected_groups_removes_them_and_ungroups_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                self._three_rois_one_group(window)
                _select_table_row(window.group_table, _row_named(window.group_table, "Sensors"))
                window._group_table_controller.delete_selected_groups()

                self.assertEqual(window._state.area_roi_groups, [])

    def test_delete_selected_groups_with_no_selection_leaves_groups_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                group = self._three_rois_one_group(window)
                window.group_table.clearSelection()
                window._group_table_controller.delete_selected_groups()

                self.assertIn(group, window._state.area_roi_groups)

    def test_group_by_column_creates_one_group_per_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                # A loose 2x3 grid: two x-clusters (~0 and ~100), three rows each.
                window._state.area_rois = [
                    AreaRoi(area_roi_id=roi_id, center_x=float(x), center_y=float(y), sample_radius_px=5.0)
                    for roi_id, (x, y) in enumerate(
                        [(0, 0), (0, 50), (0, 100), (100, 0), (100, 50), (100, 100)], start=1
                    )
                ]
                window._group_table_controller.update_table()

                window._group_rois_by_column()

                names = sorted(g.name for g in window._state.area_roi_groups)
                self.assertEqual(names, ["Column 1", "Column 2"])
                by_name = {g.name: g for g in window._state.area_roi_groups}
                self.assertEqual(set(by_name["Column 1"].area_roi_ids), {1, 2, 3})
                self.assertEqual(set(by_name["Column 2"].area_roi_ids), {4, 5, 6})
                self.assertNotEqual(by_name["Column 1"].sample_color_hex, by_name["Column 2"].sample_color_hex)

    def test_group_by_column_replaces_existing_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                window._state.area_rois = [
                    AreaRoi(area_roi_id=roi_id, center_x=float(x), center_y=0.0, sample_radius_px=5.0)
                    for roi_id, x in enumerate([0, 100], start=1)
                ]
                stale_group = AreaRoiGroup(
                    group_id="group_1", name="Stale", sample_color_hex="#ff0000",
                    reference_color_hex="#00ff00", area_roi_ids=[1, 2],
                )
                window._state.area_roi_groups = [stale_group]
                window._group_table_controller.update_table()

                window._group_rois_by_column()

                self.assertNotIn(stale_group, window._state.area_roi_groups)
                self.assertEqual(len(window._state.area_roi_groups), 2)

    def test_group_by_column_with_no_column_structure_leaves_groups_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                # All ROIs at (roughly) the same x - a single column, nothing to split.
                window._state.area_rois = [
                    AreaRoi(area_roi_id=roi_id, center_x=0.0, center_y=float(roi_id * 20), sample_radius_px=5.0)
                    for roi_id in (1, 2, 3)
                ]
                window._group_table_controller.update_table()

                window._group_rois_by_column()

                self.assertEqual(window._state.area_roi_groups, [])

    def test_group_table_shows_1_based_position_in_first_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                window._state.area_rois = [
                    AreaRoi(area_roi_id=roi_id, center_x=0.0, center_y=0.0, sample_radius_px=5.0)
                    for roi_id in (1, 2)
                ]
                group_a = AreaRoiGroup(
                    group_id="group_1", name="Alpha", sample_color_hex="#ff0000",
                    reference_color_hex="#00ff00", area_roi_ids=[1],
                )
                group_b = AreaRoiGroup(
                    group_id="group_2", name="Beta", sample_color_hex="#0000ff",
                    reference_color_hex="#ffff00", area_roi_ids=[2],
                )
                window._state.area_roi_groups = [group_a, group_b]
                window._group_table_controller.update_table()

                self.assertEqual(window.group_table.item(_row_named(window.group_table, "Alpha"), 0).text(), "1")
                self.assertEqual(window.group_table.item(_row_named(window.group_table, "Beta"), 0).text(), "2")

    def test_move_group_swaps_list_order_and_renumbers_display(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                group_a = AreaRoiGroup(
                    group_id="group_1", name="Alpha", sample_color_hex="#ff0000",
                    reference_color_hex="#00ff00", area_roi_ids=[],
                )
                group_b = AreaRoiGroup(
                    group_id="group_2", name="Beta", sample_color_hex="#0000ff",
                    reference_color_hex="#ffff00", area_roi_ids=[],
                )
                window._state.area_roi_groups = [group_a, group_b]
                window._group_table_controller.update_table()

                window._group_table_controller.move_group("group_2", -1)
                window._group_table_controller.update_table()

                self.assertEqual(window._state.area_roi_groups, [group_b, group_a])
                self.assertEqual(window.group_table.item(_row_named(window.group_table, "Beta"), 0).text(), "1")
                self.assertEqual(window.group_table.item(_row_named(window.group_table, "Alpha"), 0).text(), "2")

    def test_move_group_up_at_top_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                group_a = AreaRoiGroup(
                    group_id="group_1", name="Alpha", sample_color_hex="#ff0000",
                    reference_color_hex="#00ff00", area_roi_ids=[],
                )
                group_b = AreaRoiGroup(
                    group_id="group_2", name="Beta", sample_color_hex="#0000ff",
                    reference_color_hex="#ffff00", area_roi_ids=[],
                )
                window._state.area_roi_groups = [group_a, group_b]
                window._group_table_controller.update_table()

                window._group_table_controller.move_group("group_1", -1)

                self.assertEqual(window._state.area_roi_groups, [group_a, group_b])

    def test_move_selected_no_ops_on_ungrouped_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _open_window(Path(tmp)) as window:
                group = self._three_rois_one_group(window)
                _select_table_row(window.group_table, _row_named(window.group_table, "Ungrouped"))

                window._group_table_controller.move_selected(-1)

                self.assertEqual(window._state.area_roi_groups, [group])

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


class TestGroupTableLayoutAndDoubleClickButtonFilter(unittest.TestCase):
    """Bugs found from a screenshot of the Group table, one of which turned
    out to also affect the (older, separately-implemented) ROI table:
    (1) the Sample/Reference swatch columns showed visible empty space next
    to the color icon - because ResizeToContents sizes an icon-only column
    to fit its *header text* ("Sample"/"Reference"), not the 16px icon, and
    because Qt also clamps any explicit width to a hidden minimumSectionSize
    regardless of resize mode. The ROI table's own C_s/C_r columns had the
    identical bug already, just less noticeably (its setColumnWidth() calls
    were silently ignored too - see test_roi_table_swatch_columns_also_
    honor_their_requested_width). (2) right-double-clicking a color swatch
    opened the same color-picker dialog a left-double-click does, which is
    standard (if surprising) Qt behavior: cellDoubleClicked/the DoubleClicked
    edit trigger fire for any mouse button, not just Left."""

    def _window_with_one_group(self, tmp: str):
        window = MainWindow(Path(tmp), fast_startup=True)
        window._state.area_rois = [AreaRoi(area_roi_id=1, center_x=0.0, center_y=0.0, sample_radius_px=5.0)]
        group = AreaRoiGroup(
            group_id="group_1", name="Alpha", sample_color_hex="#ff0000",
            reference_color_hex="#00ff00", area_roi_ids=[1],
        )
        window._state.area_roi_groups = [group]
        window._group_table_controller.update_table()
        return window

    def test_color_swatch_columns_are_narrow_not_header_width(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = self._window_with_one_group(tmp)
            try:
                self.assertEqual(window.group_table.columnWidth(2), 22)
                self.assertEqual(window.group_table.columnWidth(3), 22)
            finally:
                window._state.dataset = None
                window.close()
                window.deleteLater()

    def test_roi_table_swatch_columns_also_honor_their_requested_width(self) -> None:
        """RoiTableController.update_table()'s own setColumnWidth() calls had
        the identical bug (pre-existing, not introduced this session): left
        in ResizeToContents mode, every explicit width was silently ignored,
        so C_s/C_r rendered at ~60px instead of the requested 22px."""
        with tempfile.TemporaryDirectory() as tmp:
            window = self._window_with_one_group(tmp)
            try:
                window._roi_table_controller.update_table()
                self.assertEqual(window.roi_table.columnWidth(0), 34)
                self.assertEqual(window.roi_table.columnWidth(1), 96)
                self.assertEqual(window.roi_table.columnWidth(2), 22)
                self.assertEqual(window.roi_table.columnWidth(3), 22)
                self.assertEqual(window.roi_table.columnWidth(4), 58)
                self.assertEqual(window.roi_table.columnWidth(5), 58)
                self.assertEqual(window.roi_table.columnWidth(6), 58)
                self.assertEqual(window.roi_table.columnWidth(7), 64)
                self.assertEqual(window.roi_table.columnWidth(8), 64)
            finally:
                window._state.dataset = None
                window.close()
                window.deleteLater()

    def test_right_double_click_on_group_table_is_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = self._window_with_one_group(tmp)
            try:
                event = QMouseEvent(
                    QEvent.Type.MouseButtonDblClick, QPointF(5, 5),
                    Qt.MouseButton.RightButton, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier,
                )
                consumed = window._image_interaction.handle_event(window.group_table.viewport(), event)
                self.assertTrue(consumed, "right-double-click should be swallowed, not treated as a double-click")
            finally:
                window._state.dataset = None
                window.close()
                window.deleteLater()

    def test_right_double_click_on_roi_table_is_also_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = self._window_with_one_group(tmp)
            try:
                event = QMouseEvent(
                    QEvent.Type.MouseButtonDblClick, QPointF(5, 5),
                    Qt.MouseButton.RightButton, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier,
                )
                consumed = window._image_interaction.handle_event(window.roi_table.viewport(), event)
                self.assertTrue(consumed)
            finally:
                window._state.dataset = None
                window.close()
                window.deleteLater()

    def test_left_double_click_on_group_table_is_not_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = self._window_with_one_group(tmp)
            try:
                event = QMouseEvent(
                    QEvent.Type.MouseButtonDblClick, QPointF(5, 5),
                    Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                )
                consumed = window._image_interaction.handle_event(window.group_table.viewport(), event)
                self.assertFalse(consumed, "left-double-click must keep working (rename/recolor still needs it)")
            finally:
                window._state.dataset = None
                window.close()
                window.deleteLater()


if __name__ == "__main__":
    unittest.main()
