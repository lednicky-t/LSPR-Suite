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

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

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
    from lspr_imaging_app.panels.image.general_group import AreaSelectionPicker
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import (
        AreaSelectionMode,
        AreaSelectionModule,
        HighlightRangeModule,
        ReferenceFrameModule,
        SelectionModule,
    )
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

_SHAPE = (64, 80)
_FRAME = (0, 500.0)


def _pump(seconds: float = 0.5) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
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
        _pump()

    def tearDown(self) -> None:
        self.image_panel._renderer.stop()
        self._tmp.cleanup()

    # -- picker / gesture / ants -------------------------------------------

    def _picker(self) -> AreaSelectionPicker:
        return self.image_panel._area_picker

    def _pick(self, mode: AreaSelectionMode) -> None:
        self._picker()._actions[mode].trigger()

    def test_picking_rectangle_arms_the_draw_tool_and_all_disarms_and_clears(self) -> None:
        self._pick(AreaSelectionMode.RECTANGLE)
        self.assertIs(self.active_tool.active(), ImageTool.SELECT_AREA)
        self.area.set_rectangle(5, 5, 20, 20)
        self._pick(AreaSelectionMode.ALL)
        self.assertIsNone(self.active_tool.active())
        self.assertFalse(self.area.has_selection())

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

    def test_general_group_sits_left_of_the_ribbon_with_cursor_and_picker(self) -> None:
        general = self.image_panel._general_row
        self.assertIs(self.image_panel._cursor_overlay.icon_label.parent(), general)
        self.assertIs(self._picker().parent(), general)
        ribbon = self.image_panel._tool_ribbon
        # The General row is the ribbon's own pinned leading column (2026-10-03), so it
        # sits inside the ribbon, left of the first tab - no longer left of the ribbon.
        self.assertTrue(ribbon.isAncestorOf(general))
        first_tab = ribbon._tab_buttons[0]
        self.assertLess(
            general.mapTo(self.image_panel, general.rect().topLeft()).x(),
            first_tab.mapTo(self.image_panel, first_tab.rect().topLeft()).x(),
        )
        # Same icon-button size for both, as requested.
        self.assertEqual(self.image_panel._cursor_overlay.icon_label.size(), self._picker().size())

    def test_cursor_readout_text_goes_to_the_canvas_not_the_button(self) -> None:
        overlay = self.image_panel._cursor_overlay
        overlay.toggle()
        self.image_panel._set_cursor_readout("(3, 4) = 150")
        self.assertEqual(self.image_panel._cursor_readout.text(), "(3, 4) = 150")
        self.assertFalse(self.image_panel._cursor_readout.isHidden())
        self.assertEqual(overlay.icon_label.text(), "")
        overlay.toggle()
        self.assertTrue(self.image_panel._cursor_readout.isHidden())

    def test_ribbon_announces_category_changes(self) -> None:
        seen: list[str] = []
        self.image_panel.ribbon_category_changed.connect(seen.append)
        self.image_panel._tool_ribbon._tab_buttons[1].click()
        self.assertEqual(seen, ["Image tools"])
        self.assertEqual(self.image_panel.active_ribbon_category(), "Image tools")

    # -- histogram -----------------------------------------------------------------

    def _counts_mode(self) -> None:
        self.histogram._show_settings_dialog()
        self.histogram._settings_dialog.axis_mode_combo.setCurrentIndex(1)

    def _all_total(self) -> float:
        _, y = self.histogram._plot._all_pixels_curve.getData()
        return float(np.sum(y))

    def test_histogram_ignores_the_selection_unless_its_ribbon_tab_is_open(self) -> None:
        self._counts_mode()
        self.image_panel._tool_ribbon._tab_buttons[1].click()
        self.area.set_rectangle(0, 0, 40, 64)
        _pump()
        self.assertEqual(self.image_panel.active_ribbon_category(), "Image tools")
        self.assertAlmostEqual(self._all_total(), float(_SHAPE[0] * _SHAPE[1]))

    def test_histogram_narrows_to_the_selection_when_its_tab_is_open(self) -> None:
        self._counts_mode()
        self.assertEqual(self.image_panel.active_ribbon_category(), "View")  # the first tab, open at start
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
        self.image_panel._tool_ribbon._tab_buttons[0].click()
        self.area.set_rectangle(0, 0, 8, 8)  # only flat 150 px inside
        _pump()
        self.assertEqual(self.highlight_range.current_range(), before)

    # -- mask editing -----------------------------------------------------------------

    def test_histogram_selection_add_only_reaches_inside_the_selection(self) -> None:
        self.highlight_range.set_range(2000.0, 4000.0)  # both bright blocks
        self.area.set_rectangle(0, 0, 40, 64)  # left block only
        self.image_panel._mask_edit_stack.widget(0)._editor.apply(subtract=False)
        _, mask, _scope = self.mask.resolve_mask_source(_FRAME)
        self.assertTrue(mask[15, 15])
        self.assertFalse(mask[45, 55])

    def test_histogram_selection_add_is_unrestricted_without_a_selection(self) -> None:
        self.highlight_range.set_range(2000.0, 4000.0)
        self.image_panel._mask_edit_stack.widget(0)._editor.apply(subtract=False)
        _, mask, _scope = self.mask.resolve_mask_source(_FRAME)
        self.assertTrue(mask[15, 15])
        self.assertTrue(mask[45, 55])

    def test_morphology_leaves_pixels_outside_the_selection_alone(self) -> None:
        base = np.zeros(_SHAPE, dtype=bool)
        base[10:20, 10:20] = True
        base[40:50, 50:60] = True
        self.mask.set_mask_change(_FRAME, "persistent", base)
        self.area.set_rectangle(0, 0, 40, 64)  # left block only
        self.image_panel._mask_edit_stack.widget(3)._apply("dilate")
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

    def test_in_selection_gate_used_by_add_roi(self) -> None:
        self.area.set_rectangle(0, 0, 40, 64)
        self.assertFalse(self.image_panel._in_selection(60.0, 20.0))
        self.assertTrue(self.image_panel._in_selection(20.0, 20.0))
        self.area.clear()
        self.assertTrue(self.image_panel._in_selection(60.0, 20.0))


if __name__ == "__main__":
    unittest.main()
