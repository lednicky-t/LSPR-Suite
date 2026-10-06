"""Background estimate (cv2 fast path) and apply modes - 2026-10-06.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`test_lspri_rewrite_analysis_core.py`'s docstring.

The reference for the fast path is the previous scipy implementation
(downsample by block mean, `gaussian_filter`, `ndimage.zoom`), kept here as a
few lines so the new code is pinned to the old maths. Measured on real frames
the two agree to 0.02 % of the background level at bin 2
(`docs/background_method_study_2026-10-06.md`); the tolerances below are loose
enough for synthetic data, tight enough to catch a wrong sigma or a shifted grid.
"""

from __future__ import annotations

import sys
import unittest

import numpy as np
from scipy import ndimage

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.background.apply import apply_background
    from lspr_imaging_app.image_tools.background.estimate import estimate_background_profile, flatten_background
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings, max_binning_for_sigma
    from lspr_imaging_app.roi.model import AreaRoiDetectionSettings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


def _scene(height: int = 160, width: int = 240) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A smooth multiplicative gain over a 50k white substrate with dark
    (30k) disks. Returns (image, gain, disk mask)."""
    yy, xx = np.mgrid[:height, :width].astype(np.float32)
    gain = 1.0 + 0.04 * np.sin(xx / 70.0) + 0.05 * (yy / height - 0.5)
    ideal = np.full((height, width), 50000.0, dtype=np.float32)
    disks = np.zeros((height, width), dtype=bool)
    for cy in (40, 120):
        for cx in (40, 120, 200):
            disks |= np.hypot(xx - cx, yy - cy) <= 12
    ideal[disks] = 30000.0
    return (ideal * gain).astype(np.float32), gain.astype(np.float32), disks


def _reference_binned(image: np.ndarray, valid: np.ndarray, sigma: float, factor: int) -> np.ndarray:
    """The previous scipy estimate (block mean, gaussian_filter, zoom)."""
    h, w = image.shape
    ph, pw = -(-h // factor) * factor, -(-w // factor) * factor

    def binned(a: np.ndarray) -> np.ndarray:
        padded = np.pad(a, ((0, ph - h), (0, pw - w)), mode="edge")
        return padded.reshape(ph // factor, factor, pw // factor, factor).mean(axis=(1, 3), dtype=np.float32)

    s = max(sigma / factor, 1.0)
    num = ndimage.gaussian_filter(binned(np.where(valid, image, 0.0).astype(np.float32)), s, mode="nearest")
    den = ndimage.gaussian_filter(binned(valid.astype(np.float32)), s, mode="nearest")
    small = num / np.maximum(den, 1e-6)
    return ndimage.zoom(small, (h / small.shape[0], w / small.shape[1]), order=1, mode="nearest", prefilter=False)[:h, :w]


class TestCv2EstimateMatchesScipyReference(unittest.TestCase):
    def setUp(self) -> None:
        self.image, self.gain, self.disks = _scene()
        self.settings = AreaRoiDetectionSettings(ignore_marked_pixels=True)
        self.valid = ~self.disks

    def _estimate(self, binning: int) -> np.ndarray:
        return estimate_background_profile(
            self.image, sigma_px=24.0, binning=binning, mask_settings=self.settings, external_mask=self.disks
        )

    def test_binned_estimate_agrees_with_old_scipy_maths(self) -> None:
        for factor in (2, 4):
            new = self._estimate(factor)
            ref = _reference_binned(self.image, self.valid, 24.0, factor)
            rel = np.abs(new - ref)[self.valid] / 50000.0
            self.assertLess(float(np.sqrt(np.mean(rel**2))), 5e-4, f"bin {factor}")

    def test_unbinned_estimate_agrees_with_scipy_gaussian(self) -> None:
        new = self._estimate(1)
        num = ndimage.gaussian_filter(np.where(self.valid, self.image, 0.0).astype(np.float32), 24.0, mode="nearest")
        den = ndimage.gaussian_filter(self.valid.astype(np.float32), 24.0, mode="nearest")
        np.testing.assert_allclose(new[self.valid], (num / den)[self.valid], atol=2.0)

    def test_estimate_is_the_local_white_level_not_the_disk(self) -> None:
        new = self._estimate(2)
        # Under the disks the estimate must read white (~50k * gain), not 30k.
        self.assertGreater(float(new[self.disks].min()), 46000.0)

    def test_region_is_a_slice_of_the_full_estimate(self) -> None:
        full = self._estimate(2)
        region = estimate_background_profile(
            self.image, sigma_px=24.0, binning=2, mask_settings=self.settings, external_mask=self.disks,
            region=(20, 10, 100, 90),
        )
        np.testing.assert_array_equal(region, full[10:90, 20:100])


class TestApplyModes(unittest.TestCase):
    def test_divide_recovers_the_ideal_example(self) -> None:
        """The maintainer's worked example: gain 0.90 at the sample, 0.96 at
        the reference, ideal white 50k / sample 30k."""
        image = np.array([[27000.0, 48000.0]], dtype=np.float32)  # sample, reference
        background = np.array([[45000.0, 48000.0]], dtype=np.float32)  # local white level at each
        out = apply_background(image, background, 50000.0)
        np.testing.assert_allclose(out, [[30000.0, 50000.0]], rtol=1e-5)
        absorbance = np.log10(out[0, 1] / out[0, 0])
        self.assertAlmostEqual(float(absorbance), float(np.log10(50.0 / 30.0)), places=5)

    def test_divide_gives_nan_where_there_is_no_positive_background(self) -> None:
        out = apply_background(
            np.array([[100.0, 100.0, np.nan]], dtype=np.float32),
            np.array([[0.0, -5.0, 10.0]], dtype=np.float32), 50.0,
        )
        self.assertTrue(np.all(np.isnan(out)))

    def test_flatten_removes_a_multiplicative_gain_from_dark_disks(self) -> None:
        image, _gain, disks = _scene()
        kw = dict(sigma_px=24.0, binning=2, mask_settings=AreaRoiDetectionSettings(ignore_marked_pixels=True), external_mask=disks)
        flat = flatten_background(image, **kw)
        # Ideal is 30k in every disk; the raw disks vary by ~2 % with the gain.
        self.assertGreater(float(image[disks].std()) / 30000.0, 0.01)
        self.assertLess(float(flat[disks].std()) / 30000.0, 0.005)


class TestBinningLimit(unittest.TestCase):
    def test_binning_is_limited_to_a_sixth_of_sigma(self) -> None:
        self.assertEqual([max_binning_for_sigma(v) for v in (3, 11, 12, 24, 47, 48, 500)], [1, 1, 2, 4, 4, 8, 8])

    def test_default_binning_is_8_and_allowed_for_the_default_sigma(self) -> None:
        settings = BackgroundSettings()
        self.assertEqual(settings.flatten_background_binning, 8)
        self.assertLessEqual(settings.flatten_background_binning, max_binning_for_sigma(settings.flatten_background_sigma_px))


class TestSettingsDefaults(unittest.TestCase):
    def test_new_settings_default_to_divide(self) -> None:
        self.assertEqual(BackgroundSettings().flatten_background_mode, "divide")


if __name__ == "__main__":
    unittest.main()
