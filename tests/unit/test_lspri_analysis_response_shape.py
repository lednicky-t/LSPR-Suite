"""Pure-logic tests for the response-shape readout (`analysis/response_shape.py`, wired in `scan_trace.response_traces`): the matched-filter projection
on a change learned per ROI group, scaled so a pure 1 nm shift reads 1 nm. Synthetic absorbance bands; no Qt, no files."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()
APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.query import FormulaSpectrum, fit_spectrum
    from lspr_imaging_app.analysis.response_shape import ResponseError, calibrate, pool_rows, readout
    from lspr_imaging_app.analysis.scan_store import StoredScan
    from lspr_imaging_app.analysis.scan_trace import compute_traces, response_traces, trace_key
    from lspr_imaging_app.analysis.settings import MetricSettings
    from lspr_imaging_app.analysis.template_shift import template_peak_nm
    from lspr_imaging_app.analysis.trace_fit import TraceInput, traces_from_inputs
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

WL = np.linspace(470.0, 720.0, 26)
N_BASE, N_SIGNAL = 12, 10  # cubes 0-11 baseline, 12-21 signal


def band(centre: float, height: float = 0.3, width: float = 45.0) -> np.ndarray:
    return height * np.exp(-0.5 * ((WL - centre) / width) ** 2)


def values_for(shifts: np.ndarray, heights: np.ndarray | None = None, noise: float = 0.0, n_roi: int = 6, seed: int = 0) -> np.ndarray:
    """``[cube, roi, wavelength]``: every ROI a band at 580 + shift (its own small offset in centre), white noise optional."""
    rng = np.random.default_rng(seed)
    heights = np.full(len(shifts), 0.3) if heights is None else heights
    out = np.empty((len(shifts), n_roi, WL.size))
    for c, (s, h) in enumerate(zip(shifts, heights, strict=True)):
        for r in range(n_roi):
            out[c, r] = band(580.0 + r + s, h) + noise * rng.standard_normal(WL.size)
    return out


def masks(n: int) -> tuple[np.ndarray, np.ndarray]:
    cubes = np.arange(n)
    return cubes < N_BASE, (cubes >= N_BASE) & (cubes < N_BASE + N_SIGNAL)


class ReadoutTests(unittest.TestCase):
    def test_a_pure_shift_reads_in_nm_from_the_baseline_peak(self) -> None:
        shifts = np.r_[np.zeros(N_BASE), np.full(N_SIGNAL, 0.5), np.linspace(-0.4, 0.4, 9)]
        noisy_cal = values_for(shifts, noise=2e-4)  # the weights need a noise variance: learn them on noisy cubes
        base, signal = masks(len(shifts))
        cal = calibrate(range(6), WL, noisy_cal, base, signal)
        got = readout(cal, values_for(shifts))  # read noise-free spectra: the reference is the (noisy) baseline mean, so compare changes, not levels
        for r in range(6):
            np.testing.assert_allclose(got[r] - got[r, 0], shifts, atol=0.01)  # 1 nm of pure shift reads 1 nm (linear regime)
            peak = template_peak_nm(WL, band(580.0 + r))
            self.assertAlmostEqual(float(np.mean(readout(cal, noisy_cal)[r, :N_BASE])), peak, delta=0.1)  # the baseline reads its own peak (of a noisy 12-cube mean)

    def test_an_offset_reads_as_nothing(self) -> None:
        shifts = np.r_[np.zeros(N_BASE), np.full(N_SIGNAL, 0.5)]
        data = values_for(shifts, noise=2e-4)
        base, signal = masks(len(shifts))
        cal = calibrate(range(6), WL, data, base, signal)
        flat = values_for(np.zeros(3))
        np.testing.assert_allclose(readout(cal, flat + 0.01), readout(cal, flat), atol=1e-9)

    def test_an_amplitude_change_like_the_calibration_adds_to_the_shift_equivalent(self) -> None:
        shifts = np.r_[np.zeros(N_BASE), np.full(N_SIGNAL, 0.5)]
        heights = np.r_[np.full(N_BASE, 0.3), np.full(N_SIGNAL, 0.31)]
        data = values_for(shifts, heights, noise=2e-4)
        base, signal = masks(len(shifts))
        cal = calibrate(range(6), WL, data, base, signal)
        got = readout(cal, data)
        response = np.nanmean(got[:, signal], 1) - np.nanmean(got[:, base], 1)
        self.assertTrue(np.all(response > 0.5))  # more than the 0.5 nm shift: the amplitude counts (it is not a peak position)

    def test_an_incomplete_spectrum_is_a_gap(self) -> None:
        shifts = np.r_[np.zeros(N_BASE), np.full(N_SIGNAL, 0.5)]
        data = values_for(shifts, noise=2e-4)
        base, signal = masks(len(shifts))
        cal = calibrate(range(6), WL, data, base, signal)
        holed = data.copy()
        holed[3, 2, 5] = np.nan
        got = readout(cal, holed)
        self.assertTrue(np.isnan(got[2, 3]))
        self.assertTrue(np.isfinite(got[2, 4]) and np.isfinite(got[1, 3]))

    def test_the_noise_of_a_pure_shift_is_near_the_cramer_rao_floor(self) -> None:
        """For a pure shift the matched filter is the linearised least-squares shift: its scatter equals the bound sigma / ||d spectrum / d nm||."""
        n = 400
        shifts = np.r_[np.zeros(N_BASE * 10), np.full(N_SIGNAL * 10, 0.5), np.zeros(n)]
        sigma = 3e-4
        data = values_for(shifts, noise=sigma, seed=3)
        cubes = np.arange(len(shifts))
        base, signal = cubes < N_BASE * 10, (cubes >= N_BASE * 10) & (cubes < (N_BASE + N_SIGNAL) * 10)
        cal = calibrate(range(6), WL, data, base, signal)
        scatter = np.std(readout(cal, data)[0, -n:], ddof=1)
        d = np.gradient(band(580.0), WL)
        bound = sigma / np.linalg.norm(d - d.mean())
        self.assertLess(scatter / bound, 1.25)


class GroupTests(unittest.TestCase):
    def test_pool_rows_union_of_groups_and_everyone_for_no_group(self) -> None:
        rows = pool_rows([10, 11, 12, 13], [[10, 11], [11, 12], [99]])
        self.assertEqual(rows[0].tolist(), [0, 1])
        self.assertEqual(rows[1].tolist(), [0, 1, 2])  # in two groups: the union
        self.assertEqual(rows[3].tolist(), [0, 1, 2, 3])  # in none: every ROI

    def test_weights_are_learned_per_group(self) -> None:
        shifts = np.r_[np.zeros(N_BASE), np.full(N_SIGNAL, 0.5)]
        data = values_for(shifts, noise=2e-4)
        data[N_BASE:, 3:] = values_for(shifts, np.r_[np.full(N_BASE, 0.3), np.full(N_SIGNAL, 0.33)], noise=2e-4, seed=1)[N_BASE:, 3:]
        base, signal = masks(len(shifts))
        cal = calibrate(range(6), WL, data, base, signal, groups=[[0, 1, 2], [3, 4, 5]])
        self.assertTrue(np.allclose(cal.weights[0], cal.weights[1]))
        self.assertFalse(np.allclose(cal.weights[0], cal.weights[3], rtol=0.05))

    def test_errors_say_what_to_fix(self) -> None:
        data = values_for(np.zeros(N_BASE + N_SIGNAL), noise=2e-4)
        base, _signal = masks(N_BASE + N_SIGNAL)
        with self.assertRaisesRegex(ResponseError, "overlap"):
            calibrate(range(6), WL, data, base, base)
        with self.assertRaisesRegex(ResponseError, "two complete baseline cubes"):
            calibrate(range(6), WL, data, base, np.zeros(N_BASE + N_SIGNAL, bool))


def make_scan(n_roi: int = 6) -> StoredScan:
    shifts = np.r_[np.zeros(N_BASE), np.full(N_SIGNAL, 0.5)]
    absorbance = values_for(shifts, noise=2e-4, n_roi=n_roi)
    n_cube = absorbance.shape[0]
    mean = np.empty((n_cube, n_roi, WL.size, 2))
    mean[..., 1] = 1000.0
    mean[..., 0] = 1000.0 * 10 ** (-absorbance)
    shape = mean.shape
    return StoredScan(
        path=Path("memory.h5"), attrs={}, cube_indices=tuple(range(n_cube)), roi_ids=tuple(range(1, n_roi + 1)), wavelength_nm=tuple(WL.tolist()),
        acquired_at_unix_ms=np.zeros((n_cube, WL.size), dtype=np.int64), values={"mean": mean}, std=np.full(shape, 5.0),
        n_valid=np.full(shape, 100, dtype=np.uint32), n_nominal=np.full(shape, 100, dtype=np.uint32), n_masked=np.zeros(shape, dtype=np.uint32),
        cell_hash=np.ones((n_cube, n_roi), dtype=np.uint64), layout_id=np.zeros(n_cube, dtype=np.int32),
        recipe_id=np.zeros((n_cube, n_roi), dtype=np.int32), layouts={}, recipes={},
    )


RESPONSE = replace(MetricSettings(), fit_method="response", response_base_first=0, response_base_last=N_BASE - 1,
                   response_signal_first=N_BASE, response_signal_last=N_BASE + N_SIGNAL - 1)


class WiringTests(unittest.TestCase):
    def test_compute_traces_gives_the_response_traces_and_reads_the_shift(self) -> None:
        stored = make_scan()
        every = response_traces(stored, "absorbance", RESPONSE, [[1, 2, 3], [4, 5, 6]])
        new = compute_traces(stored, (2, 5), "absorbance", RESPONSE, {}, groups=[[1, 2, 3], [4, 5, 6]])
        self.assertEqual(sorted(new), [2, 5])
        np.testing.assert_array_equal(new[2], every[2])
        step = np.mean(every[2][N_BASE:]) - np.mean(every[2][:N_BASE])
        self.assertAlmostEqual(step, 0.5, delta=0.05)

    def test_the_trace_key_follows_the_groups_only_for_the_response_shape(self) -> None:
        stored = make_scan()
        a, b = [[1, 2, 3], [4, 5, 6]], [[1, 2], [3, 4, 5, 6]]
        self.assertNotEqual(trace_key(stored, "absorbance", RESPONSE, a), trace_key(stored, "absorbance", RESPONSE, b))
        poly = MetricSettings()
        self.assertEqual(trace_key(stored, "absorbance", poly, a), trace_key(stored, "absorbance", poly, b))
        self.assertNotEqual(trace_key(stored, "absorbance", RESPONSE, a), trace_key(stored, "absorbance", replace(RESPONSE, response_signal_last=N_BASE + 3), a))

    def test_no_signal_range_is_a_clear_error(self) -> None:
        with self.assertRaisesRegex(ResponseError, "signal cubes"):
            response_traces(make_scan(), "absorbance", replace(RESPONSE, response_signal_first=None), ())

    def test_the_per_roi_paths_refuse_it_rather_than_fall_back_to_another_method(self) -> None:
        data = TraceInput(WL, np.ones((2, WL.size)), np.ones((2, WL.size)), np.ones((2, WL.size), bool), np.ones(2, bool))
        with self.assertRaises(ValueError):
            traces_from_inputs([data], "absorbance", RESPONSE)
        with self.assertRaises(ValueError):
            fit_spectrum(FormulaSpectrum(WL, band(580.0), np.ones(WL.size), np.ones(WL.size)), "response")


if __name__ == "__main__":
    unittest.main()
