"""`roi/array_detection.py`: what is left of the old detector - the spot-size scan the array finder uses when no ROI says how big the spots are (2026-11-25).

Tested the way it is used: through `find_candidates` / `build_array` on synthetic images of known geometry (pure numpy; no Qt, no files). The old tests of the retired
`find_array` (clean array, pitch, tilt, missing spots, masks ...) are covered for the finder in `test_lspri_array_finder.py`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()
sys.path.insert(0, str(REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from _array_synth import synth  # noqa: E402

from lspr_imaging_app.roi.array_finder import FinderError, FinderSettings, build_array, find_candidates  # noqa: E402


def _find_without_a_guide(image: np.ndarray):
    candidates = find_candidates(image, (), FinderSettings())
    return candidates, build_array(candidates, FinderSettings())


class SpotSizeScanTest(unittest.TestCase):
    """No ROI given: the size comes from a scan over 6-120 px scored by lattice fit (2026-10-20). The single scale-space guess it replaced put a real 34 px array at
    54-68 px or 12 px and then found no spot at all."""

    def _assert_whole_array(self, image: np.ndarray, rows: int = 4, cols: int = 6):
        candidates, result = _find_without_a_guide(image)
        self.assertTrue(result.ordered, result.report)
        self.assertEqual((result.rows, result.cols), (rows, cols))
        self.assertEqual(result.count, rows * cols)
        return candidates, result

    def test_small_medium_and_large_spots_are_all_found(self) -> None:
        for diameter in (10.0, 24.0, 50.0):
            with self.subTest(diameter=diameter):
                pitch = 2.6 * diameter
                image, _truth = synth(
                    rows=4, cols=6, pitch=(pitch, pitch), diameter=diameter, origin=(1.5 * pitch, 1.3 * pitch), shape=(int(5.6 * pitch), int(7.8 * pitch))
                )
                candidates, _result = self._assert_whole_array(image)
                # The search size lands near the true size (the disk test tolerates about +-25 %).
                self.assertLess(abs(candidates.diameter_px / diameter - 1.0), 0.35)

    def test_debris_does_not_win_the_size_scan(self) -> None:
        image, _truth = synth(rows=4, cols=6, pitch=(64.0, 64.0), diameter=24.0, origin=(64.0, 58.0), shape=(320, 470))
        rng = np.random.default_rng(5)
        yy, xx = np.mgrid[0 : image.shape[0], 0 : image.shape[1]]
        for _ in range(40):  # small dark specks scattered between the spots
            cx, cy = rng.uniform(0, image.shape[1]), rng.uniform(0, image.shape[0])
            image[np.hypot(xx - cx, yy - cy) <= 3.0] *= 0.5
        self._assert_whole_array(image)

    def test_noise_is_refused_with_a_reason_never_a_fake_array(self) -> None:
        image = np.random.default_rng(0).normal(1000.0, 10.0, (300, 400)).astype(np.float32)
        try:
            _candidates, result = _find_without_a_guide(image)
        except FinderError as error:
            self.assertTrue(str(error))
        else:
            self.assertFalse(result.ordered, "pure noise must not give a lattice")

    def test_a_flat_image_says_no_spot_size_was_found(self) -> None:
        with self.assertRaises(FinderError) as caught:
            find_candidates(np.full((300, 400), 1000.0, dtype=np.float32), (), FinderSettings())
        self.assertIn("any spot size", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
