"""Pure-logic tests for the template shift (`analysis/template_shift.py`, 2026-10-25) and its wiring: the one-spectrum route (`query.fit_and_metric`, what the Spectra panel
uses), the batch route (`trace_fit`, what the Sensorgram uses) and the settings / cache keys. No Qt, no files. Method record:
apps/LSPRi/eva/docs/shift_estimators_comparison_2026-10-25.md; maths: the module docstring and `spectra_analysis_methods.md`.

Tolerances: the batch and the one-spectrum route do the same least-squares problem on the same points (a missing point is a zero weight in one and an absent point in the other),
so they differ by rounding only (bound here 1e-8 nm). The grid-and-parabola minimum agrees with a bounded scalar minimiser to < 1e-3 nm. A synthetic shift is recovered to < 0.02 nm
(the template is a cubic spline through 26 points 10 nm apart)."""

from __future__ import annotations

import sys
import unittest
from dataclasses import asdict, replace
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import template_shift as ts
    from lspr_imaging_app.analysis.query import FormulaSpectrum, fit_and_metric, fit_spectrum, formula_spectrum
    from lspr_imaging_app.analysis.scan_trace import metric_trace, trace_key
    from lspr_imaging_app.analysis.scan_view import SpectraRequest, compute_spectra
    from lspr_imaging_app.analysis.settings import FIT_METHODS, MetricSettings
    from lspr_imaging_app.analysis.trace_fit import (
        TraceInput, reference_template, template_diagnostics, template_trace, trace_from_input, trace_from_input_per_cube, traces_from_inputs,
    )
    from lspr_imaging_app.storage.session import _decode_settings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
from scipy.optimize import minimize_scalar  # noqa: E402

WL = np.linspace(470.0, 720.0, 26)
TEMPLATE_METRIC = MetricSettings(fit_method="template", template_first_cube=0, template_last_cube=4)


def band(wl: np.ndarray, centre: float) -> np.ndarray:
    """A broad asymmetric absorption band (a main peak and a shoulder on the red side) on a small offset: absorbance of about 0.3."""
    return 0.30 * np.exp(-0.5 * ((wl - centre) / 45.0) ** 2) + 0.08 * np.exp(-0.5 * ((wl - (centre + 60.0)) / 50.0) ** 2) + 0.02


def make_input(shifts, *, noise: float = 0.0, seed: int = 1, cube_offset: int = 0, unscanned=(), holes=None, shuffle: bool = False, scale=None, offset=None) -> TraceInput:
    """One ROI: cube ``c`` holds the band moved by ``shifts[c]`` nm. Absorbance = log10(reference / sample), so sample = reference x 10^(-A) gives A back exactly."""
    rng = np.random.default_rng(seed)
    shifts = np.asarray(shifts, dtype=np.float64)
    n_cube = shifts.size
    a = band(WL[None, :], 580.0 + shifts[:, None]) + rng.normal(0.0, noise, (n_cube, WL.size))
    if scale is not None:
        a = a * np.asarray(scale)[:, None]
    if offset is not None:
        a = a + np.asarray(offset)[:, None]
    reference = np.full((n_cube, WL.size), 1000.0)
    sample = reference * 10.0 ** (-a)
    usable = np.ones(sample.shape, dtype=bool)
    for cube, indices in (holes or {}).items():
        usable[cube, list(indices)] = False
        sample[cube, list(indices)] = np.nan
    scanned = np.ones(n_cube, dtype=bool)
    scanned[list(unscanned)] = False
    wl = WL.copy()
    if shuffle:  # the scan's wavelengths need not be ascending
        order = rng.permutation(wl.size)
        wl, sample, reference, usable = wl[order], sample[:, order], reference[:, order], usable[:, order]
    return TraceInput(wl, sample, reference, usable, scanned, np.arange(n_cube) + cube_offset)


