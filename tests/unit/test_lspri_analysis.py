from __future__ import annotations

import math
import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.processing.analysis import (
    absorbance_from_means,
    fit_absorbance_curve,
    fit_curve_for_method,
    fit_gaussian_curve,
    formula_value,
    metric_value_from_fit,
    metric_value_from_spectrum,
)


class TestAbsorbanceFromMeans(unittest.TestCase):
    def test_basic_log_ratio(self) -> None:
        # absorbance = log10(reference / sample); reference brighter than sample
        # (sample absorbs light) gives a positive absorbance.
        result = absorbance_from_means(sample_mean=50.0, reference_mean=500.0)
        self.assertAlmostEqual(result, math.log10(500.0 / 50.0), places=9)

    def test_equal_means_gives_zero(self) -> None:
        self.assertAlmostEqual(absorbance_from_means(123.4, 123.4), 0.0, places=9)

    def test_zero_or_negative_inputs_are_clamped_not_raised(self) -> None:
        # Both means are floored at 1e-9 rather than raising / returning inf or nan.
        result = absorbance_from_means(sample_mean=0.0, reference_mean=0.0)
        self.assertTrue(math.isfinite(result))
        self.assertAlmostEqual(result, 0.0, places=6)

        result_negative = absorbance_from_means(sample_mean=-5.0, reference_mean=100.0)
        self.assertTrue(math.isfinite(result_negative))


class TestFormulaValue(unittest.TestCase):
    """formula_value is the predefined-menu dispatch that replaced
    absorbance_from_means's single hardcoded formula (ROI's math panel).
    absorbance_from_means must keep producing bit-for-bit-equivalent output
    after the refactor - existing cached "absorbance" values must not
    silently shift."""

    def test_absorbance_matches_legacy_absorbance_from_means(self) -> None:
        for sample, reference in [(50.0, 500.0), (123.4, 123.4), (1.0, 1.0), (900.0, 12.0)]:
            self.assertEqual(
                formula_value(sample, reference, "absorbance"),
                absorbance_from_means(sample, reference),
            )

    def test_absorbance_default_matches_unknown_key(self) -> None:
        # Falls back to "absorbance" for any key it doesn't recognize, same
        # clamp-not-raise philosophy as the rest of this module.
        self.assertEqual(
            formula_value(50.0, 500.0, "not_a_real_formula"),
            formula_value(50.0, 500.0, "absorbance"),
        )

    def test_ratio(self) -> None:
        self.assertAlmostEqual(formula_value(2.0, 20.0, "ratio"), 0.1, places=9)

    def test_relative_change(self) -> None:
        self.assertAlmostEqual(formula_value(2.0, 20.0, "relative_change"), 0.9, places=9)

    def test_mod_absorbance_is_1000x_absorbance(self) -> None:
        sample, reference = 50.0, 500.0
        absorbance = formula_value(sample, reference, "absorbance")
        mod_absorbance = formula_value(sample, reference, "mod_absorbance")
        self.assertAlmostEqual(mod_absorbance, absorbance * 1000.0, places=6)

    def test_formula_key_is_case_and_whitespace_insensitive(self) -> None:
        self.assertAlmostEqual(
            formula_value(2.0, 20.0, "  RATIO  "),
            formula_value(2.0, 20.0, "ratio"),
            places=9,
        )

    def test_zero_or_negative_inputs_are_clamped_for_every_formula(self) -> None:
        for key in ("absorbance", "ratio", "relative_change", "mod_absorbance"):
            result = formula_value(0.0, 0.0, key)
            self.assertTrue(math.isfinite(result), msg=f"formula={key}")
            result_negative = formula_value(-5.0, 100.0, key)
            self.assertTrue(math.isfinite(result_negative), msg=f"formula={key}")


