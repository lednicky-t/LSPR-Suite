"""The Voigt fit (`analysis/voigt_fit.py`, `query.fit_voigt`, `trace_fit._voigt_group`, 2026-11-02) and the analysis speed-ups of the same day
(`trace_fit.row_patterns`, the batched "no fit" metric, the Gaussian centroid by trapezoid weights). No Qt, no files.

Pinned: the solver's gradient is exact (finite differences); it reaches the same least-squares optimum as scipy run to tight tolerances; it
recovers the peak of synthetic Voigt, Gaussian (gamma = 0) and Lorentzian spectra; the maximum is the centre's wavelength; a spectrum gives the same
result alone and in a batch; the Sensorgram's batch path gives the same trace as the one-cube-at-a-time reference (as for the other fits); the
metric range's baseline is the fitted offset. And: `row_patterns` equals `np.unique(axis=0)`, the batched "no fit" metric equals the per-cube one."""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import voigt_fit as vf
    from lspr_imaging_app.analysis.metric_range import curve_base
    from lspr_imaging_app.analysis.query import fit_spectrum, formula_spectrum
    from lspr_imaging_app.analysis.settings import FIT_METHODS, MetricSettings
    from lspr_imaging_app.analysis.trace_fit import row_patterns
    from tests.unit.test_lspri_analysis_trace_fit import assert_same, make_input
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
from scipy.optimize import least_squares  # noqa: E402
from scipy.special import wofz  # noqa: E402

WL = np.linspace(470.0, 720.0, 26)
NOISE = 0.002


def voigt_spectra(n: int, seed: int = 3, sigma=(0.04, 0.10), gamma=(0.02, 0.10)) -> tuple[np.ndarray, np.ndarray]:
    """``n`` noisy spectra of known Voigt peaks of height ~0.4-0.8 near 2.0-2.2 eV: ``(true params, spectra)``."""
    rng = np.random.default_rng(seed)
    s = rng.uniform(*sigma, n)
    g = rng.uniform(*gamma, n)
    height = rng.uniform(0.4, 0.8, n) / wofz(1j * g / (np.sqrt(2.0) * s)).real
    true = np.stack([height, rng.uniform(2.0, 2.2, n), s, g, rng.uniform(0.0, 0.2, n)], axis=1)
    return true, vf.model(WL, true) + rng.normal(0.0, NOISE, (n, WL.size))


class SolverTests(unittest.TestCase):
    def test_the_gradient_is_exact(self) -> None:
        _true, y = voigt_spectra(1)
        energy = vf.energy_ev(WL)
        theta = np.array([2.08, 0.07, 0.05])

        def cost(t: np.ndarray) -> float:
            return float(vf._terms(energy, y, t[0:1], t[1:2], t[2:3])[0][0])

        gradient = vf._terms(energy, y, theta[0:1], theta[1:2], theta[2:3])[1][0]
        numeric = np.array([(cost(theta + h) - cost(theta - h)) / 2e-7 for h in np.eye(3) * 1e-7])
        np.testing.assert_allclose(gradient, numeric, rtol=1e-5, atol=1e-9)

    def test_it_reaches_the_least_squares_optimum(self) -> None:
        """Against scipy's least_squares started at our answer and run to tight tolerances: it must not find a better point."""
        _true, y = voigt_spectra(25)
        params, ok = vf.solve_voigt(WL, y)
        self.assertTrue(ok.all())
        energy = vf.energy_ev(WL)
        for i in range(y.shape[0]):
            result = least_squares(
                lambda p: p[0] * vf.profile(energy, p[1:2], np.abs(p[2:3]), np.abs(p[3:4]))[0] + p[4] - y[i],
                params[i], xtol=1e-15, ftol=1e-15, gtol=1e-15,
            )
            best = result.x.copy()
            best[2:4] = np.abs(best[2:4])
            self.assertLess(abs(vf.peak_wavelength_nm(best)[0] - vf.peak_wavelength_nm(params[i])[0]), 1e-6)
            ours = 0.5 * np.sum((vf.model(WL, params[i])[0] - y[i]) ** 2)
            self.assertLessEqual(ours, result.cost * (1.0 + 1e-9))

    def test_it_recovers_the_peak_of_voigt_gaussian_and_lorentzian_spectra(self) -> None:
        for label, sigma, gamma in (("voigt", (0.04, 0.10), (0.02, 0.10)), ("gaussian", (0.05, 0.08), (0.0, 0.0)), ("lorentzian", (1e-4, 1e-4), (0.04, 0.08))):
            true, y = voigt_spectra(200, seed=7, sigma=sigma, gamma=gamma)
            params, ok = vf.solve_voigt(WL, y)
            self.assertGreater(ok.mean(), 0.99, label)
            error = np.abs(vf.peak_wavelength_nm(params[ok]) - vf.peak_wavelength_nm(true[ok]))
            self.assertLess(float(np.median(error)), 0.3, label)  # nm, at this noise (NOISE on a peak of ~0.6)
            self.assertTrue(np.all(params[ok, 2] > 0.0) and np.all(params[ok, 3] >= 0.0), label)

    def test_a_spectrum_gives_the_same_result_alone_and_in_a_batch(self) -> None:
        _true, y = voigt_spectra(12)
        together, ok = vf.solve_voigt(WL, y)
        for i in (0, 5, 11):
            alone, ok_alone = vf.solve_voigt(WL, y[i : i + 1])
            self.assertEqual(bool(ok_alone[0]), bool(ok[i]))
            np.testing.assert_allclose(alone[0], together[i], rtol=1e-12, atol=1e-14)

    def test_the_maximum_is_the_centres_wavelength(self) -> None:
        _true, y = voigt_spectra(1)
        fit = fit_spectrum(formula_spectrum(WL, 10.0 ** (-y[0]), np.ones(WL.size), "absorbance"), "voigt")
        self.assertEqual(fit.coefficients.size, 5)
        self.assertAlmostEqual(fit.peak_wavelength_nm, float(vf.peak_wavelength_nm(fit.coefficients)[0]), places=9)
        self.assertGreaterEqual(fit.peak_value + 1e-9, float(np.max(fit.fitted_values)))

    def test_fewer_than_five_points_are_no_fit(self) -> None:
        fit = fit_spectrum(formula_spectrum(WL[:4], np.full(4, 0.5), np.ones(4), "absorbance"), "voigt")
        self.assertEqual(fit.coefficients.size, 0)
        self.assertIsNone(fit.peak_wavelength_nm)

    def test_the_metric_range_baseline_is_the_fitted_offset(self) -> None:
        coefficients = np.array([[1.0, 2.1, 0.05, 0.02, 0.123]])
        self.assertEqual(float(curve_base("voigt", coefficients, np.zeros((1, 3)))[0]), 0.123)

    def test_it_is_one_of_the_fit_methods(self) -> None:
        self.assertIn("voigt", FIT_METHODS)


