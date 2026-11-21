"""`area_selection_to_raw`: the selection is copied into the mask as it is (2026-11-21).

Found on an OME-Zarr session where the bottom row could be selected but never masked: a 3x3 binary closing at the end of the function
counted everything outside the image as empty and so stripped the 1-pixel border of any selection touching the image edge. The closing
is gone (a mask is closed by the user's Close tool); without a rotation, flip or crop the result must be exactly the selection.
No Qt, no files.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.image_tools.geometry.model import CropDefinition, GeometrySettings  # noqa: E402
from lspr_imaging_app.image_tools.geometry.transform import spatial_output_shape  # noqa: E402
from lspr_imaging_app.image_tools.preprocess import area_selection_to_raw  # noqa: E402

SHAPE = (60, 90)


class AreaSelectionToRawIdentityTests(unittest.TestCase):
    """No rotation, flip or crop: processed pixel (y, x) is raw pixel (y, x), so nothing may change."""

    def test_full_image_selection_keeps_every_pixel_including_the_border(self) -> None:
        full = np.ones(SHAPE, dtype=bool)
        raw = area_selection_to_raw(full, SHAPE, GeometrySettings())
        self.assertTrue(np.array_equal(raw, full), f"{int((~raw).sum())} pixels were lost")

    def test_bottom_row_selection_reaches_the_bottom_row(self) -> None:
        region = np.zeros(SHAPE, dtype=bool)
        region[-8:, 10:70] = True
        raw = area_selection_to_raw(region, SHAPE, GeometrySettings())
        self.assertEqual(int(raw[-1].sum()), 60)
        self.assertTrue(np.array_equal(raw, region))

    def test_every_edge_and_corner_survives(self) -> None:
        for name, (rows, cols) in {
            "top": (slice(0, 5), slice(20, 40)), "bottom": (slice(-5, None), slice(20, 40)),
            "left": (slice(20, 40), slice(0, 5)), "right": (slice(20, 40), slice(-5, None)),
            "top-left corner": (slice(0, 1), slice(0, 1)), "bottom-right corner": (slice(-1, None), slice(-1, None)),
        }.items():
            with self.subTest(name):
                region = np.zeros(SHAPE, dtype=bool)
                region[rows, cols] = True
                self.assertTrue(np.array_equal(area_selection_to_raw(region, SHAPE, GeometrySettings()), region))

    def test_no_morphology_is_applied(self) -> None:
        """A one-pixel hole and a lone pixel stay exactly as drawn (a closing would have filled the hole, an opening removed the pixel)."""
        region = np.zeros(SHAPE, dtype=bool)
        region[10:30, 10:30] = True
        region[20, 20] = False
        region[50, 70] = True
        self.assertTrue(np.array_equal(area_selection_to_raw(region, SHAPE, GeometrySettings()), region))

    def test_empty_selection_gives_an_empty_mask(self) -> None:
        self.assertFalse(area_selection_to_raw(np.zeros(SHAPE, dtype=bool), SHAPE, GeometrySettings()).any())


class AreaSelectionToRawRotationTests(unittest.TestCase):
    """With a rotation the raw grid is resampled: the mask stays where the selection is, to about a pixel (see the docstring)."""

    def test_rotated_selection_lands_on_the_same_area(self) -> None:
        raw_shape = (200, 300)
        geometry = replace(GeometrySettings(), rotation_angle_deg=1.5, crop=CropDefinition())
        out = spatial_output_shape(raw_shape, geometry)
        region = np.zeros(out, dtype=bool)
        region[int(out[0] * 0.3):int(out[0] * 0.7), int(out[1] * 0.3):int(out[1] * 0.7)] = True
        raw = area_selection_to_raw(region, raw_shape, geometry)
        self.assertEqual(raw.shape, raw_shape)
        # Same area to within the one-pixel edge accuracy and the scatter's isolated gaps (0.1 % at this angle, a few % allowed here).
        self.assertLess(abs(int(raw.sum()) - int(region.sum())) / region.sum(), 0.03)


if __name__ == "__main__":
    unittest.main()
