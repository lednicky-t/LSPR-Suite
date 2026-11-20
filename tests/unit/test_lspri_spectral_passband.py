"""Pure-logic tests for the spectral correction (`analysis/spectral_passband.py`, `analysis/passband_store.py`, 2026-11-20) and its wiring into the polynomial fit
(`query.fit_polynomial`, the batch route `trace_fit`, the order check, the Spectra and Sensorgram routes). No Qt. Method: the module docstring of
`spectral_passband.py` and `apps/LSPRi/eva/docs/spectral_correction_2026-11-20.md`.

The synthetic sweep has Gaussian bands whose centres sit at known offsets from their settings (a repeatable pattern like the real filter's) and whose widths grow with
the wavelength; everything the derivation returns is compared with those known numbers. Tolerances: the centroid of a sampled Gaussian is exact to ~1e-6 nm without noise;
with seeded noise (a few counts on a 30000-count peak) it is within 0.01 nm. The polynomial with the passband design matrix recovers a polynomial that was averaged over the
bands to 1e-6 of its coefficients' scale (noise-free); the exponent of the absorbance (the average is of 10^-A, the fit is linear in A) costs < 0.3 % of a peak height.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import gaussian_fit, voigt_fit
    from lspr_imaging_app.analysis import passband_store as ps
    from lspr_imaging_app.analysis import spectral_passband as sp
    from lspr_imaging_app.analysis.order_quality import loo_rms_all_orders
    from lspr_imaging_app.analysis.query import fit_polynomial, formula_spectrum
    from lspr_imaging_app.analysis.scan_store import StoredScan
    from lspr_imaging_app.analysis.scan_trace import trace_input, trace_key
    from lspr_imaging_app.analysis.scan_view import SpectraRequest, compute_spectra
    from lspr_imaging_app.analysis.settings import MetricSettings
    from lspr_imaging_app.analysis.trace_fit import trace_from_input, trace_from_input_per_cube
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

PIXEL = np.arange(300.0, 900.0, 0.35)
SETS = np.arange(420.0, 721.0, 5.0)
OFFSETS = 0.45 * np.sin(SETS / 17.0) - 0.25  # the known centre error of each setting, nm (a repeatable pattern, mean ~ -0.25)
SIGMAS = 1.6 + (SETS - 420.0) / 300.0 * 5.2  # 1.6 nm at 420 to 6.8 nm at 720, like the real filter
DARK = 120.0 + 0.0 * PIXEL


def band_weights(set_nm: float, offset: float, sigma: float) -> np.ndarray:
    """The true band on the pixel axis, area-normalised in nm."""
    w = np.exp(-((PIXEL - (set_nm + offset)) ** 2) / (2.0 * sigma**2))
    return w / np.sum(w * np.gradient(PIXEL))


def sweep_csv(noise: float = 0.0, seed: int = 3, with_dark: bool = True, sets=SETS, offsets=OFFSETS, sigmas=SIGMAS) -> bytes:
    rng = np.random.default_rng(seed)
    lines = ["requested_nm,confirmed_nm,settle_ms,temperature_c," + ",".join(f"{p:.3f}" for p in PIXEL)]
    if with_dark:
        lines.append("DARK,,,," + ",".join(f"{v:.4f}" for v in DARK))
    for s, o, g in zip(sets, offsets, sigmas):
        counts = DARK + 30000.0 * np.exp(-((PIXEL - (s + o)) ** 2) / (2.0 * g**2)) + rng.normal(0.0, noise, PIXEL.size)
        lines.append(f"{s:.3f},{s:.3f},25.00,30.80," + ",".join(f"{v:.4f}" for v in counts))
    return ("\n".join(lines) + "\n").encode()


def table(noise: float = 0.0, **kw) -> sp.PassbandTable:
    return ps.parse_sweep(sweep_csv(noise, **kw), "synthetic.csv", illumination="synthetic lamp")


class ParseTests(unittest.TestCase):
    def test_a_probe_sweep_is_read_as_it_is(self) -> None:
        t = table()
        self.assertEqual(t.counts.shape, (SETS.size, PIXEL.size))
        np.testing.assert_allclose(t.set_nm, SETS)
        np.testing.assert_allclose(t.pixel_nm, PIXEL, atol=1e-3)
        np.testing.assert_allclose(t.dark, DARK, atol=1e-3)
        self.assertAlmostEqual(t.mean_temperature_c, 30.8)
        self.assertEqual(t.illumination, "synthetic lamp")
        self.assertEqual(len(t.sha256), 64)

    def test_the_digest_follows_the_file(self) -> None:
        self.assertEqual(table().sha256, table().sha256)
        self.assertNotEqual(table().sha256, table(noise=1.0).sha256)

    def test_a_sweep_without_a_dark_row_has_no_dark(self) -> None:
        self.assertIsNone(table(with_dark=False).dark)

    def test_a_separate_dark_file_is_used(self) -> None:
        dark_csv = "wavelength_nm,intensity\n" + "\n".join(f"{p:.3f},{v:.4f}" for p, v in zip(PIXEL, DARK)) + "\n"
        t = ps.parse_sweep(sweep_csv(with_dark=False), "s.csv", dark_csv.encode())
        np.testing.assert_allclose(t.dark, DARK, atol=1e-3)
        self.assertNotEqual(t.sha256, table(with_dark=False).sha256)  # the digest covers both files

    def test_what_is_not_a_sweep_is_refused_with_a_sentence(self) -> None:
        with self.assertRaisesRegex(sp.PassbandError, "dark-spectrum file"):
            ps.parse_sweep(b"wavelength_nm,intensity\n500,1\n", "d.csv")
        with self.assertRaisesRegex(sp.PassbandError, "not a spectral sweep"):
            ps.parse_sweep(b"a,b,c\n1,2,3\n", "x.csv")
        with self.assertRaisesRegex(sp.PassbandError, "empty"):
            ps.parse_sweep(b"", "x.csv")

    def test_a_damaged_line_names_its_number(self) -> None:
        text = sweep_csv().decode().splitlines()
        text[5] = text[5][:-12]  # lose the last values of line 6
        with self.assertRaisesRegex(sp.PassbandError, "Line 6"):
            ps.parse_sweep("\n".join(text).encode(), "x.csv")
        text = sweep_csv().decode().splitlines()
        text[4] = text[4].replace("420.000", "abc", 1) if "420.000" in text[4] else text[4].replace(text[4].split(",")[0], "abc", 1)
        with self.assertRaisesRegex(sp.PassbandError, "Line 5"):
            ps.parse_sweep("\n".join(text).encode(), "x.csv")

    def test_a_repeated_setting_is_refused(self) -> None:
        sets = np.r_[SETS, SETS[10]]
        with self.assertRaisesRegex(sp.PassbandError, "same wavelength twice"):
            ps.parse_sweep(sweep_csv(sets=sets, offsets=np.r_[OFFSETS, 0.0], sigmas=np.r_[SIGMAS, 3.0]), "x.csv")

    def test_a_dark_spectrum_on_another_axis_is_refused(self) -> None:
        dark_csv = "wavelength_nm,intensity\n" + "\n".join(f"{p + 1.0:.3f},{v:.4f}" for p, v in zip(PIXEL, DARK)) + "\n"
        with self.assertRaisesRegex(sp.PassbandError, "not on the sweep's spectrometer axis"):
            ps.parse_sweep(sweep_csv(with_dark=False), "s.csv", dark_csv.encode())


class DeriveTests(unittest.TestCase):
    def test_centres_and_widths_of_known_bands_are_found(self) -> None:
        pb = sp.derive(table())
        self.assertTrue(pb.usable.all())
        np.testing.assert_allclose(pb.centroid_nm, SETS + OFFSETS, atol=2e-5)
        np.testing.assert_allclose(pb.sigma_nm, SIGMAS, rtol=2e-3)
        np.testing.assert_allclose(pb.fwhm_nm, 2.3548 * SIGMAS, rtol=0.02)

    def test_noise_moves_the_centres_by_far_less_than_the_offsets(self) -> None:
        pb = sp.derive(table(noise=6.0))
        self.assertLess(np.max(np.abs(pb.centroid_nm - (SETS + OFFSETS))), 0.02)

    def test_the_moments_are_those_of_the_band(self) -> None:
        pb = sp.derive(table())
        k = 30  # 570 nm
        np.testing.assert_allclose(pb.moments[k, :3], [1.0, 0.0, SIGMAS[k] ** 2], atol=2e-3 * SIGMAS[k] ** 2)
        self.assertAlmostEqual(pb.moments[k, 3] / SIGMAS[k] ** 3, 0.0, delta=1e-3)  # a Gaussian is symmetric
        self.assertAlmostEqual(pb.moments[k, 4] / SIGMAS[k] ** 4, 3.0, delta=0.02)  # its fourth moment is 3 sigma^4

    def test_a_weak_row_has_no_band(self) -> None:
        t = table(noise=6.0)
        counts = t.counts.copy()
        counts[7] = DARK + np.random.default_rng(1).normal(0.0, 6.0, PIXEL.size)  # no light at all
        pb = sp.derive(replace(t, counts=counts))
        self.assertFalse(pb.usable[7])
        self.assertIn("no clear band", pb.why[7])
        np.testing.assert_array_equal(pb.moments[7], sp.POINT_MOMENTS)
        self.assertTrue(np.all(pb.usable[np.arange(SETS.size) != 7]))

    def test_a_band_far_from_its_setting_is_refused(self) -> None:
        pb = sp.derive(table(offsets=np.where(np.arange(SETS.size) == 12, 25.0, OFFSETS)))
        self.assertFalse(pb.usable[12])
        self.assertIn("centred", pb.why[12])

    def test_the_derivation_does_not_touch_the_rows(self) -> None:
        t = table(noise=3.0)
        before = t.counts.copy()
        sp.derive(t)
        np.testing.assert_array_equal(t.counts, before)

    def test_describe_reports_the_coverage_of_a_scan(self) -> None:
        pb = sp.derive(table())
        d = sp.describe(pb, np.arange(470.0, 721.0, 10.0))
        self.assertEqual((d["scan_points"], d["scan_covered"]), (26, 26))
        d = sp.describe(pb, np.array([470.0, 472.0, 480.0, 1000.0]))  # 472 is not a setting; 1000 is out of range
        self.assertEqual(d["scan_covered"], 2)
        self.assertAlmostEqual(d["shift_mean_nm"], float(np.mean(OFFSETS)), delta=1e-4)


class AxisTests(unittest.TestCase):
    wl = np.arange(470.0, 721.0, 10.0)

    def setUp(self) -> None:
        self.pb = sp.derive(table())

    def test_none_is_the_nominal_axis(self) -> None:
        for passbands in (self.pb, None):
            ax = sp.correction_axis(passbands, self.wl, "none")
            np.testing.assert_array_equal(ax.x_nm, self.wl)
            self.assertIsNone(ax.moments)
            self.assertEqual((ax.key, ax.n_covered), ("none", 0))

    def test_centroid_moves_each_point_to_its_band(self) -> None:
        ax = sp.correction_axis(self.pb, self.wl, "centroid")
        idx = np.searchsorted(SETS, self.wl)
        np.testing.assert_allclose(ax.x_nm, SETS[idx] + OFFSETS[idx], atol=2e-5)
        self.assertIsNone(ax.moments)
        self.assertEqual(ax.n_covered, 26)

    def test_profile_also_carries_the_moments(self) -> None:
        ax = sp.correction_axis(self.pb, self.wl, "profile")
        self.assertEqual(ax.moments.shape, (26, sp.MAX_ORDER + 1))
        np.testing.assert_allclose(ax.moments[:, 0], 1.0)

    def test_a_wavelength_with_no_band_stays_nominal_with_a_zero_width_band(self) -> None:
        wl = np.array([470.0, 472.0, 480.0])  # 472 is between two settings: never interpolated
        ax = sp.correction_axis(self.pb, wl, "profile")
        self.assertEqual(ax.covered.tolist(), [True, False, True])
        self.assertEqual(ax.x_nm[1], 472.0)
        np.testing.assert_array_equal(ax.moments[1], sp.POINT_MOMENTS)

    def test_the_key_changes_with_the_mode_and_the_table(self) -> None:
        keys = {sp.correction_axis(self.pb, self.wl, m).key for m in sp.MODES}
        self.assertEqual(len(keys), 3)
        other = sp.derive(table(noise=1.0))
        self.assertNotEqual(sp.correction_axis(self.pb, self.wl, "centroid").key, sp.correction_axis(other, self.wl, "centroid").key)

    def test_the_axis_describes_itself_for_the_record_beside_an_export(self) -> None:
        info = sp.correction_axis(self.pb, self.wl, "profile").info
        self.assertEqual((info["mode"], info["points_with_a_band"], info["points"]), ("profile", 26, 26))
        self.assertEqual((info["table"], info["illumination"], len(info["sha256"])), ("synthetic.csv", "synthetic lamp", 64))
        self.assertAlmostEqual(info["filter_temperature_c"], 30.8)
        self.assertEqual(info["algorithm_version"], sp.PASSBAND_ALGORITHM_VERSION)
        partial = sp.correction_axis(self.pb, np.array([470.0, 472.0, 480.0]), "centroid").info
        self.assertEqual((partial["points_with_a_band"], partial["points"]), (2, 3))
        self.assertEqual(sp.nominal_axis(self.wl).info, {})

    def test_an_unknown_mode_is_an_error_not_a_fallback(self) -> None:
        with self.assertRaises(ValueError):
            sp.correction_axis(self.pb, self.wl, "full")


class VanderTests(unittest.TestCase):
    def test_bands_of_zero_width_give_the_ordinary_vandermonde_matrix(self) -> None:
        x = np.linspace(470.0, 720.0, 26)
        middle, half = 595.0, 125.0
        zero = np.tile(sp.POINT_MOMENTS, (x.size, 1))
        np.testing.assert_allclose(sp.passband_vander(x, zero, 7, middle, half), np.polynomial.polynomial.polyvander((x - middle) / half, 7), atol=1e-14)

    def test_the_moment_formula_is_the_integral_of_the_band(self) -> None:
        pb = sp.derive(table())
        middle, half = 595.0, 125.0
        for k in (0, 17, 40, 60):
            w = band_weights(SETS[k], OFFSETS[k], SIGMAS[k])
            t = (PIXEL - middle) / half
            brute = np.array([np.sum(w * np.gradient(PIXEL) * t**m) for m in range(9)])
            got = sp.passband_vander(np.array([pb.centroid_nm[k]]), pb.moments[k : k + 1], 8, middle, half)[0]
            np.testing.assert_allclose(got, brute, atol=3e-6, rtol=1e-5)

    def test_too_high_an_order_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            sp.passband_vander(np.array([500.0]), sp.POINT_MOMENTS[None, :], sp.MAX_ORDER + 1, 500.0, 100.0)


def underlying(lam: np.ndarray) -> np.ndarray:
    """A cubic absorbance with a maximum near 612 nm, 0.30 high, over 470-720 nm."""
    t = (np.asarray(lam, dtype=np.float64) - 595.0) / 125.0
    return 0.30 + 0.12 * t - 0.45 * t * t - 0.10 * t**3


def true_peak_position() -> float:
    grid = np.linspace(470.0, 720.0, 250001)
    return float(grid[np.argmax(underlying(grid))])


def make_stored(n_cube: int = 5, wavelengths=None) -> StoredScan:
    """A scan whose samples are the passband averages (of 10^-A, the exact physics) of ``underlying``, with the true bands of the synthetic sweep."""
    wl = np.arange(470.0, 721.0, 10.0) if wavelengths is None else np.asarray(wavelengths, dtype=np.float64)
    idx = np.searchsorted(SETS, wl)
    tau = 10.0 ** (-underlying(PIXEL))
    sample = np.array([np.sum(band_weights(SETS[i], OFFSETS[i], SIGMAS[i]) * np.gradient(PIXEL) * tau) for i in idx])
    mean = np.zeros((n_cube, 1, wl.size, 2))
    mean[:, 0, :, 1] = 1000.0
    mean[:, 0, :, 0] = 1000.0 * sample[None, :]
    shape = mean.shape
    return StoredScan(
        path=None, attrs={}, cube_indices=tuple(range(n_cube)), roi_ids=(1,), wavelength_nm=tuple(wl), acquired_at_unix_ms=np.zeros(n_cube), values={"mean": mean},
        std=np.full(shape, 1.0), n_valid=np.full(shape, 100, dtype=np.int32), n_nominal=np.full(shape, 100, dtype=np.int32), n_masked=np.zeros(shape, dtype=np.int32),
        cell_hash=np.ones((n_cube, 1), dtype=np.uint64), recipe_id=np.zeros((n_cube, 1), dtype=np.int32), layout_id=np.zeros((n_cube, 1), dtype=np.int32),
    )


POLY3 = MetricSettings(fit_method="polynomial", poly_order=3, metric_key="maximum", metric_range="off")


class FitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pb = sp.derive(table())
        cls.stored = make_stored()

    def corrected(self, mode: str) -> StoredScan:
        return replace(self.stored, spectral=sp.correction_axis(self.pb, np.asarray(self.stored.wavelength_nm), mode))

    def peak(self, stored: StoredScan, order: int = 3) -> float:
        spectrum = compute_spectra(stored, SpectraRequest((1,), 0, "absorbance", replace(POLY3, poly_order=order)))[0]
        return float(spectrum.metric_wavelength_nm)

    def test_each_mode_is_closer_to_the_truth_than_the_one_before(self) -> None:
        """The worst position error over the polynomial orders 3-5 (measured 2026-11-20: none 0.04 / 0.35 / 0.51 nm, centroid 0.17 / 0.13 / 0.14, full profile 0.03 / 0.01 / 0.00).
        With the nominal axis the error depends on the order, because a higher order follows the point-to-point scatter of the band centres; with the centres it is steady but
        keeps the second-order term (sigma^2 / 2) A'' of the band width, which grows twenty-fold from 420 to 720 nm; the full profile removes it."""
        truth = true_peak_position()
        worst = {m: max(abs(self.peak(self.corrected(m), order) - truth) for order in (3, 4, 5)) for m in sp.MODES}
        self.assertLess(worst["centroid"], worst["none"])
        self.assertLess(worst["profile"], worst["centroid"])
        self.assertLess(worst["profile"], 0.05)  # nm; what is left is the exponent of the absorbance (see the module docstring)
        self.assertGreater(worst["none"], 0.3)  # the effect this correction exists for is not small

    def test_the_full_profile_recovers_the_underlying_cubic_exactly_for_the_ratio(self) -> None:
        """Sample / reference is exactly linear in the transmittance, so the band average of a cubic is a cubic with the passband design matrix: recovered to rounding.
        (Absorbance is the logarithm of the average: see `AbsorbanceApproximationTests`.)"""
        wl = np.asarray(self.stored.wavelength_nm)
        axis = sp.correction_axis(self.pb, wl, "profile")
        idx = np.searchsorted(SETS, wl)
        tau = 0.55 + 0.10 * ((PIXEL - 595.0) / 125.0) - 0.30 * ((PIXEL - 595.0) / 125.0) ** 2 - 0.05 * ((PIXEL - 595.0) / 125.0) ** 3
        ratio = np.array([np.sum(band_weights(SETS[i], OFFSETS[i], SIGMAS[i]) * np.gradient(PIXEL) * tau) for i in idx])
        spectrum = formula_spectrum(axis.x_nm, 1000.0 * ratio, np.full(wl.size, 1000.0), "ratio", axis.moments)
        fit = fit_polynomial(spectrum.wavelengths_nm, spectrum.values, 3, moments=spectrum.passband)
        grid = np.linspace(470.0, 720.0, 11)
        truth = np.interp(grid, PIXEL, tau)
        np.testing.assert_allclose(np.polynomial.Polynomial(fit.coefficients)(grid), truth, atol=2e-6)
        plain = fit_polynomial(spectrum.wavelengths_nm, spectrum.values, 3)  # the same centres, no band: the curvature term is left in
        self.assertGreater(np.max(np.abs(np.polynomial.Polynomial(plain.coefficients)(grid) - truth)), 3e-4)

    def test_the_curve_is_the_underlying_one_and_the_peak_is_higher_than_the_measured_points(self) -> None:
        none = compute_spectra(self.corrected("none"), SpectraRequest((1,), 0, "absorbance", POLY3))[0]
        full = compute_spectra(self.corrected("profile"), SpectraRequest((1,), 0, "absorbance", POLY3))[0]
        self.assertGreater(float(np.max(full.fit_values)), float(np.max(none.fit_values)) - 1e-9)
        self.assertAlmostEqual(float(np.max(full.fit_values)), 0.30 + 0.0, delta=0.06)

    def test_bands_of_zero_width_change_nothing(self) -> None:
        wl = np.linspace(470.0, 720.0, 26)
        rng = np.random.default_rng(2)
        y = underlying(wl) + rng.normal(0.0, 0.002, wl.size)
        zero = np.tile(sp.POINT_MOMENTS, (wl.size, 1))
        a = fit_polynomial(wl, y, 4)
        b = fit_polynomial(wl, y, 4, moments=zero)
        np.testing.assert_allclose(b.coefficients, a.coefficients, rtol=1e-8, atol=1e-10)
        self.assertAlmostEqual(b.peak_wavelength_nm, a.peak_wavelength_nm, delta=1e-7)

    def test_the_window_picks_the_same_bands_as_the_points(self) -> None:
        axis = sp.correction_axis(self.pb, np.asarray(self.stored.wavelength_nm), "profile")
        y = np.asarray(self.stored.values["mean"][0, 0, :, 0])
        values = -np.log10(y / 1000.0)
        full = fit_polynomial(axis.x_nm, values, 3, wl_min=520.0, wl_max=660.0, moments=axis.moments)
        inside = (axis.x_nm >= 520.0) & (axis.x_nm <= 660.0)
        direct = fit_polynomial(axis.x_nm[inside], values[inside], 3, moments=axis.moments[inside])
        np.testing.assert_allclose(full.coefficients, direct.coefficients, rtol=1e-9)

    def test_the_batch_route_equals_the_one_spectrum_route(self) -> None:
        for mode in sp.MODES:
            stored = self.corrected(mode)
            data = trace_input(stored, 1)
            for metric in (POLY3, replace(POLY3, metric_key="centroid"), replace(POLY3, poly_order=5), replace(POLY3, metric_key="centroid", metric_range="top_fraction")):
                batch = trace_from_input(data, "absorbance", metric)
                single = trace_from_input_per_cube(data, "absorbance", metric)
                np.testing.assert_allclose(batch, single, atol=1e-6, err_msg=f"{mode} {metric.metric_key} order {metric.poly_order}")
                spectra = compute_spectra(stored, SpectraRequest((1,), 2, "absorbance", metric))[0]
                self.assertAlmostEqual(spectra.metric_wavelength_nm, float(batch[2]), delta=1e-6)

    def test_a_fit_with_no_band_model_reads_the_centroid_axis_in_the_full_profile_mode(self) -> None:
        none_fit = replace(POLY3, fit_method="none", metric_key="maximum")  # read off the measured points: there is no curve to take the broadening out of
        self.assertEqual(self.peak_for(self.corrected("centroid"), none_fit), self.peak_for(self.corrected("profile"), none_fit))

    def peak_for(self, stored: StoredScan, metric: MetricSettings) -> float:
        return float(compute_spectra(stored, SpectraRequest((1,), 0, "absorbance", metric))[0].metric_wavelength_nm)

    def test_the_order_check_judges_the_same_model_the_fit_uses(self) -> None:
        axis = sp.correction_axis(self.pb, np.asarray(self.stored.wavelength_nm), "profile")
        values = np.log10(1000.0 / self.stored.values["mean"][:, 0, :, 0]).T  # [points, cubes]
        plain = loo_rms_all_orders(axis.x_nm, values)
        banded = loo_rms_all_orders(axis.x_nm, values, moments=axis.moments)
        self.assertEqual(plain.shape, banded.shape)
        self.assertFalse(np.allclose(plain[:6], banded[:6]))  # not the same design, so not the same errors
        # for the third order, the truth is exactly a cubic: with the bands the leave-one-out error is the exponent term only, far below the plain fit's
        self.assertLess(banded[2, 0], plain[2, 0])

    def test_the_scan_says_which_correction_its_numbers_carry(self) -> None:
        self.assertEqual(self.stored.spectral_info(), {"mode": "none"})
        info = self.corrected("centroid").spectral_info()
        self.assertEqual((info["mode"], info["points_with_a_band"]), ("centroid", 26))
        info["mode"] = "changed by a caller"
        self.assertEqual(self.corrected("centroid").spectral_info()["mode"], "centroid")  # a copy: the record cannot change the scan

    def test_the_trace_key_follows_the_correction(self) -> None:
        keys = {trace_key(self.corrected(m), "absorbance", POLY3) for m in sp.MODES}
        self.assertEqual(len(keys), 3)


class AbsorbanceApproximationTests(unittest.TestCase):
    def test_the_error_of_averaging_the_absorbance_instead_of_the_transmittance(self) -> None:
        """The exact average is of the transmittance; the passband fit averages the absorbance. The difference is about (ln 10 / 2) x the variance of A over the band: it grows
        with the peak height and with the band width, and it is largest on the flanks of a narrow peak in the red (measured 2026-11-20, as a share of the peak height: A = 0.1:
        <= 0.25 %; A = 0.3: <= 0.76 %; A = 1: <= 2.8 %, always for a 30 nm wide peak at 700 nm, where the band is 15 nm wide). It is even about the peak, so it does not move it."""
        for a0, bound in ((0.1, 0.0030), (0.3, 0.0100)):
            for width in (30.0, 40.0, 60.0, 100.0):
                worst = 0.0
                for k in range(0, SETS.size, 2):
                    w = band_weights(SETS[k], OFFSETS[k], SIGMAS[k]) * np.gradient(PIXEL)
                    a = a0 / (1.0 + ((PIXEL - SETS[k]) / (width / 2.0)) ** 2)  # the band sits on the peak top: the largest value of A, the largest variance
                    worst = max(worst, abs(-np.log10(np.sum(w * 10.0 ** (-a))) - np.sum(w * a)) / a0)
                self.assertLess(worst, bound, msg=f"A = {a0}, FWHM {width} nm")


class StoreTests(unittest.TestCase):
    def test_a_table_survives_the_session_file(self) -> None:
        t = table(noise=2.0)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / ps.FILE_NAME
            ps.write_table(path, t)
            back = ps.read_table(path)
            self.assertFalse(path.with_name(path.name + ".tmp").exists())
        for name in ("pixel_nm", "set_nm", "counts", "confirmed_nm", "settle_ms", "temperature_c", "dark"):
            np.testing.assert_array_equal(getattr(back, name), getattr(t, name), err_msg=name)
        self.assertEqual((back.source_name, back.sha256, back.illumination, back.imported_at), (t.source_name, t.sha256, t.illumination, t.imported_at))
        a, b = sp.derive(t), sp.derive(back)
        np.testing.assert_array_equal(a.centroid_nm, b.centroid_nm)

    def test_a_table_with_no_dark_survives_too(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / ps.FILE_NAME
            ps.write_table(path, table(with_dark=False))
            self.assertIsNone(ps.read_table(path).dark)

    def test_a_file_that_is_not_a_table_is_refused(self) -> None:
        import h5py

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "other.h5"
            with h5py.File(path, "w") as f:
                f.attrs["schema_name"] = "lspri_scan"
            with self.assertRaisesRegex(sp.PassbandError, "not a spectral-correction table"):
                ps.read_table(path)
            newer = Path(tmp) / "newer.h5"
            ps.write_table(newer, table())
            with h5py.File(newer, "a") as f:
                f.attrs["schema_version"] = "2.0"
            with self.assertRaisesRegex(sp.PassbandError, "format 2.0"):
                ps.read_table(newer)


class SessionBlockTests(unittest.TestCase):
    """The session's "spectral_correction" block (schema 1.13): written only with a table, read leniently, absent in an older file."""

    BLOCK = {"mode": "profile", "file": "spectral_correction/spectral_correction.h5", "sha256": "ab" * 32, "source_name": "s.csv", "illumination": "LED", "imported_at": "2026-11-20T10:00:00+01:00"}

    def setUp(self) -> None:
        import json

        from lspr_imaging_app.frame_naming import FrameNamingScheme
        from lspr_imaging_app.storage.session import SessionState, load_session, save_session, session_path

        self.json, self.SessionState, self.load, self.save, self.path = json, SessionState, load_session, save_session, session_path
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.naming = FrameNamingScheme.for_dataset([0, 1], [500.0, 550.0])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_block_goes_into_the_file_and_comes_back(self) -> None:
        self.save(self.root, self.SessionState(spectral_correction=dict(self.BLOCK)), self.naming)
        payload = self.json.loads(self.path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], "1.14")
        self.assertEqual(payload["spectral_correction"], self.BLOCK)
        self.assertEqual(self.load(self.root).spectral_correction, self.BLOCK)

    def test_a_session_without_a_table_writes_no_block(self) -> None:
        self.save(self.root, self.SessionState(), self.naming)
        self.assertNotIn("spectral_correction", self.json.loads(self.path(self.root).read_text(encoding="utf-8")))
        self.assertIsNone(self.load(self.root).spectral_correction)

    def test_a_1_12_file_loads_with_no_correction_and_a_broken_block_is_left_out(self) -> None:
        path = self.save(self.root, self.SessionState(spectral_correction=dict(self.BLOCK)), self.naming)
        payload = self.json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = "1.12"
        payload.pop("spectral_correction")
        path.write_text(self.json.dumps(payload), encoding="utf-8")
        self.assertIsNone(self.load(self.root).spectral_correction)
        for broken in ("a string", {"mode": "centroid"}, {"file": ""}, 7):  # no file name, or not a block at all: dropped, the session still opens
            payload["spectral_correction"] = broken
            path.write_text(self.json.dumps(payload), encoding="utf-8")
            self.assertIsNone(self.load(self.root).spectral_correction, repr(broken))