class SensorgramPathTests(unittest.TestCase):
    """The batch path (`trace_fit`) against the one-cube-at-a-time reference (`query.fit_and_metric`), as for the other fits."""

    def test_voigt_batch_equals_per_cube(self) -> None:
        data = make_input(n_cube=12, seed=4, holes={3: [0, 1], 7: [25]})
        for key in ("maximum", "centroid"):
            for window in ({}, {"fit_wl_min": 520.0, "fit_wl_max": 680.0}):
                assert_same(self, data, "absorbance", MetricSettings(fit_method="voigt", metric_key=key, **window))

    def test_the_no_fit_metric_batch_equals_per_cube(self) -> None:
        data = make_input(n_cube=15, seed=9, holes={2: [0, 1, 2], 9: list(range(3, 26))}, shuffle=True)  # cube 9: 3 points left
        for key in ("maximum", "centroid"):
            for window in ({}, {"fit_wl_min": 540.0, "fit_wl_max": 660.0}, {"window_mode": "around_max", "window_on": True, "window_half_width_nm": 20.0}):
                batch = assert_same(self, data, "absorbance", MetricSettings(fit_method="none", metric_key=key, **window))
                self.assertTrue(np.isfinite(batch).any())


class SpectraBatchTests(unittest.TestCase):
    """`scan_view.fit_spectra` solves the Gaussian / Voigt fits of all its spectra in one call (2026-11-03): the spectra must be exactly the ones the
    one-spectrum-at-a-time path gives (measured on the real scan: 0.0 difference, 10x faster)."""

    def test_batched_spectra_equal_one_at_a_time(self) -> None:
        from unittest import mock

        from lspr_imaging_app.analysis import scan_view
        from lspr_imaging_app.analysis.scan_view import SpectraRequest, compute_spectra
        from tests.unit.test_lspri_analysis_trace_pool import _stored_scan

        stored = _stored_scan(n_cube=8, n_roi=6)
        for method in ("gaussian", "voigt"):
            for key in ("maximum", "centroid"):
                request = SpectraRequest((1, 2, 3, 4, 5, 6, 99), 6, "absorbance", MetricSettings(fit_method=method, metric_key=key))
                batched = compute_spectra(stored, request)
                with mock.patch.object(scan_view, "_batch_solved", return_value={}):
                    single = compute_spectra(stored, request)
                for a, b in zip(batched, single):
                    self.assertEqual((a.roi_id, a.note, a.metric_wavelength_nm, a.metric_value), (b.roi_id, b.note, b.metric_wavelength_nm, b.metric_value))
                    if b.fit_values is not None:
                        np.testing.assert_array_equal(a.fit_values, b.fit_values)


class RowPatternTests(unittest.TestCase):
    def test_the_same_as_np_unique_on_rows(self) -> None:
        rng = np.random.default_rng(1)
        for n_cols in (1, 7, 8, 9, 26, 63, 64, 65, 100):
            for share in (0.02, 0.5, 0.97):
                mask = rng.random((300, n_cols)) < share
                mask[::7] = mask[3]
                expected, inverse = np.unique(mask, axis=0, return_inverse=True)
                patterns, got = row_patterns(mask)
                np.testing.assert_array_equal(patterns, expected)
                np.testing.assert_array_equal(got, np.ravel(inverse))


if __name__ == "__main__":
    unittest.main()
