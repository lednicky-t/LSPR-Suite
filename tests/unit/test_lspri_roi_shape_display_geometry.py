"""The geometry behind drawing and pointing at rectangle and mask ROIs (phase 2.4 of apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md).

`overlay_geometry.rectangle_points` (the outline of a rotated rectangle, and of the band edges of its border) and `shape_hits` (is this point of the image in
that ROI's region). Both are compared with things written independently here (a signed distance, the shoelace area) and with the pixels the rasterizer finds,
so that what is drawn and what is clicked is what is measured.

No Qt, no files.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.domain.roi_model import AreaRoi
    from lspr_imaging_app.domain.roi_shapes import NO_BORDER, Border, Circle, MaskShape, Rectangle, RoiMask
    from lspr_imaging_app.roi import rasterize as rz
    from lspr_imaging_app.roi.overlay_geometry import CELL_OFFSET, circle_points, rectangle_points
    from lspr_imaging_app.roi.shape_hits import FrameMap, display_point_in_region, display_point_to_source
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
CHROMATIC = np.array([[1.004, 0.0021, -2.4], [-0.0017, 0.9985, 1.3]])
ROTATED = np.array([[0.99819, -0.08733, 3.7], [0.08733, 0.99819, -2.9]])
STRETCHED = np.array([[1.08, -0.04, 5.0], [0.03, 0.93, -4.0]])
HEIGHT, WIDTH = 100, 140


def _shoelace(xs: np.ndarray, ys: np.ndarray) -> float:
    return 0.5 * abs(float(np.dot(xs, np.roll(ys, -1)) - np.dot(ys, np.roll(xs, -1))))


def _rect_distance(x, y, cx, cy, w, h, angle_deg):
    """Signed distance from a rotated rectangle's edge (outside positive), written independently of the code under test."""
    a = np.radians(angle_deg)
    u = (x - cx) * np.cos(a) + (y - cy) * np.sin(a)
    v = (y - cy) * np.cos(a) - (x - cx) * np.sin(a)
    qx, qy = np.abs(u) - w / 2.0, np.abs(v) - h / 2.0
    return np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0)) + np.minimum(np.maximum(qx, qy), 0.0)