class TestFitAbsorbanceCurve(unittest.TestCase):
    def _symmetric_parabola(self, center: float = 600.0, half_width: float = 100.0, height: float = 1.0, n: int = 81):
        x = np.linspace(center - half_width, center + half_width, n)
        y = height - ((x - center) / half_width) ** 2
        return x, y

    def test_recovers_peak_and_centroid_of_exact_quadratic(self) -> None:
        x, y = self._symmetric_parabola(center=600.0, half_width=100.0, height=1.0)
        fit = fit_absorbance_curve(x, y, poly_order=2)
        self.assertIsNotNone(fit.peak_wavelength_nm)
        self.assertAlmostEqual(fit.peak_wavelength_nm, 600.0, places=3)
        self.assertAlmostEqual(fit.peak_value, 1.0, places=6)
        # A parabola sampled symmetrically about its own peak has its
        # area-weighted centroid exactly at the peak.
        self.assertIsNotNone(fit.centroid_nm)
        self.assertAlmostEqual(fit.centroid_nm, 600.0, places=2)

    def test_wl_window_excludes_points_outside_range(self) -> None:
        x, y = self._symmetric_parabola(center=600.0, half_width=100.0, height=1.0)
        fit_full = fit_absorbance_curve(x, y, poly_order=2)
        fit_windowed = fit_absorbance_curve(x, y, poly_order=2, wl_min=550.0, wl_max=650.0)
        self.assertAlmostEqual(fit_windowed.fitted_wavelengths_nm.min(), 550.0, places=6)
        self.assertAlmostEqual(fit_windowed.fitted_wavelengths_nm.max(), 650.0, places=6)
        # Still recovers the same peak since the window is symmetric about it.
        self.assertAlmostEqual(fit_windowed.peak_wavelength_nm, fit_full.peak_wavelength_nm, places=2)

    def test_non_finite_samples_are_dropped(self) -> None:
        x, y = self._symmetric_parabola(center=600.0, half_width=100.0, height=1.0)
        y = y.copy()
        y[3] = np.nan
        y[10] = np.inf
        fit = fit_absorbance_curve(x, y, poly_order=2)
        self.assertAlmostEqual(fit.peak_wavelength_nm, 600.0, places=2)

    def test_too_few_points_returns_empty_result(self) -> None:
        fit = fit_absorbance_curve(np.array([500.0]), np.array([0.5]))
        self.assertIsNone(fit.peak_wavelength_nm)
        self.assertIsNone(fit.centroid_nm)
        self.assertIsNone(fit.peak_value)
        self.assertEqual(fit.coefficients.size, 0)


class TestFitGaussianCurve(unittest.TestCase):
    def _exact_gaussian(self, amplitude=1.0, center=600.0, sigma=20.0, offset=0.0, half_width=100.0, n=81):
        x = np.linspace(center - half_width, center + half_width, n)
        y = amplitude * np.exp(-((x - center) ** 2) / (2.0 * sigma * sigma)) + offset
        return x, y

    def test_recovers_center_and_amplitude_of_exact_gaussian(self) -> None:
        x, y = self._exact_gaussian(amplitude=2.0, center=610.0, sigma=15.0, offset=0.1)
        fit = fit_gaussian_curve(x, y)
        self.assertIsNotNone(fit.peak_wavelength_nm)
        self.assertAlmostEqual(fit.peak_wavelength_nm, 610.0, places=2)
        self.assertAlmostEqual(fit.peak_value, 2.1, places=2)

    def test_centroid_matches_center_for_symmetric_peak(self) -> None:
        x, y = self._exact_gaussian(center=550.0, sigma=10.0)
        fit = fit_gaussian_curve(x, y)
        self.assertIsNotNone(fit.centroid_nm)
        self.assertAlmostEqual(fit.centroid_nm, fit.peak_wavelength_nm, places=1)

    def test_coefficients_are_amplitude_center_sigma_offset(self) -> None:
        x, y = self._exact_gaussian(amplitude=3.0, center=500.0, sigma=8.0, offset=0.5)
        fit = fit_gaussian_curve(x, y)
        self.assertEqual(fit.coefficients.size, 4)
        amplitude, center, sigma, offset = fit.coefficients
        self.assertAlmostEqual(amplitude, 3.0, places=1)
        self.assertAlmostEqual(center, 500.0, places=1)
        self.assertAlmostEqual(abs(sigma), 8.0, places=1)
        self.assertAlmostEqual(offset, 0.5, places=1)

    def test_wl_window_excludes_points_outside_range(self) -> None:
        x, y = self._exact_gaussian(center=600.0, sigma=20.0, half_width=150.0, n=151)
        fit = fit_gaussian_curve(x, y, wl_min=550.0, wl_max=650.0)
        self.assertAlmostEqual(fit.fitted_wavelengths_nm.min(), 550.0, places=6)
        self.assertAlmostEqual(fit.fitted_wavelengths_nm.max(), 650.0, places=6)

    def test_too_few_points_returns_empty_result(self) -> None:
        fit = fit_gaussian_curve(np.array([500.0, 510.0, 520.0]), np.array([0.1, 0.5, 0.1]))
        self.assertIsNone(fit.peak_wavelength_nm)
        self.assertIsNone(fit.centroid_nm)
        self.assertIsNone(fit.peak_value)
        self.assertEqual(fit.coefficients.size, 0)

    def test_flat_noisy_data_does_not_crash(self) -> None:
        rng = np.random.default_rng(0)
        x = np.linspace(500.0, 700.0, 41)
        y = rng.normal(scale=1e-6, size=x.size)
        fit = fit_gaussian_curve(x, y)
        # Either converges to something finite, or bails out cleanly - never raises.
        if fit.peak_wavelength_nm is not None:
            self.assertTrue(math.isfinite(fit.peak_wavelength_nm))

    def test_non_finite_samples_are_dropped(self) -> None:
        x, y = self._exact_gaussian(center=600.0, sigma=20.0)
        y = y.copy()
        y[3] = np.nan
        y[10] = np.inf
        fit = fit_gaussian_curve(x, y)
        self.assertIsNotNone(fit.peak_wavelength_nm)
        self.assertAlmostEqual(fit.peak_wavelength_nm, 600.0, places=1)


