"""ROI position tuning, step C1 (`lspri_imaging_app.analysis.roi_tuning_objectives`): the "Rank by" registry, the smooth noise surface and the best position
per ROI, on small planted lattices. No Qt."""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import numpy as np

    from lspr_imaging_app.analysis import roi_tuning as rt
    from lspr_imaging_app.analysis import roi_tuning_objectives as ob
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _objectives(lattice: rt.Lattice, delta: np.ndarray, noise: np.ndarray) -> rt.LatticeObjectives:
    """A LatticeObjectives with the given ``delta`` and ``noise`` (both ``[point, roi]``); the means and SEMs are made up around them."""
    offsets = lattice.offsets()
    shape = delta.shape
    zeros = np.zeros(shape)
    sem = np.full(shape, 0.01)
    n = np.full(shape, 30, dtype=np.int64)
    return rt.LatticeObjectives(
        tuple(range(1, shape[1] + 1)), offsets, zeros, delta.astype(np.float64), noise.astype(np.float64), noise.astype(np.float64),
        np.full(shape, 0.05), np.full(shape, 0.05), sem, sem, n, n,
    )


def _plane(lattice: rt.Lattice, fn) -> np.ndarray:
    """A ``[point]`` array from ``fn(dx, dy)`` over the lattice's offsets."""
    return np.array([fn(dx, dy) for dx, dy in lattice.offsets()], dtype=np.float64)


class RegistryTest(unittest.TestCase):
    def test_the_default_objective_is_in_the_registry_and_is_the_smoothed_snr(self) -> None:
        self.assertEqual(ob.DEFAULT_OBJECTIVE_KEY, "snr_smooth")
        self.assertEqual(ob.objective(ob.DEFAULT_OBJECTIVE_KEY).key, "snr_smooth")

    def test_keys_are_unique_and_every_entry_has_a_label_and_a_tooltip(self) -> None:
        keys = [o.key for o in ob.OBJECTIVES]
        self.assertEqual(len(keys), len(set(keys)))
        for entry in ob.OBJECTIVES:
            self.assertTrue(entry.label and entry.tooltip, entry.key)
            self.assertIn(entry.unit, ("", "nm"))

    def test_an_unknown_key_names_the_valid_ones(self) -> None:
        with self.assertRaises(KeyError) as ctx:
            ob.objective("nonsense")
        self.assertIn("snr_smooth", str(ctx.exception))

    def test_only_the_noise_objective_is_lower_is_better(self) -> None:
        self.assertEqual([o.key for o in ob.OBJECTIVES if not o.higher_is_better], ["noise"])


class CentreTest(unittest.TestCase):
    def test_the_centre_is_the_zero_offset_row_whatever_the_pitch(self) -> None:
        for size, pitch in ((1, 1.0), (3, 2.0), (5, 0.5), (7, 3.0)):
            lattice = rt.Lattice(size, pitch)
            self.assertEqual(ob.centre_index(lattice.offsets()), lattice.centre_index)

    def test_a_lattice_without_the_current_position_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            ob.centre_index(np.array([[1.0, 0.0], [2.0, 0.0]]))


