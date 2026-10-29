"""ROI position tuning, the pure part (`lspri_imaging_app.analysis.roi_tuning`): the lattice of offsets, shifted copies of ROIs, the two cube regions and the
objectives, checked against plain numpy. No Qt."""

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

    from lspr_imaging_app.analysis import frame_reduce as fr
    from lspr_imaging_app.analysis import roi_tuning as rt
    from lspr_imaging_app.analysis.settings import StatisticsSettings
    from lspr_imaging_app.roi.model import AreaRoi, RoiMask
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

T0 = 1_700_000_000_000  # unix ms of t = 0 s
PERIOD_S, CUBE_S = 10.0, 6.0  # a cube starts every 10 s and its frames span 6 s
NO_BASELINE = StatisticsSettings(baseline_enabled=False)


def _row(x: float, y: float = 35.0, number: int = 1) -> AreaRoi:
    return AreaRoi(number, x, y, 10.0, reference_inner_diameter_px=14.0, reference_outer_diameter_px=22.0)  # sample r 5 px, ring out r 11 px


def _roi_set(*xs: float) -> fr.RoiSet:
    return fr.build_roi_set([_row(x, number=i + 1) for i, x in enumerate(xs)], 14.0, 22.0)


def _cube_times(cubes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    first = T0 + cubes * PERIOD_S * 1000.0
    return first, first + CUBE_S * 1000.0


class MovedCentreTest(unittest.TestCase):
    """A trial must scan the centre the ROI toolbox will store after the move: both coordinates rounded to 0.1 px (so accepting never stores a centre that was not measured)."""

    def test_a_moved_centre_is_rounded_to_a_tenth_of_a_pixel_like_every_position_edit(self) -> None:
        self.assertEqual(rt.moved_centre(15.04, 35.06, 1.0, 0.0), (16.0, 35.1))
        self.assertEqual(rt.moved_centre(40.6, 20.0, 1.0, -2.0), (41.6, 18.0))

    def test_no_shift_is_no_move_so_the_centre_is_returned_as_it_is(self) -> None:
        self.assertEqual(rt.moved_centre(15.04, 35.06, 0.0, 0.0), (15.04, 35.06))

    def test_the_toolbox_stores_exactly_the_centre_the_trial_scanned(self) -> None:
        from lspr_imaging_app.roi.toolbox import POSITION_DECIMALS

        for x, y, dx, dy in ((40.6, 20.3, 1.0, 1.0), (16.0, 16.0, -0.3, 0.7), (63.9, 12.1, 2.0, -2.0)):
            scanned = rt.moved_centre(x, y, dx, dy)
            stored = (round(float(x + dx), POSITION_DECIMALS), round(float(y + dy), POSITION_DECIMALS))
            self.assertEqual(scanned, stored)

    def test_a_shifted_roi_set_has_the_rounded_centre_and_an_unshifted_one_is_left_alone(self) -> None:
        rs = fr.build_roi_set([_row(15.04), _row(60.0, number=2)], 14.0, 22.0)
        moved = rt.shifted_roi_set(rs, [0], 1.0, 0.0)
        self.assertEqual((moved.rois[0].center_x, moved.rois[0].center_y), rt.moved_centre(15.04, 35.0, 1.0, 0.0))
        self.assertEqual(moved.rois[1].center_x, 60.0)
        same = rt.shifted_roi_set(rs, [0], 0.0, 0.0)
        self.assertEqual(same.rois[0].center_x, 15.04)  # an unmoved ROI is not rounded: it equals the ordinary scan's ROI


class LatticeTest(unittest.TestCase):
    def test_a_three_by_three_lattice_is_the_square_around_the_centre_dx_fastest(self) -> None:
        offsets = rt.Lattice(3, 2.0).offsets()
        expected = [(-2, -2), (0, -2), (2, -2), (-2, 0), (0, 0), (2, 0), (-2, 2), (0, 2), (2, 2)]
        np.testing.assert_array_equal(offsets, np.array(expected, dtype=np.float64))

    def test_the_current_centre_is_the_middle_point_for_every_size(self) -> None:
        for size in (1, 3, 5, 7):
            lattice = rt.Lattice(size, 1.5)
            self.assertEqual(lattice.n_points, size * size)
            np.testing.assert_array_equal(lattice.offsets()[lattice.centre_index], [0.0, 0.0])
            np.testing.assert_allclose(lattice.offsets().sum(axis=0), [0.0, 0.0], atol=1e-12)  # symmetric

    def test_one_point_is_the_current_position_and_reaches_nowhere(self) -> None:
        lattice = rt.Lattice(1, 99.0)
        np.testing.assert_array_equal(lattice.offsets(), [[0.0, 0.0]])
        self.assertEqual(lattice.max_shift_px, 0.0)

    def test_the_longest_move_is_a_corner(self) -> None:
        self.assertAlmostEqual(rt.Lattice(5, 1.5).max_shift_px, np.hypot(3.0, 3.0))
        self.assertAlmostEqual(rt.Lattice(3, 2.0).half_extent_px, 2.0)

    def test_an_even_or_empty_lattice_and_a_bad_pitch_are_refused(self) -> None:
        for size in (0, 2, 4, -3):
            with self.assertRaises(ValueError):
                rt.Lattice(size, 1.0)
        for pitch in (0.0, -1.0, float("nan")):
            with self.assertRaises(ValueError):
                rt.Lattice(3, pitch)


class ShiftedRoiSetTest(unittest.TestCase):
    def test_only_the_chosen_rois_move_by_exactly_dx_dy(self) -> None:
        rs = _roi_set(15.0, 55.0, 95.0)
        moved = rt.shifted_roi_set(rs, [1], 2.0, -2.0)
        np.testing.assert_array_equal(moved.cx, [15.0, 57.0, 95.0])
        np.testing.assert_array_equal(moved.cy, [35.0, 33.0, 35.0])

    def test_the_original_set_and_the_radii_are_untouched(self) -> None:
        rs = _roi_set(15.0, 55.0)
        before = (rs.cx.copy(), rs.cy.copy())
        moved = rt.shifted_roi_set(rs, [0, 1], 3.0, 1.0)
        np.testing.assert_array_equal(rs.cx, before[0])
        np.testing.assert_array_equal(rs.cy, before[1])
        for name in ("sample_r", "ring_in_r", "ring_out_r"):
            np.testing.assert_array_equal(getattr(moved, name), getattr(rs, name))
        self.assertNotEqual(moved.key, rs.key)  # a cached pixel layout of the old set must not be reused

    def test_the_zero_offset_is_the_same_set(self) -> None:
        rs = _roi_set(15.0, 55.0)
        self.assertEqual(rt.shifted_roi_set(rs, [0, 1], 0.0, 0.0).key, rs.key)

    def test_a_free_form_roi_cannot_be_tuned(self) -> None:
        free = AreaRoi(2, 30.0, 10.0, 8.0, sample_geometry_type="mask", sample_mask=RoiMask(26, 6, np.ones((8, 9), dtype=bool)))
        rs = fr.build_roi_set([_row(15.0), free], 14.0, 22.0)
        with self.assertRaises(ValueError):
            rt.shifted_roi_set(rs, [1], 1.0, 0.0)
        rt.shifted_roi_set(rs, [0], 1.0, 0.0)  # the round one is fine


class NeighboursWithinReachTest(unittest.TestCase):
    def test_rois_far_apart_do_not_interact_over_a_small_lattice(self) -> None:
        rs = _roi_set(15.0, 55.0, 95.0)  # 40 px apart; ring r 11 + sample r 5 + margin 2 = 18 px
        self.assertEqual(rt.neighbours_within_reach(rs, [0, 1, 2], rt.Lattice(3, 2.0)), [])

    def test_a_lattice_wide_enough_to_reach_a_neighbour_reports_the_pair(self) -> None:
        rs = _roi_set(15.0, 55.0, 95.0)
        pairs = rt.neighbours_within_reach(rs, [0, 1, 2], rt.Lattice(9, 5.0))  # corner move 28.3 px: 18 + 28.3 > 40
        self.assertIn((0, 1), pairs)
        self.assertIn((1, 0), pairs)
        self.assertIn((1, 2), pairs)
        self.assertNotIn((0, 2), pairs)  # 80 px apart
        self.assertTrue(all(a != k for a, k in pairs))

    def test_only_the_selected_rois_are_asked_about(self) -> None:
        rs = _roi_set(15.0, 55.0, 95.0)
        pairs = rt.neighbours_within_reach(rs, [0], rt.Lattice(9, 5.0))
        self.assertEqual(pairs, [(0, 1)])


class TuningRegionsTest(unittest.TestCase):
    def test_overlapping_or_reversed_regions_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            rt.TuningRegions((2, 32), (30, 40))
        with self.assertRaises(ValueError):
            rt.TuningRegions((10, 5), (20, 30))
        rt.TuningRegions((2, 32), (33, 40))  # neighbours that do not share a cube are fine

    def test_cubes_are_the_union_in_ascending_order(self) -> None:
        self.assertEqual(rt.TuningRegions((2, 4), (201, 202)).cubes(), (2, 3, 4, 201, 202))
        self.assertEqual(rt.TuningRegions((50, 51), (3, 4)).cubes(), (3, 4, 50, 51))

    def test_membership_marks_each_regions_cubes_baseline_row_first(self) -> None:
        cubes = np.array([2, 3, 4, 5, 201, 202, 203])
        first, last = _cube_times(cubes.astype(float))
        m = rt.region_membership(rt.TuningRegions((2, 4), (202, 203)), cubes, first, last)
        np.testing.assert_array_equal(m.used[rt.BASELINE], [1, 1, 1, 0, 0, 0, 0])
        np.testing.assert_array_equal(m.used[rt.SIGNAL], [0, 0, 0, 0, 0, 1, 1])
        np.testing.assert_array_equal(m.n_partial, [0, 0])
        self.assertEqual([s.label for s in m.steps], ["baseline", "signal"])

    def test_the_baseline_row_is_first_even_when_the_signal_comes_earlier_in_time(self) -> None:
        cubes = np.arange(20)
        first, last = _cube_times(cubes.astype(float))
        m = rt.region_membership(rt.TuningRegions((12, 15), (2, 5)), cubes, first, last)
        np.testing.assert_array_equal(np.flatnonzero(m.used[rt.BASELINE]), [12, 13, 14, 15])
        np.testing.assert_array_equal(np.flatnonzero(m.used[rt.SIGNAL]), [2, 3, 4, 5])

    def test_a_cube_without_timing_is_in_no_region(self) -> None:
        cubes = np.arange(6)
        first, last = _cube_times(cubes.astype(float))
        first[2] = last[2] = np.nan
        m = rt.region_membership(rt.TuningRegions((0, 3), (4, 5)), cubes, first, last)
        np.testing.assert_array_equal(m.used[rt.BASELINE], [1, 1, 0, 1, 0, 0])

    def test_mismatched_lengths_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            rt.region_membership(rt.TuningRegions((0, 1), (2, 3)), np.arange(4), np.zeros(3), np.zeros(4))


class LatticeObjectivesTest(unittest.TestCase):
    N_CUBES = 60  # baseline = cubes 0-29, signal = cubes 40-59

    def setUp(self) -> None:
        self.cubes = np.arange(self.N_CUBES)
        self.first, self.last = _cube_times(self.cubes.astype(float))
        self.regions = rt.TuningRegions((0, 29), (40, 59))
        self.membership = rt.region_membership(self.regions, self.cubes, self.first, self.last)
        self.x = self.cubes.astype(np.float64)
        self.t_s = (self.first - self.first[0]) / 1000.0
        self.roi_ids = (7, 9)
        self.offsets = rt.Lattice(3, 2.0).offsets()
        rng = np.random.default_rng(11)
        # a drifting baseline with noise 0.05 nm (ROI 7) and 0.20 nm (ROI 9); the signal region sits 3 nm (ROI 7) / 2 nm (ROI 9) higher
        self.traces = np.empty((len(self.offsets), 2, self.N_CUBES))
        for p in range(len(self.offsets)):
            for i, (noise, jump) in enumerate(((0.05, 3.0), (0.20, 2.0))):
                trace = 600.0 + 0.002 * self.t_s + rng.normal(0.0, noise, self.N_CUBES)
                trace[40:] += jump
                self.traces[p, i] = trace

    def _objectives(self, traces: np.ndarray | None = None, statistics: StatisticsSettings = NO_BASELINE) -> rt.LatticeObjectives:
        return rt.lattice_objectives(self.roi_ids, self.offsets, self.traces if traces is None else traces, self.membership, self.x, statistics)

    def test_the_numbers_match_plain_numpy(self) -> None:
        obj = self._objectives()
        for p in range(len(self.offsets)):
            for i in range(2):
                base, sig = self.traces[p, i, :30], self.traces[p, i, 40:]
                t = self.t_s[:30]
                line = np.polyfit(t, base, 1)
                residual = base - np.polyval(line, t)
                self.assertAlmostEqual(obj.baseline_mean[p, i], base.mean(), places=10)
                self.assertAlmostEqual(obj.signal_mean[p, i], sig.mean(), places=10)
                self.assertAlmostEqual(obj.delta[p, i], sig.mean() - base.mean(), places=10)
                self.assertAlmostEqual(obj.noise[p, i], np.sqrt(np.sum(residual**2) / (30 - 2)), places=9)  # detrended SD, ddof = 2
                self.assertAlmostEqual(obj.snr[p, i], abs(sig.mean() - base.mean()) / obj.noise[p, i], places=8)
                self.assertEqual((obj.n_baseline[p, i], obj.n_signal[p, i]), (30, 20))
                self.assertAlmostEqual(obj.baseline_std[p, i], base.std(ddof=1), places=10)  # one mean +/- std per region
                self.assertAlmostEqual(obj.signal_std[p, i], sig.std(ddof=1), places=10)
                self.assertAlmostEqual(obj.baseline_sem[p, i], base.std(ddof=1) / np.sqrt(30), places=10)
                self.assertAlmostEqual(obj.delta_sem[p, i], np.hypot(base.std(ddof=1) / np.sqrt(30), sig.std(ddof=1) / np.sqrt(20)), places=10)

    def test_the_change_against_the_centre_is_counted_in_units_of_its_uncertainty(self) -> None:
        obj = self._objectives()
        centre = rt.Lattice(3, 2.0).centre_index
        sigma = obj.delta_change_sigma(centre)
        np.testing.assert_array_equal(sigma[centre], 0.0)
        for p in (0, 5, 8):
            for i in range(2):
                expected = (obj.delta[p, i] - obj.delta[centre, i]) / np.hypot(obj.delta_sem[p, i], obj.delta_sem[centre, i])
                self.assertAlmostEqual(sigma[p, i], expected, places=10)

    def test_a_clear_gain_is_many_sigma_and_a_pure_noise_difference_is_not(self) -> None:
        centre = rt.Lattice(3, 2.0).centre_index
        gain = self.traces.copy()
        gain[0, :, 40:] += 1.0  # +1 nm in the signal region at one point; the uncertainty of the difference of two deltas is 0.02 nm (quiet ROI) and 0.08 nm (noisy one)
        sigma = self._objectives(gain).delta_change_sigma(centre)
        self.assertTrue(np.all(sigma[0] > 8.0))
        quiet = self._objectives().delta_change_sigma(centre)  # the traces are independent noise draws with a 3 nm / 2 nm step: differences are chance
        self.assertTrue(np.all(np.abs(quiet) < 4.0))

    def test_a_response_that_goes_down_counts_a_larger_drop_as_a_gain(self) -> None:
        centre = rt.Lattice(3, 2.0).centre_index
        down = self.traces.copy()
        down[:, :, 40:] -= 10.0  # the signal region is now below the baseline
        down[0, :, 40:] -= 1.0  # and at point 0 it drops further
        self.assertTrue(np.all(self._objectives(down).delta_change_sigma(centre)[0] > 8.0))

    def test_the_change_is_nan_without_an_uncertainty(self) -> None:
        centre = rt.Lattice(3, 2.0).centre_index
        flat = np.full_like(self.traces, 600.0)
        flat[:, :, 40:] = 603.0  # no scatter at all: sem is 0, so a difference cannot be measured in sigmas
        self.assertTrue(np.all(np.isnan(self._objectives(flat).delta_change_sigma(centre))))

    def test_the_quieter_roi_has_the_better_signal_to_noise(self) -> None:
        obj = self._objectives()
        self.assertTrue(np.all(obj.snr[:, 0] > obj.snr[:, 1]))

    def test_a_constant_added_to_a_roi_changes_neither_delta_nor_noise(self) -> None:
        plain = self._objectives()
        shifted = self._objectives(self.traces + 25.0)  # baseline subtraction would be exactly this
        np.testing.assert_allclose(shifted.delta, plain.delta, atol=1e-9)
        np.testing.assert_allclose(shifted.noise, plain.noise, atol=1e-9)
        np.testing.assert_allclose(shifted.snr, plain.snr, rtol=1e-9)

    def test_a_signal_that_goes_down_still_has_a_positive_snr_and_a_negative_delta(self) -> None:
        down = self.traces.copy()
        down[:, :, 40:] -= 10.0
        obj = self._objectives(down)
        self.assertTrue(np.all(obj.delta < 0))
        self.assertTrue(np.all(obj.snr > 0))

    def test_a_noise_free_baseline_has_no_snr_instead_of_infinity(self) -> None:
        flat = np.full_like(self.traces, 600.0)
        flat[:, :, 40:] = 603.0
        obj = self._objectives(flat)
        np.testing.assert_allclose(obj.delta, 3.0, atol=1e-9)
        self.assertTrue(np.all(np.isnan(obj.snr)))

    def test_gaps_are_skipped_and_counted_and_an_empty_region_is_nan(self) -> None:
        gappy = self.traces.copy()
        gappy[0, 0, [3, 4, 5]] = np.nan
        gappy[1, 1, 40:] = np.nan
        obj = self._objectives(gappy)
        self.assertEqual(obj.n_baseline[0, 0], 27)
        self.assertEqual(obj.n_signal[1, 1], 0)
        self.assertTrue(np.isnan(obj.signal_mean[1, 1]) and np.isnan(obj.snr[1, 1]))
        self.assertTrue(np.isfinite(obj.snr[0, 0]))

    def test_smoothing_is_done_inside_each_region_so_the_two_levels_never_blend(self) -> None:
        # neighbouring regions with no cube between them: a smoother that ran over the whole trace would blend the jump into both
        regions = rt.TuningRegions((0, 29), (30, 59))
        membership = rt.region_membership(regions, self.cubes, self.first, self.last)
        steady = np.full((len(self.offsets), 2, self.N_CUBES), 600.0)
        steady[:, :, 30:] = 603.0
        smoothing = StatisticsSettings(baseline_enabled=False, smoothing_method="moving_average", smoothing_window=5, smooth_within_steps=True)
        obj = rt.lattice_objectives(self.roi_ids, self.offsets, steady, membership, self.x, smoothing)
        np.testing.assert_allclose(obj.baseline_mean, 600.0, atol=1e-9)
        np.testing.assert_allclose(obj.signal_mean, 603.0, atol=1e-9)

    def test_wrong_shapes_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._objectives(self.traces[:, :1, :])
        with self.assertRaises(ValueError):
            self._objectives(self.traces[:, :, :-1])


if __name__ == "__main__":
    unittest.main()
