"""Pure-logic tests for the polynomial-order verdicts (`analysis/order_quality.py`, 2026-10-25): leave-one-out cross-validation in closed form against brute-force refits, the verdict
rules (good / over-fitted / under-fitted, relative to the best order), the cap, missing points and windows, the median over ROIs, and the colour state of the menu
(`OrderQuality.state`). No Qt, no files. Method and measured numbers: `docs/poly_order_quality_study_2026-10-25.md`.

Tolerances: the closed form and a refit are the same least-squares problem (bound 1e-8 relative); a synthetic truth is judged on the share of cubes, not on one noisy spectrum."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import order_quality as oq
    from lspr_imaging_app.analysis.settings import MetricSettings
    from lspr_imaging_app.analysis.trace_fit import TraceInput
    from lspr_imaging_app.progress import Cancelled
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

WL = np.linspace(470.0, 720.0, 26)


def roi_input(absorbance: np.ndarray, *, unscanned=(), holes=None, cube_offset: int = 0) -> TraceInput:
    """One ROI whose cube ``c`` holds the absorbance ``absorbance[c]`` (sample = reference x 10^-A gives it back exactly)."""
    n_cube = absorbance.shape[0]
    reference = np.full(absorbance.shape, 1000.0)
    sample = reference * 10.0 ** (-absorbance)
    usable = np.ones(absorbance.shape, dtype=bool)
    for cube, indices in (holes or {}).items():
        usable[cube, list(indices)] = False
        sample[cube, list(indices)] = np.nan
    scanned = np.ones(n_cube, dtype=bool)
    scanned[list(unscanned)] = False
    return TraceInput(WL.copy(), sample, reference, usable, scanned, np.arange(n_cube) + cube_offset)


def polynomial_spectra(true_order: int, n_cube: int = 40, noise: float = 0.002, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = (2.0 * WL - WL[0] - WL[-1]) / (WL[-1] - WL[0])
    coef = rng.normal(0.0, 0.12, true_order + 1)
    coef[0] += 0.3
    truth = np.polynomial.polynomial.polyval(t, coef)
    return truth[None, :] + rng.normal(0.0, noise, (n_cube, WL.size))


def make_quality(verdict: np.ndarray, cubes=None) -> oq.OrderQuality:
    n_cube = verdict.shape[1]
    return oq.OrderQuality(np.arange(n_cube) if cubes is None else np.asarray(cubes), np.full(verdict.shape, np.nan), verdict.astype(np.int8), np.zeros(n_cube, dtype=int), 1)


class ClosedFormTests(unittest.TestCase):
    def test_the_closed_form_is_the_leave_one_out_of_a_refit(self) -> None:
        rng = np.random.default_rng(3)
        for n in (26, 15, 9):
            x = np.sort(rng.uniform(470.0, 720.0, n))
            y = np.sin(x / 60.0)[:, None] * rng.uniform(0.5, 2.0, 3) + rng.normal(0.0, 0.01, (n, 3))
            got = oq.loo_rms_all_orders(x, y)
            for order in range(1, min(oq.MAX_ORDER, n // 2) + 1):
                for s in range(y.shape[1]):
                    errors = []
                    for i in range(n):
                        keep = np.arange(n) != i
                        fit = np.polynomial.Polynomial.fit(x[keep], y[keep, s], order)
                        errors.append(y[i, s] - fit(x[i]))
                    self.assertAlmostEqual(got[order - 1, s], float(np.sqrt(np.mean(np.square(errors)))), delta=1e-8 * max(1.0, got[order - 1, s]) + 1e-9, msg=f"{n=} {order=}")

    def test_an_order_above_the_cap_has_no_error(self) -> None:
        out = oq.loo_rms_all_orders(WL[:11], np.random.default_rng(0).normal(size=(11, 2)))
        self.assertEqual(out.shape, (oq.MAX_ORDER, 2))
        self.assertTrue(np.all(np.isfinite(out[:5])))  # half of 11 points: 5 terms
        self.assertTrue(np.all(np.isnan(out[5:])))
        self.assertTrue(np.all(np.isnan(oq.loo_rms_all_orders(WL[:3], np.ones((3, 1))))[1:]))

    def test_offset_and_scale_of_the_spectrum_do_not_change_the_verdict_basis(self) -> None:
        y = polynomial_spectra(3, n_cube=4).T
        base = oq.loo_rms_all_orders(WL, y)
        np.testing.assert_allclose(oq.loo_rms_all_orders(WL, y + 5.0), base, rtol=1e-6)  # a constant is inside every polynomial
        np.testing.assert_allclose(oq.loo_rms_all_orders(WL, 7.0 * y), 7.0 * base, rtol=1e-8)

    def test_each_spectrum_is_judged_alone(self) -> None:
        y = polynomial_spectra(4, n_cube=5, seed=5).T
        together = oq.loo_rms_all_orders(WL, y)
        for s in range(y.shape[1]):
            np.testing.assert_allclose(oq.loo_rms_all_orders(WL, y[:, [s]])[:, 0], together[:, s], rtol=1e-9)


class VerdictTests(unittest.TestCase):
    def verdicts(self, absorbance, metric=None, **kw) -> oq.OrderQuality:
        return oq.evaluate_order_quality([roi_input(absorbance, **kw)], "absorbance", metric or MetricSettings())

    def test_the_orders_are_judged_against_the_best_one(self) -> None:
        q = self.verdicts(polynomial_spectra(3, n_cube=60))
        low, mid, high = q.verdict[0], q.verdict[2], q.verdict[12]
        self.assertTrue(np.all(low == oq.UNDER))  # a straight line cannot follow a cubic
        self.assertGreaterEqual(float(np.mean(mid == oq.GOOD)), 0.8)  # the true order is good at (nearly) every cube
        self.assertGreaterEqual(float(np.mean(high == oq.OVER)), 0.8)  # the cap, 13 terms for 26 noisy points: more variance, no gain
        for c in range(q.verdict.shape[1]):  # the verdicts have the shape under ... good ... over: no over-fitted order below a good one, no under-fitted above it
            column = q.verdict[:, c]
            judged = np.flatnonzero(column != oq.UNAVAILABLE)
            seq = column[judged]
            self.assertTrue(np.all(np.diff(np.where(seq == oq.UNDER, 0, np.where(seq == oq.GOOD, 1, 2))) >= 0) or int(q.best_order[c]) > 0)
            best = int(q.best_order[c]) - 1
            self.assertEqual(int(column[best]), oq.GOOD)  # the best order is always good
            self.assertTrue(np.all(column[:best][column[:best] != oq.GOOD] == oq.UNDER))
            self.assertTrue(np.all(column[best + 1 :][column[best + 1 :] != oq.GOOD] == oq.OVER))

    def test_a_peak_in_a_window_has_one_best_order_with_under_below_and_over_above(self) -> None:
        rng = np.random.default_rng(2)
        band = 0.30 * np.exp(-0.5 * ((WL[None, :] - 590.0 - rng.normal(0, 2.0, (50, 1))) / 45.0) ** 2) + rng.normal(0.0, 0.0005, (50, WL.size))
        q = self.verdicts(band, MetricSettings(fit_wl_min=520.0, fit_wl_max=660.0))  # 15 points: orders 1..7
        self.assertTrue(np.all(q.verdict[7:] == oq.UNAVAILABLE))
        self.assertTrue(np.all(q.verdict[:3] == oq.UNDER))  # too few terms for a bell: smooth structure is left
        self.assertGreaterEqual(float(np.mean(q.verdict[3] == oq.UNDER)), 0.9)
        self.assertTrue(np.all(q.best_order >= 5))
        self.assertTrue(np.any(q.verdict[6] == oq.OVER))  # the cap (7 terms for 15 points) is over-fitted at some cubes: the extra terms only add variance
        self.assertTrue(np.all(q.verdict[q.best_order - 1, np.arange(50)] == oq.GOOD))

    def test_a_window_with_few_points_caps_the_orders(self) -> None:
        q = self.verdicts(polynomial_spectra(2, n_cube=5), MetricSettings(fit_wl_min=540.0, fit_wl_max=640.0))  # 11 points: at most 5 terms
        self.assertTrue(np.all(q.verdict[5:] == oq.UNAVAILABLE))
        self.assertTrue(np.all(q.verdict[:5] != oq.UNAVAILABLE))
        self.assertEqual(q.state(6, 2), "na")
        self.assertEqual(q.state(13, 2), "na")

    def test_a_cube_never_scanned_has_no_verdict_and_the_others_are_judged(self) -> None:
        q = self.verdicts(polynomial_spectra(3, n_cube=8), unscanned=(2,))
        self.assertTrue(np.all(q.verdict[:, 2] == oq.UNKNOWN))
        self.assertEqual(int(q.best_order[2]), 0)
        self.assertTrue(np.all(q.verdict[:, 3] != oq.UNKNOWN))
        self.assertIn(q.state(3, 2), ("good", "some"))  # the present cube has nothing to say: the other cubes still colour the order, and it is never amber or purple there

    def test_missing_wavelengths_are_left_out_of_that_cubes_fit(self) -> None:
        a = polynomial_spectra(3, n_cube=6, seed=7)
        holes = {1: (3, 4, 5), 4: tuple(range(18, 26))}  # cube 4 keeps 18 points: the cap is 9 there
        q = self.verdicts(a, holes=holes)
        keep = np.setdiff1d(np.arange(26), list(holes[1]))
        direct = oq.loo_rms_all_orders(WL[keep], a[1, keep][:, None])[:, 0]
        np.testing.assert_allclose(q.loo_rms[:, 1], direct, rtol=1e-7)
        self.assertTrue(np.all(q.verdict[9:, 4] == oq.UNAVAILABLE))
        self.assertTrue(np.all(q.verdict[:9, 4] != oq.UNAVAILABLE))

    def test_the_cube_numbers_are_the_datasets_not_the_rows(self) -> None:
        q = self.verdicts(polynomial_spectra(3, n_cube=6), cube_offset=100)
        self.assertEqual(q.column(103), 3)
        self.assertIsNone(q.column(3))
        self.assertIsNone(q.column(None))
        verdict = int(q.verdict[2, 3])  # the third order at the dataset's cube 103 (row 3)
        expected = {oq.OVER: "over", oq.UNDER: "under"}.get(verdict)
        if expected is None:
            self.assertIn(q.state(3, 103), ("good", "some"))
        else:
            self.assertEqual(q.state(3, 103), expected)
        self.assertIn(q.state(3, 3), ("good", "some"))  # cube 3 is not in the scan (row 3 is cube 103): nothing is judged there, so it is never amber or purple

    def test_the_error_of_a_cube_is_the_median_over_the_rois_and_a_crowd_is_sampled(self) -> None:
        good = [roi_input(polynomial_spectra(3, n_cube=10, seed=s)) for s in (1, 2)]
        odd = roi_input(polynomial_spectra(3, n_cube=10, noise=0.05, seed=3))  # one ROI that is far noisier
        q = oq.evaluate_order_quality([good[0], odd, good[1]], "absorbance", MetricSettings())
        reference = oq.evaluate_order_quality([good[0], good[1]], "absorbance", MetricSettings())
        self.assertEqual(q.n_rois, 3)
        self.assertLess(float(np.nanmax(q.loo_rms[2] / np.maximum(np.nanmax(reference.loo_rms[2]), 1e-12))), 30.0)  # the median keeps the odd ROI from setting the scale
        many = [roi_input(polynomial_spectra(3, n_cube=3, seed=s)) for s in range(oq.MAX_ROIS + 7)]
        self.assertEqual(oq.evaluate_order_quality(many, "absorbance", MetricSettings()).n_rois, oq.MAX_ROIS)

    def test_nothing_to_judge_and_cancel(self) -> None:
        self.assertIsNone(oq.evaluate_order_quality([], "absorbance", MetricSettings()))
        with self.assertRaises(Cancelled):
            oq.evaluate_order_quality([roi_input(polynomial_spectra(3, n_cube=3))], "absorbance", MetricSettings(), cancelled=lambda: True)

    def test_the_best_order_is_always_good_and_its_tolerance_is_25_percent(self) -> None:
        q = self.verdicts(polynomial_spectra(4, n_cube=30, seed=11))
        for c in range(q.verdict.shape[1]):
            column = q.loo_rms[:, c]
            best = int(np.nanargmin(column))
            limit = oq.TOLERANCE * column[best]
            expected_good = np.isfinite(column) & (column <= limit)
            np.testing.assert_array_equal(q.verdict[:, c] == oq.GOOD, expected_good)
        self.assertEqual(oq.TOLERANCE, 1.25)
        self.assertEqual(oq.MAX_ORDER, 13)


class StateTests(unittest.TestCase):
    """The colour of an order for the present cube: green (good here and at 95 % of the cubes), yellow, amber, purple, grey."""

    def setUp(self) -> None:
        g, o, u, na, unk = oq.GOOD, oq.OVER, oq.UNDER, oq.UNAVAILABLE, oq.UNKNOWN
        verdict = np.full((oq.MAX_ORDER, 4), na)
        verdict[0] = (u, u, u, u)  # order 1: under-fitted everywhere
        verdict[1] = (g, g, g, g)  # order 2: good everywhere
        verdict[2] = (g, o, g, g)  # order 3: over-fitted at cube 1
        verdict[3] = (g, g, u, g)  # order 4: under-fitted at cube 2
        verdict[4] = (o, o, o, o)  # order 5: over-fitted everywhere
        verdict[5] = (g, g, unk, g)  # order 6: one cube not judged
        verdict[6] = (unk, unk, unk, unk)  # order 7: nothing judged
        self.q = make_quality(verdict)

    def test_green_if_good_at_every_cube(self) -> None:
        self.assertEqual([self.q.state(2, c) for c in range(4)], ["good"] * 4)
        self.assertEqual(self.q.state(6, 0), "good")  # a cube that was not judged does not spoil it

    def test_green_needs_95_percent_of_the_cubes_good_not_every_single_one(self) -> None:
        g, o, u = oq.GOOD, oq.OVER, oq.UNDER
        row = np.full((oq.MAX_ORDER, 20), oq.UNAVAILABLE, dtype=np.int8)
        row[0] = g
        row[0, 7] = o  # 1 of 20 out: 95 % good
        row[1] = g
        row[1, [7, 12]] = (o, u)  # 2 of 20 out: 90 % good
        row[2] = g
        row[2, 0] = o  # 95 % good, but amber where the present cube is
        q = make_quality(row)
        self.assertEqual(oq.GOOD_SHARE, 0.95)
        self.assertEqual(q.state(1, 0), "good")  # exactly 95 % is enough
        self.assertEqual(q.state(2, 0), "some")  # 90 % is not
        self.assertEqual(q.state(2, 7), "over")
        self.assertEqual(q.state(3, 0), "over")  # the present cube decides amber whatever the share
        self.assertEqual(q.state(3, 5), "good")
        self.assertEqual(q.state(1, 7), "over")

    def test_yellow_if_good_here_but_some_other_cubes_are_out(self) -> None:
        self.assertEqual(self.q.state(3, 0), "some")
        self.assertEqual(self.q.state(3, 3), "some")
        self.assertEqual(self.q.state(4, 0), "some")  # out because under-fitted somewhere else counts too

    def test_amber_for_the_present_cube_over_fitted_and_purple_for_under_fitted(self) -> None:
        self.assertEqual(self.q.state(3, 1), "over")
        self.assertEqual(self.q.state(5, 2), "over")
        self.assertEqual(self.q.state(4, 2), "under")
        self.assertEqual(self.q.state(1, 0), "under")

    def test_grey_when_not_available_or_not_judged(self) -> None:
        self.assertEqual(self.q.state(9, 0), "na")
        self.assertEqual(self.q.state(7, 0), "unknown")
        self.assertEqual(self.q.state(0, 0), "na")
        self.assertEqual(self.q.state(14, 0), "na")

    def test_the_present_cube_decides_amber_and_purple_not_the_others(self) -> None:
        self.assertEqual([self.q.state(3, c) for c in range(4)], ["some", "over", "some", "some"])
        self.assertEqual([self.q.state(4, c) for c in range(4)], ["some", "some", "under", "some"])

    def test_a_cube_outside_the_scan_falls_back_to_the_cubes(self) -> None:
        self.assertEqual(self.q.state(2, 99), "good")
        self.assertEqual(self.q.state(3, 99), "some")
        self.assertEqual(self.q.state(3, None), "some")

    def test_counts(self) -> None:
        self.assertEqual(self.q.counts(3), (4, 1, 0))
        self.assertEqual(self.q.counts(4), (4, 0, 1))
        self.assertEqual(self.q.counts(6), (3, 0, 0))
        self.assertEqual(replace(self.q).counts(9), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
