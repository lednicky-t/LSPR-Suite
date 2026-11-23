"""The selection's four numbers and their wording (2026-11-23): share of the image, blobs, mean equivalent-circle diameter."""

from __future__ import annotations

import math
import sys
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image.selection_stats import (
        SelectionStats,
        format_area,
        format_density,
        format_length,
        format_percent,
        format_stats,
        selection_stats,
    )
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class SelectionStatsTest(unittest.TestCase):
    def test_empty_selection(self) -> None:
        stats = selection_stats(np.zeros((10, 20), dtype=bool))
        self.assertEqual((stats.selected_px, stats.total_px, stats.blobs, stats.mean_diameter_px), (0, 200, 0, 0.0))
        self.assertEqual(stats.percent, 0.0)

    def test_two_blobs_share_and_mean_diameter(self) -> None:
        mask = np.zeros((100, 100), dtype=bool)
        mask[10:20, 10:20] = True  # 100 px
        mask[50:80, 50:80] = True  # 900 px
        stats = selection_stats(mask)
        self.assertEqual(stats.blobs, 2)
        self.assertEqual(stats.selected_px, 1000)
        self.assertAlmostEqual(stats.percent, 10.0)
        expected = (2 * math.sqrt(100 / math.pi) + 2 * math.sqrt(900 / math.pi)) / 2  # the mean of the diameters, not the diameter of the mean
        self.assertAlmostEqual(stats.mean_diameter_px, expected, places=9)

    def test_corner_touching_pixels_are_one_blob(self) -> None:
        mask = np.zeros((6, 6), dtype=bool)
        mask[1, 1] = mask[2, 2] = True  # 8-connected, like the ROI rims
        self.assertEqual(selection_stats(mask).blobs, 1)

    def test_input_is_not_modified(self) -> None:
        mask = np.zeros((8, 8), dtype=bool)
        mask[2:4, 2:4] = True
        mask.setflags(write=False)  # the selection module hands out read-only shared arrays
        before = mask.copy()
        selection_stats(mask)
        self.assertTrue(np.array_equal(mask, before))

    def test_percent_wording(self) -> None:
        self.assertEqual(format_percent(0.0), "0 %")
        self.assertEqual(format_percent(0.004), "< 0.01 %")
        self.assertEqual(format_percent(0.256), "0.26 %")
        self.assertEqual(format_percent(12.34), "12.3 %")

    def test_length_follows_the_unit(self) -> None:
        self.assertEqual(format_length(10.0, None), "10.0 px")
        self.assertEqual(format_length(10.0, 0.1), "1.00 µm")
        self.assertEqual(format_length(4.0, 0.1), "400 nm")
        self.assertEqual(format_length(2000.0, 0.1), "200 µm")

    def test_spread_of_the_blob_sizes(self) -> None:
        mask = np.zeros((100, 100), dtype=bool)
        mask[10:20, 10:20] = True  # 100 px
        mask[50:80, 50:80] = True  # 900 px
        mask[90:93, 5:8] = True  # 9 px
        stats = selection_stats(mask)
        areas = np.array([100.0, 900.0, 9.0])
        diameters = 2 * np.sqrt(areas / math.pi)
        self.assertAlmostEqual(stats.mean_diameter_px, diameters.mean(), places=9)
        self.assertAlmostEqual(stats.sd_diameter_px, diameters.std(ddof=1), places=9)  # the sample SD, n - 1
        self.assertAlmostEqual(stats.median_diameter_px, float(np.median(diameters)), places=9)
        self.assertAlmostEqual(stats.min_diameter_px, diameters.min(), places=9)
        self.assertAlmostEqual(stats.max_diameter_px, diameters.max(), places=9)
        self.assertAlmostEqual(stats.mean_area_px, areas.mean(), places=9)
        self.assertAlmostEqual(stats.sd_area_px, areas.std(ddof=1), places=9)

    def test_one_blob_has_no_spread(self) -> None:
        mask = np.zeros((20, 20), dtype=bool)
        mask[2:6, 2:6] = True
        stats = selection_stats(mask)
        self.assertEqual((stats.blobs, stats.sd_diameter_px, stats.sd_area_px), (1, 0.0, 0.0))
        self.assertEqual(stats.min_diameter_px, stats.max_diameter_px)

    def test_area_and_density_wording(self) -> None:
        self.assertEqual(format_area(350.0, None), "350 px²")
        self.assertEqual(format_area(350.0, 0.1), "3.5 µm²")
        self.assertEqual(format_area(1e8, 0.5), "25 mm²")
        self.assertEqual(format_density(2, 20000, None), "100.0 /Mpx")
        self.assertEqual(format_density(2, 20000, 0.1), "10,000 /mm²")  # image = 200 px^2 * 0.01 µm^2/px^2 * 100 = 200 µm^2 = 2e-4 mm^2

    def test_lines_in_pixels_and_in_micrometres(self) -> None:
        mask = np.zeros((100, 200), dtype=bool)
        mask[10:20, 10:20] = True
        mask[50:70, 100:130] = True
        stats = selection_stats(mask)
        lines = format_stats(stats, None)
        self.assertEqual(lines, ("Selected: 3.5 %", "Blobs: n = 2", "⌀ = 19.5 ± 11.6 px  (mean ± SD)"))
        self.assertEqual(format_stats(stats, 0.1)[2], "⌀ = 1.95 ± 1.16 µm  (mean ± SD)")

    def test_lines_with_one_blob_and_with_none(self) -> None:
        one = SelectionStats(10, 100, 1, mean_diameter_px=3.0, mean_area_px=7.0)
        self.assertEqual(format_stats(one, None)[2], "⌀ = 3.0 px")
        self.assertEqual(format_stats(SelectionStats(0, 100, 0), None)[1:], ("no blobs",)[0:])


if __name__ == "__main__":
    unittest.main()
