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
from PyQt6.QtCore import QItemSelectionModel, QMimeData, QModelIndex, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog, QInputDialog, QMenu, QToolButton

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np  # noqa: E402

try:
    from lspr_imaging_app.domain.roi_model import RoiMask
    from lspr_imaging_app.domain.roi_shapes import MaskShape, NO_BORDER, Rectangle, ring_diameters
    from lspr_ui import get_active_theme, load_tabler_icon, set_active_theme, transparent_icon_button_stylesheet

    from lspr_imaging_app.gui.app_theme import LSPRI_BRIGHT_THEME, LSPRI_DARK_THEME, apply_app_theme
    from lspr_imaging_app.image_tools import GeometryModule
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.panels.image.general_group import style_general_icon_button
    from lspr_imaging_app.panels.roi_table import RoiTablePanel
    from lspr_imaging_app.panels.roi_table.dialogs import ShiftDialog
    from lspr_imaging_app.panels.roi_table.model import (
        MIME_TYPE,
        ALL_SELECTED_ROLE,
        COLOR_ROLE,
        INHERITED_ROLE,
        KIND_ROLE,
    )
    from lspr_imaging_app.panels.roi_table.rows import (
        COLUMN_ANGLE,
        COLUMN_AREA,
        COLUMN_HEIGHT,
        COLUMN_ID,
        COLUMN_NAME,
        COLUMN_COUNT,
        COLUMN_SHAPE,
        COLUMN_TITLES,
        COLUMN_RIM_OFFSET,
        COLUMN_RIM_THICKNESS,
        COLUMN_SAMPLE,
        COLUMN_WIDTH,
        COLUMN_X,
        COLUMN_Y,
    )
    from lspr_imaging_app.panels.roi_table.view import suggested_column_widths
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_SELECT_ROWS = QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows


def _pump_events(seconds: float) -> None:
    import time as _time

    deadline = _time.monotonic() + seconds
    while _time.monotonic() < deadline:
        _APP.processEvents()
        _time.sleep(0.01)


def _pump_until(condition, seconds: float = 10.0) -> bool:
    """Process events until *condition* holds (True) or *seconds* have passed (False). For things a timer
    does: a fixed wait is long enough on a quiet machine, and came up short on a loaded CI runner."""
    import time as _time

    deadline = _time.monotonic() + seconds
    while _time.monotonic() < deadline:
        _APP.processEvents()
        if condition():
            return True
        _time.sleep(0.01)
    return False


