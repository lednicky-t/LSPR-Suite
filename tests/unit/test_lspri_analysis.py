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
    metric_value_from_fit,
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
        self.assertAlmostEqual(fit.peak_absorbance, 1.0, places=6)
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
        self.assertIsNone(fit.peak_absorbance)
        self.assertEqual(fit.coefficients.size, 0)


class TestMetricValueFromFit(unittest.TestCase):
    def test_maximum_metric_returns_peak(self) -> None:
        x, y = TestFitAbsorbanceCurve()._symmetric_parabola()
        fit = fit_absorbance_curve(x, y, poly_order=2)
        value, signal = metric_value_from_fit(fit, "maximum")
        self.assertAlmostEqual(value, fit.peak_wavelength_nm, places=9)
        self.assertAlmostEqual(signal, fit.peak_absorbance, places=9)

    def test_centroid_metric_returns_centroid_and_interpolated_signal(self) -> None:
        x, y = TestFitAbsorbanceCurve()._symmetric_parabola()
        fit = fit_absorbance_curve(x, y, poly_order=2)
        value, signal = metric_value_from_fit(fit, "centroid")
        self.assertAlmostEqual(value, fit.centroid_nm, places=9)
        expected_signal = float(np.interp(fit.centroid_nm, fit.fitted_wavelengths_nm, fit.fitted_absorbance))
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


if __name__ == "__main__":
    unittest.main()