X26 = np.linspace(470.0, 720.0, 26)
HC = 1239.84198


def heavy_sweep_csv(core_scale: float = 0.45, wing_share: float = 0.15, wing_factor: float = 4.0) -> bytes:
    """A sweep whose bands are NOT Gaussian: a narrow core plus a wide wing (kurtosis ~ 8), like the real filter's heavy tails."""
    lines = ["requested_nm,confirmed_nm,settle_ms,temperature_c," + ",".join(f"{p:.3f}" for p in PIXEL), "DARK,,,," + ",".join(f"{v:.4f}" for v in DARK)]
    for s_, o, g in zip(SETS, OFFSETS, SIGMAS):
        d2 = (PIXEL - (s_ + o)) ** 2
        core = core_scale * g
        shape = (1.0 - wing_share) * np.exp(-d2 / (2.0 * core**2)) + wing_share * np.exp(-d2 / (2.0 * (wing_factor * core) ** 2))
        counts = DARK + 30000.0 * shape
        lines.append(f"{s_:.3f},{s_:.3f},25.00,30.80," + ",".join(f"{v:.4f}" for v in counts))
    return ("\n".join(lines) + "\n").encode()


def heavy_band_weights(k: int, core_scale: float = 0.45, wing_share: float = 0.15, wing_factor: float = 4.0) -> np.ndarray:
    d2 = (PIXEL - (SETS[k] + OFFSETS[k])) ** 2
    core = core_scale * SIGMAS[k]
    w = ((1.0 - wing_share) * np.exp(-d2 / (2.0 * core**2)) + wing_share * np.exp(-d2 / (2.0 * (wing_factor * core) ** 2))) * np.gradient(PIXEL)
    return w / w.sum()


