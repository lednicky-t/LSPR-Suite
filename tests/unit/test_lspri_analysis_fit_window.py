"""Pure-logic tests for the per-spectrum fit window (`analysis/fit_window.py`, 2026-10-25): where the centre and the width come from, the
"same number of points" rule, and that the batch fit (`trace_fit`) selects exactly the points the one-spectrum path does. No Qt, no files."""

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
    from lspr_imaging_app.analysis import fit_window
    from lspr_imaging_app.analysis.scan_trace import trace_key
    from lspr_imaging_app.analysis.settings import MetricSettings
    from lspr_imaging_app.analysis.trace_fit import TraceInput, trace_from_input, trace_from_input_per_cube
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

WL = np.linspace(470.0, 720.0, 26)  # the real scans: 10 nm steps


def bell(centre: float, sigma: float = 46.0, offset: float = 0.1, height: float = 0.25, wl: np.ndarray = WL) -> np.ndarray:
    return offset + height * np.exp(-0.5 * ((wl - centre) / sigma) ** 2)


def metric(mode: str, **kw) -> MetricSettings:
    return MetricSettings(fit_method="gaussian", window_on=True, window_mode=mode, **kw)


def bounds_of(y: np.ndarray, m: MetricSettings, x: np.ndarray = WL) -> tuple[float, float]:
    low, high = fit_window.window_bounds(x, y[None, :], np.ones((1, x.size), dtype=bool), m)
    return float(low[0]), float(high[0])


class CentreTests(unittest.TestCase):
    def test_the_parabola_top_is_found_between_grid_points(self) -> None:
        y = 3.0 - 0.002 * (WL - 583.7) ** 2  # an exact parabola, top at 583.7 nm
        _top, centre = fit_window.peak_centre(WL, y[None, :])
        self.assertAlmostEqual(float(centre[0]), 583.7, places=9)

    def test_the_parabola_top_is_right_for_uneven_spacing(self) -> None:
        x = np.array([500.0, 508.0, 530.0, 541.0, 575.0, 590.0])
        y = 5.0 - 0.01 * (x - 536.0) ** 2
        top, centre = fit_window.peak_centre(x, y[None, :])
        self.assertEqual(int(top[0]), 3)
        self.assertAlmostEqual(float(centre[0]), 536.0, places=9)

    def test_a_maximum_at_the_end_keeps_the_grid_point(self) -> None:
        y = np.linspace(0.1, 0.9, WL.size)
        top, centre = fit_window.peak_centre(WL, y[None, :])
        self.assertEqual((int(top[0]), float(centre[0])), (WL.size - 1, WL[-1]))

    def test_the_centre_moves_smoothly_while_the_peak_shifts_by_less_than_a_step(self) -> None:
        centres = [bounds_of(bell(c), metric("around_max", window_half_width_nm=50.0)) for c in (580.0, 582.0, 584.0, 586.0)]
        lows = [low for low, _high in centres]
        self.assertTrue(all(b > a for a, b in zip(lows, lows[1:])), lows)  # a grid maximum would give the same window for all four


class WidthTests(unittest.TestCase):
    def test_the_fwhm_of_a_gaussian_is_found_with_or_without_an_offset(self) -> None:
        x = np.arange(400.0, 800.0, 2.0)  # fine grid: the interpolation error is negligible
        for offset in (0.0, 0.12):
            y = bell(600.0, sigma=40.0, offset=offset, wl=x)
            s = fit_window.smooth3(y[None, :])
            top, centre = fit_window.peak_centre(x, s)
            width = fit_window.half_max_width(x, s, top, centre)
            self.assertAlmostEqual(float(width[0]), 2.3548 * 40.0, delta=1.0)

    def test_the_fwhm_window_is_the_centre_plus_and_minus_k_times_the_width(self) -> None:
        y = bell(590.0)
        low, high = bounds_of(y, metric("fwhm", window_fwhm_factor=0.5))
        self.assertAlmostEqual((low + high) / 2.0, 590.0, delta=1.0)
        self.assertAlmostEqual(high - low, 2.3548 * 46.0, delta=6.0)  # +/- 0.5 FWHM spans one FWHM (10 nm grid, light smoothing widens it a little)

    def test_a_peak_wider_than_the_data_on_one_side_uses_twice_the_other_side(self) -> None:
        y = bell(485.0, sigma=60.0)  # the half-height point on the left (414 nm) is outside the data (from 470 nm)
        s = fit_window.smooth3(y[None, :])
        top, centre = fit_window.peak_centre(WL, s)
        width = fit_window.half_max_width(WL, s, top, centre)
        self.assertAlmostEqual(float(width[0]), 2.3548 * 60.0, delta=15.0)

    def test_a_flat_spectrum_or_too_few_points_has_no_window(self) -> None:
        self.assertEqual(bounds_of(np.full(WL.size, 0.2), metric("fwhm")), (-np.inf, np.inf))
        x = WL[:3]
        self.assertEqual(bounds_of(np.array([0.1, 0.3, 0.1]), metric("around_max"), x), (-np.inf, np.inf))
        self.assertEqual(fit_window.spectrum_window(WL, np.full(WL.size, 0.2), metric("fwhm")), (None, None))

    def test_a_spectrums_window_does_not_depend_on_the_other_spectra(self) -> None:
        m = metric("fwhm", window_fwhm_factor=0.75)
        rows = np.array([bell(c, sigma=s) for c, s in ((560.0, 30.0), (590.0, 50.0), (630.0, 40.0))])
        together = fit_window.window_bounds(WL, rows, np.ones(rows.shape, dtype=bool), m)
        for i in range(3):
            alone = fit_window.window_bounds(WL, rows[i : i + 1], np.ones((1, WL.size), dtype=bool), m)
            self.assertEqual((together[0][i], together[1][i]), (alone[0][0], alone[1][0]))


