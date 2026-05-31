from __future__ import annotations

import logging
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

import lspr_app.gui.main_window_logging as logging_ui
from lspr_app.gui.main_window_logging import (
    build_pipeline_timing_breakdown_for,
    build_recording_experiment_log_path,
    _timing_share_text,
    _log_record_matches_view,
    build_session_statistics_text_for,
    clear_log_terminal,
    set_log_view_mode,
)
from lspr_app.gui.main_window_plotting import build_pipeline_telemetry_text_for


class _FakeTerminal:
    def __init__(self) -> None:
        self.cleared = 0

    def clear(self) -> None:
        self.cleared += 1


class _FakeTimer:
    def __init__(self) -> None:
        self.stopped = 0

    def stop(self) -> None:
        self.stopped += 1


class _FakeSpin:
    def __init__(self, value: float) -> None:
        self._value = float(value)

    def value(self) -> float:
        return self._value


class _FakeSizedBuffer:
    def __init__(self, size: int) -> None:
        self._size = int(size)

    def __len__(self) -> int:
        return self._size


class MainWindowLoggingTests(unittest.TestCase):
    def test_warning_and_error_logs_bypass_buffer(self) -> None:
        calls: list[tuple[int, str, str]] = []

        original = logging_ui.append_log_record_now

        def fake_append_log_record_now(window, levelno: int, source: str, text: str) -> None:
            calls.append((int(levelno), str(source), str(text)))

        logging_ui.append_log_record_now = fake_append_log_record_now
        try:
            window = SimpleNamespace(
                log_terminal=object(),
                _log_emit_levels={
                    logging.INFO,
                    logging.WARNING,
                    logging.ERROR,
                    logging.CRITICAL,
                },
                _log_buffering_enabled=True,
                _log_buffer=[],
                _log_buffer_timer=object(),
                _log_buffer_requested_at=None,
            )

            logging_ui.append_log_record(window, logging.INFO, "main", "informational")
            logging_ui.append_log_record(window, logging.WARNING, "main", "warning")
            logging_ui.append_log_record(window, logging.ERROR, "main", "error")
        finally:
            logging_ui.append_log_record_now = original

        self.assertEqual(window._log_buffer, [(logging.INFO, "main", "informational")])
        self.assertEqual(
            calls,
            [
                (logging.WARNING, "main", "warning"),
                (logging.ERROR, "main", "error"),
            ],
        )

    def test_log_record_matches_gui_and_devices_views(self) -> None:
        self.assertTrue(
            _log_record_matches_view(
                logging.INFO,
                "lspr_app.experiment_control",
                "Experiment control bootstrap +1.0 ms: UI built",
                "gui",
            )
        )
        self.assertFalse(
            _log_record_matches_view(
                logging.INFO,
                "lspr_app.experiment_control",
                "Pump controller connected on COM8.",
                "gui",
            )
        )
        self.assertTrue(
            _log_record_matches_view(
                logging.INFO,
                "main",
                "Pump controller connected on COM8.",
                "devices",
            )
        )

    def test_set_log_view_mode_updates_mode_without_refresh(self) -> None:
        window = SimpleNamespace(_log_view_mode="all")

        set_log_view_mode(window, "devices", refresh=False)

        self.assertEqual(window._log_view_mode, "devices")

    def test_clear_log_terminal_preserves_log_view_mode(self) -> None:
        window = SimpleNamespace(
            log_terminal=_FakeTerminal(),
            _log_history=[(logging.INFO, "main", "one")],
            _log_buffer=[(logging.INFO, "main", "two")],
            _log_buffer_timer=_FakeTimer(),
            _log_view_mode="devices",
        )

        clear_log_terminal(window)

        self.assertEqual(window.log_terminal.cleared, 1)
        self.assertEqual(window._log_history, [])
        self.assertEqual(window._log_buffer, [])
        self.assertEqual(window._log_buffer_timer.stopped, 1)
        self.assertEqual(window._log_view_mode, "devices")

    def test_save_session_stats_log_uses_experiment_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_destination = Path(tmpdir) / "project"
            window = SimpleNamespace(
                _session_stats_log=["first", "second"],
                _session_stats_recording_started_at=None,
                _measurement_started_at=None,
                _session_stats_recording_duration_s=12.4,
                recording_project_destination=lambda: str(project_destination),
                recording_experiment_name=lambda: "demo run",
            )

            destination = logging_ui.save_session_stats_log_for(window)

            self.assertIsNotNone(destination)
            assert destination is not None
            self.assertEqual(destination.parent, project_destination / "demo run")
            self.assertTrue(destination.name.startswith("session_stats_"))
            self.assertTrue(destination.name.endswith("_12s.txt"))
            self.assertEqual(destination.read_text(encoding="utf-8"), "first\n\nsecond")

    def test_build_recording_experiment_log_path_uses_experiment_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            destination = build_recording_experiment_log_path(
                str(Path(tmpdir) / "project"),
                "demo run",
                prefix="startup_log",
                suffix=".log",
                timestamp=logging_ui.datetime(2025, 5, 31, 12, 34, 56, tzinfo=logging_ui.timezone.utc),
            )

            self.assertEqual(destination.parent, Path(tmpdir) / "project" / "demo run")
            self.assertTrue(destination.name.startswith("startup_log_"))
            self.assertTrue(destination.name.endswith(".log"))

    def test_timing_share_text_formats_percentage_against_total(self) -> None:
        self.assertEqual(_timing_share_text(39.4, 1559.8), "39.4 ms (2.5%)")
        self.assertEqual(_timing_share_text(-0.6, 1559.8), "-0.6 ms (-0.0%)")
        self.assertEqual(_timing_share_text(39.4, None), "39.4 ms")

    def test_build_session_statistics_text_includes_timing_shares(self) -> None:
        window = SimpleNamespace(
            live_rate_spin=_FakeSpin(4.0),
            sim_output_rate_spin=_FakeSpin(1.33),
            _live_skip_rate_hz=lambda: 0.9,
            _last_ui_heartbeat_delay_ms=240.0,
            _ui_heartbeat_max_delay_ms=1038.2,
            _last_ui_state_delay_ms=12.3,
            _last_ui_state_save_ms=4.5,
            _last_acquisition_state_delay_ms=8.7,
            _last_acquisition_state_save_ms=5.6,
            _last_session_stats_recording_delay_ms=2.4,
            _last_session_stats_recording_snapshot_ms=3.3,
            _last_processing_ms=2.1,
            _processing_headroom_ratio=117.39,
            _last_live_result_timer_delay_ms=0.0,
            _last_live_acquisition_flush_ms=18.4,
            _last_live_processed_timer_delay_ms=0.0,
            _last_live_processed_flush_ms=14.6,
            _last_stats_refresh_delay_ms=0.0,
            _last_summary_refresh_ms=6.3,
            _last_session_stats_refresh_ms=4.4,
            _last_log_buffer_delay_ms=0.0,
            _last_log_buffer_flush_ms=3.2,
            _last_processing_queue_wait_ms=0.7,
            _last_elapsed_ms=39.4,
            _last_overhead_ms=-0.6,
            _last_spacing_ms=1559.8,
            _last_plot_refresh_delay_ms=0.0,
            _last_plot_refresh_ms=93.2,
            _last_sensorgram_render_ms=65.9,
            _last_sensorgram_heatmap_render_ms=1.8,
            _last_deferred_ui_refresh_ms=6.2,
            _last_deferred_ui_live_estimate_ms=0.4,
            _last_deferred_ui_telemetry_ms=1.1,
            _last_deferred_ui_trace_plot_ms=2.5,
            _last_deferred_ui_summary_ms=0.3,
            _last_deferred_ui_stats_ms=0.9,
            _last_gui_housekeeping_total_ms=22.4,
            _last_spectrum_curve_update_ms=18.5,
            _last_spectrum_fit_update_ms=11.2,
            _last_spectrum_marker_update_ms=7.1,
            _last_spectrum_residual_update_ms=3.9,
            _peak_history={"smoothed_max": _FakeSizedBuffer(2386)},
            _peak_history_buffers={"smoothed_max": _FakeSizedBuffer(17)},
            _sensorgram_heatmap_history=[(0.0, object()), (1.0, object())],
            _effective_raw_rate_hz=0.64,
            _live_display_dropped_frames=17,
            _live_result_queue=SimpleNamespace(qsize=lambda: 1),
            _live_processed_queue=SimpleNamespace(qsize=lambda: 2),
            _live_result_queue_max_depth=3,
            _live_processed_queue_max_depth=4,
            _measurement_active=False,
            _measurement_started_at=None,
        )

        text = build_session_statistics_text_for(window)

        self.assertIn("Acquisition latency: 39.4 ms", text)
        self.assertIn("Acquisition overhead: -0.6 ms", text)
        self.assertIn("Frame spacing: 1559.8 ms", text)
        self.assertIn("Simulation output rate: 1.33 Hz", text)
        self.assertIn("UI event loop heartbeat", text)
        self.assertIn("Current delay: 240.0 ms", text)
        self.assertIn("Max delay: 1038.2 ms", text)
        self.assertIn("Periodic callbacks", text)
        self.assertIn("UI state save delay: 12.3 ms", text)
        self.assertIn("UI state save time: 4.5 ms", text)
        self.assertIn("Acquisition state delay: 8.7 ms", text)
        self.assertIn("Acquisition state save time: 5.6 ms", text)
        self.assertIn("Session stats snapshot delay: 2.4 ms", text)
        self.assertIn("Session stats snapshot time: 3.3 ms", text)
        self.assertIn("Pipeline gap breakdown", text)
        self.assertIn("Live acquisition timer delay: 0.0 ms", text)
        self.assertIn("Live acquisition flush: 18.4 ms", text)
        self.assertIn("Live processing timer delay: 0.0 ms", text)
        self.assertIn("Live processing flush: 14.6 ms", text)
        self.assertIn("Stats refresh timer delay: 0.0 ms", text)
        self.assertIn("Session summary refresh: 6.3 ms", text)
        self.assertIn("Session stats refresh: 4.4 ms", text)
        self.assertIn("Log buffer timer delay: 0.0 ms", text)
        self.assertIn("Log buffer flush: 3.2 ms", text)
        self.assertIn("Processing queue wait: 0.7 ms", text)
        self.assertIn("Plot refresh timer delay: 0.0 ms", text)
        self.assertIn("Plot render: 93.2 ms", text)
        self.assertIn("Sensorgram render: 65.9 ms", text)
        self.assertIn("Sensorgram heatmap render: 1.8 ms", text)
        self.assertIn("Deferred UI flush: 6.2 ms", text)
        self.assertIn("Deferred UI live estimate: 0.4 ms", text)
        self.assertIn("Deferred UI telemetry: 1.1 ms", text)
        self.assertIn("Deferred UI metric plot: 2.5 ms", text)
        self.assertIn("Deferred UI summary: 0.3 ms", text)
        self.assertIn("Deferred UI stats: 0.9 ms", text)
        self.assertIn("GUI housekeeping total: 22.4 ms", text)
        self.assertIn("Metric history points: 2386", text)
        self.assertIn("Metric display buffer points: 17", text)
        self.assertIn("Heatmap rows: 2", text)
        self.assertIn("Live result queue: 1 | max: 3", text)
        self.assertIn("Live processed queue: 2 | max: 4", text)
        self.assertIn("Unattributed / idle:", text)
        self.assertIn("Spectrum redraw breakdown", text)
        self.assertIn("Curve update: 18.5 ms", text)
        self.assertIn("Fit update: 11.2 ms", text)
        self.assertIn("Marker update: 7.1 ms", text)
        self.assertIn("Residual update: 3.9 ms", text)
        self.assertIn("Dropped frames: 17", text)

    def test_build_pipeline_timing_breakdown_reports_idle_remainder(self) -> None:
        window = SimpleNamespace(
            _last_elapsed_ms=39.4,
            _last_ui_heartbeat_delay_ms=240.0,
            _ui_heartbeat_max_delay_ms=1038.2,
            _last_ui_state_delay_ms=12.3,
            _last_ui_state_save_ms=4.5,
            _last_acquisition_state_delay_ms=8.7,
            _last_acquisition_state_save_ms=5.6,
            _last_session_stats_recording_delay_ms=2.4,
            _last_session_stats_recording_snapshot_ms=3.3,
            _last_live_result_timer_delay_ms=0.0,
            _last_live_acquisition_flush_ms=18.4,
            _last_live_processed_timer_delay_ms=0.0,
            _last_live_processed_flush_ms=14.6,
            _last_stats_refresh_delay_ms=0.0,
            _last_summary_refresh_ms=6.3,
            _last_session_stats_refresh_ms=4.4,
            _last_log_buffer_delay_ms=0.0,
            _last_log_buffer_flush_ms=3.2,
            _last_processing_queue_wait_ms=0.7,
            _last_processing_ms=2.1,
            _last_plot_refresh_delay_ms=0.0,
            _last_plot_refresh_ms=93.2,
            _last_sensorgram_render_ms=65.9,
            _last_sensorgram_heatmap_render_ms=1.8,
            _last_deferred_ui_refresh_ms=6.2,
            _last_deferred_ui_live_estimate_ms=0.4,
            _last_deferred_ui_telemetry_ms=1.1,
            _last_deferred_ui_trace_plot_ms=2.5,
            _last_deferred_ui_summary_ms=0.3,
            _last_deferred_ui_stats_ms=0.9,
            _last_gui_housekeeping_total_ms=22.4,
            _last_spectrum_curve_update_ms=18.5,
            _last_spectrum_fit_update_ms=11.2,
            _last_spectrum_marker_update_ms=7.1,
            _last_spectrum_residual_update_ms=3.9,
            _peak_history={"smoothed_max": _FakeSizedBuffer(2386)},
            _peak_history_buffers={"smoothed_max": _FakeSizedBuffer(17)},
            _sensorgram_heatmap_history=[(0.0, object()), (1.0, object())],
            _live_display_dropped_frames=17,
            _live_result_queue=SimpleNamespace(qsize=lambda: 1),
            _live_processed_queue=SimpleNamespace(qsize=lambda: 2),
            _live_result_queue_max_depth=3,
            _live_processed_queue_max_depth=4,
            _last_spacing_ms=1559.8,
        )

        breakdown = build_pipeline_timing_breakdown_for(window)

        self.assertAlmostEqual(breakdown["reference_ms"], 1559.8)
        self.assertAlmostEqual(breakdown["idle_ms"], 1303.6, places=1)

    def test_build_pipeline_telemetry_text_uses_plain_times(self) -> None:
        window = SimpleNamespace(
            _last_elapsed_ms=39.4,
            _last_overhead_ms=-0.6,
            _last_spacing_ms=1559.8,
            _last_ui_heartbeat_delay_ms=240.0,
            _ui_heartbeat_max_delay_ms=1038.2,
            _last_ui_state_delay_ms=12.3,
            _last_ui_state_save_ms=4.5,
            _last_acquisition_state_delay_ms=8.7,
            _last_acquisition_state_save_ms=5.6,
            _last_session_stats_recording_delay_ms=2.4,
            _last_session_stats_recording_snapshot_ms=3.3,
            _last_live_result_timer_delay_ms=0.0,
            _last_live_acquisition_flush_ms=18.4,
            _last_live_processed_timer_delay_ms=0.0,
            _last_live_processed_flush_ms=14.6,
            _last_stats_refresh_delay_ms=0.0,
            _last_summary_refresh_ms=6.3,
            _last_session_stats_refresh_ms=4.4,
            _last_log_buffer_delay_ms=0.0,
            _last_log_buffer_flush_ms=3.2,
            _last_processing_queue_wait_ms=0.7,
            _last_processing_ms=2.1,
            _last_plot_refresh_delay_ms=0.0,
            _last_plot_refresh_ms=93.2,
            _last_sensorgram_render_ms=65.9,
            _last_sensorgram_heatmap_render_ms=1.8,
            _last_deferred_ui_refresh_ms=6.2,
            _last_deferred_ui_live_estimate_ms=0.4,
            _last_deferred_ui_telemetry_ms=1.1,
            _last_deferred_ui_trace_plot_ms=2.5,
            _last_deferred_ui_summary_ms=0.3,
            _last_deferred_ui_stats_ms=0.9,
            _last_gui_housekeeping_total_ms=22.4,
            _last_spectrum_curve_update_ms=18.5,
            _last_spectrum_fit_update_ms=11.2,
            _last_spectrum_marker_update_ms=7.1,
            _last_spectrum_residual_update_ms=3.9,
            _peak_history_buffers={"smoothed_max": _FakeSizedBuffer(17)},
            _sensorgram_heatmap_history=[(0.0, object()), (1.0, object())],
            _last_display_average_count=9,
            _last_display_period_ms=999.0,
            _live_result_queue=SimpleNamespace(qsize=lambda: 2),
            _live_processed_queue=SimpleNamespace(qsize=lambda: 1),
        )

        text = build_pipeline_telemetry_text_for(window)

        self.assertIn("gap 1559.8 ms", text)
        self.assertIn("acq 39.4 ms", text)
        self.assertIn("proc 2.8 ms", text)
        self.assertIn("plot 93.2 ms", text)
        self.assertIn("ui 6.2 ms", text)
        self.assertIn("idle 1303.6 ms", text)
        self.assertIn("srcq 2", text)
        self.assertIn("procq 1", text)
        self.assertIn("ovh -0.6 ms", text)
        self.assertNotIn("%", text)


if __name__ == "__main__":
    unittest.main()