def make_stored_ratio(tau_fn, n_cube: int = 5, n_roi: int = 2, noise: float = 0.002, seed: int = 4) -> StoredScan:
    """A scan whose samples are the band averages of ``tau_fn(lambda)`` (the ratio Is / Ir is exactly linear in it) through the true Gaussian bands of the synthetic sweep."""
    wl = np.arange(470.0, 721.0, 10.0)
    idx = np.searchsorted(SETS, wl)
    tau = tau_fn(PIXEL)
    sample = np.array([np.sum(band_weights(SETS[i], OFFSETS[i], SIGMAS[i]) * np.gradient(PIXEL) * tau) for i in idx])
    rng = np.random.default_rng(seed)
    mean = np.zeros((n_cube, n_roi, wl.size, 2))
    mean[..., 1] = 1000.0
    mean[..., 0] = 1000.0 * sample[None, None, :] * (1.0 + noise * rng.standard_normal((n_cube, n_roi, wl.size)))
    shape = mean.shape
    return StoredScan(
        path=None, attrs={}, cube_indices=tuple(range(n_cube)), roi_ids=tuple(range(1, n_roi + 1)), wavelength_nm=tuple(wl), acquired_at_unix_ms=np.zeros(n_cube), values={"mean": mean},
        std=np.full(shape, 1.0), n_valid=np.full(shape, 100, dtype=np.int32), n_nominal=np.full(shape, 100, dtype=np.int32), n_masked=np.zeros(shape, dtype=np.int32),
        cell_hash=np.ones((n_cube, n_roi), dtype=np.uint64), recipe_id=np.zeros((n_cube, n_roi), dtype=np.int32), layout_id=np.zeros((n_cube, n_roi), dtype=np.int32),
    )


