"""Dragging the handles of a selected rectangle ROI on the real Image panel (phase 3 of apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md).

Drawn only for selected rectangles (`RectangleHandlesItem`); a press on an edge, a corner or the knob starts a resize or a turn (`RoiGestures`): the whole drag is
one undo step, every selected rectangle gets the width / height / angle under the cursor, a circle among the selection is untouched, and the hover cursor says
what a press would do. The geometry itself is `tests/unit/test_lspri_rectangle_handles.py`.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPointF, Qt

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.domain.roi_shapes import Circle, Rectangle
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.roi_gestures import KNOB_PX
    from lspr_imaging_app.panels.image.rotate_cursor import rotate_cursor
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.overlay_geometry import CELL_OFFSET
    from lspr_imaging_app.roi.shape_hits import ZONE_CORNER, ZONE_HEIGHT, ZONE_ROTATE, ZONE_WIDTH, rectangle_handle_points
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_image_panel import _pump, _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402


class RectangleHandlesPanelTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset_model = _write_dataset(Path(self._tmp.name))
        self.dataset = DatasetModule()
        self.toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), self.toolbox,
            self.selection, ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(), mask_scope=MaskScopeModule(),
        )
        self.panel.resize(900, 700)
        self.panel.show()
        self.dataset.load_dataset(self.dataset_model)
        _pump()
        self.panel._plot.vb.setRange(xRange=(0.0, 80.0), yRange=(0.0, 64.0), padding=0.0)
        self.panel._tool_ribbon.set_category("ROIs")
        _pump(0.1)
        self.rect_a = self.toolbox.add_shaped_roi(30.0, 34.0, Rectangle(20.0, 10.0, 0.0))
        self.rect_b = self.toolbox.add_shaped_roi(60.0, 34.0, Rectangle(16.0, 8.0, 0.0))
        self.circle = self.toolbox.add_roi(15.0, 12.0, sample_diameter_px=10.0)
        _pump(0.2)
        undo_manager.clear()
        self.gestures = self.panel._interaction._roi_gestures
        self.pixel = float(self.gestures._view_pixel_size())

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()
        undo_manager.clear()

    def shape(self, roi_id: int):
        return self.toolbox.roi_by_id(roi_id).shape

    @staticmethod
    def at(x: float, y: float) -> tuple[float, float]:
        """The view point over pixel-index coordinate ``(x, y)`` (what a ROI's centre is stored in): the canvas draws it half a cell further in (`CELL_OFFSET`)."""
        return x + CELL_OFFSET, y + CELL_OFFSET

    def handle(self, roi_id: int, name: str) -> tuple[float, float]:
        """Where the named handle is on the canvas (view coordinates)."""
        roi = self.toolbox.roi_by_id(roi_id)
        return rectangle_handle_points(roi.shape, self.at(roi.center_x, roi.center_y), np.eye(2), KNOB_PX * self.pixel)[name]

    def drag(self, start: tuple[float, float], end: tuple[float, float]) -> bool:
        claimed = self.gestures.begin(*start, additive=False)
        self.gestures.update(*end)
        self.gestures.end(*end, additive=False)
        return claimed

    def scene(self, x: float, y: float) -> QPointF:
        return self.panel._plot.vb.mapViewToScene(QPointF(x, y))

    # -- what is drawn --------------------------------------------------------------------------

    def test_handles_are_drawn_for_the_selected_rectangles_only(self) -> None:
        overlay = self.panel._roi_overlay
        self.selection.set_roi_selection({self.rect_a})
        self.panel._draw_roi_overlay()
        self.assertEqual(overlay.rect_handles.count(), 1)
        self.selection.set_roi_selection({self.rect_a, self.rect_b, self.circle})
        self.panel._draw_roi_overlay()
        self.assertEqual(overlay.rect_handles.count(), 2)  # the circle has none
        self.selection.set_roi_selection({self.circle})
        self.panel._draw_roi_overlay()
        self.assertEqual(overlay.rect_handles.count(), 0)
        self.selection.set_roi_selection(set())
        self.panel._draw_roi_overlay()
        self.assertEqual(overlay.rect_handles.count(), 0)

    # -- what a press finds ----------------------------------------------------------------------

    def test_a_press_on_each_handle_finds_its_zone(self) -> None:
        self.selection.set_roi_selection({self.rect_a})
        for name, zone in (("e", ZONE_WIDTH), ("w", ZONE_WIDTH), ("n", ZONE_HEIGHT), ("s", ZONE_HEIGHT), ("ne", ZONE_CORNER), ("sw", ZONE_CORNER), ("knob", ZONE_ROTATE)):
            hit = self.gestures.hit(*self.handle(self.rect_a, name))
            self.assertIsNotNone(hit, name)
            self.assertEqual((hit.roi_id, hit.zone), (self.rect_a, zone), name)
        self.assertEqual(self.gestures.hit(*self.at(30.0, 34.0)).zone, "body")  # the inside still moves it

    def test_an_unselected_rectangle_has_no_handles(self) -> None:
        self.selection.set_roi_selection({self.rect_b})
        self.assertNotEqual(getattr(self.gestures.hit(*self.handle(self.rect_a, "e")), "roi_id", None), self.rect_a)

    # -- what a drag does -------------------------------------------------------------------------

    def test_dragging_the_east_handle_sets_the_width_about_the_centre_in_one_undo_step(self) -> None:
        self.selection.set_roi_selection({self.rect_a})
        start = self.handle(self.rect_a, "e")
        self.assertTrue(self.drag(start, (start[0] + 5.0, start[1] + 3.0)))  # the sideways part does not matter for a width
        self.assertEqual(self.shape(self.rect_a), Rectangle(30.0, 10.0, 0.0))  # 2 x (45 - 30)
        self.assertEqual((self.toolbox.roi_by_id(self.rect_a).center_x, self.toolbox.roi_by_id(self.rect_a).center_y), (30.0, 34.0))
        undo_manager.undo()
        self.assertEqual(self.shape(self.rect_a), Rectangle(20.0, 10.0, 0.0))
        self.assertFalse(undo_manager.can_undo)

    def test_dragging_the_south_handle_and_a_corner(self) -> None:
        self.selection.set_roi_selection({self.rect_a})
        south = self.handle(self.rect_a, "s")
        self.drag(south, (south[0], south[1] + 4.0))
        self.assertEqual(self.shape(self.rect_a), Rectangle(20.0, 18.0, 0.0))  # 2 x (43 - 34)
        corner = self.handle(self.rect_a, "se")
        self.drag(corner, self.at(30.0 + 14.0, 34.0 + 3.0))
        self.assertEqual(self.shape(self.rect_a), Rectangle(28.0, 6.0, 0.0))

    def test_dragging_the_knob_turns_the_rectangle_about_its_centre(self) -> None:
        self.selection.set_roi_selection({self.rect_a})
        knob = self.handle(self.rect_a, "knob")
        self.drag(knob, self.at(50.0, 34.0))  # straight to the right of the centre: a quarter turn clockwise
        self.assertAlmostEqual(self.shape(self.rect_a).angle_deg, 90.0, places=6)
        self.assertEqual((self.shape(self.rect_a).width_px, self.shape(self.rect_a).height_px), (20.0, 10.0))
        undo_manager.undo()
        self.assertEqual(self.shape(self.rect_a).angle_deg, 0.0)

    def test_a_turned_rectangle_is_resized_along_its_own_axes(self) -> None:
        self.toolbox.resize_rectangles([self.rect_a], angle_deg=90.0)  # now its width runs down the screen
        undo_manager.clear()
        self.selection.set_roi_selection({self.rect_a})
        east = self.handle(self.rect_a, "e")  # the rectangle's own east is down the screen
        self.assertAlmostEqual(east[0], 30.0 + CELL_OFFSET)
        self.assertGreater(east[1], 34.0 + CELL_OFFSET)
        self.drag(east, self.at(30.0, 34.0 + 20.0))
        self.assertEqual(self.shape(self.rect_a), Rectangle(40.0, 10.0, 90.0))

    def test_every_selected_rectangle_gets_the_value_and_a_circle_is_left_alone(self) -> None:
        self.selection.set_roi_selection({self.rect_a, self.rect_b, self.circle})
        start = self.handle(self.rect_a, "e")
        self.drag(start, self.at(30.0 + 12.0, 34.0))
        self.assertEqual(self.shape(self.rect_a).width_px, 24.0)
        self.assertEqual(self.shape(self.rect_b).width_px, 24.0)  # the same value for each, as a circle's diameter is
        self.assertEqual((self.shape(self.rect_a).height_px, self.shape(self.rect_b).height_px), (10.0, 8.0))
        self.assertEqual(self.shape(self.circle), Circle(10.0))
        undo_manager.undo()  # one step for both
        self.assertEqual((self.shape(self.rect_a).width_px, self.shape(self.rect_b).width_px), (20.0, 16.0))

    def test_a_size_cannot_be_dragged_below_the_minimum(self) -> None:
        self.selection.set_roi_selection({self.rect_a})
        east = self.handle(self.rect_a, "e")
        self.drag(east, self.at(30.0, 34.0))  # onto the centre
        from lspr_imaging_app.roi.toolbox import MIN_SAMPLE_DIAMETER_PX

        self.assertEqual(self.shape(self.rect_a).width_px, MIN_SAMPLE_DIAMETER_PX)

    # -- the hover cursor -------------------------------------------------------------------------

    def test_the_hover_cursor_says_what_a_press_would_do(self) -> None:
        self.selection.set_roi_selection({self.rect_a})
        interaction = self.panel._interaction
        interaction._update_roi_hover_cursor(self.scene(*self.handle(self.rect_a, "knob")))
        self.assertIs(interaction._roi_cursor, rotate_cursor())  # the knob turns the rectangle: the rotate cursor, not the open hand
        interaction._update_roi_hover_cursor(self.scene(*self.handle(self.rect_a, "e")))
        self.assertEqual(interaction._roi_cursor, Qt.CursorShape.SizeHorCursor)
        interaction._update_roi_hover_cursor(self.scene(*self.at(30.0, 34.0)))
        self.assertEqual(interaction._roi_cursor, Qt.CursorShape.SizeAllCursor)


if __name__ == "__main__":
    unittest.main()