class SamePointsTests(unittest.TestCase):
    def test_every_window_holds_the_same_number_of_points(self) -> None:
        m = metric("around_max", window_half_width_nm=40.0, window_same_points=True)  # 2 x 40 / 10 + 1 = 9 points
        for centre in np.arange(520.0, 680.0, 3.7):
            low, high = bounds_of(bell(float(centre)), m)
            self.assertEqual(int(((WL >= low) & (WL <= high)).sum()), 9, centre)

    def test_without_the_rule_the_count_changes_with_the_phase_of_the_peak(self) -> None:
        m = metric("around_max", window_half_width_nm=45.0)
        counts = {int(((WL >= lo) & (WL <= hi)).sum()) for lo, hi in (bounds_of(bell(float(c)), m) for c in np.arange(560.0, 600.0, 1.0))}
        self.assertGreater(len(counts), 1)

    def test_a_window_at_the_end_of_the_data_moves_inwards_and_keeps_its_count(self) -> None:
        m = metric("around_max", window_half_width_nm=50.0, window_same_points=True)  # 11 points
        low, high = bounds_of(bell(480.0), m)
        self.assertEqual((low, high), (WL[0], WL[10]))  # the nominal range reaches past the data; the nearest points are taken
        low, high = bounds_of(bell(715.0), m)
        self.assertEqual((low, high), (WL[-11], WL[-1]))

    def test_the_window_is_centred_on_the_nearest_points(self) -> None:
        m = metric("around_max", window_half_width_nm=20.0, window_same_points=True)  # 5 points
        low, high = bounds_of(bell(593.0), m)
        self.assertEqual((low, high), (570.0, 610.0))  # 590 is the nearest grid point to 593
        low, high = bounds_of(bell(597.0), m)
        self.assertEqual((low, high), (580.0, 620.0))

    def test_a_count_larger_than_the_data_takes_everything(self) -> None:
        m = metric("around_max", window_half_width_nm=500.0, window_same_points=True)
        self.assertEqual(bounds_of(bell(590.0), m), (WL[0], WL[-1]))


class SettingsTests(unittest.TestCase):
    def test_the_manual_range_is_unchanged_and_needs_no_flag(self) -> None:
        m = MetricSettings(fit_wl_min=520.0, fit_wl_max=600.0)
        self.assertEqual(m.manual_bounds(), (520.0, 600.0))
        self.assertFalse(m.relative_window)

    def test_a_per_spectrum_mode_is_on_only_when_the_flag_is(self) -> None:
        m = MetricSettings(window_mode="fwhm", window_on=False)
        self.assertFalse(m.relative_window)
        self.assertTrue(replace(m, window_on=True).relative_window)
        self.assertEqual(replace(m, window_on=True).manual_bounds(), (None, None))

    def test_the_cache_key_changes_with_every_setting_that_changes_the_points(self) -> None:
        base = metric("around_max", window_half_width_nm=40.0)
        keys = {
            base.window_key(),
            replace(base, window_half_width_nm=50.0).window_key(),
            replace(base, window_same_points=True).window_key(),
            replace(base, window_mode="fwhm").window_key(),
            replace(base, window_on=False).window_key(),
        }
        self.assertEqual(len(keys), 5)
        self.assertEqual(base.window_key(), replace(base, window_fwhm_factor=2.0).window_key())  # the unused width does not change the points

    def test_the_sensorgram_trace_cache_key_follows_the_window(self) -> None:
        base = metric("fwhm")
        self.assertNotEqual(trace_key(None, "absorbance", base), trace_key(None, "absorbance", replace(base, window_fwhm_factor=1.0)))


