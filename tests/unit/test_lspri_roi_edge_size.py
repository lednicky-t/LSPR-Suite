"""`roi/edge_size.py`: spot diameter from a blurred edge, on synthetic spots of known size.

Pure numpy; no Qt, no files. Spots are rendered 4x supersampled, blurred with a Gaussian
(optics) and noised, so the true diameter is known exactly.
"""

from __future__ import annotations

import math
import unittest

import numpy as np
from scipy import ndimage

from lspr_imaging_app.roi.edge_size import EDGE_MODELS, EdgeSizeParams, measure_spot_sizes, summarize


def _spots(centers, diameter, blur_sigma, noise=0.0, shape=(120, 160), plateau=0.3, seed=0):
    """Contrast image: dark spots positive (`plateau`), background 0."""
    rng = np.random.default_rng(seed)
    up = 4
    yy, xx = np.mgrid[0 : shape[0] * up, 0 : shape[1] * up]
    image = np.zeros((shape[0] * up, shape[1] * up), dtype=np.float64)
    for cx, cy in centers:
        # pixel-centre convention: the centre of pixel i is at i, so supersampled coordinate = (i + 0.5) * up - 0.5
        image[np.hypot((xx + 0.5) / up - 0.5 - cx, (yy + 0.5) / up - 0.5 - cy) <= diameter / 2.0] = plateau
    image = image.reshape(shape[0], up, shape[1], up).mean(axis=(1, 3))
    if blur_sigma > 0:
        image = ndimage.gaussian_filter(image, blur_sigma)
    if noise > 0:
        image = image + rng.normal(0.0, noise, image.shape)
    return image.astype(np.float32)


CENTERS = [(40.0, 40.0), (80.3, 41.2), (120.0, 40.0), (40.6, 80.0), (80.0, 80.0)]