class TestFitCurveForMethod(unittest.TestCase):
    def test_poly_key_dispatches_to_fit_absorbance_curve(self) -> None:
        x, y = TestFitAbsorbanceCurve()._symmetric_parabola()
        via_dispatch = fit_curve_for_method(x, y, "poly", poly_order=2)
        direct = fit_absorbance_curve(x, y, poly_order=2)
        self.assertAlmostEqual(via_dispatch.peak_wavelength_nm, direct.peak_wavelength_nm, places=9)

    def test_gaussian_key_dispatches_to_fit_gaussian_curve(self) -> None:
        x, y = TestFitGaussianCurve()._exact_gaussian(center=620.0, sigma=12.0)
        via_dispatch = fit_curve_for_method(x, y, "gaussian")
        direct = fit_gaussian_curve(x, y)
        self.assertAlmostEqual(via_dispatch.peak_wavelength_nm, direct.peak_wavelength_nm, places=9)

    def test_unknown_key_falls_back_to_poly(self) -> None:
        x, y = TestFitAbsorbanceCurve()._symmetric_parabola()
        via_dispatch = fit_curve_for_method(x, y, "not_a_real_method", poly_order=2)
        direct = fit_absorbance_curve(x, y, poly_order=2)
        self.assertAlmostEqual(via_dispatch.peak_wavelength_nm, direct.peak_wavelength_nm, places=9)