def arrays_of(data: TraceInput):
    values = np.log10(data.reference / np.maximum(data.sample, 1e-9))
    return data.wavelengths, values, data.usable


class ShiftMathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.valid = np.ones((1, WL.size), dtype=bool)
        template_values = band(WL[None, :], 580.0)
        self.template = ts.build_template(WL, template_values, self.valid, np.array([True]))

    def fit(self, values, valid=None):
        values = np.atleast_2d(values)
        return ts.fit_template_shift(self.template, WL, values, np.ones(values.shape, dtype=bool) if valid is None else valid)

    def test_a_known_sideways_movement_is_recovered(self) -> None:
        truth = np.array([-15.0, -7.3, -0.4, 0.0, 0.6, 2.1, 7.27, 15.0])
        found = self.fit(np.array([band(WL, 580.0 + d) for d in truth])).shift_nm
        np.testing.assert_allclose(found, truth, atol=0.02)
        # a shift of a third of the peak's width or more moves the band past the end of the data, where the reference is held at its end value: a small bias, not a failure
        far = np.array([-25.0, 40.0])
        found_far = self.fit(np.array([band(WL, 580.0 + d) for d in far])).shift_nm
        np.testing.assert_allclose(found_far, far, atol=0.2)

    def test_the_position_is_the_template_peak_plus_the_shift(self) -> None:
        dense = np.arange(470.0, 720.0, 0.01)
        self.assertAlmostEqual(self.template.position_nm, float(dense[np.argmax(band(dense, 580.0))]), delta=0.05)  # the true top of the band (the shoulder pulls it to the red)
        fit = self.fit(np.array([band(WL, 590.0)]))
        self.assertAlmostEqual(float(fit.position_nm[0]), self.template.position_nm + float(fit.shift_nm[0]), places=9)
        direct = ts.template_peak_nm(WL, band(WL, 590.0))
        self.assertAlmostEqual(float(fit.position_nm[0]), direct, delta=0.1)  # and it is where the moved band's own top is

    def test_a_change_of_amplitude_or_of_the_offset_is_not_a_shift(self) -> None:
        base = band(WL, 585.0)
        plain = float(self.fit(base).shift_nm[0])
        for scale, offset in ((1.05, 0.0), (0.7, 0.0), (1.0, 0.01), (1.3, -0.02), (1.02, 0.002)):
            self.assertAlmostEqual(float(self.fit(base * scale + offset).shift_nm[0]), plain, places=7, msg=f"{scale=} {offset=}")
        fit = self.fit(base * 1.25 + 0.03)
        self.assertAlmostEqual(float(fit.scale[0]), 1.25, delta=0.02)
        self.assertAlmostEqual(float(fit.offset[0]), 0.03, delta=0.01)

    def test_the_grid_and_parabola_minimum_agrees_with_a_scalar_minimiser(self) -> None:
        rng = np.random.default_rng(5)
        spline = self.template.spline()
        for shift in (-12.0, 3.7, 9.9):
            y = band(WL, 580.0 + shift) + rng.normal(0.0, 0.004, WL.size)
            found = float(self.fit(y).shift_nm[0])

            def sse(d: float) -> float:
                t = self.template.evaluate(spline, WL - d)
                x = np.stack([t, np.ones_like(t)], axis=1)
                c = np.linalg.lstsq(x, y, rcond=None)[0]
                return float(((y - x @ c) ** 2).sum())

            best = minimize_scalar(sse, bounds=(found - 2.0, found + 2.0), method="bounded", options={"xatol": 1e-9})
            self.assertAlmostEqual(found, float(best.x), delta=1e-3)

    def test_each_spectrum_is_fitted_alone(self) -> None:
        rng = np.random.default_rng(7)
        y = np.array([band(WL, 580.0 + d) + rng.normal(0.0, 0.003, WL.size) for d in (1.0, -4.0, 9.0, 0.5)])
        together = self.fit(y)
        for i in range(len(y)):
            alone = self.fit(y[i])
            for name in ("shift_nm", "position_nm", "scale", "offset", "rms", "tilt", "tilt_shift_nm"):  # equal to rounding: the matrix products add in another order for another batch size
                np.testing.assert_allclose(getattr(alone, name)[0], getattr(together, name)[i], rtol=0.0, atol=1e-8, err_msg=name)
        shuffled = self.fit(y[::-1])
        np.testing.assert_allclose(shuffled.shift_nm, together.shift_nm[::-1], rtol=0.0, atol=1e-8)

    def test_a_missing_point_is_left_out_not_filled_in(self) -> None:
        y = band(WL, 584.0)
        valid = np.ones((1, WL.size), dtype=bool)
        valid[0, [3, 11, 12]] = False
        with_hole = self.fit(np.where(valid[0], y, np.nan), valid)
        keep = valid[0]
        sub_template = ts.build_template(WL[keep], band(WL, 580.0)[keep][None, :], np.ones((1, keep.sum()), dtype=bool), np.array([True]))
        subset = ts.fit_template_shift(sub_template, WL[keep], y[keep][None, :], np.ones((1, keep.sum()), dtype=bool))
        # the template of the full grid holds the points the spectrum lacks, so the two differ a little; both must find the movement of the band
        self.assertAlmostEqual(float(with_hole.shift_nm[0]), 4.0, delta=0.05)
        self.assertAlmostEqual(float(subset.shift_nm[0]), 4.0, delta=0.05)
        self.assertEqual(int(with_hole.n_points[0]), WL.size - 3)
        # NaN under a False mark is never read
        poisoned = np.where(valid[0], y, 1.0e6)
        np.testing.assert_array_equal(self.fit(poisoned, valid).shift_nm, with_hole.shift_nm)

    def test_cases_without_an_answer_are_gaps_not_numbers(self) -> None:
        few = np.zeros((1, WL.size), dtype=bool)
        few[0, :3] = True
        self.assertTrue(np.isnan(self.fit(band(WL, 580.0), few).shift_nm[0]))  # fewer points than unknowns
        flat = ts.build_template(WL, np.full((1, WL.size), 0.2), np.ones((1, WL.size), dtype=bool), np.array([True]))
        out = ts.fit_template_shift(flat, WL, band(WL, 580.0)[None, :], np.ones((1, WL.size), dtype=bool))
        self.assertTrue(np.isnan(out.shift_nm[0]) and np.isnan(out.position_nm[0]))  # a flat reference has no shift
        self.assertTrue(np.isnan(self.fit(band(WL, 580.0 + 90.0)).shift_nm[0]) or abs(float(self.fit(band(WL, 580.0 + 90.0)).shift_nm[0])) < ts.MAX_SHIFT_NM)
        empty = ts.build_template(WL, band(WL, 580.0)[None, :], np.ones((1, WL.size), dtype=bool), np.array([False]))
        self.assertIsNone(empty)  # no reference cube
        sparse = np.zeros((1, WL.size), dtype=bool)
        sparse[0, :3] = True
        self.assertIsNone(ts.build_template(WL, band(WL, 580.0)[None, :], sparse, np.array([True])))  # fewer than four wavelengths

    def test_the_template_is_the_mean_of_the_reference_cubes_per_wavelength(self) -> None:
        a = np.vstack([band(WL, 580.0), band(WL, 584.0), band(WL, 700.0)])
        valid = np.ones(a.shape, dtype=bool)
        valid[1, 5] = False  # cube 1 lacks wavelength 5
        valid[:, 8] = False  # no cube has wavelength 8: left out of the template
        tpl = ts.build_template(WL, a, valid, np.array([True, True, False]))
        expected = (a[0] + a[1]) / 2.0
        expected[5] = a[0, 5]
        keep = np.arange(WL.size) != 8
        np.testing.assert_allclose(tpl.values, expected[keep])
        np.testing.assert_array_equal(tpl.wavelengths_nm, WL[keep])
        self.assertEqual(tpl.n_cubes, 2)

    def test_the_wavelengths_must_ascend(self) -> None:
        with self.assertRaises(ValueError):
            ts.build_template(WL[::-1], band(WL, 580.0)[None, ::-1], np.ones((1, WL.size), dtype=bool), np.array([True]))

    def test_the_tilt_is_the_baseline_change_not_explained_by_the_shift(self) -> None:
        plain = self.fit(band(WL, 585.0))
        self.assertLess(abs(float(plain.tilt[0])), 3e-3)  # a pure movement carries almost no tilt (what is left is the spline's error between the grid points)
        u = (WL - WL[0]) / (WL[-1] - WL[0])
        tilted = self.fit(band(WL, 585.0) + 0.004 * u)
        self.assertAlmostEqual(float(tilted.tilt[0]) - float(plain.tilt[0]), 0.004, places=6)  # an added tilt is measured in full: the joint fit moves the shift to make room for it
        self.assertAlmostEqual(float(tilted.tilt_shift_nm[0]), float(plain.tilt_shift_nm[0]), delta=0.01)
        self.assertGreater(abs(float(tilted.shift_nm[0]) - float(plain.shift_nm[0])), 0.05)  # while the metric's own shift is bent by it (0.2 nm for 0.002 A on the real scan)
        flat = self.fit(band(WL, 585.0) + 0.05)
        self.assertAlmostEqual(float(flat.tilt[0]), float(plain.tilt[0]), places=9)  # an offset is not a tilt
        off = ts.fit_template_shift(self.template, WL, band(WL, 585.0)[None, :] + 0.004 * u, np.ones((1, WL.size), dtype=bool), diagnostics=False)
        self.assertTrue(np.isnan(off.tilt[0]) and np.isnan(off.tilt_shift_nm[0]))  # asked not to compute them
        self.assertEqual(float(off.shift_nm[0]), float(tilted.shift_nm[0]))  # and the metric is the same either way

    def test_the_response_law_separates_the_tilt_that_follows_the_shift_from_drift(self) -> None:
        shift = np.linspace(0.0, 8.0, 40)
        drift = np.zeros(40)
        drift[30:] = 0.001
        tilt = 0.0026 * shift + drift
        kappa, beyond = ts.response_law(shift, tilt)
        self.assertGreater(kappa, 0.0026)  # the drift sits at large shifts, so it leans the slope a little
        self.assertTrue(np.all(np.isfinite(beyond)))
        kappa2, beyond2 = ts.response_law(shift, 0.0026 * shift)
        self.assertAlmostEqual(kappa2, 0.0026, places=12)
        np.testing.assert_allclose(beyond2, 0.0, atol=1e-12)
        k3, b3 = ts.response_law(np.zeros(5), np.zeros(5))
        self.assertEqual(k3, 0.0)
        shift_gap = shift.copy()
        shift_gap[3] = np.nan
        self.assertTrue(np.isnan(ts.response_law(shift_gap, tilt)[1][3]))


class OneSpectrumRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = make_input([0.0, 0.1, -0.1, 0.05, 0.0, 6.0, 7.0])
        self.template = reference_template(self.data, "absorbance", TEMPLATE_METRIC)

    def spectrum(self, cube: int) -> FormulaSpectrum:
        usable = self.data.usable[cube]
        return formula_spectrum(self.data.wavelengths[usable], self.data.sample[cube][usable], self.data.reference[cube][usable], "absorbance")

    def test_fit_and_metric_gives_the_curve_the_position_and_the_height(self) -> None:
        fit, x, y = fit_and_metric(self.spectrum(5), TEMPLATE_METRIC, template=self.template)
        shift, scale, offset, tilt = (float(v) for v in fit.coefficients)
        self.assertAlmostEqual(shift, 6.0, delta=0.05)
        self.assertAlmostEqual(x, self.template.position_nm + shift, places=9)
        self.assertEqual(fit.peak_wavelength_nm, x)
        self.assertIsNone(fit.centroid_nm)
        self.assertGreaterEqual(fit.fitted_wavelengths_nm.size, 400)  # the whole measured range, not a window
        self.assertAlmostEqual(float(fit.fitted_wavelengths_nm[0]), 470.0)
        self.assertAlmostEqual(float(fit.fitted_wavelengths_nm[-1]), 720.0)
        self.assertAlmostEqual(y, float(np.interp(x, fit.fitted_wavelengths_nm, fit.fitted_values)), delta=2e-4)  # the marker sits on the curve
        self.assertLess(abs(tilt), 3e-3)

    def test_without_a_reference_it_refuses_instead_of_using_another_method(self) -> None:
        with self.assertRaises(ValueError):
            fit_and_metric(self.spectrum(5), TEMPLATE_METRIC)
        with self.assertRaises(ValueError):
            fit_spectrum(self.spectrum(5), "template")  # the old dispatch must not fall through to the polynomial

    def test_the_window_order_and_metric_choice_are_not_read(self) -> None:
        plain = fit_and_metric(self.spectrum(5), TEMPLATE_METRIC, template=self.template)[1]
        odd = replace(TEMPLATE_METRIC, poly_order=7, metric_key="centroid", fit_wl_min=600.0, fit_wl_max=640.0, window_on=True, window_mode="fwhm", metric_range="top_fraction")
        self.assertEqual(fit_and_metric(self.spectrum(5), odd, template=self.template)[1], plain)

    def test_a_spectrum_without_enough_points_gives_no_position(self) -> None:
        spectrum = FormulaSpectrum(WL[:3], band(WL, 580.0)[:3], band(WL, 580.0)[:3], band(WL, 580.0)[:3])
        fit, x, y = fit_and_metric(spectrum, TEMPLATE_METRIC, template=self.template)
        self.assertIsNone(x)
        self.assertIsNone(y)
        self.assertEqual(fit.fitted_wavelengths_nm.size, 0)


