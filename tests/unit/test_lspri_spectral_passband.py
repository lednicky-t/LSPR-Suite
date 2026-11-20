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

    def test_gaussian_reads_the_centroid_axis_in_the_full_profile_mode(self) -> None:
        gauss = replace(POLY3, fit_method="gaussian", metric_key="maximum")
        a = self.peak_for(self.corrected("centroid"), gauss)
        b = self.peak_for(self.corrected("profile"), gauss)
        self.assertAlmostEqual(a, b, delta=1e-9)  # the Gaussian has no passband model yet: Full profile = Centroid there (the "i" text says so)

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
        self.assertEqual(payload["schema_version"], "1.13")
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
