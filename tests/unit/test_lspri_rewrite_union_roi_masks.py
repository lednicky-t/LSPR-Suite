"""`union_roi_masks` must give exactly what OR-ing `rasterize_sample` /
`rasterize_reference` over the ROIs gives - it only changes how much memory
and time that takes (the Histogram panel's per-ROI full-plane form measured
4.2 s for 1000 ROIs at 2048 x 2048, 2026-10-07). Boolean masks, so the
comparison is exact equality, not a tolerance."""

from __future__ import annotations

import sys
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.image_tools.chromatic.affine import identity_affine_matrix
from lspr_imaging_app.roi.model import AreaRoi
from lspr_imaging_app.roi.rasterize import crop_mask, rasterize_reference, rasterize_sample, union_roi_masks

_SHAPE = (300, 400)
_AFFINE = np.array([[1.0007, 0.0005, 2.3], [-0.0004, 0.9995, -1.6]])
_DEFAULT_RING = {"default_inner_diameter_px": 28.0, "default_outer_diameter_px": 36.0}


def _reference_union(rois, affine):
    sample = np.zeros(_SHAPE, dtype=bool)
    reference = np.zeros(_SHAPE, dtype=bool)
    for roi in rois:
        sample |= rasterize_sample(roi, _SHAPE, affine)
        reference |= rasterize_reference(roi, _SHAPE, affine, **_DEFAULT_RING)
    return sample, reference


def _mixed_rois() -> list[AreaRoi]:
    blob = np.zeros(_SHAPE, dtype=bool)
    blob[100:112, 200:230] = True
    return [
        AreaRoi(area_roi_id=1, center_x=50.0, center_y=60.0, sample_diameter_px=20.0),
        AreaRoi(area_roi_id=2, center_x=52.0, center_y=64.0, sample_diameter_px=14.0),  # overlaps ROI 1
        AreaRoi(area_roi_id=3, center_x=2.0, center_y=3.0, sample_diameter_px=24.0),  # clipped by the top-left corner
        AreaRoi(area_roi_id=4, center_x=398.0, center_y=297.0, sample_diameter_px=24.0),  # bottom-right corner
        AreaRoi(area_roi_id=5, center_x=-40.0, center_y=500.0, sample_diameter_px=20.0),  # entirely outside
        AreaRoi(
            area_roi_id=6, center_x=200.0, center_y=150.0, sample_diameter_px=18.0,
            reference_inner_diameter_px=22.0, reference_outer_diameter_px=40.0,  # its own ring
        ),
        AreaRoi(area_roi_id=7, center_x=300.0, center_y=80.0, sample_diameter_px=16.0, reference_geometry_type="none"),
        AreaRoi(
            area_roi_id=8, center_x=0.0, center_y=0.0, sample_diameter_px=10.0,
            sample_geometry_type="mask", sample_mask=crop_mask(blob),
            reference_geometry_type="mask", reference_mask=crop_mask(blob),
        ),
    ]


class UnionRoiMasksTest(unittest.TestCase):
    def test_equals_the_per_roi_union_with_the_identity_affine(self) -> None:
        rois = _mixed_rois()
        expected = _reference_union(rois, identity_affine_matrix())
        got = union_roi_masks(rois, _SHAPE, identity_affine_matrix(), **_DEFAULT_RING)
        np.testing.assert_array_equal(got[0], expected[0])
        np.testing.assert_array_equal(got[1], expected[1])

    def test_equals_the_per_roi_union_with_a_chromatic_affine(self) -> None:
        rois = _mixed_rois()
        expected = _reference_union(rois, _AFFINE)
        got = union_roi_masks(rois, _SHAPE, _AFFINE, **_DEFAULT_RING)
        np.testing.assert_array_equal(got[0], expected[0])
        np.testing.assert_array_equal(got[1], expected[1])

    def test_random_rois_match(self) -> None:
        rng = np.random.default_rng(7)
        rois = [
            AreaRoi(
                area_roi_id=i + 1,
                center_x=float(rng.uniform(-10, 410)),
                center_y=float(rng.uniform(-10, 310)),
                sample_diameter_px=float(rng.uniform(4, 30)),
            )
            for i in range(60)
        ]
        expected = _reference_union(rois, _AFFINE)
        got = union_roi_masks(rois, _SHAPE, _AFFINE, **_DEFAULT_RING)
        np.testing.assert_array_equal(got[0], expected[0])
        np.testing.assert_array_equal(got[1], expected[1])

    def test_no_rois_gives_two_empty_planes(self) -> None:
        sample, reference = union_roi_masks([], _SHAPE, identity_affine_matrix())
        self.assertEqual(sample.shape, _SHAPE)
        self.assertFalse(sample.any() or reference.any())

    def test_a_zero_sized_ring_adds_nothing_to_the_reference(self) -> None:
        roi = AreaRoi(area_roi_id=1, center_x=50.0, center_y=50.0, sample_diameter_px=10.0)
        sample, reference = union_roi_masks([roi], _SHAPE, identity_affine_matrix())  # defaults 0 / 0
        self.assertTrue(sample.any())
        self.assertFalse(reference.any())


if __name__ == "__main__":
    unittest.main()