class BatchRouteTests(unittest.TestCase):
    def assert_routes_agree(self, data: TraceInput, metric: MetricSettings = TEMPLATE_METRIC) -> np.ndarray:
        batch = trace_from_input(data, "absorbance", metric)
        reference = trace_from_input_per_cube(data, "absorbance", metric)
        np.testing.assert_array_equal(np.isnan(batch), np.isnan(reference), err_msg="gaps differ")
        finite = np.isfinite(reference)
        np.testing.assert_allclose(batch[finite], reference[finite], rtol=0.0, atol=1e-8)
        return batch

    def test_the_batch_is_the_one_spectrum_route_cube_by_cube(self) -> None:
        shifts = np.r_[np.zeros(5), np.linspace(0.0, 9.0, 30)]
        data = make_input(shifts, noise=0.003, unscanned=(12, 13), holes={7: (0, 4, 5), 20: (25,), 30: tuple(range(10, 22))}, shuffle=True)
        batch = self.assert_routes_agree(data)
        self.assertTrue(np.isnan(batch[12]) and np.isnan(batch[13]))  # never scanned: a gap
        self.assertTrue(np.all(np.isfinite(np.delete(batch, [12, 13]))))

    def test_it_follows_the_movement_and_reads_a_position(self) -> None:
        shifts = np.r_[np.zeros(5), np.linspace(0.0, 9.0, 30)]
        data = make_input(shifts, noise=0.0003)  # a noise of 0.0005 absorbance costs about 0.05 nm on a real spectrum
        trace = trace_from_input(data, "absorbance", TEMPLATE_METRIC)
        np.testing.assert_allclose(trace - trace[:5].mean(), shifts, atol=0.1)
        tpl = reference_template(data, "absorbance", TEMPLATE_METRIC)
        self.assertAlmostEqual(float(trace[:5].mean()), tpl.position_nm, delta=0.06)

    def test_amplitude_and_offset_drift_does_not_move_the_trace(self) -> None:
        n = 35
        shifts = np.r_[np.zeros(5), np.linspace(0.0, 9.0, n - 5)]
        plain = trace_from_input(make_input(shifts), "absorbance", TEMPLATE_METRIC)
        drifting = trace_from_input(make_input(shifts, scale=np.linspace(1.0, 1.15, n), offset=np.linspace(0.0, 0.02, n)), "absorbance", TEMPLATE_METRIC)
        # the reference (first five cubes) is built from drifting cubes too, so compare the movement relative to it
        np.testing.assert_allclose(drifting - drifting[:5].mean(), plain - plain[:5].mean(), atol=0.08)

    def test_the_reference_range_is_in_cube_numbers_not_rows(self) -> None:
        shifts = np.r_[np.zeros(5), np.full(5, 12.0)]
        data = make_input(shifts, cube_offset=100)
        metric = replace(TEMPLATE_METRIC, template_first_cube=105, template_last_cube=109)  # the cubes that sit 12 nm away: they become the reference
        trace = trace_from_input(data, "absorbance", metric)
        np.testing.assert_allclose(trace[:5] - trace[5:], -12.0, atol=0.05)  # the first five cubes now read 12 nm to the blue of the reference
        reversed_typed = replace(TEMPLATE_METRIC, template_first_cube=109, template_last_cube=105)  # typed the wrong way round: the same range
        np.testing.assert_array_equal(trace_from_input(data, "absorbance", reversed_typed), trace)

    def test_a_reference_without_data_makes_a_gap_not_a_stop(self) -> None:
        data = make_input(np.zeros(6))
        metric = replace(TEMPLATE_METRIC, template_first_cube=50, template_last_cube=60)
        self.assertTrue(np.all(np.isnan(trace_from_input(data, "absorbance", metric))))
        self.assertIsNone(template_diagnostics(data, "absorbance", metric))
        never = make_input(np.zeros(6), unscanned=(0, 1, 2, 3, 4))  # the reference cubes exist but were never scanned
        self.assertTrue(np.all(np.isnan(trace_from_input(never, "absorbance", TEMPLATE_METRIC))))

    def test_every_roi_is_computed_alone_whatever_the_others_are(self) -> None:
        a = make_input(np.r_[np.zeros(5), np.linspace(0.0, 6.0, 15)], noise=0.002, seed=1)
        b = make_input(np.r_[np.zeros(5), np.linspace(0.0, -3.0, 15)], noise=0.002, seed=2)
        together = traces_from_inputs([a, b], "absorbance", TEMPLATE_METRIC)
        np.testing.assert_array_equal(together[0], trace_from_input(a, "absorbance", TEMPLATE_METRIC))
        np.testing.assert_array_equal(together[1], trace_from_input(b, "absorbance", TEMPLATE_METRIC))
        timings: dict[str, float] = {}
        traces_from_inputs([a, b], "absorbance", TEMPLATE_METRIC, timings)
        self.assertGreater(timings["fit_ms"], 0.0)

    def test_the_metric_of_the_other_methods_is_untouched(self) -> None:
        data = make_input(np.linspace(0.0, 9.0, 20), noise=0.002)
        for method in ("polynomial", "gaussian", "none"):
            metric = MetricSettings(fit_method=method, template_first_cube=3, template_last_cube=8)
            other = replace(metric, template_first_cube=0, template_last_cube=1)
            np.testing.assert_array_equal(trace_from_input(data, "absorbance", metric), trace_from_input(data, "absorbance", other), err_msg=method)

    def test_the_diagnostics_separate_tilt_that_follows_the_shift_from_drift(self) -> None:
        n = 40
        shifts = np.r_[np.zeros(5), np.linspace(0.0, 8.0, n - 5)]
        u = (WL - WL[0]) / (WL[-1] - WL[0])
        a = band(WL[None, :], 580.0 + shifts[:, None]) + 0.0026 * shifts[:, None] * u[None, :]  # a tilt locked to the shift
        a[30:] += 0.002 * u[None, :]  # and a baseline drift that is not
        reference = np.full(a.shape, 1000.0)
        data = TraceInput(WL.copy(), reference * 10.0 ** (-a), reference, np.ones(a.shape, dtype=bool), np.ones(n, dtype=bool), np.arange(n))
        diag = template_diagnostics(data, "absorbance", TEMPLATE_METRIC)
        self.assertEqual(diag.reference_cubes, 5)
        self.assertTrue(np.all(np.isfinite(diag.tilt)))
        quiet = replace(data, sample=data.reference * 10.0 ** (-(band(WL[None, :], 580.0 + shifts[:, None]) + 0.0026 * shifts[:, None] * u[None, :])))
        no_drift = template_diagnostics(quiet, "absorbance", TEMPLATE_METRIC)
        self.assertLess(float(np.sqrt(np.nanmean(no_drift.tilt_beyond ** 2))), 2e-4)  # a tilt that follows the shift leaves (almost) nothing beyond the law
        jump = float(np.mean(diag.tilt_beyond[30:]) - np.mean(diag.tilt_beyond[5:30]))
        self.assertGreater(jump, 0.0006)  # the drift of 0.002 A shows as a step (about half of it: the law is fitted to the same trace and leans towards the drift)
        np.testing.assert_allclose(diag.position_nm, template_trace(data, "absorbance", TEMPLATE_METRIC))


