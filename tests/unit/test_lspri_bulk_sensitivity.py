"""Bulk sensitivity (`lspr_imaging_app.analysis.bulk_sensitivity`): the slope of step means against refractive index and its uncertainty. No Qt.

The uncertainty is checked the only way an uncertainty can be: on synthetic data with a known slope, about 68 % of many runs must land within +/- u of it."""

from __future__ import annotations

import math
import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import numpy as np

    from lspr_imaging_app.analysis import bulk_sensitivity as bs
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

RI = np.array([1.33300, 1.34531, 1.35762, 1.36993, 1.38224])  # water and an EG series, about 0.0123 RIU apart
TRUE_SLOPE = 200.0  # nm/RIU
TRUE_INTERCEPT = 400.0


class ExactLineTest(unittest.TestCase):
    def test_points_on_a_line_give_its_slope_and_intercept_in_both_weightings(self) -> None:
        y = TRUE_SLOPE * RI + TRUE_INTERCEPT
        for weighting in bs.WEIGHTINGS:
            fit = bs.fit_bulk(RI, y, np.full(5, 0.01), weighting=weighting)
            self.assertAlmostEqual(fit.slope, TRUE_SLOPE, places=6, msg=weighting)
            self.assertAlmostEqual(fit.intercept, TRUE_INTERCEPT, places=4, msg=weighting)
            self.assertAlmostEqual(fit.chi2_red, 0.0, places=6)
            self.assertEqual(fit.u, fit.u_data)  # no scatter: the points' own uncertainty, never less
            self.assertTrue(fit.ok)

    def test_the_slope_does_not_depend_on_a_baseline_taken_off(self) -> None:
        y = TRUE_SLOPE * RI + TRUE_INTERCEPT + np.array([0.01, -0.02, 0.0, 0.015, -0.01])
        u = np.array([0.01, 0.02, 0.01, 0.03, 0.01])
        a, b = bs.fit_bulk(RI, y, u), bs.fit_bulk(RI, y - 512.3, u)
        self.assertAlmostEqual(a.slope, b.slope, places=8)
        self.assertAlmostEqual(a.u, b.u, places=10)

    def test_u_data_of_equal_errors_matches_the_textbook_formula(self) -> None:
        s = 0.02
        fit = bs.fit_bulk(RI, TRUE_SLOPE * RI, np.full(5, s))
        expected = s / math.sqrt(float(((RI - RI.mean()) ** 2).sum()))
        self.assertAlmostEqual(fit.u_data, expected, places=10)
        self.assertAlmostEqual(bs.fit_bulk(RI, TRUE_SLOPE * RI, np.full(5, s), weighting=bs.WEIGHT_EQUAL).u_data, expected, places=10)


class EdgeCaseTest(unittest.TestCase):
    def test_one_solution_gives_no_slope_and_says_why(self) -> None:
        fit = bs.fit_bulk([1.333, 1.333, 1.333], [600.0, 600.1, 600.0], [0.01, 0.01, 0.01])
        self.assertFalse(fit.ok)
        self.assertEqual(fit.n_distinct_ri, 1)
        self.assertIn("two solutions", fit.reason)
        self.assertFalse(bs.fit_bulk([], [], []).ok)

    def test_two_points_give_a_slope_without_a_scatter_check(self) -> None:
        fit = bs.fit_bulk([1.333, 1.345], [600.0, 602.4], [0.01, 0.01])
        self.assertAlmostEqual(fit.slope, 2.4 / 0.012, places=6)
        self.assertTrue(math.isnan(fit.chi2_red))
        self.assertEqual(fit.u, fit.u_data)

    def test_a_point_without_a_number_or_an_uncertainty_is_left_out_of_the_weighted_fit(self) -> None:
        y = TRUE_SLOPE * RI
        u = np.array([0.01, np.nan, 0.01, 0.0, 0.01])
        weighted = bs.fit_bulk(RI, y, u)
        self.assertEqual(weighted.n_points, 3)
        equal = bs.fit_bulk(RI, y, u, weighting=bs.WEIGHT_EQUAL)
        self.assertEqual(equal.n_points, 5)  # equal weights need no uncertainty to place the line...
        self.assertTrue(math.isnan(equal.u_data))  # ...but cannot carry a missing one into the slope
        self.assertAlmostEqual(equal.u, 0.0, places=6)  # the scatter about a perfect line
        self.assertEqual(bs.fit_bulk(RI, np.where(np.arange(5) == 2, np.nan, y), np.full(5, 0.01)).n_points, 4)

    def test_an_unknown_weighting_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            bs.fit_bulk(RI, RI, RI, weighting="median")

    def test_weighting_follows_the_point_uncertainty(self) -> None:
        """A point far off the line with a large uncertainty pulls the weighted line less than the equal one."""
        y = TRUE_SLOPE * RI
        y[-1] += 1.0
        u = np.array([0.01, 0.01, 0.01, 0.01, 1.0])
        weighted, equal = bs.fit_bulk(RI, y, u), bs.fit_bulk(RI, y, u, weighting=bs.WEIGHT_EQUAL)
        self.assertLess(abs(weighted.slope - TRUE_SLOPE), abs(equal.slope - TRUE_SLOPE))


