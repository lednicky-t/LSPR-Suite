"""Regression coverage for two related bugs, both stemming from the same root
cause: while live acquisition was running, the raw-sample live-processing
worker's result was rendered/tracked unconditionally on every frame - that
worker only ever transforms the raw sample, with no idea dark/reference/
absorbance plot modes even exist.

Bug 1 (fixed first): switching the spectrum plot dropdown to Dark/Reference/
Absorbance briefly showed the right thing (whatever enqueue_plot_processing_for
had just rendered) and then immediately reverted to looking like Raw, even
though the dropdown still correctly said otherwise. Fixed by branching on the
current dropdown selection: "sample" keeps the existing fast path (render the
worker's result directly); "absorbance" routes through the normal thread-pool
processing path instead (which reads the freshly-recomputed
session.state.absorbance); "dark"/"reference" are left alone entirely, since
neither changes just because a new live sample frame arrived.

Bug 2 (found later, via the "Start Tracking" sensorgram button): the
trace-history append (append_processed_trace_history) and
spectrum_stats_label update were only ever wired to the raw fast path above,
so while viewing Absorbance the sensorgram/tracking button silently tracked
raw-sample metrics instead, and spectrum_stats_label stayed frozen on
whatever it last showed in Sample mode. Fixed by moving the raw path's
trace-history append inside its own "sample" branch (not unconditional at the
end), and adding the equivalent stats-update + trace-append calls to
handle_plot_processing_result_for - the completion handler for the
Dark/Reference/Absorbance thread-pool path - gated so only Absorbance (not
the static Dark/Reference snapshots) feeds the trace.
"""
from __future__ import annotations

import queue
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tests._paths import ensure_repo_paths


ensure_repo_paths()

import sys
from pathlib import Path

APP_SRC = Path(__file__).resolve().parents[2] / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.models import Spectrum
from lspr_app.gui.acquisition_controller import flush_live_processed_results
from lspr_app.gui.main_window_plotting import handle_plot_processing_result_for
from lspr_app.gui.workers import LiveProcessedEvent, ProcessingResult