GAUSS_TAU = lambda lam: 0.35 + 0.5 * np.exp(-((lam - 600.0) ** 2) / (2.0 * 46.0**2))  # a peak as wide as the Bulk data's (FWHM 108 nm), on a floor  # noqa: E731
VOIGT_PARAMS = (1.0, HC / 600.0, 0.09, 0.14, 0.30)  # amplitude, centre eV, sigma eV, gamma eV, offset


def voigt_tau(lam: np.ndarray) -> np.ndarray:
    return voigt_fit.model(lam, np.array([VOIGT_PARAMS]))[0]


class GaussianBandTests(unittest.TestCase):
    """The Gaussian fit averaged over each point's band (``gaussian_fit``: closed form for a Gaussian band)."""

    def test_the_derivatives_of_the_banded_bell_match_finite_differences(self) -> None:
        rng = np.random.default_rng(1)
        band_var = np.linspace(1.6, 7.0, 26) ** 2
        y = 0.4 * np.exp(-((X26 - 600.0) ** 2) / (2 * 30.0**2))[None, :] + rng.normal(0.0, 0.01, (8, 26))
        c, sg = rng.uniform(560.0, 640.0, 8), rng.uniform(20.0, 70.0, 8)  # arbitrary points, not optima: the derivatives must be right everywhere

        def cost(cc, ss):
            residual = gaussian_fit._linear_part(y, gaussian_fit._bell_at(X26, cc, ss, band_var))[2]
            return 0.5 * np.einsum("ij,ij->i", residual, residual)

        _a, _r, _sb, gradient, gauss_newton, newton = gaussian_fit._reduced_terms(X26, y, c, sg, band_var=band_var)
        h = 1.0e-4
        np.testing.assert_allclose(gradient[:, 0], (cost(c + h, sg) - cost(c - h, sg)) / (2 * h), atol=1e-9)
        np.testing.assert_allclose(gradient[:, 1], (cost(c, sg + h) - cost(c, sg - h)) / (2 * h), atol=1e-9)
        fd_cc = (cost(c + h, sg) - 2 * cost(c, sg) + cost(c - h, sg)) / h**2
        fd_ss = (cost(c, sg + h) - 2 * cost(c, sg) + cost(c, sg - h)) / h**2
        fd_cs = (cost(c + h, sg + h) - cost(c + h, sg - h) - cost(c - h, sg + h) + cost(c - h, sg - h)) / (4 * h * h)
        for exact, fd in ((newton[0], fd_cc), (newton[1], fd_cs), (newton[2], fd_ss)):
            np.testing.assert_allclose(exact, fd, rtol=5e-3, atol=1e-6)

    def test_bands_of_zero_width_take_the_original_code_path_and_change_nothing(self) -> None:
        self.assertIsNone(gaussian_fit._band_variance(None))
        self.assertIsNone(gaussian_fit._band_variance(np.zeros(26)))
        y = 0.4 * np.exp(-((X26 - 590.0) ** 2) / (2 * 40.0**2)) + 0.1 + np.random.default_rng(3).normal(0.0, 0.003, (6, 26))
        p0, ok0 = gaussian_fit.solve_gaussian(X26, y)
        p1, ok1 = gaussian_fit.solve_gaussian(X26, y, band_sigma=np.zeros(26))
        np.testing.assert_array_equal(p0, p1)  # bit for bit: no band is the old code
        np.testing.assert_array_equal(ok0, ok1)

    def test_a_peak_through_gaussian_bands_is_recovered_exactly_and_the_plain_fit_is_biased(self) -> None:
        band = np.linspace(1.6, 7.0, 26)
        truth = np.array([0.5, 603.0, 20.0, 0.35])  # amplitude, centre, sigma, offset (a narrow peak: the broadening shows)
        u = truth[2] ** 2 + band**2
        y = truth[0] * (truth[2] / np.sqrt(u)) * np.exp(-((X26 - truth[1]) ** 2) / (2 * u)) + truth[3]
        banded, ok = gaussian_fit.solve_gaussian(X26, y[None, :], band_sigma=band)
        self.assertTrue(ok[0])
        np.testing.assert_allclose(banded[0], truth, rtol=1e-7, atol=1e-7)
        plain, _ = gaussian_fit.solve_gaussian(X26, y[None, :])
        self.assertGreater(abs(plain[0, 2] - truth[2]), 0.1)  # the plain fit reads the peak too wide

    def test_bands_that_are_not_gaussian_leave_the_documented_small_error(self) -> None:
        """Heavy-wing bands (kurtosis ~ 8): the closed form is an approximation. For the Bulk data's peak (sigma 46 nm) it is within 0.5 % in width and 0.03 nm in position;
        for a narrower peak (sigma 20 nm, FWHM 47 nm) it is still better than ignoring the bands."""
        pb = sp.derive(ps.parse_sweep(heavy_sweep_csv(), "heavy.csv"))
        self.assertGreater(float(np.median(pb.moments[:, 4] / pb.moments[:, 2] ** 2)), 6.0)  # really not Gaussian
        idx = np.searchsorted(SETS, X26)
        for sigma_peak, bound in ((46.0, 0.005), (20.0, 0.012)):
            f = lambda lam: 0.35 + 0.5 * np.exp(-((lam - 600.0) ** 2) / (2.0 * sigma_peak**2))  # noqa: E731
            y = np.array([np.sum(heavy_band_weights(k) * f(PIXEL)) for k in idx])
            x = pb.centroid_nm[idx]
            banded, ok = gaussian_fit.solve_gaussian(x, y[None, :], band_sigma=pb.sigma_nm[idx])
            plain, _ = gaussian_fit.solve_gaussian(x, y[None, :])
            self.assertTrue(ok[0])
            err_b, err_p = abs(banded[0, 2] - sigma_peak) / sigma_peak, abs(plain[0, 2] - sigma_peak) / sigma_peak
            self.assertLess(err_b, bound, f"sigma {sigma_peak}: banded {err_b:.4f}")
            self.assertLess(err_b, err_p, f"sigma {sigma_peak}: banded {err_b:.4f} vs plain {err_p:.4f}")
            self.assertLess(abs(banded[0, 1] - 600.0), 0.03)


