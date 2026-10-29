"""`image_tools/mask/roi_features.py` (2026-10-29): small features on or around ROIs, found from the image alone. No Qt, no dataset.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see `test_lspri_rewrite_analysis_core.py`'s docstring.

A synthetic array of 16 dark disks on a bright background (soft edge, shot-like noise, fixed seeds) has the ingredients of the real
2026-10-29 dataset: a small bright lobe at every disk's rim whose strength differs from ROI to ROI (shared structure that must
*not* be reported), an invalid border, and a few injected features that must be:

- ROI 5: a dark spot on the reference ring;  ROI 9: a bright streak across the sample disk. All other ROIs are quiet.
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
    from lspr_imaging_app.image_tools.mask import roi_features as rf
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

SIZE = 400
SPACING = 90
HALF = 42
RADIUS_FIT = 36.0
DISK_R = 20.0  # where the disk's edge is (50 % point)
SAMPLE_R = 16.0
RING = (24.0, 29.0)
BACKGROUND, DISK = 1000.0, 100.0


def _grid_centres() -> list[tuple[float, float]]:
    return [(60.0 + SPACING * i, 60.0 + SPACING * j) for j in range(4) for i in range(4)]  # ROI k = 4 j + i + 1


def _gauss(yy: np.ndarray, xx: np.ndarray, cx: float, cy: float, sigma: float) -> np.ndarray:
    return np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * sigma**2))


def build_image(*, seed: int = 5, features: bool = True, invalid_top: int = 0, edge_falloff: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """(image, valid). `invalid_top` rows at the top have no data (NaN), as the rotation fill of a real frame; `edge_falloff` darkens the
    valid image smoothly towards that border (a real rotated frame is 18 % darker 3 px from it, 7 % darker 20 px from it)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(np.float64)
    img = np.full((SIZE, SIZE), BACKGROUND)
    for k, (cx, cy) in enumerate(_grid_centres(), start=1):
        r = np.hypot(xx - cx, yy - cy)
        img += (DISK - BACKGROUND) * (1.0 - 0.5 * (1.0 + np.tanh((r - DISK_R) / 2.0))) * (r < 60)  # the disk: dark inside, soft edge
        # the lobe every disk shares, of a different strength each time (0 .. 0.14 of the disk's contrast)
        img += rng.uniform(0.0, 0.14) * (BACKGROUND - DISK) * _gauss(yy, xx, cx + 13.0, cy + 10.0, 2.5)
        if features and k == 5:
            img -= 380.0 * _gauss(yy, xx, cx + 27.0 * np.cos(0.5), cy + 27.0 * np.sin(0.5), 2.0)  # dark spot on the ring
        if features and k == 9:
            for t in np.linspace(-12.0, 12.0, 60):  # bright streak across the disk, ~4 px wide
                img += 12.0 * _gauss(yy, xx, cx + t * 0.7 - 2.0, cy + t * 0.7, 1.6)
    if invalid_top and edge_falloff:
        img = img * (1.0 - edge_falloff * np.exp(-np.maximum(yy - invalid_top, 0.0) / 14.0))
    img = img + rng.normal(size=img.shape) * 0.3 * np.sqrt(np.maximum(img, 1.0))  # shot-like noise
    valid = np.ones(img.shape, bool)
    if invalid_top:
        valid[:invalid_top] = False
        img = np.where(valid, img, np.nan)
    return img.astype(np.float32), valid


def _region_masks(cx: float, cy: float, box: tuple[int, int, int, int]) -> tuple[np.ndarray, np.ndarray]:
    y0, y1, x0, x1 = box
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
    r = np.hypot(xx - cx, yy - cy)
    return r <= SAMPLE_R, (r >= RING[0]) & (r <= RING[1])


def fit_all(img: np.ndarray, *, nominal_offset: tuple[float, float] = (1.5, -1.0)) -> tuple[dict, dict]:
    patches, windows = {}, {}
    for k, (cx, cy) in enumerate(_grid_centres(), start=1):
        y0, y1 = max(int(cy) - HALF, 0), min(int(cy) + HALF + 1, SIZE)
        x0, x1 = max(int(cx) - HALF, 0), min(int(cx) + HALF + 1, SIZE)
        win = img[y0:y1, x0:x1]
        valid = np.isfinite(win)
        sample, ring = _region_masks(cx, cy, (y0, y1, x0, x1))
        windows[k] = (win, sample, ring, (y0, y1, x0, x1))
        patch = rf.fit_patch(np.where(valid, win, 0.0), valid, (cx - x0 + nominal_offset[0], cy - y0 + nominal_offset[1]), RADIUS_FIT)
        if patch is not None:
            patches[k] = patch
    return patches, windows


