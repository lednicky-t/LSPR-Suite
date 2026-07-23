"""stop_measurement_run() must rebase the sensorgram live cache's tail
before switching back to session view, so the session-relative reload it
triggers doesn't merge in measurement-relative points on the wrong time
scale (see tests/unit/test_plot_view_cache.py for the underlying
rebase_live_absolute_metric_recent_tail() coverage, and
docs/sensorgram_improvements.md for the full bug writeup).
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.acquisition_controller import stop_measurement_run


class _FakeSensorgramDisplay:
    def __init__(self) -> None:
        self.end_measurement_calls = 0

    def end_measurement(self) -> None:
        self.end_measurement_calls += 1


class _FakePlotViewCache:
    def __init__(self) -> None:
        self.rebase_calls: list[float] = []

    def rebase_live_absolute_metric_recent_tail(self, offset_s: float) -> None:
        self.rebase_calls.append(offset_s)


class _FakeStatusLabel:
    def setText(self, _text: str) -> None:
        return None


def _make_window(**overrides) -> SimpleNamespace:
    close_segment_calls: list[int] = []
    window = SimpleNamespace(
        _measurement_active=True,
        _measurement_path=None,
        _live_worker=None,
        _measurement_writer=None,
        _measurement_paused=True,
        _measurement_started_at=None,
        _measurement_axis_lock=None,
        _plot_view_cache=_FakePlotViewCache(),
        _live_trace_started_at=None,
        _metric_reference_processed=object(),
        _sensorgram_display=_FakeSensorgramDisplay(),
        _last_metric_autoscale_range=None,
        _sensorgram_control_step_events=[{"state": "RUN"}],
        _close_sensorgram_control_step_overlay_segment=lambda: close_segment_calls.append(1),
        _close_segment_calls=close_segment_calls,
        _refresh_session_statistics=lambda force=False: None,
        _refresh_trace_plot=lambda _label: None,
        _request_trace_autoscale=lambda: None,
        status_label=_FakeStatusLabel(),
        _log_success=lambda _msg: None,
        _refresh_plot=lambda: None,
        _refresh_session_summary=lambda force=False: None,
        _update_window_mode_label=lambda: None,
    )
    for key, value in overrides.items():
        setattr(window, key, value)
    return window


class StopMeasurementRunSensorgramRebaseTests(unittest.TestCase):
    def setUp(self) -> None:
        # These helpers touch a much larger swath of the window (file
        # writers, UI buttons, the simulated-backend sync, ...) unrelated to
        # the rebase fix under test - stub them so this test only depends on
        # the window surface stop_measurement_run's own body actually reads.
        patchers = [
            patch("lspr_app.gui.acquisition_controller.flush_live_recording_results"),
            patch("lspr_app.gui.acquisition_controller.flush_measurement_frames"),
            patch("lspr_app.gui.acquisition_controller.set_measurement_ui_locked"),
            patch("lspr_app.gui.acquisition_controller.sync_simulation_backend_from_controls_for"),
            patch("lspr_app.gui.acquisition_controller.set_measurement_buttons_enabled"),
            patch("lspr_app.gui.main_window_sensorgram_archive.request_absolute_sensorgram_metric_archive_reload"),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_rebases_tail_by_the_measurement_to_session_offset(self) -> None:
        session_started_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        measurement_started_at = session_started_at + timedelta(seconds=100.0)
        window = _make_window(
            _measurement_started_at=measurement_started_at,
            _metric_archive_started_at=session_started_at,
        )

        stop_measurement_run(window)

        self.assertEqual(window._plot_view_cache.rebase_calls, [100.0])
        self.assertEqual(window._sensorgram_display.end_measurement_calls, 1)

    def test_no_rebase_when_session_anchor_is_not_set_yet(self) -> None:
        # Edge case: a measurement stopped before any spectrum ever got
        # processed, so ensure_session_writer() never ran and
        # _metric_archive_started_at is still None - nothing to rebase
        # against, and must not raise.
        window = _make_window(
            _measurement_started_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            _metric_archive_started_at=None,
        )

        stop_measurement_run(window)

        self.assertEqual(window._plot_view_cache.rebase_calls, [])

    def test_no_rebase_when_measurement_started_at_is_already_none(self) -> None:
        window = _make_window(
            _measurement_started_at=None,
            _metric_archive_started_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        )

        stop_measurement_run(window)

        self.assertEqual(window._plot_view_cache.rebase_calls, [])

    def test_measurement_started_before_session_anchor_gives_negative_offset(self) -> None:
        # Not expected in practice (session always starts at or before the
        # measurement), but the arithmetic should still just work rather
        # than assume a sign.
        session_started_at = datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc)
        measurement_started_at = session_started_at - timedelta(seconds=10.0)
        window = _make_window(
            _measurement_started_at=measurement_started_at,
            _metric_archive_started_at=session_started_at,
        )

        stop_measurement_run(window)

        self.assertEqual(window._plot_view_cache.rebase_calls, [-10.0])

    def test_missing_rebase_method_on_cache_does_not_raise(self) -> None:
        window = _make_window(
            _measurement_started_at=datetime(2026, 1, 1, 12, 0, 10, tzinfo=timezone.utc),
            _metric_archive_started_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            _plot_view_cache=object(),  # no rebase_live_absolute_metric_recent_tail attribute
        )

        stop_measurement_run(window)  # must not raise

    def test_measurement_started_at_is_cleared_after_stop(self) -> None:
        window = _make_window(
            _measurement_started_at=datetime(2026, 1, 1, 12, 0, 10, tzinfo=timezone.utc),
            _metric_archive_started_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        )

        stop_measurement_run(window)

        self.assertIsNone(window._measurement_started_at)
        self.assertFalse(window._measurement_active)

    def test_sensorgram_control_step_events_are_not_cleared(self) -> None:
        # Session view must be able to show step markers from every past
        # measurement in the session, not just the most recent one - so
        # stopping a measurement must not wipe the accumulated events list.
        window = _make_window()
        events_before = window._sensorgram_control_step_events

        stop_measurement_run(window)

        self.assertIs(window._sensorgram_control_step_events, events_before)
        self.assertEqual(window._sensorgram_control_step_events, [{"state": "RUN"}])

    def test_closes_the_overlay_segment_before_clearing_measurement_started_at(self) -> None:
        window = _make_window()

        stop_measurement_run(window)

        self.assertEqual(window._close_segment_calls, [1])

    def test_missing_close_segment_method_does_not_raise(self) -> None:
        window = _make_window()
        del window._close_sensorgram_control_step_overlay_segment

        stop_measurement_run(window)  # must not raise


if __name__ == "__main__":
    unittest.main()