class SmoothNoiseTest(unittest.TestCase):
    lattice = rt.Lattice(5, 1.0)

    def test_a_noise_that_is_exactly_exp_of_a_quadratic_is_returned_unchanged(self) -> None:
        noise = _plane(self.lattice, lambda dx, dy: np.exp(-3.0 + 0.04 * dx - 0.02 * dy + 0.03 * dx * dx + 0.01 * dx * dy + 0.05 * dy * dy))[:, None]
        np.testing.assert_allclose(ob.smooth_noise(self.lattice.offsets(), noise), noise, rtol=1e-9)

    def test_scatter_on_one_position_is_pulled_back_towards_the_surface(self) -> None:
        truth = _plane(self.lattice, lambda dx, dy: np.exp(-3.0 + 0.1 * (dx * dx + dy * dy)))[:, None]
        lucky = truth.copy()
        lucky[0, 0] *= 0.6  # one corner measured 40 % too quiet
        smooth = ob.smooth_noise(self.lattice.offsets(), lucky)
        # A corner has the most leverage on a quadratic fit, so it moves only part of the way back (here about half): closer to the truth, not equal to it.
        self.assertLess(abs(smooth[0, 0] - truth[0, 0]), 0.7 * abs(lucky[0, 0] - truth[0, 0]))
        self.assertGreater(smooth[0, 0], lucky[0, 0])
        np.testing.assert_allclose(smooth[1:, 0], truth[1:, 0], rtol=0.15)  # the other 24 positions are shifted a little by the outlier (13 % at most)

    def test_the_scale_of_the_pitch_does_not_change_the_surface(self) -> None:
        fine, wide = rt.Lattice(5, 0.5), rt.Lattice(5, 4.0)
        noise = _plane(fine, lambda dx, dy: 0.05 + 0.01 * dx * dx / 0.25 + 0.002 * dy)[:, None]  # same numbers at the same rows
        a = ob.smooth_noise(fine.offsets(), noise)
        b = ob.smooth_noise(wide.offsets(), noise)
        np.testing.assert_allclose(a, b, rtol=1e-9)

    def test_each_roi_is_fitted_on_its_own(self) -> None:
        col1 = _plane(self.lattice, lambda dx, dy: np.exp(-3.0 + 0.1 * dx * dx))
        col2 = _plane(self.lattice, lambda dx, dy: np.exp(-2.0 + 0.05 * dy * dy))
        both = ob.smooth_noise(self.lattice.offsets(), np.column_stack([col1, col2]))
        np.testing.assert_allclose(both[:, 0], ob.smooth_noise(self.lattice.offsets(), col1[:, None])[:, 0], rtol=1e-12)
        np.testing.assert_allclose(both[:, 1], ob.smooth_noise(self.lattice.offsets(), col2[:, None])[:, 0], rtol=1e-12)

    def test_too_few_positions_give_no_value_not_the_raw_noise(self) -> None:
        one = rt.Lattice(1, 1.0)
        self.assertTrue(np.isnan(ob.smooth_noise(one.offsets(), np.array([[0.05]]))).all())
        three = rt.Lattice(3, 1.0)
        noise = np.full((9, 1), 0.05)
        noise[:4, 0] = np.nan  # only five positions left: fewer than the six coefficients
        self.assertTrue(np.isnan(ob.smooth_noise(three.offsets(), noise)).all())

    def test_positions_on_a_line_do_not_determine_the_surface(self) -> None:
        offsets = np.column_stack([np.arange(-3.0, 4.0), np.zeros(7)])  # seven positions, all dy = 0
        self.assertTrue(np.isnan(ob.smooth_noise(offsets, np.full((7, 1), 0.05))).all())

    def test_a_position_with_no_noise_value_stays_without_one(self) -> None:
        noise = _plane(self.lattice, lambda dx, dy: 0.05 + 0.001 * dx * dx)[:, None]
        noise[7, 0] = np.nan
        smooth = ob.smooth_noise(self.lattice.offsets(), noise)
        self.assertTrue(np.isnan(smooth[7, 0]))
        self.assertEqual(int(np.isfinite(smooth).sum()), 24)

    def test_zero_or_negative_noise_is_not_a_value(self) -> None:
        noise = _plane(self.lattice, lambda dx, dy: 0.05 + 0.001 * dx * dx)[:, None]
        noise[3, 0], noise[4, 0] = 0.0, -1.0
        smooth = ob.smooth_noise(self.lattice.offsets(), noise)
        self.assertTrue(np.isnan(smooth[[3, 4], 0]).all())
        self.assertTrue(np.isfinite(np.delete(smooth, [3, 4], axis=0)).all())

    def test_noise_with_the_wrong_number_of_rows_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            ob.smooth_noise(self.lattice.offsets(), np.ones((3, 1)))


