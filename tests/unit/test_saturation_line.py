"""Unit tests for the spectrum plot's red saturation-warning line
(gui/main_window_plotting.py's _update_saturation_line_for).

The line used to be positioned at its own hardcoded fraction
(SATURATION_WARNING_FRACTION), independent of AutoExposureSettings.saturation_fraction
- the auto-exposure procedure's actual "too bright" threshold. That let the two
silently disagree (see the maintainer's saturation-plateau investigation). The line
now reads window._auto_exposure_settings.saturation_fraction directly, so it always
reflects wherever auto-exposure would currently back off.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tests._paths import ensure_repo_paths


ensure_repo_paths()

import sys
from pathlib import Path

APP_SRC = Path(__file__).resolve().parents[2] / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.models import AutoExposureSettings, Spectrum
from lspr_app.gui.main_window_plotting import (
    DEFAULT_SATURATION_WARNING_FRACTION,
    _RAW_COUNTS_Y_LABEL,
    _update_saturation_line_for,
)


class _FakeInfiniteLine:
    def __init__(self) -> None:
        self.pos: float | None = None
        self.visible = True

    def setPos(self, pos: float) -> None:
        self.pos = float(pos)

    def setVisible(self, visible: bool) -> None:
        self.visible = bool(visible)


class _FakeSpectrometer:
    def __init__(self, max_intensity: float = 65535.0) -> None:
        self._max_intensity = max_intensity

    def max_intensity(self) -> float:
        return self._max_intensity


def _make_spectrum(y_label: str) -> Spectrum:
    return Spectrum(
        wavelengths_nm=[400.0, 500.0],
        values=[100.0, 200.0],
        y_label=y_label,
        acquired_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _make_window(*, max_intensity: float = 65535.0, saturation_fraction: float | None = 0.885):
    window = type("_FakeWindow", (), {})()
    window.saturation_line = _FakeInfiniteLine()
    window._spectrometer = _FakeSpectrometer(max_intensity)
    if saturation_fraction is not None:
        settings = AutoExposureSettings()
        settings.saturation_fraction = saturation_fraction
        window._auto_exposure_settings = settings
    return window


class SaturationLineTests(unittest.TestCase):
    def test_line_tracks_auto_exposures_saturation_fraction(self) -> None:
        window = _make_window(max_intensity=65535.0, saturation_fraction=0.885)

        _update_saturation_line_for(window, _make_spectrum(_RAW_COUNTS_Y_LABEL))

        self.assertTrue(window.saturation_line.visible)
        self.assertAlmostEqual(window.saturation_line.pos, 65535.0 * 0.885)

    def test_line_follows_a_saturation_fraction_change(self) -> None:
        # The whole point of reading it live instead of a hardcoded constant -
        # a future change to AutoExposureSettings.saturation_fraction (as just
        # happened when the target band was lowered) must move this line too,
        # with no separate code change required.
        window = _make_window(max_intensity=65535.0, saturation_fraction=0.95)
        _update_saturation_line_for(window, _make_spectrum(_RAW_COUNTS_Y_LABEL))
        self.assertAlmostEqual(window.saturation_line.pos, 65535.0 * 0.95)

        window._auto_exposure_settings.saturation_fraction = 0.885
        _update_saturation_line_for(window, _make_spectrum(_RAW_COUNTS_Y_LABEL))

        self.assertAlmostEqual(window.saturation_line.pos, 65535.0 * 0.885)

    def test_falls_back_to_default_fraction_when_auto_exposure_settings_missing(self) -> None:
        window = _make_window(max_intensity=65535.0, saturation_fraction=None)

        _update_saturation_line_for(window, _make_spectrum(_RAW_COUNTS_Y_LABEL))

        self.assertAlmostEqual(window.saturation_line.pos, 65535.0 * DEFAULT_SATURATION_WARNING_FRACTION)

    def test_hidden_for_non_raw_counts_plot(self) -> None:
        window = _make_window()

        _update_saturation_line_for(window, _make_spectrum("Absorbance"))

        self.assertFalse(window.saturation_line.visible)

    def test_hidden_when_processed_is_none(self) -> None:
        window = _make_window()

        _update_saturation_line_for(window, None)

        self.assertFalse(window.saturation_line.visible)


if __name__ == "__main__":
    unittest.main()
