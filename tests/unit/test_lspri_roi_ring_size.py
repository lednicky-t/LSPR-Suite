"""`roi/ring_size.py`: reference-ring diameters on synthetic spots of known size (diameters only)."""

from __future__ import annotations

import math
import unittest

import numpy as np
from scipy import ndimage

from lspr_imaging_app.roi.ring_size import (
    RingParams,
    measure_rings,
    neighbour_warnings,
    outer_from_inner,
    ring_from_inner,
    summarize_rings,
)


def _spots(centers, diameter, blur_sigma, noise=0.0, shape=(200, 260), plateau=0.3, seed=0):
    rng = np.random.default_rng(seed)
    up = 4
    yy, xx = np.mgrid[0 : shape[0] * up, 0 : shape[1] * up]
    image = np.zeros((shape[0] * up, shape[1] * up))
    for cx, cy in centers:
        image[np.hypot((xx + 0.5) / up - 0.5 - cx, (yy + 0.5) / up - 0.5 - cy) <= diameter / 2.0] = plateau
    image = image.reshape(shape[0], up, shape[1], up).mean(axis=(1, 3))
    if blur_sigma > 0:
        image = ndimage.gaussian_filter(image, blur_sigma)
    if noise > 0:
        image = image + rng.normal(0.0, noise, image.shape)
    return image.astype(np.float32)


CENTERS = [(70.0, 70.0), (160.0, 71.0), (70.5, 130.0)]
PITCH = 90.0


