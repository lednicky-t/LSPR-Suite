"""Pure-logic tests for the Gaussian fit (`analysis/gaussian_fit.py`, 2026-10-24; the first choice for every Gaussian fit in the rewrite): the same model
and the exact least-squares optimum, with scipy's `curve_fit` at a tight tolerance used here as the independent reference; the exact gradient and curvature;
a spectrum the solver cannot fit (a window of a handful of points) is fitted by scipy's `curve_fit` as before, so no cube is left without a value.
No Qt, no files."""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import gaussian_fit
    from lspr_imaging_app.analysis.gaussian_fit import solve_gaussian
    from lspr_imaging_app.analysis.query import fit_gaussian
    from lspr_imaging_app.analysis.settings import MetricSettings
    from lspr_imaging_app.analysis.trace_fit import TraceInput, trace_from_input, trace_from_input_per_cube
    from tests.unit.test_lspri_analysis_trace_fit import make_input
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
from scipy.optimize import curve_fit  # noqa: E402

X = np.linspace(470.0, 720.0, 26)
AGREE_NM = 1.0e-3


def bell(x, amplitude, centre, sigma, offset):
    return amplitude * np.exp(-((x - centre) ** 2) / (2.0 * sigma**2)) + offset


def noisy_spectra(n: int = 30, seed: int = 3, noise: float = 0.002) -> np.ndarray:
    rng = np.random.default_rng(seed)
    centres = rng.uniform(540.0, 640.0, n)
    sigmas = rng.uniform(25.0, 70.0, n)
    amplitudes = rng.uniform(0.05, 0.4, n)
    offsets = rng.uniform(0.0, 0.1, n)
    return np.stack([bell(X, a, c, s, o) for a, c, s, o in zip(amplitudes, centres, sigmas, offsets)]) + rng.normal(0.0, noise, (n, X.size))


class SolverTests(unittest.TestCase):
    def test_a_noiseless_bell_is_recovered_in_all_four_parameters(self) -> None:
        y = np.stack([bell(X, 0.3, 580.0, 40.0, 0.05), bell(X, 0.12, 610.5, 25.0, 0.0), bell(X, 0.5, 550.0, 60.0, 0.2)])
        params, ok = solve_gaussian(X, y)
        self.assertTrue(ok.all())
        np.testing.assert_allclose(params, [[0.3, 580.0, 40.0, 0.05], [0.12, 610.5, 25.0, 0.0], [0.5, 550.0, 60.0, 0.2]], rtol=1e-6, atol=1e-7)

    def test_noisy_spectra_reach_the_same_optimum_as_scipy(self) -> None:
        y = noisy_spectra()
        params, ok = solve_gaussian(X, y)
        self.assertTrue(ok.all())
        for row, p in zip(y, params):
            offset = row.min()
            reference = curve_fit(bell, X, row, p0=[row.max() - offset, X[np.argmax(row)], max((X[-1] - X[0]) / 6.0, 1e-3), offset], xtol=1e-15, ftol=1e-15, gtol=0.0, maxfev=100000)[0]
            self.assertAlmostEqual(p[1], reference[1], delta=1e-5)  # centre
            self.assertAlmostEqual(p[2], reference[2], delta=1e-5)  # sigma
            self.assertLessEqual(((bell(X, *p) - row) ** 2).sum(), ((bell(X, *reference) - row) ** 2).sum() * (1.0 + 1e-9))

    def test_the_result_does_not_depend_on_what_else_is_in_the_batch(self) -> None:
        y = noisy_spectra(10)
        alone = np.stack([solve_gaussian(X, row[None, :])[0][0] for row in y])
        together, ok = solve_gaussian(X, y)
        self.assertTrue(ok.all())
        np.testing.assert_allclose(together, alone, rtol=1e-8, atol=1e-10)

    def test_a_dip_gets_a_negative_amplitude_or_is_handed_back(self) -> None:
        y = bell(X, -0.2, 600.0, 40.0, 0.5)[None, :]
        params, ok = solve_gaussian(X, y)
        if ok[0]:  # the first guess sits at the highest point, as in scipy's path; whatever it reaches must fit the data at least as well as the flat line
            self.assertLessEqual(((bell(X, *params[0]) - y[0]) ** 2).sum(), ((y[0] - y[0].mean()) ** 2).sum())

    def test_a_flat_spectrum_has_no_amplitude_or_is_handed_to_scipy(self) -> None:
        params, ok = solve_gaussian(X, np.full((2, X.size), 0.3))  # centre and width are arbitrary: the curve is flat whatever they are
        for row in np.flatnonzero(ok):  # whether it counts as fitted is immaterial (the caller falls back to scipy if not), but never nonsense
            np.testing.assert_allclose(params[row, [0, 3]], [0.0, 0.3], atol=1e-12)

    def test_nan_data_is_not_ok_and_does_not_spoil_the_others(self) -> None:
        y = noisy_spectra(3)
        y[1, 4] = np.nan
        params, ok = solve_gaussian(X, y)
        self.assertEqual(list(ok), [True, False, True])
        self.assertTrue(np.isnan(params[1]).all())

    def test_a_parabola_is_handed_back_not_fitted_with_a_runaway_width(self) -> None:
        x = np.linspace(560.0, 600.0, 9)
        y = (0.5 - 0.0004 * (x - 581.3) ** 2)[None, :] + np.random.default_rng(5).normal(0.0, 1.0e-4, (3, x.size))
        params, ok = solve_gaussian(x, y)  # a bell this narrow a window cannot be told from a parabola: sigma runs away
        span = x[-1] - x[0]
        self.assertTrue(np.all(~ok | (params[:, 2] <= 100.0 * span)))

    def test_sigma_stays_positive(self) -> None:
        params, ok = solve_gaussian(X, noisy_spectra(40, seed=8, noise=0.02))
        self.assertTrue((params[ok, 2] > 0).all())


