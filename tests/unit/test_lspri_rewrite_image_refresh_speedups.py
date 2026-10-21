"""Pins for the 2026-10-11 Image-panel refresh speed-ups
(`apps/LSPRi/eva/docs/image_refresh_performance_analysis_2026-10-11.md`).

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`test_lspri_rewrite_analysis_core.py`'s docstring.

No Qt application, no files.

1. `TestOpenCvGeometryMatchesScipy`: the OpenCV rotate/flip/crop must agree
   with the scipy reference (`_apply_spatial_transform`) in shape and in
   which pixels are NaN. Values: Lanczos4 since 2026-10-20, pinned against a
   quintic spline and by how much pixel noise it keeps (`_assert_lanczos_quality`).
2. `TestResolvedMaskCache`: `resolve_external_mask` hands back the same
   read-only object for the same inputs and recomputes when any input changes.
3. `TestPrefetchOrder` / `TestRendererPrefetch`: read-ahead order, and that
   the reader reads planes only after the quiet period and drops a
   replaced list (one reader thread here; several, and the render thread
   not waiting behind them: `test_lspri_rewrite_renderer_readers.py`).
4. `TestRendererFlattenCaches` (2026-10-21, Play at 12-15 instead of 20-25 frames/s): the renderer reuses the
   ROI exclusion mask and the valid-mask weights between frames. Pins that the image is identical to the uncached
   `apply_preprocessing` result, that the ROI mask is rebuilt exactly when an ROI or the image shape changes,
   and that nothing is cached with flattening off.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from dataclasses import replace
from unittest import mock

import numpy as np
from scipy import ndimage

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.chromatic.warp import warp_boolean_mask_affine
    from lspr_imaging_app.image_tools.geometry import transform as T
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.geometry.model import CropDefinition, GeometrySettings
    from lspr_imaging_app.image_tools.preprocess import apply_preprocessing, resolve_external_mask
    from lspr_imaging_app.panels.image import render as render_module
    from lspr_imaging_app.panels.image.prefetch import prefetch_order
    from lspr_imaging_app.panels.image.render import ImageRenderer, RenderRequest
    from lspr_imaging_app.roi.model import AreaRoi
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_SHAPE = (300, 420)
_CROP = CropDefinition(enabled=True, x=30, y=20, width=250, height=180)
_CASES = {
    "rot3": GeometrySettings(rotation_angle_deg=3.0),
    "rot-17.5 flipH": GeometrySettings(rotation_angle_deg=-17.5, flip_horizontal=True),
    "rot90.3 flipV crop": GeometrySettings(rotation_angle_deg=90.3, flip_vertical=True, crop=_CROP),
    "rot3 crop": GeometrySettings(rotation_angle_deg=3.0, crop=_CROP),
}


def _frame() -> np.ndarray:
    return (np.random.default_rng(3).random(_SHAPE) * 4000.0 + 100.0).astype(np.float32)


class TestOpenCvGeometryMatchesScipy(unittest.TestCase):
    def test_image_shape_nan_set_and_values(self) -> None:
        frame = _frame()
        for name, settings in _CASES.items():
            for skip_crop in (False, True):
                with self.subTest(case=name, skip_crop=skip_crop):
                    reference = T._apply_spatial_transform(
                        frame, settings, order=1, mode="constant", cval=np.nan, skip_crop=skip_crop
                    )
                    actual = T.apply_spatial_preprocessing(frame, settings, skip_crop=skip_crop)
                    self.assertEqual(actual.shape, reference.shape)
                    np.testing.assert_array_equal(np.isnan(actual), np.isnan(reference))
                    self._assert_lanczos_quality(frame, settings, skip_crop, actual)

    def test_fast_rotation_is_the_bilinear_one_with_the_same_nan_pixels(self) -> None:
        """`fast=True` (slideshow preview): the bilinear warp of before 2026-10-20, within OpenCV's 1/32 px step of
        scipy's bilinear (worst case 1.95 counts on this frame, measured 2026-10-11), same NaN pixels."""
        frame = _frame()
        for name, settings in _CASES.items():
            with self.subTest(case=name):
                reference = T._apply_spatial_transform(frame, settings, order=1, mode="constant", cval=np.nan)
                fast = T.apply_spatial_preprocessing(frame, settings, fast=True)
                np.testing.assert_array_equal(np.isnan(fast), np.isnan(reference))
                self.assertLess(float(np.nanmax(np.abs(fast - reference))), 3.0)

    def _assert_lanczos_quality(self, frame, settings, skip_crop, actual) -> None:
        """Lanczos4 since 2026-10-20 (`_cv2_affine`): close to a quintic spline and keeps the pixel noise. Measured on
        this white-noise frame: RMS 44-46 counts from the quintic spline (bilinear: ~400) and 90-92 % of the noise kept
        (bilinear: 63-67 %); the bounds below fail for bilinear."""
        effective = settings if not skip_crop else GeometrySettings(
            rotation_angle_deg=settings.rotation_angle_deg, flip_horizontal=settings.flip_horizontal,
            flip_vertical=settings.flip_vertical,
        )
        matrix, offset, shape = T._combined_export_transform(frame.shape, effective)
        quintic = ndimage.affine_transform(frame.astype(np.float64), matrix, offset, output_shape=shape, order=5,
                                           mode="nearest")
        inner = np.zeros(shape, dtype=bool)
        inner[6:-6, 6:-6] = True
        inner &= np.isfinite(actual)
        self.assertLess(float(np.sqrt(np.mean((actual - quintic)[inner] ** 2))), 80.0)
        self.assertGreater(float(np.std(actual[inner]) / np.std(frame)), 0.85)

    def test_output_shape_matches_the_real_transform_at_many_angles(self) -> None:
        for angle in np.linspace(-180.0, 180.0, 37) + 0.37:
            for flip in (False, True):
                settings = GeometrySettings(rotation_angle_deg=float(angle), flip_horizontal=flip)
                reference = T._apply_spatial_transform(
                    np.zeros(_SHAPE, np.uint8), settings, order=0, mode="constant", cval=0.0
                )
                self.assertEqual(T.spatial_output_shape(_SHAPE, settings), reference.shape, msg=f"{angle=}")

    def test_coordinate_maps_match_scipy(self) -> None:
        yy, xx = np.indices(_SHAPE, dtype=np.float32)
        for name, settings in _CASES.items():
            with self.subTest(case=name):
                x_map, y_map = T.spatial_coordinate_maps(_SHAPE, settings)
                ref_x = T._apply_spatial_transform(xx, settings, order=1, mode="nearest", cval=0.0)
                ref_y = T._apply_spatial_transform(yy, settings, order=1, mode="nearest", cval=0.0)
                self.assertEqual(x_map.shape, ref_x.shape)
                np.testing.assert_allclose(x_map, ref_x, atol=1e-3)
                np.testing.assert_allclose(y_map, ref_y, atol=1e-3)

    def test_mask_differs_from_scipy_only_at_the_rotated_edge(self) -> None:
        mask = np.zeros(_SHAPE, bool)
        mask[40:120, 50:200] = True
        mask[150:170, 300:330] = True
        for name, settings in _CASES.items():
            with self.subTest(case=name):
                reference = (
                    T._apply_spatial_transform(
                        mask.astype(np.float32), settings, order=0, mode="constant", cval=0.0
                    )
                    >= 0.5
                )
                actual = T.apply_spatial_mask(mask, settings)
                self.assertEqual(actual.shape, reference.shape)
                self.assertEqual(actual.dtype, bool)
                # Interior rectangles: only boundary pixels may move, by at most a pixel.
                self.assertLessEqual(int(np.count_nonzero(actual != reference)), 0.02 * int(reference.sum()))

    def test_no_rotation_path_is_untouched(self) -> None:
        frame = _frame()
        settings = GeometrySettings(flip_horizontal=True, crop=_CROP)
        reference = T._apply_spatial_transform(frame, settings, order=1, mode="constant", cval=np.nan)
        np.testing.assert_array_equal(T.apply_spatial_preprocessing(frame, settings), reference)

    def test_chromatic_mask_warp_is_nearest_neighbour_and_boolean(self) -> None:
        mask = np.zeros(_SHAPE, bool)
        mask[60:140, 80:220] = True
        translate = np.array([[1.0, 0.0, 5.0], [0.0, 1.0, -3.0]])
        warped = warp_boolean_mask_affine(mask, translate)
        self.assertEqual(warped.dtype, bool)
        expected = np.zeros(_SHAPE, bool)
        expected[57:137, 85:225] = True  # moved +5 px in x, -3 px in y, exactly
        np.testing.assert_array_equal(warped, expected)