class RingSizeTest(unittest.TestCase):
    def test_measured_inner_diameter_clears_the_blurred_spot(self) -> None:
        image = _spots(CENTERS, 30.0, 1.5)
        rings = measure_rings(image, np.array(CENTERS), 28.0, RingParams(max_radius_px=0.5 * PITCH))
        for ring in rings:
            self.assertTrue(ring.ok)
            self.assertGreater(ring.inner_diameter_px, 30.0 + 2.0)  # beyond the true edge: no spot pixels in the ring
            self.assertLess(ring.inner_diameter_px, 30.0 + 14.0)  # but not far beyond (blur sigma 1.5)

    def test_inner_is_always_larger_than_the_sample_diameter(self) -> None:
        image = _spots(CENTERS, 30.0, 0.7)
        # a sample diameter set larger than the spot's own end: the rule still holds
        for ring in measure_rings(image, np.array(CENTERS), 36.0, RingParams(max_radius_px=0.5 * PITCH)):
            self.assertGreaterEqual(ring.inner_diameter_px, 36.0 + 1.0)

    def test_blurrier_spots_get_a_larger_inner_diameter(self) -> None:
        params = RingParams(max_radius_px=0.5 * PITCH)
        sharp = measure_rings(_spots(CENTERS, 30.0, 0.7), np.array(CENTERS), 28.0, params)
        blurry = measure_rings(_spots(CENTERS, 30.0, 2.5), np.array(CENTERS), 28.0, params)
        self.assertGreater(np.median([r.inner_diameter_px for r in blurry]), np.median([r.inner_diameter_px for r in sharp]) + 2.0)

    def test_equal_area_outer_diameter(self) -> None:
        inner, outer = ring_from_inner(40.0, 30.0, RingParams())
        self.assertAlmostEqual(math.pi / 4 * (outer**2 - inner**2), math.pi / 4 * 30.0**2)

    def test_thickness_and_ratio_rules(self) -> None:
        self.assertAlmostEqual(outer_from_inner(40.0, 30.0, RingParams(thickness_mode="thickness", thickness_px=5.0)), 50.0)
        self.assertAlmostEqual(outer_from_inner(40.0, 30.0, RingParams(thickness_mode="outer_ratio", outer_ratio=1.5)), 60.0)
        with self.assertRaises(ValueError):
            outer_from_inner(40.0, 30.0, RingParams(thickness_mode="nope"))

    def test_ratio_mode_does_not_look_at_the_image(self) -> None:
        rings = measure_rings(np.zeros((10, 10), np.float32), np.array([(5.0, 5.0)]), 20.0, RingParams(inner_mode="ratio", inner_ratio=1.5))
        self.assertAlmostEqual(rings[0].inner_diameter_px, 30.0)
        self.assertTrue(math.isnan(rings[0].spot_end_diameter_px))
        # a ratio below 1 still leaves the ring outside the disk
        low = measure_rings(np.zeros((10, 10), np.float32), np.array([(5.0, 5.0)]), 20.0, RingParams(inner_mode="ratio", inner_ratio=0.8))
        self.assertGreaterEqual(low[0].inner_diameter_px, 21.0)

    def test_a_neighbouring_spot_in_the_search_range_does_not_decide_the_inner_diameter(self) -> None:
        close = [(70.0, 70.0), (126.0, 70.0)]  # neighbour edge 41 px from the first centre (ring search radius 45)
        image = _spots(close, 30.0, 1.2)
        rings = measure_rings(image, np.array(close), 30.0, RingParams(max_radius_px=45.0, quantile=0.7))
        self.assertTrue(rings[0].ok)
        self.assertLess(rings[0].inner_diameter_px, 50.0)

    def test_margin_is_added_to_the_measured_end(self) -> None:
        image = _spots(CENTERS, 30.0, 1.5)
        base = measure_rings(image, np.array(CENTERS), 28.0, RingParams(max_radius_px=0.5 * PITCH))
        wide = measure_rings(image, np.array(CENTERS), 28.0, RingParams(max_radius_px=0.5 * PITCH, margin_px=4.0))
        self.assertAlmostEqual(wide[0].inner_diameter_px - base[0].inner_diameter_px, 4.0, delta=0.01)

    def test_unmeasurable_spots_say_why(self) -> None:
        flat = measure_rings(np.zeros((100, 100), np.float32), np.array([(50.0, 50.0)]), 20.0)
        self.assertFalse(flat[0].ok)
        self.assertIn("contrast", flat[0].reason)
        edge = measure_rings(_spots([(5.0, 80.0)], 30.0, 1.0), np.array([(5.0, 80.0)]), 28.0)
        self.assertFalse(edge[0].ok)

    def test_per_spot_sample_diameters(self) -> None:
        image = _spots(CENTERS, 30.0, 1.0)
        rings = measure_rings(image, np.array(CENTERS), [28.0, 29.0, 30.0], RingParams(max_radius_px=0.5 * PITCH))
        self.assertEqual(len(rings), 3)

    def test_neighbour_warning_only_when_a_ring_reaches_another_disk(self) -> None:
        centers = np.array([(0.0, 0.0), (35.0, 0.0), (500.0, 0.0)])
        rings = [ring_from_inner(40.0, 30.0, RingParams()) for _ in centers]
        spot_rings = [type("R", (), {"ok": True, "outer_diameter_px": outer})() for _inner, outer in rings]
        messages = neighbour_warnings(centers, 30.0, spot_rings)  # outer = 50: reaches 25 + 15 = 40 > 35
        self.assertTrue(any("Ring 1 reaches the sample disk of 2" in m for m in messages))
        self.assertFalse(any("3" in m.split("of")[-1] for m in messages))

    def test_summary_uses_the_maximum_inner_diameter(self) -> None:
        image = _spots(CENTERS, 30.0, 1.5)
        rings = measure_rings(image, np.array(CENTERS), 28.0, RingParams(max_radius_px=0.5 * PITCH))
        summary = summarize_rings(rings)
        self.assertEqual(summary["count"], 3.0)
        self.assertEqual(summary["inner_max"], max(r.inner_diameter_px for r in rings))
        self.assertTrue(math.isnan(summarize_rings([])["inner_max"]))

    def test_robust_summary_drops_a_single_outlier(self) -> None:
        from lspr_imaging_app.roi.ring_size import SpotRing

        rings = [SpotRing(46.0 + 0.2 * k, 60.0, 46.0) for k in range(20)] + [SpotRing(76.0, 90.0, 76.0)]
        summary = summarize_rings(rings)
        self.assertEqual(summary["inner_max"], 76.0)
        self.assertLess(summary["inner_robust"], 52.0)
        self.assertEqual(summary["outliers"], 1.0)

    def test_bad_arguments_are_errors(self) -> None:
        image = np.zeros((20, 20), np.float32)
        with self.assertRaises(ValueError):
            measure_rings(image, np.array([(10.0, 10.0)]), 10.0, RingParams(inner_mode="nope"))
        with self.assertRaises(ValueError):
            measure_rings(image, np.array([(10.0, 10.0)]), 0.0)
        self.assertEqual(measure_rings(image, np.empty((0, 2)), 10.0), [])


if __name__ == "__main__":
    unittest.main()