class _PanelCase(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._theme = get_active_theme()
        self.toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.geometry = GeometryModule()
        self.toolbox.roi_ids_renumbered.connect(self.selection.remap_roi_ids)  # as the app wires it
        for x in (10.0, 20.0, 30.0, 40.0, 50.0):
            self.toolbox.add_roi(x, 5.0, sample_diameter_px=6.0)
        self.panel = RoiTablePanel(self.toolbox, self.selection, self.geometry)
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
        return self.panel.status_text()


class AppearanceTest(_PanelCase):
    def test_a_hidden_table_is_not_rebuilt_until_it_is_shown_again(self) -> None:
        self.panel.refresh_now()
        rows_before = self.panel._model.rowCount()
        rebuilds = []
        original = self.panel.refresh_now
        self.panel.refresh_now = lambda: (rebuilds.append(1), original())[1]
        self.panel.set_panel_visible(False)  # what the dock's visibilityChanged does
        self.toolbox.add_roi(60.0, 5.0, sample_diameter_px=6.0)  # a change while hidden
        _pump_events(0.3)
        self.assertEqual(rebuilds, [])
        self.assertFalse(self.panel._redraw_timer.isActive())
        self.panel.set_panel_visible(True)
        _pump_events(0.3)
        self.assertEqual(len(rebuilds), 1)
        self.assertEqual(self.panel._model.rowCount(), rows_before + 1)

    def test_the_table_is_not_rebuilt_in_the_middle_of_a_roi_drag_and_catches_up_once_after_it(self) -> None:
        """2026-11-19: the rebuild ran when the mouse paused during a resize and the next move waited behind it (~0.5 s on a large dataset)."""
        self.panel.refresh_now()
        rebuilds = []
        original = self.panel.refresh_now
        self.panel.refresh_now = lambda: (rebuilds.append(1), original())[1]
        self.panel.set_edit_running(True)  # what the Image panel's roi_edit_running does
        for diameter in (7.0, 8.0, 9.0):
            self.toolbox.resize_rois([1], sample_diameter_px=diameter)  # one write per mouse step
            _pump_events(0.05)
        _pump_events(0.3)  # a pause: longer than the table's refresh delay
        self.assertEqual(rebuilds, [], "no rebuild during the drag, not even after a pause")
        self.assertFalse(self.panel._redraw_timer.isActive())
        self.panel.set_edit_running(False)
        _pump_events(0.3)
        self.assertEqual(len(rebuilds), 1, "one catch-up when the button is let go")

    def test_a_cube_change_rebuilds_the_table_only_when_a_roi_has_a_timeline(self) -> None:
        self.panel.refresh_now()
        timer = self.panel._redraw_timer
        timer.stop()
        self.selection.set_cube(1)
        self.assertFalse(timer.isActive(), "no timeline: every cube shows the same rows, nothing to rebuild")
        self.toolbox.move_roi(1, 12.0, 5.0, cube=2, scope="persistent")  # a geometry change from cube 2 on
        self.assertTrue(self.toolbox.has_timeline())
        timer.stop()
        self.selection.set_cube(2)
        self.assertTrue(timer.isActive(), "with a timeline the rows differ per cube")

    def test_no_rois_shows_the_hint_and_adding_one_shows_the_table(self) -> None:
        empty = RoiTablePanel(RoiToolbox(), SelectionModule(), self.geometry)
        self.assertEqual(empty._stack.currentIndex(), 1)
        self.assertEqual(empty.status_text(), "No ROIs")
        self.assertEqual(self.panel._stack.currentIndex(), 0)
        self.assertEqual(self.footer(), "5 ROIs")

    def test_a_change_refreshes_the_table_after_a_short_pause(self) -> None:
        self.toolbox.add_roi(60.0, 5.0)
        self.assertTrue(self.panel._redraw_timer.isActive())
        self.assertEqual(len(self.model.rows()), 5, "not rebuilt yet")
        # Wait for the rebuild itself, not a fixed 250 ms: the timer is 100 ms, but with ~3600 tests in one
        # process a loaded CI runner once missed that (2026-10-18: 5 rows instead of 6).
        rebuilt = _pump_until(lambda: len(self.model.rows()) == 6)
        self.assertTrue(
            rebuilt,
            f"the table was never rebuilt (rows {len(self.model.rows())}, timer active {self.panel._redraw_timer.isActive()}, "
            f"panel visible-for-redraw {self.panel._gate.visible})",
        )
        self.assertFalse(self.panel._redraw_timer.isActive(), "one rebuild, then the timer is done")

    def test_flat_until_there_are_groups_then_headers(self) -> None:
        self.assertFalse(self.model.is_grouped_view())
        self.assertEqual(self.shown_ids(), [1, 2, 3, 4, 5])
        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        self.assertTrue(self.model.is_grouped_view())
        self.assertEqual([self.model.data(h) for h in self.model.group_headers()], ["A", "Ungrouped"])
        self.assertEqual(self.shown_ids(), [1, 2, 3, 4, 5])

    def test_cells_show_the_values_and_mark_inherited_rims(self) -> None:
        self.assertEqual(self.text(1, COLUMN_ID), "1")
        self.assertEqual(self.text(2, COLUMN_X), "20.0")
        self.assertEqual(self.text(2, COLUMN_Y), "5.0")
        self.assertEqual(self.text(2, COLUMN_SAMPLE), "6.0")
        self.assertEqual((self.text(2, COLUMN_RIM_OFFSET), self.text(2, COLUMN_RIM_THICKNESS)), ("4.0", "4.0"), "the shared default rim: offset 4, thickness 4 from the edge")
        self.assertTrue(self.model.data(self.cell(2, COLUMN_RIM_OFFSET), INHERITED_ROLE))
        self.assertTrue(self.model.data(self.cell(2, COLUMN_RIM_THICKNESS), INHERITED_ROLE))
        self.assertFalse(self.model.data(self.cell(2, COLUMN_SAMPLE), INHERITED_ROLE))
        self.toolbox.resize_rois((2,), reference_inner_diameter_px=10.0)  # a ring from diameter 10 to 22 round the 6 px circle: offset 2, thickness 6
        self.refresh()
        self.assertEqual((self.text(2, COLUMN_RIM_OFFSET), self.text(2, COLUMN_RIM_THICKNESS)), ("2.0", "6.0"))
        self.assertFalse(self.model.data(self.cell(2, COLUMN_RIM_OFFSET), INHERITED_ROLE))
        self.assertFalse(self.model.data(self.cell(2, COLUMN_RIM_THICKNESS), INHERITED_ROLE))  # the rim is one setting: editing a number makes it the ROI's own

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
        self.assertEqual(self.panel._unit_toggle.text(), "µm")
        self.assertEqual(self.text(2, COLUMN_X), "10")  # 20 px * 0.5
        self.edit(2, COLUMN_SAMPLE, "10")  # typed in µm
        self.assertEqual(self.toolbox.roi_by_id(2).shape.diameter_px, 20.0)

    def test_the_title_bar_counts_rois_and_the_selection(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.assertEqual(self.footer(), "5 ROIs  ·  2 selected")

    def test_the_counts_are_fields_of_fixed_places_and_nothing_is_left_under_the_table(self) -> None:
        self.selection.set_roi_selection({1, 2})
        roi_count, group_count, selected = self.panel.title_columns()
        self.assertEqual((roi_count.text, group_count.text, selected.text), ("5 ROIs", "", "2 selected"))
        self.assertTrue(roi_count.widest and group_count.widest, "sized fields: a group count appearing must not shift the ones after it")
        layout = self.panel.layout()
        self.assertIs(layout.itemAt(layout.count() - 1).widget(), self.panel._stack, "the row under the table is gone")

    def test_a_change_of_the_counts_is_announced_to_the_dock_once(self) -> None:
        announced = []
        self.panel.title_columns_changed.connect(announced.append)
        self.selection.set_roi_selection({1})
        self.assertEqual(len(announced), 1)
        self.selection.set_roi_selection({1})  # nothing changed: nothing to redraw in the title bar
        self.assertEqual(len(announced), 1)
        self.assertEqual(announced[0][2].text, "1 selected")

    def test_a_notice_takes_the_place_of_the_counts_for_a_while_and_the_counts_come_back(self) -> None:
        self.panel._show_notice("Sort by # (ascending) to reorder ROIs.", error=True)
        (notice,) = self.panel.title_columns()
        self.assertEqual(notice.text, "Sort by # (ascending) to reorder ROIs.")
        self.assertEqual(notice.color, get_active_theme().accent_red)
        self.assertEqual(notice.tooltip, notice.text, "the whole text stays readable when the title bar is too narrow for it")
        self.assertTrue(self.panel._notice_timer.isActive())
        self.panel._notice_timer.timeout.emit()  # the pause is over
        self.assertEqual(self.footer(), "5 ROIs")
        self.assertFalse(self.panel._notice_timer.isActive())

    def test_the_dock_title_bar_shows_the_fields(self) -> None:
        from lspr_imaging_app.panels.dock_container import PanelContainer

        window = QtWidgets.QMainWindow()
        dock = PanelContainer("ROI / Groups", self.panel, window)
        self.panel.title_columns_changed.connect(dock.set_subtitle_columns)
        dock.set_subtitle_columns(self.panel.title_columns())
        self.selection.set_roi_selection({1, 2})
        def shown() -> list[str]:  # the unsized field is an elided label: cut to the room it has, so ask for its whole text
            return [label.full_text() if hasattr(label, "full_text") else label.text() for label in dock._subtitle_column_labels]

        self.assertEqual(shown(), ["5 ROIs", "", "2 selected"])
        self.panel._show_notice("Hello")
        self.assertEqual(shown(), ["Hello"])
        window.deleteLater()


class SharedDisplayStyleTest(_PanelCase):
    """The Sample colour (that of a ROI with none of its own) lives in one `RoiDisplayStyle`, read by the table and the overlay."""

    def setUp(self) -> None:
        super().setUp()
        from lspr_imaging_app.roi.display_style import RoiDisplayStyle

        self.style = RoiDisplayStyle()
        self.panel.deleteLater()
        self.panel = RoiTablePanel(self.toolbox, self.selection, self.geometry, display_style=self.style)
        self.model = self.panel._model

    def test_the_dot_of_a_roi_with_no_colour_follows_the_shared_sample_colour(self) -> None:
        self.assertEqual(self.model.data(self.cell(1, COLUMN_ID), COLOR_ROLE), self.style.sample.color)
        self.style.set("sample", "color", "#c957e8")
        self.panel.refresh_now()
        self.assertEqual(self.model.data(self.cell(1, COLUMN_ID), COLOR_ROLE), "#c957e8")

    def test_a_roi_with_its_own_colour_keeps_it(self) -> None:
        self.toolbox.set_roi_colors([2], "#112233")
        self.style.set("sample", "color", "#c957e8")
        self.panel.refresh_now()
        self.assertEqual(self.model.data(self.cell(2, COLUMN_ID), COLOR_ROLE), "#112233")

    def test_the_overlay_uses_the_very_same_style_object(self) -> None:
        import pyqtgraph as pg

        from lspr_imaging_app.panels.image.roi_overlay import RoiOverlay

        overlay = RoiOverlay(pg.PlotItem(), self.style)
        self.assertIs(overlay.sample, self.style.sample)
        self.style.set("sample", "color", "#abcdef")
        self.assertEqual(overlay.sample.color, "#abcdef")


class RowHeightTest(_PanelCase):
    """Row heights come from the delegate's `sizeHint` of column 0 alone (the
    other columns return no height) - asking every column and the base class
    made rebuilding a 1500-ROI table take 1.3 s instead of 0.2 s."""

    def test_roi_and_group_rows_get_their_own_heights_from_column_zero(self) -> None:
        from PyQt6.QtWidgets import QStyleOptionViewItem

        from lspr_imaging_app.panels.roi_table.delegate import group_row_height, row_height

        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        delegate = self.tree.itemDelegate()
        option = QStyleOptionViewItem()
        option.font = self.tree.font()
        option.fontMetrics = QFontMetrics(option.font)
        header = self.header(self.model.group_id(self.model.group_headers()[0]))
        roi_cell = self.cell(1, COLUMN_ID)

        self.assertEqual(delegate.sizeHint(option, header).height(), group_row_height(option.fontMetrics))
        self.assertEqual(delegate.sizeHint(option, roi_cell).height(), row_height(option.fontMetrics))
        self.assertEqual(delegate.sizeHint(option, self.cell(1, COLUMN_X)).height(), 0)

    def test_the_view_still_lays_rows_out_at_those_heights(self) -> None:
        from lspr_imaging_app.panels.roi_table.delegate import row_height

        self.refresh()
        expected = row_height(QFontMetrics(self.tree.font()))
        self.assertEqual(self.tree.rowHeight(self.cell(1, COLUMN_ID)), expected)


class UnitToggleTest(_PanelCase):
    """The px/µm toggle in the toolbar: the same control as the Image ribbon's
    View tab, driving the one Geometry display unit."""

    def calibrate(self, display_units: str = "px") -> None:
        self.geometry.restore_settings(
            GeometrySettings(calibration_enabled=True, microns_per_pixel_x=0.5, microns_per_pixel_y=0.5, display_units=display_units)
        )

    def test_without_a_calibration_it_shows_px_and_is_disabled_with_a_reason(self) -> None:
        toggle = self.panel._unit_toggle
        self.assertEqual(toggle.text(), "px")
        self.assertFalse(toggle.isEnabled())
        self.assertIn("calibration", toggle.toolTip())

    def test_clicking_it_switches_the_geometry_unit_and_the_table_follows(self) -> None:
        self.calibrate()
        toggle = self.panel._unit_toggle
        self.assertTrue(toggle.isEnabled())
        self.assertEqual(self.text(2, COLUMN_X), "20.0")

        toggle.click()
        self.assertEqual(self.geometry.settings().display_units, "um")
        self.assertEqual(toggle.text(), "µm")
        self.assertTrue(toggle.isChecked())
        self.refresh()
        self.assertEqual(self.text(2, COLUMN_X), "10")  # 20 px at 0.5 µm/px

        toggle.click()
        self.assertEqual(self.geometry.settings().display_units, "px")
        self.assertEqual(toggle.text(), "px")
        self.refresh()
        self.assertEqual(self.text(2, COLUMN_X), "20.0")

    def test_a_change_made_elsewhere_moves_the_toggle_and_schedules_a_refresh(self) -> None:
        self.calibrate()
        self.panel._redraw_timer.stop()
        self.geometry.set_display_units("um")  # as the ribbon's own units button does
        self.assertEqual(self.panel._unit_toggle.text(), "µm")
        self.assertTrue(self.panel._unit_toggle.isChecked())
        self.assertTrue(self.panel._redraw_timer.isActive())

    def test_a_restored_session_in_micrometers_shows_micrometers(self) -> None:
        self.calibrate("um")
        self.assertEqual(self.panel._unit_toggle.text(), "µm")
        self.refresh()
        self.assertEqual(self.text(2, COLUMN_X), "10")

    def test_losing_the_calibration_puts_it_back_to_px_and_disabled(self) -> None:
        self.calibrate("um")
        self.geometry.restore_settings(GeometrySettings(calibration_enabled=False, display_units="px"))
        self.assertEqual(self.panel._unit_toggle.text(), "px")
        self.assertFalse(self.panel._unit_toggle.isEnabled())

    def test_it_keeps_its_own_look_when_the_theme_switches(self) -> None:
        set_active_theme(LSPRI_BRIGHT_THEME)
        self.panel.refresh_theme()
        ribbon_style = transparent_icon_button_stylesheet()
        self.assertEqual(self.panel._unit_toggle.styleSheet(), ribbon_style)
        self.assertEqual(self.panel._delete_button.styleSheet(), ribbon_style, "the ribbon's icon-button style, not a private one")


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
        self.assertEqual(self.toolbox.roi_by_id(2).shape.diameter_px, 9.0)
        self.assertEqual(self.toolbox.roi_by_id(3).shape.diameter_px, 6.0)
        undo_manager.undo()
        self.assertEqual(self.toolbox.roi_by_id(2).shape.diameter_px, 6.0)

    def test_an_edit_applies_to_the_whole_selection_in_one_undo_step(self) -> None:
        self.selection.set_roi_selection({1, 2, 3})
        self.edit(2, COLUMN_SAMPLE, "9")
        self.assertEqual([self.toolbox.roi_by_id(i).shape.diameter_px for i in (1, 2, 3, 4)], [9.0, 9.0, 9.0, 6.0])
        self.assertIn("3 ROIs", self.footer())
        undo_manager.undo()
        self.assertEqual([self.toolbox.roi_by_id(i).shape.diameter_px for i in (1, 2, 3)], [6.0, 6.0, 6.0])

    def test_editing_a_roi_outside_the_selection_edits_only_that_roi(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.edit(4, COLUMN_SAMPLE, "9")
        self.assertEqual([self.toolbox.roi_by_id(i).shape.diameter_px for i in (1, 2, 4)], [6.0, 6.0, 9.0])

    def test_editing_x_of_a_selection_lines_them_up(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.edit(1, COLUMN_X, "0")
        self.assertEqual(self.xs()[:3], [0.0, 0.0, 30.0])
        self.assertEqual([roi.center_y for roi in self.toolbox.rois()][:3], [5.0, 5.0, 5.0], "y untouched")
        self.assertIn("Set x of 2 ROIs", self.messages[-1])

    def test_a_bad_value_is_refused_shown_and_changes_nothing(self) -> None:
        self.edit(2, COLUMN_SAMPLE, "1")
        self.assertEqual(self.toolbox.roi_by_id(2).shape.diameter_px, 6.0)
        self.assertIn("at least 2", self.footer())
        self.assertIn("at least 2", self.messages[-1])
        self.edit(2, COLUMN_SAMPLE, "abc")
        self.assertIn("'abc' is not a number", self.footer())
        self.edit(2, COLUMN_RIM_THICKNESS, "-3")  # a band cannot be less than nothing wide (an offset may be negative: that is a rim starting inside the shape)
        self.assertIn("thickness cannot be negative", self.footer())
        self.assertIsNone(self.toolbox.roi_by_id(2).border)

    def test_a_rim_override_is_stored_for_that_roi(self) -> None:
        self.edit(2, COLUMN_RIM_OFFSET, "10")
        roi = self.toolbox.roi_by_id(2)
        self.assertEqual((roi.border.gap_px, roi.border.thickness_px), (10.0, 4.0))  # the thickness keeps the value it had (the default's)
        self.assertEqual(ring_diameters(roi.shape, roi.border), (26.0, 34.0))  # the same rim as diameters round the 6 px circle
        self.edit(2, COLUMN_RIM_OFFSET, "-2")
        self.assertEqual(self.toolbox.roi_by_id(2).border.gap_px, -2.0, "an offset can be negative")

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


class ExcelStyleEditingTest(_PanelCase):
    """Ctrl+C / Ctrl+V / Ctrl+D on the current cell, and the diameter step."""

    def current(self, roi_id: int, column: int) -> None:
        self.refresh()  # edits rebuild the table after a short pause, which drops the current cell
        self.tree.selectionModel().setCurrentIndex(self.cell(roi_id, column), QItemSelectionModel.SelectionFlag.NoUpdate)

    def diameters(self, *ids: int) -> list[float]:
        return [self.toolbox.roi_by_id(i).shape.diameter_px for i in ids]

    def test_copy_then_paste_sets_that_column_on_every_selected_roi(self) -> None:
        self.edit(5, COLUMN_SAMPLE, "9")
        self.current(5, COLUMN_SAMPLE)
        self.panel._copy_cell()
        self.selection.set_roi_selection({1, 2})
        self.current(1, COLUMN_SAMPLE)
        self.panel._paste_cells()
        self.assertEqual(self.diameters(1, 2, 3), [9.0, 9.0, 6.0])
        undo_manager.undo()
        self.assertEqual(self.diameters(1, 2), [6.0, 6.0], "one undo step")

    def test_paste_with_no_selection_goes_to_the_current_row_only(self) -> None:
        QtWidgets.QApplication.clipboard().setText("8")
        self.current(3, COLUMN_SAMPLE)
        self.panel._paste_cells()
        self.assertEqual(self.diameters(2, 3, 4), [6.0, 8.0, 6.0])

    def test_paste_takes_the_first_value_of_a_spreadsheet_clipboard(self) -> None:
        QtWidgets.QApplication.clipboard().setText("7\t99\n1\t2")
        self.selection.set_roi_selection({1})
        self.current(1, COLUMN_SAMPLE)
        self.panel._paste_cells()
        self.assertEqual(self.diameters(1), [7.0])

    def test_paste_into_the_name_column_is_refused(self) -> None:
        QtWidgets.QApplication.clipboard().setText("7")
        self.current(1, COLUMN_NAME)
        self.panel._paste_cells()
        self.assertIn("Paste works on", self.messages[-1])

    def test_a_bad_clipboard_value_is_refused_and_shown(self) -> None:
        QtWidgets.QApplication.clipboard().setText("abc")
        self.current(1, COLUMN_SAMPLE)
        self.panel._paste_cells()
        self.assertEqual(self.diameters(1), [6.0])
        self.assertIn("not a number", self.messages[-1])

    def test_fill_down_copies_the_topmost_selected_row_to_the_others(self) -> None:
        self.edit(2, COLUMN_SAMPLE, "11")
        self.selection.set_roi_selection({2, 3, 4})
        self.current(4, COLUMN_SAMPLE)
        self.panel._fill_down()
        self.assertEqual(self.diameters(2, 3, 4, 5), [11.0, 11.0, 11.0, 6.0])

    def test_fill_down_needs_two_rows(self) -> None:
        self.selection.set_roi_selection({2})
        self.current(2, COLUMN_SAMPLE)
        self.panel._fill_down()
        self.assertIn("two or more", self.messages[-1])

    def test_ctrl_wheel_step_applies_to_the_selection(self) -> None:
        self.selection.set_roi_selection({1, 2})
        self.panel._step_cell(self.cell(1, COLUMN_SAMPLE), 1, False)
        self.assertEqual(self.diameters(1, 2, 3), [6.5, 6.5, 6.0])

    def test_stepping_below_the_minimum_is_refused_not_clamped(self) -> None:
        self.panel._step_cell(self.cell(1, COLUMN_SAMPLE), -1, True)  # 6 - 5 = 1 px < the 2 px minimum
        self.assertEqual(self.diameters(1), [6.0])
        self.assertIn("at least", self.messages[-1])

    def test_the_editor_steps_a_diameter_with_up_and_down(self) -> None:
        from PyQt6.QtWidgets import QStyleOptionViewItem

        editor = self.tree.itemDelegate().createEditor(self.tree, QStyleOptionViewItem(), self.cell(1, COLUMN_SAMPLE))
        editor.setText("6")
        QTest.keyClick(editor, Qt.Key.Key_Up)
        self.assertEqual(editor.text(), "6.5")
        QTest.keyClick(editor, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(editor.text(), "1.5")

    def test_the_editor_of_a_position_does_not_step(self) -> None:
        from PyQt6.QtWidgets import QStyleOptionViewItem

        editor = self.tree.itemDelegate().createEditor(self.tree, QStyleOptionViewItem(), self.cell(1, COLUMN_X))
        editor.setText("6")
        QTest.keyClick(editor, Qt.Key.Key_Up)
        self.assertEqual(editor.text(), "6")

    def test_the_step_is_a_whole_micrometre_in_micrometres(self) -> None:
        from lspr_imaging_app.panels.roi_table.rows import micrometers, step_text

        self.assertEqual(step_text("3", 1, False, micrometers(0.5)), "4")  # no decimals in um, so 1 um per step
        self.assertEqual(step_text("0", -1, False, micrometers(0.5)), "0", "never below zero")
        self.assertIsNone(step_text("x", 1, False, micrometers(0.5)))


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


class DeleteTest(_PanelCase):
    def test_delete_removes_the_selected_rois(self) -> None:
        self.select_in_view(self.cell(2, 0), self.cell(3, 0))
        self.panel._delete_selected()
        self.assertEqual(self.xs(), [10.0, 40.0, 50.0])

    def test_with_a_group_header_selected_delete_removes_the_group_and_its_rois(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        self.select_in_view(self.header("group_1"))
        self.panel._delete_selected()
        self.assertEqual(self.toolbox.groups(), ())
        self.assertEqual(len(self.toolbox.rois()), 3)

    def test_ungroup_button_dissolves_selected_groups_and_keeps_their_rois(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        self.select_in_view(self.header("group_1"))
        self.assertTrue(self.panel._ungroup_button.isEnabled())
        self.panel._ungroup_button.click()
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
        self.assertEqual(self.toolbox.roi_by_id(1).shape.diameter_px, 20.0)
        self.assertIsNone(self.toolbox.roi_by_id(1).border)
        self.assertEqual(self.toolbox.roi_by_id(2).shape.diameter_px, 12.0)

    def test_shift_position_in_the_display_unit(self) -> None:
        self.selection.set_roi_selection({1, 2})
        with mock.patch.object(ShiftDialog, "exec", return_value=QDialog.DialogCode.Accepted), mock.patch.object(
            ShiftDialog, "shift", return_value=(2.0, -1.0)
        ):
            self.panel._shift_selected()
        self.assertEqual(self.xs()[:3], [12.0, 22.0, 30.0])
        self.assertEqual([roi.center_y for roi in self.toolbox.rois()][:3], [4.0, 4.0, 5.0])

    def test_group_menu_offers_group_actions_and_ungrouped_only_expand_collapse(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        menu = QMenu()
        self.panel._fill_group_menu(menu, self.header("group_1"))
        labels = [a.text() for a in menu.actions() if a.text()]
        self.assertIn("Rename…", labels)
        self.assertIn("Ungroup (keeps the ROIs)", labels)
        self.assertIn("Delete group and its ROIs", labels)
        self.assertNotIn("Select members", labels)  # a group's members are always selected with it
        self.assertNotIn("Move group", labels, "one group has nowhere to move: no Move entry (2026-11-19: what cannot be done is left out)")
        ungrouped = self.model.group_index(None)
        menu = QMenu()
        self.panel._fill_group_menu(menu, ungrouped)
        self.assertEqual([a.text() for a in menu.actions() if a.text()], ["Expand", "Collapse"])

    @staticmethod
    def _entries(menu):
        """Every entry of a menu and its submenus (not the separators)."""
        found = []
        for action in menu.actions():
            if action.isSeparator():
                continue
            found.append(action)
            if action.menu() is not None:
                found.extend(CommandTest._entries(action.menu()))
        return found

    def test_every_entry_of_the_roi_and_group_menus_wears_an_icon(self) -> None:
        """2026-11-19 (maintainer: icons before the texts). The names of the groups under "Add to group" are the one kind of entry with no command to picture."""
        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        self.selection.set_roi_selection({1, 3})
        menu = QMenu()
        self.panel._fill_roi_menu(menu)
        add_to = next(a for a in menu.actions() if a.text() == "Add to group").menu()
        bare = [a.text() for a in self._entries(menu) if a.icon().isNull() and a not in add_to.actions()]
        self.assertEqual(bare, [])
        menu = QMenu()
        self.panel._fill_group_menu(menu, self.header("group_1"))
        self.assertEqual([a.text() for a in self._entries(menu) if a.icon().isNull()], [])

    def _texts(self, fill, *args) -> list[str]:
        menu = QMenu()
        fill(menu, *args)
        return [a.text() for a in menu.actions() if a.text()]

    def test_the_roi_menu_offers_only_what_the_selection_allows(self) -> None:
        """2026-11-19 (maintainer): an entry whose prerequisite is not met is not in the menu."""
        self.selection.set_roi_selection({1})
        texts = self._texts(self.panel._fill_roi_menu)
        self.assertNotIn("Remove from group", [t.split("\t")[0] for t in texts], "no selected ROI is grouped")
        self.assertNotIn("Clear color", texts, "no selected ROI has a colour of its own")
        self.assertNotIn("Add to group", texts, "there is no group to add to")
        self.assertIn("Delete ROI", texts)
        self.toolbox.group_rois((1, 2), "A")
        self.toolbox.set_roi_colors((1,), "#112233")
        self.refresh()
        texts = self._texts(self.panel._fill_roi_menu)
        self.assertIn("Remove from group\tCtrl+Shift+G", texts)
        self.assertIn("Clear color", texts)
        self.assertIn("Add to group", texts)

    def test_with_no_roi_selected_the_roi_menu_offers_no_roi_command(self) -> None:
        """A locked, empty selection: the menu a right click on a row opens then has nothing that acts on ROIs."""
        self.selection.set_roi_selection(set())
        self.assertEqual(self._texts(self.panel._fill_roi_menu), [])

    def test_the_group_menu_offers_the_moves_that_would_change_something(self) -> None:
        self._three_groups()
        def move(group_id):
            menu = self._menu_of(group_id)
            return [a.text() for a in next(x for x in menu.actions() if x.text() == "Move group").menu().actions()]

        self.assertEqual(move("group_1"), ["Down", "To bottom"], "the first group cannot go up")
        self.assertEqual(move("group_2"), ["To top", "Up", "Down", "To bottom"])
        self.assertEqual(move("group_3"), ["To top", "Up"], "the last group cannot go down")

    def _menu_of(self, group_id: str):
        menu = QMenu()
        self.panel._fill_group_menu(menu, self.header(group_id))
        return menu

    def test_rename_is_left_out_for_several_groups_and_expand_in_the_groups_only_view(self) -> None:
        self._three_groups()
        texts = self._texts(self.panel._fill_group_menu, self.header("group_1"))
        self.assertIn("Rename…", texts)
        self.assertIn("Expand", texts)
        self.select_in_view(self.header("group_1"), self.header("group_2"))
        self.assertNotIn("Rename…", self._texts(self.panel._fill_group_menu, self.header("group_1")))
        self.panel.set_group_only(True)
        self.assertNotIn("Expand", self._texts(self.panel._fill_group_menu, self.header("group_1")))

    def test_the_roi_menu_shows_the_group_keys(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        self.refresh()
        self.selection.set_roi_selection({1, 3})
        menu = QMenu()
        self.panel._fill_roi_menu(menu)
        texts = [a.text() for a in menu.actions()]
        self.assertIn("Group 2 ROIs…\tCtrl+G", texts)
        self.assertIn("Remove from group\tCtrl+Shift+G", texts)

    def test_the_header_menu_entries_wear_icons(self) -> None:
        bare = [a.text() for a in self._entries(self.panel._view_menu) if a.icon().isNull()]
        self.assertEqual(bare, [])

    def test_the_roi_menu_leaves_out_moves_when_not_sorted_by_number(self) -> None:
        self.selection.set_roi_selection({2})
        menu = QMenu()
        self.panel._fill_roi_menu(menu)
        move = next(a for a in menu.actions() if a.text() == "Move")
        self.assertEqual([a.text() for a in move.menu().actions()], ["To top", "Up", "Down", "To bottom"])
        self.panel._on_header_clicked(COLUMN_Y)
        menu = QMenu()
        self.panel._fill_roi_menu(menu)
        self.assertNotIn("Move", [a.text() for a in menu.actions()], "sorted by something else: Move is left out, not greyed")

    def test_sorting_by_label_orders_the_groups_and_keeps_collapsed_groups_collapsed(self) -> None:
        self.toolbox.group_rois((1,), "B")
        self.toolbox.group_rois((2,), "A")
        self.refresh()
        self.panel._tree.collapse(self.header("group_1"))
        self.panel._on_header_clicked(COLUMN_NAME)
        names = [self.model.data(h) for h in self.model.group_headers()]
        self.assertEqual(names[:2], ["A", "B"])
        self.assertFalse(self.panel._tree.isExpanded(self.model.group_index("group_1")))
        self.assertEqual([g.name for g in self.toolbox.groups()], ["B", "A"])  # the real order is untouched

    def test_group_menu_expands_and_collapses_the_selected_groups(self) -> None:
        self.toolbox.group_rois((1,), "A")
        self.refresh()
        header = self.header("group_1")
        menu = QMenu()
        self.panel._fill_group_menu(menu, header)
        action = {a.text(): a for a in menu.actions()}
        action["Collapse"].trigger()
        self.assertFalse(self.panel._tree.isExpanded(self.header("group_1")))
        action["Collapse"].trigger()  # already collapsed: stays so
        self.assertFalse(self.panel._tree.isExpanded(self.header("group_1")))
        action["Expand"].trigger()
        self.assertTrue(self.panel._tree.isExpanded(self.header("group_1")))

    def test_title_bar_counts_groups(self) -> None:
        self.toolbox.group_rois((1,), "A")
        self.refresh()
        self.assertIn("1 group", self.panel.status_text())

    def test_moving_a_group_up_and_down(self) -> None:
        self.toolbox.group_rois((1,), "A")
        self.toolbox.group_rois((2,), "B")
        self.panel._move_groups(["group_2"], -1)
        self.assertEqual([g.name for g in self.toolbox.groups()], ["B", "A"])

    def _three_groups(self) -> None:
        self.toolbox.group_rois((1,), "A")
        self.toolbox.group_rois((2,), "B")
        self.toolbox.group_rois((3,), "C")
        self.refresh()

    def test_several_selected_groups_move_together(self) -> None:
        self._three_groups()
        self.select_in_view(self.header("group_2"), self.header("group_3"))
        self.assertTrue(self.panel._up_button.isEnabled())
        self.panel._up_button.click()
        self.assertEqual([g.name for g in self.toolbox.groups()], ["B", "C", "A"])
        self.refresh()
        self.select_in_view(self.header("group_2"), self.header("group_3"))
        self.panel._down_button.click()
        self.assertEqual([g.name for g in self.toolbox.groups()], ["A", "B", "C"])

    def test_moving_a_collapsed_group_keeps_it_collapsed_and_selected(self) -> None:
        self._three_groups()
        self.tree.collapse(self.header("group_2"))
        self.select_in_view(self.header("group_2"))
        self.panel._up_button.click()
        self.refresh()
        self.assertFalse(self.tree.isExpanded(self.header("group_2")))
        self.assertEqual(self.panel._selected_group_ids(), ["group_2"])
        self.panel._up_button.click()  # already on top: still enabled, nothing changes
        self.assertEqual([g.name for g in self.toolbox.groups()], ["B", "A", "C"])

    def test_groups_move_to_the_top_and_bottom(self) -> None:
        self._three_groups()
        self.panel._move_groups_to_edge(["group_3"], top=True)
        self.assertEqual([g.name for g in self.toolbox.groups()], ["C", "A", "B"])
        self.panel._move_groups_to_edge(["group_3", "group_1"], top=False)
        self.assertEqual([g.name for g in self.toolbox.groups()], ["B", "C", "A"])

    def test_several_selected_groups_are_recoloured_and_deleted_together(self) -> None:
        self._three_groups()
        self.select_in_view(self.header("group_1"), self.header("group_3"))
        self.panel._choose_color = lambda *_args: "#112233"
        self.panel._color_selected()
        colors = {g.name: g.sample_color_hex for g in self.toolbox.groups()}
        self.assertEqual(colors["A"], "#112233")
        self.assertEqual(colors["C"], "#112233")
        self.assertNotEqual(colors["B"], "#112233")
        menu = QMenu()
        self.panel._fill_group_menu(menu, self.header("group_1"))
        delete = next(a for a in menu.actions() if a.text().startswith("Ungroup"))
        delete.trigger()
        self.assertEqual([g.name for g in self.toolbox.groups()], ["B"])


class RememberedStateTest(_PanelCase):
    def test_view_mode_sort_and_widths_are_restored_and_saved(self) -> None:
        store = UiStateStore({
            "roi_table/view_mode": "geometry",
            "roi_table/sort": [COLUMN_X, True],
            "roi_table/column_widths": [70, 90, 61, 62, 63, 64, 65, 40],
        })
        self.panel.restore_ui_state(store)
        self.assertEqual(self.panel._view_mode, "geometry")
        self.assertEqual(self.model.sort_state(), (COLUMN_X, True))
        self.assertEqual(self.tree.columnWidth(COLUMN_X), 61)
        self.assertEqual(self.tree.columnWidth(COLUMN_RIM_THICKNESS), 65)
        self.panel._view_actions["simple"].trigger()
        self.assertEqual(store.get("roi_table/view_mode"), "simple")
        self.assertEqual(store.get("roi_table/sort"), [COLUMN_ID, False], "a sort by a hidden column goes back to #")
        self.panel._on_header_clicked(COLUMN_NAME)
        self.assertEqual(store.get("roi_table/sort"), [COLUMN_NAME, False])
        self.tree.setColumnWidth(COLUMN_NAME, 77)
        self.assertEqual(store.get("roi_table/column_widths")[COLUMN_NAME], 77)
        self.assertEqual(store.get("roi_table/column_widths")[COLUMN_RIM_THICKNESS], 65, "a hidden column keeps its width")

    def test_a_saved_sort_by_a_column_the_saved_mode_hides_is_ignored(self) -> None:
        self.panel.restore_ui_state(UiStateStore({"roi_table/view_mode": "simple", "roi_table/sort": [COLUMN_X, True]}))
        self.assertEqual(self.model.sort_state(), (COLUMN_ID, False))

    def test_widths_saved_before_the_geometry_columns_existed_are_still_read(self) -> None:
        self.panel.restore_ui_state(UiStateStore({"roi_table/column_widths": [70, 90, 61, 62, 63, 64, 65]}))
        self.assertEqual(self.tree.columnWidth(COLUMN_ID), max(70, suggested_column_widths(self.tree.fontMetrics())[COLUMN_ID]))
        self.assertGreater(self.tree.columnWidth(COLUMN_SHAPE), 0)

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


class GroupOnlyViewTest(_PanelCase):
    """The Groups-only view (2026-10-24): every group stays collapsed; what the user had open comes back when it is switched off."""

    def setUp(self) -> None:
        super().setUp()
        self.toolbox.group_rois((1, 2), "A")
        self.toolbox.group_rois((3,), "B")
        self.refresh()

    def test_switching_on_collapses_every_group_and_off_gives_back_the_open_ones(self) -> None:
        self.tree.collapse(self.header("group_2"))  # the user closed B by hand
        self.panel.set_group_only(True)
        self.assertFalse(self.tree.isExpanded(self.header("group_1")))
        self.assertFalse(self.tree.isExpanded(self.header("group_2")))
        self.panel.set_group_only(False)
        self.assertTrue(self.tree.isExpanded(self.header("group_1")))
        self.assertFalse(self.tree.isExpanded(self.header("group_2")))

    def test_a_group_that_is_opened_while_locked_closes_again(self) -> None:
        self.panel.set_group_only(True)
        self.tree.expand(self.header("group_1"))
        _APP.processEvents()
        self.assertFalse(self.tree.isExpanded(self.header("group_1")))
        self.panel.refresh_now()
        self.assertFalse(self.tree.isExpanded(self.header("group_1")))

    def test_selecting_a_roi_elsewhere_does_not_open_its_group_while_locked(self) -> None:
        self.panel.set_group_only(True)
        self.selection.set_roi_selection({1})
        self.assertFalse(self.tree.isExpanded(self.header("group_1")))

    def test_it_is_reported_remembered_and_restored(self) -> None:
        reported: list[bool] = []
        self.panel.group_only_changed.connect(reported.append)
        store = UiStateStore({})
        self.panel.restore_ui_state(store)
        self.panel.set_group_only(True)
        self.assertEqual((reported, store.get("roi_table/group_only")), ([True], True))
        later = RoiTablePanel(self.toolbox, self.selection, self.geometry)
        later.restore_ui_state(store)
        self.assertTrue(later.group_only())
        later.deleteLater()

    def test_the_dock_title_is_the_switch(self) -> None:
        from lspr_imaging_app.panels.dock_container import PanelContainer, TitleChoice

        dock = PanelContainer("ROI / Groups", self.panel)
        dock.set_title_choices((TitleChoice("ROI", "r"), TitleChoice("Groups", "g", on_double_click=True)), current=0)
        picked: list[int] = []
        dock.title_choice_picked.connect(picked.append)
        roi_word, groups_word = dock._title_words
        groups_word.clicked.emit()  # a single click on Groups does nothing
        self.assertEqual(picked, [])
        groups_word.double_clicked.emit()
        self.assertEqual(picked, [1])
        dock.set_title_current(1)
        roi_word.clicked.emit()
        self.assertEqual(picked, [1, 0])
        dock.deleteLater()

    def test_a_dock_made_after_the_restore_starts_on_the_saved_view_and_can_leave_it(self) -> None:
        """The reported bug (2026-11-05): the panel restores "Groups-only" from the saved settings *before* the dock exists, so the
        title used to start on "ROI" while the table was locked, and neither word could switch it back."""
        from lspr_imaging_app.app_rewrite import _wire_roi_table_title
        from lspr_imaging_app.panels.dock_container import PanelContainer

        self.panel.restore_ui_state(UiStateStore({"roi_table/group_only": True}))
        self.assertTrue(self.panel.group_only())
        dock = PanelContainer("ROI / Groups", self.panel)
        _wire_roi_table_title(dock, self.panel)
        self.assertEqual(dock._title_current, 1)  # "Groups" is highlighted, as the table really is
        dock._title_words[0].clicked.emit()  # a click on "ROI" leaves the locked view
        self.assertFalse(self.panel.group_only())
        self.assertEqual(dock._title_current, 0)
        self.assertTrue(self.tree.isExpanded(self.header("group_1")))
        dock._title_words[1].double_clicked.emit()  # and a double click on "Groups" goes back
        self.assertTrue(self.panel.group_only())
        self.assertEqual(dock._title_current, 1)
        dock.deleteLater()


class LayoutAndPaintTest(_PanelCase):
    def test_numeric_columns_leave_room_around_their_widest_text(self) -> None:
        """Measured against the font in use here (offscreen Qt may fall back
        to a different font than the app's on a real display)."""
        metrics = QFontMetrics(self.tree.font())
        widths = suggested_column_widths(metrics)
        for column in (COLUMN_X, COLUMN_Y, COLUMN_SAMPLE, COLUMN_RIM_OFFSET, COLUMN_RIM_THICKNESS):
            self.assertGreaterEqual(widths[column] - metrics.horizontalAdvance("9999.9" if column in (COLUMN_X, COLUMN_Y) else "999.9"), 12, column)
        from lspr_imaging_app.panels.roi_table.view import title_width

        for column, title in ((COLUMN_SAMPLE, "D_s"), (COLUMN_RIM_OFFSET, "d"), (COLUMN_RIM_THICKNESS, "w")):
            self.assertEqual(COLUMN_TITLES[column], title)
            self.assertGreaterEqual(widths[column] - title_width(metrics, title), 20, f"{title}: room for the sort arrow")

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

    @staticmethod
    def _painted_extent(button: QToolButton) -> int:
        """Width in pixels of what the button actually paints for its icon
        (the pixels that differ from the button's own background).

        Read as straight (not pre-multiplied) colours: on a pre-multiplied picture `pixel()` darkens the soft edge
        pixels, and an edge of one saturated hue (the colour button's rainbow droplet: green on the left, blue on the
        right) then falls under the contrast limit that a grey icon's edge passes, so the same-sized icon measured 2 px
        narrower."""
        image = button.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
        background = QColor(image.pixel(1, 1))
        columns = [
            x
            for x in range(image.width())
            for y in range(image.height())
            if sum(abs(a - b) for a, b in zip(QColor(image.pixel(x, y)).getRgb()[:3], background.getRgb()[:3])) > 120
        ]
        return (max(columns) - min(columns) + 1) if columns else 0

    def test_toolbar_icons_are_painted_as_large_as_the_ribbons(self) -> None:
        """Regression (maintainer's screenshot): under the app-wide style a
        private button stylesheet without `padding: 0` shrank a 22 px icon to
        about 4 px. Measured on what is painted, next to a button built exactly
        as the Image ribbon builds its own; needs the app theme applied, which
        is where the shrinking came from."""
        saved = (_APP.styleSheet(), _APP.palette(), _APP.style().objectName())
        apply_app_theme(_APP, LSPRI_DARK_THEME)
        set_active_theme(LSPRI_DARK_THEME)
        try:
            self.panel.refresh_theme()
            self.panel.resize(460, 300)
            self.panel.show()
            buttons = {
                "group": self.panel._group_button, "ungroup": self.panel._ungroup_button, "color": self.panel._color_button,
                "up": self.panel._up_button, "down": self.panel._down_button, "delete": self.panel._delete_button,
            }
            _APP.processEvents()
            for name, button in buttons.items():
                button.setEnabled(True)  # a disabled icon is greyed, not smaller; measure them all alike
                reference = QToolButton()  # the same icon, built exactly as the Image ribbon builds its own
                reference.setIcon(
                    load_tabler_icon(str(button.property("icon_name")), color=get_active_theme().text_secondary, size=44, stroke_width=2.1)
                )
                style_general_icon_button(reference)
                reference.show()
                _APP.processEvents()
                expected = self._painted_extent(reference)
                self.assertGreaterEqual(expected, 10, f"{name}: the reference itself must be a normal-sized icon")
                # 2 px of slack (was 1 at the 22 px ribbon icons; ribbon icons are 18 px since 2026-10-18). The rainbow
                # colour droplet's saturated edge falls under the contrast limit (see `_painted_extent`), which at
                # 18 px costs it 2 px against the grey reference. Checked then: every button here and its reference
                # have the same icon size (18) and an identical painted alpha extent (18 px) - only this contrast
                # reading of the gradient differs. A real regression (the 22 px icon shrunk to ~4 px) still fails.
                self.assertGreaterEqual(self._painted_extent(button), expected - 2, f"{name} icon is smaller than the ribbon's")
                reference.hide()
        finally:
            self.panel.hide()
            _APP.setStyleSheet(saved[0])
            _APP.setPalette(saved[1])
            _APP.setStyle(saved[2])

    def test_the_toolbar_buttons_get_icons_and_follow_the_theme(self) -> None:
        for button in (self.panel._group_button, self.panel._delete_button):
            self.assertFalse(button.icon().isNull())
        set_active_theme(LSPRI_BRIGHT_THEME)
        self.panel.refresh_theme()
        self.assertFalse(self.panel._group_button.icon().isNull())



_MOVE = Qt.DropAction.MoveAction


class DragAndDropTest(_PanelCase):
    """What Qt calls during a drag, driven directly: the real mouse gesture
    cannot be simulated headless, so `dropMimeData` is what these pin."""

    def mime(self, *roi_ids: int) -> QMimeData:
        indexes = [self.model.roi_index(roi_id, column) for roi_id in roi_ids for column in range(3)]
        return self.model.mimeData(indexes)

    def drop(self, roi_ids, row: int, parent: QModelIndex = QModelIndex()) -> bool:
        return self.model.dropMimeData(self.mime(*roi_ids), _MOVE, row, -1, parent)

    def grouped(self) -> None:
        self.toolbox.group_rois((1, 3, 5), "odd")
        self.toolbox.group_rois((2,), "two")
        self.refresh()

    # -- what can be dragged and dropped where ---------------------------------------------

    def test_roi_rows_can_be_dragged_and_headers_accept_drops(self) -> None:
        self.grouped()
        roi_flags = self.model.flags(self.cell(1, COLUMN_NAME))
        self.assertTrue(roi_flags & Qt.ItemFlag.ItemIsDragEnabled)
        self.assertFalse(roi_flags & Qt.ItemFlag.ItemIsDropEnabled, "a drop lands between ROI rows, not on one")
        header_flags = self.model.flags(self.header("group_1"))
        self.assertTrue(header_flags & Qt.ItemFlag.ItemIsDropEnabled)
        self.assertFalse(header_flags & Qt.ItemFlag.ItemIsDragEnabled)

    def test_the_view_is_set_up_for_dragging(self) -> None:
        self.assertTrue(self.tree.dragEnabled())
        self.assertTrue(self.tree.acceptDrops())
        self.assertTrue(self.tree.showDropIndicator())
        self.assertEqual(self.tree.dragDropMode(), type(self.tree.dragDropMode()).DragDrop)
        self.assertGreater(self.tree.autoExpandDelay(), 0)

    def test_the_drag_carries_the_roi_ids_once_each(self) -> None:
        data = self.mime(4, 2, 4)
        self.assertEqual(self.model.mimeTypes(), [MIME_TYPE])
        import json

        self.assertEqual(json.loads(bytes(data.data(MIME_TYPE)).decode()), [2, 4])

    def test_group_headers_are_not_dragged(self) -> None:
        self.grouped()
        data = self.model.mimeData([self.header("group_1")])
        import json

        self.assertEqual(json.loads(bytes(data.data(MIME_TYPE)).decode()), [])

    def test_which_drops_are_accepted(self) -> None:
        data = self.mime(2)
        self.assertTrue(self.model.canDropMimeData(data, _MOVE, 1, -1, QModelIndex()), "between flat rows")
        self.assertTrue(self.model.canDropMimeData(data, _MOVE, -1, -1, QModelIndex()), "empty space: the end of the flat list")
        self.grouped()
        header = self.header("group_1")
        self.assertTrue(self.model.canDropMimeData(data, _MOVE, -1, -1, header), "on a header")
        self.assertTrue(self.model.canDropMimeData(data, _MOVE, 1, -1, header), "between a group's rows")
        self.assertFalse(self.model.canDropMimeData(data, _MOVE, 1, -1, QModelIndex()), "between two headers")
        self.assertFalse(self.model.canDropMimeData(data, _MOVE, -1, -1, self.cell(1, 0)), "on a ROI row")
        other = QMimeData()
        other.setText("not rois")
        self.assertFalse(self.model.canDropMimeData(other, _MOVE, -1, -1, header))
        self.assertFalse(self.model.canDropMimeData(data, Qt.DropAction.CopyAction, -1, -1, header))

    def test_a_drop_never_asks_qt_to_remove_the_source_rows(self) -> None:
        self.assertFalse(self.drop([2], 0), "the new arrangement comes back from the toolbox, not from Qt")
        self.assertEqual(len(self.toolbox.rois()), 5)

    def test_rubbish_mime_data_is_ignored(self) -> None:
        bad = QMimeData()
        bad.setData(MIME_TYPE, b"not json")
        self.assertFalse(self.model.dropMimeData(bad, _MOVE, 0, -1, QModelIndex()))
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0])

    # -- reordering ---------------------------------------------------------------------------

    def test_dragging_a_roi_to_the_top_of_the_flat_list(self) -> None:
        self.drop([4], 0)
        self.assertEqual(self.xs(), [40.0, 10.0, 20.0, 30.0, 50.0])

    def test_dragging_a_roi_down_to_before_a_later_row(self) -> None:
        self.drop([1], 3)  # dropped just above the row that shows ROI 4
        self.assertEqual(self.xs(), [20.0, 30.0, 10.0, 40.0, 50.0])

    def test_dropping_on_empty_space_moves_to_the_end_and_a_block_stays_together(self) -> None:
        self.drop([2, 3], -1)
        self.assertEqual(self.xs(), [10.0, 40.0, 50.0, 20.0, 30.0])

    def test_dropping_a_roi_where_it_already_is_changes_nothing(self) -> None:
        last_step = undo_manager.undo_label
        self.drop([3], 2)
        self.drop([3], 3)
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0])
        self.assertEqual(undo_manager.undo_label, last_step, "nothing was recorded")

    def test_a_reorder_by_dragging_undoes_in_one_step(self) -> None:
        self.drop([4, 5], 0)
        self.assertEqual(self.xs(), [40.0, 50.0, 10.0, 20.0, 30.0])
        undo_manager.undo()
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0])

    def test_dragging_within_a_group_stays_inside_it(self) -> None:
        self.grouped()
        self.drop([5], 0, self.header("group_1"))  # to the top of the group [1, 3, 5]
        self.assertEqual(self.xs(), [50.0, 20.0, 10.0, 40.0, 30.0])
        self.assertEqual(self.toolbox.groups()[0].area_roi_ids, [1, 3, 5])

    def test_reordering_by_dragging_needs_the_list_sorted_by_number(self) -> None:
        self.panel._on_header_clicked(COLUMN_X)
        self.drop([4], 0)
        self.assertIn("Sort by #", self.footer())
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0])


    # -- groups ------------------------------------------------------------------------------------

    def test_dropping_on_a_header_puts_the_rois_in_that_group(self) -> None:
        self.grouped()
        self.drop([4], -1, self.header("group_2"))
        self.assertEqual(self.toolbox.groups()[1].area_roi_ids, [2, 4])
        self.assertIsNotNone(self.toolbox.roi_by_id(4).sample_color_hex, "it gets a tint of the group colour")
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0], "membership only: nobody was renumbered")

    def test_putting_rois_in_a_group_works_whatever_the_sort(self) -> None:
        self.grouped()
        self.panel._on_header_clicked(COLUMN_X)
        self.drop([4], -1, self.header("group_2"))
        self.assertEqual(self.toolbox.groups()[1].area_roi_ids, [2, 4])

    def test_dropping_between_the_rows_of_another_group_puts_the_roi_in_that_group(self) -> None:
        self.grouped()
        self.drop([2], 1, self.header("group_1"))  # ROI 2 comes from the other group
        self.assertEqual(self.toolbox.groups()[0].area_roi_ids, [1, 2, 3, 5])
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0], "put in the group, not renumbered")

    def test_dropping_on_ungrouped_takes_rois_out_of_their_groups(self) -> None:
        self.grouped()
        self.drop([1, 3], -1, self.model.group_index(None))
        self.assertEqual([g.area_roi_ids for g in self.toolbox.groups()], [[5], [2]])
        self.assertIsNone(self.toolbox.roi_by_id(1).sample_color_hex)

    def test_a_drop_into_an_unknown_group_is_reported_not_swallowed(self) -> None:
        self.grouped()
        from lspr_imaging_app.panels.roi_table.model import DropTarget

        self.panel._on_roi_dropped([1], DropTarget("header", "group_404"))
        self.assertIn("group_404", self.footer())