class VoigtBandTests(unittest.TestCase):
    """The Voigt fit (photon energy) averaged over each point's band (``voigt_fit``: a Gaussian band turns sigma into sqrt(sigma^2 + S^2), scaled by sigma / sigma')."""

    band_s = np.linspace(0.01, 0.04, 26)  # arbitrary energy widths of the bands, eV (the maths does not care where they come from)
    energy = HC / X26

    def test_the_hc_constant_is_the_one_of_the_voigt_fit(self) -> None:
        self.assertEqual(sp.HC_EV_NM, voigt_fit.HC_EV_NM)

    def test_the_gradient_of_the_banded_cost_matches_finite_differences(self) -> None:
        rng = np.random.default_rng(2)
        y = 0.5 * voigt_fit.profile(self.energy, np.array([2.05]), np.array([0.1]), np.array([0.12]), self.band_s)[0][None, :] + rng.normal(0.0, 0.01, (5, 26))
        c, s, g = rng.uniform(1.95, 2.15, 5), rng.uniform(0.05, 0.15, 5), rng.uniform(0.02, 0.2, 5)
        band_var = self.band_s**2

        def cost(cc, ss, gg):
            return voigt_fit._terms(self.energy, y, cc, ss, gg, band_var)[0]

        gradient = voigt_fit._terms(self.energy, y, c, s, g, band_var)[1]
        h = 1.0e-6
        for j, (dc, ds, dg) in enumerate(((h, 0, 0), (0, h, 0), (0, 0, h))):
            fd = (cost(c + dc, s + ds, g + dg) - cost(c - dc, s - ds, g - dg)) / (2 * h)
            np.testing.assert_allclose(gradient[:, j], fd, rtol=2e-4, atol=1e-8, err_msg=("centre", "sigma", "gamma")[j])

    def test_bands_of_zero_width_take_the_original_code_path_and_change_nothing(self) -> None:
        y = voigt_tau(X26)[None, :] + np.random.default_rng(5).normal(0.0, 0.003, (4, 26))
        p0, ok0 = voigt_fit.solve_voigt(X26, y)
        p1, ok1 = voigt_fit.solve_voigt(X26, y, band=(HC / X26, np.zeros(26)))
        np.testing.assert_array_equal(p0, p1)
        np.testing.assert_array_equal(ok0, ok1)

    def test_a_voigt_peak_through_gaussian_energy_bands_is_recovered_exactly(self) -> None:
        truth = np.array([0.6, 2.05, 0.09, 0.14, 0.1])  # amplitude, centre, sigma, gamma, offset
        shape = voigt_fit.profile(self.energy, truth[1:2], truth[2:3], truth[3:4], self.band_s)[0]
        y = truth[0] * shape + truth[4]
        banded, ok = voigt_fit.solve_voigt(X26, y[None, :], band=(self.energy, self.band_s))
        self.assertTrue(ok[0])
        np.testing.assert_allclose(banded[0], truth, rtol=1e-5, atol=1e-6)
        plain, ok_plain = voigt_fit.solve_voigt(X26, y[None, :])
        self.assertTrue(ok_plain[0])
        self.assertGreater(abs(plain[0, 2] - truth[2]) + abs(plain[0, 3] - truth[3]), 2e-3)  # ignoring the bands reads the widths wrong

    def test_the_scipy_fallback_gives_the_same_answer(self) -> None:
        truth = np.array([0.6, 2.05, 0.09, 0.14, 0.1])
        y = truth[0] * voigt_fit.profile(self.energy, truth[1:2], truth[2:3], truth[3:4], self.band_s)[0] + truth[4]
        p = voigt_fit.solve_voigt_scipy(X26, y, band=(self.energy, self.band_s))
        self.assertIsNotNone(p)
        np.testing.assert_allclose(p, truth, rtol=1e-4, atol=1e-5)