def detect(
    img: np.ndarray, settings: rf.FeatureSettings = rf.FeatureSettings(), *, second_pass: bool = True
) -> tuple[dict, dict, dict]:
    patches, windows = fit_all(img)
    regions = {k: windows[k][1] | windows[k][2] for k in patches}
    out = rf.detect(patches, regions, settings, second_pass=second_pass)
    return {k: v[1] for k, v in out.items()}, {k: v[0] for k, v in out.items()}, windows


class RoiFeaturesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.image, _ = build_image()
        cls.found, cls.scores, cls.windows = detect(cls.image)

    def test_the_injected_dark_spot_on_the_ring_is_found_and_masked(self) -> None:
        features = self.found[5].features
        self.assertEqual([f.sign for f in features], [-1])
        self.assertGreaterEqual(features[0].overlap_px, 4)
        # the spot's own centre pixel is inside the mask (window coordinates)
        win, _, _, (y0, y1, x0, x1) = self.windows[5]
        cx, cy = _grid_centres()[4]
        sx, sy = int(round(cx + 27.0 * np.cos(0.5) - x0)), int(round(cy + 27.0 * np.sin(0.5) - y0))
        self.assertTrue(self.found[5].mask[sy, sx])

    def test_the_injected_bright_streak_in_the_sample_disk_is_found(self) -> None:
        features = self.found[9].features
        self.assertTrue(features, "the streak was not found")
        self.assertTrue(all(f.sign == 1 for f in features))
        self.assertGreater(sum(f.overlap_px for f in features), 20)

    def test_a_streak_through_the_centre_leaves_no_dark_twin_only_because_of_the_second_pass(self) -> None:
        # A bright streak through the middle pulls the radial profile up where few pixels per bin exist; the pixels beside it then
        # look darker than expected. Leaving the first pass's findings out of the profile removes that twin.
        one_pass, _, _ = detect(self.image, second_pass=False)
        self.assertTrue(any(f.sign == -1 for f in one_pass[9].features), "the artefact this test is about did not appear in one pass")
        self.assertTrue(all(f.sign == 1 for f in self.found[9].features))

    def test_the_shared_lobe_of_varying_strength_is_not_reported_anywhere(self) -> None:
        # every other ROI carries the lobe (0 .. 14 % of the contrast) and nothing else
        quiet = [k for k in self.found if k not in (5, 9)]
        self.assertEqual({k: len(self.found[k].features) for k in quiet if self.found[k].features}, {})

    def test_the_score_is_about_unit_normal_where_nothing_is_wrong(self) -> None:
        z = np.concatenate([self.scores[k].z[self.scores[k].z != 0] for k in (1, 2, 3, 4, 6, 7, 8, 10, 11)])
        robust_rms = 1.4826 * float(np.median(np.abs(z)))
        self.assertGreater(robust_rms, 0.6)
        self.assertLess(robust_rms, 1.4)
        self.assertLess(float(np.mean(np.abs(z) > 5.0)), 1e-3)

    def test_an_invalid_border_is_not_taken_for_a_feature(self) -> None:
        image, _ = build_image(features=False, invalid_top=30)  # ROIs 1-4 lose their upper part
        found, _, _ = detect(image)
        self.assertEqual({k: len(f.features) for k, f in found.items() if f.features}, {})

    def test_the_darkening_towards_the_edge_of_the_data_is_not_a_feature(self) -> None:
        image, _ = build_image(features=False, invalid_top=30, edge_falloff=0.2)
        edge = rf.FeatureSettings()
        found, _, _ = detect(image, edge)
        self.assertEqual({k: len(f.features) for k, f in found.items() if f.features}, {})
        # and it is the edge rule that makes it so: without it the slope is found in the ROIs that touch the border
        off, _, _ = detect(image, rf.FeatureSettings(edge_margin_px=0))
        self.assertTrue(any(f.features for k, f in off.items() if k <= 4), "the artefact this test is about did not appear without the rule")

    def test_the_result_is_deterministic(self) -> None:
        again, _, _ = detect(self.image)
        for k in self.found:
            np.testing.assert_array_equal(again[k].mask, self.found[k].mask)

    def test_too_few_rois_refuse_instead_of_guessing(self) -> None:
        patches, _ = fit_all(self.image)
        few = {k: patches[k] for k in list(patches)[: rf.MIN_ROIS - 1]}
        with self.assertRaises(rf.NotEnoughRois):
            rf.score_rois(few)

    def test_a_window_without_data_gives_no_patch(self) -> None:
        window = np.ones((85, 85))
        valid = np.zeros((85, 85), bool)
        valid[:10] = True
        self.assertIsNone(rf.fit_patch(window, valid, (42.0, 42.0), RADIUS_FIT))

    def test_the_fitted_centre_moves_to_the_disk(self) -> None:
        patches, windows = fit_all(self.image, nominal_offset=(2.5, -2.0))
        cx, cy = _grid_centres()[0]
        y0, _, x0, _ = windows[1][3]
        fx, fy = patches[1].centre
        self.assertAlmostEqual(fx, cx - x0, delta=0.4)
        self.assertAlmostEqual(fy, cy - y0, delta=0.4)


