"""`roi/array_detection.py`: find a regular spot array (lattice) in synthetic images of known geometry.

Pure numpy; no Qt, no files. Spots are dark (as in the instrument), blurred, on an uneven background.
"""

from __future__ import annotations

import math
import unittest

import numpy as np
from scipy import ndimage

from lspr_imaging_app.progress import Cancelled
from lspr_imaging_app.roi.array_detection import ArrayPrior, find_array, lattice_nodes


def _array_image(
    rows=4,
    cols=6,
    pitch_x=62.0,
    pitch_y=62.0,
    tilt_deg=0.0,
    diameter=24.0,
    origin=(60.0, 55.0),
    shape=(300, 440),
    missing=(),
    noise=0.01,
    gradient=0.25,
    blur=1.2,
    seed=1,
    bright=False,
):
    """Image plus the true (rows*cols, 2) centres. Dark spots on a bright, uneven background."""
    rng = np.random.default_rng(seed)
    a1 = pitch_x * np.array([math.cos(math.radians(tilt_deg)), math.sin(math.radians(tilt_deg))])
    a2 = pitch_y * np.array([-math.sin(math.radians(tilt_deg)), math.cos(math.radians(tilt_deg))])
    nodes = lattice_nodes(rows, cols, origin, tuple(a1), tuple(a2))
    up = 3
    yy, xx = np.mgrid[0 : shape[0] * up, 0 : shape[1] * up]
    x_hi, y_hi = (xx + 0.5) / up - 0.5, (yy + 0.5) / up - 0.5
    mask = np.zeros(x_hi.shape, dtype=bool)
    for n, (cx, cy) in enumerate(nodes):
        if n in missing:
            continue
        mask |= np.hypot(x_hi - cx, y_hi - cy) <= diameter / 2.0
    mask = mask.reshape(shape[0], up, shape[1], up).mean(axis=(1, 3))
    mask = ndimage.gaussian_filter(mask, blur)
    column = np.linspace(0.0, 1.0, shape[1])[None, :]
    background = 1000.0 * (1.0 + gradient * (column - 0.5))
    image = background * (1.0 - 0.6 * mask) + rng.normal(0.0, noise * 1000.0, shape)
    if bright:
        image = 2000.0 - image
    return image.astype(np.float32), nodes


def _match(fit, truth, tolerance):
    """Largest distance from each true node to the nearest found centre (rows/cols may differ)."""
    distances = np.hypot(*(truth[:, None, :] - fit.centers_xy[None, :, :]).transpose(2, 0, 1))
    return distances.min(axis=1).max() if tolerance is None else float((distances.min(axis=1) <= tolerance).mean())