def shifting_input(n_cube: int = 40, seed: int = 3, holes=None) -> TraceInput:
    rng = np.random.default_rng(seed)
    centre = 560.0 + 1.3 * np.arange(n_cube)  # a peak that drifts by a fraction of a grid step per cube
    band = 0.25 * np.exp(-0.5 * ((WL[None, :] - centre[:, None]) / 40.0) ** 2)
    reference = 1000.0 + rng.normal(0.0, 1.0, (n_cube, WL.size))
    sample = reference * 10.0 ** (-band) + rng.normal(0.0, 0.5, (n_cube, WL.size))
    usable = np.ones(sample.shape, dtype=bool)
    for cube, indices in (holes or {}).items():
        usable[cube, list(indices)] = False
        sample[cube, list(indices)] = np.nan
    return TraceInput(WL.copy(), sample, reference, usable, np.ones(n_cube, dtype=bool))


class BatchMatchesPerSpectrumTests(unittest.TestCase):
    """The Sensorgram fits every cube at once; the Spectra panel one spectrum at a time. They must pick the same points and so give the same number."""

    def check(self, m: MetricSettings, data: TraceInput | None = None) -> None:
        data = data or shifting_input()
        batch = trace_from_input(data, "absorbance", m)
        reference = trace_from_input_per_cube(data, "absorbance", m)
        np.testing.assert_array_equal(np.isnan(batch), np.isnan(reference))
        np.testing.assert_allclose(batch[np.isfinite(reference)], reference[np.isfinite(reference)], atol=1.0e-6, rtol=0)
        self.assertTrue(np.isfinite(batch).sum() >= 30)

    def test_each_mode_fit_and_metric(self) -> None:
        for fit in ("gaussian", "polynomial"):
            for key in ("maximum", "centroid"):
                for mode, kw in (("around_max", {"window_half_width_nm": 45.0}), ("fwhm", {"window_fwhm_factor": 0.6})):
                    for same in (False, True):
                        with self.subTest(fit=fit, metric=key, mode=mode, same=same):
                            self.check(MetricSettings(fit_method=fit, metric_key=key, window_on=True, window_mode=mode, window_same_points=same, **kw))

    def test_missing_wavelengths_in_some_cubes(self) -> None:
        data = shifting_input(holes={3: [10, 11], 7: [0], 12: [20, 21, 22]})
        self.check(MetricSettings(fit_method="polynomial", window_on=True, window_mode="fwhm", window_fwhm_factor=0.75), data)
        self.check(MetricSettings(fit_method="gaussian", window_on=True, window_mode="around_max", window_same_points=True), data)

    def test_no_fit_reads_the_metric_off_the_points_in_the_window(self) -> None:
        self.check(MetricSettings(fit_method="none", window_on=True, window_mode="around_max", window_half_width_nm=30.0))

    def test_a_window_that_is_off_changes_nothing(self) -> None:
        data = shifting_input()
        plain = trace_from_input(data, "absorbance", MetricSettings(fit_method="polynomial"))
        off = trace_from_input(data, "absorbance", MetricSettings(fit_method="polynomial", window_mode="fwhm", window_on=False))
        np.testing.assert_array_equal(plain, off)

    def test_the_window_follows_the_peak_better_than_a_fixed_range(self) -> None:
        data = shifting_input(n_cube=60)
        truth = 560.0 + 1.3 * np.arange(60)
        fixed = trace_from_input(data, "absorbance", MetricSettings(fit_method="gaussian", fit_wl_min=540.0, fit_wl_max=600.0))
        follow = trace_from_input(data, "absorbance", MetricSettings(fit_method="gaussian", window_on=True, window_mode="around_max", window_half_width_nm=60.0))
        late = slice(40, 60)  # the peak has left the fixed range by now (it sits at 612..637 nm)
        self.assertLess(float(np.nanmean(np.abs(follow[late] - truth[late]))), 1.0)
        self.assertGreater(float(np.nanmean(np.abs(fixed[late] - truth[late]))), 5.0)


if __name__ == "__main__":
    unittest.main()
