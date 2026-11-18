"""Rectangle and mask ROIs on the Image panel: what is drawn and what a click finds (phase 2.4 of apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md).

`RoiOverlay` draws circles and rectangles as rows of one item and each mask as its traced pixel outline, with the border of every shape; `RoiQueries` answers
"which ROI is under this point", "what does the ROI layer's wand pick here", "which ROIs touch this area selection" and "what can be grabbed" for every shape
(and the gesture hit test uses the same inside test). A session of circles only draws and answers exactly as before (the older tests of the overlay and the
gestures pin that).

No dataset and no window: a plot item, the overlay, and small stand-ins for the toolbox, the chromatic module and the selection.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import pyqtgraph as pg

    from lspr_imaging_app.domain.roi_model import AreaRoi, AreaRoiDetectionSettings
    from lspr_imaging_app.domain.roi_shapes import NO_BORDER, Border, Circle, MaskShape, Rectangle, RoiMask
    from lspr_imaging_app.panels.image.roi_gestures import hit_test
    from lspr_imaging_app.panels.image.roi_overlay import CIRCLE_POINTS, RoiOverlay
    from lspr_imaging_app.panels.image.roi_queries import RoiQueries
    from lspr_imaging_app.roi import rasterize as rz
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
CHROMATIC = np.array([[1.004, 0.0021, -2.4], [-0.0017, 0.9985, 1.3]])
DEFAULT_BORDER = Border(4.0, 4.0)
SHAPE = (100, 140)


def _centres(rois, affine) -> np.ndarray:
    points = np.array([(r.center_x, r.center_y) for r in rois], dtype=np.float64)
    return points @ np.asarray(affine, dtype=np.float64)[:, :2].T + np.asarray(affine, dtype=np.float64)[:, 2]


def _rect(roi_id=1, x=60.0, y=50.0, w=30.0, h=4.0, angle=45.0, border=Border(2.0, 4.0), **kwargs) -> AreaRoi:
    return AreaRoi(roi_id, x, y, Rectangle(w, h, angle), border=border, **kwargs)


def _mask(roi_id=2, x0=100, y0=40, border=Border(1.0, 3.0), **kwargs) -> AreaRoi:
    pixels = np.ones((9, 9), dtype=bool)
    pixels[3:6, 3:6] = False  # a hole in the middle
    return AreaRoi(roi_id, x0 + 4.0, y0 + 4.0, MaskShape(RoiMask(x0, y0, pixels)), border=border, **kwargs)


def _circle(roi_id=3, x=20.0, y=20.0, d=12.0, border=None, **kwargs) -> AreaRoi:
    return AreaRoi(roi_id, x, y, Circle(d), border=border, **kwargs)


class _Toolbox:
    def __init__(self, rois, affine) -> None:
        self._rois, self._affine = tuple(rois), np.asarray(affine, dtype=np.float64)

    def rois_at(self, _cube):
        return self._rois

    def rois(self):
        return self._rois

    def display_positions(self, _frame, affine):
        return _centres(self._rois, affine)

    def detection_settings(self):
        return AreaRoiDetectionSettings()


class _Chromatic:
    def __init__(self, affine) -> None:
        self._affine = affine

    def affine_for(self, _frame):
        return self._affine


class _Selection:
    def __init__(self, ids=()) -> None:
        self.ids = set(ids)

    def selected_roi_ids(self):
        return set(self.ids)


def _queries(rois, affine=IDENTITY, selected=()) -> RoiQueries:
    return RoiQueries(
        _Selection(selected), _Toolbox(rois, affine), _Chromatic(affine),
        current_cube=lambda: 1, current_wavelength=lambda: 500.0, reference_visible=lambda: True,
    )


class OverlayShapesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plot = pg.PlotItem()
        self.overlay = RoiOverlay(self.plot)

    def _draw(self, rois, affine=IDENTITY, selected=()):
        self.overlay.draw(rois, _centres(rois, affine), set(selected), affine, DEFAULT_BORDER)

    def test_circles_only_leave_the_mask_items_empty(self) -> None:
        self._draw([_circle(1, 20.0, 20.0), _circle(2, 50.0, 30.0)])
        self.assertEqual(self.overlay.sample_circles.circle_count(), 2)
        self.assertEqual((self.overlay.mask_samples.path_count(), self.overlay.mask_borders.path_count(), self.overlay.mask_selection.path_count()), (0, 0, 0))

    def test_a_rectangle_is_a_row_of_the_circles_item_drawn_as_a_rotated_rectangle(self) -> None:
        self._draw([_rect(angle=0.0, w=30.0, h=10.0)])
        xs, ys = self.overlay.sample_circles.outline_xy()
        self.assertEqual(xs.size, CIRCLE_POINTS)
        self.assertAlmostEqual(float(np.ptp(xs)), 30.0)
        self.assertAlmostEqual(float(np.ptp(ys)), 10.0)
        self.assertAlmostEqual(float(xs.min()), 45.0)  # around the centre (60, 50)
        self.assertAlmostEqual(float(ys.min()), 45.0)

    def test_a_mask_has_no_row_but_its_traced_outline_along_the_cells_of_its_pixels(self) -> None:
        self._draw([_rect(), _mask(x0=100, y0=40)])
        self.assertEqual(self.overlay.sample_circles.circle_count(), 1)  # the rectangle only
        self.assertEqual(self.overlay.mask_samples.path_count(), 1)
        box = self.overlay.mask_samples.paths()[0].boundingRect()
        self.assertEqual((box.x(), box.y(), box.width(), box.height()), (100.0, 40.0, 9.0, 9.0))  # the 9 x 9 cells of its pixels, nothing added or shifted
        self.assertGreater(self.overlay.mask_samples.paths()[0].elementCount(), 8)  # the hole's outline is in it too

    def test_a_masks_outline_is_its_pixels_through_the_affine_whatever_the_rois_centre_says(self) -> None:
        """The scan warps the mask's pixels by the affine and ignores the ROI centre (only the label point): so does the drawing."""
        mask = _mask(x0=100, y0=40)
        plain = self.overlay_path([mask], IDENTITY)
        moved = self.overlay_path([mask], CHROMATIC)
        corner = np.array([100.0, 40.0])  # the top-left corner of the first cell, in view cells; the pixel index is the view coordinate minus half a cell
        expected = CHROMATIC[:, :2] @ (corner - 0.5) + CHROMATIC[:, 2] + 0.5
        found = np.array([(p.x, p.y) for p in self._points(moved)])
        self.assertLess(float(np.min(np.hypot(*(found - expected).T))), 1e-9)
        self.assertEqual(len(found), len(self._points(plain)))
        mask.center_x, mask.center_y = 20.0, 70.0  # the label point is somewhere else now: the outline does not move
        again = np.array([(p.x, p.y) for p in self._points(self.overlay_path([mask], CHROMATIC))])
        self.assertTrue(np.allclose(again, found))

    def overlay_path(self, rois, affine):
        self._draw(rois, affine)
        return self.overlay.mask_samples.paths()[0]

    @staticmethod
    def _points(path):
        return [path.elementAt(i) for i in range(path.elementCount())]

    def test_the_selected_shapes_are_marked(self) -> None:
        rois = [_rect(1), _mask(2), _circle(3)]
        self._draw(rois, selected=(1, 2))
        xs, _ys = self.overlay.selection_curve.getData()
        self.assertEqual(int(np.isfinite(xs).sum()), CIRCLE_POINTS)  # the rectangle's row (the mask is marked by its own item)
        self.assertEqual(self.overlay.mask_selection.path_count(), 1)

    def test_a_border_is_drawn_for_every_shape_but_not_for_a_reference_roi_or_a_roi_without_one(self) -> None:
        rois = [_rect(1), _mask(2), _circle(3), _rect(4, x=30.0, y=70.0, role="reference"), _rect(5, x=100.0, y=80.0, border=NO_BORDER)]
        self._draw(rois)
        self.assertEqual(self.overlay.mask_borders.path_count(), 1)  # the mask's band
        xs, _ys = self.overlay.reference_curve.getData()
        finite_rows = int(np.isfinite(xs).sum()) // CIRCLE_POINTS
        self.assertEqual(finite_rows, 2 * len(rois))  # an inner and an outer row per ROI; the ones with nothing to draw sit on their centre

    def test_a_rectangles_border_edges_are_the_gap_and_gap_plus_thickness_outlines(self) -> None:
        self._draw([_rect(angle=0.0, w=30.0, h=10.0, border=Border(2.0, 4.0))])
        xs, ys = self.overlay.reference_curve.getData()
        rows_x = np.split(xs[np.isfinite(xs)], 2)
        rows_y = np.split(ys[np.isfinite(ys)], 2)
        self.assertAlmostEqual(float(np.ptp(rows_x[0])), 30.0 + 2 * 2.0)  # inner edge: the rectangle grown by the gap
        self.assertAlmostEqual(float(np.ptp(rows_x[1])), 30.0 + 2 * 6.0)  # outer edge: grown by the gap and the thickness
        self.assertAlmostEqual(float(np.ptp(rows_y[1])), 10.0 + 2 * 6.0)

    def test_hiding_the_parts_clears_the_mask_items(self) -> None:
        self._draw([_mask(), _rect()], selected=(2,))
        self.overlay.sample.visible = False
        self.overlay.reference.visible = False
        self._draw([_mask(), _rect()], selected=(2,))
        self.assertEqual((self.overlay.mask_samples.path_count(), self.overlay.mask_selection.path_count(), self.overlay.mask_borders.path_count()), (0, 0, 0))

    def test_clear_empties_everything(self) -> None:
        self._draw([_mask(), _rect()], selected=(2,))
        self.overlay.clear()
        self.assertEqual((self.overlay.mask_samples.path_count(), self.overlay.mask_selection.path_count(), self.overlay.mask_borders.path_count()), (0, 0, 0))
        self.assertEqual(self.overlay.sample_circles.circle_count(), 0)