class SettingsAndKeysTests(unittest.TestCase):
    def test_the_method_is_offered_and_the_defaults_are_the_first_ten_cubes(self) -> None:
        self.assertIn("template", FIT_METHODS)
        m = MetricSettings()
        self.assertEqual((m.template_first_cube, m.template_last_cube), (0, 9))
        self.assertFalse(m.uses_template)
        self.assertTrue(replace(m, fit_method="template").uses_template)

    def test_the_cache_key_follows_the_reference_only_when_it_matters(self) -> None:
        template = replace(MetricSettings(), fit_method="template")
        keys = {template.template_key(), replace(template, template_last_cube=20).template_key(), replace(template, template_first_cube=2).template_key()}
        self.assertEqual(len(keys), 3)
        self.assertEqual(template.template_key(), replace(template, template_first_cube=9, template_last_cube=0).template_key())  # the range is read in order
        polynomial = MetricSettings()
        self.assertEqual(polynomial.template_key(), ())
        self.assertEqual(polynomial.template_key(), replace(polynomial, template_last_cube=99).template_key())  # an unused field does not spoil a cached trace
        stored = SimpleNamespace()
        self.assertNotEqual(trace_key(stored, "absorbance", template), trace_key(stored, "absorbance", replace(template, template_last_cube=20)))
        self.assertNotEqual(trace_key(stored, "absorbance", template), trace_key(stored, "absorbance", replace(template, fit_method="polynomial")))

    def test_a_session_written_before_the_template_shift_loads_with_the_defaults(self) -> None:
        old = asdict(MetricSettings())
        del old["template_first_cube"], old["template_last_cube"]
        loaded = _decode_settings(old, MetricSettings())
        self.assertEqual((loaded.template_first_cube, loaded.template_last_cube), (0, 9))
        saved = asdict(replace(MetricSettings(), fit_method="template", template_first_cube=4, template_last_cube=12))
        again = _decode_settings(saved, MetricSettings())
        self.assertEqual((again.fit_method, again.template_first_cube, again.template_last_cube), ("template", 4, 12))