class RankTest(unittest.TestCase):
    lattice = rt.Lattice(5, 1.0)

    def _flat_noise(self, n_rois: int = 1) -> np.ndarray:
        return np.full((self.lattice.n_points, n_rois), 0.05)

    def test_signal_picks_the_planted_maximum_of_the_absolute_delta(self) -> None:
        delta = _plane(self.lattice, lambda dx, dy: 4.0 - 0.05 * ((dx - 1) ** 2 + (dy + 2) ** 2))[:, None]
        ranking = ob.rank(_objectives(self.lattice, delta, self._flat_noise()), "signal")
        np.testing.assert_array_equal(self.lattice.offsets()[ranking.best[0]], [1.0, -2.0])
        self.assertTrue(ranking.moves[0])
        np.testing.assert_array_equal(ranking.best_offsets[0], [1.0, -2.0])

    def test_a_response_that_goes_down_is_ranked_by_its_size(self) -> None:
        delta = -_plane(self.lattice, lambda dx, dy: 4.0 - 0.05 * ((dx + 2) ** 2 + dy**2))[:, None]
        ranking = ob.rank(_objectives(self.lattice, delta, self._flat_noise()), "signal")
        np.testing.assert_array_equal(self.lattice.offsets()[ranking.best[0]], [-2.0, 0.0])

    def test_noise_is_lower_is_better_and_its_improvement_is_positive(self) -> None:
        noise = _plane(self.lattice, lambda dx, dy: 0.05 + 0.002 * ((dx - 1) ** 2 + dy**2))[:, None]
        ranking = ob.rank(_objectives(self.lattice, np.full(noise.shape, 3.0), noise), "noise")
        np.testing.assert_array_equal(self.lattice.offsets()[ranking.best[0]], [1.0, 0.0])
        self.assertFalse(ranking.higher_is_better)
        centre_noise = 0.05 + 0.002
        self.assertAlmostEqual(ranking.improvement[0], (centre_noise - 0.05) / centre_noise, places=12)
        self.assertEqual(ranking.scores[ranking.best[0], 0], 1.0)  # best = 1 even though its value is the smallest

    def test_improvement_of_a_higher_is_better_objective_is_the_relative_gain(self) -> None:
        delta = _plane(self.lattice, lambda dx, dy: 4.0 + (0.2 if (dx, dy) == (2.0, 2.0) else 0.0))[:, None]
        ranking = ob.rank(_objectives(self.lattice, delta, self._flat_noise()), "signal")
        self.assertAlmostEqual(ranking.improvement[0], 0.2 / 4.0, places=12)

    def test_the_raw_snr_follows_a_lucky_noise_the_smoothed_one_does_not(self) -> None:
        noise = _plane(self.lattice, lambda dx, dy: np.exp(-3.0 + 0.1 * ((dx - 1) ** 2 + dy**2)))[:, None]
        lucky = noise.copy()
        fluke = int(np.flatnonzero((self.lattice.offsets() == (1.0, 1.0)).all(axis=1))[0])  # one step from the true minimum at (1, 0)
        lucky[fluke, 0] *= 0.8  # measured 20 % too quiet, well inside the ~13 % scatter a std from 30 cubes has (twice over)
        delta = np.full(noise.shape, 3.0)
        obj = _objectives(self.lattice, delta, lucky)
        raw, smooth = ob.rank(obj, "snr_raw"), ob.rank(obj, "snr_smooth")
        self.assertEqual(int(raw.best[0]), fluke)  # fooled by the one quiet measurement
        np.testing.assert_array_equal(self.lattice.offsets()[smooth.best[0]], [1.0, 0.0])  # the real bowl's minimum

    def test_the_centre_stays_when_nothing_is_strictly_better(self) -> None:
        delta = np.full((self.lattice.n_points, 2), 3.0)
        ranking = ob.rank(_objectives(self.lattice, delta, self._flat_noise(2)), "signal")
        np.testing.assert_array_equal(ranking.best, [self.lattice.centre_index] * 2)
        self.assertFalse(ranking.moves.any())
        np.testing.assert_array_equal(ranking.best_offsets, np.zeros((2, 2)))
        np.testing.assert_array_equal(ranking.improvement, [0.0, 0.0])
        np.testing.assert_array_equal(ranking.scores, np.full(delta.shape, 0.5))  # all positions equal: no best colour

    def test_a_tie_with_the_centre_keeps_the_centre(self) -> None:
        delta = np.full((self.lattice.n_points, 1), 3.0)
        delta[0, 0] = delta[self.lattice.centre_index, 0] = 5.0
        ranking = ob.rank(_objectives(self.lattice, delta, self._flat_noise()), "signal")
        self.assertEqual(int(ranking.best[0]), self.lattice.centre_index)

    def test_a_roi_without_any_value_is_not_ranked_and_not_moved(self) -> None:
        delta = np.full((self.lattice.n_points, 2), 3.0)
        delta[:, 1] = np.nan
        delta[0, 0] = 4.0
        ranking = ob.rank(_objectives(self.lattice, delta, self._flat_noise(2)), "signal")
        self.assertEqual(int(ranking.best[1]), -1)
        self.assertFalse(ranking.moves[1])
        self.assertTrue(np.isnan(ranking.scores[:, 1]).all())
        self.assertTrue(np.isnan(ranking.improvement[1]))
        np.testing.assert_array_equal(ranking.best_offsets[1], [0.0, 0.0])
        self.assertTrue(ranking.moves[0])  # the other ROI is unaffected

    def test_a_missing_centre_value_does_not_stop_the_other_positions_from_winning(self) -> None:
        delta = np.full((self.lattice.n_points, 1), 3.0)
        delta[self.lattice.centre_index, 0] = np.nan
        delta[5, 0] = 3.5
        ranking = ob.rank(_objectives(self.lattice, delta, self._flat_noise()), "signal")
        self.assertEqual(int(ranking.best[0]), 5)
        self.assertTrue(np.isnan(ranking.improvement[0]))  # nothing to compare with

    def test_scores_run_from_0_to_1_inside_each_rois_own_lattice(self) -> None:
        small = _plane(self.lattice, lambda dx, dy: 0.001 * dx + 1.0)
        large = _plane(self.lattice, lambda dx, dy: 100.0 * dy + 1000.0)
        ranking = ob.rank(_objectives(self.lattice, np.column_stack([small, large]), self._flat_noise(2)), "signal")
        for j in range(2):
            self.assertAlmostEqual(float(np.nanmin(ranking.scores[:, j])), 0.0, places=12)
            self.assertAlmostEqual(float(np.nanmax(ranking.scores[:, j])), 1.0, places=12)
        self.assertEqual(ranking.scores[ranking.best[0], 0], 1.0)
        self.assertEqual(ranking.scores[ranking.best[1], 1], 1.0)

    def test_the_rois_are_ranked_independently_of_each_other(self) -> None:
        a = _plane(self.lattice, lambda dx, dy: 4.0 - 0.05 * ((dx - 1) ** 2 + dy**2))
        b = _plane(self.lattice, lambda dx, dy: 4.0 - 0.05 * ((dx + 1) ** 2 + (dy - 2) ** 2))
        together = ob.rank(_objectives(self.lattice, np.column_stack([a, b]), self._flat_noise(2)), "signal")
        alone_a = ob.rank(_objectives(self.lattice, a[:, None], self._flat_noise()), "signal")
        alone_b = ob.rank(_objectives(self.lattice, b[:, None], self._flat_noise()), "signal")
        self.assertEqual([int(together.best[0]), int(together.best[1])], [int(alone_a.best[0]), int(alone_b.best[0])])

    def test_the_ranking_carries_what_the_tab_needs_to_label_and_draw_it(self) -> None:
        delta = _plane(self.lattice, lambda dx, dy: 3.0 + 0.1 * dx)[:, None]
        ranking = ob.rank(_objectives(self.lattice, delta, self._flat_noise()), "signal")
        self.assertEqual((ranking.key, ranking.unit, ranking.roi_ids, ranking.centre), ("signal", "nm", (1,), 12))
        self.assertEqual(ranking.values.shape, ranking.scores.shape)
        np.testing.assert_array_equal(ranking.offsets, self.lattice.offsets())

    def test_the_default_objective_ranks_a_planted_snr_optimum(self) -> None:
        noise = _plane(self.lattice, lambda dx, dy: np.exp(-3.0 + 0.05 * ((dx + 2) ** 2 + (dy - 1) ** 2)))[:, None]
        obj = _objectives(self.lattice, np.full(noise.shape, 3.0), noise)
        ranking = ob.rank(obj)
        self.assertEqual(ranking.key, "snr_smooth")
        np.testing.assert_array_equal(self.lattice.offsets()[ranking.best[0]], [-2.0, 1.0])

    def test_a_one_point_lattice_has_nothing_to_rank_with_the_smoothed_noise(self) -> None:
        one = rt.Lattice(1, 1.0)
        ranking = ob.rank(_objectives(one, np.array([[3.0]]), np.array([[0.05]])))
        self.assertEqual(int(ranking.best[0]), -1)  # no surface from one position: not ranked, not moved
        self.assertFalse(ranking.moves.any())


