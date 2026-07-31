"""Unit tests for the live auto-exposure procedure (gui/acquisition_controller.py).

Unlike the old implementation, auto-exposure never touches the spectrometer's
own hardware connection - it only reacts to spectra the live worker is
already producing (see auto_exposure_handle_live_frame_for) and nudges
integration_spin, which the existing live-settings-change wiring already
pushes to the live worker. These tests fake just enough of "window" to drive
that per-frame state machine directly, without a real Qt app or spectrometer.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from lspr_app.domain.models import AutoExposureSettings, Spectrum
from lspr_app.gui.acquisition_controller import (
    _AutoExposureState,
    _auto_exposure_begin,
    auto_exposure_handle_live_frame_for,
    set_manual_acquisition_buttons_enabled,
    start_auto_exposure_for,
)


class _FakeSpin:
    def __init__(self, value: float) -> None:
        self._value = float(value)
        self.set_calls: list[float] = []

    def value(self) -> float:
        return self._value

    def setValue(self, value: float) -> None:
        self._value = float(value)
        self.set_calls.append(float(value))

    def setEnabled(self, enabled: bool) -> None:
        pass


class _FakeButton:
    def __init__(self) -> None:
        self.enabled = True

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)


class _FakeLabel:
    def __init__(self) -> None:
        self.text = ""
        self.tooltip = ""
        self.style = ""

    def setText(self, text: str) -> None:
        self.text = text

    def setToolTip(self, tooltip: str) -> None:
        self.tooltip = tooltip

    def setStyleSheet(self, style: str) -> None:
        self.style = style


class _FakeSpectrometer:
    def __init__(self, max_intensity: float = 65535.0, limits_us=None) -> None:
        self._max_intensity = max_intensity
        self._limits_us = limits_us

    def max_intensity(self) -> float:
        return self._max_intensity

    def integration_time_limits_us(self):
        return self._limits_us


def _make_window(*, integration_ms: float = 50.0, max_intensity: float = 65535.0, limits_us=None):
    spin = _FakeSpin(integration_ms)
    window = type("_FakeWindow", (), {})()
    window.integration_spin = spin
    window.auto_integration_button = _FakeButton()
    window.acquire_dark_button = _FakeButton()
    window.acquire_reference_button = _FakeButton()
    window.auto_integration_status_label = _FakeLabel()
    window.status_label = _FakeLabel()
    window._spectrometer = _FakeSpectrometer(max_intensity=max_intensity, limits_us=limits_us)
    window._auto_exposure_settings = AutoExposureSettings()
    window._auto_exposure_state = None
    window._source_mode = "spectrometer"
    window._live_active = True
    window._pending_auto_exposure_start = False
    window._log_success = lambda *_a, **_k: None
    window._log_warning = lambda *_a, **_k: None
    window._start_live_acquisition = lambda: None
    return window


def _make_spectrum(values, integration_time_ms: float) -> Spectrum:
    return Spectrum(
        wavelengths_nm=[float(i) for i in range(len(values))],
        values=list(values),
        y_label="Intensity (counts)",
        acquired_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        metadata={"integration_time_ms": integration_time_ms},
    )


class AutoExposureBeginTests(unittest.TestCase):
    def test_begin_uses_current_spin_value_as_starting_point(self) -> None:
        window = _make_window(integration_ms=50.0)
        _auto_exposure_begin(window)

        self.assertTrue(window._auto_exposure_state.active)
        self.assertEqual(window._auto_exposure_state.requested_time_us, 50_000)
        self.assertFalse(window.auto_integration_button.enabled)
        self.assertEqual(window.integration_spin.set_calls, [])  # no change needed yet

    def test_begin_clamps_to_hardware_limits(self) -> None:
        # Device only supports down to 5 ms - narrower than our 1 ms config floor.
        window = _make_window(integration_ms=1.0, limits_us=(5_000, 2_000_000))
        _auto_exposure_begin(window)

        self.assertEqual(window._auto_exposure_state.requested_time_us, 5_000)
        self.assertEqual(window._auto_exposure_state.min_us, 5_000)
        self.assertEqual(window.integration_spin.value(), 5.0)


class AutoExposureFrameTests(unittest.TestCase):
    def test_stale_frame_is_ignored(self) -> None:
        window = _make_window()
        window._auto_exposure_state = _AutoExposureState(requested_time_us=50_000)
        spectrum = _make_spectrum([100.0, 200.0], integration_time_ms=10.0)  # old time

        auto_exposure_handle_live_frame_for(window, spectrum)

        self.assertTrue(window._auto_exposure_state.active)
        self.assertEqual(window._auto_exposure_state.iteration, 0)

    def test_converges_within_target_band_reports_done(self) -> None:
        window = _make_window(max_intensity=65535.0)
        window._auto_exposure_state = _AutoExposureState(requested_time_us=50_000)
        # 87% of full scale - inside the [85.5%, 88.5%) target band.
        peak = 65535.0 * 0.87
        spectrum = _make_spectrum([peak], integration_time_ms=50.0)

        auto_exposure_handle_live_frame_for(window, spectrum)

        self.assertFalse(window._auto_exposure_state.active)
        self.assertEqual(window.auto_integration_status_label.text, "Done")
        self.assertIn("ms", window.auto_integration_status_label.tooltip)
        self.assertTrue(window.auto_integration_button.enabled)

    def test_underexposed_scales_up_proportionally(self) -> None:
        window = _make_window(max_intensity=65535.0)
        window._auto_exposure_state = _AutoExposureState(requested_time_us=50_000)
        # 10% of full scale - well under the 85.5% band floor.
        peak = 65535.0 * 0.10
        spectrum = _make_spectrum([peak], integration_time_ms=50.0)

        auto_exposure_handle_live_frame_for(window, spectrum)

        state = window._auto_exposure_state
        self.assertTrue(state.active)
        self.assertEqual(state.iteration, 1)
        self.assertGreater(state.requested_time_us, 50_000)
        # Roughly a ~8.7x step (target 87% / measured 10%), proportional scaling.
        self.assertAlmostEqual(state.requested_time_us / 50_000, 8.7, delta=0.2)
        self.assertEqual(window.integration_spin.value(), state.requested_time_us / 1000.0)

    def test_overexposed_scales_down_proportionally(self) -> None:
        window = _make_window(max_intensity=65535.0)
        window._auto_exposure_state = _AutoExposureState(requested_time_us=200_000)
        # 100% of full scale - saturated.
        peak = 65535.0
        spectrum = _make_spectrum([peak], integration_time_ms=200.0)

        auto_exposure_handle_live_frame_for(window, spectrum)

        state = window._auto_exposure_state
        self.assertTrue(state.active)
        self.assertLess(state.requested_time_us, 200_000)

    def test_too_bright_at_min_bound_fails_with_bright_message(self) -> None:
        window = _make_window(max_intensity=65535.0)
        window._auto_exposure_state = _AutoExposureState(requested_time_us=1_000, min_us=1_000, max_us=1_000_000)
        spectrum = _make_spectrum([65535.0], integration_time_ms=1.0)

        auto_exposure_handle_live_frame_for(window, spectrum)

        self.assertFalse(window._auto_exposure_state.active)
        self.assertEqual(window.auto_integration_status_label.text, "Failed")
        self.assertIn("too bright", window.auto_integration_status_label.tooltip.lower())

    def test_too_dark_at_max_bound_fails_with_dark_message(self) -> None:
        window = _make_window(max_intensity=65535.0)
        window._auto_exposure_state = _AutoExposureState(
            requested_time_us=1_000_000, min_us=1_000, max_us=1_000_000
        )
        spectrum = _make_spectrum([0.0], integration_time_ms=1000.0)

        auto_exposure_handle_live_frame_for(window, spectrum)

        self.assertFalse(window._auto_exposure_state.active)
        self.assertEqual(window.auto_integration_status_label.text, "Failed")
        self.assertIn("too dark", window.auto_integration_status_label.tooltip.lower())

    def test_zero_signal_jumps_to_max_instead_of_dividing_by_zero(self) -> None:
        window = _make_window(max_intensity=65535.0)
        window._auto_exposure_state = _AutoExposureState(requested_time_us=50_000, min_us=1_000, max_us=1_000_000)
        spectrum = _make_spectrum([0.0, 0.0], integration_time_ms=50.0)

        auto_exposure_handle_live_frame_for(window, spectrum)

        self.assertTrue(window._auto_exposure_state.active)
        self.assertEqual(window._auto_exposure_state.requested_time_us, 1_000_000)

    def test_exceeding_max_iterations_fails_generically(self) -> None:
        window = _make_window(max_intensity=65535.0)
        cfg = window._auto_exposure_settings
        cfg.max_iterations = 2
        window._auto_exposure_state = _AutoExposureState(
            requested_time_us=50_000, min_us=1_000, max_us=1_000_000, iteration=2
        )
        # Under-band peak so it would normally take another step.
        spectrum = _make_spectrum([65535.0 * 0.10], integration_time_ms=50.0)

        auto_exposure_handle_live_frame_for(window, spectrum)

        self.assertFalse(window._auto_exposure_state.active)
        self.assertEqual(window.auto_integration_status_label.text, "Failed")
        self.assertIn("converge", window.auto_integration_status_label.tooltip.lower())


class StartAutoExposureForTests(unittest.TestCase):
    def test_simulation_source_is_rejected(self) -> None:
        window = _make_window()
        window._source_mode = "simulation"

        start_auto_exposure_for(window)

        self.assertIsNone(window._auto_exposure_state)

    def test_already_running_is_ignored(self) -> None:
        window = _make_window()
        window._auto_exposure_state = _AutoExposureState(requested_time_us=50_000)
        window._auto_exposure_state.active = True

        start_auto_exposure_for(window)

        # Still the same state object - _auto_exposure_begin was not re-invoked.
        self.assertEqual(window._auto_exposure_state.iteration, 0)

    def test_starts_live_first_when_not_already_live(self) -> None:
        window = _make_window()
        window._live_active = False
        started = []
        window._start_live_acquisition = lambda: started.append(True)

        start_auto_exposure_for(window)

        self.assertTrue(window._pending_auto_exposure_start)
        self.assertEqual(started, [True])
        # The run itself hasn't begun yet - that happens on the first live frame.
        self.assertIsNone(window._auto_exposure_state)


class ManualAcquisitionButtonsEnabledTests(unittest.TestCase):
    """Regression coverage for a real bug: start_live_acquisition calls
    set_measurement_buttons_enabled(False) (which disables auto_integration_button
    among others), then set_manual_acquisition_buttons_enabled(True) to bring
    dark/reference back for the "cache from live sample" flow. Auto exposure
    needs live acquisition running, so if it isn't in that same re-enabled
    bucket, the button stays disabled for as long as live acquisition runs -
    i.e. always, once a spectrometer is connected and its live view starts.
    """

    def test_enabling_manual_acquisition_buttons_also_reenables_auto_exposure(self) -> None:
        window = _make_window()
        window._source_mode = "spectrometer"
        window.auto_integration_button.setEnabled(False)  # as left by set_measurement_buttons_enabled(False)

        set_manual_acquisition_buttons_enabled(window, True)

        self.assertTrue(window.auto_integration_button.enabled)

    def test_stays_disabled_in_simulation_mode(self) -> None:
        window = _make_window()
        window._source_mode = "simulation"
        window.auto_integration_button.setEnabled(False)

        set_manual_acquisition_buttons_enabled(window, True)

        self.assertFalse(window.auto_integration_button.enabled)


if __name__ == "__main__":
    unittest.main()