class CurvatureTests(unittest.TestCase):
    """The exact gradient and curvature of the two-parameter cost (`gaussian_fit._reduced_terms`) against finite differences of the cost itself."""

    def setUp(self) -> None:
        rng = np.random.default_rng(1)
        n = 10
        self.y = noisy_spectra(n, seed=2, noise=0.01)
        self.c = rng.uniform(540.0, 640.0, n)  # arbitrary points, not optima: the curvature must be right everywhere
        self.s = rng.uniform(25.0, 70.0, n)

    def cost(self, c, s):
        bell = np.exp(-((X[None, :] - c[:, None]) ** 2) / (2.0 * s[:, None] ** 2))
        residual = gaussian_fit._linear_part(self.y, bell)[2]
        return 0.5 * np.einsum("ij,ij->i", residual, residual)

    def test_gradient_and_curvature_match_finite_differences(self) -> None:
        _a, _r, _s, gradient, gauss_newton, newton = gaussian_fit._reduced_terms(X, self.y, self.c, self.s)
        h = 1.0e-4
        c, s, f = self.c, self.s, self.cost
        np.testing.assert_allclose(gradient[:, 0], (f(c + h, s) - f(c - h, s)) / (2 * h), atol=1e-9)
        np.testing.assert_allclose(gradient[:, 1], (f(c, s + h) - f(c, s - h)) / (2 * h), atol=1e-9)
        fd_cc = (f(c + h, s) - 2 * f(c, s) + f(c - h, s)) / h**2
        fd_ss = (f(c, s + h) - 2 * f(c, s) + f(c, s - h)) / h**2
        fd_cs = (f(c + h, s + h) - f(c + h, s - h) - f(c - h, s + h) + f(c - h, s - h)) / (4 * h * h)
        for exact, fd in ((newton[0], fd_cc), (newton[1], fd_cs), (newton[2], fd_ss)):
            np.testing.assert_allclose(exact, fd, rtol=5e-3, atol=1e-6)
        self.assertGreater(np.abs(gauss_newton[0] - fd_cc).max(), 10 * np.abs(newton[0] - fd_cc).max())  # the Gauss-Newton matrix leaves the residual out

    def test_newton_and_gauss_newton_reach_the_same_fit_and_newton_needs_fewer_passes(self) -> None:
        y = noisy_spectra(60, seed=11, noise=0.01)  # noise left in the residual, as in real spectra: where Gauss-Newton converges only linearly
        passes = {}
        fits = {}
        for use_newton in (True, False):
            with mock.patch.object(gaussian_fit, "_USE_NEWTON", use_newton), mock.patch.object(gaussian_fit, "_reduced_terms", wraps=gaussian_fit._reduced_terms) as spy:
                fits[use_newton] = solve_gaussian(X, y)
                passes[use_newton] = spy.call_count
        self.assertTrue(fits[True][1].all() and fits[False][1].all())
        np.testing.assert_allclose(fits[True][0][:, 1:3], fits[False][0][:, 1:3], rtol=0.0, atol=2e-5)  # centre and sigma (nm)
        self.assertLess(passes[True], 0.7 * passes[False])


