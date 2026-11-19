"""A selected rectangle's reference border can be dragged like a circle's ring; the cursors; the speed of the handles (2026-11-18, maintainer's requests).

On the real Image panel (offscreen), driven through the gesture object and hand-built events, never by screen coordinate:

- **Border drag.** The inner edge of the band changes the gap (every selected rectangle gets it, each keeps its thickness), the outer edge the thickness (each keeps its
  gap); one undo step for the whole drag; a reference ROI, a circle, an unselected rectangle and hidden reference rings are not touched or not grabbable.
- **Cursors.** The knob shows the rotate cursor (not the open hand) on hover and for the whole turning drag, even when the press came with no hover before; an edge shows the
  two-headed arrow along its own axis, not along the radius.
- **Speed.** The handles are worked out once per change of the rectangles or the zoom, not once per ``boundingRect()`` call (Qt makes several per paint): that was why 150
  selected rectangles painted 3.4 times slower than 150 selected circles.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

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
    from lspr_imaging_app.domain.roi_shapes import Border, Rectangle
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image import rectangle_handles_item as handles_module
    from lspr_imaging_app.panels.image.roi_gestures import KNOB_PX, MIN_RING_GAP_PX
    from lspr_imaging_app.panels.image.resize_cursor import resize_cursor
    from lspr_imaging_app.panels.image.rotate_cursor import rotate_cursor
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.shape_hits import ZONE_BORDER_INNER, ZONE_BORDER_OUTER, rectangle_handle_points
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_image_panel import _pump, _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

GAP, THICKNESS = 5.0, 6.0


class _Drag:
    def __init__(self, scene_pos: QPointF, *, start: bool, finish: bool, down: QPointF | None = None) -> None:
        self._pos, self._start, self._finish = scene_pos, start, finish
        self._down = scene_pos if down is None else down

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._pos

    def buttonDownScenePos(self) -> QPointF:  # noqa: N802
        return self._down

    def button(self) -> Qt.MouseButton:
        return Qt.MouseButton.LeftButton

    def isStart(self) -> bool:  # noqa: N802
        return self._start

    def isFinish(self) -> bool:  # noqa: N802
        return self._finish

    def modifiers(self) -> Qt.KeyboardModifier:
        return Qt.KeyboardModifier.NoModifier


class RectangleBorderPanelTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), self.toolbox,
            self.selection, ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(), mask_scope=MaskScopeModule(),
        )
        self.panel.resize(900, 700)
        self.panel.show()
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump()
        self.panel._plot.vb.setRange(xRange=(0.0, 80.0), yRange=(0.0, 64.0), padding=0.0)
        self.panel._tool_ribbon.set_category("ROIs")
        _pump(0.1)
        self.toolbox.set_detection_settings(replace(self.toolbox.detection_settings(), border_gap_px=GAP, border_thickness_px=THICKNESS))
        self.rect_a = self.toolbox.add_shaped_roi(30.0, 34.0, Rectangle(20.0, 10.0, 0.0))
        self.rect_b = self.toolbox.add_shaped_roi(60.0, 34.0, Rectangle(16.0, 8.0, 0.0))
        self.circle = self.toolbox.add_roi(15.0, 12.0, sample_diameter_px=10.0)
        _pump(0.2)
        undo_manager.clear()
        self.gestures = self.panel._interaction._roi_gestures
        self.assertTrue(self.panel._roi_overlay.reference.visible, "the reference rings are shown by default")

    def tearDown(self) -> None:
        self.panel._roi_overlay.reference.visible = True
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()
        undo_manager.clear()

    # -- helpers ---------------------------------------------------------------------------------

    def border_of(self, roi_id: int) -> Border | None:
        return self.toolbox.roi_by_id(roi_id).border

    def view_centre(self, roi_id: int) -> tuple[float, float]:
        queries = self.panel._roi_queries
        index = [r.area_roi_id for r in self.toolbox.rois()].index(roi_id)
        centre = queries.view_centers((queries._current_cube(), queries._current_wavelength()), queries.current_affine())[index]
        return float(centre[0]), float(centre[1])

    def above_top_edge(self, roi_id: int, offset: float) -> tuple[float, float]:
        """The view point ``offset`` (image pixels) above the top edge of an unturned rectangle, in the middle of that edge (negative: inside it)."""
        x, y = self.view_centre(roi_id)
        return x, y - self.toolbox.roi_by_id(roi_id).shape.height_px / 2.0 - offset

    def drag(self, start: tuple[float, float], *path: tuple[float, float]) -> bool:
        claimed = self.gestures.begin(*start, additive=False)
        for point in path:
            self.gestures.update(*point)
        self.gestures.end(*(path[-1] if path else start), additive=False)
        return claimed

    def scene(self, point: tuple[float, float]) -> QPointF:
        return self.panel._plot.vb.mapViewToScene(QPointF(*point))

    def hover(self, point: tuple[float, float]) -> None:
        self.panel._interaction.on_scene_moved(self.scene(point))

    def cursor_shape(self) -> Qt.CursorShape:
        return self.panel._view.viewport().cursor().shape()

    # -- the border drag --------------------------------------------------------------------------

    def test_a_press_on_each_edge_of_the_band_finds_its_zone_and_the_middle_of_the_band_does_not(self) -> None:
        self.selection.set_roi_selection({self.rect_a})
        self.assertEqual(self.gestures.hit(*self.above_top_edge(self.rect_a, GAP)).zone, ZONE_BORDER_INNER)
        self.assertEqual(self.gestures.hit(*self.above_top_edge(self.rect_a, GAP + THICKNESS)).zone, ZONE_BORDER_OUTER)
        mid = self.gestures.hit(*self.above_top_edge(self.rect_a, GAP + THICKNESS / 2.0))
        self.assertIsNone(mid, "the middle of the band is nobody's handle (a circle's ring is the same)")

    def test_dragging_the_inner_edge_changes_the_gap_and_keeps_the_thickness_in_one_undo_step(self) -> None:
        self.selection.set_roi_selection({self.rect_a})
        self.assertIsNone(self.border_of(self.rect_a), "it follows the shared default until it is edited")
        steps = [self.above_top_edge(self.rect_a, offset) for offset in (5.5, 6.5, 7.5, 8.0)]
        self.assertTrue(self.drag(self.above_top_edge(self.rect_a, GAP), *steps))
        border = self.border_of(self.rect_a)
        self.assertAlmostEqual(border.gap_px, 8.0)
        self.assertAlmostEqual(border.thickness_px, THICKNESS)
        undo_manager.undo()
        self.assertIsNone(self.border_of(self.rect_a), "one undo step puts back what followed the default")
        self.assertFalse(undo_manager.can_undo)

    def test_dragging_the_outer_edge_changes_the_thickness_and_keeps_the_gap(self) -> None:
        self.selection.set_roi_selection({self.rect_a})
        self.assertTrue(self.drag(self.above_top_edge(self.rect_a, GAP + THICKNESS), self.above_top_edge(self.rect_a, 14.0)))
        border = self.border_of(self.rect_a)
        self.assertAlmostEqual(border.gap_px, GAP)
        self.assertAlmostEqual(border.thickness_px, 14.0 - GAP)

    def test_every_selected_rectangle_gets_the_gap_a_circle_and_an_unselected_one_are_left(self) -> None:
        other = self.toolbox.add_shaped_roi(30.0, 54.0, Rectangle(12.0, 12.0, 0.0))
        _pump(0.1)
        undo_manager.clear()
        self.selection.set_roi_selection({self.rect_a, self.rect_b, self.circle})
        self.drag(self.above_top_edge(self.rect_a, GAP), self.above_top_edge(self.rect_a, 8.0))
        for roi_id in (self.rect_a, self.rect_b):
            border = self.border_of(roi_id)
            self.assertAlmostEqual(border.gap_px, 8.0, msg=str(roi_id))
            self.assertAlmostEqual(border.thickness_px, THICKNESS, msg=str(roi_id))
        self.assertIsNone(self.border_of(self.circle), "a circle's ring is its own business")
        self.assertIsNone(self.border_of(other), "unselected")

    def test_each_rectangle_keeps_its_own_gap_when_the_outer_edge_is_dragged(self) -> None:
        self.toolbox.set_borders({self.rect_b: Border(2.0, 6.0)})
        undo_manager.clear()
        self.selection.set_roi_selection({self.rect_a, self.rect_b})
        self.drag(self.above_top_edge(self.rect_a, GAP + THICKNESS), self.above_top_edge(self.rect_a, 13.0))
        self.assertAlmostEqual(self.border_of(self.rect_a).thickness_px, 13.0 - GAP)
        self.assertAlmostEqual(self.border_of(self.rect_b).gap_px, 2.0)
        self.assertAlmostEqual(self.border_of(self.rect_b).thickness_px, 13.0 - 2.0)  # the same outer distance, so a different thickness

    def test_the_inner_edge_cannot_go_past_the_middle_of_the_shorter_side(self) -> None:
        self.selection.set_roi_selection({self.rect_a, self.rect_b})
        self.drag(self.above_top_edge(self.rect_a, GAP), self.above_top_edge(self.rect_a, -5.0))  # to the middle of A (half of its height is 5)
        self.assertAlmostEqual(self.border_of(self.rect_a).gap_px, -5.0)
        self.assertAlmostEqual(self.border_of(self.rect_b).gap_px, -4.0, msg="B is 8 high: half is 4")

    def test_the_outer_edge_keeps_a_minimum_thickness(self) -> None:
        self.selection.set_roi_selection({self.rect_a})
        self.drag(self.above_top_edge(self.rect_a, GAP + THICKNESS), self.above_top_edge(self.rect_a, 1.0))  # dragged in past the inner edge
        self.assertAlmostEqual(self.border_of(self.rect_a).thickness_px, MIN_RING_GAP_PX / 2.0)

    def test_a_reference_roi_has_no_band_to_grab_and_is_left_alone(self) -> None:
        self.toolbox.set_roi_role([self.rect_b], "reference")
        _pump(0.1)
        undo_manager.clear()
        self.selection.set_roi_selection({self.rect_a, self.rect_b})
        self.assertNotIn(getattr(self.gestures.hit(*self.above_top_edge(self.rect_b, GAP)), "zone", None), (ZONE_BORDER_INNER, ZONE_BORDER_OUTER))
        self.drag(self.above_top_edge(self.rect_a, GAP), self.above_top_edge(self.rect_a, 8.0))
        self.assertAlmostEqual(self.border_of(self.rect_a).gap_px, 8.0)
        self.assertIsNone(self.border_of(self.rect_b))

    def test_an_unselected_rectangles_band_is_not_a_handle(self) -> None:
        self.selection.set_roi_selection({self.rect_b})
        self.assertNotEqual(getattr(self.gestures.hit(*self.above_top_edge(self.rect_a, GAP)), "zone", None), ZONE_BORDER_INNER)

    def test_hidden_reference_rings_cannot_be_grabbed(self) -> None:
        self.selection.set_roi_selection({self.rect_a})
        self.panel._roi_overlay.reference.visible = False
        self.assertNotIn(getattr(self.gestures.hit(*self.above_top_edge(self.rect_a, GAP)), "zone", None), (ZONE_BORDER_INNER, ZONE_BORDER_OUTER))
        self.panel._roi_overlay.reference.visible = True
        self.assertEqual(self.gestures.hit(*self.above_top_edge(self.rect_a, GAP)).zone, ZONE_BORDER_INNER)

    def test_a_press_nearer_to_the_rectangles_own_edge_than_to_a_thin_gap_still_resizes_the_rectangle(self) -> None:
        """The nearer of the rectangle's own edge and the band's inner edge wins, so a small gap does not hide the width / height handles."""
        self.toolbox.set_borders({self.rect_a: Border(1.0, 6.0)})
        undo_manager.clear()
        self.selection.set_roi_selection({self.rect_a})
        pixel = float(self.gestures._view_pixel_size())
        near_edge = self.above_top_edge(self.rect_a, 0.3 * pixel)  # a hair outside the top edge: its own edge is the nearest
        self.assertEqual(self.gestures.hit(*near_edge).zone, "rect_h")
        near_band = self.above_top_edge(self.rect_a, 1.0)  # on the band's inner edge, 1 from the rectangle's own
        hit = self.gestures.hit(*near_band)
        self.assertEqual(hit.zone, ZONE_BORDER_INNER)

    # -- cursors ---------------------------------------------------------------------------------

    def knob(self, roi_id: int) -> tuple[float, float]:
        roi = self.toolbox.roi_by_id(roi_id)
        return rectangle_handle_points(roi.shape, self.view_centre(roi_id), np.eye(2), KNOB_PX * float(self.gestures._view_pixel_size()))["knob"]

    def test_the_knob_shows_the_rotate_cursor_not_the_open_hand(self) -> None:
        self.selection.set_roi_selection({self.rect_a})
        self.hover(self.knob(self.rect_a))
        self.assertEqual(self.cursor_shape(), Qt.CursorShape.BitmapCursor, "a cursor of our own")
        self.assertIs(self.panel._interaction._roi_cursor, rotate_cursor())
        self.assertNotEqual(self.cursor_shape(), Qt.CursorShape.OpenHandCursor)
        self.hover((70.0, 60.0))  # empty image: the plain cursor again
        self.assertNotEqual(self.cursor_shape(), Qt.CursorShape.BitmapCursor)

    def test_the_rotate_cursor_stays_for_the_whole_turning_drag(self) -> None:
        self.selection.set_roi_selection({self.rect_a})
        knob = self.knob(self.rect_a)
        centre = self.view_centre(self.rect_a)
        handler = self.panel._on_left_drag_event
        down = self.scene(knob)  # no hover before: the press alone must bring the cursor
        self.assertEqual(self.cursor_shape(), Qt.CursorShape.ArrowCursor)
        handler(_Drag(down, start=True, finish=False, down=down))
        self.assertEqual(self.cursor_shape(), Qt.CursorShape.BitmapCursor)
        for angle in (0.2, 0.5, 0.9):  # round the centre
            radius = float(np.hypot(knob[0] - centre[0], knob[1] - centre[1]))
            point = (centre[0] + radius * np.sin(angle), centre[1] - radius * np.cos(angle))
            handler(_Drag(self.scene(point), start=False, finish=False, down=down))
            self.hover(point)  # the hover handler runs during a drag too: it must leave the cursor alone
            self.assertEqual(self.cursor_shape(), Qt.CursorShape.BitmapCursor)
        handler(_Drag(self.scene(point), start=False, finish=True, down=down))
        self.assertGreater(self.toolbox.roi_by_id(self.rect_a).shape.angle_deg, 10.0, "it did turn")
        self.assertFalse(self.gestures.rotating)

    def test_an_edge_arrow_follows_the_edges_own_axis_not_the_radius(self) -> None:
        self.selection.set_roi_selection({self.rect_a})
        x, y = self.view_centre(self.rect_a)
        self.hover((x + 10.0, y + 3.0))  # on the right edge, well below its middle: the radius there is diagonal
        self.assertEqual(self.cursor_shape(), Qt.CursorShape.BitmapCursor, "the arrow is drawn by `resize_cursor.py`")
        self.assertIs(self.panel._interaction._roi_cursor, resize_cursor(0.0))  # horizontal
        self.hover((x + 4.0, y - 5.0))  # on the top edge, off to the side of its middle
        self.assertIs(self.panel._interaction._roi_cursor, resize_cursor(90.0))  # vertical

    def drag_cursors(self, start: tuple[float, float], *path: tuple[float, float]) -> list[object]:
        """Press at ``start``, move through ``path`` with the button down, and the canvas cursor (our ``_roi_cursor``) after each move; the button is let go at the end."""
        handler = self.panel._on_left_drag_event
        down = self.scene(start)
        handler(_Drag(down, start=True, finish=False, down=down))
        seen = []
        for point in path:
            handler(_Drag(self.scene(point), start=False, finish=False, down=down))
            self.hover(point)  # the hover handler runs during a drag too, and must not undo what the drag set
            seen.append(self.panel._interaction._roi_cursor)
        handler(_Drag(self.scene(path[-1]), start=False, finish=True, down=down))
        return seen

    def test_an_edit_drag_says_when_it_starts_and_ends_so_other_panels_can_wait(self) -> None:
        """`edit_running`: True at the press of a resize / handle / border / move, False at the release; nothing for a rubber band."""
        events: list[bool] = []
        self.gestures.edit_running.connect(events.append)
        self.selection.set_roi_selection({self.circle})
        x, y = self.view_centre(self.circle)
        radius = self.toolbox.roi_by_id(self.circle).shape.diameter_px / 2.0
        self.drag((x + radius, y), (x + radius + 2.0, y))  # the circle's edge: a resize
        self.assertEqual(events, [True, False])
        events.clear()
        self.drag((x, y), (x + 1.0, y + 1.0))  # inside it: a move
        self.assertEqual(events, [True, False])
        events.clear()
        self.drag((70.0, 5.0), (75.0, 9.0))  # empty image: a rubber band, which only selects
        self.assertEqual(events, [])

    def test_a_circles_arrow_turns_with_the_pointer_while_it_is_being_resized(self) -> None:
        """2026-11-19: during the drag the arrow kept the direction it started with; it must keep lying along the radius, wherever the pointer goes round the circle."""
        self.selection.set_roi_selection({self.circle})
        x, y = self.view_centre(self.circle)
        radius = self.toolbox.roi_by_id(self.circle).shape.diameter_px / 2.0
        angles = (20.0, 75.0, 130.0, 200.0, 290.0)
        path = [(x + (radius + 2.0) * np.cos(np.radians(a)), y + (radius + 2.0) * np.sin(np.radians(a))) for a in angles]
        seen = self.drag_cursors((x + radius, y), *path)
        for angle, cursor in zip(angles, seen, strict=True):
            self.assertIs(cursor, resize_cursor(angle % 180.0), f"pointer at {angle} degrees round the centre")

    def test_a_rectangle_corners_arrow_follows_its_changing_proportions_while_it_is_being_resized(self) -> None:
        """A corner arrow lies along the diagonal: 26.6 degrees for the 20 x 10 rectangle, 45 once the pointer has made it square."""
        self.selection.set_roi_selection({self.rect_a})
        x, y = self.view_centre(self.rect_a)
        corner = rectangle_handle_points(self.toolbox.roi_by_id(self.rect_a).shape, (x, y), np.eye(2), KNOB_PX * float(self.gestures._view_pixel_size()))["se"]
        self.hover(corner)
        self.assertIs(self.panel._interaction._roi_cursor, resize_cursor(np.degrees(np.arctan2(5.0, 10.0))), "20 x 10: the diagonal is 26.6 degrees")
        seen = self.drag_cursors(corner, (x + 8.0, y + 8.0), (x + 5.0, y + 5.0))
        self.assertIs(seen[0], resize_cursor(45.0), "16 x 16")
        self.assertIs(seen[1], resize_cursor(45.0), "10 x 10")
        self.assertEqual(self.toolbox.roi_by_id(self.rect_a).shape.width_px, 10.0, "the drag did resize it")

    def test_the_border_band_shows_an_arrow_across_it(self) -> None:
        self.selection.set_roi_selection({self.rect_a})
        self.hover(self.above_top_edge(self.rect_a, GAP))
        self.assertIs(self.panel._interaction._roi_cursor, resize_cursor(90.0))
        x, y = self.view_centre(self.rect_a)
        self.hover((x + 10.0 + GAP, y + 1.0))  # beside the right edge
        self.assertIs(self.panel._interaction._roi_cursor, resize_cursor(0.0))

    def test_a_turned_rectangles_arrows_are_exactly_across_its_edges_not_snapped_to_45_degrees(self) -> None:
        """2026-11-19: Qt's own resize cursors have four directions; a rectangle turned 30 degrees got the arrow of 45 or 0, off by 15 or 30."""
        self.toolbox.resize_rectangles([self.rect_a], angle_deg=30.0)
        self.selection.set_roi_selection({self.rect_a})
        x, y = self.view_centre(self.rect_a)
        along_width = (np.cos(np.radians(30.0)), np.sin(np.radians(30.0)))  # the rectangle's own x axis on the screen (clockwise, y down)
        along_height = (-along_width[1], along_width[0])
        interaction = self.panel._interaction
        east = (x + 10.0 * along_width[0], y + 10.0 * along_width[1])  # the middle of its right edge
        self.hover(east)
        self.assertIs(interaction._roi_cursor, resize_cursor(30.0))
        south = (x + 5.0 * along_height[0], y + 5.0 * along_height[1])  # the middle of its bottom edge
        self.hover(south)
        self.assertIs(interaction._roi_cursor, resize_cursor(120.0))
        for wrong in (0.0, 45.0, 90.0, 135.0):
            self.assertIsNot(resize_cursor(30.0), resize_cursor(wrong))

    def test_a_circles_arrow_is_along_its_radius_at_any_angle(self) -> None:
        """Perpendicular to the edge all the way round (to the 3-degree step), not the nearest of four directions."""
        self.selection.set_roi_selection({self.circle})
        x, y = self.view_centre(self.circle)
        radius = self.toolbox.roi_by_id(self.circle).shape.diameter_px / 2.0
        for degrees in (0.0, 20.0, 37.0, 70.0, 100.0, 160.0, 200.0, 290.0, 345.0):
            self.hover((x + radius * np.cos(np.radians(degrees)), y + radius * np.sin(np.radians(degrees))))
            self.assertIs(self.panel._interaction._roi_cursor, resize_cursor(degrees % 180.0), f"at {degrees} degrees round the edge")

    # -- speed -----------------------------------------------------------------------------------

    def test_the_handles_are_worked_out_once_per_change_not_once_per_bounding_rect_call(self) -> None:
        self.selection.set_roi_selection({self.rect_a, self.rect_b})
        self.panel._draw_roi_overlay()
        item = self.panel._roi_overlay.rect_handles
        item.boundingRect()  # worked out for the current rectangles and zoom
        with mock.patch.object(handles_module, "rectangle_handle_arrays", wraps=handles_module.rectangle_handle_arrays) as arrays:
            for _ in range(25):
                item.boundingRect()
            self.panel._view.viewport().repaint()
            self.panel._view.viewport().repaint()
            self.assertEqual(arrays.call_count, 0, "nothing changed: the kept handles are used")
            self.selection.set_roi_selection({self.rect_a})
            self.panel._draw_roi_overlay()
            for _ in range(25):
                item.boundingRect()
            self.assertEqual(arrays.call_count, 1, "one calculation for the new rectangles, however often Qt asks for the bounds")

    def test_the_drawn_handles_are_where_they_can_be_grabbed(self) -> None:
        """Painted from the same numbers as the hit test: the pixel at a handle's centre is the handle's fill (the image is grey, the fill is not)."""
        self.selection.set_roi_selection({self.rect_a})
        self.panel._draw_roi_overlay()
        _pump(0.2)
        image = self.panel._view.viewport().grab().toImage()
        roi = self.toolbox.roi_by_id(self.rect_a)
        points = rectangle_handle_points(roi.shape, self.view_centre(self.rect_a), np.eye(2), KNOB_PX * float(self.gestures._view_pixel_size()))
        for name in ("ne", "sw", "n", "e", "knob"):
            at = self.panel._view.mapFromScene(self.scene(points[name]))
            colour = image.pixelColor(at.x(), at.y())
            self.assertEqual((colour.red(), colour.green(), colour.blue()), (248, 250, 252), name)
        far = self.panel._view.mapFromScene(self.scene((70.0, 60.0)))
        far_colour = image.pixelColor(far.x(), far.y())
        self.assertNotEqual((far_colour.red(), far_colour.green(), far_colour.blue()), (248, 250, 252))

    def test_no_handles_for_no_selected_rectangle(self) -> None:
        self.selection.set_roi_selection({self.circle})
        self.panel._draw_roi_overlay()
        self.assertEqual(self.panel._roi_overlay.rect_handles.count(), 0)
        self.assertTrue(self.panel._roi_overlay.rect_handles.boundingRect().isNull())

if __name__ == "__main__":
    unittest.main()