class TestResolvedMaskCache(unittest.TestCase):
    def test_same_inputs_return_the_same_read_only_object(self) -> None:
        mask = np.zeros(_SHAPE, bool)
        mask[10:50, 10:80] = True
        settings = GeometrySettings(rotation_angle_deg=2.0)
        first = resolve_external_mask(mask, settings, None)
        second = resolve_external_mask(mask.copy(), GeometrySettings(rotation_angle_deg=2.0), None)
        self.assertIs(first, second)
        self.assertFalse(first.flags.writeable)

    def test_any_changed_input_recomputes(self) -> None:
        mask = np.zeros(_SHAPE, bool)
        mask[10:50, 10:80] = True
        base = resolve_external_mask(mask, GeometrySettings(rotation_angle_deg=2.0), None)
        edited = mask.copy()
        edited[100, 100] = True
        self.assertIsNot(resolve_external_mask(edited, GeometrySettings(rotation_angle_deg=2.0), None), base)
        self.assertIsNot(resolve_external_mask(mask, GeometrySettings(rotation_angle_deg=4.0), None), base)
        shifted = np.array([[1.0, 0.0, 3.0], [0.0, 1.0, 0.0]])
        self.assertIsNot(resolve_external_mask(mask, GeometrySettings(rotation_angle_deg=2.0), shifted), base)

    def test_display_only_geometry_fields_do_not_invalidate(self) -> None:
        mask = np.zeros(_SHAPE, bool)
        mask[10:50, 10:80] = True
        first = resolve_external_mask(mask, GeometrySettings(rotation_angle_deg=2.0), None)
        second = resolve_external_mask(
            mask, GeometrySettings(rotation_angle_deg=2.0, scale_bar_visible=True, display_units="um"), None
        )
        self.assertIs(first, second)

    def test_none_stays_none(self) -> None:
        self.assertIsNone(resolve_external_mask(None, GeometrySettings(), None))


