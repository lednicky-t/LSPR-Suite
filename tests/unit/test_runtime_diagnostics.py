from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from lspr_app.gui.runtime_diagnostics import (
    SessionDiagnosticsSnapshot,
    build_session_statistics_lines,
    format_ms_auto_unit,
    format_rate_for_window,
)


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
            _last_deferred_display_refresh_ms=14.0,
            _last_deferred_stats_refresh_ms=14.5,
            _last_deferred_ui_live_estimate_ms=15.0,
            _last_deferred_ui_telemetry_ms=16.0,
            _last_deferred_ui_trace_plot_ms=17.0,
            _last_deferred_ui_summary_ms=18.0,
            _last_deferred_ui_stats_ms=19.0,
            _last_session_summary_refresh_total_ms=20.0,
            _last_session_stats_refresh_total_ms=21.0,
            _gui_housekeeping_enabled=False,
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
        self.assertIn("Diagnostics: Diagnostics profile: normal | gui_log=off", "\n".join(lines))
        self.assertIn("File log info: on | min=INFO", "\n".join(lines))
        self.assertIn("GUI housekeeping switch: disabled", "\n".join(lines))
        self.assertIn("Deferred display total: 14.0 ms", "\n".join(lines))
        self.assertIn("Deferred stats total: 14.5 ms", "\n".join(lines))
        self.assertIn("Scheduler dispatch lag: 22.0 ms", "\n".join(lines))
        self.assertIn("Live processed queue: 6 | max: 7", "\n".join(lines))
        self.assertIn("Runtime drift probe", "\n".join(lines))


class FormatMsAutoUnitTests(unittest.TestCase):
    """format_ms_auto_unit switches a large millisecond value to seconds,
    using hysteresis (two thresholds, not one) so a value oscillating near
    the boundary doesn't flip units on every update - see the maintainer's
    explicit "don't want it to flit around the mark" request."""

    def _make_window(self) -> SimpleNamespace:
        return SimpleNamespace()

    def test_small_value_stays_in_milliseconds(self) -> None:
        window = self._make_window()
        self.assertEqual(format_ms_auto_unit(window, "x", 250.0), "250.0 ms")

    def test_value_above_high_threshold_switches_to_seconds(self) -> None:
        window = self._make_window()
        self.assertEqual(format_ms_auto_unit(window, "x", 11000.0), "11.00 s")

    def test_hysteresis_keeps_seconds_while_in_the_dead_zone(self) -> None:
        window = self._make_window()
        format_ms_auto_unit(window, "x", 1800.0)  # cross into seconds
        # Drops back below the high threshold but stays above the low one -
        # must NOT flip back to milliseconds yet.
        result = format_ms_auto_unit(window, "x", 1200.0)
        self.assertEqual(result, "1.20 s")

    def test_hysteresis_returns_to_milliseconds_below_the_low_threshold(self) -> None:
        window = self._make_window()
        format_ms_auto_unit(window, "x", 1800.0)
        format_ms_auto_unit(window, "x", 1200.0)
        result = format_ms_auto_unit(window, "x", 900.0)
        self.assertEqual(result, "900.0 ms")

    def test_does_not_flip_back_and_forth_near_the_boundary(self) -> None:
        # The exact scenario hysteresis exists to prevent: with a single
        # naive cutoff, a value bouncing just above and below 1500 ms would
        # flip units on every single update. Here it correctly makes one
        # clean ms->s transition (on 1510) and then holds steady through the
        # rest of the oscillation, since none of the later values drop below
        # the *low* threshold (1000 ms) needed to switch back.
        window = self._make_window()
        transitions = 0
        previous_unit = None
        for value in [1490.0, 1510.0, 1495.0, 1505.0, 1499.0, 1501.0]:
            text = format_ms_auto_unit(window, "x", value)
            unit = "s" if text.endswith(" s") else "ms"
            if previous_unit is not None and unit != previous_unit:
                transitions += 1
            previous_unit = unit
        self.assertLessEqual(transitions, 1)

    def test_independent_keys_do_not_share_state(self) -> None:
        window = self._make_window()
        format_ms_auto_unit(window, "skip", 2000.0)
        text = format_ms_auto_unit(window, "ovh", 1200.0)
        self.assertEqual(text, "1200.0 ms")

    def test_none_and_non_finite_values(self) -> None:
        window = self._make_window()
        self.assertEqual(format_ms_auto_unit(window, "x", None), "-")
        self.assertEqual(format_ms_auto_unit(window, "x", float("nan")), "-")


class FormatRateForWindowAutoSecondsTests(unittest.TestCase):
    def _make_window(self, *, unit: str) -> SimpleNamespace:
        return SimpleNamespace(_timing_display_unit=unit)

    def test_auto_seconds_key_opt_in_only(self) -> None:
        # A very low rate in "ms" mode produces a huge period - without
        # opting in, callers (logs, diagnostics) keep getting the exact
        # figure unchanged, preserving every existing caller's behavior.
        window = self._make_window(unit="ms")
        text = format_rate_for_window(window, 0.09, decimals=1)
        self.assertEqual(text, "11111.1 ms")

    def test_auto_seconds_key_converts_a_large_period(self) -> None:
        window = self._make_window(unit="ms")
        text = format_rate_for_window(window, 0.09, decimals=1, auto_seconds_key="skip")
        self.assertTrue(text.endswith(" s"), text)

    def test_hz_mode_is_unaffected_by_auto_seconds_key(self) -> None:
        window = self._make_window(unit="hz")
        text = format_rate_for_window(window, 0.09, decimals=1, auto_seconds_key="skip")
        self.assertTrue(text.endswith(" Hz"))