class TestMetricValueFromFit(unittest.TestCase):
    def test_maximum_metric_returns_peak(self) -> None:
        x, y = TestFitAbsorbanceCurve()._symmetric_parabola()
        fit = fit_absorbance_curve(x, y, poly_order=2)
        value, signal = metric_value_from_fit(fit, "maximum")
        self.assertAlmostEqual(value, fit.peak_wavelength_nm, places=9)
        self.assertAlmostEqual(signal, fit.peak_value, places=9)

    def test_centroid_metric_returns_centroid_and_interpolated_signal(self) -> None:
        x, y = TestFitAbsorbanceCurve()._symmetric_parabola()
        fit = fit_absorbance_curve(x, y, poly_order=2)
        value, signal = metric_value_from_fit(fit, "centroid")
        self.assertAlmostEqual(value, fit.centroid_nm, places=9)
        expected_signal = float(np.interp(fit.centroid_nm, fit.fitted_wavelengths_nm, fit.fitted_values))
        self.assertAlmostEqual(signal, expected_signal, places=9)

    def test_unknown_metric_key_returns_none_none(self) -> None:
        x, y = TestFitAbsorbanceCurve()._symmetric_parabola()
        fit = fit_absorbance_curve(x, y, poly_order=2)
        value, signal = metric_value_from_fit(fit, "not_a_real_metric")
        self.assertIsNone(value)
        self.assertIsNone(signal)

    def test_metric_key_is_case_and_whitespace_insensitive(self) -> None:
        x, y = TestFitAbsorbanceCurve()._symmetric_parabola()
        fit = fit_absorbance_curve(x, y, poly_order=2)
        value, _ = metric_value_from_fit(fit, "  Maximum  ")
        self.assertAlmostEqual(value, fit.peak_wavelength_nm, places=9)


class TestMetricValueFromSpectrum(unittest.TestCase):
    """metric_value_from_spectrum is the "Fitting: None" counterpart to
    metric_value_from_fit - it reads Maximum/Centroid straight off the raw
    absorbance points instead of a fitted curve, so a discrete dataset (not
    fit_absorbance_curve's exact quadratic) is the right fixture here."""

    def _asymmetric_spectrum(self, n: int = 21):
        x = np.linspace(500.0, 700.0, n)
        y = np.zeros_like(x)
        y[10] = 1.0
        y[9] = 0.6
        y[11] = 0.6
        return x, y

    def test_maximum_metric_is_raw_argmax(self) -> None:
        x, y = self._asymmetric_spectrum()
        value, signal = metric_value_from_spectrum(x, y, "maximum")
        self.assertAlmostEqual(value, x[10], places=9)
        self.assertAlmostEqual(signal, 1.0, places=9)

    def test_centroid_metric_is_weighted_average_for_symmetric_data(self) -> None:
        x, y = self._asymmetric_spectrum()
        value, signal = metric_value_from_spectrum(x, y, "centroid")
        # y is symmetric about x[10], so the intensity-weighted centroid
        # lands there too.
        self.assertAlmostEqual(value, x[10], places=6)
        self.assertAlmostEqual(signal, 1.0, places=6)

    def test_wl_window_excludes_points_outside_range(self) -> None:
        x, y = self._asymmetric_spectrum()
        value, _ = metric_value_from_spectrum(x, y, "maximum", wl_min=500.0, wl_max=x[9])
        self.assertAlmostEqual(value, x[9], places=9)

    def test_non_finite_samples_are_dropped(self) -> None:
        x, y = self._asymmetric_spectrum()
        y = y.copy()
        y[10] = np.nan
        value, signal = metric_value_from_spectrum(x, y, "maximum")
        self.assertAlmostEqual(value, x[9], places=9)
        self.assertAlmostEqual(signal, 0.6, places=9)

    def test_unknown_metric_key_returns_none_none(self) -> None:
        x, y = self._asymmetric_spectrum()
        value, signal = metric_value_from_spectrum(x, y, "not_a_real_metric")
        self.assertIsNone(value)
        self.assertIsNone(signal)

    def test_empty_input_returns_none_none(self) -> None:
        value, signal = metric_value_from_spectrum(np.array([]), np.array([]), "maximum")
        self.assertIsNone(value)
        self.assertIsNone(signal)

    def test_agrees_with_fit_based_metric_on_a_smooth_symmetric_peak(self) -> None:
        # Sanity cross-check: on data smooth enough for a quadratic fit to
        # reproduce almost exactly, the fit-free and fit-based metrics should
        # land on essentially the same peak/centroid.
        x, y = TestFitAbsorbanceCurve()._symmetric_parabola(n=401)
        fit = fit_absorbance_curve(x, y, poly_order=2)
        fit_value, _ = metric_value_from_fit(fit, "maximum")
        raw_value, _ = metric_value_from_spectrum(x, y, "maximum")
        self.assertAlmostEqual(raw_value, fit_value, places=1)


if __name__ == "__main__":
    unittest.main()
