"""Pure-logic tests for remapping existing ROIs/masks onto a new rotation/
flip/crop (`docs/image_tools_coordinate_spaces.md`'s "known gap").

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`test_lspri_rewrite_analysis_core.py`'s docstring.

No Qt, no files. `remap_point_for_geometry_change` and `remap_roi_mask` are
checked against two independent ground truths, not against each other or
against their own implementation:

- `remap_point_for_geometry_change` against `apply_spatial_preprocessing`
  itself (plant a point, render under old/new settings, compare) - see
  `test_lspri_rewrite_rotation_alignment.py` for the same method applied to
  the two-click tool's angle math.
- `remap_roi_mask` against `apply_spatial_mask` (already relied on
  elsewhere in this codebase for the ignore-mask's own raw-to-processed
  warp) - an *exact* pixel-for-pixel comparison is possible here because
  both paths do the same nearest-neighbor affine warp, just composed
  differently (raw straight to `new_settings` vs. old-processed -> raw ->
  new-processed).
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
    from lspr_imaging_app.image_tools.geometry.model import CropDefinition, GeometrySettings
    from lspr_imaging_app.image_tools.geometry.transform import (
        apply_spatial_mask,
        apply_spatial_preprocessing,
        remap_point_for_geometry_change,
    )
    from lspr_imaging_app.roi.model import AreaRoi
    from lspr_imaging_app.roi.rasterize import crop_mask, expand_mask, remap_roi_mask
    from lspr_imaging_app.roi.toolbox import _remap_roi_shape
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


def _blob(shape: tuple[int, int], cx: float, cy: float, sigma: float = 1.5) -> np.ndarray:
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    return np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma**2)).astype(np.float32)


def _centroid(img: np.ndarray) -> tuple[float, float]:
    ys, xs = np.nonzero(img > np.nanmax(img) * 0.5)  # rotation corners are NaN: never above
    return float(xs.mean()), float(ys.mean())


class RemapPointAgainstRealTransformTest(unittest.TestCase):
    """Cross-checks `remap_point_for_geometry_change` against the actual
    image pipeline, for every case the two-click tool itself needs."""

    RAW_SHAPE = (120, 160)
    RAW_POINT = (63.0, 40.0)

    def _check(self, old_settings: GeometrySettings, new_settings: GeometrySettings) -> None:
        raw_img = _blob(self.RAW_SHAPE, *self.RAW_POINT)
        old_processed = apply_spatial_preprocessing(raw_img, old_settings)
        new_processed = apply_spatial_preprocessing(raw_img, new_settings)
        old_xy = _centroid(old_processed)
        expected_new_xy = _centroid(new_processed)

        computed_new_xy = remap_point_for_geometry_change(old_xy, self.RAW_SHAPE, old_settings, new_settings)

        err = np.hypot(computed_new_xy[0] - expected_new_xy[0], computed_new_xy[1] - expected_new_xy[1])
        # Resampling/centroid-measurement noise, not the transform's own
        # error - the pure round-trip check below has no such noise and is
        # accurate to float precision.
        self.assertLess(err, 0.3, msg=f"old={old_settings} new={new_settings}")

    def test_identity_is_a_no_op(self) -> None:
        self._check(GeometrySettings(), GeometrySettings())

    def test_pure_rotation(self) -> None:
        self._check(GeometrySettings(), GeometrySettings(rotation_angle_deg=12.0))
        self._check(GeometrySettings(rotation_angle_deg=-7.0), GeometrySettings())

    def test_refining_an_existing_rotation(self) -> None:
        self._check(GeometrySettings(rotation_angle_deg=5.0), GeometrySettings(rotation_angle_deg=8.0))

    def test_flips(self) -> None:
        self._check(GeometrySettings(), GeometrySettings(flip_horizontal=True))
        self._check(GeometrySettings(), GeometrySettings(flip_vertical=True))
        self._check(GeometrySettings(), GeometrySettings(flip_horizontal=True, flip_vertical=True))
        self._check(
            GeometrySettings(rotation_angle_deg=15.0),
            GeometrySettings(rotation_angle_deg=15.0, flip_horizontal=True),
        )

    def test_crop_change_alone(self) -> None:
        self._check(
            GeometrySettings(crop=CropDefinition(x=5, y=8, width=90, height=70, enabled=True)),
            GeometrySettings(crop=CropDefinition(x=12, y=3, width=100, height=80, enabled=True)),
        )

    def test_rotation_flip_and_crop_all_change_together(self) -> None:
        self._check(
            GeometrySettings(
                rotation_angle_deg=-6.0, flip_horizontal=True,
                crop=CropDefinition(x=3, y=2, width=100, height=90, enabled=True),
            ),
            GeometrySettings(
                rotation_angle_deg=11.0, flip_vertical=True,
                crop=CropDefinition(x=8, y=15, width=90, height=80, enabled=True),
            ),
        )


class RemapPointRoundTripTest(unittest.TestCase):
    """No image, no interpolation: old point -> raw -> new -> raw must
    return the exact original point, to float precision."""

    def test_round_trip_matches_to_float_precision(self) -> None:
        raw_shape = (120, 160)
        old = GeometrySettings(
            rotation_angle_deg=13.7, flip_horizontal=True,
            crop=CropDefinition(x=4, y=9, width=80, height=60, enabled=True),
        )
        new = GeometrySettings(
            rotation_angle_deg=-22.3, flip_vertical=True,
            crop=CropDefinition(x=1, y=2, width=90, height=70, enabled=True),
        )
        point = (30.0, 50.0)
        forward = remap_point_for_geometry_change(point, raw_shape, old, new)
        back = remap_point_for_geometry_change(forward, raw_shape, new, old)
        self.assertAlmostEqual(back[0], point[0], places=9)
        self.assertAlmostEqual(back[1], point[1], places=9)


class RemapRoiMaskAgainstGroundTruthTest(unittest.TestCase):
    def test_translation_only_shifts_by_the_crop_delta(self) -> None:
        raw_shape = (200, 200)
        old_settings = GeometrySettings(crop=CropDefinition(x=20, y=20, width=150, height=150, enabled=True))
        new_settings = GeometrySettings(crop=CropDefinition(x=25, y=15, width=150, height=150, enabled=True))
        full = np.zeros(raw_shape, dtype=bool)
        full[60:70, 80:90] = True
        roi_mask = crop_mask(full)

        remapped = remap_roi_mask(roi_mask, raw_shape, old_settings, new_settings)

        self.assertIsNotNone(remapped)
        self.assertEqual((remapped.x0, remapped.y0), (75, 65))  # -5 in x (crop x0 20->25), +5 in y (20->15)
        np.testing.assert_array_equal(remapped.mask, roi_mask.mask)

    def test_rotation_matches_apply_spatial_mask_exactly(self) -> None:
        """Independent ground truth: an identity `old_settings` means
        old-processed space *is* raw space, so remapping to a rotated
        `new_settings` must equal warping the same raw mask with
        `apply_spatial_mask(raw_mask, new_settings)` directly."""
        raw_shape = (200, 220)
        full = np.zeros(raw_shape, dtype=bool)
        full[80:100, 90:130] = True
        new_settings = GeometrySettings(rotation_angle_deg=17.0)

        roi_mask = crop_mask(full)
        remapped = remap_roi_mask(roi_mask, raw_shape, GeometrySettings(), new_settings)
        ground_truth = apply_spatial_mask(full, new_settings)

        self.assertIsNotNone(remapped)
        expanded = expand_mask(remapped, ground_truth.shape)
        np.testing.assert_array_equal(expanded, ground_truth)

    def test_an_extreme_crop_offset_still_returns_a_valid_unclipped_mask(self) -> None:
        """A crop change never bounds/clips this warp's own math (see
        `remap_roi_mask`'s docstring) - even a crop origin far outside the
        raw image (`combined_transform_for_box` clamps *that* internally,
        independent of this function) must still return a correctly-
        positioned, un-clipped mask, not `None`. Checked against
        `remap_point_for_geometry_change` (independently verified above)
        applied to the mask's own corner, rather than a hand-computed
        expected shift - the exact clamped shift is `combined_transform_
        for_box`'s own existing, unrelated behavior, not something this
        test should re-derive by hand."""
        raw_shape = (40, 40)
        full = np.zeros(raw_shape, dtype=bool)
        full[0:3, 0:3] = True
        roi_mask = crop_mask(full)
        old_settings = GeometrySettings()
        new_settings = GeometrySettings(crop=CropDefinition(x=2000, y=2000, width=10, height=10, enabled=True))

        remapped = remap_roi_mask(roi_mask, raw_shape, old_settings, new_settings)

        self.assertIsNotNone(remapped)
        expected_x0, expected_y0 = remap_point_for_geometry_change(
            (float(roi_mask.x0), float(roi_mask.y0)), raw_shape, old_settings, new_settings
        )
        self.assertAlmostEqual(remapped.x0, expected_x0, delta=1.0)
        self.assertAlmostEqual(remapped.y0, expected_y0, delta=1.0)
        np.testing.assert_array_equal(remapped.mask, roi_mask.mask)

    def test_pixel_count_is_preserved_up_to_small_boundary_rounding(self) -> None:
        """`remap_roi_mask` never observed to lose more than boundary-rounding
        noise across randomized rotate/flip/crop combinations - the
        empirical basis for that function's "near-unreachable None" claim."""
        rng = np.random.default_rng(0)
        for _ in range(50):
            raw_shape = (int(rng.integers(30, 80)), int(rng.integers(30, 80)))
            full = np.zeros(raw_shape, dtype=bool)
            height, width = raw_shape
            y0, x0 = int(rng.integers(0, height - 5)), int(rng.integers(0, width - 5))
            full[y0 : y0 + 3, x0 : x0 + 3] = True
            roi_mask = crop_mask(full)
            old_settings = GeometrySettings(rotation_angle_deg=float(rng.uniform(-45, 45)))
            new_settings = GeometrySettings(
                rotation_angle_deg=float(rng.uniform(-45, 45)),
                flip_horizontal=bool(rng.integers(0, 2)),
                flip_vertical=bool(rng.integers(0, 2)),
                crop=CropDefinition(
                    x=int(rng.integers(-50, 50)), y=int(rng.integers(-50, 50)), width=10, height=10, enabled=True
                ),
            )
            remapped = remap_roi_mask(roi_mask, raw_shape, old_settings, new_settings)
            self.assertIsNotNone(remapped)
            # +-1 pixel of nearest-neighbor boundary rounding, never total loss.
            self.assertGreaterEqual(remapped.mask.sum(), roi_mask.mask.sum() - 2)

    def test_a_single_pixel_mask_never_warps_to_nothing(self) -> None:
        """The smallest possible mask is the hardest case for "did it warp to
        zero area" - checked across a full sweep of rotation angles."""
        raw_shape = (50, 50)
        full = np.zeros(raw_shape, dtype=bool)
        full[25, 25] = True
        roi_mask = crop_mask(full)
        for angle in np.linspace(-89.0, 89.0, 37):
            remapped = remap_roi_mask(roi_mask, raw_shape, GeometrySettings(), GeometrySettings(rotation_angle_deg=float(angle)))
            self.assertIsNotNone(remapped, msg=f"angle={angle}")

    # The "warped to nothing" plumbing (left at its old value, reported, not
    # silently replaced with an empty mask) is exercised directly in
    # `RemapRoiShapeDispatchTest` below via a stand-in callback, since
    # `remap_roi_mask` itself could not be made to return `None` through any
    # real geometry combination tried above.


