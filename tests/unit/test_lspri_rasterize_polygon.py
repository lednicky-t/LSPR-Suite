"""`rasterize_polygon` (2026-11-16): the scanline fill that replaced `skimage.draw.polygon2mask`, which took 0.4-3 s for a hand-drawn lasso
(it tests every pixel of the bounding box against every vertex). skimage stays here as the oracle: for any shape with no pixel center exactly
on an edge the masks must be identical. Pure numpy, no Qt widgets.
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
    from lspr_imaging_app.selection.area_selection_module import rasterize_polygon
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
from skimage.draw import polygon2mask  # noqa: E402


def _skimage(vertices: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """The old implementation: skimage wants (row, col) with whole numbers at pixel centers; view coordinates have centers at +0.5."""
    return polygon2mask(shape, np.column_stack((vertices[:, 1] - 0.5, vertices[:, 0] - 0.5)))


class RasterizePolygonTest(unittest.TestCase):
    def test_random_polygons_equal_skimage(self) -> None:
        """300 random polygons with float corners (so no pixel center lies on an edge): self-crossing ones, ones reaching far outside the
        image, tiny ones, in a wide and a tall image."""
        rng = np.random.default_rng(5)
        for shape in ((80, 100), (100, 60)):
            for k in range(150):
                count = int(rng.integers(3, 40))
                low, high = (-20.0, 130.0) if k % 2 else (5.0, 55.0)
                vertices = rng.uniform(low, high, (count, 2))
                with self.subTest(shape=shape, polygon=k):
                    np.testing.assert_array_equal(rasterize_polygon(vertices, shape), _skimage(vertices, shape))

    def test_a_hand_drawn_lasso_of_thousands_of_points_equals_skimage(self) -> None:
        rng = np.random.default_rng(1)
        shape = (400, 600)
        angle = np.linspace(0.0, 2.0 * np.pi, 2000, endpoint=False)
        radius = 1.0 + 0.15 * np.sin(7.0 * angle) + 0.02 * rng.standard_normal(angle.size)
        vertices = np.column_stack((300.0 + 150.0 * radius * np.cos(angle), 200.0 + 100.0 * radius * np.sin(angle)))
        mask = rasterize_polygon(vertices, shape)
        self.assertGreater(int(mask.sum()), 30000)
        np.testing.assert_array_equal(mask, _skimage(vertices, shape))

    def test_a_rectangle_with_whole_number_corners_is_the_pixels_between_them(self) -> None:
        mask = rasterize_polygon(np.array([[5.0, 5.0], [20.0, 5.0], [20.0, 20.0], [5.0, 20.0]]), (64, 80))
        self.assertEqual(int(mask.sum()), 15 * 15)
        self.assertTrue(mask[5, 5] and mask[19, 19])
        self.assertFalse(mask[4, 5] or mask[20, 19] or mask[5, 4] or mask[19, 20])
        np.testing.assert_array_equal(mask, _skimage(np.array([[5.0, 5.0], [20.0, 5.0], [20.0, 20.0], [5.0, 20.0]]), (64, 80)))

    def test_a_pixel_center_exactly_on_an_edge_belongs_to_its_top_and_left_side_only(self) -> None:
        """The one documented difference from skimage (which kept the pixels on both sides: 16 x 16 here): half-open, so two shapes sharing
        an edge do not overlap."""
        corners = np.array([[5.5, 5.5], [20.5, 5.5], [20.5, 20.5], [5.5, 20.5]])  # the edges run through pixel centers
        mask = rasterize_polygon(corners, (64, 80))
        self.assertEqual(int(mask.sum()), 15 * 15)
        self.assertTrue(mask[5, 5] and mask[19, 19])
        self.assertFalse(mask[20, 10] or mask[10, 20])
        left = rasterize_polygon(np.array([[5.5, 5.5], [12.5, 5.5], [12.5, 20.5], [5.5, 20.5]]), (64, 80))
        right = rasterize_polygon(np.array([[12.5, 5.5], [20.5, 5.5], [20.5, 20.5], [12.5, 20.5]]), (64, 80))
        self.assertFalse((left & right).any(), "neighbours sharing an edge do not overlap")
        np.testing.assert_array_equal(left | right, mask, "and together they are the whole")

    def test_a_shape_reaching_outside_the_image_is_cut_by_the_image(self) -> None:
        mask = rasterize_polygon(np.array([[-30.0, -30.0], [10.0, -30.0], [10.0, 12.0], [-30.0, 12.0]]), (64, 80))
        self.assertEqual(int(mask.sum()), 10 * 12)
        self.assertFalse(rasterize_polygon(np.array([[100.0, 10.0], [150.0, 10.0], [150.0, 40.0]]), (64, 80)).any())
        self.assertFalse(rasterize_polygon(np.array([[10.0, 90.0], [50.0, 90.0], [50.0, 120.0]]), (64, 80)).any())

    def test_a_bow_tie_leaves_its_crossing_loops_both_filled_and_a_loop_inside_a_loop_a_hole(self) -> None:
        bow = np.array([[10.23, 10.71], [30.41, 30.17], [30.67, 10.29], [10.11, 30.83]])  # no pixel center on an edge
        np.testing.assert_array_equal(rasterize_polygon(bow, (64, 80)), _skimage(bow, (64, 80)))
        outer = [(5.2, 5.2), (40.2, 5.2), (40.2, 40.2), (5.2, 40.2), (5.2, 5.2)]
        inner = [(15.2, 15.2), (15.2, 30.2), (30.2, 30.2), (30.2, 15.2), (15.2, 15.2)]
        ring = np.array(outer + inner)  # one outline that goes round twice: even-odd makes the inside a hole
        mask = rasterize_polygon(ring, (64, 80))
        self.assertFalse(mask[20, 20])
        self.assertTrue(mask[8, 8])
        np.testing.assert_array_equal(mask, _skimage(ring, (64, 80)))

    def test_fewer_than_three_points_or_no_area_select_nothing(self) -> None:
        self.assertFalse(rasterize_polygon(np.zeros((0, 2)), (64, 80)).any())
        self.assertFalse(rasterize_polygon(np.array([[1.0, 1.0], [30.0, 30.0]]), (64, 80)).any())
        self.assertFalse(rasterize_polygon(np.array([[10.0, 10.3], [30.0, 10.3], [50.0, 10.3]]), (64, 80)).any())  # a flat line
        self.assertEqual(rasterize_polygon(np.array([[1.0, 1.0], [30.0, 30.0]]), (64, 80)).shape, (64, 80))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
