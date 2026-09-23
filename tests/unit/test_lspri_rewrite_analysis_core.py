"""Pure-logic tests for the LSPRimaging Evaluation rewrite's analysis core.

**These only run when the `apps/LSPRi/eva` submodule is checked out on its
`rewrite` branch.** The rewrite is deliberately branch-local - the umbrella
repo's tracked submodule pointer still references the stable `develop`
commit (see `apps/LSPRi/eva/docs/rewrite_build_log_2026-09.md`) - so on a
stable checkout these modules simply don't exist and the whole file skips.
They live here rather than inside the submodule so there is one test
location for the Suite and nothing has to move if the rewrite eventually
replaces the stable app.

No Qt, no files: everything here is a pure function or a plain dataclass,
which is what keeps it in `tests/unit`. The parts that need a real
`QApplication`, real TIFFs or a real HDF5 store are in
`tests/integration/test_lspri_rewrite_analysis_engine.py`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.provenance import (
        DEFAULT_REFERENCE_EXCLUSION_MODE,
        REFERENCE_EXCLUSION_MODES,
        mask_scope_tag,
    )
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools.geometry.model import CropDefinition, GeometrySettings
    from lspr_imaging_app.image_tools.geometry.transform import apply_spatial_mask
    from lspr_imaging_app.image_tools.preprocess import resolve_external_mask
    from lspr_imaging_app.roi.model import AreaRoi
    from lspr_imaging_app.roi.rasterize import effective_reference_radii
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402


def _dataset_with_a_missing_wavelength() -> ImageDataset:
    """Two cubes, but cube 1 never recorded 550 nm - what a partially failed
    acquisition actually leaves behind."""
    records = [
        ImageRecord(key=ImageKey(wavelength_nm=wl, spectral_cube_index=cube), path=Path(f"c{cube}_w{wl}.tif"))
        for cube, wl in ((0, 500.0), (0, 550.0), (0, 600.0), (1, 500.0), (1, 600.0))
    ]
    return ImageDataset(folder=Path("."), records=records)


class PerCubeWavelengthsTest(unittest.TestCase):
    """`wavelengths_nm` is the union across every cube; anything that then
    loads a plane needs the per-cube list instead, or it asks for a frame
    that was never recorded."""

    def test_global_wavelengths_are_the_union(self) -> None:
        self.assertEqual(_dataset_with_a_missing_wavelength().wavelengths_nm, [500.0, 550.0, 600.0])

    def test_per_cube_wavelengths_exclude_what_that_cube_lacks(self) -> None:
        dataset = _dataset_with_a_missing_wavelength()
        self.assertEqual(dataset.wavelengths_for_cube(0), [500.0, 550.0, 600.0])
        self.assertEqual(dataset.wavelengths_for_cube(1), [500.0, 600.0])

    def test_unknown_cube_is_empty_not_an_error(self) -> None:
        self.assertEqual(_dataset_with_a_missing_wavelength().wavelengths_for_cube(99), [])


class MaskScopeTagTest(unittest.TestCase):
    """`MaskModule` says persistent/individual; provenance filenames say
    persi/indiv (both 5 characters, so a directory listing stays aligned).
    One translation point, and it must fail loudly rather than write a
    mask under a wrong-length tag."""

    def test_translates_both_scopes(self) -> None:
        self.assertEqual(mask_scope_tag("persistent"), "persi")
        self.assertEqual(mask_scope_tag("individual"), "indiv")

    def test_rejects_an_unknown_scope(self) -> None:
        with self.assertRaises(ValueError):
            mask_scope_tag("something-else")


class ReferenceExclusionDefaultTest(unittest.TestCase):
    def test_default_excludes_sample_apertures(self) -> None:
        """Maintainer's 2026-09-23 call. `"none"` matches the stable app's
        *single-ROI* behavior only; with two ROIs close enough that one's
        sample circle falls inside the other's reference ring, it leaves a
        measurably biased reference value."""
        self.assertEqual(DEFAULT_REFERENCE_EXCLUSION_MODE, "exclude_all_sample_rois")
        self.assertIn(DEFAULT_REFERENCE_EXCLUSION_MODE, REFERENCE_EXCLUSION_MODES)


class ExternalMaskCoordinateSpaceTest(unittest.TestCase):
    """The regression guard for the 2026-09-23 coordinate-space fix.

    An ignore mask is authored in **raw** image space, but the chromatic
    affine is expressed in **processed** space. So the mask must be
    crop/rotate/flipped first and warped second. Doing it the other way
    round doesn't crash - it just silently excludes the wrong pixels,
    which is why this is pinned by a test rather than left to a comment.
    """

    def setUp(self) -> None:
        self.geometry = GeometrySettings(
            image_tools_enabled=True,
            rotation_angle_deg=12.0,
            crop=CropDefinition(enabled=True, x=8, y=6, width=48, height=40),
        )
        self.raw_mask = np.zeros((64, 80), dtype=bool)
        self.raw_mask[10:20, 12:24] = True

    def test_none_stays_none(self) -> None:
        self.assertIsNone(resolve_external_mask(None, self.geometry))

    def test_result_is_in_processed_space(self) -> None:
        resolved = resolve_external_mask(self.raw_mask, self.geometry)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.shape, (40, 48))
        self.assertNotEqual(resolved.shape, self.raw_mask.shape)

    def test_without_a_warp_it_is_exactly_the_spatial_transform(self) -> None:
        self.assertTrue(
            np.array_equal(
                resolve_external_mask(self.raw_mask, self.geometry),
                apply_spatial_mask(self.raw_mask, self.geometry),
            )
        )

    def test_warp_is_applied_after_the_spatial_transform(self) -> None:
        """If the warp ran first, in raw space, the result would still be
        64x80 - it is the processed shape that proves the order."""
        shift = np.array([[1.0, 0.0, 5.0], [0.0, 1.0, 3.0]])
        warped = resolve_external_mask(self.raw_mask, self.geometry, shift)
        unwarped = resolve_external_mask(self.raw_mask, self.geometry)
        self.assertEqual(warped.shape, (40, 48))
        self.assertFalse(np.array_equal(warped, unwarped))
        # A pure translation well inside the frame preserves area, so a big
        # area change would mean the mask was warped somewhere it shouldn't be.
        self.assertLessEqual(abs(int(warped.sum()) - int(unwarped.sum())), int(0.10 * unwarped.sum()))


class EffectiveReferenceRadiiTest(unittest.TestCase):
    """The Image panel draws the reference ring it is about to measure, so
    it calls this rather than re-deriving the override rule. Pinned so the
    drawn ring and the measured ring cannot drift apart."""

    def test_falls_back_to_the_shared_defaults(self) -> None:
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
        self.assertEqual(effective_reference_radii(roi, 14.0, 18.0), (14.0, 18.0))

    def test_per_roi_diameter_overrides_win_and_halve(self) -> None:
        roi = AreaRoi(
            area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0,
            reference_inner_diameter_px=40.0, reference_outer_diameter_px=60.0,
        )
        self.assertEqual(effective_reference_radii(roi, 14.0, 18.0), (20.0, 30.0))

    def test_one_override_does_not_drag_the_other(self) -> None:
        roi = AreaRoi(
            area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0,
            reference_outer_diameter_px=60.0,
        )
        self.assertEqual(effective_reference_radii(roi, 14.0, 18.0), (14.0, 30.0))


if __name__ == "__main__":
    unittest.main()
