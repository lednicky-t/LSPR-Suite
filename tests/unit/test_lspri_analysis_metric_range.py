"""Pure-logic tests for the metric range (`analysis/metric_range.py`, 2026-10-25): the centroid read from part of the fitted curve, with the baseline
taken out. The batch fit (`trace_fit`) and the one-spectrum route must give the same number. No Qt, no files."""

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
    from lspr_imaging_app.analysis import metric_range
    from lspr_imaging_app.analysis.query import fit_and_metric, formula_spectrum
    from lspr_imaging_app.analysis.scan_trace import trace_key
    from lspr_imaging_app.analysis.settings import MetricSettings
    from lspr_imaging_app.analysis.trace_fit import TraceInput, trace_from_input, trace_from_input_per_cube
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

WL = np.linspace(470.0, 720.0, 26)
GRID = np.linspace(470.0, 720.0, 501)


def bell(x: np.ndarray, centre: float, sigma: float = 40.0, offset: float = 0.1, height: float = 0.25, skew: float = 0.0) -> np.ndarray:
    """A bell; ``skew`` > 0 widens the long-wavelength side."""
    width = np.where(x > centre, sigma * (1.0 + skew), sigma)
    return offset + height * np.exp(-0.5 * ((x - centre) / width) ** 2)


def top(f: float = 0.3, **kw) -> MetricSettings:
    return MetricSettings(metric_key="centroid", metric_range="top_fraction", metric_top_fraction=f, **kw)


def centroid(curve: np.ndarray, metric: MetricSettings, base: float | None = None) -> float:
    base_ = np.array([curve.min() if base is None else base])
    return float(metric_range.range_centroid(GRID, curve[None, :], base_, metric)[0])


class RangeCentroidTests(unittest.TestCase):
    def test_a_symmetric_bell_has_its_centroid_at_its_centre_with_any_baseline(self) -> None:
        for offset in (0.0, 0.1, 0.5):
            for f in (0.0, 0.3, 0.8):
                self.assertAlmostEqual(centroid(bell(GRID, 590.0, offset=offset), top(f)), 590.0, delta=0.05, msg=(offset, f))  # the grid ends cut the tails a little

    def test_the_baseline_has_no_weight(self) -> None:
        # whole-curve centroid of a bell sitting on a baseline is pulled to the middle of the window; the ranged one is not
        skewed = bell(GRID, 560.0, offset=0.4, height=0.1)
        whole = float(np.trapezoid(skewed * GRID, GRID) / np.trapezoid(skewed, GRID))
        self.assertGreater(whole - 560.0, 20.0)
        self.assertLess(abs(centroid(skewed, top(0.0)) - 560.0), 2.0)  # (the grid cuts the left tail)

    def test_a_higher_fraction_moves_the_centroid_to_the_top_of_a_skewed_peak(self) -> None:
        curve = bell(GRID, 590.0, skew=0.6)
        values = [centroid(curve, top(f)) for f in (0.0, 0.3, 0.6, 0.9)]
        self.assertTrue(all(b < a for a, b in zip(values, values[1:])), values)  # the long tail pulls low fractions to longer wavelengths
        self.assertAlmostEqual(values[-1], 590.0, delta=5.0)

    def test_the_edges_of_the_part_do_not_matter(self) -> None:
        """The weights go to zero at the level, so moving the ends of the curve (the fit window) beyond the part changes nothing."""
        wide = bell(GRID, 590.0, skew=0.3)
        narrow_grid = GRID[(GRID >= 520.0) & (GRID <= 680.0)]
        a = centroid(wide, top(0.3), base=0.1)
        b = float(metric_range.range_centroid(narrow_grid, bell(narrow_grid, 590.0, skew=0.3)[None, :], np.array([0.1]), top(0.3))[0])
        self.assertAlmostEqual(a, b, delta=0.02)

    def test_a_manual_range_takes_only_the_curve_inside_it(self) -> None:
        curve = bell(GRID, 590.0)
        m = MetricSettings(metric_key="centroid", metric_range="manual", metric_range_min=560.0, metric_range_max=620.0)
        self.assertAlmostEqual(centroid(curve, m), 590.0, places=6)
        right = replace(m, metric_range_min=590.0, metric_range_max=650.0)
        self.assertGreater(centroid(curve, right), 600.0)
        one_sided = replace(m, metric_range_min=None, metric_range_max=590.0)
        self.assertLess(centroid(curve, one_sided), 590.0)

    def test_a_curve_without_a_peak_has_no_centroid(self) -> None:
        self.assertTrue(np.isnan(centroid(np.full(GRID.size, 0.3), top(0.3))))
        m = MetricSettings(metric_key="centroid", metric_range="manual", metric_range_min=900.0, metric_range_max=950.0)
        self.assertTrue(np.isnan(centroid(bell(GRID, 590.0), m)))

    def test_the_baseline_of_a_gaussian_is_its_fitted_offset_and_of_a_polynomial_its_lowest_point(self) -> None:
        curve = np.array([[0.3, 0.5, 0.4]])
        self.assertEqual(float(metric_range.curve_base("gaussian", np.array([[0.2, 0.0, 1.0, 0.12]]), curve)[0]), 0.12)
        self.assertEqual(float(metric_range.curve_base("polynomial", np.array([[1.0, 2.0, 3.0, 4.0]]), curve)[0]), 0.3)


