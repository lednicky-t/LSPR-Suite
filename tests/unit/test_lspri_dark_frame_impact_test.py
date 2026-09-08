"""Regression tests for AnalysisWorkerMixin's dark-frame-impact test (the
"Test dark-frame impact" button in Preferences > Wavelength handling, next
to "Treat 0 nm as a dark reference frame") - specifically the two pure
functions the button's background computation reduces to:
dark_frame_pixel_impact (measures the dataset's 0 nm frame's own pixel
counts and simulates subtracting them from every real wavelength) and
format_dark_frame_impact_result (the resulting message text). Split out for
testability the same way this module's other orchestration/pure-compute
functions already are.

Built from a real incident: a user running Fitting=Poly(11)/Metric=Maximum
over a 470-720nm dataset that also had a genuine 0 nm dark/calibration frame
(mean pixel value ~9, vs ~45,000 for the real spectral frames) got a
sensorgram trace pinned around 55nm - the polynomial fit, correctly doing
what it was told, was dragged off by that one extreme low-signal outlier.
An earlier version of this test asked "how does the dark frame distort a
spectral fit" - the maintainer pointed out that's the wrong question (the
dark frame was never meant to be a spectral data point at all): the real
question is whether the dark frame's own pixel counts are large enough,
relative to the real signal, to matter if subtracted as a dark-current
offset correction - see dark_frame_pixel_impact's own docstring.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - some
# Qt objects are touched at import time.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.gui.analysis_worker_mixin import (
    DarkFramePixelImpact,
    dark_frame_pixel_impact,
    format_dark_frame_impact_result,
)


def _synthetic_spectrum(
    dark_sample: float = 20.0,
    dark_reference: float = 18.0,
    signal_scale: float = 1.0,
    hot_pixel: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, tuple[np.ndarray, np.ndarray]]]:
    """A real absorbance peak centered at 600nm across 470-720nm (10nm
    steps, matching the real incident's dataset), expressed as sample/
    reference pixel COUNTS (not pre-combined absorbance values - this test
    exercises the pixel-intensity question, not a fit), plus one 0nm
    dark-frame entry carrying `dark_sample`/`dark_reference` counts.
    `signal_scale` scales the real counts down to simulate a dimmer
    dataset where the same dark count matters proportionally more.
    Returns (wavelengths, sample, reference, reduced_values_by_method) -
    the exact shapes dark_frame_pixel_impact expects."""
    real_wl = np.arange(470.0, 721.0, 10.0)
    true_absorbance = 0.3 * np.exp(-((real_wl - 600.0) ** 2) / (2 * 40.0 ** 2)) + 0.02
    reference_counts = np.full(real_wl.size, 40000.0) * signal_scale
    sample_counts = reference_counts / (10.0 ** true_absorbance)

    wavelengths = np.concatenate([[0.0], real_wl])
    sample = np.concatenate([[dark_sample], sample_counts])
    reference = np.concatenate([[dark_reference], reference_counts])

    trimmed_sample = sample.copy()
    if hot_pixel:
        # Trimmed mean much lower than the plain mean at the dark index -
        # simulates a few hot/noisy pixels inflating the plain mean.
        trimmed_sample[0] = dark_sample * 0.4
    reduced_values_by_method = {"mean": (sample, reference), "trimmed_mean": (trimmed_sample, reference.copy())}
    return wavelengths, sample, reference, reduced_values_by_method


class DarkFramePixelImpactTests(unittest.TestCase):
    def test_bright_dataset_gives_negligible_impact(self) -> None:
        """A dark count of 20/18 counts against a well-lit ~40,000-count
        signal (the maintainer's own "20 counts can be negligible" framing)
        should show a tiny shift - the whole point of this test existing is
        to *not* cry wolf on an ordinary, harmless dark level."""
        wavelengths, sample, reference, reduced = _synthetic_spectrum(signal_scale=1.0)
        impact = dark_frame_pixel_impact(wavelengths, sample, reference, reduced, "absorbance")
        self.assertIsNotNone(impact)
        self.assertAlmostEqual(impact.dark_sample_mean, 20.0)
        self.assertAlmostEqual(impact.dark_reference_mean, 18.0)
        self.assertLess(impact.dark_as_percent_of_dimmest_signal, 1.0)
        # Shift should be tiny relative to the real spectrum's own range.
        self.assertLess(abs(impact.worst_shift) / impact.formula_value_range, 0.05)

    def test_dim_dataset_gives_significant_impact(self) -> None:
        """The same absolute dark count (20/18) against a much dimmer real
        signal (the maintainer's "situations when it could have a visible
        effect") should show a shift that's a meaningful fraction of the
        spectrum's own range - the exact scenario the test needs to be
        able to flag."""
        wavelengths, sample, reference, reduced = _synthetic_spectrum(signal_scale=1.0 / 400.0)
        impact = dark_frame_pixel_impact(wavelengths, sample, reference, reduced, "absorbance")
        self.assertIsNotNone(impact)
        self.assertGreater(impact.dark_as_percent_of_dimmest_signal, 20.0)
        self.assertGreater(abs(impact.worst_shift) / impact.formula_value_range, 0.05)

    def test_worst_wavelength_is_where_signal_is_dimmest_relative_to_dark(self) -> None:
        """The peak itself (600nm, where sample counts dip lowest due to
        absorbance) is where dark counts matter proportionally most -
        confirms worst_wavelength_nm actually finds a meaningful location,
        not just the first or last entry."""
        wavelengths, sample, reference, reduced = _synthetic_spectrum(signal_scale=1.0 / 400.0)
        impact = dark_frame_pixel_impact(wavelengths, sample, reference, reduced, "absorbance")
        self.assertAlmostEqual(impact.worst_wavelength_nm, 600.0, delta=10.0)

    def test_hot_pixel_ratio_flags_mean_trimmed_mean_divergence(self) -> None:
        wavelengths, sample, reference, reduced = _synthetic_spectrum(hot_pixel=True)
        impact = dark_frame_pixel_impact(wavelengths, sample, reference, reduced, "absorbance")
        self.assertIsNotNone(impact.dark_sample_hot_pixel_ratio)
        self.assertGreater(impact.dark_sample_hot_pixel_ratio, 0.15)

    def test_no_hot_pixel_when_mean_and_trimmed_mean_agree(self) -> None:
        wavelengths, sample, reference, reduced = _synthetic_spectrum(hot_pixel=False)
        impact = dark_frame_pixel_impact(wavelengths, sample, reference, reduced, "absorbance")
        self.assertAlmostEqual(impact.dark_sample_hot_pixel_ratio, 0.0, delta=1e-6)

    def test_no_dark_frame_present_returns_none(self) -> None:
        real_wl = np.arange(470.0, 721.0, 10.0)
        sample = np.full(real_wl.size, 30000.0)
        reference = np.full(real_wl.size, 40000.0)
        reduced = {"mean": (sample, reference), "trimmed_mean": (sample, reference)}
        self.assertIsNone(dark_frame_pixel_impact(real_wl, sample, reference, reduced, "absorbance"))

    def test_only_dark_frame_no_real_wavelengths_returns_none(self) -> None:
        wavelengths = np.asarray([0.0])
        sample = np.asarray([20.0])
        reference = np.asarray([18.0])
        reduced = {"mean": (sample, reference), "trimmed_mean": (sample, reference)}
        self.assertIsNone(dark_frame_pixel_impact(wavelengths, sample, reference, reduced, "absorbance"))

    def test_zero_dark_sample_mean_gives_no_hot_pixel_ratio(self) -> None:
        """Avoids a division-by-zero rather than crashing when the dark
        frame's own mean happens to be exactly zero."""
        wavelengths, sample, reference, reduced = _synthetic_spectrum(dark_sample=0.0)
        impact = dark_frame_pixel_impact(wavelengths, sample, reference, reduced, "absorbance")
        self.assertIsNone(impact.dark_sample_hot_pixel_ratio)

    def test_missing_trimmed_mean_entry_gives_no_hot_pixel_ratio(self) -> None:
        wavelengths, sample, reference, _reduced = _synthetic_spectrum()
        impact = dark_frame_pixel_impact(wavelengths, sample, reference, {"mean": (sample, reference)}, "absorbance")
        self.assertIsNone(impact.dark_sample_hot_pixel_ratio)


class FormatDarkFrameImpactResultTests(unittest.TestCase):
    def _impact(self, **overrides) -> DarkFramePixelImpact:
        defaults = dict(
            dark_sample_mean=20.0,
            dark_reference_mean=18.0,
            dark_sample_hot_pixel_ratio=0.0,
            dark_as_percent_of_dimmest_signal=0.1,
            worst_wavelength_nm=600.0,
            worst_shift=0.0003,
            mean_abs_shift=0.0001,
            formula_value_range=0.3,
        )
        defaults.update(overrides)
        return DarkFramePixelImpact(**defaults)

    def test_none_impact_reports_could_not_evaluate(self) -> None:
        text = format_dark_frame_impact_result(None, 0, "Absorbance")
        self.assertIn("could not evaluate", text)

    def test_significant_shift_recommends_action(self) -> None:
        impact = self._impact(worst_shift=0.15, formula_value_range=0.3)  # 50% of range
        text = format_dark_frame_impact_result(impact, 0, "Absorbance")
        self.assertIn("could visibly affect", text)

    def test_small_shift_says_negligible(self) -> None:
        impact = self._impact(worst_shift=0.0003, formula_value_range=0.3)  # 0.1% of range
        text = format_dark_frame_impact_result(impact, 3, "Absorbance")
        self.assertIn("Negligible", text)

    def test_hot_pixel_note_included_when_ratio_exceeds_threshold(self) -> None:
        impact = self._impact(dark_sample_hot_pixel_ratio=0.6)
        text = format_dark_frame_impact_result(impact, 0, "Absorbance")
        self.assertIn("hot/noisy pixels", text)

    def test_hot_pixel_note_omitted_when_ratio_is_none(self) -> None:
        impact = self._impact(dark_sample_hot_pixel_ratio=None)
        text = format_dark_frame_impact_result(impact, 0, "Absorbance")
        self.assertNotIn("hot/noisy pixels", text)

    def test_hot_pixel_note_omitted_when_ratio_below_threshold(self) -> None:
        impact = self._impact(dark_sample_hot_pixel_ratio=0.05)
        text = format_dark_frame_impact_result(impact, 0, "Absorbance")
        self.assertNotIn("hot/noisy pixels", text)

    def test_zero_range_does_not_divide_by_zero(self) -> None:
        impact = self._impact(formula_value_range=0.0, worst_shift=0.001)
        text = format_dark_frame_impact_result(impact, 0, "Absorbance")
        self.assertIn("too flat", text)

    def test_formula_label_appears_verbatim(self) -> None:
        impact = self._impact()
        text = format_dark_frame_impact_result(impact, 0, "mOD Absorbance (-1000 x log10(Is/Ir))")
        self.assertIn("mOD Absorbance (-1000 x log10(Is/Ir))", text)


if __name__ == "__main__":
    unittest.main()