def _make_spectrum(value: float) -> Spectrum:
    return Spectrum(
        wavelengths_nm=[500.0, 600.0, 700.0],
        values=[value, value * 2, value],
        y_label="Intensity (counts)",
        acquired_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class _FakeSession:
    def __init__(self) -> None:
        self.samples_set: list[object] = []

    def set_sample(self, spectrum) -> None:
        self.samples_set.append(spectrum)


class _FakeSpin:
    def value(self) -> float:
        return 4.0


class _FakeRefreshState:
    def __init__(self) -> None:
        self.live_estimate_dirty = False
        self.telemetry_dirty = False


def _make_window(plot_mode_label: str):
    window = type("_FakeWindow", (), {})()
    window._live_processed_requested_at = None
    window._live_processed_queue = queue.Queue()
    window._live_processed_queue_max_depth = 0
    window._source_epoch = 1
    window._source_mode = "spectrometer"
    window._display_window_ms = 100.0
    window._session = _FakeSession()
    window.live_rate_spin = _FakeSpin()
    window.plot_selector = type("_FakeSelector", (), {"currentText": lambda self: plot_mode_label})()
    window.PLOT_MODES = {"Raw": "sample", "Absorbance": "absorbance", "Reference": "reference", "Dark": "dark"}
    window._ui_refresh_state = _FakeRefreshState()
    window._live_processing_worker = None
    window._live_active = False

    window.refresh_calls: list[str] = []
    window._refresh_spectrum_plot = lambda *_a, **_k: window.refresh_calls.append("refresh_spectrum_plot")
    window._autoscale_spectrum_plot = lambda: window.refresh_calls.append("autoscale_spectrum_plot")
    window._update_spectrum_stats = lambda *_a, **_k: window.refresh_calls.append("update_spectrum_stats")
    window._enqueue_plot_processing = lambda: window.refresh_calls.append("enqueue_plot_processing")
    window._update_poly_warning_indicator = lambda *_a, **_k: window.refresh_calls.append("update_poly_warning_indicator")
    window._append_processed_trace_history = lambda *_a, **_k: window.refresh_calls.append("append_processed_trace_history")
    window._log_throttled = lambda *_a, **_k: None
    return window


def _push_event(window, spectrum: Spectrum) -> None:
    window._live_processed_queue.put_nowait(
        LiveProcessedEvent(
            result=ProcessingResult(processed=spectrum, fit=None, epoch=1, processing_ms=1.0, queue_wait_ms=0.0),
            source_epoch=1,
        )
    )


class LivePlotModeRoutingTests(unittest.TestCase):
    def test_sample_mode_renders_the_live_workers_result_directly(self) -> None:
        window = _make_window("Raw")
        spectrum = _make_spectrum(100.0)
        _push_event(window, spectrum)

        flush_live_processed_results(window)

        self.assertIn("refresh_spectrum_plot", window.refresh_calls)
        self.assertIn("autoscale_spectrum_plot", window.refresh_calls)
        self.assertIn("update_spectrum_stats", window.refresh_calls)
        self.assertNotIn("enqueue_plot_processing", window.refresh_calls)
        self.assertIs(window._last_processed_plot, spectrum)
        # Sample mode is the raw fast path - it tracks its own metrics directly.
        self.assertIn("append_processed_trace_history", window.refresh_calls)

    def test_absorbance_mode_routes_through_the_processing_queue_instead(self) -> None:
        window = _make_window("Absorbance")
        spectrum = _make_spectrum(100.0)
        _push_event(window, spectrum)

        flush_live_processed_results(window)

        self.assertNotIn("refresh_spectrum_plot", window.refresh_calls)
        self.assertNotIn("autoscale_spectrum_plot", window.refresh_calls)
        self.assertIn("enqueue_plot_processing", window.refresh_calls)
        # The raw fast path must NOT track/stat-update on Absorbance's behalf -
        # handle_plot_processing_result_for (the enqueue_plot_processing
        # completion handler) owns that instead, once it has the actual
        # absorbance-derived processed/fit rather than the raw sample.
        self.assertNotIn("append_processed_trace_history", window.refresh_calls)
        self.assertNotIn("update_spectrum_stats", window.refresh_calls)
        # session.set_sample() must still run so absorbance has fresh data to
        # recompute from - only the *display* routing changes.
        self.assertEqual(len(window._session.samples_set), 1)

    def test_dark_mode_does_not_touch_the_spectrum_plot_at_all(self) -> None:
        window = _make_window("Dark")
        spectrum = _make_spectrum(100.0)
        _push_event(window, spectrum)

        flush_live_processed_results(window)

        self.assertNotIn("refresh_spectrum_plot", window.refresh_calls)
        self.assertNotIn("enqueue_plot_processing", window.refresh_calls)
        # Dark is a static snapshot - never fed into the trace/tracking button.
        self.assertNotIn("append_processed_trace_history", window.refresh_calls)

    def test_reference_mode_does_not_touch_the_spectrum_plot_at_all(self) -> None:
        window = _make_window("Reference")
        spectrum = _make_spectrum(100.0)
        _push_event(window, spectrum)

        flush_live_processed_results(window)

        self.assertNotIn("refresh_spectrum_plot", window.refresh_calls)
        self.assertNotIn("enqueue_plot_processing", window.refresh_calls)
        self.assertNotIn("append_processed_trace_history", window.refresh_calls)


def _make_processing_completion_window(plot_mode_label: str):
    window = type("_FakeWindow", (), {})()
    window._closing = False
    window._plot_processing_running = True
    window._active_plot_processing_epoch = 1
    window._pending_plot_request = None
    window._temporal_processed_history = []
    window._temporal_history_key = None
    window._source_mode = "spectrometer"
    window._current_processing_settings = lambda: type(
        "_FakeSettings", (), {"temporal_smoothing": 1, "crop_method": "fixed_width", "crop_fraction": 0.7,
                               "fit_method": "poly", "fit_window_width_nm": 40.0}
    )()
    window.plot_selector = type("_FakeSelector", (), {"currentText": lambda self: plot_mode_label})()
    window.PLOT_MODES = {"Raw": "sample", "Absorbance": "absorbance", "Reference": "reference", "Dark": "dark"}
    window.live_rate_spin = _FakeSpin()

    window.refresh_calls: list[str] = []
    window._autoscale_spectrum_plot = lambda: window.refresh_calls.append("autoscale_spectrum_plot")
    window._update_spectrum_stats = lambda *_a, **_k: window.refresh_calls.append("update_spectrum_stats")
    window._update_poly_warning_indicator = lambda *_a, **_k: window.refresh_calls.append("update_poly_warning_indicator")
    window._append_processed_trace_history = lambda *_a, **_k: window.refresh_calls.append("append_processed_trace_history")
    window._request_deferred_ui_refresh = lambda **_k: window.refresh_calls.append("request_deferred_ui_refresh")
    window._log_error = lambda *_a, **_k: None
    return window


class PlotProcessingCompletionRoutingTests(unittest.TestCase):
    """handle_plot_processing_result_for is the Dark/Reference/Absorbance
    counterpart to flush_live_processed_results' raw "sample" fast path -
    it must update spectrum_stats_label for whatever mode it's rendering, and
    feed the trace/tracking button only for Absorbance (not the static
    Dark/Reference snapshots, matching the raw path's own exclusion)."""

    def _make_result(self, spectrum: Spectrum) -> ProcessingResult:
        return ProcessingResult(processed=spectrum, fit=None, epoch=1, processing_ms=1.0, queue_wait_ms=0.0)

    @patch("lspr_app.gui.main_window_plotting.refresh_spectrum_plot_for")
    def test_absorbance_completion_updates_stats_and_trace(self, _mock_refresh) -> None:
        window = _make_processing_completion_window("Absorbance")
        result = self._make_result(_make_spectrum(50.0))

        handle_plot_processing_result_for(window, result)

        self.assertIn("update_spectrum_stats", window.refresh_calls)
        self.assertIn("append_processed_trace_history", window.refresh_calls)

    @patch("lspr_app.gui.main_window_plotting.refresh_spectrum_plot_for")
    def test_dark_completion_updates_stats_but_not_trace(self, _mock_refresh) -> None:
        window = _make_processing_completion_window("Dark")
        result = self._make_result(_make_spectrum(50.0))

        handle_plot_processing_result_for(window, result)

        self.assertIn("update_spectrum_stats", window.refresh_calls)
        self.assertNotIn("append_processed_trace_history", window.refresh_calls)

    @patch("lspr_app.gui.main_window_plotting.refresh_spectrum_plot_for")
    def test_reference_completion_updates_stats_but_not_trace(self, _mock_refresh) -> None:
        window = _make_processing_completion_window("Reference")
        result = self._make_result(_make_spectrum(50.0))

        handle_plot_processing_result_for(window, result)

        self.assertIn("update_spectrum_stats", window.refresh_calls)
        self.assertNotIn("append_processed_trace_history", window.refresh_calls)


if __name__ == "__main__":
    unittest.main()