class RectanglePointsTests(unittest.TestCase):
    def _row(self, w, h, angle, offset=0.0, centre=(50.0, 40.0), affine=IDENTITY):
        xs, ys = rectangle_points(np.array([centre]), np.array([w]), np.array([h]), np.array([angle]), affine, offset_px=offset)
        return xs[0], ys[0]

    def test_rows_are_closed_and_have_the_circles_length(self) -> None:
        xs, ys = rectangle_points(np.array([[1.0, 2.0], [30.0, 40.0]]), np.array([10.0, 4.0]), np.array([6.0, 9.0]), np.array([0.0, 33.0]), IDENTITY)
        self.assertEqual(xs.shape, (2, 48))
        self.assertEqual(xs.shape[1], circle_points(np.array([[1.0, 2.0]]), np.array([5.0]), IDENTITY)[0].shape[1])  # the same row length as a circle's
        self.assertTrue(np.array_equal(xs[:, 0], xs[:, -1]) and np.array_equal(ys[:, 0], ys[:, -1]))
        self.assertEqual(rectangle_points(np.empty((0, 2)), np.empty(0), np.empty(0), np.empty(0), IDENTITY)[0].shape, (0, 48))

    def test_a_sharp_rectangle_has_the_rectangles_area_and_corners(self) -> None:
        for w, h, angle in ((20.0, 8.0, 0.0), (20.0, 8.0, 90.0), (13.0, 7.5, 33.0), (6.0, 30.0, 200.0)):
            xs, ys = self._row(w, h, angle)
            self.assertAlmostEqual(_shoelace(xs, ys), w * h, places=9)
            distance = _rect_distance(xs, ys, 50.0, 40.0, w, h, angle)
            np.testing.assert_allclose(distance, 0.0, atol=1e-9)  # every vertex is on the edge

    def test_a_positive_angle_turns_clockwise_on_the_screen(self) -> None:
        xs, ys = self._row(20.0, 4.0, 90.0)  # the long side now runs down the screen (y points down)
        self.assertAlmostEqual(float(np.ptp(xs)), 4.0, places=9)
        self.assertAlmostEqual(float(np.ptp(ys)), 20.0, places=9)
        xs, ys = self._row(20.0, 4.0, 30.0)
        right = np.argmax(xs)  # the extreme right vertex of a rectangle turned clockwise by 30 degrees lies below the centre line
        self.assertGreater(ys[right], 40.0)

    def test_an_offset_outline_is_at_that_distance_from_the_edge(self) -> None:
        for offset in (1.0, 2.5, 6.0):
            xs, ys = self._row(16.0, 9.0, 27.0, offset=offset)
            np.testing.assert_allclose(_rect_distance(xs, ys, 50.0, 40.0, 16.0, 9.0, 27.0), offset, atol=1e-9)
            area = 16.0 * 9.0 + 2.0 * offset * (16.0 + 9.0) + np.pi * offset**2  # a rounded rectangle; the arcs are polylines, so a little less
            self.assertLess(abs(_shoelace(xs, ys) - area) / area, 0.01)
            self.assertLessEqual(_shoelace(xs, ys), area)

    def test_a_negative_offset_is_a_smaller_sharp_rectangle_and_clamps_at_zero(self) -> None:
        xs, ys = self._row(16.0, 9.0, 0.0, offset=-2.0)
        self.assertAlmostEqual(float(np.ptp(xs)), 12.0, places=9)
        self.assertAlmostEqual(float(np.ptp(ys)), 5.0, places=9)
        xs, ys = self._row(16.0, 9.0, 0.0, offset=-20.0)
        self.assertAlmostEqual(float(np.ptp(xs)), 0.0, places=9)  # nothing left of it

    def test_the_affine_bends_the_outline_about_the_centre_like_a_circles(self) -> None:
        for affine in (CHROMATIC, ROTATED, STRETCHED):
            plain = np.array(self._row(14.0, 6.0, 20.0, offset=3.0))
            bent = np.array(self._row(14.0, 6.0, 20.0, offset=3.0, affine=affine))
            expected = np.array([50.0, 40.0])[:, None] + affine[:, :2] @ (plain - np.array([50.0, 40.0])[:, None])
            np.testing.assert_allclose(bent, expected, atol=1e-9)


