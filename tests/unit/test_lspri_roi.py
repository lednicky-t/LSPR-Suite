from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.domain.models import RoiDefinition
from lspr_imaging_app.processing.roi import region_means, roi_masks


def _roi(shape: str = "ellipse", **overrides) -> RoiDefinition:
    defaults = dict(
        roi_id="roi-1",
        name="Test ROI",
        shape=shape,
        center_x=50.0,
        center_y=50.0,
        size_x=20.0,
        size_y=20.0,
        background_padding_px=5.0,
        background_width_px=8.0,
    )
    defaults.update(overrides)
    return RoiDefinition(**defaults)


class TestRoiMasks(unittest.TestCase):
    def test_ellipse_inner_mask_is_centered_and_nonempty(self) -> None:
        roi = _roi(shape="ellipse")
        inner, background = roi_masks((100, 100), roi)
        self.assertTrue(inner[50, 50])
        self.assertGreater(int(inner.sum()), 0)
        # Center pixel is inside the sample region, not the background ring.
        self.assertFalse(background[50, 50])
        # A point far outside the ROI+background extent is in neither mask.
        self.assertFalse(inner[0, 0])
        self.assertFalse(background[0, 0])

    def test_rectangle_inner_mask_matches_half_extents(self) -> None:
        roi = _roi(shape="rectangle", center_x=50.0, center_y=50.0, size_x=20.0, size_y=10.0)
        inner, _ = roi_masks((100, 100), roi)
        # Rectangle inner region is |x-cx|<=size_x/2 and |y-cy|<=size_y/2.
        self.assertTrue(inner[50, 50])
        self.assertTrue(inner[54, 59])  # within +/-5 y, +/-10 x
        self.assertFalse(inner[50, 61])  # just outside the x half-extent
        self.assertFalse(inner[56, 50])  # just outside the y half-extent

    def test_background_ring_excludes_the_padding_gap(self) -> None:
        roi = _roi(shape="ellipse", size_x=10.0, size_y=10.0, background_padding_px=5.0, background_width_px=8.0)
        inner, background = roi_masks((100, 100), roi)
        # Directly adjacent to the inner disk (inside the padding gap) is
        # neither sample nor background.
        self.assertFalse(inner[50, 57])
        self.assertFalse(background[50, 57])
        # Further out, inside the background ring itself.
        self.assertTrue(background[50, 62])


class TestRegionMeans(unittest.TestCase):
    def test_recovers_known_constant_means(self) -> None:
        image = np.full((100, 100), 1000.0, dtype=np.float32)
        roi = _roi(shape="ellipse", center_x=50.0, center_y=50.0, size_x=10.0, size_y=10.0,
                   background_padding_px=5.0, background_width_px=10.0)
        inner, background = roi_masks(image.shape, roi)
        image[inner] = 200.0
        image[background] = 800.0
        roi_mean, background_mean = region_means(image, roi)
        self.assertAlmostEqual(roi_mean, 200.0, places=4)
        self.assertAlmostEqual(background_mean, 800.0, places=4)

    def test_roi_entirely_outside_image_raises(self) -> None:
        roi = _roi(shape="ellipse", center_x=-1000.0, center_y=-1000.0, size_x=4.0, size_y=4.0,
                   background_padding_px=1.0, background_width_px=1.0)
        image = np.zeros((20, 20), dtype=np.float32)
        with self.assertRaises(ValueError):
            region_means(image, roi)


if __name__ == "__main__":
    unittest.main()