class QueriesShapesTests(unittest.TestCase):
    def test_a_point_is_on_a_rectangle_only_inside_it_not_in_its_reach_circle(self) -> None:
        rois = [_rect(1, 60.0, 50.0, 30.0, 4.0, 45.0)]
        q = _queries(rois)
        along = (60.0 + 10.0 * np.cos(np.pi / 4), 50.0 + 10.0 * np.sin(np.pi / 4))  # on the long axis, inside
        across = (60.0 + 10.0 * np.cos(np.pi / 4), 50.0 - 10.0 * np.sin(np.pi / 4))  # 10 px off the axis: inside the reach circle (15.1), outside the rectangle
        self.assertEqual(q.roi_at(*along), 1)
        self.assertIsNone(q.roi_at(*across))

    def test_a_point_in_a_masks_hole_is_not_on_it_and_one_in_a_pixels_cell_is(self) -> None:
        q = _queries([_mask(2, 100, 40)])
        self.assertEqual(q.roi_at(100.5, 40.5), 2)
        self.assertIsNone(q.roi_at(104.5, 44.5))  # the hole
        self.assertIsNone(q.roi_at(120.5, 44.5))

    def test_the_nearest_centre_wins_so_a_small_circle_inside_a_rectangle_stays_reachable(self) -> None:
        rois = [_rect(1, 60.0, 50.0, 40.0, 30.0, 0.0), _circle(2, 66.0, 50.0, 6.0)]
        q = _queries(rois)
        self.assertEqual(q.roi_at(66.0, 50.0), 2)
        self.assertEqual(q.roi_at(50.0, 50.0), 1)

    def test_it_agrees_under_a_chromatic_affine(self) -> None:
        rois = [_rect(1, 60.0, 50.0, 30.0, 4.0, 45.0)]
        q = _queries(rois, CHROMATIC)
        centre = _centres(rois, CHROMATIC)[0]
        self.assertEqual(q.roi_at(centre[0] + 5.0, centre[1] + 5.0), 1)
        self.assertIsNone(q.roi_at(centre[0] + 5.0, centre[1] - 5.0))

    def test_the_wand_finds_a_rectangles_border_and_a_masks(self) -> None:
        rect = _rect(1, 60.0, 50.0, 20.0, 10.0, 0.0, border=Border(2.0, 4.0))
        q = _queries([rect])
        found = q.region_at(60.0, 50.0 + 5.0 + 4.0, SHAPE)  # 4 px below the edge: in the band 2..6
        self.assertIsNotNone(found)
        plane, what = found
        self.assertEqual(what, "ROI 1 reference border")
        self.assertTrue(np.array_equal(plane, rz.rasterize_reference(rect, SHAPE, IDENTITY, default_border=DEFAULT_BORDER)))
        self.assertIsNone(q.region_at(60.0, 50.0 + 5.0 + 1.0, SHAPE))  # in the gap
        self.assertIsNone(q.region_at(60.0, 50.0 + 5.0 + 9.0, SHAPE))  # beyond the band
        inside = q.region_at(60.0, 50.0, SHAPE)  # the sample is found first
        self.assertEqual(inside[1], "ROI 1 disk")
        mask = _mask(2, 100, 40, Border(1.0, 3.0))
        found = _queries([mask]).region_at(104.5, 40.5 - 3.0, SHAPE, sample=False)  # 3 px above the top edge of the mask's first row
        self.assertIsNotNone(found)

    def test_a_reference_roi_has_no_border_to_point_at(self) -> None:
        ref = _rect(1, 60.0, 50.0, 20.0, 10.0, 0.0, border=Border(2.0, 4.0), role="reference")
        self.assertIsNone(_queries([ref]).region_at(60.0, 50.0 + 5.0 + 4.0, SHAPE))

    def test_an_area_selection_touches_a_rectangle_only_where_its_pixels_are(self) -> None:
        rois = [_rect(1, 60.0, 50.0, 30.0, 4.0, 45.0), _circle(3, 20.0, 20.0, 12.0)]
        q = _queries(rois)
        off_axis = np.zeros(SHAPE, dtype=bool)
        off_axis[40:43, 67:70] = True  # inside the reach circle, about 10 px off the long axis
        self.assertEqual(q.rois_touching(off_axis), set())
        on_axis = np.zeros(SHAPE, dtype=bool)
        on_axis[57:60, 66:69] = True  # on the axis
        self.assertEqual(q.rois_touching(on_axis), {1})
        on_circle = np.zeros(SHAPE, dtype=bool)
        on_circle[20, 20] = True
        self.assertEqual(q.rois_touching(on_circle), {3})

    def test_an_area_selection_touches_a_mask_only_on_a_pixel_of_it(self) -> None:
        q = _queries([_mask(2, 100, 40)])
        hole = np.zeros(SHAPE, dtype=bool)
        hole[44, 104] = True
        self.assertEqual(q.rois_touching(hole), set())
        edge = np.zeros(SHAPE, dtype=bool)
        edge[40, 100] = True
        self.assertEqual(q.rois_touching(edge), {2})

    def test_a_selected_rectangle_can_be_grabbed_by_its_inside_and_has_no_edge_handles(self) -> None:
        rois = [_rect(1, 60.0, 50.0, 30.0, 4.0, 45.0), _circle(3, 20.0, 20.0, 12.0)]
        q = _queries(rois, selected=(1, 3))
        apertures = q.selected_apertures()
        self.assertEqual(list(apertures.resizable), [False, True])
        along = (60.0 + 10.0 * np.cos(np.pi / 4), 50.0 + 10.0 * np.sin(np.pi / 4))
        across = (60.0 + 10.0 * np.cos(np.pi / 4), 50.0 - 10.0 * np.sin(np.pi / 4))
        hit = hit_test(*along, apertures, np.eye(2), 3.0)
        self.assertEqual((hit.roi_id, hit.zone), (1, "body"))
        self.assertIsNone(hit_test(*across, apertures, np.eye(2), 3.0))
        circle_hit = hit_test(20.0, 20.0, apertures, np.eye(2), 3.0)
        self.assertEqual((circle_hit.roi_id, circle_hit.zone), (3, "body"))

    def test_circles_only_selected_apertures_carry_no_body_test(self) -> None:
        self.assertIsNone(_queries([_circle(3)], selected=(3,)).selected_apertures().body)


if __name__ == "__main__":
    unittest.main()