class FitGaussianTests(unittest.TestCase):
    def test_the_fit_has_all_four_parameters_and_the_metric_of_the_exact_optimum(self) -> None:
        row = noisy_spectra(1)[0]
        fit = fit_gaussian(X, row)
        offset = row.min()
        exact = curve_fit(bell, X, row, p0=[row.max() - offset, X[np.argmax(row)], (X[-1] - X[0]) / 6.0, offset], xtol=1e-15, ftol=1e-15, gtol=0.0, maxfev=100000)[0]
        self.assertEqual(fit.coefficients.shape, (4,))  # amplitude, centre, sigma, offset: the curve for the plot needs all of them
        np.testing.assert_allclose(fit.coefficients, exact, rtol=1e-5, atol=1e-6)
        self.assertAlmostEqual(fit.peak_wavelength_nm, exact[1], delta=1e-5)
        np.testing.assert_allclose(fit.fitted_values, bell(fit.fitted_wavelengths_nm, *exact), atol=1e-6)

    def test_a_window_too_narrow_for_a_bell_is_fitted_by_scipy_as_before(self) -> None:
        x = np.linspace(560.0, 600.0, 9)
        y = 0.5 - 0.0004 * (x - 581.3) ** 2  # an exact parabola: a bell never fits it, the width runs away in the solver
        _params, converged = solve_gaussian(x, y[None, :])
        self.assertFalse(converged[0])
        fit = fit_gaussian(x, y)  # ... so scipy fits it
        scipy_only = fit_gaussian(x, y, fast_first=False)
        self.assertIsNotNone(fit.peak_wavelength_nm)
        np.testing.assert_array_equal(fit.coefficients, scipy_only.coefficients)
        self.assertAlmostEqual(fit.peak_wavelength_nm, 581.3, delta=0.1)  # the vertex of the parabola

    def test_too_few_points_and_nan_give_an_empty_fit(self) -> None:
        self.assertIsNone(fit_gaussian(X[:3], np.array([0.1, 0.2, 0.1])).peak_wavelength_nm)
        row = noisy_spectra(1)[0]
        row[3] = np.nan  # a NaN is dropped from the window like any non-finite point; the rest is still fitted
        self.assertIsNotNone(fit_gaussian(X, row).peak_wavelength_nm)


class SettingsTests(unittest.TestCase):
    def test_a_session_that_still_names_the_removed_solver_choice_loads(self) -> None:
        from lspr_imaging_app.storage.session import _decode_settings

        # written by the build of 2026-10-24 that had a Fast / Classic choice: the field is gone, the rest of the block loads
        loaded = _decode_settings({"fit_method": "gaussian", "poly_order": 4, "gaussian_solver": "classic"}, MetricSettings())
        self.assertEqual((loaded.fit_method, loaded.poly_order), ("gaussian", 4))
        self.assertFalse(hasattr(loaded, "gaussian_solver"))