def _paraboloid(size: int, x0: float, y0: float, *, a: float = 1.0, b: float = 2.0, c: float = 0.8) -> np.ndarray:
    """``[position, 1]``: a peak at lattice coordinates (x0, y0) (in pitches, the centre is 0), with a cross term so that its axes are tilted; larger is better."""
    axis = np.arange(size) - size // 2
    gx, gy = np.meshgrid(axis, axis)
    u, v = gx.ravel() - x0, gy.ravel() - y0
    return (10.0 - (a * u * u + b * v * v + c * u * v))[:, None]


class RefineTest(unittest.TestCase):
    """`refine_best`: a quadratic through the 3 x 3 points around the best lattice point."""

    def best_of(self, oriented: np.ndarray) -> np.ndarray:
        return np.array([int(np.nanargmax(oriented[:, j])) for j in range(oriented.shape[1])])

    def test_a_planted_peak_between_lattice_points_is_found_exactly_whatever_its_tilt(self) -> None:
        for size in (3, 5, 7):
            for x0, y0 in ((0.3, -0.2), (-0.4, 0.45), (0.0, 0.0), (0.1, 0.0)):
                oriented = _paraboloid(size, x0, y0)
                sub = ob.refine_best(oriented, self.best_of(oriented), size, 1.0)
                best = int(self.best_of(oriented)[0])
                lattice_point = np.array([best % size - size // 2, best // size - size // 2], dtype=float)
                with self.subTest(size=size, peak=(x0, y0)):
                    np.testing.assert_allclose(lattice_point + sub[0], [x0, y0], atol=1e-9)

    def test_the_offset_is_in_pixels_so_it_scales_with_the_pitch(self) -> None:
        oriented = _paraboloid(5, 0.3, -0.2)
        sub = ob.refine_best(oriented, self.best_of(oriented), 5, 2.5)
        np.testing.assert_allclose(sub[0], [0.3 * 2.5, -0.2 * 2.5], atol=1e-9)

    def test_a_peak_away_from_the_centre_is_found_from_the_best_point_not_from_the_centre(self) -> None:
        oriented = _paraboloid(5, 1.4, 0.9)  # best lattice point (1, 1)
        best = self.best_of(oriented)
        self.assertEqual(int(best[0]), 3 * 5 + 3)
        np.testing.assert_allclose(ob.refine_best(oriented, best, 5, 1.0)[0], [0.4, -0.1], atol=1e-9)

    def test_a_peak_just_inside_the_edge_is_found_with_the_window_pushed_inward(self) -> None:
        oriented = _paraboloid(5, 1.6, 0.0)  # nearest lattice point is the edge column (+2)
        best = self.best_of(oriented)
        self.assertEqual(int(best[0]) % 5, 4)
        np.testing.assert_allclose(ob.refine_best(oriented, best, 5, 1.0)[0], [-0.4, 0.0], atol=1e-9)

    def test_a_peak_beyond_the_lattice_is_not_extrapolated(self) -> None:
        oriented = _paraboloid(5, 2.4, 0.0)  # the true peak is outside what was measured
        np.testing.assert_array_equal(ob.refine_best(oriented, self.best_of(oriented), 5, 1.0), [[0.0, 0.0]])

    def test_a_surface_without_a_top_keeps_the_lattice_point(self) -> None:
        axis = np.arange(5) - 2
        gx, gy = np.meshgrid(axis, axis)
        ramp = (1.0 * gx.ravel() + 0.5 * gy.ravel())[:, None]  # a plane: best at the corner, nothing to interpolate
        saddle = (gx.ravel() ** 2 - gy.ravel() ** 2 + 0.3 * gx.ravel())[:, None]
        for name, surface in (("ramp", ramp), ("saddle", saddle)):
            with self.subTest(name):
                np.testing.assert_array_equal(ob.refine_best(surface, self.best_of(surface), 5, 1.0), [[0.0, 0.0]])

    def test_a_top_outside_the_window_the_surface_was_fitted_on_is_not_extrapolated_to(self) -> None:
        oriented = _paraboloid(5, 1.3, 0.0)
        centre = np.array([12])  # told that the centre is the best although the peak is 1.3 away: beyond the 3 x 3 window around it
        np.testing.assert_array_equal(ob.refine_best(oriented, centre, 5, 1.0), [[0.0, 0.0]])

    def test_a_top_inside_the_window_is_accepted_even_when_it_is_more_than_half_a_pitch_from_the_best_point(self) -> None:
        oriented = _paraboloid(5, 0.8, 0.0)
        centre = np.array([12])  # a surface tilted against the lattice can make a point other than the nearest the best one: the fit is still exact
        np.testing.assert_allclose(ob.refine_best(oriented, centre, 5, 1.0), [[0.8, 0.0]], atol=1e-9)

    def test_a_missing_value_in_the_window_keeps_the_lattice_point_and_spares_the_other_rois(self) -> None:
        oriented = np.hstack([_paraboloid(5, 0.3, 0.2), _paraboloid(5, -0.3, 0.1)])
        oriented[11, 0] = np.nan  # next to ROI 0's best point
        best = np.array([12, 12])
        sub = ob.refine_best(oriented, best, 5, 1.0)
        np.testing.assert_array_equal(sub[0], [0.0, 0.0])
        np.testing.assert_allclose(sub[1], [-0.3, 0.1], atol=1e-9)

    def test_nothing_to_interpolate_on_a_one_point_lattice_or_for_a_roi_without_a_best(self) -> None:
        np.testing.assert_array_equal(ob.refine_best(np.array([[3.0]]), np.array([0]), 1, 1.0), [[0.0, 0.0]])
        oriented = np.hstack([_paraboloid(5, 0.3, 0.2), _paraboloid(5, 0.3, 0.2)])
        sub = ob.refine_best(oriented, np.array([-1, 12]), 5, 1.0)
        np.testing.assert_array_equal(sub[0], [0.0, 0.0])
        np.testing.assert_allclose(sub[1], [0.3, 0.2], atol=1e-9)

    def test_many_rois_at_once_give_what_each_gives_alone(self) -> None:
        rng = np.random.default_rng(7)
        peaks = rng.uniform(-0.45, 0.45, size=(40, 2))
        oriented = np.hstack([_paraboloid(5, x, y) for x, y in peaks])
        best = self.best_of(oriented)
        together = ob.refine_best(oriented, best, 5, 1.0)
        alone = np.vstack([ob.refine_best(oriented[:, [j]], best[[j]], 5, 1.0) for j in range(40)])
        np.testing.assert_allclose(together, alone, rtol=0, atol=1e-12)  # the same numbers up to the rounding of a matrix product
        # Each ROI: the peak relative to its best lattice point if that is inside the fitted window (a tilted surface can make a neighbouring point the best one), else nothing.
        lattice_points = np.column_stack([best % 5 - 2, best // 5 - 2]).astype(float)
        expected = peaks - lattice_points
        near = np.all(np.abs(expected) <= ob.MAX_REFINE_PITCH, axis=1)
        np.testing.assert_allclose(together[near], expected[near], atol=1e-9)
        np.testing.assert_array_equal(together[~near], 0.0)
        self.assertGreater(int(near.sum()), 35)  # and nearly every peak is found: the rule is not swallowing everything


class RankInterpolateTest(unittest.TestCase):
    lattice = rt.Lattice(5, 1.0)

    def planted(self, peak=(0.4, -0.3), n_rois: int = 1) -> rt.LatticeObjectives:
        delta = _plane(self.lattice, lambda dx, dy: 20.0 - 0.5 * ((dx - peak[0]) ** 2 + (dy - peak[1]) ** 2))  # stays positive: "signal" ranks |delta|
        return _objectives(self.lattice, np.tile(delta[:, None], (1, n_rois)), np.full((self.lattice.n_points, n_rois), 0.05))

    def test_without_interpolation_the_best_is_the_lattice_point_and_a_roi_at_the_centre_stays(self) -> None:
        ranking = ob.rank(self.planted(), "signal")
        self.assertEqual(int(ranking.best[0]), self.lattice.centre_index)
        self.assertFalse(ranking.moves[0])
        self.assertFalse(ranking.refined[0])
        np.testing.assert_array_equal(ranking.best_offsets, [[0.0, 0.0]])

    def test_with_interpolation_the_best_position_is_between_the_lattice_points(self) -> None:
        ranking = ob.rank(self.planted(), "signal", interpolate=True)
        self.assertEqual(int(ranking.best[0]), self.lattice.centre_index)  # the measured best is still the lattice point
        self.assertTrue(ranking.refined[0])
        self.assertTrue(ranking.moves[0], "a ROI whose best point is its centre still moves to the interpolated position")
        np.testing.assert_allclose(ranking.best_offsets[0], [0.4, -0.3], atol=1e-9)
        np.testing.assert_allclose(ranking.subpixel[0], [0.4, -0.3], atol=1e-9)

    def test_the_interpolated_position_is_added_to_a_best_point_that_is_not_the_centre(self) -> None:
        ranking = ob.rank(self.planted(peak=(1.4, 0.9)), "signal", interpolate=True)
        np.testing.assert_allclose(ranking.best_offsets[0], [1.4, 0.9], atol=1e-9)

    def test_the_scores_and_the_improvement_still_describe_the_measured_lattice(self) -> None:
        plain, refined = ob.rank(self.planted(peak=(1.4, 0.9)), "signal"), ob.rank(self.planted(peak=(1.4, 0.9)), "signal", interpolate=True)
        np.testing.assert_array_equal(plain.scores, refined.scores)
        np.testing.assert_array_equal(plain.improvement, refined.improvement)
        np.testing.assert_array_equal(plain.best, refined.best)

    def test_a_shift_smaller_than_the_tenth_of_a_pixel_a_roi_centre_is_stored_in_is_no_move(self) -> None:
        ranking = ob.rank(self.planted(peak=(0.03, -0.02)), "signal", interpolate=True)
        self.assertFalse(ranking.refined[0])
        self.assertFalse(ranking.moves[0])

    def test_a_best_point_on_the_outer_ring_is_flagged_and_is_not_refined(self) -> None:
        ranking = ob.rank(self.planted(peak=(3.0, 0.0)), "signal", interpolate=True)  # the peak is beyond the lattice
        self.assertTrue(ranking.edge[0])
        self.assertFalse(ranking.refined[0])
        np.testing.assert_array_equal(ranking.best_offsets[0], [2.0, 0.0])
        self.assertFalse(ob.rank(self.planted(), "signal").edge[0])

    def test_a_ranking_built_without_the_new_fields_has_none_of_them(self) -> None:
        ranking = ob.rank(self.planted(), "signal")
        bare = ob.Ranking("signal", "nm", True, ranking.roi_ids, ranking.offsets, ranking.centre, ranking.values, ranking.scores, ranking.best)
        self.assertFalse(bare.refined.any() or bare.edge.any())
        np.testing.assert_array_equal(bare.subpixel, [[0.0, 0.0]])


if __name__ == "__main__":
    unittest.main()
