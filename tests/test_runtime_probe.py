from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from lspr_app.gui.runtime_probe import RuntimeDriftSample, build_runtime_drift_lines_for


class RuntimeProbeTests(unittest.TestCase):
    def test_build_runtime_drift_lines_show_trend_summary(self) -> None:
        sample_a = RuntimeDriftSample(
            captured_at_s=0.0,
            scheduler_lag_ms=1.0,
            scheduler_duration_ms=2.0,
            scheduler_pending=1,
            gui_housekeeping_total_ms=3.0,
            log_buffer_total_ms=4.0,
            deferred_ui_total_ms=5.0,
            plot_refresh_total_ms=6.0,
            live_result_queue_size=0,
            live_processed_queue_size=1,
            log_history_count=10,
            log_buffer_count=2,
            session_stats_log_count=3,
            peak_history_points=4,
            peak_history_buffer_points=5,
            temporal_history_count=6,
            sensorgram_rows=7,
            widget_count=8,
            working_set_mb=123.4,
        )
        sample_b = RuntimeDriftSample(
            captured_at_s=120.0,
            scheduler_lag_ms=11.0,
            scheduler_duration_ms=12.0,
            scheduler_pending=4,
            gui_housekeeping_total_ms=13.0,
            log_buffer_total_ms=14.0,
            deferred_ui_total_ms=15.0,
            plot_refresh_total_ms=16.0,
            live_result_queue_size=2,
            live_processed_queue_size=3,
            log_history_count=20,
            log_buffer_count=6,
            session_stats_log_count=7,
            peak_history_points=14,
            peak_history_buffer_points=15,
            temporal_history_count=16,
            sensorgram_rows=17,
            widget_count=18,
            working_set_mb=223.4,
        )
        window = SimpleNamespace(
            _runtime_drift_samples=[sample_a, sample_b],
            _runtime_drift_probe_interval_ms=60_000,
        )

        lines = build_runtime_drift_lines_for(window)

        joined = "\n".join(lines)
        self.assertIn("Samples: 2 | interval: 60s", joined)
        self.assertIn("Scheduler lag: 1.0 ms -> 11.0 ms", joined)
        self.assertIn("GUI housekeeping total: 3.0 ms -> 13.0 ms", joined)
        self.assertIn("Log history entries: 10 -> 20", joined)
        self.assertIn("Working set: 123.4 MB -> 223.4 MB", joined)
        self.assertIn("Per-minute scheduler lag:", joined)
        self.assertIn("Per-minute working set:", joined)
        self.assertIn("Top growth contributors:", joined)
        self.assertIn("Working set: +50.00 MB/min", joined)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
