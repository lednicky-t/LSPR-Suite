"""`image_tools/mask/feature_map.py` (2026-11-03): every small feature of the picture, four kinds, without ROIs. No Qt, no dataset.

A synthetic picture (log intensity, 10 wavelengths): a 7 x 5 array of nanostructure disks whose contrast follows a plasmon-like
spectrum and whose edge is softer towards the left (like the real field), on light that falls off towards the left, pixel noise.
The top-left disk is cut by the border of the data. Injected (each where the real 2026-10-22 dataset has one):

- a disk-sized dark smudge between four disks (must be dirt, not a disk);  a speck between disks;
- a dark stub attached to a disk's edge (ROI 105);  a notch of missing nanostructure at a disk's rim (ROIs 37, 53);
- a spot of extra nanostructure;  a disk with 40 % of its nanostructure gone in a band (ROI 76: no false "extra" on its intact part);
- bright dirt;  faint dark dirt (3 %: kind "faint", not masked by default);  a dense particle (40 %) in a faint wide shadow (3 %, ROI 105): the particle, not the shadow.
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
    from lspr_imaging_app.image_tools.mask import feature_map as fm
    from lspr_imaging_app.progress import Cancelled
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)

H, W, N_WL = 420, 600, 10
PITCH, R = 80.0, 22.0
LATTICE = [(60.0 + PITCH * i, 60.0 + PITCH * j) for j in range(5) for i in range(7)]
CONTRAST = -0.25 - 0.45 * np.exp(-(((np.arange(N_WL) - 5.0) / 2.0) ** 2))  # log(disk / background) per wavelength
YY, XX = np.indices((H, W), dtype=np.float64)

SMUDGE = (100.0, 100.0)  # between disks (60,60), (140,60), (60,140), (140,140)
SPECK = (100.0, 180.0)
STUB = (slice(218, 223), slice(268, 279))  # touches the left edge of disk (300, 220)
NOTCH = (400.0, 148.0)  # at the rim of disk (380, 140)
EXTRA = (215.0, 303.0)  # inside disk (220, 300)
BAND_DISK = (460.0, 300.0)
BRIGHT = (420.0, 100.0)
FAINT = (260.0, 180.0)
PARTICLE = (500.0, 180.0)  # between four disks


def _spot(c: tuple[float, float], radius: float) -> np.ndarray:
    return np.hypot(XX - c[0], YY - c[1]) <= radius


def _band() -> np.ndarray:
    return (np.abs((XX - BAND_DISK[0]) + 0.5 * (YY - BAND_DISK[1]) - 6.0) <= 9.0) & _spot(BAND_DISK, R + 3)


def picture(*, features: bool = True, seed: int = 2) -> np.ndarray:
    rng = np.random.default_rng(seed)
    structure = np.ones((H, W))
    grey = np.zeros((H, W))
    if features:
        grey += -0.3 * _spot(SMUDGE, 17.0)
        grey += np.log(0.9) * _spot(SPECK, 3.0)
        stub = np.zeros((H, W))
        stub[STUB] = 1.0
        grey += -0.4 * stub
        structure[_spot(NOTCH, 6.0)] = 0.0
        structure[_spot(EXTRA, 4.0)] = 1.4
        structure[_band()] = 0.4
        grey += np.log(1.08) * _spot(BRIGHT, 3.5)
        grey += np.log(0.97) * _spot(FAINT, 4.0)
        grey += np.log(0.97) * np.exp(-((XX - PARTICLE[0]) ** 2 + (YY - PARTICLE[1]) ** 2) / (2 * 12.0**2))  # the shadow
        grey += np.log(0.6) * _spot(PARTICLE, 3.0)
    disk = np.zeros((H, W))
    for cx, cy in LATTICE:
        soft = 1.2 + 1.0 * (1.0 - cx / W)  # softer edge on the left
        disk += 1.0 / (1.0 + np.exp((np.hypot(XX - cx, YY - cy) - R) / soft))
    light = -0.2 * np.exp(-XX / 120.0)
    logs = np.empty((N_WL, H, W), dtype=np.float32)
    for w in range(N_WL):
        logs[w] = light + disk * structure * CONTRAST[w] + grey + rng.normal(0.0, 0.006, (H, W))
    logs[:, (YY < 62) & (XX < 150)] = np.nan  # the top-left disk is cut by the border of the data
    return logs


def _kinds_at(result: fm.FeatureMap, region: np.ndarray) -> set[str]:
    return {kind for kind, m in result.kind_masks.items() if (m & region).any()}


class FeatureMapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.logs = picture()
        cls.result = fm.feature_map(cls.logs)

    def test_disks_are_found_in_the_picture_on_the_lattice(self) -> None:
        found = np.array([d.centre for d in self.result.disks])
        self.assertEqual(len(found), len(LATTICE))  # the cut disk is in, the smudge is not a disk
        for cx, cy in LATTICE:
            self.assertLess(float(np.min(np.hypot(found[:, 0] - cx, found[:, 1] - cy))), 1.5)

    def test_each_injection_is_found_as_its_own_kind(self) -> None:
        stub = np.zeros((H, W), dtype=bool)
        stub[STUB] = True
        cases = {
            "smudge": (_spot(SMUDGE, 6.0), {fm.DIRT_DARK}),
            "speck": (_spot(SPECK, 1.5), {fm.DIRT_DARK}),
            "stub on the disk edge": (stub, {fm.DIRT_DARK}),
            "notch": (_spot(NOTCH, 2.0), {fm.STRUCTURE_MISSING}),
            "extra": (_spot(EXTRA, 1.5), {fm.STRUCTURE_EXTRA}),
            "bright": (_spot(BRIGHT, 1.5), {fm.DIRT_BRIGHT}),
            "faint": (_spot(FAINT, 2.0), {fm.DIRT_FAINT}),
        }
        for name, (region, kinds) in cases.items():
            with self.subTest(name):
                self.assertEqual(_kinds_at(self.result, region), kinds)

    def test_band_of_missing_nanostructure_is_not_called_extra_elsewhere(self) -> None:
        # the band covers more than half of the disk's inner part: its own level is wrong and its neighbours' is used. Measured
        # 2026-11-03: the band is found whole, a false "extra" covers 3.6 % of the disk (its rim); before the fix, half the disk
        disk = _spot(BAND_DISK, R - 2)
        self.assertGreater((self.result.kind_masks[fm.STRUCTURE_MISSING] & _band()).sum() / _band().sum(), 0.9)
        self.assertLess((self.result.kind_masks[fm.STRUCTURE_EXTRA] & disk).sum() / disk.sum(), 0.08)

    def test_clean_picture_gives_nothing(self) -> None:
        for seed in (2, 3):
            with self.subTest(seed=seed):
                clean = fm.feature_map(picture(features=False, seed=seed))
                self.assertEqual(clean.features, ())
                self.assertEqual(len(clean.disks), len(LATTICE))  # one disk per lattice point, the cut one included

    def test_sizes_in_physical_units(self) -> None:
        speck = [f for f in self.result.features if f.kind == fm.DIRT_DARK and np.hypot(f.centroid[0] - SPECK[0], f.centroid[1] - SPECK[1]) < 3]
        self.assertAlmostEqual(speck[0].size, -0.10, delta=0.03)
        notch = [f for f in self.result.features if f.kind == fm.STRUCTURE_MISSING and np.hypot(f.centroid[0] - NOTCH[0], f.centroid[1] - NOTCH[1]) < 8]
        self.assertLess(float(np.nanmin([f.size for f in notch])), -0.6)  # all missing at its core (a rim piece may have no size: NaN)

    def test_dirt_limit_is_a_fraction_of_light(self) -> None:
        strict = fm.feature_map(self.logs, fm.FeatureMapSettings(dirt_min=0.05), disks=self.result.disks)
        self.assertEqual(_kinds_at(strict, _spot(FAINT, 2.0)), set())  # 3 % < 5 %: not found at all
        self.assertIn(fm.DIRT_DARK, _kinds_at(strict, _spot(SPECK, 1.5)))  # 10 % > 5 %

    def test_switched_off_kinds_stay_out_of_the_mask(self) -> None:
        only_dirt = fm.feature_map(self.logs, fm.FeatureMapSettings(kinds=frozenset({fm.DIRT_DARK})), disks=self.result.disks)
        self.assertTrue(only_dirt.mask[_spot(SPECK, 1.5)].all())
        self.assertFalse(only_dirt.mask[_spot(NOTCH, 2.0)].any())
        self.assertTrue(only_dirt.kind_masks[fm.STRUCTURE_MISSING][_spot(NOTCH, 2.0)].any())  # found, only not in the mask

    def test_dense_particle_in_a_faint_shadow_is_the_particle(self) -> None:
        near = _spot(PARTICLE, 25.0)
        particle = _spot(PARTICLE, 3.0)
        dark = self.result.kind_masks[fm.DIRT_DARK]
        self.assertTrue(dark[particle].all())
        self.assertLess(int((dark & near).sum()), 4 * int(particle.sum()))  # the particle and a rim, not the ~1000 px of shadow
        whole = fm.feature_map(self.logs, fm.FeatureMapSettings(core_share=0.0), disks=self.result.disks)
        self.assertGreater(int((whole.kind_masks[fm.DIRT_DARK] & near).sum()), 8 * int(particle.sum()))  # pins the rule

    def test_border_unlit_corner_is_no_dirt_but_a_cut_particle_is(self) -> None:
        # the field's black rounded corner (the real data's bottom-left) and a particle cut by the bottom border; searched to 3 px of the
        # border (2026-11-03: the 20 px strip left out before hid real dirt there)
        logs = picture(features=False)
        corner = np.hypot(XX - (W - 1), YY - (H - 1)) <= 28.0  # bottom-right corner, no disk within reach
        soft = np.clip((np.hypot(XX - (W - 1), YY - (H - 1)) - 28.0) / 6.0, 0.0, 1.0)
        dark_edge = np.log(0.05 + 0.95 * soft)  # black, then a ~6 px soft edge
        particle = _spot((300.0, H - 2.0), 6.0)
        logs = logs + (np.where(corner, np.log(0.05), dark_edge) + np.log(0.3) * particle)[None].astype(np.float32)
        result = fm.feature_map(logs)
        corner_zone = np.hypot(XX - (W - 1), YY - (H - 1)) <= 50.0
        self.assertFalse(result.mask[corner_zone].any())
        self.assertIn(fm.DIRT_DARK, _kinds_at(result, particle & (YY < H - 3)))

    def test_soft_shadow_is_faint_dirt_and_not_masked_by_default(self) -> None:
        # dust out of focus: a round, soft, faint dimming (the real blobs under ROI 3, at 31/32, 48/64, 119, 101: 2-9 %, 0.6-1.6 %/px)
        centre = (180.0, 260.0)  # between four disks
        shadow = np.log(1.0 - 0.05 * np.exp(-((XX - centre[0]) ** 2 + (YY - centre[1]) ** 2) / (2 * 9.0**2)))
        result = fm.feature_map(self.logs + shadow[None].astype(np.float32), disks=self.result.disks)
        core = _spot(centre, 4.0)
        self.assertEqual(_kinds_at(result, core), {fm.DIRT_FAINT})
        self.assertFalse(result.mask[core].any())  # found, but not in the mask by default
        both = fm.feature_map(self.logs + shadow[None].astype(np.float32), fm.FeatureMapSettings(kinds=frozenset(fm.KINDS)), disks=self.result.disks)
        self.assertTrue(both.mask[core].all())  # switched on, it is masked
        self.assertEqual(_kinds_at(self.result, _spot(PARTICLE, 3.0)), {fm.DIRT_DARK})  # a sharp particle in a soft halo stays dirt

    def test_cancel(self) -> None:
        with self.assertRaises(Cancelled):
            fm.feature_map(self.logs, disks=self.result.disks, cancelled=lambda: True)


class KindLabelsTest(unittest.TestCase):
    def test_every_kind_has_its_own_label_and_the_saved_ids_are_unchanged(self) -> None:
        self.assertEqual(set(fm.KIND_LABELS), set(fm.KINDS))
        self.assertEqual(len(set(fm.KIND_LABELS.values())), len(fm.KINDS))  # no two kinds share a name in the list
        # The ids, not the labels, are what the app saves (image/feature_map/kind_<id>): renaming a label is free, an id is not.
        self.assertEqual(fm.KINDS, ("dirt_dark", "dirt_bright", "structure_missing", "structure_extra", "dirt_faint"))


class GrowMaskTest(unittest.TestCase):
    """The margin (`fm.grow_mask`): the one function `score` and the app's regrow of a finished map (2026-11-05) both use."""

    def test_zero_is_the_mask_itself_and_each_step_is_a_diamond(self) -> None:
        mask = np.zeros((9, 9), dtype=bool)
        mask[4, 4] = True
        valid = np.ones_like(mask)
        self.assertIs(fm.grow_mask(mask, valid, 0), mask)
        one = fm.grow_mask(mask, valid, 1)
        self.assertEqual(int(one.sum()), 5)  # the pixel and its four neighbours
        self.assertFalse(one[3, 3])  # not the corners
        self.assertEqual(int(fm.grow_mask(mask, valid, 2).sum()), 13)  # 1 + 4 + 8

    def test_it_stays_inside_the_data_and_an_empty_mask_stays_empty(self) -> None:
        mask = np.zeros((9, 9), dtype=bool)
        mask[4, 4] = True
        valid = np.ones_like(mask)
        valid[:, 5:] = False  # no data from column 5 on
        grown = fm.grow_mask(mask, valid, 3)
        self.assertFalse(grown[:, 5:].any())
        self.assertTrue(grown[4, 1])  # three steps to the left are still data
        self.assertFalse(fm.grow_mask(np.zeros_like(mask), valid, 3).any())


if __name__ == "__main__":
    unittest.main()
