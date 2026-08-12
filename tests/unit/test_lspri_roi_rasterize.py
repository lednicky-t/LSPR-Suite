from __future__ import annotations

import math
import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.domain.models import AreaRoi, RoiMask
from lspr_imaging_app.gui.analysis_tasks import _selected_roi_masks_for_spectrum
from lspr_imaging_app.processing.analysis import absorbance_from_means
from lspr_imaging_app.processing.roi_rasterize import crop_mask, expand_mask, expand_mask_to_patch


def _synthetic_image() -> np.ndarray:
    # Matches the spec's own numeric regression example (section 38):
    # a uniform background of 100 with a 20x20 block of 50 in the middle,
    # so a circle sample ROI vs. an equivalent mask ROI must both read 50,
    # and a reference ROI in the background must read 100.
    image = np.full((100, 100), 100.0, dtype=np.float32)
    image[40:60, 40:60] = 50.0
    return image


class TestCropAndExpandMask(unittest.TestCase):
    def test_crop_then_expand_round_trips(self) -> None:
        full = np.zeros((20, 20), dtype=bool)
        full[5:9, 6:11] = True
        roi_mask = crop_mask(full)
        self.assertEqual((roi_mask.x0, roi_mask.y0), (6, 5))
        self.assertEqual(roi_mask.mask.shape, (4, 5))
        restored = expand_mask(roi_mask, (20, 20))
        np.testing.assert_array_equal(restored, full)

    def test_crop_empty_mask_raises(self) -> None:
        with self.assertRaises(ValueError):
            crop_mask(np.zeros((10, 10), dtype=bool))

    def test_expand_clips_when_partly_outside_image(self) -> None:
        # A mask whose stored bounding box would spill past the image edge
        # must be clipped, not crash or wrap around.
        roi_mask = RoiMask(x0=18, y0=18, mask=np.ones((5, 5), dtype=bool))
        restored = expand_mask(roi_mask, (20, 20))
        self.assertEqual(restored.shape, (20, 20))
        self.assertTrue(restored[18:20, 18:20].all())
        self.assertEqual(int(restored.sum()), 4)  # only the 2x2 corner that overlaps

    def test_expand_fully_outside_image_is_empty_not_error(self) -> None:
        roi_mask = RoiMask(x0=100, y0=100, mask=np.ones((5, 5), dtype=bool))
        restored = expand_mask(roi_mask, (20, 20))
        self.assertFalse(restored.any())

    def test_expand_to_patch_offsets_by_patch_origin(self) -> None:
        roi_mask = RoiMask(x0=10, y0=10, mask=np.ones((3, 3), dtype=bool))
        patch = expand_mask_to_patch(roi_mask, (8, 8), (10, 10))
        self.assertTrue(patch[2:5, 2:5].all())
        self.assertEqual(int(patch.sum()), 9)


class TestMaskGeometryMatchesCircleGeometry(unittest.TestCase):
    """Numeric regression per the spec's section 38: an equivalent mask-geometry
    ROI must give the identical sample/reference means (and therefore identical
    absorbance) as the circle/annulus geometry it replaces.
    """

    def test_disk_mask_roi_matches_circle_roi(self) -> None:
        image = _synthetic_image()
        image_shape = image.shape

        circle_roi = AreaRoi(area_roi_id=1, center_x=50.0, center_y=50.0, sample_radius_px=8.0)

        yy, xx = np.indices(image_shape)
        disk = (xx - 50) ** 2 + (yy - 50) ** 2 <= 8.0**2
        mask_roi = AreaRoi(
            area_roi_id=1,
            center_x=50.0,
            center_y=50.0,
            sample_radius_px=8.0,
            sample_geometry_type="mask",
            sample_mask=crop_mask(disk),
            reference_geometry_type="none",
        )

        # Reference annulus must clear the 20x20 block's corners (max corner
        # distance from center = sqrt(10**2 + 10**2) ~= 14.14px), or ring
        # pixels near the corners would sample the block instead of the
        # background -- 25-30px stays entirely in the background.
        circle_sample_mask, circle_reference_mask = _selected_roi_masks_for_spectrum(
            image_shape, [circle_roi], (1,), 25.0, 30.0, None
        )
        mask_sample_mask, _mask_reference_mask = _selected_roi_masks_for_spectrum(
            image_shape, [mask_roi], (1,), 25.0, 30.0, None
        )

        # Both geometries describe the same disk, so the resulting boolean
        # masks (and therefore the pixels they select) must match exactly.
        np.testing.assert_array_equal(mask_sample_mask, circle_sample_mask)

        sample_mean = float(np.mean(image[mask_sample_mask]))
        reference_mean = float(np.mean(image[circle_reference_mask]))
        self.assertAlmostEqual(sample_mean, 50.0, places=6)
        self.assertAlmostEqual(reference_mean, 100.0, places=6)
        absorbance = absorbance_from_means(sample_mean, reference_mean)
        self.assertAlmostEqual(absorbance, math.log10(100.0 / 50.0), places=9)
        self.assertAlmostEqual(absorbance, 0.30102999566, places=6)

    def test_mask_reference_independent_of_sample_circle(self) -> None:
        # A "mask"-geometry reference region should compose with a normal
        # circle sample -- sample and reference geometry are independent.
        image = _synthetic_image()
        image_shape = image.shape

        reference_patch = np.zeros(image_shape, dtype=bool)
        reference_patch[0:10, 0:10] = True  # background region, value 100
        roi = AreaRoi(
            area_roi_id=1,
            center_x=50.0,
            center_y=50.0,
            sample_radius_px=8.0,
            reference_geometry_type="mask",
            reference_mask=crop_mask(reference_patch),
        )

        sample_mask, reference_mask = _selected_roi_masks_for_spectrum(image_shape, [roi], (1,), 14.0, 18.0, None)
        self.assertAlmostEqual(float(np.mean(image[sample_mask])), 50.0, places=6)
        self.assertAlmostEqual(float(np.mean(image[reference_mask])), 100.0, places=6)
        np.testing.assert_array_equal(reference_mask, reference_patch)

    def test_default_geometry_unaffected_by_new_fields(self) -> None:
        # Existing circle/annulus AreaRoi data (no geometry_type set explicitly,
        # i.e. relying on the new fields' defaults) must produce byte-identical
        # masks to what the pre-existing code path produced.
        image_shape = (60, 60)
        roi = AreaRoi(area_roi_id=1, center_x=30.0, center_y=30.0, sample_radius_px=5.0)
        self.assertEqual(roi.sample_geometry_type, "circle")
        self.assertEqual(roi.reference_geometry_type, "annulus")
        self.assertIsNone(roi.sample_mask)
        self.assertIsNone(roi.reference_mask)

        sample_mask, reference_mask = _selected_roi_masks_for_spectrum(image_shape, [roi], (1,), 7.0, 10.0, None)
        yy, xx = np.indices(image_shape)
        distance_sq = (xx - 30.0) ** 2 + (yy - 30.0) ** 2
        expected_sample = distance_sq <= 5.0**2
        expected_reference = (distance_sq <= 10.0**2) & ~(distance_sq < 7.0**2) & ~expected_sample
        np.testing.assert_array_equal(sample_mask, expected_sample)
        np.testing.assert_array_equal(reference_mask, expected_reference)


if __name__ == "__main__":
    unittest.main()