class BatchedTraceTests(unittest.TestCase):
    def _compare(self, data: TraceInput, metric: MetricSettings) -> np.ndarray:
        """The batch against the same cubes one at a time (`fit_gaussian` of each spectrum): a cube must not depend on its batch-mates."""
        batch = trace_from_input(data, "absorbance", metric)
        alone = trace_from_input_per_cube(data, "absorbance", metric)
        np.testing.assert_array_equal(np.isnan(batch), np.isnan(alone), err_msg="gaps differ")
        finite = np.isfinite(alone)
        np.testing.assert_allclose(batch[finite], alone[finite], rtol=0.0, atol=1e-6)
        return batch

    def test_maximum_and_centroid_follow_the_cubes_fitted_alone(self) -> None:
        data = make_input(n_cube=24)
        for key in ("maximum", "centroid"):
            for window in ((None, None), (500.0, 700.0), (540.0, 640.0)):
                with self.subTest(metric=key, window=window):
                    batch = self._compare(data, MetricSettings(fit_method="gaussian", metric_key=key, fit_wl_min=window[0], fit_wl_max=window[1]))
                    self.assertTrue(np.isfinite(batch).any())

    def test_the_maximum_agrees_with_the_exact_scipy_optimum(self) -> None:
        data = make_input(n_cube=8)
        batch = trace_from_input(data, "absorbance", MetricSettings(fit_method="gaussian"))
        values = -np.log10(data.sample / data.reference)
        for cube in range(8):
            row = values[cube]
            offset = row.min()
            exact = curve_fit(bell, X, row, p0=[row.max() - offset, X[np.argmax(row)], (X[-1] - X[0]) / 6.0, offset], xtol=1e-15, ftol=1e-15, gtol=0.0, maxfev=100000)[0]
            self.assertAlmostEqual(batch[cube], float(np.clip(exact[1], X[0], X[-1])), delta=1e-5)

    def test_cubes_with_missing_wavelengths_unscanned_cubes_and_tiny_windows(self) -> None:
        data = make_input(n_cube=12, unscanned=(2,), holes={4: [0, 1, 2], 7: [10], 9: list(range(26))})
        batch = self._compare(data, MetricSettings(fit_method="gaussian"))
        self.assertTrue(np.isnan(batch[[2, 9]]).all())
        for window in ((560.0, 580.0), (560.0, 560.0)):  # three points and one point: fewer than the four parameters, a gap
            with self.subTest(window=window):
                tiny = self._compare(data, MetricSettings(fit_method="gaussian", fit_wl_min=window[0], fit_wl_max=window[1]))
                self.assertTrue(np.isnan(tiny).all())

    def test_a_window_too_narrow_for_a_bell_leaves_no_gap_the_cubes_scipy_can_fit(self) -> None:
        centres = np.linspace(572.0, 590.0, 12)  # parabolas through the top of every cube's band: nine points of the window cannot show a bell
        values = 0.5 - 0.0004 * (X[None, :] - centres[:, None]) ** 2
        data = TraceInput(X, 1000.0 * 10.0 ** (-values), np.full(values.shape, 1000.0), np.ones(values.shape, bool), np.ones(12, bool))
        m = MetricSettings(fit_method="gaussian", fit_wl_min=550.0, fit_wl_max=630.0)  # 9 points for 4 parameters
        batch = self._compare(data, m)
        keep = (X >= 550.0) & (X <= 630.0)
        failed_in_batch = 0
        for cube in range(12):
            _params, converged = solve_gaussian(X[keep], values[cube, keep][None, :])
            if not converged[0]:  # the solver gave up on this cube: the batch must hold what scipy gives for it
                failed_in_batch += 1
                self.assertAlmostEqual(batch[cube], fit_gaussian(X, values[cube], 550.0, 630.0, fast_first=False).peak_wavelength_nm, delta=1e-3)  # an ill-posed fit amplifies rounding differences of the input
        self.assertGreater(failed_in_batch, 0, "the test data no longer exercise the fallback")
        self.assertTrue(np.isfinite(batch).all())


if __name__ == "__main__":
    unittest.main()
