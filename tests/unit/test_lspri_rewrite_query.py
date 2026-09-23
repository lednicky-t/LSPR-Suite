"""Pure-logic tests for the rewrite's query layer - Pillar I layers 2-3 and
Pillar II (`apps/LSPRi/eva/docs/analysis_pipeline_layers.md`).

**These only run when the `apps/LSPRi/eva` submodule is checked out on its
`rewrite` branch** - see `test_lspri_rewrite_analysis_core.py`'s docstring.

No Qt, no files. The engine-level half of this layer - the cache, the
background trace path, and the claim that changing a formula recomputes
nothing - is in `tests/integration/test_lspri_rewrite_analysis_engine.py`,
since it needs a real store.

Most of this is a verbatim port of code that has been in production use, so
what is pinned here is chosen accordingly: the arithmetic of each formula
(against values worked out by hand, not copied from the implementation),
the clamps and fallbacks that exist because of a past bug, and the ordering
rules that are easy to get subtly wrong and impossible to see in a plot.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.query import (
        FORMULA_KEYS,
        fit_gaussian,
        fit_polynomial,
        fit_spectrum,
        formula_spectrum,
        formula_values,
        metric_value,
    )
    from lspr_imaging_app.analysis.settings import MetricSettings, StatisticsSettings
    from lspr_imaging_app.analysis.statistics import (
        aggregate_traces,
        apply_statistics,
        normalize_to_baseline_window,
        reject_spikes_hampel,
        smooth_moving_average,
    )
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402


class FormulaTest(unittest.TestCase):
    def test_each_formula_matches_its_definition(self) -> None:
        """Expected values worked out by hand from the definitions, not read
        back out of the implementation - otherwise this pins whatever the
        code happens to do, including a sign error."""
        sample = np.asarray([25.0])
        reference = np.asarray([100.0])
        self.assertAlmostEqual(float(formula_values(sample, reference, "ratio")[0]), 0.25)
        self.assertAlmostEqual(float(formula_values(sample, reference, "relative_change")[0]), 0.75)
        # A = log10(reference/sample) = log10(4)
        self.assertAlmostEqual(float(formula_values(sample, reference, "absorbance")[0]), np.log10(4.0))
        # mOD = -1000 * log10(sample/reference) = +1000 * log10(4)
        self.assertAlmostEqual(float(formula_values(sample, reference, "mod_absorbance")[0]), 1000.0 * np.log10(4.0))

    def test_an_unknown_key_falls_back_to_absorbance(self) -> None:
        """The menu is fixed, and an unrecognised key must produce the
        documented default rather than a NaN that looks like missing data."""
        values = formula_values(np.asarray([25.0]), np.asarray([100.0]), "not_a_formula")
        self.assertAlmostEqual(float(values[0]), np.log10(4.0))

    def test_nonpositive_values_are_floored_not_infinite(self) -> None:
        """A reduced value of 0 (or below, after an over-subtracted
        background) is not physically meaningful. The floor keeps it a large
        finite number instead of letting -inf/NaN propagate silently into a
        fitted peak position."""
        for key in FORMULA_KEYS:
            with self.subTest(formula=key):
                value = float(formula_values(np.asarray([0.0]), np.asarray([100.0]), key)[0])
                self.assertTrue(np.isfinite(value), f"{key} produced {value}")

    def test_a_spectrum_keeps_the_pairs_it_came_from(self) -> None:
        """What lets a formula change be re-projected with no access to the
        store at all."""
        spectrum = formula_spectrum([500.0, 550.0], [25.0, 50.0], [100.0, 100.0], "ratio")
        self.assertEqual(spectrum.formula_key, "ratio")
        np.testing.assert_allclose(spectrum.sample_values, [25.0, 50.0])
        np.testing.assert_allclose(spectrum.values, [0.25, 0.5])


def _peaked_spectrum(center: float = 560.0) -> object:
    """A clean absorbance-like peak, sampled the way a real cube is."""
    wavelengths = np.arange(500.0, 621.0, 10.0)
    values = 0.8 * np.exp(-((wavelengths - center) ** 2) / (2.0 * 18.0**2)) + 0.05
    # formula_spectrum expects reduced pairs; build ones that reproduce
    # `values` under "ratio" so the fit sees exactly this curve.
    return formula_spectrum(wavelengths, values, np.ones_like(values), "ratio")


class FitTest(unittest.TestCase):
    def test_polynomial_finds_a_peak_near_the_real_one(self) -> None:
        fit = fit_polynomial(_peaked_spectrum().wavelengths_nm, _peaked_spectrum().values, poly_order=4)
        self.assertIsNotNone(fit.peak_wavelength_nm)
        self.assertAlmostEqual(fit.peak_wavelength_nm, 560.0, delta=6.0)

    def test_gaussian_recovers_the_centre_it_was_given(self) -> None:
        spectrum = _peaked_spectrum(center=573.0)
        fit = fit_gaussian(spectrum.wavelengths_nm, spectrum.values)
        self.assertIsNotNone(fit.peak_wavelength_nm)
        self.assertAlmostEqual(fit.peak_wavelength_nm, 573.0, delta=1.0)

    def test_polynomial_order_is_capped_at_half_the_points(self) -> None:
        """The cap exists because of a real failure: an order approaching
        the point count interpolates the noise, and the resulting
        oscillation spike at a window edge can outscore the true peak and be
        reported as the metric. With 13 points an order of 50 must not
        produce a peak pinned to the window edge."""
        spectrum = _peaked_spectrum()
        fit = fit_polynomial(spectrum.wavelengths_nm, spectrum.values, poly_order=50)
        self.assertLessEqual(fit.coefficients.size - 1, spectrum.wavelengths_nm.size // 2)
        self.assertNotIn(fit.peak_wavelength_nm, (500.0, 620.0))

    def test_too_few_points_returns_an_empty_fit_rather_than_raising(self) -> None:
        fit = fit_polynomial([500.0], [0.2])
        self.assertIsNone(fit.peak_wavelength_nm)
        self.assertEqual(fit.coefficients.size, 0)

    def test_a_gaussian_that_cannot_converge_returns_an_empty_fit(self) -> None:
        """A flat or hopeless spectrum should cost that one cube its metric,
        not kill the run it is part of."""
        fit = fit_gaussian(np.arange(500.0, 620.0, 10.0), np.full(12, np.nan))
        self.assertIsNone(fit.peak_wavelength_nm)

    def test_the_window_restricts_which_points_are_fitted(self) -> None:
        spectrum = _peaked_spectrum()
        fit = fit_polynomial(spectrum.wavelengths_nm, spectrum.values, wl_min=530.0, wl_max=580.0)
        self.assertGreaterEqual(float(np.min(fit.fitted_wavelengths_nm)), 530.0)
        self.assertLessEqual(float(np.max(fit.fitted_wavelengths_nm)), 580.0)

    def test_fit_method_none_is_a_setting_not_a_failure(self) -> None:
        """`None` from `fit_spectrum` means "no fit was asked for"; an empty
        FitResult means "a fit was asked for and didn't converge". A caller
        that conflates them shows "needs analysis" for a working setting."""
        spectrum = _peaked_spectrum()
        self.assertIsNone(fit_spectrum(spectrum, "none"))
        self.assertIsNotNone(fit_spectrum(spectrum, "polynomial"))


class MetricTest(unittest.TestCase):
    def test_maximum_without_a_fit_reads_the_measured_points(self) -> None:
        spectrum = _peaked_spectrum(center=560.0)
        wavelength, value = metric_value(spectrum, "none", "maximum")
        # Sampled every 10 nm, so the argmax lands on the nearest sample.
        self.assertEqual(wavelength, 560.0)
        self.assertIsNotNone(value)

    def test_a_fit_can_resolve_a_peak_between_samples(self) -> None:
        """The reason fitting exists at all: the true peak is at 565 nm,
        which is not one of the sampled wavelengths, so the unfitted maximum
        cannot report it and the fitted one can."""
        spectrum = _peaked_spectrum(center=565.0)
        unfitted, _ = metric_value(spectrum, "none", "maximum")
        fitted, _ = metric_value(spectrum, "gaussian", "maximum")
        self.assertIn(unfitted, (560.0, 570.0))
        self.assertAlmostEqual(fitted, 565.0, delta=1.0)

    def test_centroid_and_maximum_agree_for_a_symmetric_peak(self) -> None:
        """Not a tautology - they are computed by different routes (argmax
        over candidates vs. an intensity-weighted integral), and for a
        symmetric peak centred in its window they must still land together."""
        spectrum = _peaked_spectrum(center=560.0)
        maximum, _ = metric_value(spectrum, "gaussian", "maximum")
        centroid, _ = metric_value(spectrum, "gaussian", "centroid")
        self.assertAlmostEqual(maximum, centroid, delta=4.0)

    def test_an_unknown_metric_returns_none_rather_than_a_number(self) -> None:
        self.assertEqual(metric_value(_peaked_spectrum(), "polynomial", "nonsense"), (None, None))


class StatisticsTest(unittest.TestCase):
    def _spiky_trace(self) -> tuple[np.ndarray, np.ndarray]:
        x = np.arange(20.0)
        y = np.full(20, 5.0)
        y[10] = 40.0  # one bad frame
        return x, y

    def test_spike_rejection_removes_a_single_bad_frame(self) -> None:
        _x, y = self._spiky_trace()
        cleaned = reject_spikes_hampel(y, window=5, threshold=3.5)
        self.assertAlmostEqual(float(cleaned[10]), 5.0, delta=0.01)

    def test_spikes_are_rejected_before_smoothing_not_after(self) -> None:
        """The documented order, and the reason for it: smoothing first
        spreads one bad frame across a whole window, after which the spike
        filter no longer sees an outlier to reject. Pinned by comparing
        against the wrong order explicitly."""
        x, y = self._spiky_trace()
        settings = StatisticsSettings(
            spike_rejection_enabled=True, spike_rejection_method="hampel",
            smoothing_method="moving_average", smoothing_window=5,
        )
        correct, _ = apply_statistics(x, y, settings)
        wrong_order = reject_spikes_hampel(smooth_moving_average(y, 5), window=5)

        self.assertAlmostEqual(float(correct[10]), 5.0, delta=0.05)
        # The wrong order leaves the spike smeared into its neighbours.
        self.assertGreater(float(np.max(np.abs(wrong_order - 5.0))), 1.0)

    def test_baseline_is_subtracted_from_the_whole_trace(self) -> None:
        x = np.arange(10.0)
        y = np.concatenate([np.full(5, 2.0), np.full(5, 7.0)])
        corrected, baseline = normalize_to_baseline_window(x, y, 0.0, 4.0)
        self.assertAlmostEqual(baseline, 2.0)
        self.assertAlmostEqual(float(corrected[0]), 0.0)
        self.assertAlmostEqual(float(corrected[-1]), 5.0)

    def test_an_empty_baseline_window_is_reported_not_raised(self) -> None:
        """A window that selects nothing is a routine user mistake - it has
        to be visible (NaN baseline) without destroying the trace."""
        x = np.arange(10.0)
        y = np.full(10, 3.0)
        corrected, baseline = normalize_to_baseline_window(x, y, 100.0, 200.0)
        self.assertTrue(np.isnan(baseline))
        np.testing.assert_allclose(corrected, y)

    def test_smoothing_keeps_nan_gaps_as_gaps(self) -> None:
        """A NaN means "this cell isn't analyzed", which smoothing must not
        invent a value for - nor let it blank out a window-sized
        neighbourhood."""
        y = np.full(20, 4.0)
        y[7] = np.nan
        smoothed = smooth_moving_average(y, 5)
        self.assertTrue(np.isnan(smoothed[7]))
        self.assertFalse(np.any(np.isnan(np.delete(smoothed, 7))))

    def test_aggregate_ignores_a_member_missing_one_frame(self) -> None:
        traces = {
            1: np.asarray([1.0, 2.0, 3.0]),
            2: np.asarray([3.0, np.nan, 5.0]),
        }
        center, low, high = aggregate_traces(traces, center="mean", band="sd")
        np.testing.assert_allclose(center, [2.0, 2.0, 4.0])
        # One valid member at index 1 -> zero spread there, not NaN.
        self.assertAlmostEqual(float(low[1]), 2.0)
        self.assertAlmostEqual(float(high[1]), 2.0)

    def test_sem_uses_the_sample_standard_deviation(self) -> None:
        """ddof=1, not 0 - the population std understates the band by ~29%
        at n=2, which is exactly the group size people compare."""
        traces = {1: np.asarray([1.0]), 2: np.asarray([3.0])}
        _center, low, high = aggregate_traces(traces, band="sem")
        # sample sd of [1, 3] is sqrt(2); SEM = sqrt(2)/sqrt(2) = 1.0
        self.assertAlmostEqual(float(high[0] - low[0]), 2.0)

    def test_aggregating_nothing_returns_empty_not_an_error(self) -> None:
        center, low, high = aggregate_traces({})
        self.assertEqual((center.size, low.size, high.size), (0, 0, 0))


class SettingsDefaultsTest(unittest.TestCase):
    def test_defaults_match_the_stable_app(self) -> None:
        """These are what a session written before the analysis block
        existed falls back to, so they have to be the stable app's own
        defaults rather than a fresh opinion."""
        metric = MetricSettings()
        self.assertEqual((metric.fit_method, metric.poly_order, metric.metric_key), ("polynomial", 3, "maximum"))
        statistics = StatisticsSettings()
        self.assertEqual(statistics.smoothing_method, "none")
        self.assertFalse(statistics.spike_rejection_enabled)
        self.assertFalse(statistics.baseline_enabled)


if __name__ == "__main__":
    unittest.main()