class DisplayPointTests(unittest.TestCase):
    """`display_point_in_region` gives, at each pixel of the frame on screen, what the rasterizer gives for that pixel."""

    def _compare(self, roi, region, default_border, affine, *, mask_cells=False, shape=(HEIGHT, WIDTH)):
        border = roi.border if roi.border is not None else default_border  # what the scan and the click both resolve
        frame = FrameMap.of(affine)
        centre = affine[:, :2] @ np.array([roi.center_x, roi.center_y]) + affine[:, 2]  # the display centre of the ROI
        plane = rz.rasterize_sample(roi, shape, affine) if region == "sample" else rz.rasterize_reference(roi, shape, affine, default_border=default_border)
        shift = CELL_OFFSET if mask_cells else 0.0  # a mask is pointed at by the middle of a pixel's cell
        wrong = 0
        for row in range(0, shape[0]):
            for col in range(0, shape[1]):
                if bool(display_point_in_region(roi, region, border, centre, frame, col + shift, row + shift)) != bool(plane[row, col]):
                    wrong += 1
        return wrong, int(plane.sum())

    def test_a_circle_and_a_rectangle_are_hit_where_their_pixels_are(self) -> None:
        circle = AreaRoi(1, 60.0, 45.0, Circle(21.0), border=Border(2.0, 5.0))
        rect = AreaRoi(2, 70.0, 50.0, Rectangle(24.0, 11.0, 33.0), border=Border(-1.5, 6.0))
        for affine in (IDENTITY, CHROMATIC, ROTATED, STRETCHED):
            for roi in (circle, rect):
                for region in ("sample", "border"):
                    wrong, size = self._compare(roi, region, Border(2.0, 5.0), affine)
                    self.assertGreater(size, 20)
                    self.assertEqual(wrong, 0, f"{type(roi.shape).__name__} {region}")

    def test_a_mask_is_hit_by_the_cells_of_its_pixels_including_its_border(self) -> None:
        rng = np.random.default_rng(8)
        pixels = rng.random((9, 12)) < 0.7
        pixels[4, 6] = True
        roi = AreaRoi(1, 66.0, 50.0, MaskShape(RoiMask(60, 46, pixels)), border=Border(1.0, 3.0))
        for region in ("sample", "border"):
            wrong, size = self._compare(roi, region, Border(1.0, 3.0), IDENTITY, mask_cells=True)
            self.assertGreater(size, 20)
            self.assertEqual(wrong, 0, region)

    def test_a_mask_under_a_chromatic_affine_differs_from_the_warp_only_at_rounding_ties(self) -> None:
        pixels = np.ones((10, 10), dtype=bool)
        pixels[3:6, 3:6] = False  # a hole
        roi = AreaRoi(1, 65.0, 50.0, MaskShape(RoiMask(60, 45, pixels)), border=NO_BORDER)
        for affine in (CHROMATIC, ROTATED):
            wrong, size = self._compare(roi, "sample", NO_BORDER, affine, mask_cells=True)
            self.assertGreater(size, 60)
            self.assertLessEqual(wrong, 2, "the click lookup and the nearest-neighbour warp round a point exactly half way between two pixels differently")

    def test_a_point_in_a_masks_hole_or_outside_is_not_a_hit(self) -> None:
        pixels = np.ones((9, 9), dtype=bool)
        pixels[3:6, 3:6] = False
        roi = AreaRoi(1, 54.0, 54.0, MaskShape(RoiMask(50, 50, pixels)), border=NO_BORDER)
        frame = FrameMap.of(IDENTITY)
        centre = (54.0, 54.0)
        self.assertTrue(display_point_in_region(roi, "sample", NO_BORDER, centre, frame, 50.5, 50.5))  # first pixel's cell
        self.assertFalse(display_point_in_region(roi, "sample", NO_BORDER, centre, frame, 54.5, 54.5))  # the hole
        self.assertFalse(display_point_in_region(roi, "sample", NO_BORDER, centre, frame, 70.5, 54.5))
        self.assertFalse(display_point_in_region(roi, "border", NO_BORDER, centre, frame, 50.5, 50.5))  # no border: nothing of it

    def test_a_masks_hits_do_not_depend_on_the_rois_centre(self) -> None:
        """The centre of a mask ROI is only its label point; the scan measures the pixels. Moving the centre (a table edit, say) must not move what is hit."""
        pixels = np.ones((8, 8), dtype=bool)
        near = AreaRoi(1, 54.0, 54.0, MaskShape(RoiMask(50, 50, pixels)), border=Border(1.0, 2.0))
        far = AreaRoi(1, 90.0, 20.0, MaskShape(RoiMask(50, 50, pixels)), border=Border(1.0, 2.0))
        for affine in (IDENTITY, CHROMATIC):
            frame = FrameMap.of(affine)
            for region in ("sample", "border"):
                for y in range(44, 66):
                    for x in range(44, 66):
                        self.assertEqual(
                            display_point_in_region(near, region, Border(1.0, 2.0), (54.0, 54.0), frame, x + 0.5, y + 0.5),
                            display_point_in_region(far, region, Border(1.0, 2.0), (90.0, 20.0), frame, x + 0.5, y + 0.5),
                        )

    def test_the_source_point_of_a_display_point(self) -> None:
        roi = AreaRoi(1, 60.0, 45.0, Rectangle(10.0, 4.0))
        frame = FrameMap.of(CHROMATIC)
        inverse = frame.inverse
        centre = CHROMATIC[:, :2] @ np.array([60.0, 45.0]) + CHROMATIC[:, 2]
        sx, sy = display_point_to_source(roi, centre, frame, centre[0], centre[1])
        self.assertAlmostEqual(sx, 60.0)
        self.assertAlmostEqual(sy, 45.0)  # the display centre is the ROI's centre
        # a point one display pixel to the right is where the inverse linear map says
        sx, sy = display_point_to_source(roi, centre, frame, centre[0] + 1.0, centre[1])
        np.testing.assert_allclose([sx - 60.0, sy - 45.0], inverse @ np.array([1.0, 0.0]), atol=1e-12)


if __name__ == "__main__":
    unittest.main()