class EdgeSizeTest(unittest.TestCase):
    def test_half_max_recovers_the_true_diameter_of_a_blurred_spot(self) -> None:
        image = _spots(CENTERS, diameter=24.0, blur_sigma=1.5)
        sizes = measure_spot_sizes(image, np.array(CENTERS), 24.0, EdgeSizeParams(model="half_max"))
        self.assertTrue(all(s.ok for s in sizes))
        for size in sizes:
            self.assertAlmostEqual(size.diameter_px, 24.0, delta=0.6)

    def test_max_gradient_recovers_the_true_diameter(self) -> None:
        image = _spots(CENTERS, diameter=24.0, blur_sigma=1.5)
        sizes = measure_spot_sizes(image, np.array(CENTERS), 24.0, EdgeSizeParams(model="max_gradient"))
        for size in sizes:
            self.assertAlmostEqual(size.diameter_px, 24.0, delta=0.8)

    def test_plateau_models_exclude_the_feathered_edge(self) -> None:
        """Pixels 15 % off the plateau lie inside the true edge, so the size is smaller than half-max, and
        grows with the blur."""
        sharp = _spots(CENTERS, diameter=24.0, blur_sigma=0.6)
        blurry = _spots(CENTERS, diameter=24.0, blur_sigma=2.0)
        params = EdgeSizeParams(model="plateau_fraction", fraction=0.15)
        d_sharp = np.median([s.diameter_px for s in measure_spot_sizes(sharp, np.array(CENTERS), 24.0, params)])
        d_blurry = np.median([s.diameter_px for s in measure_spot_sizes(blurry, np.array(CENTERS), 24.0, params)])
        self.assertLess(d_blurry, d_sharp)
        self.assertLess(d_blurry, 24.0 - 1.0)
        self.assertGreater(d_sharp, 24.0 - 2.5)

    def test_a_looser_fraction_gives_a_larger_diameter(self) -> None:
        image = _spots(CENTERS, diameter=24.0, blur_sigma=1.5)
        sizes = {}
        for fraction in (0.05, 0.15, 0.30):
            params = EdgeSizeParams(model="plateau_fraction", fraction=fraction)
            sizes[fraction] = np.median([s.diameter_px for s in measure_spot_sizes(image, np.array(CENTERS), 24.0, params)])
        self.assertLess(sizes[0.05], sizes[0.15])
        self.assertLess(sizes[0.15], sizes[0.30])

    def test_every_model_gives_similar_sizes_for_every_spot_in_noise(self) -> None:
        image = _spots(CENTERS, diameter=24.0, blur_sigma=1.5, noise=0.01)
        for model in EDGE_MODELS:
            sizes = measure_spot_sizes(image, np.array(CENTERS), 24.0, EdgeSizeParams(model=model))
            stats = summarize(sizes)
            self.assertEqual(stats["count"], len(CENTERS), model)
            self.assertLess(stats["mad"], 1.0, model)
            # the plateau models drop the feathered edge on purpose, so they sit below the true edge
            lower = 24.0 - (1.0 if model in ("half_max", "max_gradient") else 6.0)
            self.assertGreaterEqual(stats["median"], lower, model)
            self.assertLessEqual(stats["median"], 24.5, model)

    def test_sigma_tolerance_depends_on_the_noise_level(self) -> None:
        """Known weakness of the sigma-only model (why `plateau_combined` exists): the cleaner the image, the
        tighter the tolerance and the smaller the measured spot."""
        clean = _spots(CENTERS, diameter=24.0, blur_sigma=1.5, noise=0.002)
        noisy = _spots(CENTERS, diameter=24.0, blur_sigma=1.5, noise=0.02)
        params = EdgeSizeParams(model="plateau_sigma", sigma_k=3.0)
        d_clean = summarize(measure_spot_sizes(clean, np.array(CENTERS), 24.0, params))["median"]
        d_noisy = summarize(measure_spot_sizes(noisy, np.array(CENTERS), 24.0, params))["median"]
        self.assertLess(d_clean, d_noisy - 1.0)

    def test_oversampling_keeps_the_size_close(self) -> None:
        image = _spots(CENTERS, diameter=24.0, blur_sigma=1.5)
        base = summarize(measure_spot_sizes(image, np.array(CENTERS), 24.0, EdgeSizeParams(oversample=1)))["median"]
        fine = summarize(measure_spot_sizes(image, np.array(CENTERS), 24.0, EdgeSizeParams(oversample=4)))["median"]
        self.assertAlmostEqual(base, fine, delta=0.8)

    def test_a_spot_near_the_image_edge_is_reported_not_guessed(self) -> None:
        image = _spots([(5.0, 40.0)] + CENTERS[:1], diameter=24.0, blur_sigma=1.0)
        sizes = measure_spot_sizes(image, np.array([(5.0, 40.0), CENTERS[0]]), 24.0)
        self.assertFalse(sizes[0].ok)
        self.assertTrue(sizes[1].ok)
        self.assertTrue(math.isnan(sizes[0].diameter_px))

    def test_invalid_pixels_next_to_a_spot_make_it_unmeasurable(self) -> None:
        image = _spots(CENTERS[:1], diameter=24.0, blur_sigma=1.0)
        valid = np.ones(image.shape, dtype=bool)
        valid[:, :55] = False
        sizes = measure_spot_sizes(image, np.array(CENTERS[:1]), 24.0, valid_mask=valid)
        self.assertFalse(sizes[0].ok)

    def test_no_contrast_is_reported(self) -> None:
        image = np.zeros((100, 100), dtype=np.float32)
        sizes = measure_spot_sizes(image, np.array([(50.0, 50.0)]), 20.0)
        self.assertFalse(sizes[0].ok)
        self.assertIn("contrast", sizes[0].reason)

    def test_unknown_model_and_bad_size_are_errors(self) -> None:
        image = np.zeros((50, 50), dtype=np.float32)
        with self.assertRaises(ValueError):
            measure_spot_sizes(image, np.array([(25.0, 25.0)]), 10.0, EdgeSizeParams(model="nope"))
        with self.assertRaises(ValueError):
            measure_spot_sizes(image, np.array([(25.0, 25.0)]), 0.0)

    def test_empty_input(self) -> None:
        self.assertEqual(measure_spot_sizes(np.zeros((10, 10), np.float32), np.empty((0, 2)), 4.0), [])
        self.assertEqual(summarize([])["count"], 0.0)


if __name__ == "__main__":
    unittest.main()