if __name__ == "__main__":
    unittest.main()


class ViewModeTest(_PanelCase):
    """The view-mode button and the shape-aware cells."""

    def shown(self, column: int) -> bool:
        return not self.tree.isColumnHidden(column)

    def shown_left_to_right(self) -> list[int]:
        return sorted((c for c in range(COLUMN_COUNT) if self.shown(c)), key=self.tree.header().visualIndex)

    def show_panel(self) -> None:
        self.panel.resize(700, 300)
        self.panel.show()
        _APP.processEvents()
        self.addCleanup(self.panel.hide)

    def test_geometry_is_the_default_and_simple_shows_only_id_and_label(self) -> None:
        geometry = [COLUMN_ID, COLUMN_X, COLUMN_Y, COLUMN_SHAPE, COLUMN_AREA, COLUMN_SAMPLE, COLUMN_RIM_OFFSET, COLUMN_RIM_THICKNESS]
        self.assertEqual(self.shown_left_to_right(), geometry)
        self.panel._view_actions["simple"].trigger()
        self.assertEqual(self.shown_left_to_right(), [COLUMN_ID, COLUMN_NAME])
        self.panel._view_actions["geometry"].trigger()
        self.assertEqual(self.shown_left_to_right(), geometry)

    def test_the_toolbar_button_toggles_geometry_and_simple_and_follows_the_menu(self) -> None:
        button = self.panel._view_toggle
        layout = self.panel.layout().itemAt(0).layout()
        widgets = [w for w in (layout.itemAt(i).widget() for i in range(layout.count())) if w is not None]  # (spacers have no widget)
        self.assertEqual(widgets[widgets.index(self.panel._delete_button) + 1], self.panel._view_toggle, "right behind the delete button")
        self.assertTrue(button.isChecked())  # Geometry is the default
        button.click()
        self.assertEqual(self.panel._view_mode, "simple")
        self.assertEqual(self.shown_left_to_right(), [COLUMN_ID, COLUMN_NAME])
        self.assertIn("Simple view", button.toolTip())
        button.click()
        self.assertEqual(self.panel._view_mode, "geometry")
        self.panel._view_actions["simple"].trigger()  # a choice made in the header menu moves the button
        self.assertFalse(button.isChecked())
        self.panel._view_actions["geometry"].trigger()
        self.assertTrue(button.isChecked())

    def test_a_right_click_on_the_headers_opens_the_view_menu(self) -> None:
        from unittest import mock

        header = self.tree.header()
        self.assertEqual(header.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
        with mock.patch.object(self.panel._view_menu, "exec") as opened:
            header.customContextMenuRequested.emit(header.rect().center())
        opened.assert_called_once()

    def test_the_unit_toggle_is_only_shown_with_the_geometry_columns(self) -> None:
        self.show_panel()
        self.assertTrue(self.panel._unit_toggle.isVisible())
        self.panel._view_actions["simple"].trigger()
        self.assertFalse(self.panel._unit_toggle.isVisible())
        self.panel._view_actions["geometry"].trigger()
        self.assertTrue(self.panel._unit_toggle.isVisible())

    def test_the_view_menu_has_two_exclusive_choices(self) -> None:
        actions = [self.panel._view_actions[mode] for mode in ("simple", "geometry")]  # (the menu's other checkable entries, the Linked-view switch since phase 3, are not choices of the columns)
        self.assertEqual([a.isChecked() for a in actions], [False, True])
        actions[0].trigger()
        self.assertEqual([a.isChecked() for a in actions], [True, False])
        self.assertEqual(self.panel._view_mode, "simple")

    def test_switching_to_a_mode_that_hides_the_sorted_column_goes_back_to_number_order(self) -> None:
        self.panel._on_header_clicked(COLUMN_X)
        self.assertEqual(self.model.sort_state(), (COLUMN_X, False))
        self.panel._view_actions["simple"].trigger()
        self.assertEqual(self.model.sort_state(), (COLUMN_ID, False))

    def test_the_shape_column_shows_the_shape_and_is_not_editable(self) -> None:
        self.toolbox.roi_by_id(1).border = NO_BORDER
        self.refresh()
        self.assertEqual(self.text(1, COLUMN_SHAPE), "Circle")
        self.assertEqual(self.text(2, COLUMN_SHAPE), "Circle with rim")
        self.assertFalse(self.model.flags(self.cell(1, COLUMN_SHAPE)) & Qt.ItemFlag.ItemIsEditable)
        tip = self.model.headerData(COLUMN_SHAPE, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole)
        self.assertTrue("Amber" in tip and "Green" in tip and "rim" in tip)

    def test_a_disk_has_no_ring_cells_and_a_mask_no_diameter_cells(self) -> None:
        self.toolbox.roi_by_id(1).border = NO_BORDER
        self.toolbox.roi_by_id(2).shape = MaskShape(RoiMask(0, 0, np.ones((2, 2), dtype=bool)))
        self.refresh()
        editable = Qt.ItemFlag.ItemIsEditable
        self.assertEqual([self.text(1, c) for c in (COLUMN_SAMPLE, COLUMN_RIM_OFFSET, COLUMN_RIM_THICKNESS)], ["6.0", "", ""])
        self.assertTrue(self.model.flags(self.cell(1, COLUMN_SAMPLE)) & editable)
        self.assertFalse(self.model.flags(self.cell(1, COLUMN_RIM_OFFSET)) & editable)
        self.assertEqual(self.text(2, COLUMN_SAMPLE), "mask")
        self.assertFalse(self.model.flags(self.cell(2, COLUMN_SAMPLE)) & editable)
        self.assertTrue(self.model.flags(self.cell(2, COLUMN_X)) & editable, "a mask ROI still has a position")

    def test_setting_a_rim_thickness_skips_a_roi_with_no_rim(self) -> None:
        self.toolbox.roi_by_id(1).border = NO_BORDER
        self.refresh()
        self.panel._set_numeric_cells([1, 2], COLUMN_RIM_THICKNESS, "40")
        self.assertEqual(self.toolbox.roi_by_id(1).border, NO_BORDER)  # no rim to set
        roi = self.toolbox.roi_by_id(2)
        self.assertEqual((roi.border.gap_px, roi.border.thickness_px), (4.0, 40.0))
        self.panel._set_numeric_cells([1], COLUMN_RIM_THICKNESS, "41")
        self.assertIn("no rim to set", self.footer())

    def test_painting_every_view_does_not_raise(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        self.toolbox.roi_by_id(3).border = NO_BORDER
        self.toolbox.roi_by_id(4).shape = MaskShape(RoiMask(0, 0, np.ones((2, 2), dtype=bool)))
        self.toolbox.roi_by_id(5).shape = Rectangle(8.0, 4.0, 0.0)
        self.refresh()
        self.show_panel()
        for mode in ("geometry", "simple", "geometry"):
            self.panel._view_actions[mode].trigger()
            _APP.processEvents()
            self.assertFalse(self.tree.viewport().grab().isNull())


class AreaColumnTest(_PanelCase):
    """The Area column (2026-11-19): the area of each ROI's own region, in pixels or µm² as the unit says, on screen right after Shape (Geometry view)."""

    def setUp(self) -> None:
        super().setUp()  # ROIs 1-5: circles of 6 px diameter
        self.rectangle_id = self.toolbox.add_shaped_roi(60.0, 5.0, Rectangle(30.0, 14.0, 33.0))  # ROI 6
        plane = np.zeros((4, 5), dtype=bool)
        plane[:, :3] = True
        plane[0, 4] = True  # 13 pixels, not all joined: a count, not an area of an outline
        self.mask_id = self.toolbox.add_shaped_roi(80.0, 5.0, MaskShape(RoiMask(78, 3, plane)))  # ROI 7
        self.refresh()

    def on_screen(self) -> list[int]:
        return sorted((c for c in range(COLUMN_COUNT) if not self.tree.isColumnHidden(c)), key=self.tree.header().visualIndex)

    def use_micrometers(self) -> None:
        self.geometry.restore_settings(
            GeometrySettings(calibration_enabled=True, microns_per_pixel_x=0.5, microns_per_pixel_y=0.5, display_units="um")
        )
        self.refresh()

    def test_it_sits_between_the_shape_and_the_first_size_column(self) -> None:
        order = self.on_screen()
        self.assertEqual(order[order.index(COLUMN_SHAPE) + 1], COLUMN_AREA)
        self.assertEqual(order[order.index(COLUMN_AREA) + 1], COLUMN_SAMPLE)
        self.assertEqual(self.model.headerData(COLUMN_AREA, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole), "Area")

    def test_the_simple_view_does_not_show_it(self) -> None:
        self.panel._view_actions["simple"].trigger()
        self.assertTrue(self.tree.isColumnHidden(COLUMN_AREA))
        self.panel._view_actions["geometry"].trigger()
        self.assertFalse(self.tree.isColumnHidden(COLUMN_AREA))
        order = self.on_screen()
        self.assertEqual(order[order.index(COLUMN_SHAPE) + 1], COLUMN_AREA, "still after the shape when the mode comes back")

    def test_pixels_are_a_count_for_every_shape(self) -> None:
        self.assertEqual(self.text(1, COLUMN_AREA), "28", "a circle of 6 px: pi x 3^2 = 28.27")
        self.assertEqual(self.text(self.rectangle_id, COLUMN_AREA), "420", "30 x 14, whatever its angle")
        self.assertEqual(self.text(self.mask_id, COLUMN_AREA), "13", "a mask is its own pixels, counted")

    def test_micrometers_are_the_pixel_area_times_the_pixel_size_squared(self) -> None:
        self.use_micrometers()
        self.assertEqual(self.text(1, COLUMN_AREA), "7.1", "28.27 px^2 x 0.25 (0.5 µm per px, squared)")
        self.assertEqual(self.text(self.rectangle_id, COLUMN_AREA), "105.0")
        self.assertEqual(self.text(self.mask_id, COLUMN_AREA), "3.2")

    def test_it_follows_a_resize(self) -> None:
        self.toolbox.resize_rois([1], sample_diameter_px=10.0)
        self.refresh()
        self.assertEqual(self.text(1, COLUMN_AREA), "79", "pi x 5^2 = 78.54")
        self.toolbox.resize_rectangles([self.rectangle_id], width_px=20.0)
        self.refresh()
        self.assertEqual(self.text(self.rectangle_id, COLUMN_AREA), "280")

    def test_nothing_can_be_typed_into_it(self) -> None:
        for roi_id in (1, self.rectangle_id, self.mask_id):
            self.assertFalse(self.model.flags(self.cell(roi_id, COLUMN_AREA)) & Qt.ItemFlag.ItemIsEditable, roi_id)

    def test_the_numbers_are_right_aligned_and_the_tool_tips_say_where_they_come_from(self) -> None:
        align = self.model.data(self.cell(1, COLUMN_AREA), Qt.ItemDataRole.TextAlignmentRole)
        self.assertTrue(align & int(Qt.AlignmentFlag.AlignRight))
        tip = lambda roi_id: self.model.data(self.cell(roi_id, COLUMN_AREA), Qt.ItemDataRole.ToolTipRole)  # noqa: E731
        self.assertIn("13 pixels", tip(self.mask_id))
        self.assertIn("420.0 px²", tip(self.rectangle_id))
        self.assertIn("28.3 px²", tip(1))
        self.assertNotIn("µm²", tip(1))
        self.use_micrometers()
        self.assertIn("7.07 µm²", tip(1))
        self.assertIn("pixels", self.model.headerData(COLUMN_AREA, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole))

    def test_sorting_by_it_orders_by_size_and_ties_by_id(self) -> None:
        self.model.set_sort(COLUMN_AREA, False)
        ascending = self.shown_ids()
        self.assertEqual(ascending, [self.mask_id, 1, 2, 3, 4, 5, self.rectangle_id], "13 px, five equal circles in id order, 420 px")
        self.model.set_sort(COLUMN_AREA, True)
        self.assertEqual(self.shown_ids(), ascending[::-1], "descending is the exact reverse, as for every other column")

    def test_it_can_be_hidden_like_any_column_and_a_saved_hide_of_another_column_still_means_the_same(self) -> None:
        self.tree.set_hidden_keys({COLUMN_AREA})
        self.assertTrue(self.tree.isColumnHidden(COLUMN_AREA))
        self.tree.set_hidden_keys({COLUMN_ANGLE})  # the numbers of the older columns are unchanged: 10 is still the angle
        self.assertFalse(self.tree.isColumnHidden(COLUMN_AREA))
        self.assertTrue(self.tree.isColumnHidden(COLUMN_ANGLE))

    def test_a_width_list_saved_before_the_area_existed_is_still_read(self) -> None:
        store = UiStateStore({"roi_table/column_widths": [70, 90, 61, 62, 63, 64, 65, 40, 41, 42, 43]})  # the 11 columns of the day before
        self.panel.restore_ui_state(store)
        self.assertEqual(self.tree.columnWidth(COLUMN_X), 61)
        self.assertEqual(self.tree.columnWidth(COLUMN_ANGLE), 43)
        self.assertGreater(self.tree.columnWidth(COLUMN_AREA), 0, "the column it did not have keeps its default")

    def test_the_rightmost_column_on_screen_has_no_line_on_its_right_whatever_its_number(self) -> None:
        """The area has the highest number but sits in the middle: the delegate must not take the model's last column for the rightmost one."""
        from lspr_imaging_app.panels.roi_table.delegate import RoiTableDelegate

        last = RoiTableDelegate._last_column_on_screen
        self.assertEqual(last(self.tree, self.model), COLUMN_ANGLE, "with a rectangle in the table")
        self.toolbox.delete_rois([self.rectangle_id])
        self.refresh()
        self.assertEqual(last(self.tree, self.model), COLUMN_RIM_THICKNESS, "the angle is hidden without a rectangle")
        self.panel._view_actions["simple"].trigger()
        self.assertEqual(last(self.tree, self.model), COLUMN_NAME, "the Simple view has the number and the label")


class RectangleColumnsTest(_PanelCase):
    """The width, height and angle columns (W, H, θ): they exist for rectangles only, show only while the table holds one, and edit through
    `RoiToolbox.resize_rectangles` like the handles and the size dialog do (2026-11-19)."""

    def setUp(self) -> None:
        super().setUp()
        self.rectangle_id = self.toolbox.add_shaped_roi(60.0, 5.0, Rectangle(30.0, 14.0, 33.0))  # ROI 6; ROIs 1-5 are circles
        self.second_id = self.toolbox.add_shaped_roi(70.0, 5.0, Rectangle(20.0, 10.0, 0.0))  # ROI 7
        self.refresh()

    def rectangle(self, roi_id: int | None = None) -> Rectangle:
        return self.toolbox.roi_by_id(roi_id or self.rectangle_id).shape

    def shown(self, column: int) -> bool:
        return not self.tree.isColumnHidden(column)

    def test_the_columns_show_while_a_rectangle_is_in_the_table_and_go_when_the_last_one_does(self) -> None:
        columns = (COLUMN_WIDTH, COLUMN_HEIGHT, COLUMN_ANGLE)
        self.assertEqual([self.shown(c) for c in columns], [True, True, True])
        order = sorted((c for c in range(COLUMN_COUNT) if self.shown(c)), key=self.tree.header().visualIndex)
        self.assertEqual(order[-3:], list(columns), "after the diameters, left to right")
        self.toolbox.delete_rois([self.rectangle_id, self.second_id])
        self.refresh()
        self.assertEqual([self.shown(c) for c in columns], [False, False, False], "three empty columns would say nothing")
        self.toolbox.add_shaped_roi(60.0, 5.0, Rectangle(30.0, 14.0, 0.0))
        self.refresh()
        self.assertEqual([self.shown(c) for c in columns], [True, True, True])

    def test_the_simple_view_never_shows_them(self) -> None:
        self.panel._view_actions["simple"].trigger()
        self.assertFalse(any(self.shown(c) for c in (COLUMN_WIDTH, COLUMN_HEIGHT, COLUMN_ANGLE)))

    def test_a_column_the_user_hid_stays_hidden_with_rectangles_present(self) -> None:
        self.tree.set_hidden_keys({COLUMN_ANGLE})
        self.assertEqual([self.shown(c) for c in (COLUMN_WIDTH, COLUMN_HEIGHT, COLUMN_ANGLE)], [True, True, False])
        self.tree.set_hidden_keys(())
        self.assertTrue(self.shown(COLUMN_ANGLE))

    def test_a_rectangle_shows_its_size_and_its_rim_and_a_circle_shows_no_width(self) -> None:
        self.assertEqual([self.text(self.rectangle_id, c) for c in (COLUMN_WIDTH, COLUMN_HEIGHT, COLUMN_ANGLE)], ["30.0", "14.0", "33.0"])
        self.assertEqual([self.text(self.rectangle_id, c) for c in (COLUMN_SAMPLE, COLUMN_RIM_OFFSET, COLUMN_RIM_THICKNESS)], ["", "4.0", "4.0"], "no diameter; the rim, as for any shape")
        self.assertEqual([self.text(1, c) for c in (COLUMN_WIDTH, COLUMN_HEIGHT, COLUMN_ANGLE)], ["", "", ""])
        editable = Qt.ItemFlag.ItemIsEditable
        self.assertTrue(all(self.model.flags(self.cell(self.rectangle_id, c)) & editable for c in (COLUMN_WIDTH, COLUMN_HEIGHT, COLUMN_ANGLE)))
        self.assertFalse(any(self.model.flags(self.cell(1, c)) & editable for c in (COLUMN_WIDTH, COLUMN_HEIGHT, COLUMN_ANGLE)))
        self.assertFalse(self.model.flags(self.cell(self.rectangle_id, COLUMN_SAMPLE)) & editable, "a rectangle has no diameter")

    def test_a_mask_shows_nothing_in_them(self) -> None:
        self.toolbox.roi_by_id(1).shape = MaskShape(RoiMask(0, 0, np.ones((2, 2), dtype=bool)))
        self.refresh()
        self.assertEqual([self.text(1, c) for c in (COLUMN_WIDTH, COLUMN_HEIGHT, COLUMN_ANGLE)], ["", "", ""])

    def test_editing_a_width_height_or_angle_is_a_toolbox_command_and_undoes(self) -> None:
        self.edit(self.rectangle_id, COLUMN_WIDTH, "40")
        self.assertEqual(self.rectangle(), Rectangle(40.0, 14.0, 33.0))
        self.edit(self.rectangle_id, COLUMN_HEIGHT, "8,5")
        self.assertEqual(self.rectangle(), Rectangle(40.0, 8.5, 33.0))
        self.edit(self.rectangle_id, COLUMN_ANGLE, "-45")
        self.assertEqual(self.rectangle(), Rectangle(40.0, 8.5, -45.0))
        self.assertEqual(self.rectangle(self.second_id), Rectangle(20.0, 10.0, 0.0), "the other rectangle is untouched")
        undo_manager.undo()
        self.assertEqual(self.rectangle(), Rectangle(40.0, 8.5, 33.0))

    def test_an_edit_applies_to_every_selected_rectangle_and_skips_a_circle(self) -> None:
        self.selection.set_roi_selection({1, self.rectangle_id, self.second_id})
        self.edit(self.rectangle_id, COLUMN_ANGLE, "90")
        self.assertEqual([self.rectangle(i).angle_deg for i in (self.rectangle_id, self.second_id)], [90.0, 90.0])
        self.assertEqual(self.toolbox.roi_by_id(1).shape.diameter_px, 6.0, "a circle has no angle")
        self.assertIn("2 rectangles", self.footer())
        undo_manager.undo()
        self.assertEqual([self.rectangle(i).angle_deg for i in (self.rectangle_id, self.second_id)], [33.0, 0.0], "one undo step")

    def test_a_circle_alone_says_why_nothing_happened(self) -> None:
        self.panel._set_numeric_cells([1], COLUMN_WIDTH, "40")
        self.assertIn("not rectangles", self.footer())
        self.assertEqual(self.toolbox.roi_by_id(1).shape.diameter_px, 6.0)

    def test_a_bad_value_is_refused_shown_and_changes_nothing(self) -> None:
        self.edit(self.rectangle_id, COLUMN_WIDTH, "1")
        self.assertIn("at least 2", self.footer())
        self.edit(self.rectangle_id, COLUMN_ANGLE, "abc")
        self.assertIn("'abc' is not a number", self.footer())
        self.assertEqual(self.rectangle(), Rectangle(30.0, 14.0, 33.0))

    def test_committing_an_unchanged_cell_does_nothing(self) -> None:
        edits: list[tuple] = []
        self.model.cell_edited.connect(lambda *args: edits.append(args))
        index = self.cell(self.rectangle_id, COLUMN_ANGLE)
        self.model.setData(index, self.model.data(index, Qt.ItemDataRole.EditRole), Qt.ItemDataRole.EditRole)
        self.assertEqual(edits, [])

    def test_a_length_follows_the_unit_but_the_angle_never_does(self) -> None:
        self.geometry.restore_settings(
            GeometrySettings(calibration_enabled=True, microns_per_pixel_x=0.5, microns_per_pixel_y=0.5, display_units="um")
        )
        self.refresh()
        self.assertEqual(self.text(self.rectangle_id, COLUMN_WIDTH), "15", "30 px at 0.5 µm/px")
        self.assertEqual(self.text(self.rectangle_id, COLUMN_ANGLE), "33.0", "degrees, whatever the unit")
        self.edit(self.rectangle_id, COLUMN_WIDTH, "10")  # typed in µm, stored in px
        self.edit(self.rectangle_id, COLUMN_ANGLE, "45")
        self.assertEqual(self.rectangle(), Rectangle(20.0, 14.0, 45.0))

        def tip(column: int) -> str:
            return self.model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole)

        self.assertIn("(µm)", tip(COLUMN_WIDTH))
        self.assertNotIn("µm", tip(COLUMN_ANGLE))

    def test_the_new_columns_sort(self) -> None:
        self.panel._on_header_clicked(COLUMN_WIDTH)
        self.assertEqual(self.model.sort_state(), (COLUMN_WIDTH, False))
        self.assertEqual(self.shown_ids()[-2:], [self.second_id, self.rectangle_id], "narrowest rectangle last of the circles, which have no width")

    def test_copy_paste_and_the_wheel_work_on_them(self) -> None:
        self.tree.selectionModel().setCurrentIndex(self.cell(self.rectangle_id, COLUMN_ANGLE), QItemSelectionModel.SelectionFlag.NoUpdate)
        self.panel._copy_cell()
        self.selection.set_roi_selection({self.second_id})
        self.refresh()
        self.tree.selectionModel().setCurrentIndex(self.cell(self.second_id, COLUMN_ANGLE), QItemSelectionModel.SelectionFlag.NoUpdate)
        self.panel._paste_cells()
        self.assertEqual(self.rectangle(self.second_id).angle_deg, 33.0)
        self.refresh()  # (the table shows a change after a short pause; a step reads what the cell shows)
        self.panel._step_cell(self.cell(self.second_id, COLUMN_ANGLE), 1, False)
        self.assertEqual(self.rectangle(self.second_id).angle_deg, 34.0, "one degree")
        self.refresh()
        self.panel._step_cell(self.cell(self.second_id, COLUMN_ANGLE), -1, True)
        self.assertEqual(self.rectangle(self.second_id).angle_deg, 29.0, "Shift: five degrees")
        self.refresh()
        self.panel._step_cell(self.cell(self.second_id, COLUMN_WIDTH), 1, False)
        self.assertEqual(self.rectangle(self.second_id).width_px, 20.5, "a width steps like a diameter")

    def test_the_editor_steps_a_width_and_an_angle_with_up_and_down(self) -> None:
        from PyQt6.QtWidgets import QStyleOptionViewItem

        delegate = self.tree.itemDelegate()
        editor = delegate.createEditor(self.tree, QStyleOptionViewItem(), self.cell(self.rectangle_id, COLUMN_ANGLE))
        editor.setText("30")
        QTest.keyClick(editor, Qt.Key.Key_Up)
        self.assertEqual(editor.text(), "31")
        QTest.keyClick(editor, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(editor.text(), "26")
        editor = delegate.createEditor(self.tree, QStyleOptionViewItem(), self.cell(self.rectangle_id, COLUMN_WIDTH))
        editor.setText("30")
        QTest.keyClick(editor, Qt.Key.Key_Up)
        self.assertEqual(editor.text(), "30.5")

    def test_widths_saved_before_these_columns_existed_are_still_read_and_the_new_list_has_every_column(self) -> None:
        store = UiStateStore({"roi_table/column_widths": [70, 90, 61, 62, 63, 64, 65, 40]})
        self.panel.restore_ui_state(store)
        self.assertEqual(self.tree.columnWidth(COLUMN_X), 61)
        self.assertGreater(self.tree.columnWidth(COLUMN_WIDTH), 0, "the columns an old list did not have keep their default")
        self.tree.setColumnWidth(COLUMN_X, 66)
        self.assertEqual(len(store.get("roi_table/column_widths")), COLUMN_COUNT)

    def test_painting_with_rectangles_does_not_raise(self) -> None:
        self.panel.resize(900, 300)
        self.panel.show()
        _APP.processEvents()
        self.addCleanup(self.panel.hide)
        self.assertFalse(self.tree.viewport().grab().isNull())


class WheelEditTest(_PanelCase):
    """The mouse wheel over a number of a selected ROI row steps it (Ctrl+wheel: any row); elsewhere it scrolls (2026-11-19). Real `QWheelEvent`s into the table."""

    def setUp(self) -> None:
        super().setUp()
        self.rectangle_id = self.toolbox.add_shaped_roi(60.0, 5.0, Rectangle(30.0, 14.0, 33.0))  # ROI 6; ROIs 1-5 are circles of 6 px at x = 10 ... 50
        self.panel.resize(1100, 320)
        self.panel.show()
        self.addCleanup(self.panel.hide)
        self.refresh()
        _APP.processEvents()

    def select(self, *ids: int) -> None:
        self.selection.set_roi_selection(set(ids))
        self.refresh()
        _APP.processEvents()

    def wheel(self, roi_id: int, column: int, delta: int = 120, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        from PyQt6.QtCore import QPoint, QPointF
        from PyQt6.QtGui import QWheelEvent

        rect = self.tree.visualRect(self.cell(roi_id, column))
        self.assertTrue(rect.isValid() and self.tree.viewport().rect().contains(rect.center()), f"ROI {roi_id} column {column} is not on screen")
        position = QPointF(rect.center())
        event = QWheelEvent(
            position, self.tree.viewport().mapToGlobal(position), QPoint(0, 0), QPoint(0, delta), Qt.MouseButton.NoButton, modifiers,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        QtWidgets.QApplication.sendEvent(self.tree.viewport(), event)

    def diameters(self, *ids: int) -> list[float]:
        return [self.toolbox.roi_by_id(i).shape.diameter_px for i in ids]

    def test_a_plain_wheel_over_a_number_of_a_selected_row_steps_it_on_every_selected_roi(self) -> None:
        self.select(1, 2)
        self.wheel(1, COLUMN_SAMPLE)
        self.assertEqual(self.diameters(1, 2, 3), [6.5, 6.5, 6.0])
        self.wheel(1, COLUMN_SAMPLE, -120, Qt.KeyboardModifier.ShiftModifier)  # Shift: the large step (5 px): 6.5 - 5 = 1.5, below the 2 px minimum
        self.assertEqual(self.diameters(1, 2, 3), [6.5, 6.5, 6.0], "refused, nothing changes")
        self.assertIn("at least", self.footer())
        self.wheel(1, COLUMN_SAMPLE, 120, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.diameters(1, 2, 3), [11.5, 11.5, 6.0], "large step up")

    def test_the_wheel_scrolls_where_it_does_not_step(self) -> None:
        self.select(1)
        before = self.diameters(1, 2, 3)
        self.assertFalse(self.tree.wheel_steps(self.cell(2, COLUMN_SAMPLE)), "a row that is not selected")
        self.wheel(2, COLUMN_SAMPLE)
        self.assertEqual(self.diameters(1, 2, 3), before)
        self.assertFalse(self.tree.wheel_steps(self.cell(1, COLUMN_NAME)), "the label")
        self.assertFalse(self.tree.wheel_steps(self.cell(1, COLUMN_SHAPE)), "the shape")
        self.assertFalse(self.tree.wheel_steps(self.cell(1, COLUMN_WIDTH)), "a circle has no width: a blank cell")
        self.assertTrue(self.tree.wheel_steps(self.cell(1, COLUMN_SAMPLE)))

    def test_a_group_header_is_never_a_step(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        self.select(1, 2)
        self.assertFalse(self.tree.wheel_steps(self.model.group_index("group_1")))

    def test_ctrl_wheel_steps_any_row_but_only_that_row(self) -> None:
        self.select(1)
        self.wheel(3, COLUMN_SAMPLE, 120, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.diameters(1, 3), [6.0, 6.5])

    def test_fast_notches_do_not_lose_steps(self) -> None:
        """The table shows a change only after a short pause: the second notch must start from the stored value, not from the one the cell still shows."""
        self.select(1)
        self.wheel(1, COLUMN_SAMPLE)
        self.wheel(1, COLUMN_SAMPLE)
        self.wheel(1, COLUMN_SAMPLE)
        self.assertEqual(self.diameters(1), [7.5])

    def test_small_turns_add_up_to_a_step_and_a_change_of_direction_starts_again(self) -> None:
        self.select(1)
        for _ in range(2):
            self.wheel(1, COLUMN_SAMPLE, 40)
        self.assertEqual(self.diameters(1), [6.0], "80 units: not a notch yet")
        self.wheel(1, COLUMN_SAMPLE, 40)
        self.assertEqual(self.diameters(1), [6.5], "120: one step")
        self.wheel(1, COLUMN_SAMPLE, 60)
        self.wheel(1, COLUMN_SAMPLE, -120)
        self.assertEqual(self.diameters(1), [6.0], "the other way counts from zero: one step down, not none")

    def test_a_position_moves_the_selected_rois_together(self) -> None:
        self.select(1, 2)
        self.wheel(1, COLUMN_X)
        self.assertEqual(self.xs()[:3], [10.5, 20.5, 30.0], "both move, the 10 px between them stays")
        self.wheel(2, COLUMN_Y, -120, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual([roi.center_y for roi in self.toolbox.rois()][:3], [0.0, 0.0, 5.0])
        undo_manager.undo()
        self.assertEqual([roi.center_y for roi in self.toolbox.rois()][:3], [5.0, 5.0, 5.0], "one notch, one undo step")

    def test_a_position_step_is_a_micrometre_in_micrometres(self) -> None:
        self.geometry.restore_settings(
            GeometrySettings(calibration_enabled=True, microns_per_pixel_x=0.5, microns_per_pixel_y=0.5, display_units="um")
        )
        self.refresh()
        _APP.processEvents()
        self.select(1)
        self.wheel(1, COLUMN_X)
        self.assertEqual(self.xs()[0], 12.0, "1 um at 0.5 um/px = 2 px")

    def test_a_pixel_map_among_the_selection_moves_a_whole_pixel_with_the_others(self) -> None:
        self.toolbox.roi_by_id(2).shape = MaskShape(RoiMask(0, 0, np.ones((2, 2), dtype=bool)))
        self.select(1, 2)
        self.wheel(1, COLUMN_X)  # a notch is half a pixel: the circle moves that, the map the nearest whole pixel (the half goes away from zero)
        self.assertEqual(self.xs()[:2], [10.5, 21.0])
        self.assertEqual(self.toolbox.roi_by_id(2).shape.mask.x0, 1, "its pixels went with its label point")
        self.assertNotIn("pixel map", self.footer())

    def test_a_typed_position_under_half_a_pixel_leaves_a_pixel_map_where_it_is_and_says_why(self) -> None:
        self.toolbox.roi_by_id(2).shape = MaskShape(RoiMask(0, 0, np.ones((2, 2), dtype=bool)))
        self.refresh()
        self.edit(2, COLUMN_X, "20.3")
        self.assertEqual(self.xs()[1], 20.0)
        self.assertIn("pixel map", self.footer())
        self.assertIn("whole pixels", self.footer())

    def test_a_rectangle_steps_its_width_and_angle(self) -> None:
        self.select(self.rectangle_id)
        self.wheel(self.rectangle_id, COLUMN_ANGLE)
        self.wheel(self.rectangle_id, COLUMN_WIDTH, -120)
        self.assertEqual(self.toolbox.roi_by_id(self.rectangle_id).shape, Rectangle(29.5, 14.0, 34.0))
        self.wheel(self.rectangle_id, COLUMN_ANGLE, 120, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.toolbox.roi_by_id(self.rectangle_id).shape.angle_deg, 39.0, "Shift: five degrees")


class CellCopyPasteTest(_PanelCase):
    """Picking cells (click a selected row's number again and drag), copying them, and pasting into matching columns (2026-11-19)."""

    def setUp(self) -> None:
        super().setUp()
        self.rectangle_id = self.toolbox.add_shaped_roi(60.0, 5.0, Rectangle(30.0, 14.0, 33.0))  # ROI 6; ROIs 1-5 are circles of 6 px
        self.panel.resize(1100, 320)
        self.panel.show()
        self.addCleanup(self.panel.hide)
        self.addCleanup(lambda: QtWidgets.QApplication.clipboard().clear())
        self.refresh()
        _APP.processEvents()

    # -- helpers --------------------------------------------------------------------
    def select(self, *ids: int) -> None:
        self.selection.set_roi_selection(set(ids))
        self.refresh()
        _APP.processEvents()

    def where(self, roi_id: int, column: int):
        rect = self.tree.visualRect(self.cell(roi_id, column))
        self.assertTrue(rect.isValid() and self.tree.viewport().rect().contains(rect.center()), f"ROI {roi_id} column {column} is not on screen")
        return rect.center()

    def press(self, roi_id: int, column: int, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        QTest.mousePress(self.tree.viewport(), Qt.MouseButton.LeftButton, modifiers, self.where(roi_id, column))

    def move(self, roi_id: int, column: int) -> None:
        QTest.mouseMove(self.tree.viewport(), self.where(roi_id, column))

    def release(self, roi_id: int, column: int) -> None:
        QTest.mouseRelease(self.tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.where(roi_id, column))

    def drag(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        self.press(*start)
        self.move(*end)
        self.release(*end)

    def picked(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        return self.tree.cell_range()

    def diameters(self, *ids: int) -> list[float]:
        return [self.toolbox.roi_by_id(i).shape.diameter_px for i in ids]

    def set_diameters(self, **by_id) -> None:
        for roi_id, value in by_id.items():
            self.toolbox.resize_rois([int(roi_id[1:])], sample_diameter_px=value)
        self.refresh()
        undo_manager.clear()

    def copy_cells(self, rows, columns) -> None:
        self.tree.set_cell_range(rows, columns)
        self.panel._copy_cell()
        self.tree.clear_cell_marks()  # (the copy is on the clipboard; the marks are not needed for what follows)

    def tip(self) -> str:
        return QtWidgets.QToolTip.text()

    # -- picking --------------------------------------------------------------------------
    def test_a_first_click_selects_the_row_as_always_and_picks_no_cells(self) -> None:
        self.press(2, COLUMN_SAMPLE)
        self.release(2, COLUMN_SAMPLE)
        self.assertEqual(sorted(self.selection.selected_roi_ids()), [2])
        self.assertEqual(self.picked(), ((), ()))

    def test_clicking_a_selected_rows_number_again_picks_that_cell_and_keeps_the_selection(self) -> None:
        self.select(1, 2, 3)
        self.press(2, COLUMN_SAMPLE)
        self.release(2, COLUMN_SAMPLE)
        self.assertEqual(sorted(self.selection.selected_roi_ids()), [1, 2, 3], "the click did not collapse the selection to one row")
        self.assertEqual(self.picked(), ((2,), (COLUMN_SAMPLE,)))

    def test_dragging_picks_the_selected_rows_and_the_number_columns_between(self) -> None:
        self.select(1, 2, 3)
        self.drag((1, COLUMN_SAMPLE), (3, COLUMN_RIM_THICKNESS))
        self.assertEqual(self.picked(), ((1, 2, 3), (COLUMN_SAMPLE, COLUMN_RIM_OFFSET, COLUMN_RIM_THICKNESS)))
        self.drag((3, COLUMN_RIM_OFFSET), (1, COLUMN_SAMPLE))  # the other way round
        self.assertEqual(self.picked(), ((1, 2, 3), (COLUMN_SAMPLE, COLUMN_RIM_OFFSET)))
        self.assertEqual(sorted(self.selection.selected_roi_ids()), [1, 2, 3], "the rows stay selected")

    def test_only_selected_rows_are_picked_and_the_shape_column_is_never(self) -> None:
        self.select(1, 3)
        self.drag((1, COLUMN_X), (3, COLUMN_SAMPLE))
        self.assertEqual(self.picked(), ((1, 3), (COLUMN_X, COLUMN_Y, COLUMN_SAMPLE)), "row 2 is not selected; the Shape column lies between y and D_s and is not a number")

    def test_pressing_anywhere_else_is_the_usual_row_click(self) -> None:
        self.select(1, 2)
        self.press(1, COLUMN_SHAPE)  # the shape: not a number
        self.release(1, COLUMN_SHAPE)
        self.assertEqual(self.picked(), ((), ()))
        self.select(1, 2)
        self.press(4, COLUMN_SAMPLE)  # a row that is not selected
        self.release(4, COLUMN_SAMPLE)
        self.assertEqual(sorted(self.selection.selected_roi_ids()), [4])
        self.assertEqual(self.picked(), ((), ()))
        self.select(1, 2)
        self.press(1, COLUMN_SAMPLE, Qt.KeyboardModifier.ControlModifier)  # Ctrl: the usual selection gesture
        self.release(1, COLUMN_SAMPLE)
        self.assertEqual(self.picked(), ((), ()))

    def test_a_click_elsewhere_lets_go_of_the_cells_and_so_does_another_selection(self) -> None:
        self.select(1, 2, 3)
        self.drag((1, COLUMN_SAMPLE), (2, COLUMN_SAMPLE))
        self.assertTrue(self.picked()[0])
        self.press(3, COLUMN_SHAPE)
        self.release(3, COLUMN_SHAPE)
        self.assertEqual(self.picked(), ((), ()))
        self.select(1, 2, 3)
        self.drag((1, COLUMN_SAMPLE), (3, COLUMN_SAMPLE))
        self.select(2, 3)  # the selection changed from outside (the image, say): the range keeps the rows still selected
        self.assertEqual(self.picked()[0], (2, 3))
        self.select(5)
        self.assertEqual(self.picked(), ((), ()))

    def test_a_double_click_on_a_selected_rows_number_still_opens_the_editor(self) -> None:
        """A real double-click is press, release, then the double-click event (`QTest.mouseDClick` sends only that last part). Qt opens the editor only if its own press ran for that cell:
        the press that starts picking cells lets it run."""
        self.select(1, 2)
        where = self.where(1, COLUMN_SAMPLE)
        self.press(1, COLUMN_SAMPLE)
        self.release(1, COLUMN_SAMPLE)
        QTest.mouseDClick(self.tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, where)
        self.assertEqual(self.tree.state(), QtWidgets.QAbstractItemView.State.EditingState)
        self.assertEqual(sorted(self.selection.selected_roi_ids()), [1, 2], "the selection is still both rows")

    def test_the_wheel_only_scrolls_while_cells_are_being_picked(self) -> None:
        from PyQt6.QtCore import QPoint, QPointF
        from PyQt6.QtGui import QWheelEvent

        self.select(1, 2)
        self.press(1, COLUMN_SAMPLE)
        position = QPointF(self.where(1, COLUMN_SAMPLE))
        event = QWheelEvent(position, self.tree.viewport().mapToGlobal(position), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(self.tree.viewport(), event)
        self.assertEqual(self.diameters(1, 2), [6.0, 6.0], "a wheel turn during the drag did not step the numbers")
        self.release(1, COLUMN_SAMPLE)

    def test_esc_and_the_view_mode_and_hidden_columns_let_go(self) -> None:
        self.select(1, 2)
        self.drag((1, COLUMN_SAMPLE), (2, COLUMN_RIM_THICKNESS))
        QTest.keyClick(self.tree, Qt.Key.Key_Escape)
        self.assertEqual(self.picked(), ((), ()))
        self.drag((1, COLUMN_SAMPLE), (2, COLUMN_RIM_THICKNESS))
        self.tree.set_hidden_keys({COLUMN_RIM_OFFSET})
        self.assertEqual(self.picked(), ((1, 2), (COLUMN_SAMPLE, COLUMN_RIM_THICKNESS)), "a column that is hidden leaves the picked cells")
        self.tree.set_hidden_keys(())
        self.panel._view_actions["simple"].trigger()
        self.assertEqual(self.picked(), ((), ()), "the Simple view shows no numbers")

    # -- copy -----------------------------------------------------------------------------
    def test_copy_puts_the_numbers_and_tab_text_on_the_clipboard_and_marks_the_cells(self) -> None:
        self.set_diameters(r1=7.0, r2=8.0, r3=9.0)
        self.tree.set_cell_range((1, 2, 3), (COLUMN_SAMPLE,))
        self.panel._copy_cell()
        mime = QtWidgets.QApplication.clipboard().mimeData()
        self.assertTrue(mime.hasFormat("application/x-lspr-roi-cells"))
        self.assertEqual(mime.text(), "7.0\n8.0\n9.0")
        self.assertIsNotNone(self.tree.copied_edges(self.cell(2, COLUMN_SAMPLE)))
        self.assertIsNone(self.tree.copied_edges(self.cell(4, COLUMN_SAMPLE)))
        self.assertIsNone(self.tree.copied_edges(self.cell(2, COLUMN_RIM_OFFSET)))
        self.assertEqual(self.tree.copied_edges(self.cell(1, COLUMN_SAMPLE)), (True, True, False, True), "the top of the block")
        self.assertEqual(self.tree.copied_edges(self.cell(3, COLUMN_SAMPLE)), (False, True, True, True), "the bottom of the block")
        self.assertIn("Copied 3 cells", self.footer())

    def test_copy_without_picked_cells_copies_the_current_cell(self) -> None:
        self.select(2)
        self.tree.selectionModel().setCurrentIndex(self.cell(2, COLUMN_SAMPLE), QItemSelectionModel.SelectionFlag.NoUpdate)
        self.panel._copy_cell()
        self.assertEqual(QtWidgets.QApplication.clipboard().mimeData().text(), "6.0")
        self.assertIsNotNone(self.tree.copied_edges(self.cell(2, COLUMN_SAMPLE)))

    def test_the_ants_move_while_the_table_is_shown_and_stop_on_esc_or_when_hidden(self) -> None:
        self.tree.set_cell_range((1,), (COLUMN_SAMPLE,))
        self.panel._copy_cell()
        self.assertTrue(self.tree._ants.isActive())
        phase = self.tree.ants_phase()
        self.tree._ants_tick()
        self.assertNotEqual(self.tree.ants_phase(), phase)
        self.panel.hide()
        _APP.processEvents()
        self.assertFalse(self.tree._ants.isActive(), "a hidden panel does no work")
        self.panel.show()
        _APP.processEvents()
        self.assertTrue(self.tree._ants.isActive(), "and they march again when it is shown")
        QTest.keyClick(self.tree, Qt.Key.Key_Escape)
        self.assertFalse(self.tree._ants.isActive())
        self.assertIsNone(self.tree.copied_edges(self.cell(1, COLUMN_SAMPLE)))

    def test_copying_something_else_ends_the_ants(self) -> None:
        self.tree.set_cell_range((1,), (COLUMN_SAMPLE,))
        self.panel._copy_cell()
        self.assertTrue(self.tree.copied_token())
        QtWidgets.QApplication.clipboard().setText("from another program")
        _APP.processEvents()
        self.panel._on_clipboard_changed()  # (the signal says it too; asked directly so the test does not depend on the platform's clipboard)
        self.assertEqual(self.tree.copied_token(), "")

    # -- paste ----------------------------------------------------------------------------
    def test_one_copied_row_fills_every_selected_row(self) -> None:
        self.set_diameters(r5=9.0)
        self.copy_cells((5,), (COLUMN_SAMPLE,))
        self.select(1, 2, 3)
        self.panel._paste_cells()
        self.assertEqual(self.diameters(1, 2, 3, 4), [9.0, 9.0, 9.0, 6.0])
        self.assertEqual(undo_manager.undo_label, "Paste cells")
        undo_manager.undo()
        self.assertEqual(self.diameters(1, 2, 3), [6.0, 6.0, 6.0], "one undo step")

    def test_several_copied_rows_repeat_down_the_selected_rows_until_they_run_out(self) -> None:
        self.set_diameters(r1=7.0, r2=8.0, r3=9.0)
        self.copy_cells((1, 2, 3), (COLUMN_SAMPLE,))
        self.select(1, 2, 3, 4, 5)
        self.panel._paste_cells()
        self.assertEqual(self.diameters(1, 2, 3, 4, 5), [7.0, 8.0, 9.0, 7.0, 8.0])
        undo_manager.undo()
        self.select(4, 5)
        self.panel._paste_cells()
        self.assertEqual(self.diameters(4, 5), [7.0, 8.0], "the pattern is cut where the selection ends")

    def test_one_selected_row_and_several_copied_rows_run_on_down_and_select_what_was_pasted(self) -> None:
        self.set_diameters(r1=7.0, r2=8.0, r3=9.0)
        self.copy_cells((1, 2, 3), (COLUMN_SAMPLE,))
        self.select(4)
        self.panel._paste_cells()
        self.assertEqual(self.diameters(4, 5), [7.0, 8.0])
        self.assertEqual(self.toolbox.roi_by_id(6).shape, Rectangle(30.0, 14.0, 33.0), "ROI 6 is a rectangle: it has no diameter, nothing was put there")
        self.assertEqual(sorted(self.selection.selected_roi_ids()), [4, 5], "what was pasted is selected")
        self.assertEqual(self.picked(), ((4, 5), (COLUMN_SAMPLE,)))
        self.assertIn("1 row has no D_s", self.footer())

    def test_only_matching_columns_are_filled(self) -> None:
        self.toolbox.resize_rois([1], reference_inner_diameter_px=10.0, reference_outer_diameter_px=20.0)
        self.refresh()
        undo_manager.clear()
        self.copy_cells((1,), (COLUMN_SAMPLE, COLUMN_RIM_OFFSET, COLUMN_RIM_THICKNESS))
        self.select(2, 3)
        self.tree.set_cell_range((2, 3), (COLUMN_RIM_OFFSET,))  # the picked cells name the column: only the offset (2 px: the copied ROI's)
        self.panel._paste_cells()
        self.assertEqual([ring_diameters(self.toolbox.roi_by_id(i).shape, self.toolbox.roi_by_id(i).border) for i in (2, 3)], [(10.0, 18.0)] * 2, "the thickness kept its value (4 px)")
        self.assertEqual(self.diameters(2, 3), [6.0, 6.0], "the sample diameter was not touched")

    def test_a_block_of_columns_goes_to_its_matching_columns_when_only_rows_are_chosen(self) -> None:
        self.toolbox.resize_rois([1], sample_diameter_px=8.0, reference_inner_diameter_px=12.0, reference_outer_diameter_px=24.0)
        self.refresh()
        undo_manager.clear()
        self.copy_cells((1,), (COLUMN_SAMPLE, COLUMN_RIM_OFFSET, COLUMN_RIM_THICKNESS))
        self.select(2, 3)
        self.panel._paste_cells()
        for roi_id in (2, 3):
            roi = self.toolbox.roi_by_id(roi_id)
            self.assertEqual((roi.shape.diameter_px, ring_diameters(roi.shape, roi.border)), (8.0, (12.0, 24.0)))

    def test_rectangle_numbers_go_to_rectangles_and_skip_circles(self) -> None:
        second = self.toolbox.add_shaped_roi(70.0, 5.0, Rectangle(20.0, 10.0, 0.0))
        self.refresh()
        undo_manager.clear()
        self.copy_cells((self.rectangle_id,), (COLUMN_WIDTH, COLUMN_HEIGHT, COLUMN_ANGLE))
        self.select(1, second)
        self.panel._paste_cells()
        self.assertEqual(self.toolbox.roi_by_id(second).shape, Rectangle(30.0, 14.0, 33.0))
        self.assertEqual(self.diameters(1), [6.0], "a circle has no width, height or angle")
        self.assertIn("1 row has no W", self.footer())

    def test_a_position_pasted_into_several_rows_sets_each_one(self) -> None:
        self.copy_cells((1, 2), (COLUMN_X,))
        self.select(3, 4, 5)
        self.panel._paste_cells()
        self.assertEqual([self.toolbox.roi_by_id(i).center_x for i in (3, 4, 5)], [10.0, 20.0, 10.0], "10, 20, then the pattern starts again")

    def test_a_paste_into_the_wrong_column_is_refused_with_a_tip_at_the_pointer(self) -> None:
        self.copy_cells((1,), (COLUMN_RIM_OFFSET,))
        self.select(2, 3)
        self.tree.set_cell_range((2, 3), (COLUMN_SAMPLE,))
        before = undo_manager.undo_label
        self.panel._paste_cells()
        self.assertIn("No matching column: copied d, selected D_s", self.footer())
        self.assertIn("No matching column", self.tip())
        self.assertEqual(self.diameters(2, 3), [6.0, 6.0])
        self.assertEqual(undo_manager.undo_label, before, "nothing was done")

    def test_a_value_that_breaks_a_rule_stops_the_whole_paste_and_names_the_roi(self) -> None:
        mime = QMimeData()
        from lspr_imaging_app.panels.roi_table import cell_clipboard as cc

        block = cc.CellBlock((COLUMN_RIM_THICKNESS,), ((-5.0,),))  # a thickness below zero is refused for every ROI
        mime.setData(cc.MIME_TYPE, cc.block_to_payload(block))
        QtWidgets.QApplication.clipboard().setMimeData(mime)
        self.select(1, 2)
        self.panel._paste_cells()
        self.assertIn("ROI 1: the rim's thickness cannot be negative", self.tip())
        self.assertIsNone(self.toolbox.roi_by_id(1).border)
        self.assertIsNone(self.toolbox.roi_by_id(2).border)

    def test_a_pasted_position_moves_a_pixel_map_by_the_nearest_whole_pixels(self) -> None:
        self.toolbox.roi_by_id(2).shape = MaskShape(RoiMask(0, 0, np.ones((2, 2), dtype=bool)))
        self.refresh()
        self.copy_cells((4,), (COLUMN_X,))
        x = self.toolbox.roi_by_id(4).center_x
        self.select(1, 2)
        self.panel._paste_cells()
        self.assertEqual(self.toolbox.roi_by_id(1).center_x, x)
        self.assertEqual(self.toolbox.roi_by_id(2).center_x, 20.0 + round(x - 20.0), "the map's centre went by whole pixels")
        self.assertEqual(self.toolbox.roi_by_id(2).shape.mask.x0, round(x - 20.0))

    def test_text_from_another_program_still_pastes_its_first_value(self) -> None:
        QtWidgets.QApplication.clipboard().setText("7,5\t99\n1\t2")
        self.select(1, 2)
        self.tree.selectionModel().setCurrentIndex(self.cell(1, COLUMN_SAMPLE), QItemSelectionModel.SelectionFlag.NoUpdate)
        self.panel._paste_cells()
        self.assertEqual(self.diameters(1, 2, 3), [7.5, 7.5, 6.0])

    def test_the_pasted_cells_are_picked_so_they_can_be_copied_again(self) -> None:
        self.set_diameters(r1=7.0)
        self.copy_cells((1,), (COLUMN_SAMPLE,))
        self.select(2, 3)
        self.panel._paste_cells()
        self.assertEqual(self.picked(), ((2, 3), (COLUMN_SAMPLE,)))

    # -- the menu and the painting --------------------------------------------------------------
    def test_the_menu_has_copy_paste_and_cancel_with_their_keys_and_states(self) -> None:
        def entries():
            self._menu = QMenu()  # (kept: the entries go with their menu)
            self.panel._fill_cell_menu(self._menu)
            return [a for a in self._menu.actions() if not a.isSeparator()]

        self.assertEqual(entries(), [], "nothing picked, nothing copied: none of the three is offered (2026-11-19: left out, not greyed)")
        self.select(1, 2)
        self.drag((1, COLUMN_SAMPLE), (2, COLUMN_SAMPLE))
        copy, cancel = entries()  # cells are picked: Copy and Cancel; nothing copied yet, so no Paste
        self.assertEqual((copy.text(), cancel.text()), ("Copy cells\tCtrl+C", "Cancel cell selection\tEsc"))
        copy.trigger()
        copy, paste, cancel = entries()
        self.assertEqual(paste.text(), "Paste cells\tCtrl+V")
        cancel.trigger()
        self.assertEqual(self.picked(), ((), ()))
        self.assertIsNone(self.tree.copied_edges(self.cell(1, COLUMN_SAMPLE)))

    def test_painting_the_picked_and_copied_cells_does_not_raise(self) -> None:
        self.select(1, 2, 3)
        self.drag((1, COLUMN_SAMPLE), (3, COLUMN_RIM_THICKNESS))
        self.panel._copy_cell()
        self.tree.set_cell_range((1, 2), (COLUMN_X, COLUMN_Y))
        self.assertFalse(self.tree.viewport().grab().isNull())
        self.assertEqual(self.tree.range_edges(self.cell(1, COLUMN_X)), (True, False, False, True), "top left corner of the picked block")
        self.assertEqual(self.tree.range_edges(self.cell(2, COLUMN_Y)), (False, True, True, False), "bottom right corner")
