"""Integration tests for the LSPRi rewrite's area selection: the General
picker, the drag gesture + marching ants, and every tool it restricts.

Only runs on the `apps/LSPRi/eva` `rewrite` branch. Real `QApplication` and
widgets, driven by direct method/signal calls (no screen coordinates), same
pattern as `test_lspri_rewrite_histogram_panel.py`.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        ImageTool,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.histogram import HistogramPanel
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.selection_toolbar import SelectionToolbar
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import (
        AreaSelectionMode,
        AreaSelectionModule,
        HighlightRangeModule,
        ReferenceFrameModule,
        SelectionModule,
    )
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

_SHAPE = (64, 80)
_FRAME = (0, 500.0)


def _pump(seconds: float = 0.5) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _pump_until(condition, timeout: float = 15.0) -> None:
    """Pump events until `condition()` holds. A fixed pause is not enough on a slow CI runner."""
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _write_dataset(root: Path) -> ImageDataset:
    frame = np.full(_SHAPE, 150.0, dtype=np.float32)
    frame[10:20, 10:20] = 3000.0  # bright block, left half
    frame[40:50, 50:60] = 3000.0  # bright block, right half
    path = root / "c0_w500.tif"
    tifffile.imwrite(str(path), frame)
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)
    return ImageDataset(folder=root, records=[record], source_format="image_stack")


class AreaSelectionIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset_model = _write_dataset(Path(self._tmp.name))
        self.dataset = DatasetModule()
        self.geometry = GeometryModule()
        self.mask = MaskModule()
        self.chromatic = ChromaticModule()
        self.roi_toolbox = RoiToolbox()
        self.highlight_range = HighlightRangeModule()
        self.active_tool = ActiveToolModule()
        self.area = AreaSelectionModule()
        self.image_panel = ImagePanel(
            self.dataset, self.geometry, self.mask, self.chromatic, BackgroundModule(), self.roi_toolbox,
            SelectionModule(), self.active_tool, ReferenceFrameModule(), self.highlight_range,
            mask_scope=MaskScopeModule(), area_selection=self.area,
        )
        self.histogram = HistogramPanel(
            self.image_panel, self.geometry, self.mask, self.chromatic, self.roi_toolbox, self.highlight_range,
        )
        self.image_panel.resize(900, 600)
        self.image_panel.show()
        self.dataset.load_dataset(self.dataset_model)
        _pump_until(lambda: self.image_panel._area_tool._image_shape is not None)  # first picture drawn
        _pump()

    def tearDown(self) -> None:
        self.image_panel._renderer.stop()
        self._tmp.cleanup()

    # -- picker / gesture / ants -------------------------------------------

    def _toolbar(self) -> SelectionToolbar:
        return self.image_panel._selection_tools.toolbar

    def _pick(self, mode: AreaSelectionMode) -> None:
        self._toolbar().tool_button(mode).click()

    def test_picking_rectangle_arms_the_draw_tool_and_picking_it_again_leaves_it_keeping_the_selection(self) -> None:
        self._pick(AreaSelectionMode.RECTANGLE)
        self.assertIs(self.active_tool.active(), ImageTool.SELECT_AREA)
        self.assertTrue(self._toolbar().tool_button(AreaSelectionMode.RECTANGLE).isChecked())
        self.area.set_rectangle(5, 5, 20, 20)
        self._pick(AreaSelectionMode.RECTANGLE)
        self.assertIsNone(self.active_tool.active())
        self.assertFalse(self._toolbar().tool_button(AreaSelectionMode.RECTANGLE).isChecked())
        self.assertTrue(self.area.has_selection())

    def test_deselect_is_in_the_right_click_menu_and_has_no_button(self) -> None:
        """2026-11-15 (maintainer: the Deselect icon "can be removed, it is in the context menu")."""
        from unittest.mock import patch

        from PyQt6.QtCore import QPointF

        self.assertFalse(hasattr(self._toolbar(), "deselect_button"))
        self.area.set_rectangle(5, 5, 20, 20)
        scene = self.image_panel._plot.vb.mapViewToScene(QPointF(10.5, 10.5))
        with patch("lspr_imaging_app.panels.image.interaction.show_tool_context_menu", return_value="Deselect") as menu:
            self.image_panel._interaction._context_menu([], scene)
        entries = [label for label, _enabled in menu.call_args.args[1]]
        self.assertIn("Deselect", entries)
        self.assertIn("Invert selection", entries)
        self.assertFalse(self.area.has_selection())

    def test_only_the_picked_tool_is_checked(self) -> None:
        self._pick(AreaSelectionMode.LASSO)
        self._pick(AreaSelectionMode.WAND)
        checked = [mode for mode in (AreaSelectionMode.RECTANGLE, AreaSelectionMode.LASSO, AreaSelectionMode.WAND)
                   if self._toolbar().tool_button(mode).isChecked()]
        self.assertEqual(checked, [AreaSelectionMode.WAND])
        self.assertIs(self.area.mode(), AreaSelectionMode.WAND)

    def test_the_wand_does_not_drag(self) -> None:
        self._pick(AreaSelectionMode.WAND)
        self.assertFalse(self.image_panel._area_tool.begin_gesture(10.0, 10.0))

    def test_repicking_the_same_mode_rearms_after_another_tool(self) -> None:
        self._pick(AreaSelectionMode.LASSO)
        self.active_tool.set_active(ImageTool.ADD_ROI, True)
        self._pick(AreaSelectionMode.LASSO)
        self.assertIs(self.active_tool.active(), ImageTool.SELECT_AREA)

    def test_rectangle_drag_commits_on_finish_and_clamps_to_the_image(self) -> None:
        self._pick(AreaSelectionMode.RECTANGLE)
        tool = self.image_panel._area_tool
        self.assertTrue(tool.begin_gesture(10.0, 10.0))
        tool.update_gesture(500.0, 30.0)  # far past the right edge
        self.assertFalse(self.area.has_selection())  # nothing committed mid-drag
        tool.end_gesture()
        mask = self.area.mask(_SHAPE)
        self.assertTrue(mask[15, 79])
        self.assertFalse(mask[15, 5])
        self.assertEqual(int(mask.sum()), 20 * 70)  # rows 10..29, cols 10..79

    def test_lasso_drag_commits_a_polygon(self) -> None:
        self._pick(AreaSelectionMode.LASSO)
        tool = self.image_panel._area_tool
        tool.begin_gesture(10.0, 10.0)
        for point in [(30.0, 10.0), (30.0, 30.0), (10.0, 30.0)]:
            tool.update_gesture(*point)
        tool.end_gesture()
        self.assertTrue(self.area.mask(_SHAPE)[20, 20])
        self.assertFalse(self.area.mask(_SHAPE)[40, 40])

    def test_a_drag_may_start_outside_the_image_and_selects_only_the_inside(self) -> None:
        self._pick(AreaSelectionMode.RECTANGLE)
        tool = self.image_panel._area_tool
        self.assertTrue(tool.begin_gesture(-10.0, -10.0))
        tool.update_gesture(20.0, 30.0)
        tool.end_gesture()
        mask = self.area.mask(_SHAPE)
        self.assertEqual(int(mask.sum()), 30 * 20)  # rows 0..29, cols 0..19
        self.assertTrue(mask[0, 0])
        self.assertFalse(mask[0, 25])

    def test_lasso_excursion_outside_adds_no_outline_along_the_edge(self) -> None:
        """The true shape crosses the left edge only between y~8.6 and y~51.4;
        points dragged far outside must not make the border look selected
        beyond that span (clamping them onto the edge used to)."""
        self._pick(AreaSelectionMode.LASSO)
        tool = self.image_panel._area_tool
        tool.begin_gesture(5.0, 10.0)
        for point in [(40.0, 10.0), (40.0, 50.0), (5.0, 50.0), (-30.0, 60.0), (-30.0, 0.0)]:
            tool.update_gesture(*point)
        tool.end_gesture()
        path = tool._ants_item.path()
        spans = []
        for i in range(1, path.elementCount()):
            a, b = path.elementAt(i - 1), path.elementAt(i)
            if abs(a.x) < 1e-6 and abs(b.x) < 1e-6 and abs(a.y - b.y) > 1.0:
                spans.append((min(a.y, b.y), max(a.y, b.y)))
        self.assertTrue(spans)
        for low, high in spans:
            self.assertGreaterEqual(low, 8.0)
            self.assertLessEqual(high, 52.0)

    def test_a_drag_wholly_outside_the_image_selects_nothing(self) -> None:
        self._pick(AreaSelectionMode.RECTANGLE)
        tool = self.image_panel._area_tool
        tool.begin_gesture(-20.0, 10.0)
        tool.update_gesture(-5.0, 30.0)
        tool.end_gesture()
        self.assertFalse(self.area.has_selection())

    def test_ants_animate_only_while_an_outline_exists(self) -> None:
        tool = self.image_panel._area_tool
        self.assertFalse(tool.is_animating())
        self.area.set_rectangle(5, 5, 20, 20)
        self.assertTrue(tool.is_animating())
        self.area.clear()
        self.assertFalse(tool.is_animating())

    def _border_points(self, path) -> list[tuple[float, float]]:
        """Outline segments running along the image's left border (x == 0)
        inside the selection's own rows (10..30), as (y_start, y_end) pairs."""
        found = []
        for i in range(1, path.elementCount()):
            a, b = path.elementAt(i - 1), path.elementAt(i)
            if abs(a.x) < 1e-6 and abs(b.x) < 1e-6 and abs(a.y - b.y) > 5.0 and min(a.y, b.y) >= 10.0 - 1e-6 and max(a.y, b.y) <= 30.0 + 1e-6:
                found.append((a.y, b.y))
        return found

    def test_outline_is_the_editable_regions_boundary_in_every_invert_state(self) -> None:
        """Selection dragged out past the left edge. The editable region
        touches the left border there in the plain state (so the outline
        includes it) and does NOT when inverted (so the outline must not run
        along it). Checked over repeated inverts - an earlier version only got
        the first one right."""
        tool = self.image_panel._area_tool
        self.area.set_rectangle(-5, 10, 20, 30)  # covers cols 0..19, rows 10..29
        for _ in range(5):
            border = self._border_points(tool._ants_item.path())
            if self.area.is_inverted():
                self.assertEqual(border, [])
            else:
                self.assertNotEqual(border, [])
            self.assertGreater(tool._ants_item.path().elementCount(), 0)
            self.area.invert()

    def test_inverted_outline_still_encloses_the_far_image_border(self) -> None:
        self.area.set_rectangle(-5, 10, 20, 30)
        self.area.invert()
        rect = self.image_panel._area_tool._ants_item.path().boundingRect()
        self.assertAlmostEqual(rect.width(), float(_SHAPE[1]))
        self.assertAlmostEqual(rect.height(), float(_SHAPE[0]))

    def test_whole_image_selection_outlines_the_image_border(self) -> None:
        self.area.set_rectangle(-5, -5, 500, 500)
        rect = self.image_panel._area_tool._ants_item.path().boundingRect()
        self.assertAlmostEqual(rect.width(), float(_SHAPE[1]))

    # -- ribbon / general group -------------------------------------------------

    def test_the_selection_tools_sit_beside_the_image_not_in_the_ribbon_tabs(self) -> None:
        panel = self.image_panel
        toolbar = panel._selection_tools.toolbar
        self.assertFalse(hasattr(panel, "_area_pickers"), "the per-tab pickers are gone (2026-11-14)")
        self.assertTrue(toolbar.isVisible())
        self.assertIs(toolbar.parentWidget(), panel._view.parentWidget(), "beside the image canvas")
        self.assertLess(toolbar.geometry().right(), panel._view.geometry().left() + 1, "left of the image")
        for tab in (panel._mask_tab, panel._roi_tab, panel._view_tab):
            self.assertFalse(tab.isAncestorOf(toolbar))
        self.assertIs(panel._cursor_overlay.icon_label.parent(), panel._view_tab._display_range_controls)

    def test_cursor_readout_text_goes_to_the_canvas_not_the_button(self) -> None:
        overlay = self.image_panel._cursor_overlay
        overlay.toggle()
        self.image_panel._cursor_readout.set_text("(3, 4) = 150")
        self.assertEqual(self.image_panel._cursor_readout.label.text(), "(3, 4) = 150")
        self.assertFalse(self.image_panel._cursor_readout.label.isHidden())
        self.assertEqual(overlay.icon_label.text(), "")
        overlay.toggle()
        self.assertTrue(self.image_panel._cursor_readout.label.isHidden())

    def _open_tab(self, name: str) -> None:
        """Click the ribbon tab by its name: the tab order has changed before and will again."""
        ribbon = self.image_panel._tool_ribbon
        ribbon._tab_buttons[ribbon._category_names.index(name)].click()

    def test_ribbon_announces_category_changes(self) -> None:
        seen: list[str] = []
        self.image_panel.ribbon_category_changed.connect(seen.append)
        self._open_tab("Mask")
        self.assertEqual(seen, ["Mask"])
        self.assertEqual(self.image_panel.active_ribbon_category(), "Mask")

    # -- histogram -----------------------------------------------------------------

    def _counts_mode(self) -> None:
        self.histogram._show_settings_dialog()
        self.histogram._settings_dialog.axis_mode_combo.setCurrentIndex(1)

    def _all_total(self) -> float:
        _, y = self.histogram._plot._all_pixels_curve.getData()
        return float(np.sum(y))

    def test_histogram_ignores_the_selection_unless_its_ribbon_tab_is_open(self) -> None:
        self._counts_mode()
        self._open_tab("Mask")
        self.area.set_rectangle(0, 0, 40, 64)
        _pump()
        self.assertEqual(self.image_panel.active_ribbon_category(), "Mask")
        self.assertAlmostEqual(self._all_total(), float(_SHAPE[0] * _SHAPE[1]))

    def test_histogram_narrows_to_the_selection_when_its_tab_is_open(self) -> None:
        self._counts_mode()
        self._open_tab("View")
        self.assertEqual(self.image_panel.active_ribbon_category(), "View")
        self.area.set_rectangle(0, 0, 40, 64)
        _pump()
        self.assertAlmostEqual(self._all_total(), 40.0 * 64.0)
        self.area.invert()
        _pump()
        self.assertAlmostEqual(self._all_total(), 40.0 * 64.0)  # the other half, same size
        self.area.clear()
        _pump()
        self.assertAlmostEqual(self._all_total(), float(_SHAPE[0] * _SHAPE[1]))

    def test_selection_does_not_move_the_highlight_range(self) -> None:
        before = self.highlight_range.current_range()
        self._open_tab("View")
        self.area.set_rectangle(0, 0, 8, 8)  # only flat 150 px inside
        _pump()
        self.assertEqual(self.highlight_range.current_range(), before)

    # -- mask editing -----------------------------------------------------------------

    # (2026-11-15: the Mask tab's Histogram selection and Morphology tools moved into the selection toolbar; the same three rules, through
    # "Select by value", "Selection from mask" + "Modify selection" + "Replace", and the lock.)

    def test_select_by_value_reaches_the_whole_image_without_a_selection_or_lock(self) -> None:
        self.highlight_range.set_range(2000.0, 4000.0)  # both bright blocks
        self.image_panel._selection_tools.value_selector.run_for_mode(AreaSelectionMode.HISTOGRAM, "replace")
        selected = self.area.selected_mask(_SHAPE)
        self.assertTrue(selected[15, 15])
        self.assertTrue(selected[45, 55])

    def test_select_by_value_in_intersect_mode_only_reaches_inside_the_selection(self) -> None:
        self.highlight_range.set_range(2000.0, 4000.0)  # both bright blocks
        self.area.set_rectangle(0, 0, 40, 64)  # left block only
        self.image_panel._selection_tools.value_selector.run_for_mode(AreaSelectionMode.HISTOGRAM, "intersect")
        selected = self.area.selected_mask(_SHAPE)
        self.assertTrue(selected[15, 15])
        self.assertFalse(selected[45, 55])

    def test_growing_a_mask_through_the_selection_tools_leaves_the_mask_outside_the_lock_alone(self) -> None:
        base = np.zeros(_SHAPE, dtype=bool)
        base[10:20, 10:20] = True
        base[40:50, 50:60] = True
        self.mask.set_mask_change(_FRAME, "persistent", base)
        self.area.set_rectangle(0, 0, 40, 64)  # left block only (x0, y0, x1, y1)
        self.image_panel.selection_regions.lock_to_selection()
        editor = self.image_panel._mask_tab._selection_editor
        self.assertGreater(editor.from_mask("replace"), 0)
        self.image_panel._selection_tools.modify_menu.radius_spin.setValue(3)
        self.image_panel._selection_tools.modify_menu.apply("dilate")
        self.assertGreater(editor.replace(), 0)
        _, mask, _scope = self.mask.resolve_mask_source(_FRAME)
        self.assertGreater(int(mask[5:25, 5:25].sum()), 100)  # left block grew
        np.testing.assert_array_equal(mask[35:55, 45:65], base[35:55, 45:65])  # right block untouched

    # -- ROI --------------------------------------------------------------------------

    def test_roi_cannot_be_dragged_outside_the_selection(self) -> None:
        roi_id = self.roi_toolbox.add_roi(20.0, 20.0)
        self.area.set_rectangle(0, 0, 40, 64)
        self.image_panel._on_drag(roi_id, 60.0, 20.0)  # outside
        self.assertAlmostEqual(self.roi_toolbox.roi_by_id(roi_id).center_x, 20.0)
        self.image_panel._on_drag(roi_id, 30.0, 25.0)  # inside
        self.assertAlmostEqual(self.roi_toolbox.roi_by_id(roi_id).center_x, 30.0)

    def test_select_rois_in_selection_takes_the_ones_inside_or_partly_inside(self) -> None:
        """Maintainer (2026-11-15): the selection tools' right-click "Select ROIs in selection" - ROIs inside or partly
        inside. Selection = cols 0..39; ROIs of diameter 20 at x 20 (inside), 45 (reaches x 35: partly), 70 (outside)."""
        inside = self.roi_toolbox.add_roi(20.0, 20.0)
        partly = self.roi_toolbox.add_roi(45.0, 20.0)
        outside = self.roi_toolbox.add_roi(70.0, 50.0)
        self.area.set_rectangle(0, 0, 40, 64)
        selection = self.image_panel._selection
        selection.set_roi_selection({outside})
        self.image_panel._interaction.select_rois_in_selection()
        self.assertEqual(set(selection.selected_roi_ids()), {inside, partly}, "replaces the ROI selection")
        self.area.invert()  # an inverted selection: the ROIs touching the outside
        self.image_panel._interaction.select_rois_in_selection()
        self.assertEqual(set(selection.selected_roi_ids()), {partly, outside})

    def test_rois_touching_is_an_exact_disc_square_overlap(self) -> None:
        roi_id = self.roi_toolbox.add_roi(30.5, 30.5)  # radius 10
        queries = self.image_panel._roi_queries
        mask = np.zeros(_SHAPE, dtype=bool)
        mask[30, 41] = True  # square x 41..42: nearest point 10.5 px away - outside the disc
        self.assertEqual(queries.rois_touching(mask), set())
        mask[30, 40] = True  # square x 40..41: nearest point 9.5 px away - overlaps
        self.assertEqual(queries.rois_touching(mask), {roi_id})
        self.assertEqual(queries.rois_touching(np.zeros(_SHAPE, dtype=bool)), set())

    def test_the_selection_toolbar_is_as_narrow_as_its_icons(self) -> None:
        """Maintainer (2026-11-15): "as narrow as possible" - the icons had ~5 px to the left edge."""
        toolbar = self._toolbar()
        button = toolbar.tool_button(AreaSelectionMode.RECTANGLE)
        self.assertEqual(toolbar.width(), button.width())
        self.assertEqual(button.width(), button.iconSize().width() + 4, "2 px each side of the icon")
        self.assertEqual(button.x(), 0)
        # "not symmetric": pyqtgraph's 9 px layout margin no longer sits between the icons and the image.
        self.assertLessEqual(self.image_panel._plot.vb.sceneBoundingRect().left(), 2.0)

    def test_in_selection_gate_used_by_add_roi(self) -> None:
        self.area.set_rectangle(0, 0, 40, 64)
        self.assertFalse(self.image_panel._in_selection(60.0, 20.0))
        self.assertTrue(self.image_panel._in_selection(20.0, 20.0))
        self.area.clear()
        self.assertTrue(self.image_panel._in_selection(60.0, 20.0))


if __name__ == "__main__":
    unittest.main()