class FindArrayTest(unittest.TestCase):
    def test_clean_array_rows_cols_pitch_and_centres(self) -> None:
        image, truth = _array_image()
        fit = find_array(image)
        self.assertIsNotNone(fit)
        self.assertEqual((fit.rows, fit.cols), (4, 6))
        self.assertAlmostEqual(fit.pitch_x_px, 62.0, delta=0.3)
        self.assertAlmostEqual(fit.pitch_y_px, 62.0, delta=0.3)
        self.assertAlmostEqual(fit.rough_diameter_px, 24.0, delta=4.0)
        self.assertEqual(fit.found_count, 24)
        self.assertLess(_match(fit, truth, None), 0.6)  # every true centre has a found centre within 0.6 px
        self.assertLess(fit.residual_rms_px, 0.5)

    def test_non_integer_pitch_does_not_drift(self) -> None:
        image, truth = _array_image(cols=7, pitch_x=48.4, pitch_y=47.8, shape=(260, 420), origin=(40.0, 40.0), diameter=20.0)
        fit = find_array(image)
        self.assertEqual((fit.rows, fit.cols), (4, 7))
        self.assertAlmostEqual(fit.pitch_x_px, 48.4, delta=0.15)
        self.assertAlmostEqual(fit.pitch_y_px, 47.8, delta=0.15)
        self.assertLess(_match(fit, truth, None), 0.7)

    def test_different_pitch_along_x_and_y(self) -> None:
        image, _truth = _array_image(rows=3, cols=5, pitch_x=56.0, pitch_y=84.0, shape=(300, 340), origin=(50.0, 50.0))
        fit = find_array(image)
        self.assertEqual((fit.rows, fit.cols), (3, 5))
        self.assertAlmostEqual(fit.pitch_x_px, 56.0, delta=0.4)
        self.assertAlmostEqual(fit.pitch_y_px, 84.0, delta=0.4)

    def test_tilt_is_measured_and_followed(self) -> None:
        image, truth = _array_image(tilt_deg=3.0, origin=(60.0, 45.0), shape=(320, 460))
        fit = find_array(image)
        self.assertEqual((fit.rows, fit.cols), (4, 6))
        self.assertAlmostEqual(fit.tilt_deg, 3.0, delta=0.3)
        self.assertAlmostEqual(fit.skew_deg, 0.0, delta=0.5)
        self.assertLess(_match(fit, truth, None), 0.8)

    def test_a_few_missing_spots_are_reported_not_found(self) -> None:
        image, truth = _array_image(missing=(8, 9, 21))
        fit = find_array(image)
        self.assertEqual((fit.rows, fit.cols), (4, 6))
        self.assertEqual(fit.found_count, 21)
        self.assertFalse(fit.found[[8, 9, 21]].any())
        # the missing nodes still sit at their lattice positions
        self.assertLess(np.hypot(*(fit.centers_xy[8] - truth[8])), 0.8)

    def test_bright_spots_with_the_bright_flag(self) -> None:
        image, _truth = _array_image(bright=True)
        fit = find_array(image, bright=True)
        self.assertIsNotNone(fit)
        self.assertEqual((fit.rows, fit.cols), (4, 6))

    def test_a_dark_band_along_the_edge_is_not_taken_for_a_row_of_spots(self) -> None:
        image, _truth = _array_image(rows=4, cols=6, shape=(330, 440))
        image[255:, :] *= 0.05  # a dark holder border under the array, on the next lattice row
        fit = find_array(image)
        self.assertIsNotNone(fit)
        self.assertEqual((fit.rows, fit.cols), (4, 6))

    def test_uneven_background_gradient(self) -> None:
        image, _truth = _array_image(gradient=0.8)
        fit = find_array(image)
        self.assertIsNotNone(fit)
        self.assertEqual((fit.rows, fit.cols), (4, 6))

    def test_nan_corner_from_rotation_is_ignored(self) -> None:
        image, _truth = _array_image()
        image[:40, :40] = np.nan
        fit = find_array(image)
        self.assertIsNotNone(fit)
        self.assertEqual((fit.rows, fit.cols), (4, 6))

    def test_a_valid_mask_excludes_part_of_the_image(self) -> None:
        image, _truth = _array_image(rows=4, cols=6)
        valid = np.ones(image.shape, dtype=bool)
        valid[:, 330:] = False  # drop the last column (x = 370)
        fit = find_array(image, valid_mask=valid)
        self.assertIsNotNone(fit)
        self.assertEqual((fit.rows, fit.cols), (4, 5))

    def test_priors_are_checked_against_the_result(self) -> None:
        image, _truth = _array_image()
        fit = find_array(image, prior=ArrayPrior(diameter_px=24.0, rows=5, cols=6, pitch_x_px=70.0))
        self.assertIsNotNone(fit)
        self.assertTrue(any("expected" in w for w in fit.warnings))
        self.assertAlmostEqual(fit.rough_diameter_px, 24.0)

    def test_random_scatter_is_not_an_array(self) -> None:
        rng = np.random.default_rng(3)
        image = np.full((300, 440), 1000.0)
        yy, xx = np.mgrid[0:300, 0:440]
        for _ in range(20):
            cx, cy = rng.uniform(20, 420), rng.uniform(20, 280)
            image[np.hypot(xx - cx, yy - cy) <= 12] = 400.0
        image = ndimage.gaussian_filter(image, 1.0).astype(np.float32)
        diagnostics: dict = {}
        self.assertIsNone(find_array(image, diagnostics=diagnostics))
        self.assertNotEqual(diagnostics.get("reason"), "ok")

    def test_single_spot_flat_and_empty_images_are_rejected_with_a_reason(self) -> None:
        single, _t = _array_image(rows=1, cols=1)
        for image in (single, np.full((100, 100), 500.0, dtype=np.float32), np.empty((0, 0), dtype=np.float32)):
            diagnostics: dict = {}
            self.assertIsNone(find_array(image, diagnostics=diagnostics))
            self.assertTrue(diagnostics["reason"])

    def test_progress_runs_to_one_and_cancel_stops_it(self) -> None:
        image, _truth = _array_image()
        seen: list[float] = []
        find_array(image, progress=lambda fraction, _text: seen.append(fraction))
        self.assertTrue(seen and max(seen) == 1.0 and seen == sorted(seen))
        with self.assertRaises(Cancelled):
            find_array(image, cancelled=lambda: True)


class LatticeNodesTest(unittest.TestCase):
    def test_row_major_order(self) -> None:
        nodes = lattice_nodes(2, 3, (10.0, 20.0), (5.0, 0.0), (0.0, 7.0))
        self.assertEqual(nodes.shape, (6, 2))
        np.testing.assert_allclose(nodes[0], (10.0, 20.0))
        np.testing.assert_allclose(nodes[2], (20.0, 20.0))  # end of row 0
        np.testing.assert_allclose(nodes[3], (10.0, 27.0))  # start of row 1

    def test_needs_at_least_one_row_and_column(self) -> None:
        with self.assertRaises(ValueError):
            lattice_nodes(0, 3, (0.0, 0.0), (1.0, 0.0), (0.0, 1.0))


if __name__ == "__main__":
    unittest.main()