class SpectraAndSensorgramAgreeTests(unittest.TestCase):
    """The Spectra panel (`scan_view.compute_spectra`, one cube) and the Sensorgram (`scan_trace.metric_trace`, every cube) must give the same number."""

    def stored(self, n_cube: int = 16, n_roi: int = 2):
        from lspr_imaging_app.analysis.scan_store import StoredScan

        rng = np.random.default_rng(11)
        shifts = np.r_[np.zeros(5), np.linspace(0.0, 7.0, n_cube - 5)]
        mean = np.zeros((n_cube, n_roi, WL.size, 2))
        for r in range(n_roi):
            a = band(WL[None, :], 580.0 + shifts[:, None] * (1 + 0.2 * r)) * (1 + 0.1 * r) + rng.normal(0.0, 0.002, (n_cube, WL.size))
            mean[:, r, :, 1] = 1000.0
            mean[:, r, :, 0] = 1000.0 * 10.0 ** (-a)
        shape = (n_cube, n_roi, WL.size, 2)
        return StoredScan(
            path=None, attrs={}, cube_indices=tuple(range(n_cube)), roi_ids=tuple(range(1, n_roi + 1)), wavelength_nm=tuple(WL),
            acquired_at_unix_ms=np.zeros(n_cube), values={"mean": mean}, std=np.full(shape, 5.0), n_valid=np.full(shape, 100, dtype=np.int32),
            n_nominal=np.full(shape, 110, dtype=np.int32), n_masked=np.zeros(shape, dtype=np.int32), cell_hash=np.ones((n_cube, n_roi), dtype=np.uint64),
            recipe_id=np.zeros((n_cube, n_roi), dtype=np.int32), layout_id=np.zeros((n_cube, n_roi), dtype=np.int32),
        )

    def test_the_two_panels_read_the_same_position(self) -> None:
        stored = self.stored()
        for cube in (0, 4, 9, 15):
            spectra = compute_spectra(stored, SpectraRequest((1, 2), cube, "absorbance", TEMPLATE_METRIC))
            for s in spectra:
                trace = metric_trace(stored, s.roi_id, "absorbance", TEMPLATE_METRIC)
                self.assertAlmostEqual(s.metric_wavelength_nm, float(trace[cube]), delta=1e-8, msg=f"cube {cube} roi {s.roi_id}")
                self.assertIsNotNone(s.fit_values)
                self.assertEqual(s.fit_wavelengths_nm[0], WL[0])
                self.assertIsNotNone(s.shift_nm)
                self.assertLess(abs(s.shift_nm - (s.metric_wavelength_nm - float(trace[:5].mean()))), 0.06)

    def test_a_scan_with_too_few_wavelengths_says_so(self) -> None:
        stored = self.stored()
        few = replace(
            stored, wavelength_nm=stored.wavelength_nm[:3], values={"mean": stored.values["mean"][:, :, :3, :]}, std=stored.std[:, :, :3, :],
            n_valid=stored.n_valid[:, :, :3, :], n_nominal=stored.n_nominal[:, :, :3, :], n_masked=stored.n_masked[:, :, :3, :],
        )
        spectra = compute_spectra(few, SpectraRequest((1,), 3, "absorbance", TEMPLATE_METRIC))
        self.assertIn("at least 4 scanned wavelengths", spectra[0].note)
        self.assertIsNone(spectra[0].fit_values)
        self.assertEqual(spectra[0].values.size, 3)  # the measured points are still shown

    def test_a_reference_without_data_is_explained_not_drawn(self) -> None:
        stored = self.stored()
        spectra = compute_spectra(stored, SpectraRequest((1,), 3, "absorbance", replace(TEMPLATE_METRIC, template_first_cube=100, template_last_cube=120)))
        self.assertIn("reference cubes 100-120", spectra[0].note)
        self.assertIsNone(spectra[0].fit_values)
        self.assertIsNone(spectra[0].metric_wavelength_nm)
        self.assertGreater(spectra[0].values.size, 0)  # the measured points are still shown


if __name__ == "__main__":
    unittest.main()
