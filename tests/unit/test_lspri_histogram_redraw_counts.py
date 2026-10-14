"""Tests for `panels/histogram/compute.py`'s `redraw_counts`: the numeric half of a Histogram redraw that runs on a
worker thread (no Qt, no files). It must give exactly what the redraw used to compute inline, and never modify
what it is handed (the frame, the area-selection region and the ignore mask are shared with the GUI thread).
"""

from __future__ import annotations

import sys
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.histogram import compute
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc


def _frame() -> np.ndarray:
    frame = np.random.default_rng(3).normal(2000.0, 300.0, (90, 120)).astype(np.float32)
    frame[:6, :10] = np.nan
    return frame


def _region() -> np.ndarray:
    region = np.zeros((90, 120), bool)
    region[20:70, 30:100] = True
    return region


def _ignore() -> np.ndarray:
    mask = np.zeros((90, 120), bool)
    mask[40:60, 50:80] = True
    return mask


def _inline(image, region, ignore_mask, edges):
    """What the panel computed inline before the worker existed (the reference)."""
    if region is not None:
        image = np.where(region, image, np.float32(np.nan))
    total = int(np.count_nonzero(np.isfinite(image)))
    return (
        image,
        total,
        compute.population_counts(image, edges),
        None if ignore_mask is None else compute.population_counts(image[ignore_mask], edges),
    )


class RedrawCountsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.edges = compute.histogram_edges(256.0, floor=0.0, ceiling=4096.0)

    def test_matches_the_inline_computation_in_every_combination(self) -> None:
        for name, region, ignore in (
            ("plain", None, None),
            ("area selection", _region(), None),
            ("ignore mask", None, _ignore()),
            ("both", _region(), _ignore()),
        ):
            with self.subTest(name):
                image, total, all_counts, ignore_counts = _inline(_frame(), region, ignore, self.edges)
                result = compute.redraw_counts(_frame(), region, ignore, self.edges)
                np.testing.assert_array_equal(result.image, image)  # NaN positions compare equal
                self.assertEqual(result.total_pixels, total)
                np.testing.assert_array_equal(result.all_counts, all_counts)
                if ignore is None:
                    self.assertIsNone(result.ignore_counts)
                else:
                    np.testing.assert_array_equal(result.ignore_counts, ignore_counts)

    def test_in_domain_is_the_selection_size_or_the_whole_frame(self) -> None:
        self.assertEqual(compute.redraw_counts(_frame(), None, None, self.edges).in_domain, 90 * 120)
        self.assertEqual(compute.redraw_counts(_frame(), _region(), None, self.edges).in_domain, 50 * 70)

    def test_pixels_outside_the_selection_count_as_no_data(self) -> None:
        result = compute.redraw_counts(_frame(), _region(), None, self.edges)
        self.assertTrue(np.isnan(result.image[~_region()]).all())
        self.assertLessEqual(result.total_pixels, int(_region().sum()))

    def test_the_counts_are_the_numpy_histogram_of_the_finite_pixels(self) -> None:
        frame = _frame()
        expected, _ = np.histogram(frame[np.isfinite(frame)].astype(np.float64), bins=self.edges)
        np.testing.assert_array_equal(compute.redraw_counts(frame, None, None, self.edges).all_counts, expected)

    def test_nothing_it_is_given_is_modified(self) -> None:
        frame, region, ignore = _frame(), _region(), _ignore()
        region.setflags(write=False)  # the shared arrays are read-only in the app; a write would raise
        ignore.setflags(write=False)
        frame.setflags(write=False)
        before = (frame.copy(), region.copy(), ignore.copy(), self.edges.copy())
        compute.redraw_counts(frame, region, ignore, self.edges)
        np.testing.assert_array_equal(frame, before[0])
        np.testing.assert_array_equal(region, before[1])
        np.testing.assert_array_equal(ignore, before[2])
        np.testing.assert_array_equal(self.edges, before[3])

    def test_a_frame_without_values_gives_zero_counts(self) -> None:
        result = compute.redraw_counts(np.full((20, 30), np.nan, np.float32), None, None, self.edges)
        self.assertEqual(result.total_pixels, 0)
        self.assertEqual(float(result.all_counts.sum()), 0.0)


if __name__ == "__main__":
    unittest.main()