def shifted_input(centre: float, n_cube: int = 1) -> TraceInput:
    absorbance = bell(WL, centre, sigma=45.0, skew=0.2)
    reference = np.full((n_cube, WL.size), 1000.0)
    sample = reference * 10.0 ** (-absorbance[None, :])
    return TraceInput(WL.copy(), sample, reference, np.ones(sample.shape, dtype=bool), np.ones(n_cube, dtype=bool))


def trace_at(centre: float, metric: MetricSettings) -> float:
    return float(trace_from_input(shifted_input(centre), "absorbance", metric)[0])


class ResponseTests(unittest.TestCase):
    """A metric should move by what the peak moved (gain 1). The centroid of the whole window under-reports; the ranged one does not."""

    def gain(self, metric: MetricSettings) -> float:
        return (trace_at(595.0, metric) - trace_at(585.0, metric)) / 10.0

    def test_the_whole_window_centroid_under_reports_a_shift_and_the_top_fraction_does_not(self) -> None:
        """Polynomial fit in a fixed window of 540-640 nm (the case the maintainer's data showed: gain 0.21 for the centroid as built)."""
        base = MetricSettings(fit_method="polynomial", metric_key="centroid", fit_wl_min=540.0, fit_wl_max=640.0)
        self.assertLess(self.gain(base), 0.6)
        ranged = replace(base, metric_range="top_fraction", metric_top_fraction=0.3)
        self.assertGreater(self.gain(ranged), 0.85)
        self.assertLess(self.gain(ranged), 1.2)

    def test_a_gaussian_with_every_point_fitted_also_responds_one_to_one(self) -> None:
        base = MetricSettings(fit_method="gaussian", metric_key="centroid")
        self.assertLess(self.gain(base), 0.7)
        self.assertGreater(self.gain(replace(base, metric_range="top_fraction", metric_top_fraction=0.3)), 0.85)

    # A Gaussian fitted in a window that cuts the bell does not determine its own offset (the baseline), and the top fraction is measured from that
    # offset: there the maximum is the better number (docs/spectra_analysis_methods.md, section 6). Not asserted here.


def drifting_input(n_cube: int = 40, seed: int = 5, holes=None) -> TraceInput:
    rng = np.random.default_rng(seed)
    centre = 560.0 + 1.3 * np.arange(n_cube)
    band = 0.25 * np.exp(-0.5 * ((WL[None, :] - centre[:, None]) / 40.0) ** 2) + 0.05
    reference = 1000.0 + rng.normal(0.0, 1.0, (n_cube, WL.size))
    sample = reference * 10.0 ** (-band) + rng.normal(0.0, 0.5, (n_cube, WL.size))
    usable = np.ones(sample.shape, dtype=bool)
    for cube, indices in (holes or {}).items():
        usable[cube, list(indices)] = False
        sample[cube, list(indices)] = np.nan
    return TraceInput(WL.copy(), sample, reference, usable, np.ones(n_cube, dtype=bool))


