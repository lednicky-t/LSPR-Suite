from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.models import ProcessingSettings, Spectrum
from lspr_app.domain.processing import (
    process_spectrum,
    processing_debug_mode_enabled,
    set_processing_debug_mode_enabled,
)
from lspr_app.gui.processing_helpers import compute_metric_nm, get_analysis_metrics


class ProcessingProfileTests(unittest.TestCase):
    def test_slow_processing_profile_logs_stage_breakdown(self) -> None:
        spectrum = Spectrum(
            wavelengths_nm=np.asarray([610.0, 620.0, 630.0], dtype=np.float64),
            values=np.asarray([1.0, 2.0, 3.0], dtype=np.float64),
            y_label="sample",
            acquired_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        settings = ProcessingSettings()
        previous = os.environ.get("LSPR_PROCESSING_SLOW_LOG_MS")
        previous_debug = processing_debug_mode_enabled()
        os.environ["LSPR_PROCESSING_SLOW_LOG_MS"] = "0"
        set_processing_debug_mode_enabled(True)
        try:
            with self.assertLogs("lspr_app.processing", level="INFO") as captured:
                processed, fit = process_spectrum(spectrum, settings)
        finally:
            set_processing_debug_mode_enabled(previous_debug)
            if previous is None:
                os.environ.pop("LSPR_PROCESSING_SLOW_LOG_MS", None)
            else:
                os.environ["LSPR_PROCESSING_SLOW_LOG_MS"] = previous

        self.assertIsNotNone(processed)
        self.assertIsNone(fit)
        self.assertTrue(any("Slow spectrum processing" in line for line in captured.output))
        self.assertTrue(any("sanitize=" in line for line in captured.output))
        self.assertTrue(any("fit=" in line for line in captured.output))
        self.assertTrue(any("wall/cpu" in line for line in captured.output))

    def test_slow_processing_profile_stays_silent_when_debug_mode_disabled(self) -> None:
        spectrum = Spectrum(
            wavelengths_nm=np.asarray([610.0, 620.0, 630.0], dtype=np.float64),
            values=np.asarray([1.0, 2.0, 3.0], dtype=np.float64),
            y_label="sample",
            acquired_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        settings = ProcessingSettings()
        previous = os.environ.get("LSPR_PROCESSING_SLOW_LOG_MS")
        previous_debug = processing_debug_mode_enabled()
        set_processing_debug_mode_enabled(False)
        os.environ["LSPR_PROCESSING_SLOW_LOG_MS"] = "0"
        try:
            with self.assertNoLogs("lspr_app.processing", level="INFO"):
                processed, fit = process_spectrum(spectrum, settings)
        finally:
            set_processing_debug_mode_enabled(previous_debug)
            if previous is None:
                os.environ.pop("LSPR_PROCESSING_SLOW_LOG_MS", None)
            else:
                os.environ["LSPR_PROCESSING_SLOW_LOG_MS"] = previous

        self.assertIsNotNone(processed)
        self.assertIsNone(fit)

    @staticmethod
    def _synthetic_absorbance_curve() -> tuple[np.ndarray, np.ndarray]:
        wavelengths = np.arange(400.0, 601.0, 1.0)
        values = 1.0 + 0.5 * np.exp(-((wavelengths - 525.0) ** 2) / (2.0 * 20.0**2))
        return wavelengths, values

    def test_linear_baseline_resists_a_single_noisy_endpoint_sample(self) -> None:
        """Regression test for the endpoint-noise-injection bug: a single
        glitched sample at one edge used to become the *entire* anchor for
        that end of the baseline (domain/processing.py's old
        `_linear_baseline`, which used `values[0]`/`values[-1]` directly),
        so the glitch's full magnitude leaked into every other point via the
        subtracted line's slope. Averaging a window at each end (the fix)
        dilutes a single glitch's influence by roughly the window size.
        """
        n = 201
        wavelengths = np.arange(400.0, 400.0 + n, 1.0)
        flat = np.full(n, 500.0, dtype=np.float64)
        glitched = flat.copy()
        glitched[0] = 1500.0  # a single-sample spike at the very first point
        spectrum = Spectrum(
            wavelengths_nm=wavelengths,
            values=glitched,
            y_label="Absorbance (a.u.)",
            acquired_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            metadata={"kind": "absorbance"},
        )
        settings = ProcessingSettings(
            wavelength_min_nm=400.0,
            wavelength_max_nm=400.0 + n - 1,
            baseline_method="linear",
            smoothing_method="none",
            fit_method="none",
        )

        processed, _ = process_spectrum(spectrum, settings)
        self.assertIsNotNone(processed)

        mid = n // 2
        actual_mid_deviation = abs(float(processed.values[mid]) - 0.0)

        # What the old single-sample-endpoint implementation would have done:
        # baseline = linspace(values[0], values[-1], n), i.e. the glitch's
        # full magnitude directly sets one end of the line.
        naive_baseline_mid = np.linspace(float(glitched[0]), float(glitched[-1]), n)[mid]
        naive_mid_deviation = abs(float(flat[mid] - naive_baseline_mid))

        # A single spike diluted across a window of size W contributes ~1/W
        # of its magnitude to that anchor - dividing by 3 here is a safely
        # conservative bound (well under the ~5x this configuration's
        # window actually achieves) so the test isn't brittle to the exact
        # window-size rounding, while still proving a real improvement.
        self.assertLess(actual_mid_deviation, naive_mid_deviation / 3.0)

    def test_narrowing_processing_range_does_not_change_overlapping_values(self) -> None:
        """Regression test for the crop-boundary-leakage bug: narrowing
        wavelength_min_nm must not change the processed value at any
        wavelength still inside the new range, even with baseline/smoothing
        enabled (see spectral_processing_pipeline_architecture.md).
        """
        wavelengths, values = self._synthetic_absorbance_curve()
        spectrum = Spectrum(
            wavelengths_nm=wavelengths,
            values=values,
            y_label="Absorbance (a.u.)",
            acquired_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            metadata={"kind": "absorbance"},
        )
        wide_settings = ProcessingSettings(
            wavelength_min_nm=450.0,
            wavelength_max_nm=600.0,
            baseline_method="linear",
            smoothing_method="moving_average",
            smoothing_window=21,
            fit_method="none",
        )
        narrow_settings = ProcessingSettings(
            wavelength_min_nm=460.0,
            wavelength_max_nm=600.0,
            baseline_method="linear",
            smoothing_method="moving_average",
            smoothing_window=21,
            fit_method="none",
        )

        wide_processed, _ = process_spectrum(spectrum, wide_settings)
        narrow_processed, _ = process_spectrum(spectrum, narrow_settings)

        self.assertIsNotNone(wide_processed)
        self.assertIsNotNone(narrow_processed)
        overlap = len(narrow_processed.wavelengths_nm)
        np.testing.assert_allclose(
            narrow_processed.wavelengths_nm,
            wide_processed.wavelengths_nm[-overlap:],
        )
        np.testing.assert_allclose(
            narrow_processed.values,
            wide_processed.values[-overlap:],
            atol=1e-12,
        )

    def test_baseline_and_smoothing_skipped_for_non_absorbance_kind(self) -> None:
        """Raw/Dark/Reference spectra must only ever be cropped, never
        baseline-corrected or smoothed, regardless of ProcessingSettings.
        """
        wavelengths, values = self._synthetic_absorbance_curve()
        spectrum = Spectrum(
            wavelengths_nm=wavelengths,
            values=values,
            y_label="Intensity (counts)",
            acquired_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            metadata={"kind": "sample"},
        )
        settings = ProcessingSettings(
            wavelength_min_nm=450.0,
            wavelength_max_nm=600.0,
            baseline_method="linear",
            smoothing_method="moving_average",
            smoothing_window=21,
            fit_method="none",
        )

        processed, _ = process_spectrum(spectrum, settings)

        self.assertIsNotNone(processed)
        mask = (wavelengths >= 450.0) & (wavelengths <= 600.0)
        np.testing.assert_allclose(processed.values, values[mask], atol=1e-12)

    def test_baseline_and_smoothing_still_apply_to_absorbance(self) -> None:
        """Sanity check that the kind-gate only blocks non-absorbance
        spectra - absorbance still gets baseline/smoothing applied.
        """
        wavelengths, values = self._synthetic_absorbance_curve()
        spectrum = Spectrum(
            wavelengths_nm=wavelengths,
            values=values,
            y_label="Absorbance (a.u.)",
            acquired_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            metadata={"kind": "absorbance"},
        )
        settings = ProcessingSettings(
            wavelength_min_nm=450.0,
            wavelength_max_nm=600.0,
            baseline_method="linear",
            smoothing_method="moving_average",
            smoothing_window=21,
            fit_method="none",
        )

        processed, _ = process_spectrum(spectrum, settings)

        self.assertIsNotNone(processed)
        mask = (wavelengths >= 450.0) & (wavelengths <= 600.0)
        self.assertFalse(np.allclose(processed.values, values[mask], atol=1e-6))

    def test_fitting_is_skipped_for_non_absorbance_kind(self) -> None:
        """Only Absorbance is ever trackable/fittable (see
        spectral_processing_pipeline_architecture.md) - a fit_method other
        than "none" must not produce a fit curve for Raw/Dark/Reference.
        """
        wavelengths, values = self._synthetic_absorbance_curve()
        spectrum = Spectrum(
            wavelengths_nm=wavelengths,
            values=values,
            y_label="Intensity (counts)",
            acquired_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            metadata={"kind": "sample"},
        )
        settings = ProcessingSettings(
            wavelength_min_nm=450.0,
            wavelength_max_nm=600.0,
            fit_method="poly",
            polynomial_order=2,
        )

        processed, fit = process_spectrum(spectrum, settings)

        self.assertIsNotNone(processed)
        self.assertIsNone(fit)

    def test_fitting_still_applies_to_absorbance(self) -> None:
        wavelengths, values = self._synthetic_absorbance_curve()
        spectrum = Spectrum(
            wavelengths_nm=wavelengths,
            values=values,
            y_label="Absorbance (a.u.)",
            acquired_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            metadata={"kind": "absorbance"},
        )
        settings = ProcessingSettings(
            wavelength_min_nm=450.0,
            wavelength_max_nm=600.0,
            fit_method="poly",
            polynomial_order=2,
        )

        processed, fit = process_spectrum(spectrum, settings)

        self.assertIsNotNone(processed)
        self.assertIsNotNone(fit)

    def test_all_nan_processed_spectrum_does_not_crash_peak_metrics(self) -> None:
        spectrum = Spectrum(
            wavelengths_nm=np.asarray([610.0, 620.0, 630.0], dtype=np.float64),
            values=np.asarray([np.nan, np.nan, np.nan], dtype=np.float64),
            y_label="sample",
            acquired_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        settings = ProcessingSettings()

        analysis = get_analysis_metrics(spectrum, None, settings)
        self.assertTrue(np.isnan(float(analysis["dense_max_nm"])))
        self.assertTrue(np.isnan(compute_metric_nm("poly_max", spectrum, None, settings)))
        self.assertTrue(np.isnan(compute_metric_nm("gaussian_center", spectrum, None, settings)))


if __name__ == "__main__":
    unittest.main()