class GaussianVoigtPipelineTests(unittest.TestCase):
    """Spectra panel route (``compute_spectra``) and Sensorgram route (batch, ``trace_from_input``) with the full profile, on a scan made through the true bands."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.pb = sp.derive(table())
        cls.gauss = make_stored_ratio(GAUSS_TAU)
        cls.voigt = make_stored_ratio(voigt_tau)

    def corrected(self, stored: StoredScan, mode: str) -> StoredScan:
        return replace(stored, spectral=sp.correction_axis(self.pb, np.asarray(stored.wavelength_nm), mode))

    def test_the_full_profile_recovers_the_underlying_gaussian_and_the_other_modes_do_not(self) -> None:
        metric = MetricSettings(fit_method="gaussian", metric_key="maximum", metric_range="off")
        errors = {}
        for mode in sp.MODES:
            r = compute_spectra(self.corrected(self.gauss, mode), SpectraRequest((1, 2), 2, "ratio", metric))[0]
            errors[mode] = (abs(float(np.max(r.fit_values)) - 0.85), abs(r.metric_wavelength_nm - 600.0))
        self.assertLess(errors["profile"][0], 0.003)  # height of the underlying peak, within the noise of 0.2 %
        self.assertLess(errors["profile"][1], 0.3)
        self.assertGreater(errors["none"][0], errors["profile"][0] * 0.9)
        self.assertGreater(errors["centroid"][0], errors["profile"][0] * 0.9)

    def test_the_underlying_height_is_exact_without_noise(self) -> None:
        clean = make_stored_ratio(GAUSS_TAU, noise=0.0)
        metric = MetricSettings(fit_method="gaussian", metric_key="maximum", metric_range="off")
        none = compute_spectra(self.corrected(clean, "none"), SpectraRequest((1, 2), 0, "ratio", metric))[0]
        full = compute_spectra(self.corrected(clean, "profile"), SpectraRequest((1, 2), 0, "ratio", metric))[0]
        self.assertAlmostEqual(float(np.max(full.fit_values)), 0.85, delta=2e-5)  # exact: the bands of this synthetic sweep are Gaussian
        self.assertAlmostEqual(full.metric_wavelength_nm, 600.0, delta=2e-4)
        self.assertLess(float(np.max(none.fit_values)), 0.85 - 5e-4)  # the nominal axis reads the peak too low and too wide

    def test_the_voigt_fit_recovers_the_underlying_peak_in_energy(self) -> None:
        clean = make_stored_ratio(voigt_tau, noise=0.0)
        metric = MetricSettings(fit_method="voigt", metric_key="maximum", metric_range="off")
        none = compute_spectra(self.corrected(clean, "none"), SpectraRequest((1, 2), 0, "ratio", metric))[0]
        full = compute_spectra(self.corrected(clean, "profile"), SpectraRequest((1, 2), 0, "ratio", metric))[0]
        truth_height = float(voigt_tau(np.array([600.0]))[0])
        self.assertAlmostEqual(full.metric_wavelength_nm, 600.0, delta=0.05)  # the E-space band is a Gaussian only to ~1e-4 (the bands are Gaussian in nm)
        self.assertAlmostEqual(float(np.max(full.fit_values)), truth_height, delta=0.003)
        self.assertGreater(abs(float(np.max(none.fit_values)) - truth_height), abs(float(np.max(full.fit_values)) - truth_height))

    def test_the_batch_route_equals_the_one_spectrum_route(self) -> None:
        for name, stored, methods in (("gaussian", self.gauss, ("gaussian",)), ("voigt", self.voigt, ("voigt",))):
            for mode in ("centroid", "profile"):
                s = self.corrected(stored, mode)
                data = trace_input(s, 1)
                for method in methods:
                    for metric in (
                        MetricSettings(fit_method=method, metric_key="maximum", metric_range="off"),
                        MetricSettings(fit_method=method, metric_key="centroid", metric_range="off"),
                        MetricSettings(fit_method=method, metric_key="centroid", metric_range="top_fraction"),
                    ):
                        batch = trace_from_input(data, "ratio", metric)
                        single = trace_from_input_per_cube(data, "ratio", metric)
                        self.assertTrue(np.isfinite(batch).all(), f"{name} {mode} {metric.metric_key}")
                        np.testing.assert_allclose(batch, single, atol=2e-5, err_msg=f"{name} {mode} {metric.metric_key} {metric.metric_range}")
                        spectra = compute_spectra(s, SpectraRequest((1, 2), 2, "ratio", metric))[0]
                        self.assertAlmostEqual(spectra.metric_wavelength_nm, float(batch[2]), delta=2e-5, msg=f"{name} {mode} {metric.metric_key} (Spectra panel route)")

    def test_the_bands_reach_the_fit_through_the_trace_input(self) -> None:
        profile = trace_input(self.corrected(self.gauss, "profile"), 1)
        self.assertEqual(profile.band.shape, (26, 3))
        self.assertTrue(np.all(profile.band[:, 0] > 1.0))  # nm: the bands of the synthetic sweep are 1.6-7 nm wide
        self.assertTrue(np.all(np.abs(profile.band[:, 1] - HC / profile.wavelengths) < 0.01))  # eV, and near hc / wavelength
        self.assertIsNone(trace_input(self.corrected(self.gauss, "centroid"), 1).band)
        self.assertIsNone(trace_input(self.gauss, 1).band)

    def test_the_trace_with_the_full_profile_differs_from_the_centroid_one_only_where_the_peak_moves(self) -> None:
        metric = MetricSettings(fit_method="gaussian", metric_key="maximum", metric_range="off")
        a = trace_from_input(trace_input(self.corrected(self.gauss, "centroid"), 1), "ratio", metric)
        b = trace_from_input(trace_input(self.corrected(self.gauss, "profile"), 1), "ratio", metric)
        self.assertLess(float(np.max(np.abs(a - b))), 0.3)  # a position: the same peak, the band average changes widths and heights, not the centre
        self.assertGreater(float(np.max(np.abs(a - 600.0))), 0.0)


CAMERA_DIR = REPO_ROOT / "apps" / "LSPRi" / "eva" / "docs" / "camera_response"


def linear_response(slope: float, low: float = 380.0, high: float = 820.0) -> sp.CameraResponse:
    wl = np.arange(low, high + 1.0, 10.0)
    return sp.CameraResponse(wl, 1.0 + slope * (wl - 600.0), "linear.csv", "ab" * 32, "2026-11-20T10:00:00+01:00")


class CameraResponseFileTests(unittest.TestCase):
    def test_the_manufacturers_curves_of_the_repo_are_read(self) -> None:
        files = sorted(CAMERA_DIR.glob("*.csv"))
        self.assertEqual(len(files), 3, "the Basler a2A3840, the Basler acA5472 and the IDS curve")
        for path in files:
            response = ps.read_camera_response(path)
            peak = float(response.wavelength_nm[int(np.argmax(response.response))])
            self.assertTrue(500.0 <= peak <= 560.0, (path.name, peak))
            self.assertTrue(response.covers(420.0, 720.0), path.name)  # what the August sweep needs
            self.assertEqual((response.source_name, len(response.sha256)), (path.name, 64))

    def test_a_plain_file_with_a_header_and_comments(self) -> None:
        text = "# a comment\nwavelength_nm,relative_response,relative_to_peak\n400,0.5,0.5\n500,1.0,1.0\n600,0.9,0.9\n700,0.6,0.6\n800,0.3,0.3\n"
        r = ps.parse_camera_response(text.encode(), "c.csv")
        np.testing.assert_array_equal(r.wavelength_nm, [400, 500, 600, 700, 800])
        np.testing.assert_array_equal(r.response, [0.5, 1.0, 0.9, 0.6, 0.3])  # the second column: a third one is ignored

    def test_semicolons_tabs_and_spaces_are_separators_and_no_header_is_needed(self) -> None:
        text = "400;50\n500\t100\n600 90\n700;60\n800 30\n"
        r = ps.parse_camera_response(text.encode(), "c.txt")
        np.testing.assert_array_equal(r.response, [50, 100, 90, 60, 30])  # quantum efficiency in per cent does as well as a fraction

    def test_what_cannot_weight_a_band_is_refused_with_a_sentence(self) -> None:
        good = "400,1\n500,2\n600,3\n700,2\n800,1\n"
        cases = {
            "no data": ("# only a comment\n", "no camera response"),
            "too few points": ("400,1\n500,2\n", "at least 5 points"),
            "text in the middle": (good.replace("600,3", "six hundred,3"), "Line 3"),
            "a response that is not a number": (good.replace("600,3", "600,abc"), "Line 3"),
            "one value on a line": (good.replace("600,3", "600"), "Line 3"),
            "wavelengths that do not increase": (good.replace("600,3", "450,3"), "must increase"),
            "a repeated wavelength": (good.replace("600,3", "500,3"), "must increase"),
            "a zero response": (good.replace("600,3", "600,0"), "positive"),
            "a negative response": (good.replace("600,3", "600,-1"), "positive"),
        }
        for name, (text, fragment) in cases.items():
            with self.assertRaisesRegex(sp.PassbandError, fragment, msg=name):
                ps.parse_camera_response(text.encode(), "c.csv")

    def test_the_curve_is_normalised_and_held_at_its_ends(self) -> None:
        r = sp.CameraResponse(np.array([400.0, 500.0, 600.0, 700.0, 800.0]), np.array([20.0, 50.0, 100.0, 60.0, 30.0]))
        np.testing.assert_allclose(r.evaluate(np.array([350.0, 400.0, 450.0, 600.0, 900.0])), [0.2, 0.2, 0.35, 1.0, 0.3])
        self.assertTrue(r.covers(400.0, 800.0) and not r.covers(390.0, 800.0))

    def test_the_curve_survives_the_session_block_and_a_damaged_one_is_refused(self) -> None:
        r = linear_response(-0.002)
        back = sp.CameraResponse.from_payload(r.to_payload())
        np.testing.assert_array_equal(back.wavelength_nm, r.wavelength_nm)
        np.testing.assert_array_equal(back.response, r.response)
        self.assertEqual((back.source_name, back.sha256, back.imported_at), (r.source_name, r.sha256, r.imported_at))
        for bad in ("a string", {"response": [1, 2, 3, 4, 5]}, {"wavelength_nm": [1, 2, 3, 4, 5], "response": [1, 2, 3, 4]}, {"wavelength_nm": [1, 2, 3, 4, 5], "response": [1, 2, -3, 4, 5]}):
            with self.assertRaises(sp.PassbandError, msg=repr(bad)):
                sp.CameraResponse.from_payload(bad)


class CameraWeightingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table = table()
        cls.flat = sp.derive(cls.table)

    def test_a_sloped_response_moves_the_centre_by_sigma_squared_times_the_slope(self) -> None:
        """For a linear response R = 1 + s (lambda - 600) the weighted centre is exactly lambda_bar + s sigma^2 / R(lambda_bar) (the first moment of band x line)."""
        s = -0.003
        pb = sp.derive(self.table, linear_response(s))
        for k in (0, 20, 40, 60):
            c = self.flat.centroid_nm[k]
            expected = s * self.flat.sigma_nm[k] ** 2 / (1.0 + s * (c - 600.0))
            self.assertAlmostEqual(pb.centroid_nm[k] - c, expected, delta=2e-3 + 0.02 * abs(expected), msg=f"set {SETS[k]:g} nm")
        self.assertLess(pb.centroid_nm[60] - self.flat.centroid_nm[60], -0.08)  # 720 nm: sigma 6.8 nm, the real effect size

    def test_a_flat_response_changes_nothing(self) -> None:
        pb = sp.derive(self.table, sp.CameraResponse(np.arange(380.0, 821.0, 10.0), np.full(45, 3.7), "flat.csv", "cd" * 32))
        np.testing.assert_allclose(pb.centroid_nm, self.flat.centroid_nm, atol=1e-9)
        np.testing.assert_allclose(pb.moments, self.flat.moments, atol=1e-9)
        np.testing.assert_allclose(pb.camera_shift_nm(), 0.0, atol=1e-9)

    def test_the_centres_without_the_camera_are_kept_to_show_its_effect(self) -> None:
        pb = sp.derive(self.table, linear_response(-0.002))
        np.testing.assert_allclose(pb.centroid_flat_nm, self.flat.centroid_nm, atol=1e-12)
        np.testing.assert_allclose(pb.camera_shift_nm(), (pb.centroid_nm - self.flat.centroid_nm)[pb.usable])
        self.assertEqual(sp.describe(self.flat).get("camera"), None)
        d = sp.describe(pb)
        self.assertEqual(d["camera"], "linear.csv")
        self.assertTrue(d["camera_shift_min_nm"] < d["camera_shift_mean_nm"] < d["camera_shift_max_nm"])
        self.assertLess(d["camera_shift_min_nm"], 0.0)  # the response falls towards the red: the centres move blue

    def test_the_moments_are_about_the_weighted_centre(self) -> None:
        pb = sp.derive(self.table, linear_response(-0.004))
        np.testing.assert_allclose(pb.moments[:, 0], 1.0)
        np.testing.assert_allclose(pb.moments[:, 1], 0.0, atol=1e-9)  # the first central moment is zero about the NEW centre
        k = 60
        w = band_weights(SETS[k], OFFSETS[k], SIGMAS[k]) * np.gradient(PIXEL)
        w = w * np.interp(PIXEL, *[linear_response(-0.004).wavelength_nm, linear_response(-0.004).response / linear_response(-0.004).response.max()])
        w /= w.sum()
        c = float(np.sum(PIXEL * w))
        self.assertAlmostEqual(pb.centroid_nm[k], c, delta=0.02)
        self.assertAlmostEqual(pb.moments[k, 2], float(np.sum((PIXEL - c) ** 2 * w)), delta=0.05 * SIGMAS[k] ** 2 * 0.1)

    def test_a_response_that_does_not_cover_the_sweep_is_refused(self) -> None:
        with self.assertRaisesRegex(sp.PassbandError, "covers 450-820 nm; the sweep goes from 420 to 720"):
            sp.derive(self.table, linear_response(-0.002, low=450.0))
        with self.assertRaisesRegex(sp.PassbandError, "the sweep goes from 420 to 720"):
            sp.derive(self.table, linear_response(-0.002, high=700.0))

    def test_the_axis_key_and_record_name_the_camera(self) -> None:
        wl = np.arange(470.0, 721.0, 10.0)
        flat_axis = sp.correction_axis(self.flat, wl, "centroid")
        cam_axis = sp.correction_axis(sp.derive(self.table, linear_response(-0.002)), wl, "centroid")
        self.assertNotEqual(flat_axis.key, cam_axis.key)  # every cache of fitted traces must miss
        self.assertIn(":flat:", flat_axis.key)
        self.assertEqual((flat_axis.info["camera_response"], cam_axis.info["camera_response"]), (None, "linear.csv"))
        self.assertEqual(cam_axis.info["camera_response_sha256"], "ab" * 32)
        self.assertFalse(np.allclose(flat_axis.x_nm, cam_axis.x_nm))

    def test_the_effect_is_far_smaller_than_the_offsets_except_at_the_red_end(self) -> None:
        """A real-sized slope (-0.003 /nm at 700 nm, 0.001 /nm at 600): below 620 nm the camera moves the bands by less than 0.05 nm."""
        wl = np.arange(380.0, 821.0, 10.0)
        r = np.interp(wl, [380, 450, 530, 600, 650, 700, 750, 820], [0.55, 0.80, 1.0, 0.91, 0.83, 0.66, 0.58, 0.40])
        pb = sp.derive(self.table, sp.CameraResponse(wl, r, "typical.csv", "ef" * 32))
        shift = pb.centroid_nm - self.flat.centroid_nm
        self.assertLess(np.max(np.abs(shift[SETS < 620])), 0.05)
        self.assertLess(shift[-1], -0.05)


class SessionBlockCameraTests(unittest.TestCase):
    def setUp(self) -> None:
        import json

        from lspr_imaging_app.frame_naming import FrameNamingScheme
        from lspr_imaging_app.storage.session import SessionState, load_session, save_session, session_path

        self.json, self.SessionState, self.load, self.save, self.path = json, SessionState, load_session, save_session, session_path
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.naming = FrameNamingScheme.for_dataset([0, 1], [500.0, 550.0])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_block_with_only_a_camera_response_is_kept(self) -> None:
        block = {"mode": "none", "camera_response": linear_response(-0.002).to_payload()}
        self.save(self.root, self.SessionState(spectral_correction=block), self.naming)
        payload = self.json.loads(self.path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], "1.14")
        self.assertNotIn("file", payload["spectral_correction"])
        self.assertEqual(self.load(self.root).spectral_correction, block)

    def test_a_1_13_block_without_a_camera_still_loads(self) -> None:
        block = dict(SessionBlockTests.BLOCK)
        path = self.save(self.root, self.SessionState(spectral_correction=block), self.naming)
        payload = self.json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = "1.13"
        path.write_text(self.json.dumps(payload), encoding="utf-8")
        loaded = self.load(self.root).spectral_correction
        self.assertEqual(loaded, block)
        self.assertNotIn("camera_response", loaded)


REAL_SWEEP = REPO_ROOT / "spikes" / "lspri_acq_phase0" / "illumination_probe_results" / "spectral_sweep_20260807_154808.csv"


@unittest.skipUnless(REAL_SWEEP.exists(), "the August 2026 sweep is local scratch data (gitignored), not in a clean checkout")
class RealSweepTests(unittest.TestCase):
    def test_the_real_filter_is_what_the_analysis_of_2026_11_20_found(self) -> None:
        pb = sp.derive(ps.read_sweep(REAL_SWEEP))
        self.assertEqual(int(pb.usable.sum()), 61)
        d = sp.describe(pb, np.arange(470.0, 721.0, 10.0))
        self.assertEqual(d["scan_covered"], 26)
        self.assertTrue(-0.35 < d["shift_mean_nm"] < -0.10, d["shift_mean_nm"])
        self.assertTrue(0.3 < d["shift_sd_nm"] < 0.6, d["shift_sd_nm"])
        self.assertTrue(3.5 < d["fwhm_min_nm"] < 4.5 and 14.0 < d["fwhm_max_nm"] < 19.0, (d["fwhm_min_nm"], d["fwhm_max_nm"]))
        self.assertAlmostEqual(d["temperature_c"], 30.8, delta=0.1)


if __name__ == "__main__":
    unittest.main()
