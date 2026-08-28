from __future__ import annotations

import math
import sys
import unittest
import warnings

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.domain.models import FormulaSpectrumResult
from lspr_imaging_app.processing.analysis import (
    FORMULA_KEYS,
    absorbance_from_means,
    fit_polynomial_curve,
    fit_curve_for_method,
    fit_gaussian_curve,
    formula_value,
    formula_values_from_reduced_values,
    metric_value_from_fit,
    metric_value_from_spectrum,
    project_formula_spectrum,
    project_reduction_result,
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


class TestFitPolynomialCurve(unittest.TestCase):
    def _symmetric_parabola(self, center: float = 600.0, half_width: float = 100.0, height: float = 1.0, n: int = 81):
        x = np.linspace(center - half_width, center + half_width, n)
        y = height - ((x - center) / half_width) ** 2
        return x, y

    def test_recovers_peak_and_centroid_of_exact_quadratic(self) -> None:
        x, y = self._symmetric_parabola(center=600.0, half_width=100.0, height=1.0)
        fit = fit_polynomial_curve(x, y, poly_order=2)
        self.assertIsNotNone(fit.peak_wavelength_nm)
        self.assertAlmostEqual(fit.peak_wavelength_nm, 600.0, places=3)
        self.assertAlmostEqual(fit.peak_value, 1.0, places=6)
        # A parabola sampled symmetrically about its own peak has its
        # area-weighted centroid exactly at the peak.
        self.assertIsNotNone(fit.centroid_nm)
        self.assertAlmostEqual(fit.centroid_nm, 600.0, places=2)

    def test_wl_window_excludes_points_outside_range(self) -> None:
        x, y = self._symmetric_parabola(center=600.0, half_width=100.0, height=1.0)
        fit_full = fit_polynomial_curve(x, y, poly_order=2)
        fit_windowed = fit_polynomial_curve(x, y, poly_order=2, wl_min=550.0, wl_max=650.0)
        self.assertAlmostEqual(fit_windowed.fitted_wavelengths_nm.min(), 550.0, places=6)
        self.assertAlmostEqual(fit_windowed.fitted_wavelengths_nm.max(), 650.0, places=6)
        # Still recovers the same peak since the window is symmetric about it.
        self.assertAlmostEqual(fit_windowed.peak_wavelength_nm, fit_full.peak_wavelength_nm, places=2)

    def test_non_finite_samples_are_dropped(self) -> None:
        x, y = self._symmetric_parabola(center=600.0, half_width=100.0, height=1.0)
        y = y.copy()
        y[3] = np.nan
        y[10] = np.inf
        fit = fit_polynomial_curve(x, y, poly_order=2)
        self.assertAlmostEqual(fit.peak_wavelength_nm, 600.0, places=2)

    def test_too_few_points_returns_empty_result(self) -> None:
        fit = fit_polynomial_curve(np.array([500.0]), np.array([0.5]))
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
    def test_poly_key_dispatches_to_fit_polynomial_curve(self) -> None:
        x, y = TestFitPolynomialCurve()._symmetric_parabola()
        via_dispatch = fit_curve_for_method(x, y, "poly", poly_order=2)
        direct = fit_polynomial_curve(x, y, poly_order=2)
        self.assertAlmostEqual(via_dispatch.peak_wavelength_nm, direct.peak_wavelength_nm, places=9)

    def test_gaussian_key_dispatches_to_fit_gaussian_curve(self) -> None:
        x, y = TestFitGaussianCurve()._exact_gaussian(center=620.0, sigma=12.0)
        via_dispatch = fit_curve_for_method(x, y, "gaussian")
        direct = fit_gaussian_curve(x, y)
        self.assertAlmostEqual(via_dispatch.peak_wavelength_nm, direct.peak_wavelength_nm, places=9)

    def test_unknown_key_falls_back_to_poly(self) -> None:
        x, y = TestFitPolynomialCurve()._symmetric_parabola()
        via_dispatch = fit_curve_for_method(x, y, "not_a_real_method", poly_order=2)
        direct = fit_polynomial_curve(x, y, poly_order=2)
        self.assertAlmostEqual(via_dispatch.peak_wavelength_nm, direct.peak_wavelength_nm, places=9)

    def test_repeated_calls_are_bit_identical(self) -> None:
        """Regression test for the assumption behind
        gui/plot_manager.py's compute_spectrum_series_data /
        gui/analysis_worker_mixin.py's _compute_formula_spectrum_result:
        the curve fit used to be computed twice for a single-ROI spectrum
        (once to draw the curve, once again for the metric value) and was
        deduplicated to a single call whose result is reused for both. That
        dedup is only safe if fit_curve_for_method is a pure, deterministic
        function of its inputs - not, say, a solver with an internal random
        seed or floating-point evaluation-order sensitivity. Asserts EXACT
        (not approximate) equality between two independent calls with
        identical inputs, for both the "poly" and "gaussian" fit methods,
        since exact reproduction is the actual claim being relied on."""
        x, y = TestFitPolynomialCurve()._symmetric_parabola()
        poly_a = fit_curve_for_method(x, y, "poly", poly_order=3)
        poly_b = fit_curve_for_method(x, y, "poly", poly_order=3)
        np.testing.assert_array_equal(poly_a.fitted_wavelengths_nm, poly_b.fitted_wavelengths_nm)
        np.testing.assert_array_equal(poly_a.fitted_values, poly_b.fitted_values)
        np.testing.assert_array_equal(poly_a.coefficients, poly_b.coefficients)
        self.assertEqual(poly_a.peak_wavelength_nm, poly_b.peak_wavelength_nm)
        self.assertEqual(poly_a.peak_value, poly_b.peak_value)
        self.assertEqual(poly_a.centroid_nm, poly_b.centroid_nm)

        gx, gy = TestFitGaussianCurve()._exact_gaussian(center=620.0, sigma=12.0)
        gauss_a = fit_curve_for_method(gx, gy, "gaussian")
        gauss_b = fit_curve_for_method(gx, gy, "gaussian")
        np.testing.assert_array_equal(gauss_a.fitted_wavelengths_nm, gauss_b.fitted_wavelengths_nm)
        np.testing.assert_array_equal(gauss_a.fitted_values, gauss_b.fitted_values)
        np.testing.assert_array_equal(gauss_a.coefficients, gauss_b.coefficients)
        self.assertEqual(gauss_a.peak_wavelength_nm, gauss_b.peak_wavelength_nm)
        self.assertEqual(gauss_a.centroid_nm, gauss_b.centroid_nm)

        # And the actual metric-value pipeline built on top, since that's
        # what the GUI code reuses the fit object for.
        metric_a = metric_value_from_fit(poly_a, "maximum")
        metric_b = metric_value_from_fit(poly_b, "maximum")
        self.assertEqual(metric_a, metric_b)


class TestMetricValueFromFit(unittest.TestCase):
    def test_maximum_metric_returns_peak(self) -> None:
        x, y = TestFitPolynomialCurve()._symmetric_parabola()
        fit = fit_polynomial_curve(x, y, poly_order=2)
        value, signal = metric_value_from_fit(fit, "maximum")
        self.assertAlmostEqual(value, fit.peak_wavelength_nm, places=9)
        self.assertAlmostEqual(signal, fit.peak_value, places=9)

    def test_centroid_metric_returns_centroid_and_interpolated_signal(self) -> None:
        x, y = TestFitPolynomialCurve()._symmetric_parabola()
        fit = fit_polynomial_curve(x, y, poly_order=2)
        value, signal = metric_value_from_fit(fit, "centroid")
        self.assertAlmostEqual(value, fit.centroid_nm, places=9)
        expected_signal = float(np.interp(fit.centroid_nm, fit.fitted_wavelengths_nm, fit.fitted_values))
        self.assertAlmostEqual(signal, expected_signal, places=9)

    def test_unknown_metric_key_returns_none_none(self) -> None:
        x, y = TestFitPolynomialCurve()._symmetric_parabola()
        fit = fit_polynomial_curve(x, y, poly_order=2)
        value, signal = metric_value_from_fit(fit, "not_a_real_metric")
        self.assertIsNone(value)
        self.assertIsNone(signal)

    def test_metric_key_is_case_and_whitespace_insensitive(self) -> None:
        x, y = TestFitPolynomialCurve()._symmetric_parabola()
        fit = fit_polynomial_curve(x, y, poly_order=2)
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
        x, y = TestFitPolynomialCurve()._symmetric_parabola(n=401)
        fit = fit_polynomial_curve(x, y, poly_order=2)
        fit_value, _ = metric_value_from_fit(fit, "maximum")
        raw_value, _ = metric_value_from_spectrum(x, y, "maximum")
        self.assertAlmostEqual(raw_value, fit_value, places=1)


class TestFormulaValuesFromMeans(unittest.TestCase):
    """Vectorized counterpart to formula_value, used by
    project_formula_spectrum to re-express an already-reduced spectrum under
    a different formula. Must mirror formula_value exactly - a divergence
    here would mean the "instant formula switch" feature silently produces
    different numbers than a fresh compute would have."""

    def test_matches_scalar_formula_value_elementwise(self) -> None:
        sample = np.array([2.0, 50.0, 0.0, -5.0, 900.0])
        reference = np.array([4.0, 500.0, 0.0, 100.0, 12.0])
        for key in FORMULA_KEYS:
            vectorized = formula_values_from_reduced_values(sample, reference, key)
            scalar = np.array([formula_value(s, r, key) for s, r in zip(sample, reference)])
            np.testing.assert_allclose(vectorized, scalar)

    def test_unknown_key_falls_back_to_absorbance_like_formula_value(self) -> None:
        sample = np.array([50.0])
        reference = np.array([500.0])
        np.testing.assert_allclose(
            formula_values_from_reduced_values(sample, reference, "not_a_real_formula"),
            formula_values_from_reduced_values(sample, reference, "absorbance"),
        )


def _make_roi_result(formula_key: str = "absorbance", sample=2.0, reference=4.0, n: int = 1) -> FormulaSpectrumResult:
    sample_arr = np.full(n, sample, dtype=np.float64) if np.isscalar(sample) else np.asarray(sample, dtype=np.float64)
    reference_arr = np.full(n, reference, dtype=np.float64) if np.isscalar(reference) else np.asarray(reference, dtype=np.float64)
    return FormulaSpectrumResult(
        wavelengths_nm=np.linspace(500.0, 500.0 + 10.0 * (len(sample_arr) - 1), len(sample_arr)),
        formula_values=formula_values_from_reduced_values(sample_arr, reference_arr, formula_key),
        sample_reduced_value=sample_arr,
        reference_reduced_value=reference_arr,
        sample_pixel_count=np.ones(len(sample_arr), dtype=np.int32),
        reference_pixel_count=np.ones(len(sample_arr), dtype=np.int32),
        formula_key=formula_key,
    )


class TestProjectFormulaSpectrum(unittest.TestCase):
    """project_formula_spectrum is the core of the reduction/formula split:
    it must reproduce exactly what a fresh compute under the target formula
    would have produced, purely from the stored sample_reduced_value/
    reference_reduced_value - no pixel access."""

    def test_none_passes_through(self) -> None:
        self.assertIsNone(project_formula_spectrum(None, "ratio"))

    def test_same_formula_returns_identical_object(self) -> None:
        """Identity (assertIs), not just equality: several call sites rely
        on this no-op fast path to avoid needless copying when nothing
        actually changed - see _apply_formula_spectrum_result."""
        result = _make_roi_result(formula_key="absorbance")
        self.assertIs(project_formula_spectrum(result, "absorbance"), result)
        self.assertIs(project_formula_spectrum(result, "  Absorbance  "), result)

    def test_different_formula_matches_fresh_compute(self) -> None:
        sample, reference = 50.0, 500.0
        result = _make_roi_result(formula_key="absorbance", sample=sample, reference=reference)
        for key in FORMULA_KEYS:
            projected = project_formula_spectrum(result, key)
            self.assertEqual(projected.formula_key, key)
            self.assertAlmostEqual(float(projected.formula_values[0]), formula_value(sample, reference, key), places=9)
            # sample_reduced_value/reference_reduced_value are untouched by
            # projection - only the derived formula_values/formula_key change.
            np.testing.assert_array_equal(projected.sample_reduced_value, result.sample_reduced_value)
            np.testing.assert_array_equal(projected.reference_reduced_value, result.reference_reduced_value)

    def test_round_trip_returns_to_original_values(self) -> None:
        result = _make_roi_result(formula_key="absorbance", sample=50.0, reference=500.0)
        round_tripped = project_formula_spectrum(project_formula_spectrum(result, "ratio"), "absorbance")
        np.testing.assert_allclose(round_tripped.formula_values, result.formula_values)

    def test_multi_roi_combined_curve_matches_fresh_multi_roi_compute(self) -> None:
        """Mirrors _formula_spectrum_task's own multi-ROI combination rule
        (per-wavelength mean over finite ROI values) - see
        project_formula_spectrum's docstring. Two ROIs, single wavelength,
        both finite: the projected combined value must equal the mean of
        the two ROIs' own values under the new formula, matching what a
        fresh compute under that formula would produce."""
        roi_a = _make_roi_result(formula_key="absorbance", sample=50.0, reference=500.0)
        roi_b = _make_roi_result(formula_key="absorbance", sample=20.0, reference=200.0)
        combined = FormulaSpectrumResult(
            wavelengths_nm=roi_a.wavelengths_nm,
            formula_values=roi_a.formula_values,  # first-ROI convention, see _combine_roi_formula_spectrum_results
            sample_reduced_value=roi_a.sample_reduced_value,
            reference_reduced_value=roi_a.reference_reduced_value,
            sample_pixel_count=roi_a.sample_pixel_count,
            reference_pixel_count=roi_a.reference_pixel_count,
            formula_key="absorbance",
            area_roi_results={1: roi_a, 2: roi_b},
        )
        projected = project_formula_spectrum(combined, "ratio")
        expected = (formula_value(50.0, 500.0, "ratio") + formula_value(20.0, 200.0, "ratio")) / 2.0
        self.assertAlmostEqual(float(projected.formula_values[0]), expected, places=9)
        self.assertAlmostEqual(float(projected.area_roi_results[1].formula_values[0]), formula_value(50.0, 500.0, "ratio"), places=9)
        self.assertAlmostEqual(float(projected.area_roi_results[2].formula_values[0]), formula_value(20.0, 200.0, "ratio"), places=9)

    def test_nan_roi_excluded_from_combined_mean(self) -> None:
        """A ROI with a NaN sample/reference (e.g. fully masked out this
        cube) yields NaN under every formula - see the docstring's note that
        the finite/NaN pattern is formula-independent - so it must be
        excluded from the combined mean the same way under any formula."""
        roi_a = _make_roi_result(formula_key="absorbance", sample=50.0, reference=500.0)
        roi_b = _make_roi_result(formula_key="absorbance", sample=float("nan"), reference=float("nan"))
        combined = FormulaSpectrumResult(
            wavelengths_nm=roi_a.wavelengths_nm,
            formula_values=roi_a.formula_values,
            sample_reduced_value=roi_a.sample_reduced_value,
            reference_reduced_value=roi_a.reference_reduced_value,
            sample_pixel_count=roi_a.sample_pixel_count,
            reference_pixel_count=roi_a.reference_pixel_count,
            formula_key="absorbance",
            area_roi_results={1: roi_a, 2: roi_b},
        )
        projected = project_formula_spectrum(combined, "ratio")
        self.assertAlmostEqual(float(projected.formula_values[0]), formula_value(50.0, 500.0, "ratio"), places=9)

    def test_all_nan_wavelength_stays_nan_without_warning(self) -> None:
        roi_a = _make_roi_result(formula_key="absorbance", sample=float("nan"), reference=float("nan"))
        roi_b = _make_roi_result(formula_key="absorbance", sample=float("nan"), reference=float("nan"))
        combined = FormulaSpectrumResult(
            wavelengths_nm=roi_a.wavelengths_nm,
            formula_values=roi_a.formula_values,
            sample_reduced_value=roi_a.sample_reduced_value,
            reference_reduced_value=roi_a.reference_reduced_value,
            sample_pixel_count=roi_a.sample_pixel_count,
            reference_pixel_count=roi_a.reference_pixel_count,
            formula_key="absorbance",
            area_roi_results={1: roi_a, 2: roi_b},
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            projected = project_formula_spectrum(combined, "ratio")
        self.assertTrue(math.isnan(float(projected.formula_values[0])))


def _make_roi_result_with_all_methods(
    active_method: str = "mean", formula_key: str = "absorbance", methods: dict | None = None
) -> FormulaSpectrumResult:
    """A ROI result as it would come out of a fresh compute after this
    session's write-through change: `reduced_values_by_method` populated for
    every method actually computed alongside the active one - see
    processing/roi_math.py's reduce_sample_and_reference_all_methods and
    gui/analysis_worker_mixin.py's _write_through_reduced_values_by_method."""
    methods = methods or {"mean": (10.0, 20.0), "median": (11.0, 21.0), "trimmed_mean": (10.5, 20.5), "plane_fit": (9.5, 19.5)}
    reduced_values_by_method = {
        method: (np.asarray([sample]), np.asarray([reference])) for method, (sample, reference) in methods.items()
    }
    sample, reference = methods[active_method]
    return FormulaSpectrumResult(
        wavelengths_nm=np.asarray([500.0]),
        formula_values=formula_values_from_reduced_values(np.asarray([sample]), np.asarray([reference]), formula_key),
        sample_reduced_value=np.asarray([sample]),
        reference_reduced_value=np.asarray([reference]),
        sample_pixel_count=np.asarray([1], dtype=np.int32),
        reference_pixel_count=np.asarray([1], dtype=np.int32),
        reduction_method=active_method,
        formula_key=formula_key,
        reduced_values_by_method=reduced_values_by_method,
    )


class TestProjectReductionResult(unittest.TestCase):
    """project_reduction_result is the Reduction-side counterpart to
    project_formula_spectrum, made possible by fixing Trim % (processing/
    roi_math.py's DEFAULT_TRIMMED_MEAN_FRACTION) so every one of the four
    Reduction methods is always either present in reduced_values_by_method
    (a value actually computed this session) or genuinely absent (never
    computed / a disk-resumed result that only ever persists one method) -
    never present-but-stale, which is exactly what a live Trim % would have
    allowed."""

    def test_none_passes_through(self) -> None:
        self.assertIsNone(project_reduction_result(None, "median", "absorbance"))

    def test_same_method_delegates_to_formula_projection(self) -> None:
        result = _make_roi_result_with_all_methods(active_method="mean", formula_key="absorbance")
        # Same reduction AND formula: identity, same as project_formula_spectrum's own fast path.
        self.assertIs(project_reduction_result(result, "mean", "absorbance"), result)
        # Same reduction, different formula: delegates to project_formula_spectrum.
        projected = project_reduction_result(result, "mean", "ratio")
        self.assertEqual(projected.reduction_method, "mean")
        self.assertEqual(projected.formula_key, "ratio")
        self.assertAlmostEqual(float(projected.formula_values[0]), formula_value(10.0, 20.0, "ratio"), places=9)

    def test_different_method_uses_reduced_values_by_method(self) -> None:
        result = _make_roi_result_with_all_methods(active_method="mean", formula_key="absorbance")
        projected = project_reduction_result(result, "median", "absorbance")
        self.assertIsNotNone(projected)
        self.assertEqual(projected.reduction_method, "median")
        self.assertAlmostEqual(float(projected.sample_reduced_value[0]), 11.0, places=9)
        self.assertAlmostEqual(float(projected.reference_reduced_value[0]), 21.0, places=9)
        self.assertAlmostEqual(float(projected.formula_values[0]), formula_value(11.0, 21.0, "absorbance"), places=9)

    def test_different_method_and_formula_together(self) -> None:
        result = _make_roi_result_with_all_methods(active_method="mean", formula_key="absorbance")
        projected = project_reduction_result(result, "plane_fit", "ratio")
        self.assertEqual(projected.reduction_method, "plane_fit")
        self.assertEqual(projected.formula_key, "ratio")
        self.assertAlmostEqual(float(projected.formula_values[0]), formula_value(9.5, 19.5, "ratio"), places=9)

    def test_missing_method_returns_none(self) -> None:
        """Mirrors a disk-resumed result: only the one saved method's means
        are ever persisted, so reduced_values_by_method holds at most that
        one entry - requesting anything else must miss, not silently return
        a wrong value or crash."""
        result = _make_roi_result_with_all_methods(
            active_method="mean", formula_key="absorbance", methods={"mean": (10.0, 20.0)}
        )
        self.assertIsNone(project_reduction_result(result, "median", "absorbance"))

    def test_recurses_into_area_roi_results(self) -> None:
        roi_a = _make_roi_result_with_all_methods(
            active_method="mean", methods={"mean": (10.0, 20.0), "median": (11.0, 21.0)}
        )
        roi_b = _make_roi_result_with_all_methods(
            active_method="mean", methods={"mean": (30.0, 40.0), "median": (31.0, 41.0)}
        )
        combined = FormulaSpectrumResult(
            wavelengths_nm=roi_a.wavelengths_nm,
            formula_values=roi_a.formula_values,
            sample_reduced_value=roi_a.sample_reduced_value,
            reference_reduced_value=roi_a.reference_reduced_value,
            sample_pixel_count=roi_a.sample_pixel_count,
            reference_pixel_count=roi_a.reference_pixel_count,
            reduction_method="mean",
            formula_key="absorbance",
            area_roi_results={1: roi_a, 2: roi_b},
        )
        projected = project_reduction_result(combined, "median", "absorbance")
        self.assertIsNotNone(projected)
        self.assertEqual(projected.reduction_method, "median")
        self.assertAlmostEqual(float(projected.area_roi_results[1].sample_reduced_value[0]), 11.0, places=9)
        self.assertAlmostEqual(float(projected.area_roi_results[2].sample_reduced_value[0]), 31.0, places=9)
        expected_combined = (formula_value(11.0, 21.0, "absorbance") + formula_value(31.0, 41.0, "absorbance")) / 2.0
        self.assertAlmostEqual(float(projected.formula_values[0]), expected_combined, places=9)

    def test_one_roi_missing_method_causes_overall_miss(self) -> None:
        """All-or-nothing, same contract as _combined_formula_spectrum_
        results_from_ram_or_disk's own all-or-nothing rule: a combined result
        is only usable if EVERY member ROI can supply the requested method."""
        roi_a = _make_roi_result_with_all_methods(
            active_method="mean", methods={"mean": (10.0, 20.0), "median": (11.0, 21.0)}
        )
        roi_b = _make_roi_result_with_all_methods(active_method="mean", methods={"mean": (30.0, 40.0)})
        combined = FormulaSpectrumResult(
            wavelengths_nm=roi_a.wavelengths_nm,
            formula_values=roi_a.formula_values,
            sample_reduced_value=roi_a.sample_reduced_value,
            reference_reduced_value=roi_a.reference_reduced_value,
            sample_pixel_count=roi_a.sample_pixel_count,
            reference_pixel_count=roi_a.reference_pixel_count,
            reduction_method="mean",
            formula_key="absorbance",
            area_roi_results={1: roi_a, 2: roi_b},
        )
        self.assertIsNone(project_reduction_result(combined, "median", "absorbance"))


if __name__ == "__main__":
    unittest.main()
