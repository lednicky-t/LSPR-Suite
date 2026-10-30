"""Pure-logic tests for the batch metric fit (`analysis/trace_fit.py`, 2026-10-24): the metric of one ROI at every cube in one go must be the same
number as the per-cube reference (`query.fit_and_metric`, one spectrum at a time), for every setting, with gaps handled alike. No Qt, no files.

Tolerance: the batch keeps the polynomial in the scaled variable, the reference converts it to powers of the wavelength in nm, so they differ by
rounding error only (measured on the real scan, orders 2-6: <= 2.4e-7 nm). On this test's synthetic band (26 wavelengths, noise) the rounding
grows about tenfold per polynomial order: 4e-12 nm at order 1, 1e-9 at order 3, 1e-8 at order 4, and for the centroid at order 6 on the window
from 560 nm 9.9e-7 nm on the dev laptop (Python 3.14) and 1.55e-6 nm on the CI runner (Python 3.12, other CPU). A real difference is far larger
(2026-10-30, same data: fit window moved by one point 6.7 nm, order 5 instead of 6 0.18 nm, 'maximum' instead of 'centroid' 26 nm), so the
bound is 1e-5 nm: 6x above the worst rounding seen, 18000x below the smallest real difference. Until 2026-10-30 it was 1e-6 nm, which
the laptop already used 98.5 % of and the CI runner exceeded (red on CI)."""

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
    from lspr_imaging_app.analysis.settings import MetricSettings
    from lspr_imaging_app.analysis import trace_fit
    from lspr_imaging_app.analysis.trace_fit import TraceInput, trace_from_input, trace_from_input_per_cube, traces_from_inputs
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

WL = np.linspace(470.0, 720.0, 26)
TOLERANCE_NM = 1.0e-5  # see the docstring: rounding <= 1.6e-6 nm seen, the smallest real difference 0.18 nm


def make_input(n_cube: int = 20, seed: int = 1, *, unscanned=(), holes=None, shuffle=False) -> TraceInput:
    """A drifting absorption band with noise; ``holes`` = {cube: wavelength indices without a value}; ``unscanned`` = cubes never scanned."""
    rng = np.random.default_rng(seed)
    wl = WL.copy()
    centre = 560.0 + 3.0 * np.arange(n_cube)
    band = 0.25 * np.exp(-0.5 * ((wl[None, :] - centre[:, None]) / 35.0) ** 2)
    reference = 1000.0 + rng.normal(0.0, 1.0, (n_cube, wl.size))
    sample = reference * 10.0 ** (-band) + rng.normal(0.0, 0.5, (n_cube, wl.size))
    usable = np.ones(sample.shape, dtype=bool)
    for cube, indices in (holes or {}).items():
        usable[cube, list(indices)] = False
        sample[cube, list(indices)] = np.nan
    scanned = np.ones(n_cube, dtype=bool)
    scanned[list(unscanned)] = False
    if shuffle:  # the scan's wavelengths need not be ascending
        order = rng.permutation(wl.size)
        wl, sample, reference, usable = wl[order], sample[:, order], reference[:, order], usable[:, order]
    return TraceInput(wl, sample, reference, usable, scanned)


def assert_same(test: unittest.TestCase, data: TraceInput, formula: str, metric: MetricSettings) -> np.ndarray:
    batch = trace_from_input(data, formula, metric)
    reference = trace_from_input_per_cube(data, formula, metric)
    test.assertEqual(batch.shape, reference.shape)
    np.testing.assert_array_equal(np.isnan(batch), np.isnan(reference), err_msg="gaps differ")
    finite = np.isfinite(reference)
    np.testing.assert_allclose(batch[finite], reference[finite], rtol=0.0, atol=TOLERANCE_NM)
    return batch


