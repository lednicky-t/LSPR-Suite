"""Pure-logic tests for what the Spectra panel draws: error propagation (`analysis/spectrum_errors.py`), the per-ROI spectrum / fit / metric
from a stored scan (`analysis/scan_view.py`) and the staleness comparison (`scan_store.stale_cube_indices`). h5py + temp files, no Qt."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import scan_store
    from lspr_imaging_app.analysis.query import formula_spectrum, metric_value
    from lspr_imaging_app.analysis.scan_arrays import new_arrays
    from lspr_imaging_app.analysis.scan_fingerprint import cell_hashes
    from lspr_imaging_app.analysis.scan_view import SpectraRequest, compute_spectra
    from lspr_imaging_app.analysis.settings import MetricSettings
    from lspr_imaging_app.analysis.spectrum_errors import formula_errors, standard_error
    from tests.unit.test_lspri_analysis_scan_store import _App
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402


class ErrorPropagationTests(unittest.TestCase):
    def test_standard_error_is_std_over_root_n_and_nan_without_pixels(self) -> None:
        got = standard_error([4.0, 3.0, 2.0], [16, 0, 4])
        self.assertAlmostEqual(got[0], 1.0)
        self.assertTrue(np.isnan(got[1]))
        self.assertAlmostEqual(got[2], 1.0)

    def test_formula_errors_match_a_numerical_derivative_for_every_formula(self) -> None:
        s, r, ds, dr = np.array([900.0, 1200.0]), np.array([1000.0, 1100.0]), np.array([3.0, 5.0]), np.array([2.0, 4.0])
        for key in ("absorbance", "ratio", "relative_change", "mod_absorbance"):
            f = lambda sv, rv: formula_spectrum([500.0, 510.0], sv, rv, key).values  # noqa: E731
            h = 1e-5
            d_s = (f(s + h, r) - f(s - h, r)) / (2 * h)
            d_r = (f(s, r + h) - f(s, r - h)) / (2 * h)
            expected = np.sqrt((d_s * ds) ** 2 + (d_r * dr) ** 2)
            np.testing.assert_allclose(formula_errors(s, r, ds, dr, key), expected, rtol=1e-6, err_msg=key)

    def test_nan_in_gives_nan_out(self) -> None:
        out = formula_errors([900.0, np.nan], [1000.0, 1000.0], [3.0, 3.0], [2.0, 2.0], "absorbance")
        self.assertTrue(np.isfinite(out[0]) and np.isnan(out[1]))


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "scan.h5"
        self.app = _App(cubes=3)
        self.plan = self.app.plan()
        self.store = scan_store.ScanStore(self.path)
        self.store.prepare(self.plan)
        self.arrays = new_arrays(self.plan)
        wl = np.asarray(self.plan.wavelength_axis_nm)
        # cube 1: a clean absorption band near 510 nm for every ROI, different strength per ROI
        for p in range(3):
            for r in range(3):
                band = np.exp(-((wl - 510.0) / 15.0) ** 2) * (0.2 + 0.1 * r)
                self.arrays.values["mean"][p, r, :, 0] = 1000.0 * 10 ** (-band)  # sample
                self.arrays.values["mean"][p, r, :, 1] = 1000.0  # reference
                self.arrays.std[p, r] = 20.0
                self.arrays.n_valid[p, r] = 100
                self.arrays.n_nominal[p, r] = 110
        self.hashes = cell_hashes(self.plan)
        self.written = [0, 1]  # cube 2 is never scanned
        for p in self.written:
            self.arrays.done[p] = True
            self.store.write_cube(self.plan, self.arrays, p, self.hashes[p])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def stored(self):
        return scan_store.read_scan(self.path)

    def request(self, roi_ids=(1, 2, 3), cube=1, **metric) -> SpectraRequest:
        return SpectraRequest(tuple(roi_ids), cube, "absorbance", MetricSettings(**metric))


class SpectraTests(_Case):
    def test_the_spectrum_is_the_formula_of_the_stored_pair_with_errors(self) -> None:
        stored = self.stored()
        spectra = compute_spectra(stored, self.request(fit_method="none"))
        self.assertEqual([s.roi_id for s in spectra], [1, 2, 3])
        s = spectra[1]
        wl = np.asarray(stored.wavelength_nm)
        expected = np.exp(-((wl - 510.0) / 15.0) ** 2) * 0.3
        np.testing.assert_allclose(s.values, expected, rtol=1e-9, atol=1e-12)
        np.testing.assert_allclose(s.wavelengths_nm, wl)
        self.assertTrue(np.all(s.errors > 0) and np.all(np.isfinite(s.errors)))
        self.assertIsNone(s.fit_values)
        self.assertEqual(s.n_missing, 0)

    def test_no_fit_reads_the_metric_off_the_points_and_a_fit_gives_a_curve(self) -> None:
        stored = self.stored()
        none = compute_spectra(stored, self.request(fit_method="none", metric_key="maximum"))[0]
        self.assertEqual(none.metric_wavelength_nm, 510.0)
        fitted = compute_spectra(stored, self.request(fit_method="polynomial", poly_order=2, metric_key="maximum"))[0]
        self.assertIsNotNone(fitted.fit_values)
        self.assertEqual(len(fitted.fit_wavelengths_nm), len(fitted.fit_values))
        spectrum = formula_spectrum(fitted.wavelengths_nm, np.ones(3), np.ones(3), "absorbance")  # shape only
        self.assertEqual(spectrum.formula_key, "absorbance")
        x, y = metric_value(formula_spectrum(stored.wavelength_nm, 1000 * 10 ** (-fitted.values), np.full(3, 1000.0), "absorbance"),
                            "polynomial", "maximum", poly_order=2)
        self.assertAlmostEqual(fitted.metric_wavelength_nm, x, places=9)  # same answer as the query layer's own one-call form
        self.assertAlmostEqual(fitted.metric_value, y, places=9)

    def test_each_roi_is_computed_alone(self) -> None:
        stored = self.stored()
        alone = compute_spectra(stored, self.request(roi_ids=(2,), fit_method="polynomial"))[0]
        together = compute_spectra(stored, self.request(roi_ids=(1, 2, 3), fit_method="polynomial"))[1]
        np.testing.assert_array_equal(alone.values, together.values)
        np.testing.assert_array_equal(alone.fit_values, together.fit_values)
        self.assertEqual(alone.metric_wavelength_nm, together.metric_wavelength_nm)

    def test_a_wavelength_without_pixels_is_left_out_and_counted(self) -> None:
        arrays = self.arrays
        arrays.values["mean"][1, 0, 1, 0] = np.nan  # ROI 1, 510 nm, sample: no valid pixel
        arrays.n_valid[1, 0, 1, 0] = 0
        self.store.write_cube(self.plan, arrays, 1, self.hashes[1])
        s = compute_spectra(self.stored(), self.request(fit_method="polynomial"))[0]
        self.assertEqual(len(s.wavelengths_nm), 2)
        self.assertEqual(s.n_missing, 1)
        self.assertNotIn(510.0, list(s.wavelengths_nm))

    def test_a_cube_never_scanned_a_roi_not_in_the_file_and_a_cube_not_in_the_file_say_so(self) -> None:
        stored = self.stored()
        self.assertIn("not been scanned", compute_spectra(stored, self.request(cube=2))[0].note)
        self.assertIn("not in the scan file", compute_spectra(stored, self.request(roi_ids=(1, 99)))[1].note)
        self.assertIn("not in the scan file", compute_spectra(stored, self.request(cube=77))[0].note)
        self.assertEqual(compute_spectra(stored, self.request(cube=2))[0].values.size, 0)

    def test_a_cube_with_no_value_at_all_is_explained(self) -> None:
        self.arrays.values["mean"][1, 0, :, :] = np.nan
        self.store.write_cube(self.plan, self.arrays, 1, self.hashes[1])
        self.assertIn("No wavelength", compute_spectra(self.stored(), self.request())[0].note)

    def test_a_fit_that_cannot_be_made_does_not_stop_the_spectrum(self) -> None:
        for key in ("polynomial", "gaussian"):
            self.arrays.values["mean"][1, 0, :, 0] = np.where(np.arange(3) == 0, 900.0, np.nan)  # one usable wavelength
            self.store.write_cube(self.plan, self.arrays, 1, self.hashes[1])
            s = compute_spectra(self.stored(), self.request(fit_method=key))[0]
            self.assertEqual(len(s.values), 1, key)  # the spectrum is still there
            self.assertIsNone(s.fit_values, key)


class StalenessTests(_Case):
    def test_a_current_file_has_only_the_unscanned_cube_stale(self) -> None:
        stale, why = scan_store.stale_cube_indices(self.stored(), self.plan, self.hashes)
        self.assertEqual((stale, why), (frozenset({2}), ""))

    def test_a_changed_input_makes_exactly_its_cube_stale(self) -> None:
        self.app.affines[(1, 510.0)] = np.array([[1.002, 0.0, 0.3], [0.0, 1.0, 0.0]])
        changed = self.app.plan()
        stale, _ = scan_store.stale_cube_indices(self.stored(), changed, cell_hashes(changed))
        self.assertEqual(stale, frozenset({1, 2}))

    def test_another_roi_list_makes_everything_stale_and_says_why(self) -> None:
        other = _App(cubes=3, ids=(1, 2, 3, 4)).plan()
        stale, why = scan_store.stale_cube_indices(self.stored(), other, cell_hashes(other))
        self.assertEqual(stale, frozenset({0, 1, 2}))
        self.assertIn("ROIs", why)

    def test_a_subset_plan_is_compared_by_dataset_cube_index(self) -> None:
        from lspr_imaging_app.analysis.scan_jobs import subset_plan

        part = subset_plan(self.plan, [2, 0])
        stale, _ = scan_store.stale_cube_indices(self.stored(), part, cell_hashes(part))
        self.assertEqual(stale, frozenset({2}))


if __name__ == "__main__":
    unittest.main()
