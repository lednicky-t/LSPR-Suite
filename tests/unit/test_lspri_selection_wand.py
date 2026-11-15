"""The magic wand's pure computation (`image_tools/selection_wand.py`, 2026-11-14): no Qt, no files.

The test picture imitates the LSPRi data: a sloping illumination, a lattice of small bright disks (pitch 12 px), a dark
soft-edged dust shadow (radius 15 px), a bright particle (radius 6 px) and Gaussian noise (sigma 20 counts).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

_SRC = Path(__file__).resolve().parents[2] / "apps" / "LSPRi" / "eva" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lspr_imaging_app.image_tools.selection_wand import (  # noqa: E402
    WandMethod,
    WandSettings,
    estimate_pixel_noise,
    prepare_image,
    wand_region,
)

_NOISE = 20.0
_DUST = (200, 300, 15.0)
_PARTICLE = (100, 100, 6.0)


def _picture(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:400, 0:600].astype(float)
    image = 1000.0 + 0.5 * xx
    image += 150.0 * ((((yy % 12) - 6) ** 2 + ((xx % 12) - 6) ** 2) <= 2.5**2)
    with np.errstate(over="ignore"):
        image -= 400.0 / (1.0 + np.exp((np.hypot(yy - _DUST[0], xx - _DUST[1]) - _DUST[2]) / 1.5))
        image += 800.0 / (1.0 + np.exp((np.hypot(yy - _PARTICLE[0], xx - _PARTICLE[1]) - _PARTICLE[2]) / 0.8))
    image += rng.normal(0.0, _NOISE, image.shape)
    image[:5, :] = np.nan  # a strip without values, like the corners of a rotated image
    return image.astype(np.float32)


def _equivalent_radius(region: np.ndarray) -> float:
    return float(np.sqrt(np.count_nonzero(region) / np.pi))


class NoiseEstimateTest(unittest.TestCase):
    def test_the_noise_of_the_test_picture_is_found_despite_its_structure(self) -> None:
        # The lattice edges and the slope are a minority of the neighbour differences: within 15 % of the true sigma.
        self.assertAlmostEqual(estimate_pixel_noise(_picture()), _NOISE, delta=0.15 * _NOISE)

    def test_a_picture_without_values_has_no_noise(self) -> None:
        self.assertEqual(estimate_pixel_noise(np.full((4, 4), np.nan)), 0.0)


class WandMethodsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.image = _picture()

    def _run(self, method: WandMethod, row: int, col: int, **overrides: object):
        settings = WandSettings(method=method, smoothing_px=1.5, **overrides)  # type: ignore[arg-type]
        return wand_region(self.image, row, col, settings)

    def test_contrast_finds_the_dark_dust_and_the_bright_particle(self) -> None:
        for (row, col, radius), low, high in ((_DUST, 15.0, 20.5), (_PARTICLE, 6.0, 10.0)):
            for d_row, d_col in ((0, 0), (3, -4)):  # the click need not be at the centre
                result = self._run(WandMethod.CONTRAST, row + d_row, col + d_col)
                self.assertIsNotNone(result.region, result.message)
                self.assertTrue(result.region[row, col])
                # The threshold also catches the faint rim of a soft edge, so the region is a little wider than the radius.
                self.assertTrue(low <= _equivalent_radius(result.region) <= high, (radius, _equivalent_radius(result.region)))

    def test_contrast_refuses_a_click_on_plain_background_and_says_why(self) -> None:
        flat = np.random.default_rng(3).normal(1000.0, _NOISE, (200, 200)).astype(np.float32)
        result = wand_region(flat, 100, 100, WandSettings(method=WandMethod.CONTRAST, smoothing_px=1.5))
        self.assertIsNone(result.region)
        self.assertIn("less than the threshold", result.message)

    def test_contrast_in_the_gaps_of_an_array_selects_the_whole_gap_network(self) -> None:
        # The gaps between the disks are darker than the local mean (which includes the disks) and all connected: a click
        # there selects the whole gap network. There is no size limit (2026-11-15: the selection is undoable instead).
        result = self._run(WandMethod.CONTRAST, 300, 450)
        self.assertIsNotNone(result.region, result.message)
        self.assertGreater(result.area_px, 20000, "more than the old Max size default")

    def test_edges_puts_the_border_on_the_steepest_point_of_the_edge(self) -> None:
        for row, col, radius in (_DUST, _PARTICLE):
            result = self._run(WandMethod.EDGES, row + 2, col - 1)
            self.assertIsNotNone(result.region, result.message)
            # Smoothing rounds a curved edge inwards a little: within 12 % of the true radius.
            self.assertAlmostEqual(_equivalent_radius(result.region), radius, delta=0.12 * radius)

    def test_brightness_grows_with_the_tolerance(self) -> None:
        small = self._run(WandMethod.BRIGHTNESS, *_DUST[:2], brightness_tolerance=5.0)
        large = self._run(WandMethod.BRIGHTNESS, *_DUST[:2], brightness_tolerance=15.0)
        self.assertIsNotNone(small.region, small.message)
        self.assertIsNotNone(large.region, large.message)
        self.assertTrue(np.all(large.region[small.region]), "a larger tolerance keeps every pixel of a smaller one")
        self.assertGreater(large.area_px, small.area_px)

    def test_a_big_region_is_returned_whole_there_is_no_size_limit(self) -> None:
        from scipy import ndimage

        result = self._run(WandMethod.BRIGHTNESS, 300, 450, brightness_tolerance=60.0)
        self.assertIsNotNone(result.region, result.message)
        self.assertGreater(result.area_px, 20000)
        _labels, count = ndimage.label(result.region, structure=np.ones((3, 3), dtype=bool))
        self.assertEqual(count, 1, "still one connected region")
        self.assertFalse(hasattr(WandSettings(), "max_area_px"), "the Max size setting is gone")

    def test_edges_follows_a_feature_far_bigger_than_the_old_max_size(self) -> None:
        # A disk of radius 90 px (25,000 px: over the old 20,000 px default) in plain noise. A click at its centre (flat
        # inside) and a click just inside its edge (on the slope: no flat piece to start from) both find the whole disk.
        # (A click exactly on the crest of the edge is not asked for: from a single pixel there the background floods over
        # the crest first, as it did before the limit went.)
        rng = np.random.default_rng(5)
        yy, xx = np.mgrid[0:400, 0:500].astype(float)
        image = 1000.0 + 600.0 / (1.0 + np.exp((np.hypot(yy - 200, xx - 250) - 90.0) / 1.0)) + rng.normal(0.0, _NOISE, yy.shape)
        for row, col in ((200, 250), (200, 250 + 87)):
            result = wand_region(image.astype(np.float32), row, col, WandSettings(method=WandMethod.EDGES, smoothing_px=1.5))
            self.assertIsNotNone(result.region, (row, col, result.message))
            self.assertAlmostEqual(_equivalent_radius(result.region), 90.0, delta=0.08 * 90.0, msg=(row, col))

    def test_edges_on_plain_background_selects_the_open_ground_instead_of_refusing(self) -> None:
        # Click in the plain background around a dark blob: the region is the edge-free ground connected to the click, as
        # for the other methods (it was refused as "too big" while Max size existed). Ground enclosed by edges (the blob)
        # is shared out to the region around it by the method's design, so it is part of this one.
        rng = np.random.default_rng(6)
        yy, xx = np.mgrid[0:200, 0:300].astype(float)
        image = 1000.0 - 500.0 / (1.0 + np.exp((np.hypot(yy - 100, xx - 150) - 20.0) / 1.0)) + rng.normal(0.0, _NOISE, yy.shape)
        result = wand_region(image.astype(np.float32), 10, 10, WandSettings(method=WandMethod.EDGES, smoothing_px=1.5))
        self.assertIsNotNone(result.region, result.message)
        self.assertGreater(result.area_px, 50000)
        self.assertTrue(result.region[10, 10])

    def test_a_click_without_a_value_selects_nothing(self) -> None:
        for method in WandMethod:
            result = self._run(method, 2, 100)
            self.assertIsNone(result.region)
            self.assertIn("no value", result.message)

    def test_grow_adds_a_margin_of_that_many_pixels(self) -> None:
        plain = self._run(WandMethod.CONTRAST, *_PARTICLE[:2])
        grown = self._run(WandMethod.CONTRAST, *_PARTICLE[:2], grow_px=3)
        self.assertTrue(np.all(grown.region[plain.region]))
        self.assertAlmostEqual(_equivalent_radius(grown.region) - _equivalent_radius(plain.region), 3.0, delta=0.5)

    def test_the_result_is_one_connected_region_containing_the_click(self) -> None:
        from scipy import ndimage

        for method in WandMethod:
            result = self._run(method, *_DUST[:2])
            self.assertIsNotNone(result.region, (method, result.message))
            _labels, count = ndimage.label(result.region, structure=np.ones((3, 3), dtype=bool))
            self.assertEqual(count, 1, method)
            self.assertFalse(result.region[:5, :].any(), "pixels without a value are never selected")


class WandHousekeepingTest(unittest.TestCase):
    def test_a_prepared_image_gives_the_same_region_as_a_fresh_run(self) -> None:
        image = _picture(1)
        settings = WandSettings(method=WandMethod.CONTRAST)
        prepared = prepare_image(image, settings)
        fresh = wand_region(image, *_DUST[:2], settings)
        reused = wand_region(image, *_DUST[:2], settings, prepared=prepared)
        np.testing.assert_array_equal(fresh.region, reused.region)

    def test_a_prepared_image_for_other_settings_is_not_used(self) -> None:
        image = _picture(1)
        prepared = prepare_image(image, WandSettings(smoothing_px=4.0))
        settings = WandSettings(method=WandMethod.CONTRAST, smoothing_px=1.0)
        np.testing.assert_array_equal(
            wand_region(image, *_DUST[:2], settings, prepared=prepared).region, wand_region(image, *_DUST[:2], settings).region
        )

    def test_the_image_is_not_modified(self) -> None:
        image = _picture(2)
        before = image.copy()
        for method in WandMethod:
            wand_region(image, *_DUST[:2], WandSettings(method=method))
        np.testing.assert_array_equal(image, before)

    def test_a_click_outside_the_image_selects_nothing(self) -> None:
        self.assertIsNone(wand_region(_picture(), -1, 5, WandSettings()).region)


if __name__ == "__main__":
    unittest.main()