class TestPrefetchOrder(unittest.TestCase):
    def test_other_wavelengths_nearest_first_then_neighbouring_cubes(self) -> None:
        keys = prefetch_order(5, 500.0, [3, 4, 5, 6, 7, 8], [450.0, 500.0, 520.0, 600.0])
        self.assertEqual(keys[:3], [(5, 520.0), (5, 450.0), (5, 600.0)])
        self.assertEqual(keys[3:], [(6, 500.0), (4, 500.0), (7, 500.0), (3, 500.0)])
        self.assertNotIn((5, 500.0), keys)

    def test_edges_of_the_cube_list_and_unknown_cube(self) -> None:
        self.assertEqual(prefetch_order(3, 500.0, [3, 4], [500.0], neighbour_cubes=2), [(4, 500.0)])
        self.assertEqual(prefetch_order(99, 500.0, [3, 4], [500.0, 510.0]), [(99, 510.0)])


class TestRendererPrefetch(unittest.TestCase):
    def setUp(self) -> None:
        self.loaded: list[tuple[int, float]] = []
        self.lock = threading.Lock()

        def load_plane(cube: int, wavelength: float) -> np.ndarray:
            with self.lock:
                self.loaded.append((cube, wavelength))
            if cube == 99:
                raise KeyError("missing plane")
            return np.zeros((4, 4), np.float32)

        self.renderer = ImageRenderer(load_plane, readers=1)  # one reader: the order of reads is what these pin
        self.renderer.PREFETCH_QUIET_SECONDS = 0.15

    def tearDown(self) -> None:
        self.renderer.stop()

    def _wait_for(self, count: int, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if len(self.loaded) >= count:
                    return
            time.sleep(0.01)

    def test_reads_in_order_after_the_quiet_period_and_survives_a_missing_plane(self) -> None:
        started = time.monotonic()
        self.renderer.prefetch([(1, 500.0), (99, 500.0), (2, 500.0)])
        time.sleep(0.05)
        with self.lock:
            self.assertEqual(self.loaded, [])  # still inside the quiet period
        self._wait_for(3)
        with self.lock:
            self.assertEqual(self.loaded, [(1, 500.0), (99, 500.0), (2, 500.0)])
        self.assertGreaterEqual(time.monotonic() - started, 0.15)

    def test_a_new_list_replaces_the_old_one(self) -> None:
        self.renderer.prefetch([(1, 500.0), (2, 500.0)])
        time.sleep(0.05)
        self.renderer.prefetch([(7, 500.0)])
        self._wait_for(1)
        time.sleep(0.4)
        with self.lock:
            self.assertEqual(self.loaded, [(7, 500.0)])

    def test_clear_prefetch_drops_the_list(self) -> None:
        self.renderer.prefetch([(1, 500.0)])
        self.renderer.clear_prefetch()
        time.sleep(0.4)
        with self.lock:
            self.assertEqual(self.loaded, [])


class TestRendererFlattenCaches(unittest.TestCase):
    """`ImageRenderer._frame_caches`: the ROI exclusion mask and the valid-mask weights are worked out once, not per frame."""

    def setUp(self) -> None:
        self.raw = _frame()
        self.renderer = ImageRenderer(lambda cube, wavelength: self.raw)
        self.addCleanup(self.renderer.stop)
        self.rois = tuple(
            AreaRoi(area_roi_id=i, center_x=60.0 + 70.0 * i, center_y=90.0 + 40.0 * (i % 3), sample_diameter_px=16.0)
            for i in range(5)
        )
        self.background = BackgroundSettings(
            flatten_background_enabled=True, flatten_background_sigma_px=24.0, flatten_background_binning=4,
            flatten_background_exclude_area_rois=True,
        )

    def _request(self, **changes) -> "RenderRequest":
        fields = dict(
            cube_index=0, wavelength_nm=610.0, geometry=_CASES["rot3 crop"], background=self.background,
            authored_mask=None, mask_warp_affine=None, rois=self.rois, detection=None, serial=1,
            fast_rotation=True,
        )
        fields.update(changes)
        return RenderRequest(**fields)

    def _uncached(self, request: "RenderRequest") -> np.ndarray:
        """What the renderer computed before the caches: `apply_preprocessing` with only the ROIs."""
        return apply_preprocessing(
            self.raw, request.geometry, request.background, rois=list(request.rois) or None,
            fast_rotation=request.fast_rotation,
        )

    def test_the_image_is_identical_to_the_uncached_pipeline_on_every_call(self) -> None:
        request = self._request()
        expected = self._uncached(request)
        self.assertTrue(np.isnan(expected).any(), "the test needs rotation-created NaN pixels to mean something")
        for call in range(3):  # the first call fills the caches, the next two read them
            result = self.renderer._render(request)
            self.assertIsNone(result.error)
            np.testing.assert_array_equal(result.image, expected, err_msg=f"call {call}")

    def test_the_roi_mask_is_built_once_and_rebuilt_when_an_roi_or_the_shape_changes(self) -> None:
        build = render_module.roi_exclusion_mask
        with mock.patch.object(render_module, "roi_exclusion_mask", side_effect=build) as counted:
            for _ in range(3):
                self.renderer._render(self._request())
            self.assertEqual(counted.call_count, 1)

            moved = (replace(self.rois[0], center_x=self.rois[0].center_x + 9.0), *self.rois[1:])
            result = self.renderer._render(self._request(rois=moved))
            self.assertEqual(counted.call_count, 2)
            np.testing.assert_array_equal(result.image, self._uncached(self._request(rois=moved)))

            resized = self._request(geometry=replace(_CASES["rot3 crop"], crop=replace(_CROP, width=200)))
            result = self.renderer._render(resized)
            self.assertEqual(counted.call_count, 3)
            np.testing.assert_array_equal(result.image, self._uncached(resized))

    def test_a_different_ignore_mask_never_reuses_the_weights_of_another(self) -> None:
        from lspr_imaging_app.roi.model import AreaRoiDetectionSettings

        background = replace(self.background, flatten_background_exclude_mask=True)
        detection = AreaRoiDetectionSettings(ignore_marked_pixels=True)
        for top in (20, 120, 20):  # the last one returns to the first mask: a cache hit that must still be right
            authored = np.zeros(_SHAPE, dtype=bool)
            authored[top:top + 40, 150:230] = True
            request = self._request(background=background, detection=detection, authored_mask=authored)
            expected = apply_preprocessing(
                self.raw, request.geometry, background, rois=list(self.rois), mask_settings=detection,
                external_mask=resolve_external_mask(authored, request.geometry, None), fast_rotation=True,
            )
            np.testing.assert_array_equal(self.renderer._render(request).image, expected, err_msg=f"top={top}")

    def test_the_shared_roi_mask_is_read_only_and_survives_a_render_unchanged(self) -> None:
        self.renderer._render(self._request())
        key, mask = self.renderer._roi_exclusion_cache
        self.assertFalse(mask.flags.writeable)
        before = mask.copy()
        self.renderer._render(self._request())
        np.testing.assert_array_equal(mask, before)

    def test_nothing_is_cached_with_flattening_off(self) -> None:
        request = self._request(background=replace(self.background, flatten_background_enabled=False))
        self.assertEqual(self.renderer._frame_caches(request, _SHAPE), {})
        self.assertIsNone(self.renderer._roi_exclusion_cache)


if __name__ == "__main__":
    unittest.main()
