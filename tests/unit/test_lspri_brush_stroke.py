"""The selection brush's pixels (`image_tools/brush_stroke.stroke_region`) and the selection's Fill holes (`fill_mask_holes`), 2026-11-15.

Pure numpy: a pixel is painted when its centre (col + 0.5, row + 0.5) lies within half the diameter of the stroke's polyline. The tests
pin the definition (a stamp is a disk, a stroke the exact union of stamps along it, so the speed of the mouse never matters) and the
edge cases (outside the image, zero size, no points).
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
    from lspr_imaging_app.image_tools.brush_stroke import stroke_region
    from lspr_imaging_app.image_tools.mask import raster_tools
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

SHAPE = (60, 80)


def _reference(points, diameter, shape=SHAPE):
    """The definition, slowly: distance of every pixel centre to the polyline, by brute force over a dense sampling."""
    radius = diameter / 2.0
    rows, cols = np.mgrid[0 : shape[0], 0 : shape[1]]
    centre_x, centre_y = cols + 0.5, rows + 0.5
    out = np.zeros(shape, dtype=bool)
    pts = [np.asarray(p, dtype=float) for p in points]
    samples = []
    for a, b in zip(pts[:-1], pts[1:]) if len(pts) > 1 else [(pts[0], pts[0])]:
        steps = max(2, int(np.ceil(np.hypot(*(b - a)) * 20)))
        samples.extend(a + (b - a) * t for t in np.linspace(0.0, 1.0, steps))
    for s in samples:
        out |= (centre_x - s[0]) ** 2 + (centre_y - s[1]) ** 2 <= radius * radius
    return out


class StrokeRegionTest(unittest.TestCase):
    def test_a_single_point_is_one_round_stamp(self) -> None:
        painted = stroke_region([(30.5, 20.5)], 7.0, SHAPE)  # centred on a pixel centre
        rows, cols = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
        expected = (rows - 20) ** 2 + (cols - 30) ** 2 <= 3.5**2
        np.testing.assert_array_equal(painted, expected)
        self.assertTrue(painted[20, 30])
        self.assertEqual(int(painted.sum()), int(expected.sum()))

    def test_a_stroke_is_the_exact_union_of_round_stamps_along_it(self) -> None:
        points = [(10.2, 12.7), (40.9, 30.1), (62.3, 18.8)]
        np.testing.assert_array_equal(stroke_region(points, 6.0, SHAPE), _reference(points, 6.0))

    def test_extra_points_on_the_same_line_change_nothing(self) -> None:
        coarse = stroke_region([(10.5, 10.5), (50.5, 30.5)], 5.0, SHAPE)
        fine = stroke_region([(10.5 + 4.0 * t, 10.5 + 2.0 * t) for t in range(11)], 5.0, SHAPE)
        np.testing.assert_array_equal(coarse, fine)

    def test_a_stroke_that_doubles_back_is_the_union_not_a_new_shape(self) -> None:
        there = stroke_region([(10.5, 20.5), (40.5, 20.5)], 5.0, SHAPE)
        back = stroke_region([(40.5, 20.5), (10.5, 20.5)], 5.0, SHAPE)
        both = stroke_region([(10.5, 20.5), (40.5, 20.5), (10.5, 20.5)], 5.0, SHAPE)
        np.testing.assert_array_equal(there, back)
        np.testing.assert_array_equal(both, there)

    def test_a_stamp_reaching_in_from_outside_the_image_paints_the_part_inside(self) -> None:
        painted = stroke_region([(-1.5, 10.5)], 8.0, SHAPE)
        self.assertTrue(painted[10, 0])
        self.assertFalse(painted[10, 4])
        self.assertTrue(painted.any() and int(painted.sum()) < int(stroke_region([(5.5, 10.5)], 8.0, SHAPE).sum()))

    def test_a_stroke_wholly_outside_paints_nothing(self) -> None:
        self.assertFalse(stroke_region([(-30.0, -30.0), (-20.0, -25.0)], 6.0, SHAPE).any())
        self.assertFalse(stroke_region([(500.0, 500.0)], 6.0, SHAPE).any())

    def test_no_points_or_no_size_paint_nothing(self) -> None:
        self.assertFalse(stroke_region([], 6.0, SHAPE).any())
        self.assertFalse(stroke_region([(10.5, 10.5)], 0.0, SHAPE).any())
        self.assertFalse(stroke_region([(10.5, 10.5)], -3.0, SHAPE).any())

    def test_the_result_has_the_requested_shape_and_dtype(self) -> None:
        painted = stroke_region([(5.5, 5.5), (9.5, 9.5)], 3.0, (12, 14))
        self.assertEqual(painted.shape, (12, 14))
        self.assertEqual(painted.dtype, bool)


class FillMaskHolesTest(unittest.TestCase):
    def test_a_ring_becomes_a_disk_and_an_open_ring_does_not(self) -> None:
        rows, cols = np.mgrid[0:40, 0:40]
        distance = np.hypot(rows - 20, cols - 20)
        ring = (distance <= 12) & (distance >= 8)
        np.testing.assert_array_equal(raster_tools.fill_mask_holes(ring), distance <= 12)
        gap = ring.copy()
        gap[20, 28:34] = False  # a slit through the ring: the inside is connected to the outside
        np.testing.assert_array_equal(raster_tools.fill_mask_holes(gap), gap)

    def test_it_does_not_change_its_input_and_leaves_a_solid_mask_alone(self) -> None:
        solid = np.zeros((10, 10), dtype=bool)
        solid[2:8, 2:8] = True
        before = solid.copy()
        np.testing.assert_array_equal(raster_tools.fill_mask_holes(solid), solid)
        np.testing.assert_array_equal(solid, before)


if __name__ == "__main__":
    unittest.main()
