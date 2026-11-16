"""The Threshold tool's two-stage maths and its two sides (2026-11-16): `raster_tools.relative_deviation` (the slow blur),
`relative_deviation_mask` (the compare, with `side` = brighter / darker / both) and `mask_candidates.PlaneCache` (the thread-safe keeper of the
slow half). No Qt, no dataset.

Pins: the default (both sides) is **bit-identical** to the single function the tool used before (the numbers the Mask tab and every
saved result rest on did not change); brighter and darker are the two halves of both and never overlap; a pixel without a value is never
selected; the two stages give exactly the one-stage answer; the cache computes once per key, drops the least recently used, and never makes
the GUI thread wait (`invalidate`).

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see `test_lspri_rewrite_analysis_core.py`'s docstring.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.mask import raster_tools
    from lspr_imaging_app.image_tools.mask_candidates import PlaneCache
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

SHAPE = (90, 120)
SIGMA = 20.0
FRACTION = 0.20


def _image() -> np.ndarray:
    """Noisy background around 1000 counts, a bright disk (+60 %), a dark disk (-50 %) and a corner without a value."""
    image = np.random.default_rng(7).normal(1000.0, 15.0, SHAPE)
    yy, xx = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
    image[np.hypot(yy - 30, xx - 35) <= 8] += 600.0
    image[np.hypot(yy - 60, xx - 85) <= 9] -= 500.0
    image[:6, -8:] = np.nan
    return image.astype(np.float32)


def _the_old_function(image: np.ndarray, sigma_px: float, threshold_fraction: float) -> np.ndarray:
    """`create_relative_contrast_mask` as it was before 2026-11-16, copied here as the reference the new code must equal bit for bit."""
    sigma = max(float(sigma_px), 1.0)
    image_f32 = image.astype(np.float32, copy=False)
    valid = np.isfinite(image_f32)
    profile = raster_tools._normalized_gaussian(image_f32, valid, sigma)
    fraction = max(float(threshold_fraction), 0.0)
    safe_profile = np.maximum(profile, 1e-6)
    relative_delta = (image_f32 - safe_profile) / safe_profile
    with np.errstate(invalid="ignore"):
        return np.abs(relative_delta) >= fraction


class RelativeContrastSidesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.image = _image()
        self.deviation = raster_tools.relative_deviation(self.image, sigma_px=SIGMA)

    def _mask(self, side: str, fraction: float = FRACTION) -> np.ndarray:
        return raster_tools.relative_deviation_mask(self.deviation, threshold_fraction=fraction, side=side)

    def test_both_sides_is_bit_identical_to_the_function_the_tool_used_before(self) -> None:
        before = _the_old_function(self.image, SIGMA, FRACTION)
        self.assertTrue(before.any())
        np.testing.assert_array_equal(raster_tools.create_relative_contrast_mask(self.image, sigma_px=SIGMA, threshold_fraction=FRACTION), before)
        np.testing.assert_array_equal(self._mask(raster_tools.SIDE_BOTH), before)
        for fraction in (0.05, 0.5, 0.0, 3.0):  # also the edges: nothing but noise, the whole image, nothing at all
            np.testing.assert_array_equal(self._mask("both", fraction), _the_old_function(self.image, SIGMA, fraction), fraction)

    def test_brighter_and_darker_are_the_two_halves_of_both_and_never_overlap(self) -> None:
        for fraction in (0.02, 0.1, FRACTION, 0.5):
            with self.subTest(fraction=fraction):
                brighter, darker, both = self._mask("brighter", fraction), self._mask("darker", fraction), self._mask("both", fraction)
                np.testing.assert_array_equal(brighter | darker, both)
                self.assertFalse(np.any(brighter & darker))

    def test_brighter_finds_the_bright_disk_and_darker_the_dark_one(self) -> None:
        yy, xx = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
        bright = np.hypot(yy - 30, xx - 35) <= 8
        dark = np.hypot(yy - 60, xx - 85) <= 9
        brighter, darker = self._mask("brighter"), self._mask("darker")
        self.assertGreater(int((brighter & bright).sum()), int(0.8 * bright.sum()))
        self.assertEqual(int((brighter & dark).sum()), 0)
        self.assertGreater(int((darker & dark).sum()), int(0.8 * dark.sum()))
        self.assertEqual(int((darker & bright).sum()), 0)

    def test_a_higher_threshold_selects_fewer_pixels_on_either_side(self) -> None:
        for side in ("brighter", "darker"):
            counts = [int(self._mask(side, fraction).sum()) for fraction in (0.05, 0.1, 0.2, 0.4)]
            self.assertEqual(counts, sorted(counts, reverse=True), side)

    def test_a_pixel_without_a_value_is_never_selected_on_any_side(self) -> None:
        missing = ~np.isfinite(self.image)
        self.assertTrue(missing.any())
        for side in raster_tools.SIDES:
            self.assertFalse(np.any(self._mask(side, 0.0) & missing), side)  # even at threshold 0, where every pixel with a value passes

    def test_darker_cannot_go_past_black(self) -> None:
        """A positive image is never more than 100 % below its (positive) background: 101 % selects nothing on the darker side."""
        self.assertFalse(self._mask("darker", 1.01).any())

    def test_the_two_stages_give_exactly_the_one_stage_answer(self) -> None:
        for side in raster_tools.SIDES:
            np.testing.assert_array_equal(
                self._mask(side), raster_tools.create_relative_contrast_mask(self.image, sigma_px=SIGMA, threshold_fraction=FRACTION, side=side), side
            )

    def test_an_unknown_side_is_refused_not_taken_for_both(self) -> None:
        with self.assertRaises(ValueError):
            raster_tools.relative_deviation_mask(self.deviation, threshold_fraction=FRACTION, side="above")

    def test_the_deviation_is_a_float32_plane_of_the_images_shape_with_nan_where_there_is_no_value(self) -> None:
        self.assertEqual(self.deviation.shape, SHAPE)
        self.assertEqual(self.deviation.dtype, np.float32)
        np.testing.assert_array_equal(np.isnan(self.deviation), ~np.isfinite(self.image))


class PlaneCacheTest(unittest.TestCase):
    def test_a_hit_is_not_computed_again_and_is_the_same_plane(self) -> None:
        cache = PlaneCache(2)
        calls: list[int] = []

        def compute() -> np.ndarray:
            calls.append(1)
            return np.ones((2, 2), dtype=np.float32)

        first = cache.get_or_compute("a", compute)
        self.assertIs(cache.get_or_compute("a", compute), first)
        self.assertEqual(len(calls), 1)
        self.assertTrue(cache.cached("a"))

    def test_the_least_recently_used_plane_is_dropped(self) -> None:
        cache = PlaneCache(2)
        for key in ("a", "b"):
            cache.get_or_compute(key, lambda: np.zeros(1, dtype=np.float32))
        cache.get_or_compute("a", lambda: np.zeros(1, dtype=np.float32))  # "a" is now the newer one
        cache.get_or_compute("c", lambda: np.zeros(1, dtype=np.float32))  # one too many: "b" goes
        self.assertTrue(cache.cached("a") and cache.cached("c"))
        self.assertFalse(cache.cached("b"))

    def test_two_threads_asking_for_the_same_key_compute_it_once(self) -> None:
        cache = PlaneCache(2)
        started = threading.Event()
        release = threading.Event()
        calls: list[int] = []

        def slow() -> np.ndarray:
            calls.append(1)
            started.set()
            release.wait(10.0)
            return np.ones(3, dtype=np.float32)

        results: list[np.ndarray] = []
        first = threading.Thread(target=lambda: results.append(cache.get_or_compute("k", slow)))
        first.start()
        self.assertTrue(started.wait(5.0))
        second = threading.Thread(target=lambda: results.append(cache.get_or_compute("k", slow)))
        second.start()  # waits for the first one's computation, then finds the plane cached
        time.sleep(0.05)
        release.set()
        first.join(5.0)
        second.join(5.0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 2)
        self.assertIs(results[0], results[1])

    def test_invalidate_never_waits_for_a_running_computation_and_that_plane_is_not_kept(self) -> None:
        """`invalidate` runs on the GUI thread (the dataset was replaced): a blur that takes seconds must not hold it up."""
        cache = PlaneCache(2)
        started = threading.Event()
        release = threading.Event()

        def slow() -> np.ndarray:
            started.set()
            release.wait(10.0)
            return np.ones(3, dtype=np.float32)

        worker = threading.Thread(target=lambda: cache.get_or_compute("k", slow))
        worker.start()
        self.assertTrue(started.wait(5.0))
        began = time.monotonic()
        cache.invalidate()
        self.assertLess(time.monotonic() - began, 1.0, "invalidate did not wait for the computation")
        release.set()
        worker.join(5.0)
        self.assertFalse(cache.cached("k"), "a plane computed for the old dataset is not kept")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