class FindFeaturesRulesTest(unittest.TestCase):
    """The component rules on a hand-made score map."""

    def _z(self) -> np.ndarray:
        z = np.zeros((60, 60))
        z[10:14, 10:14] = 6.0  # a feature
        z[30:33, 30:33] = 4.0  # reaches z_grow but never a seed
        z[45:47, 5:7] = 6.0  # a feature outside the region
        return z

    def test_seed_overlap_and_area_rules(self) -> None:
        region = np.zeros((60, 60), bool)
        region[5:40, 5:40] = True
        found = rf.find_features(self._z(), region)
        self.assertEqual(len(found.features), 1)
        self.assertEqual(found.features[0].area_px, 16)
        self.assertEqual(found.features[0].sign, 1)
        # the mask is the feature grown by one pixel (4-connected)
        self.assertEqual(int(found.mask.sum()), 16 + 16)

    def test_a_wide_deviation_is_not_a_small_feature(self) -> None:
        z = np.zeros((80, 80))
        z[20:50, 20:50] = 6.0  # 30 px across
        region = np.ones((80, 80), bool)
        self.assertEqual(rf.find_features(z, region).features, ())
        lifted = rf.find_features(z, region, rf.FeatureSettings(max_width_px=40.0, max_area_px=2000))
        self.assertEqual(len(lifted.features), 1)  # so the width limit, not blindness, rejected it
        self.assertAlmostEqual(lifted.features[0].width_px, 30.0, delta=2.0)
        # a thin streak is small however long it is
        streak = np.zeros((80, 80))
        streak[40:44, 10:70] = 6.0
        self.assertEqual(len(rf.find_features(streak, region, rf.FeatureSettings(max_area_px=1000)).features), 1)

    def test_dark_features_have_negative_sign(self) -> None:
        region = np.ones((60, 60), bool)
        found = rf.find_features(-self._z(), region)
        self.assertTrue(all(f.sign == -1 for f in found.features))

    def test_masked_share_is_the_covered_fraction_of_the_region(self) -> None:
        region = np.zeros((20, 20), bool)
        region[:10] = True
        mask = np.zeros((20, 20), bool)
        mask[:5, :] = True
        mask[15:] = True  # outside the region: not counted
        self.assertAlmostEqual(rf.masked_share(mask, region), 0.5)
        self.assertEqual(rf.masked_share(mask, np.zeros((20, 20), bool)), 0.0)

    def test_ratio_effect_is_the_relative_shift_of_the_two_means(self) -> None:
        image = np.full((40, 40), 100.0)
        deviation = np.zeros((40, 40))
        sample = np.zeros((40, 40), bool)
        sample[10:20, 10:20] = True
        ring = np.zeros((40, 40), bool)
        ring[25:35, 25:35] = True
        mask = np.zeros((40, 40), bool)
        mask[10:15, 10:20] = True  # half of the sample disk
        deviation[mask] = 10.0  # +10 % there
        s, r = rf.ratio_effect(image, deviation, sample, ring, mask)
        self.assertAlmostEqual(s, 0.05, places=9)  # half of the disk x 10 %
        self.assertEqual(r, 0.0)


if __name__ == "__main__":
    unittest.main()
