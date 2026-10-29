"""`raster_tools.morphology_changed_pixels` (2026-10-29): the pixels a morphological operation would flip, which the Mask tab's hover
preview tints. No Qt, no dataset.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see `test_lspri_rewrite_analysis_core.py`'s docstring.
"""

from __future__ import annotations

import sys
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.mask.raster_tools import apply_morphology_to_mask, morphology_changed_pixels
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _square() -> np.ndarray:
    mask = np.zeros((9, 9), dtype=bool)
    mask[2:7, 2:7] = True  # 5 x 5
    return mask


class MorphologyChangedPixelsTest(unittest.TestCase):
    def test_dilate_changes_the_ring_around_the_mask_only(self) -> None:
        mask = _square()
        changed = morphology_changed_pixels(mask, "dilate", 1)
        self.assertEqual(int(changed.sum()), 20)  # the 4 x 5 pixels that touch the square's edges
        self.assertFalse(np.any(changed & mask))  # nothing inside the mask changes
        np.testing.assert_array_equal(changed | mask, apply_morphology_to_mask(mask, "dilate", 1))

    def test_erode_changes_the_rim_of_the_mask_only(self) -> None:
        mask = _square()
        changed = morphology_changed_pixels(mask, "erode", 1)
        self.assertEqual(int(changed.sum()), 16)  # the square's own edge pixels
        self.assertTrue(np.all(mask[changed]))  # every changed pixel was in the mask
        np.testing.assert_array_equal(mask & ~changed, apply_morphology_to_mask(mask, "erode", 1))

    def test_open_removes_a_speck_and_close_fills_a_hole(self) -> None:
        lone = np.zeros((9, 9), dtype=bool)
        lone[4, 4] = True
        opened = morphology_changed_pixels(lone, "open", 1)
        self.assertEqual(int(opened.sum()), 1)
        self.assertTrue(opened[4, 4])
        holed = _square()
        holed[4, 4] = False
        closed = morphology_changed_pixels(holed, "close", 1)
        self.assertEqual(int(closed.sum()), 1)
        self.assertTrue(closed[4, 4])

    def test_open_of_a_square_rounds_off_its_four_corners(self) -> None:
        # The app's structuring element is a cross (4-connected), so an opening cannot keep a square's corners: the preview
        # must show them, because Open really removes them.
        changed = morphology_changed_pixels(_square(), "open", 1)
        self.assertEqual({tuple(p) for p in np.argwhere(changed)}, {(2, 2), (2, 6), (6, 2), (6, 6)})

    def test_nothing_to_change_gives_an_empty_result(self) -> None:
        self.assertFalse(np.any(morphology_changed_pixels(np.zeros((9, 9), dtype=bool), "dilate", 3)))
        self.assertFalse(np.any(morphology_changed_pixels(_square(), "unknown-operation", 1)))

    def test_the_input_is_not_modified(self) -> None:
        mask = _square()
        before = mask.copy()
        morphology_changed_pixels(mask, "dilate", 2)
        np.testing.assert_array_equal(mask, before)


if __name__ == "__main__":
    unittest.main()
