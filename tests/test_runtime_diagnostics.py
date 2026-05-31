from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from lspr_app.gui.runtime_diagnostics import SessionDiagnosticsSnapshot, build_session_statistics_lines


class _FakeSpin:
    def __init__(self, value: float) -> None:
        self._value = float(value)

    def value(self) -> float:
        return self._value


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_snapshot_lines_include_summary(self) -> None:
        window = SimpleNamespace(
            _quiet_diagnostics_mode=True,
            _suppress_diagnostic_info_logs=False,
            live_rate_spin=_FakeSpin(4.0),
            sim_output_rate_spin=_FakeSpin(4.0),
            _actual_plot_refresh_rate_hz=3.8,
            _plot_refresh_rate_window_s=5.0,
            _live_skip_rate_hz=lambda: 0.5,
            _measurement_active=False,
            _last_ui_heartbeat_delay_ms=1.0,
            _last_ui_heartbeat_total_ms=2.0,
            _ui_heartbeat_max_delay_ms=3.0,
            _last_ui_state_delay_ms=4.0,
            _last_ui_state_save_ms=5.0,
            _last_ui_state_total_ms=6.0,
            _last_acquisition_state_delay_ms=7.0,
            _last_acquisition_state_save_ms=8.0,
            _last_acquisition_state_total_ms=9.0,
            _last_session_stats_recording_delay_ms=10.0,
            _last_session_stats_recording_snapshot_ms=11.0,
            _last_session_stats_recording_total_ms=12.0,
            _last_plot_refresh_total_ms=13.0,
            _last_deferred_ui_refresh_total_ms=14.0,
            _last_deferred_ui_live_estimate_ms=15.0,
            _last_deferred_ui_telemetry_ms=16.0,
            _last_deferred_ui_trace_plot_ms=17.0,
            _last_deferred_ui_summary_ms=18.0,
            _last_deferred_ui_stats_ms=19.0,
            _last_session_summary_refresh_total_ms=20.0,
            _last_session_stats_refresh_total_ms=21.0,
            _ui_task_scheduler=SimpleNamespace(
                _last_dispatch_lag_ms=22.0,
                _last_dispatch_duration_ms=23.0,
                _last_dispatch_task_count=24,
                pending_count=lambda: 25,
            ),
            _last_log_buffer_total_ms=26.0,
            _last_gui_housekeeping_total_ms=27.0,
            _last_processing_ms=28.0,
            _last_processing_queue_wait_ms=29.0,
            _processing_headroom_ratio=1.5,
            _last_elapsed_ms=30.0,
            _last_overhead_ms=31.0,
            _last_spacing_ms=32.0,
            _effective_raw_rate_hz=3.5,
            _live_display_dropped_frames=33,
            _peak_history={"a": [1, 2, 3]},
            _peak_history_buffers={"a": [1, 2]},
            _sensorgram_heatmap_history=[1, 2, 3, 4],
            _live_result_queue=SimpleNamespace(qsize=lambda: 2),
            _live_result_queue_max_depth=5,
            _live_processed_queue=SimpleNamespace(qsize=lambda: 6),
            _live_processed_queue_max_depth=7,
            _last_spectrum_curve_update_ms=34.0,
            _last_spectrum_fit_update_ms=35.0,
            _last_spectrum_marker_update_ms=36.0,
            _last_spectrum_residual_update_ms=37.0,
            _measurement_started_at=None,
        )
        snapshot = SessionDiagnosticsSnapshot.from_window(window)
        lines = build_session_statistics_lines(snapshot)
        self.assertIn("Diagnostics mode: quiet", "\n".join(lines))
        self.assertIn("File info filter: on", "\n".join(lines))
        self.assertIn("Scheduler dispatch lag: 22.0 ms", "\n".join(lines))
        self.assertIn("Live processed queue: 6 | max: 7", "\n".join(lines))
