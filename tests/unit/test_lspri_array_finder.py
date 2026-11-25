"""`roi/array_finder`: candidates from a template, then the array the thresholds make of them (synthetic images with a known truth)."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _array_synth import synth  # noqa: E402

from lspr_imaging_app.domain.roi_shapes import Circle  # noqa: E402
from lspr_imaging_app.roi.array_finder import FinderError, FinderSeed, FinderSettings, build_array, candidate_key, find_candidates  # noqa: E402

MISSING = ((2, 3), (4, 5))


def _seed(centres, node, diameter=20.0, shift=(0.0, 0.0)):
    x, y = centres[node]
    return FinderSeed(x + shift[0], y + shift[1], diameter, Circle(diameter))


def _run(image, seeds=(), **settings):
    s = FinderSettings(**settings)
    return build_array(find_candidates(image, tuple(seeds), s), s)


def _worst_error(result, centres):
    truth = np.array(list(centres.values()))
    return max(float(np.min(np.hypot(*(truth - p).T))) for p in result.member_xy)


class FinderOnSyntheticArraysTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.image, cls.centres = synth(missing=MISSING, tilt_deg=1.5)

    def test_no_guide_finds_the_whole_array(self) -> None:
        result = _run(self.image, fix_size=False)
        self.assertTrue(result.ordered)
        self.assertEqual((result.rows, result.cols, result.count), (6, 8, 46))
        self.assertEqual(result.kind, "rectangular")
        self.assertAlmostEqual(result.tilt_deg, 1.5, delta=0.1)
        self.assertLess(_worst_error(result, self.centres), 0.2)
        self.assertEqual(len(result.missing_xy), 2)

    def test_one_seed_even_when_drawn_off_its_spot(self) -> None:
        result = _run(self.image, [_seed(self.centres, (0, 0), shift=(1.5, -1.0))])
        self.assertEqual((result.rows, result.cols, result.count), (6, 8, 46))
        self.assertEqual(int(result.is_seed.sum()), 1)
        self.assertEqual(result.new_count, 45)
        self.assertLess(_worst_error(result, self.centres), 0.2)  # the template was centred on the spot, not on the drawing
        np.testing.assert_allclose(result.member_diameter, 20.0)  # size fixed: the seed's own

    def test_two_corner_seeds_limit_the_array(self) -> None:
        seeds = [_seed(self.centres, (1, 2)), _seed(self.centres, (4, 6))]
        result = _run(self.image, seeds)
        self.assertEqual((result.rows, result.cols), (4, 5))
        self.assertTrue((result.member_col <= 4).all())
        loose = _run(self.image, seeds, seeds_are_corners=False)
        self.assertEqual((loose.rows, loose.cols), (6, 8))

    def test_missing_nodes_are_left_out_unless_asked_for(self) -> None:
        left = _run(self.image, [_seed(self.centres, (0, 0))])
        placed = _run(self.image, [_seed(self.centres, (0, 0))], place_missing=True)
        self.assertEqual((left.count, len(left.missing_xy)), (46, 2))
        self.assertEqual((placed.count, len(placed.missing_xy), int(placed.is_placed.sum())), (48, 0, 2))
        _full_image, full = synth(tilt_deg=1.5)
        for xy in placed.member_xy[placed.is_placed]:
            self.assertLess(min(np.hypot(*(np.array(full[node]) - xy)) for node in MISSING), 0.5)  # on the lattice position of the empty node

    def test_the_thresholds_act_without_a_new_search(self) -> None:
        seed = [_seed(self.centres, (0, 0))]
        settings = FinderSettings()
        candidates = find_candidates(self.image, tuple(seed), settings)
        with self.assertRaises(FinderError):  # nothing but the seed is that similar
            build_array(candidates, replace(settings, similarity=0.99999))
        self.assertLess(build_array(candidates, replace(settings, similarity=0.9)).count, 47)
        self.assertEqual(build_array(candidates, settings).count, 46)

    def test_a_smudge_off_the_lattice_is_a_stray_not_a_member(self) -> None:
        x, y = self.centres[(1, 1)]
        image, centres = synth(missing=MISSING, tilt_deg=1.5, dirt=((x + 22.0, y + 22.0, 20.0),))  # between four nodes
        result = _run(image, [_seed(centres, (0, 0))])
        self.assertEqual(result.count, 46)
        self.assertGreaterEqual(len(result.stray_xy), 1)
        self.assertIn("off the lattice", result.stray_reason)

    def test_candidate_key_changes_only_with_what_the_slow_part_reads(self) -> None:
        seeds = (FinderSeed(10.0, 10.0, 20.0, Circle(20.0)),)
        base = FinderSettings()
        self.assertEqual(candidate_key(base, seeds), candidate_key(replace(base, similarity=0.9, position_tol=0.3, place_missing=True), seeds))
        self.assertNotEqual(candidate_key(base, seeds), candidate_key(replace(base, bright=True), seeds))
        self.assertNotEqual(candidate_key(base, seeds), candidate_key(base, ()))


class FinderMaskTest(unittest.TestCase):
    """A spot damaged where the user has masked is found with the mask on and missed with it off."""

    @classmethod
    def setUpClass(cls) -> None:
        image, cls.centres = synth(rows=5, cols=6, tilt_deg=1.0)
        x, y = cls.centres[(2, 3)]
        yy, xx = np.mgrid[0 : image.shape[0], 0 : image.shape[1]]
        smudge = np.hypot(xx - (x + 3.0), yy - (y - 2.0)) <= 5.5  # a lump on the spot, off its middle
        cls.damaged = image.copy()
        cls.damaged[smudge] += 0.9  # brighter than the background: the spot looks like a ring with a hole
        cls.valid = ~ndimage.binary_dilation(smudge, iterations=1)

    def _count(self, **settings) -> int:
        x, y = self.centres[(0, 0)]
        seed = FinderSeed(x, y, 20.0, Circle(20.0))
        s = FinderSettings(**settings)
        result = build_array(find_candidates(self.damaged, (seed,), s, valid_mask=self.valid), s)
        return result.count

    def test_the_masked_damage_does_not_hide_the_spot(self) -> None:
        self.assertEqual(self._count(use_mask=True), 30)
        self.assertLess(self._count(use_mask=False), 30)

    def test_the_mask_is_reported_and_changes_the_cache_key(self) -> None:
        x, y = self.centres[(0, 0)]
        seed = FinderSeed(x, y, 20.0, Circle(20.0))
        candidates = find_candidates(self.damaged, (seed,), FinderSettings(), valid_mask=self.valid)
        self.assertTrue(any("masked pixels" in w for w in candidates.warnings))
        self.assertNotEqual(candidate_key(FinderSettings(use_mask=True), (seed,)), candidate_key(FinderSettings(use_mask=False), (seed,)))


class FinderOtherLatticesTest(unittest.TestCase):
    def test_triangular_array(self) -> None:
        image, centres = synth(rows=6, cols=8, pitch=(44.0, 38.1), row_shift=0.5)
        result = _run(image, fix_size=False)
        self.assertEqual(result.count, 48)
        self.assertEqual(result.kind, "triangular")

    def test_sizes_that_drift_are_measured_per_spot_and_gated_by_the_tolerance(self) -> None:
        image, _centres = synth(size_gradient=0.3)
        individual = _run(image, fix_size=False, size_mode="individual", size_tol=0.5)
        self.assertEqual(individual.count, 48)
        self.assertGreater(float(individual.member_diameter.max() - individual.member_diameter.min()), 3.0)
        uniform = _run(image, fix_size=False, size_mode="uniform", size_tol=0.5)
        self.assertEqual(float(uniform.member_diameter.max()), float(uniform.member_diameter.min()))
        tight = _run(image, fix_size=False, size_mode="individual", size_tol=0.1)
        self.assertLess(tight.count, 48)  # the biggest and smallest spots are further from the median than 10 %
        self.assertIn("size off", tight.stray_reason)

    def test_spots_that_do_not_form_a_lattice_come_back_as_free_positions(self) -> None:
        image, _centres = synth(rows=1, cols=1)
        rng = np.random.default_rng(2)
        spots = np.column_stack([rng.uniform(40, 440, 7), rng.uniform(40, 320, 7)])
        spots = spots[np.argsort(spots[:, 0])]
        yy, xx = np.mgrid[0 : image.shape[0], 0 : image.shape[1]]
        img = image.astype(np.float64)
        for x, y in spots:
            img -= 0.25 / (1.0 + np.exp((np.hypot(xx - x, yy - y) - 10.0) / 1.0))
        seed = FinderSeed(float(spots[0, 0]), float(spots[0, 1]), 20.0, Circle(20.0))
        result = _run(img.astype(np.float32), [seed], position_tol=0.02, min_members=6)
        self.assertFalse(result.ordered)
        self.assertEqual(result.kind, "free")
        self.assertGreaterEqual(result.count, 6)

    def test_an_image_without_spots_says_so(self) -> None:
        flat = np.full((200, 200), 1.0, dtype=np.float32) + np.random.default_rng(0).normal(0, 0.005, (200, 200)).astype(np.float32)
        with self.assertRaises(FinderError):
            _run(flat, [FinderSeed(100.0, 100.0, 20.0, Circle(20.0))], similarity=0.9)


if __name__ == "__main__":
    unittest.main()
