"""Regression tests for AnalysisWorkerMixin's dark-frame-impact test (the
"Test dark-frame impact" button in Preferences > Wavelength handling, next
to "Treat 0 nm as a dark reference frame") - specifically the two pure
functions the button's background computation reduces to:
dark_frame_with_without_metrics (fit/metric with vs. without a 0 nm frame)
and format_dark_frame_impact_result (the resulting message text). Split out
for testability the same way this module's other orchestration/pure-compute
functions already are.

Built from a real incident: a user running Fitting=Poly(11)/Metric=Maximum
over a 470-720nm dataset that also had a genuine 0 nm dark/calibration frame
(mean pixel value ~9, vs ~45,000 for the real spectral frames) got a
sensorgram trace pinned around 55nm - the polynomial fit, correctly doing
what it was told, was dragged off by that one extreme low-signal outlier.
See docs/qthreadpool_zarr_crash_investigation.md's sibling
bulk_analysis_performance_investigation.md update, and this test's own
synthetic reproduction below.
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
    dark_frame_with_without_metrics,
    format_dark_frame_impact_result,
)


def _synthetic_spectrum_with_dark_frame(dark_value: float = 50.0) -> tuple[np.ndarray, np.ndarray]:
    """A real absorbance peak centered at 600nm across 470-720nm (10nm
    steps, matching the real incident's dataset), plus one 0nm dark-frame
    point carrying `dark_value` - a large, unrealistic absorbance value,
    the kind a near-zero-count dark frame's sample/reference ratio can
    produce."""
    real_wl = np.arange(470.0, 721.0, 10.0)
    real_vals = 0.5 * np.exp(-((real_wl - 600.0) ** 2) / (2 * 40.0 ** 2)) + 0.05
    wavelengths = np.concatenate([[0.0], real_wl])
    values = np.concatenate([[dark_value], real_vals])
    return wavelengths, values


class DarkFrameWithWithoutMetricsTests(unittest.TestCase):
    def test_poly_maximum_is_dragged_off_by_dark_frame(self) -> None:
        """Reproduces the real incident synthetically: including the 0nm
        dark frame in an order-11 polynomial fit distorts the peak search
        far from the real ~600nm peak; excluding it recovers the real
        peak."""
        wavelengths, values = _synthetic_spectrum_with_dark_frame()
        with_value, without_value, span = dark_frame_with_without_metrics(
            wavelengths, values,
            fit_method_key="poly", metric_key="maximum", poly_order=11,
            wl_min=None, wl_max=None,
        )
        self.assertIsNotNone(with_value)
        self.assertIsNotNone(without_value)
        self.assertEqual(span, 250.0)  # 720 - 470, the real (non-dark) spectral range
        # Without the dark frame, the fit correctly finds the real peak.
        self.assertAlmostEqual(without_value, 600.0, delta=5.0)
        # With it, the result is dragged far outside the real 470-720nm range -
        # this is the exact failure mode reported (a ~55nm result from a
        # 470-720nm dataset).
        self.assertLess(with_value, 470.0)
        self.assertGreater(abs(with_value - without_value), 100.0)

    def test_no_dark_frame_present_gives_identical_results(self) -> None:
        """Sanity check: with no 0nm entry in the input at all, "with" and
        "without" must be the same computation (nothing to filter out)."""
        real_wl = np.arange(470.0, 721.0, 10.0)
        real_vals = 0.5 * np.exp(-((real_wl - 600.0) ** 2) / (2 * 40.0 ** 2)) + 0.05
        with_value, without_value, span = dark_frame_with_without_metrics(
            real_wl, real_vals,
            fit_method_key="poly", metric_key="maximum", poly_order=3,
            wl_min=None, wl_max=None,
        )
        self.assertEqual(with_value, without_value)
        self.assertEqual(span, 250.0)

    def test_negligible_dark_frame_effect_when_dark_value_is_realistic(self) -> None:
        """A dark frame whose absorbance value happens to be small/in-range
        (not an extreme outlier) should shift the result only slightly -
        confirms this isn't flagging every 0nm frame as dangerous
        regardless of its actual value, only ones that actually distort
        the fit."""
        wavelengths, values = _synthetic_spectrum_with_dark_frame(dark_value=0.06)
        with_value, without_value, _span = dark_frame_with_without_metrics(
            wavelengths, values,
            fit_method_key="poly", metric_key="maximum", poly_order=3,
            wl_min=None, wl_max=None,
        )
        self.assertAlmostEqual(with_value, without_value, delta=10.0)

    def test_wavelength_range_filter_applies_to_both_sides_identically(self) -> None:
        """wl_min/wl_max (the Analysis section's own range filter) must be
        applied the same way to both the with- and without-dark-frame
        fits, so the comparison isolates only the dark frame's effect, not
        a range-setting difference. A range that itself excludes 0nm
        (e.g. wl_min=100) should make "with" and "without" agree, since
        the dark frame point never reaches the fit either way."""
        wavelengths, values = _synthetic_spectrum_with_dark_frame()
        with_value, without_value, _span = dark_frame_with_without_metrics(
            wavelengths, values,
            fit_method_key="poly", metric_key="maximum", poly_order=3,
            wl_min=100.0, wl_max=720.0,
        )
        self.assertEqual(with_value, without_value)

    def test_metric_none_fit_method_uses_raw_spectrum(self) -> None:
        """Fitting = None (metric read straight off the raw spectrum, no
        curve fit) must also be supported - the dark frame's raw absorbance
        value itself becomes a spurious "maximum" when included."""
        wavelengths, values = _synthetic_spectrum_with_dark_frame()
        with_value, without_value, _span = dark_frame_with_without_metrics(
            wavelengths, values,
            fit_method_key="none", metric_key="maximum", poly_order=3,
            wl_min=None, wl_max=None,
        )
        self.assertEqual(with_value, 0.0)  # argmax lands on the dark frame's own wavelength
        self.assertAlmostEqual(without_value, 600.0, delta=10.0)


class FormatDarkFrameImpactResultTests(unittest.TestCase):
    def test_large_shift_recommends_the_preference(self) -> None:
        text = format_dark_frame_impact_result(53.4, 601.2, 250.0, spectral_cube_index=0)
        self.assertIn("Recommend turning on", text)
        self.assertIn("219", text)  # ~219.1% of the 250nm range

    def test_small_shift_says_negligible(self) -> None:
        text = format_dark_frame_impact_result(600.1, 600.4, 250.0, spectral_cube_index=3)
        self.assertIn("Negligible", text)

    def test_missing_with_value_reports_which_side_failed(self) -> None:
        text = format_dark_frame_impact_result(None, 600.4, 250.0, spectral_cube_index=3)
        self.assertIn("with", text)

    def test_missing_without_value_reports_which_side_failed(self) -> None:
        text = format_dark_frame_impact_result(53.4, None, 250.0, spectral_cube_index=3)
        self.assertIn("without", text)

    def test_zero_span_does_not_divide_by_zero(self) -> None:
        text = format_dark_frame_impact_result(53.4, 600.0, 0.0, spectral_cube_index=0)
        self.assertIn("too narrow", text)


if __name__ == "__main__":
    unittest.main()
