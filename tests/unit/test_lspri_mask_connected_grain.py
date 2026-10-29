"""`image_tools/mask/raster_tools.connected_grain` (2026-10-29): the blob of mask pixels under a pixel, for the Mask tab's Eraser.
No Qt, no dataset.

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
    from lspr_imaging_app.image_tools.mask import raster_tools
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _two_squares() -> np.ndarray:
    mask = np.zeros((20, 30), dtype=bool)
    mask[2:6, 3:9] = True  # A: 4 x 6
    mask[10:14, 20:25] = True  # B: 4 x 5
    return mask


class ConnectedGrainTest(unittest.TestCase):
    def test_a_click_inside_a_blob_gives_exactly_that_blob(self) -> None:
        mask = _two_squares()
        grain = raster_tools.connected_grain(mask, 3, 4)
        expected = np.zeros_like(mask)
        expected[2:6, 3:9] = True
        np.testing.assert_array_equal(grain, expected)
        other = raster_tools.connected_grain(mask, 12, 22)
        self.assertEqual(int(other.sum()), 20)
        self.assertFalse((grain & other).any())

    def test_every_pixel_of_a_blob_gives_the_same_blob(self) -> None:
        mask = _two_squares()
        reference = raster_tools.connected_grain(mask, 2, 3)
        for row, col in zip(*np.nonzero(reference)):
            np.testing.assert_array_equal(raster_tools.connected_grain(mask, int(row), int(col)), reference)

    def test_a_pixel_that_is_not_masked_or_off_the_array_gives_none(self) -> None:
        mask = _two_squares()
        self.assertIsNone(raster_tools.connected_grain(mask, 0, 0))
        self.assertIsNone(raster_tools.connected_grain(mask, 7, 15))
        for row, col in ((-1, 3), (3, -1), (20, 3), (3, 30)):
            self.assertIsNone(raster_tools.connected_grain(mask, row, col))
        hollow = np.zeros((9, 9), dtype=bool)
        hollow[2:7, 2:7] = True
        hollow[4, 4] = False  # a hole inside a grain is not a grain
        self.assertIsNone(raster_tools.connected_grain(hollow, 4, 4))

    def test_corner_contact_joins_two_pixels_unless_four_connected_is_asked_for(self) -> None:
        mask = np.zeros((6, 6), dtype=bool)
        mask[1, 1] = True
        mask[2, 2] = True  # touches (1, 1) at a corner only
        self.assertEqual(int(raster_tools.connected_grain(mask, 1, 1).sum()), 2)
        self.assertEqual(int(raster_tools.connected_grain(mask, 1, 1, diagonal=False).sum()), 1)
        mask[1, 2] = True  # now they share an edge through (1, 2)
        self.assertEqual(int(raster_tools.connected_grain(mask, 1, 1, diagonal=False).sum()), 3)

    def test_a_blob_touching_the_border_and_a_snake_are_found_whole(self) -> None:
        mask = np.zeros((10, 10), dtype=bool)
        mask[0, :] = True  # along the top border
        mask[1:9, 9] = True  # down the right border: one L-shaped blob
        mask[5, 3] = True  # and a separate pixel
        grain = raster_tools.connected_grain(mask, 0, 0)
        self.assertEqual(int(grain.sum()), 10 + 8)
        self.assertFalse(grain[5, 3])

    def test_the_input_is_not_changed_and_the_result_is_a_new_array(self) -> None:
        mask = _two_squares()
        before = mask.copy()
        grain = raster_tools.connected_grain(mask, 3, 4)
        np.testing.assert_array_equal(mask, before)
        self.assertEqual(grain.shape, mask.shape)
        self.assertEqual(grain.dtype, bool)
        self.assertFalse(np.shares_memory(grain, mask))

    def test_a_non_boolean_mask_is_read_as_bool(self) -> None:
        mask = _two_squares().astype(np.uint8) * 255
        self.assertEqual(int(raster_tools.connected_grain(mask, 3, 4).sum()), 24)


if __name__ == "__main__":
    unittest.main()