class BatchMatchesPerSpectrumTests(unittest.TestCase):
    def check(self, m: MetricSettings, data: TraceInput | None = None) -> None:
        data = data or drifting_input()
        batch = trace_from_input(data, "absorbance", m)
        reference = trace_from_input_per_cube(data, "absorbance", m)
        np.testing.assert_array_equal(np.isnan(batch), np.isnan(reference))
        np.testing.assert_allclose(batch[np.isfinite(reference)], reference[np.isfinite(reference)], atol=1.0e-6, rtol=0)
        self.assertGreaterEqual(int(np.isfinite(batch).sum()), 30)

    def test_each_fit_range_and_window(self) -> None:
        windows = (
            {},
            {"fit_wl_min": 530.0, "fit_wl_max": 650.0},
            {"window_on": True, "window_mode": "around_max", "window_half_width_nm": 55.0},
            {"window_on": True, "window_mode": "fwhm", "window_fwhm_factor": 0.8, "window_same_points": True},
        )
        ranges = ({"metric_range": "top_fraction", "metric_top_fraction": 0.3},
                  {"metric_range": "top_fraction", "metric_top_fraction": 0.7},
                  {"metric_range": "manual", "metric_range_min": 560.0, "metric_range_max": 630.0})
        for fit in ("gaussian", "polynomial"):
            for window in windows:
                for rng in ranges:
                    with self.subTest(fit=fit, window=window, range=rng):
                        self.check(MetricSettings(fit_method=fit, metric_key="centroid", **window, **rng))

    def test_missing_wavelengths_in_some_cubes(self) -> None:
        data = drifting_input(holes={3: [10, 11], 7: [0], 12: [20, 21, 22]})
        for fit in ("gaussian", "polynomial"):
            self.check(MetricSettings(fit_method=fit, metric_key="centroid", metric_range="top_fraction"), data)

    def test_off_changes_nothing_and_the_maximum_and_no_fit_have_no_range(self) -> None:
        data = drifting_input()
        for fit in ("gaussian", "polynomial"):
            plain = trace_from_input(data, "absorbance", MetricSettings(fit_method=fit, metric_key="centroid"))
            off = trace_from_input(data, "absorbance", MetricSettings(fit_method=fit, metric_key="centroid", metric_range="off", metric_top_fraction=0.8))
            np.testing.assert_array_equal(plain, off)
            maximum = trace_from_input(data, "absorbance", MetricSettings(fit_method=fit))
            ranged = trace_from_input(data, "absorbance", MetricSettings(fit_method=fit, metric_range="top_fraction", metric_top_fraction=0.8))
            np.testing.assert_array_equal(maximum, ranged)  # a range is for the centroid only
        no_fit = MetricSettings(fit_method="none", metric_key="centroid")
        np.testing.assert_array_equal(
            trace_from_input(data, "absorbance", no_fit), trace_from_input(data, "absorbance", replace(no_fit, metric_range="top_fraction"))
        )

    def test_the_spectra_panel_route_uses_the_range(self) -> None:
        x = np.linspace(470.0, 720.0, 26)
        spectrum = formula_spectrum(x, 1000.0 * 10.0 ** (-bell(x, 590.0, skew=0.5)), np.full(26, 1000.0), "absorbance")
        whole = fit_and_metric(spectrum, MetricSettings(fit_method="polynomial", metric_key="centroid"))[1]
        ranged = fit_and_metric(spectrum, MetricSettings(fit_method="polynomial", metric_key="centroid", metric_range="top_fraction", metric_top_fraction=0.3))
        self.assertNotAlmostEqual(whole, ranged[1], places=1)
        self.assertTrue(np.isfinite(ranged[2]))  # the marker's height on the curve


class SettingsTests(unittest.TestCase):
    def test_the_key_follows_every_setting_that_changes_the_metric(self) -> None:
        base = top(0.3)
        keys = {
            base.metric_range_key(),
            replace(base, metric_top_fraction=0.5).metric_range_key(),
            replace(base, metric_range="manual", metric_range_min=550.0).metric_range_key(),
            replace(base, metric_range="manual", metric_range_min=550.0, metric_range_max=620.0).metric_range_key(),
            replace(base, metric_range="off").metric_range_key(),
        }
        self.assertEqual(len(keys), 5)
        self.assertEqual(replace(base, metric_key="maximum").metric_range_key(), ("whole",))  # the maximum has no range
        self.assertEqual(base.metric_range_key(), replace(base, metric_range_min=500.0).metric_range_key())  # an unused bound changes nothing

    def test_the_sensorgram_trace_cache_key_follows_the_range(self) -> None:
        base = top(0.3)
        self.assertNotEqual(trace_key(None, "absorbance", base), trace_key(None, "absorbance", replace(base, metric_top_fraction=0.6)))


if __name__ == "__main__":
    unittest.main()