class BatchEqualsPerCubeTests(unittest.TestCase):
    def test_every_polynomial_setting_gives_the_reference_numbers(self) -> None:
        data = make_input()
        for metric_key in ("maximum", "centroid"):
            for order in (1, 2, 3, 4, 6):
                for window in ((None, None), (500.0, 700.0), (540.0, 620.0), (None, 600.0), (560.0, None)):
                    with self.subTest(metric=metric_key, order=order, window=window):
                        m = MetricSettings(poly_order=order, fit_wl_min=window[0], fit_wl_max=window[1], metric_key=metric_key)
                        self.assertTrue(np.isfinite(assert_same(self, data, "absorbance", m)).any())

    def test_every_formula(self) -> None:
        data = make_input()
        for formula in ("absorbance", "ratio", "relative_change", "mod_absorbance"):
            with self.subTest(formula=formula):
                assert_same(self, data, formula, MetricSettings())

    def test_cubes_with_different_missing_wavelengths_are_fitted_in_their_own_groups(self) -> None:
        data = make_input(holes={2: [0, 1], 5: [10], 6: [10], 9: list(range(0, 26, 2)), 11: list(range(26))})
        batch = assert_same(self, data, "absorbance", MetricSettings())
        self.assertTrue(np.isnan(batch[11]))  # no value anywhere: a gap
        self.assertTrue(np.isfinite(batch[[0, 2, 5, 9]]).all())

    def test_unscanned_cubes_are_gaps(self) -> None:
        data = make_input(unscanned=(0, 3))
        batch = assert_same(self, data, "absorbance", MetricSettings())
        self.assertTrue(np.isnan(batch[[0, 3]]).all())

    def test_unsorted_wavelengths_are_fitted_in_wavelength_order(self) -> None:
        data = make_input(shuffle=True)
        assert_same(self, data, "absorbance", MetricSettings(metric_key="maximum"))
        assert_same(self, data, "absorbance", MetricSettings(metric_key="centroid", fit_wl_min=520.0, fit_wl_max=650.0))

    def test_windows_with_too_few_points_are_gaps(self) -> None:
        data = make_input()
        for window in ((560.0, 560.0), (560.0, 565.0), (800.0, 900.0)):  # one point, one point, none
            with self.subTest(window=window):
                m = MetricSettings(fit_wl_min=window[0], fit_wl_max=window[1])
                batch = assert_same(self, data, "absorbance", m)
                self.assertTrue(np.isnan(batch).all())

    def test_two_and_three_point_windows(self) -> None:
        data = make_input()
        for window in ((560.0, 570.0), (560.0, 580.0)):
            for key in ("maximum", "centroid"):
                with self.subTest(window=window, key=key):
                    assert_same(self, data, "absorbance", MetricSettings(fit_wl_min=window[0], fit_wl_max=window[1], metric_key=key))

    def test_the_maximum_can_sit_at_the_edge_of_the_window(self) -> None:
        wl = WL
        y = np.tile(-0.002 * (wl - 600.0) ** 2 * 0 + 0.001 * (wl - 470.0), (4, 1)) + 0.1  # rises to the right: maximum at the end
        reference = np.full(y.shape, 1000.0)
        data = TraceInput(wl, 1000.0 * 10.0 ** (-y), reference, np.ones(y.shape, bool), np.ones(4, bool))
        batch = assert_same(self, data, "absorbance", MetricSettings(poly_order=1))
        np.testing.assert_allclose(batch, wl[-1])

    def test_a_perfectly_flat_spectrum(self) -> None:
        wl = WL
        sample = np.full((3, wl.size), 500.0)
        data = TraceInput(wl, sample, np.full(sample.shape, 1000.0), np.ones(sample.shape, bool), np.ones(3, bool))
        assert_same(self, data, "absorbance", MetricSettings(metric_key="centroid"))  # the middle of the window
        # Every point of a flat curve is a maximum: which one rounding noise picks is arbitrary (the batch and the per-cube path may differ), but
        # it is always a wavelength inside the fit window.
        found = trace_from_input(data, "absorbance", MetricSettings(metric_key="maximum"))
        self.assertTrue(np.all((found >= wl[0]) & (found <= wl[-1])))

    def test_no_fit_goes_through_the_reference_path(self) -> None:
        data = make_input(n_cube=6)  # the Gaussian fit has its own tests: test_lspri_analysis_gaussian_fit.py
        m = MetricSettings(fit_method="none", metric_key="maximum")
        np.testing.assert_array_equal(trace_from_input(data, "absorbance", m), trace_from_input_per_cube(data, "absorbance", m))

    def test_an_unknown_metric_has_no_value(self) -> None:
        data = make_input(n_cube=4)
        batch = trace_from_input(data, "absorbance", MetricSettings(metric_key="nonsense"))
        self.assertTrue(np.isnan(batch).all())
        np.testing.assert_array_equal(np.isnan(batch), np.isnan(trace_from_input_per_cube(data, "absorbance", MetricSettings(metric_key="nonsense"))))

    def test_nothing_scanned_is_all_gaps(self) -> None:
        data = make_input(n_cube=3, unscanned=(0, 1, 2))
        self.assertTrue(np.isnan(trace_from_input(data, "absorbance", MetricSettings())).all())

    def test_several_rois_in_common_batches_give_each_roi_its_own_trace(self) -> None:
        inputs = [
            make_input(n_cube=9, seed=1), make_input(n_cube=14, seed=2, holes={3: [0, 1]}), make_input(n_cube=5, seed=3, unscanned=(1,)),
            make_input(n_cube=11, seed=4, holes={0: [10], 6: list(range(26))}),
        ]
        for metric in (MetricSettings(), MetricSettings(poly_order=4, metric_key="centroid"), MetricSettings(fit_method="gaussian"),
                       MetricSettings(fit_method="gaussian", metric_key="centroid", fit_wl_min=500.0, fit_wl_max=690.0)):
            alone = [trace_from_input(d, "absorbance", metric) for d in inputs]
            for rows in (trace_fit.BATCH_ROWS, 7, 1):  # one batch, batches that cut ROIs in the middle, one cube per batch
                with self.subTest(fit=metric.fit_method, metric=metric.metric_key, rows=rows), mock.patch.object(trace_fit, "BATCH_ROWS", rows):
                    together = traces_from_inputs(inputs, "absorbance", metric)
                self.assertEqual([t.shape for t in together], [t.shape for t in alone])
                for a, b in zip(alone, together):
                    np.testing.assert_allclose(b, a, rtol=0.0, atol=1e-6, equal_nan=True)

    def test_no_inputs_and_an_input_without_cubes(self) -> None:
        self.assertEqual(traces_from_inputs([], "absorbance", MetricSettings()), [])

    def test_the_inputs_are_not_modified(self) -> None:
        data = make_input(holes={1: [3]})
        before = [a.copy() for a in (data.wavelengths, data.sample, data.reference, data.usable, data.scanned)]
        trace_from_input(data, "absorbance", MetricSettings())
        for a, b in zip(before, (data.wavelengths, data.sample, data.reference, data.usable, data.scanned)):
            np.testing.assert_array_equal(a, b)


if __name__ == "__main__":
    unittest.main()