class RiUncertaintyTest(unittest.TestCase):
    def test_the_ri_uncertainty_widens_the_slope_uncertainty_by_its_size_in_nm(self) -> None:
        y = TRUE_SLOPE * RI
        sy = np.full(5, 0.01)
        without = bs.fit_bulk(RI, y, sy)
        sx = np.full(5, 5e-5)  # 5e-5 RIU at 200 nm/RIU = 0.01 nm, as large as the y uncertainty
        with_ri = bs.fit_bulk(RI, y, sy, sx)
        self.assertAlmostEqual(with_ri.u_data, without.u_data * math.sqrt(2.0), places=6)
        self.assertAlmostEqual(with_ri.slope, TRUE_SLOPE, places=6)
        self.assertAlmostEqual(bs.fit_bulk(RI, y, sy, np.full(5, np.nan)).u_data, without.u_data, places=12)  # no Std column: no change


class CoverageTest(unittest.TestCase):
    """The uncertainty is honest: over many synthetic experiments, the true slope lies within +/- u in about 68 % of them."""

    RUNS = 4000

    def _coverage(self, *, weighting: str, scale_true_noise: float = 1.0, sigma=None) -> float:
        rng = np.random.default_rng(20261112)
        sigma = np.array([0.01, 0.03, 0.015, 0.02, 0.05]) if sigma is None else sigma
        hits = 0
        for _ in range(self.RUNS):
            y = TRUE_SLOPE * RI + TRUE_INTERCEPT + rng.normal(0.0, sigma * scale_true_noise)
            fit = bs.fit_bulk(RI, y, sigma, weighting=weighting)
            hits += abs(fit.slope - TRUE_SLOPE) <= fit.u
        return hits / self.RUNS

    def test_honest_error_bars_cover_about_68_percent(self) -> None:
        for weighting in bs.WEIGHTINGS:
            # u never shrinks below u_data, so the coverage is a little above 68 % (measured 73 % weighted, 71 % equal)
            coverage = self._coverage(weighting=weighting)
            self.assertGreater(coverage, 0.66, weighting)
            self.assertLess(coverage, 0.78, weighting)

    def test_error_bars_three_times_too_small_are_rescued_by_the_scatter(self) -> None:
        """Real noise 3x what the SEMs say (smoothing, drift): u_data alone would cover ~26 %; the Birge factor brings it back to a reasonable level. With 3 degrees of freedom
        the scatter is itself uncertain, so the coverage is below 68 % (the t factor for 3 d.o.f. is 1.2), but far from 26 %."""
        coverage = self._coverage(weighting=bs.WEIGHT_INVERSE_VARIANCE, scale_true_noise=3.0)
        self.assertGreater(coverage, 0.52)

    def test_the_weighted_slope_is_more_precise_when_the_uncertainties_differ(self) -> None:
        rng = np.random.default_rng(7)
        sigma = np.array([0.005, 0.005, 0.2, 0.005, 0.2])
        weighted, equal = [], []
        for _ in range(2000):
            y = TRUE_SLOPE * RI + rng.normal(0.0, sigma)
            weighted.append(bs.fit_bulk(RI, y, sigma).slope)
            equal.append(bs.fit_bulk(RI, y, sigma, weighting=bs.WEIGHT_EQUAL).slope)
        self.assertLess(np.std(weighted), np.std(equal))


if __name__ == "__main__":
    unittest.main()
