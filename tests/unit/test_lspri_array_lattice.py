"""`roi/array_lattice.fit_lattice`: a robust lattice through points with gaps and strays."""

from __future__ import annotations

import unittest

import numpy as np

from lspr_imaging_app.roi.array_lattice import fit_lattice


def _points(a1, a2, rows, cols, origin=(100.0, 80.0), jitter=0.4, seed=1):
    rng = np.random.default_rng(seed)
    j, i = np.mgrid[0:rows, 0:cols]
    return np.asarray(origin) + i.reshape(-1, 1) * np.asarray(a1) + j.reshape(-1, 1) * np.asarray(a2) + rng.normal(0, jitter, (rows * cols, 2))


class FitLatticeTest(unittest.TestCase):
    def test_rectangular_array_with_gaps_and_strays(self) -> None:
        points = _points((40.0, 0.0), (0.0, 50.0), 6, 8)
        points = np.delete(points, [3, 17, 30], axis=0)
        strays = np.array([[520.0, 400.0], [30.0, 330.0], [610.0, 20.0]])
        fit = fit_lattice(np.vstack([points, strays]), tolerance=0.15)
        self.assertIsNotNone(fit)
        self.assertEqual((fit.rows, fit.cols), (6, 8))
        self.assertEqual(int(fit.member.sum()), len(points))
        self.assertFalse(fit.member[len(points):].any())
        np.testing.assert_allclose(np.sort([np.hypot(*fit.a1), np.hypot(*fit.a2)]), [40.0, 50.0], atol=0.2)

    def test_tilted_and_triangular_lattices(self) -> None:
        t = np.radians(3.0)
        tilted = _points((40 * np.cos(t), 40 * np.sin(t)), (-45 * np.sin(t), 45 * np.cos(t)), 5, 6)
        fit = fit_lattice(tilted, tolerance=0.15)
        self.assertEqual(int(fit.member.sum()), 30)
        self.assertAlmostEqual(float(np.degrees(np.arctan2(fit.a1[1], fit.a1[0]))), 3.0, delta=0.3)
        hexagonal = _points((40.0, 0.0), (20.0, 34.64), 6, 7)
        fit = fit_lattice(hexagonal, tolerance=0.15)
        self.assertEqual(int(fit.member.sum()), 42)
        np.testing.assert_allclose([np.hypot(*fit.a1), np.hypot(*fit.a2)], [40.0, 40.0], atol=0.3)
        self.assertGreater(float(np.dot(fit.a1, fit.a2)), 0.0)  # 60 degrees, not the same lattice written at 120

    def test_a_finer_lattice_that_holds_the_true_one_does_not_win(self) -> None:
        fit = fit_lattice(_points((44.0, 0.0), (0.0, 44.0), 6, 8), tolerance=0.2)
        self.assertEqual(sorted(np.round([np.hypot(*fit.a1), np.hypot(*fit.a2)])), [44.0, 44.0])

    def test_anchors_keep_the_users_own_spots_on_the_lattice(self) -> None:
        points = _points((44.0, 0.0), (0.0, 44.0), 6, 8)
        fit = fit_lattice(points, anchors=points[[0, 47]], tolerance=0.15)
        self.assertTrue(fit.member[[0, 47]].all())
        self.assertEqual((fit.rows, fit.cols), (6, 8))

    def test_one_row_is_a_one_dimensional_lattice(self) -> None:
        fit = fit_lattice(_points((40.0, 0.0), (0.0, 40.0), 1, 12), tolerance=0.2)
        self.assertTrue(fit.one_dimensional)
        self.assertEqual((fit.rows, fit.cols), (1, 12))

    def test_a_stray_far_from_the_body_does_not_stretch_the_array(self) -> None:
        points = _points((40.0, 0.0), (0.0, 40.0), 4, 4)
        far = np.array([[100.0 + 40 * 12, 80.0 + 40 * 9]])  # exactly on a node, 12 columns away
        fit = fit_lattice(np.vstack([points, far]), tolerance=0.2)
        self.assertEqual((fit.rows, fit.cols), (4, 4))
        self.assertFalse(fit.member[-1])

    def test_too_few_or_scattered_points_give_none(self) -> None:
        self.assertIsNone(fit_lattice(np.array([[0.0, 0.0], [10.0, 10.0]])))
        rng = np.random.default_rng(5)
        self.assertIsNone(fit_lattice(rng.uniform(0, 500, (6, 2)), tolerance=0.05, min_members=5))


if __name__ == "__main__":
    unittest.main()
