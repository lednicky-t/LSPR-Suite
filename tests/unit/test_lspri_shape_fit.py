"""Pure-logic tests for `lspr_imaging_app.roi.shape_fit`: a circle / the smallest rectangle fitted to one blob of pixels (2026-11-24).

The check that matters is the round trip: rasterise a known shape, fit the blob, rasterise the fit with the app's own rasteriser, and compare the pixels.
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
    from lspr_imaging_app.domain.roi_shapes import Circle, Rectangle
    from lspr_imaging_app.roi import rasterize as rz
    from lspr_imaging_app.roi.shape_fit import fit_circle, fit_rectangle, has_single_blob
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

SHAPE = (120, 160)
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])


def _pixels(x: float, y: float, shape) -> np.ndarray:
    roi = AreaRoi(area_roi_id=1, center_x=x, center_y=y, shape=shape, border=None, role="sample")
    return rz.rasterize_sample(roi, SHAPE, IDENTITY)


def _overlap(a: np.ndarray, b: np.ndarray) -> float:
    """Intersection over union of two bool planes."""
    return float(np.count_nonzero(a & b)) / float(np.count_nonzero(a | b))


class FitCircleTests(unittest.TestCase):
    def test_a_round_blob_gives_its_own_circle(self) -> None:
        blob = _pixels(70.3, 50.6, Circle(30.0))
        fit = fit_circle(blob)
        self.assertAlmostEqual(fit.center_x, 70.3, delta=0.1)
        self.assertAlmostEqual(fit.center_y, 50.6, delta=0.1)
        self.assertAlmostEqual(fit.shape.diameter_px, 30.0, delta=0.5)
        self.assertGreater(_overlap(blob, _pixels(fit.center_x, fit.center_y, fit.shape)), 0.95)

    def test_the_fitted_circle_has_the_blobs_area(self) -> None:
        blob = np.zeros(SHAPE, dtype=bool)
        blob[40:60, 30:90] = True  # a 20 x 60 bar, not round
        fit = fit_circle(blob)
        self.assertAlmostEqual(np.pi * (fit.shape.diameter_px / 2) ** 2, float(blob.sum()), delta=1e-6 * blob.sum())
        self.assertEqual(fit.blob_pixels, int(blob.sum()))

    def test_a_single_pixel_gives_a_circle_the_toolbox_accepts(self) -> None:
        blob = np.zeros(SHAPE, dtype=bool)
        blob[10, 20] = True
        fit = fit_circle(blob)
        self.assertGreaterEqual(fit.shape.diameter_px, 2.0)
        self.assertEqual((fit.center_x, fit.center_y), (20.0, 10.0))


class FitRectangleTests(unittest.TestCase):
    def test_an_unturned_rectangle_is_matched_exactly(self) -> None:
        blob = np.zeros(SHAPE, dtype=bool)
        blob[40:60, 30:90] = True  # rows 40..59, columns 30..89: 60 wide, 20 high
        fit = fit_rectangle(blob)
        self.assertIsInstance(fit.shape, Rectangle)
        self.assertAlmostEqual(fit.shape.width_px, 60.0, places=6)
        self.assertAlmostEqual(fit.shape.height_px, 20.0, places=6)
        self.assertAlmostEqual(fit.shape.angle_deg, 0.0, places=6)
        self.assertAlmostEqual(fit.center_x, 59.5, places=6)
        self.assertAlmostEqual(fit.center_y, 49.5, places=6)
        self.assertTrue(np.array_equal(blob, _pixels(fit.center_x, fit.center_y, fit.shape)))

    def test_a_turned_rectangle_is_found_with_its_angle(self) -> None:
        for angle in (-40.0, -20.0, 12.0, 30.0, 44.0):
            with self.subTest(angle=angle):
                blob = _pixels(80.0, 60.0, Rectangle(50.0, 18.0, angle))
                fit = fit_rectangle(blob)
                self.assertAlmostEqual(fit.shape.angle_deg, angle, delta=3.0)
                self.assertAlmostEqual(fit.shape.width_px, 50.0, delta=3.0)
                self.assertAlmostEqual(fit.shape.height_px, 18.0, delta=3.0)
                self.assertAlmostEqual(fit.center_x, 80.0, delta=0.6)
                self.assertAlmostEqual(fit.center_y, 60.0, delta=0.6)
                self.assertGreater(_overlap(blob, _pixels(fit.center_x, fit.center_y, fit.shape)), 0.9)

    def test_the_angle_stays_within_forty_five_degrees(self) -> None:
        blob = _pixels(80.0, 60.0, Rectangle(50.0, 18.0, 70.0))  # the same box as one turned by -20 degrees with width and height swapped
        fit = fit_rectangle(blob)
        self.assertLessEqual(abs(fit.shape.angle_deg), 45.0)
        self.assertGreater(_overlap(blob, _pixels(fit.center_x, fit.center_y, fit.shape)), 0.9)

    def test_the_rectangle_contains_every_pixel_of_a_round_blob(self) -> None:
        blob = _pixels(70.0, 50.0, Circle(24.0))
        fit = fit_rectangle(blob)
        self.assertAlmostEqual(fit.shape.width_px, 24.0, delta=1.5)
        self.assertAlmostEqual(fit.shape.height_px, 24.0, delta=1.5)

    def test_a_one_pixel_wide_line_does_not_fail(self) -> None:
        blob = np.zeros(SHAPE, dtype=bool)
        blob[30, 20:50] = True
        fit = fit_rectangle(blob)
        self.assertAlmostEqual(fit.shape.width_px, 30.0, places=6)
        self.assertAlmostEqual(fit.shape.height_px, 2.0, places=6)  # (a pixel is 1 px across; the shape has at least 2 px)


class BlobCountTests(unittest.TestCase):
    def test_exactly_one_blob_is_needed(self) -> None:
        two = np.zeros(SHAPE, dtype=bool)
        two[10:20, 10:20] = True
        two[50:60, 50:60] = True
        self.assertFalse(has_single_blob(two))
        self.assertFalse(has_single_blob(np.zeros(SHAPE, dtype=bool)))
        one = two.copy()
        one[50:60, 50:60] = False
        self.assertTrue(has_single_blob(one))
        for fit in (fit_circle, fit_rectangle):
            with self.assertRaises(ValueError):
                fit(two)
            with self.assertRaises(ValueError):
                fit(np.zeros(SHAPE, dtype=bool))

    def test_blobs_touching_at_a_corner_are_one_blob(self) -> None:
        plane = np.zeros(SHAPE, dtype=bool)
        plane[10:20, 10:20] = True
        plane[20:30, 20:30] = True
        self.assertTrue(has_single_blob(plane))


if __name__ == "__main__":
    unittest.main()