class RemapRoiShapeDispatchTest(unittest.TestCase):
    """`RoiToolbox._remap_roi_shape` - the per-geometry-type dispatch,
    tested directly since it is plain logic over a dataclass (no Qt)."""

    def _roi(self, **overrides: object) -> AreaRoi:
        defaults = dict(area_roi_id=1, center_x=0.0, center_y=0.0, sample_radius_px=5.0)
        defaults.update(overrides)
        return AreaRoi(**defaults)

    def test_circle_and_annulus_pass_through_unchanged(self) -> None:
        roi = self._roi(sample_geometry_type="circle", reference_geometry_type="annulus")
        mask, lost, unsupported = _remap_roi_shape(roi, "sample", lambda m: None)
        self.assertIsNone(mask)
        self.assertFalse(lost)
        self.assertFalse(unsupported)

    def test_mask_geometry_uses_the_callback(self) -> None:
        full = np.zeros((20, 20), dtype=bool)
        full[5:8, 5:8] = True
        original = crop_mask(full)
        moved = crop_mask(np.roll(full, 2, axis=0))
        roi = self._roi(sample_geometry_type="mask", sample_mask=original)

        mask, lost, unsupported = _remap_roi_shape(roi, "sample", lambda m: moved)

        self.assertIs(mask, moved)
        self.assertFalse(lost)
        self.assertFalse(unsupported)

    def test_mask_geometry_warped_to_nothing_is_reported_and_left_unchanged(self) -> None:
        full = np.zeros((20, 20), dtype=bool)
        full[5:8, 5:8] = True
        original = crop_mask(full)
        roi = self._roi(sample_geometry_type="mask", sample_mask=original)

        mask, lost, unsupported = _remap_roi_shape(roi, "sample", lambda m: None)

        self.assertIs(mask, original)  # unchanged, not replaced with an empty mask
        self.assertTrue(lost)
        self.assertFalse(unsupported)

    def test_unbuilt_shape_types_are_left_alone_and_flagged(self) -> None:
        roi = self._roi(sample_geometry_type="rectangle")
        mask, lost, unsupported = _remap_roi_shape(roi, "sample", lambda m: None)
        self.assertIsNone(mask)  # unchanged (was None)
        self.assertFalse(lost)
        self.assertTrue(unsupported)


if __name__ == "__main__":
    unittest.main()
