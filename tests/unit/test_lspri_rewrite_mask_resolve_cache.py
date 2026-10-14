"""The ignore mask while the slideshow plays (testing8, 2026-10-14).

Three things that must not change a pixel:
- `warp_boolean_mask_affine` warps a bool mask as uint8; the result equals the original float32 route;
- `resolve_external_mask` keeps the rotated mask apart from the per-wavelength chromatic warp (one rotation for
  many wavelengths) and still equals "rotate, then warp";
- the render thread resolves the mask only when the background estimate reads it.
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools import preprocess
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.chromatic.warp import warp_boolean_mask_affine, warp_image_affine
    from lspr_imaging_app.image_tools.geometry.model import CropDefinition, GeometrySettings
    from lspr_imaging_app.panels.image import render
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc


def _blob_mask(shape=(120, 160), seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    mask = np.zeros(shape, dtype=bool)
    for _ in range(12):
        cy, cx, radius = rng.integers(10, shape[0] - 10), rng.integers(10, shape[1] - 10), rng.integers(3, 9)
        mask |= (yy - cy) ** 2 + (xx - cx) ** 2 <= radius**2
    return mask


def _affines() -> list[np.ndarray]:
    out = []
    for shift_x, shift_y, degrees, scale in (
        (0.0, 0.0, 0.0, 1.0), (0.4, -0.3, 0.05, 1.0), (-2.6, 1.9, 0.3, 1.002), (7.0, -5.5, -1.2, 0.99),
        (0.5, 0.5, 0.0, 1.0), (-0.5, -0.5, 0.0, 1.0), (30.0, 12.0, 2.0, 1.01), (-80.0, 3.0, 0.0, 1.0),
    ):
        theta = np.deg2rad(degrees)
        out.append(
            np.array(
                [[scale * np.cos(theta), -scale * np.sin(theta), shift_x], [scale * np.sin(theta), scale * np.cos(theta), shift_y]]
            )
        )
    return out


def _original_float_warp(mask: np.ndarray, affine: np.ndarray) -> np.ndarray:
    return warp_image_affine(mask.astype(np.float32, copy=False), affine, order=0, cval=0.0) >= 0.5


class WarpBooleanMaskTests(unittest.TestCase):
    def test_uint8_route_equals_the_original_float32_route(self) -> None:
        for seed in range(3):
            mask = _blob_mask(seed=seed)
            for index, affine in enumerate(_affines()):
                with self.subTest(seed=seed, affine=index):
                    np.testing.assert_array_equal(warp_boolean_mask_affine(mask, affine), _original_float_warp(mask, affine))

    def test_non_bool_masks_keep_the_float_route(self) -> None:
        soft = np.zeros((20, 20), dtype=np.float32)
        soft[5:10, 5:10] = 0.7  # counts as set (>= 0.5); a uint8 cast would have dropped it
        identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        self.assertTrue(warp_boolean_mask_affine(soft, identity)[7, 7])


class ResolveCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        preprocess._resolved_mask_cache.clear()
        preprocess._spatial_mask_cache.clear()
        self.addCleanup(preprocess._resolved_mask_cache.clear)
        self.addCleanup(preprocess._spatial_mask_cache.clear)
        self.authored = _blob_mask((90, 130))
        self.geometry = GeometrySettings(
            rotation_angle_deg=1.7, crop=CropDefinition(x=4, y=3, width=110, height=70, enabled=True)
        )

    def test_equals_rotate_then_warp_for_every_wavelength(self) -> None:
        for index, affine in enumerate(_affines()):
            with self.subTest(affine=index):
                expected = warp_boolean_mask_affine(preprocess.apply_spatial_mask(self.authored, self.geometry), affine)
                np.testing.assert_array_equal(preprocess.resolve_external_mask(self.authored, self.geometry, affine), expected)

    def test_without_a_warp_it_is_just_the_rotated_mask(self) -> None:
        expected = preprocess.apply_spatial_mask(self.authored, self.geometry)
        np.testing.assert_array_equal(preprocess.resolve_external_mask(self.authored, self.geometry, None), expected)

    def test_the_rotation_runs_once_for_many_wavelengths(self) -> None:
        with mock.patch.object(preprocess, "apply_spatial_mask", wraps=preprocess.apply_spatial_mask) as spy:
            for affine in _affines():
                preprocess.resolve_external_mask(self.authored, self.geometry, affine)
            self.assertEqual(spy.call_count, 1)
            preprocess.resolve_external_mask(self.authored, GeometrySettings(rotation_angle_deg=0.4), _affines()[1])
            self.assertEqual(spy.call_count, 2)  # another geometry is another rotation

    def test_results_are_read_only_and_shared(self) -> None:
        affine = _affines()[2]
        first = preprocess.resolve_external_mask(self.authored, self.geometry, affine)
        self.assertFalse(first.flags.writeable)
        self.assertIs(preprocess.resolve_external_mask(self.authored, self.geometry, affine), first)

    def test_an_edited_mask_is_a_different_mask(self) -> None:
        before = preprocess.resolve_external_mask(self.authored, self.geometry, None).copy()
        edited = self.authored.copy()
        edited[40:60, 40:60] ^= True
        after = preprocess.resolve_external_mask(edited, self.geometry, None)
        self.assertFalse(np.array_equal(before, after))


class RenderThreadMaskUseTests(unittest.TestCase):
    @staticmethod
    def _request(**background) -> "render.RenderRequest":
        return render.RenderRequest(
            cube_index=0, wavelength_nm=550.0, geometry=GeometrySettings(),
            background=BackgroundSettings(**background), authored_mask=_blob_mask((40, 50)), mask_warp_affine=None,
            rois=(), detection=None, serial=1,
        )

    def test_truth_table(self) -> None:
        cases = (
            # (flatten on, exclude mask, show background estimate) -> mask is read
            (False, False, False, False), (False, True, False, False), (True, False, False, False),
            (True, True, False, True), (False, True, True, True), (False, False, True, False), (True, True, True, True),
        )
        for flatten, exclude, show, expected in cases:
            with self.subTest(flatten=flatten, exclude=exclude, show=show):
                request = SimpleNamespace(
                    background=BackgroundSettings(flatten_background_enabled=flatten, flatten_background_exclude_mask=exclude),
                    show_background=show,
                )
                self.assertIs(render.request_uses_ignore_mask(request), expected)

    def test_render_resolves_the_mask_only_when_it_is_read(self) -> None:
        plane = np.full((40, 50), 1000.0, dtype=np.float32)
        loader = SimpleNamespace(_load_plane_after_read_ahead=lambda cube, wavelength: plane)  # what `_render_inner` calls
        for flatten, exclude, expected_calls in ((False, True, 0), (True, False, 0), (True, True, 1)):
            with self.subTest(flatten=flatten, exclude=exclude):
                request = self._request(flatten_background_enabled=flatten, flatten_background_exclude_mask=exclude)
                with mock.patch.object(render, "resolve_external_mask", return_value=None) as resolve:
                    result = render.ImageRenderer._render_inner(loader, request, 0.0)
                self.assertIsNone(result.error)
                self.assertEqual(resolve.call_count, expected_calls)


if __name__ == "__main__":
    unittest.main()
