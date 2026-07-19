from __future__ import annotations

import queue
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
import sys

if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.models import AcquisitionSettings
from lspr_app.gui.acquisition_controller import (
    _restore_spectrometer_backend_after_live,
    _suspend_spectrometer_backend_for_live,
    _update_live_source_rate_from_event,
)
from lspr_app.gui.workers import AcquisitionResult, LiveAcquisitionEvent


class _FakeSpectrometer:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def capabilities(self):
        return SimpleNamespace(marker="capabilities")


class _DummyContext:
    def Event(self):
        return object()

    def Queue(self, maxsize: int = 0):
        return queue.Queue(maxsize=maxsize)


class _DummyLiveAcquisitionWorker:
    instances: list["_DummyLiveAcquisitionWorker"] = []

    def __init__(
        self,
        request,
        result_queue,
        processing_queue,
        recording_queue,
        stop_event,
        *,
        source_mode: str,
        simulation_parameters=None,
        debug_mode_enabled: bool = False,
        log_queue=None,
    ) -> None:
        self.request = request
        self.result_queue = result_queue
        self.processing_queue = processing_queue
        self.recording_queue = recording_queue
        self.stop_event = stop_event
        self.source_mode = source_mode
        self.simulation_parameters = simulation_parameters
        self.debug_mode_enabled = debug_mode_enabled
        self.log_queue = log_queue
        self.cycle_periods: list[float] = []
        self.started = False
        self._alive = True
        type(self).instances.append(self)

    def update_cycle_period(self, cycle_period_s: float) -> None:
        self.cycle_periods.append(float(cycle_period_s))

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self._alive


class _DummyLiveProcessingWorker:
    def __init__(self, *args, **kwargs) -> None:
        self.started = False
        self._alive = True

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self._alive


class AcquisitionControllerBackendHandoffTests(unittest.TestCase):
    def test_live_source_rate_uses_producer_sequence_not_delivery_gap(self) -> None:
        window = SimpleNamespace(
            _raw_last_finish_ts=0.0,
            _raw_last_sample_index=1,
            _last_spacing_ms=None,
            _effective_raw_rate_hz=None,
        )
        event = LiveAcquisitionEvent(
            result=AcquisitionResult(
                spectrum=None,  # type: ignore[arg-type]
                elapsed_ms=0.0,
                settings=AcquisitionSettings(),
                source_epoch=1,
            ),
            source_epoch=1,
            source_sample_index=3,
            produced_at_perf=0.4,
        )

        _update_live_source_rate_from_event(window, event, 0.4)

        self.assertAlmostEqual(window._last_spacing_ms, 200.0)
        self.assertAlmostEqual(window._effective_raw_rate_hz, 5.0)
        self.assertEqual(window._raw_last_sample_index, 3)

    def test_spectrometer_backend_is_suspended_and_restored_for_live_mode(self) -> None:
        original = _FakeSpectrometer()
        restored = _FakeSpectrometer()
        logs: list[tuple[str, str]] = []
        window = SimpleNamespace(
            _source_mode="spectrometer",
            _hardware_backend_suspended_for_live=False,
            _spectrometer=original,
            _launch_profile_spec=SimpleNamespace(force_simulator=False),
            _hardware_available=True,
            _capabilities=SimpleNamespace(marker="original"),
            _log_info=lambda message: logs.append(("info", str(message))),
            _log_warning=lambda message: logs.append(("warn", str(message))),
        )

        _suspend_spectrometer_backend_for_live(window)
        self.assertTrue(original.closed)
        self.assertTrue(window._hardware_backend_suspended_for_live)

        with patch("lspr_app.gui.acquisition_controller.create_spectrometer", return_value=restored):
            _restore_spectrometer_backend_after_live(window)

        self.assertIs(window._spectrometer, restored)
        self.assertEqual(window._capabilities.marker, "capabilities")
        self.assertTrue(window._hardware_available)
        self.assertFalse(window._hardware_backend_suspended_for_live)
        self.assertTrue(any("released for live acquisition" in message for _level, message in logs))
        self.assertTrue(any("restored after live acquisition" in message for _level, message in logs))

    def test_start_live_acquisition_leaves_spectrometer_worker_unthrottled(self) -> None:
        from lspr_app.gui.acquisition_controller import start_live_acquisition
        from lspr_app.gui.main_window_runtime_state import UiRefreshState

        _DummyLiveAcquisitionWorker.instances.clear()
        original_context_getter = _DummyContext()
        window = SimpleNamespace(
            _busy=False,
            _live_active=False,
            _live_worker=None,
            _live_processing_worker=None,
            _source_mode="spectrometer",
            _spectrometer=_FakeSpectrometer(),
            _hardware_backend_suspended_for_live=False,
            _launch_profile_spec=SimpleNamespace(force_simulator=False),
            _capabilities=SimpleNamespace(marker="capabilities"),
            _source_epoch=3,
            _measurement_active=False,
            _measurement_writer=None,
            _measurement_started_at=None,
            _processing_debug_mode_enabled=False,
            live_rate_spin=SimpleNamespace(value=lambda: 8.0),
            sim_output_rate_spin=SimpleNamespace(value=lambda: 4.0),
            _peak_history=[],
            _peak_history_buffers=[],
            _ui_refresh_state=UiRefreshState(),
            _reset_live_accumulator=lambda: None,
            _current_settings=lambda: AcquisitionSettings(),
            _current_processing_settings=lambda: SimpleNamespace(),
            _set_measurement_buttons_enabled=lambda _enabled: None,
            _set_manual_acquisition_buttons_enabled=lambda _enabled: None,
            _request_deferred_ui_refresh=lambda **_kwargs: None,
            _update_window_mode_label=lambda: None,
            _log_info=lambda _message: None,
            _log_warning=lambda _message: None,
            _log_debug=lambda _message: None,
            _log_throttled=lambda *args, **kwargs: None,
            _live_recording_timer=SimpleNamespace(start=lambda: None, stop=lambda: None),
        )

        with (
            patch("lspr_app.gui.acquisition_controller.mp.get_context", return_value=original_context_getter),
            patch("lspr_app.gui.acquisition_controller.LiveAcquisitionWorker", _DummyLiveAcquisitionWorker),
            patch("lspr_app.gui.acquisition_controller.LiveProcessingWorker", _DummyLiveProcessingWorker),
        ):
            start_live_acquisition(window)

        self.assertTrue(_DummyLiveAcquisitionWorker.instances)
        self.assertEqual(_DummyLiveAcquisitionWorker.instances[0].cycle_periods, [])
        self.assertTrue(_DummyLiveAcquisitionWorker.instances[0].started)
        self.assertTrue(window._spectrometer.closed)


if __name__ == "__main__":
    unittest.main()
