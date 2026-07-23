"""Sensorgram control-step overlay alignment across session/measurement
view switches.

The overlay's bars mark when each experiment-plan step ran, drawn on top
of the sensorgram at whatever elapsed-time x-position matches the step's
timing. Events are recorded once, during a measurement, with an absolute
timestamp; the elapsed-time value used to actually draw them must be
recomputed every time against whichever anchor is currently driving the
plot's own x-axis (measurement-relative while recording, session-relative
once back in session view) - baking in a fixed value at record time is
what made the overlay drift out of alignment with the plot on Stop. See
docs/sensorgram_improvements.md.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.main_window_sensorgram_overlay import (
    _rebase_sensorgram_control_step_events,
    _refresh_sensorgram_control_step_event_labels,
    _visible_sensorgram_control_step_events,
    close_sensorgram_control_step_overlay_segment,
    record_sensorgram_control_step_event,
    sensorgram_control_step_overlay_current_elapsed_s,
)
from lspr_app.gui.sensorgram_control_step_overlay import build_sensorgram_control_step_overlay_segments


def _make_window(**overrides) -> SimpleNamespace:
    window = SimpleNamespace(
        _measurement_started_at=None,
        _live_trace_started_at=None,
        _metric_archive_started_at=None,
        _measurement_active=False,
        _experiment_control_window=None,
        SENSORGRAM_TIME_PLOT_COLORS={},
    )
    for key, value in overrides.items():
        setattr(window, key, value)
    return window


class RecordSensorgramControlStepEventTests(unittest.TestCase):
    def test_stores_absolute_timestamp_when_present(self) -> None:
        window = _make_window()
        payload = {
            "t_ms": 5_000,
            "timestamp_utc_ms": 1_800_000_000_000.0,
            "step_index": 1,
            "plan_state": "RUN",
            "event": "plan_started",
            "color": "#1f77b4",
        }

        record_sensorgram_control_step_event(window, payload)

        event = window._sensorgram_control_step_events[0]
        self.assertEqual(event["timestamp_utc_ms"], 1_800_000_000_000.0)
        self.assertEqual(event["elapsed_s"], 5.0)  # fallback value from t_ms

    def test_missing_timestamp_leaves_the_field_out(self) -> None:
        window = _make_window()
        payload = {"t_ms": 1_000, "step_index": 1, "plan_state": "RUN", "event": "plan_started"}

        record_sensorgram_control_step_event(window, payload)

        event = window._sensorgram_control_step_events[0]
        self.assertNotIn("timestamp_utc_ms", event)


class RebaseSensorgramControlStepEventsTests(unittest.TestCase):
    def test_rebases_elapsed_s_onto_the_current_anchor(self) -> None:
        session_started_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        event_at = session_started_at + timedelta(seconds=130.0)
        window = _make_window(_metric_archive_started_at=session_started_at)
        events = [
            {
                "elapsed_s": 5.0,  # stale, measurement-relative value from record time
                "timestamp_utc_ms": event_at.timestamp() * 1000.0,
                "step_index": 1,
                "state": "RUN",
                "event": "plan_started",
                "color": "#1f77b4",
                "label": "Step 1",
            }
        ]

        rebased = _rebase_sensorgram_control_step_events(window, events)

        self.assertEqual(rebased[0]["elapsed_s"], 130.0)
        # Original event dict must be untouched (a new dict is returned).
        self.assertEqual(events[0]["elapsed_s"], 5.0)

    def test_reproduces_the_stop_transition_realignment(self) -> None:
        """The exact reported scenario: an event recorded 30s into a
        measurement that itself started 100s into the session must show up
        at 130s once the display anchor switches back to session start."""
        session_started_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        measurement_started_at = session_started_at + timedelta(seconds=100.0)
        event_at = measurement_started_at + timedelta(seconds=30.0)
        events = [
            {
                "elapsed_s": 30.0,
                "timestamp_utc_ms": event_at.timestamp() * 1000.0,
                "step_index": 2,
                "state": "RUN",
                "event": "step_jump",
                "color": "#ff7f0e",
                "label": "Step 2",
            }
        ]

        # While still recording: anchor is measurement start.
        during_window = _make_window(_measurement_active=True, _measurement_started_at=measurement_started_at)
        during = _rebase_sensorgram_control_step_events(during_window, events)
        self.assertEqual(during[0]["elapsed_s"], 30.0)

        # After Stop: anchor falls back to the stable session anchor.
        after_window = _make_window(_metric_archive_started_at=session_started_at)
        after = _rebase_sensorgram_control_step_events(after_window, events)
        self.assertEqual(after[0]["elapsed_s"], 130.0)

    def test_events_without_a_timestamp_pass_through_unchanged(self) -> None:
        window = _make_window(_metric_archive_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        events = [{"elapsed_s": 42.0, "step_index": 1, "state": "RUN"}]

        rebased = _rebase_sensorgram_control_step_events(window, events)

        self.assertEqual(rebased[0]["elapsed_s"], 42.0)
        self.assertIs(rebased[0], events[0])

    def test_no_anchor_available_returns_events_unchanged(self) -> None:
        window = _make_window()  # every anchor attribute is None
        events = [{"elapsed_s": 42.0, "timestamp_utc_ms": 1_800_000_000_000.0}]

        rebased = _rebase_sensorgram_control_step_events(window, events)

        self.assertIs(rebased, events)

    def test_elapsed_s_never_goes_negative(self) -> None:
        anchor = datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc)
        event_at = anchor - timedelta(seconds=5.0)  # event slightly before the anchor
        window = _make_window(_metric_archive_started_at=anchor)
        events = [{"elapsed_s": 0.0, "timestamp_utc_ms": event_at.timestamp() * 1000.0}]

        rebased = _rebase_sensorgram_control_step_events(window, events)

        self.assertEqual(rebased[0]["elapsed_s"], 0.0)


class CurrentElapsedSTests(unittest.TestCase):
    def test_uses_live_clock_while_measurement_is_active(self) -> None:
        measurement_started_at = datetime.now(timezone.utc) - timedelta(seconds=10.0)
        window = _make_window(_measurement_active=True, _measurement_started_at=measurement_started_at)

        elapsed = sensorgram_control_step_overlay_current_elapsed_s(window)

        self.assertIsNotNone(elapsed)
        self.assertGreaterEqual(elapsed, 9.0)
        self.assertLess(elapsed, 15.0)

    def test_uses_rebased_last_event_when_not_measuring(self) -> None:
        session_started_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        event_at = session_started_at + timedelta(seconds=130.0)
        window = _make_window(
            _measurement_active=False,
            _metric_archive_started_at=session_started_at,
            _sensorgram_control_step_events=[
                {"elapsed_s": 30.0, "timestamp_utc_ms": event_at.timestamp() * 1000.0}
            ],
        )

        elapsed = sensorgram_control_step_overlay_current_elapsed_s(window)

        self.assertEqual(elapsed, 130.0)


class VisibleEventsFilterTests(unittest.TestCase):
    """Session view shows step markers from every past measurement in the
    session; "measurement" view, while one is actively recording, must
    keep showing only the current recording's own steps."""

    def test_not_measuring_returns_every_event_unfiltered(self) -> None:
        window = _make_window(_measurement_active=False)
        events = [{"timestamp_utc_ms": 1.0}, {"timestamp_utc_ms": 2.0}]

        self.assertIs(_visible_sensorgram_control_step_events(window, events), events)

    def test_measuring_filters_out_events_from_earlier_measurements(self) -> None:
        measurement_started_at = datetime(2026, 1, 1, 12, 2, 0, tzinfo=timezone.utc)
        window = _make_window(_measurement_active=True, _measurement_started_at=measurement_started_at)
        earlier = {"timestamp_utc_ms": (measurement_started_at - timedelta(seconds=60.0)).timestamp() * 1000.0}
        current = {"timestamp_utc_ms": (measurement_started_at + timedelta(seconds=5.0)).timestamp() * 1000.0}

        visible = _visible_sensorgram_control_step_events(window, [earlier, current])

        self.assertEqual(visible, [current])

    def test_measuring_without_a_started_at_returns_everything(self) -> None:
        window = _make_window(_measurement_active=True, _measurement_started_at=None)
        events = [{"timestamp_utc_ms": 1.0}]

        self.assertIs(_visible_sensorgram_control_step_events(window, events), events)

    def test_events_without_a_timestamp_are_kept_while_measuring(self) -> None:
        measurement_started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        window = _make_window(_measurement_active=True, _measurement_started_at=measurement_started_at)
        legacy_event = {"elapsed_s": 1.0}

        visible = _visible_sensorgram_control_step_events(window, [legacy_event])

        self.assertEqual(visible, [legacy_event])


class CloseSensorgramControlStepOverlaySegmentTests(unittest.TestCase):
    def test_appends_a_stop_boundary_when_last_event_is_still_running(self) -> None:
        window = _make_window(
            _sensorgram_control_step_events=[
                {"state": "RUN", "step_index": 2, "color": "#1F77B4", "label": "Step 2", "timestamp_utc_ms": 1_000.0}
            ]
        )

        close_sensorgram_control_step_overlay_segment(window)

        events = window._sensorgram_control_step_events
        self.assertEqual(len(events), 2)
        boundary = events[-1]
        self.assertEqual(boundary["state"], "STOP")
        self.assertEqual(boundary["event"], "measurement_stopped")
        # Boundary keeps the same step context, just marks it closed.
        self.assertEqual(boundary["step_index"], 2)
        self.assertEqual(boundary["color"], "#1F77B4")
        self.assertGreater(boundary["timestamp_utc_ms"], 1_000.0)

    def test_no_op_when_last_event_is_already_stop(self) -> None:
        window = _make_window(_sensorgram_control_step_events=[{"state": "STOP", "timestamp_utc_ms": 1_000.0}])

        close_sensorgram_control_step_overlay_segment(window)

        self.assertEqual(len(window._sensorgram_control_step_events), 1)

    def test_no_op_when_events_list_is_empty_or_missing(self) -> None:
        window = _make_window(_sensorgram_control_step_events=[])
        close_sensorgram_control_step_overlay_segment(window)  # must not raise
        self.assertEqual(window._sensorgram_control_step_events, [])

        close_sensorgram_control_step_overlay_segment(SimpleNamespace())  # no such attribute at all - must not raise


class MultiMeasurementSessionOverlayTests(unittest.TestCase):
    """End-to-end: two separate measurements' events, accumulated together
    (the actual session-view scenario), must not have their idle gap drawn
    as a single continuous running segment."""

    def test_stop_boundary_prevents_bridging_the_gap_between_measurements(self) -> None:
        # Events already carry the STOP boundary close_sensorgram_control_step_
        # overlay_segment would have appended when measurement 1 stopped
        # (covered separately, with a controlled timestamp here, since that
        # function stamps the boundary with the real datetime.now() - mixing
        # that with these fixed 2026 dates would throw off elapsed_s sort
        # order for reasons that have nothing to do with what this test is
        # actually checking).
        session_started_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        window = _make_window(_metric_archive_started_at=session_started_at)
        window._sensorgram_control_step_events = [
            {
                "state": "RUN",
                "step_index": 1,
                "color": "#1F77B4",
                "label": "Step 1",
                "timestamp_utc_ms": (session_started_at + timedelta(seconds=10.0)).timestamp() * 1000.0,
            },
            {
                # Measurement 1 stopped while its plan step was still "RUN" -
                # the scenario that would bridge the gap without this
                # boundary event.
                "state": "STOP",
                "step_index": 1,
                "color": "#1F77B4",
                "label": "Step 1",
                "event": "measurement_stopped",
                "timestamp_utc_ms": (session_started_at + timedelta(seconds=15.0)).timestamp() * 1000.0,
            },
            {
                # Measurement 2 starts much later and records its own step.
                "state": "RUN",
                "step_index": 1,
                "color": "#ff7f0e",
                "label": "Step 1",
                "timestamp_utc_ms": (session_started_at + timedelta(seconds=500.0)).timestamp() * 1000.0,
            },
        ]

        rebased = _rebase_sensorgram_control_step_events(window, window._sensorgram_control_step_events)
        segments = build_sensorgram_control_step_overlay_segments(rebased, current_elapsed_s=520.0)

        # Exactly two segments: measurement 1's step (10s-15s, closed by the
        # boundary) and measurement 2's still-open step (500s onward) -
        # nothing spanning the idle gap between them.
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["label"], "Step 1")
        self.assertEqual(segments[0]["start_s"], 10.0)
        self.assertEqual(segments[0]["end_s"], 15.0)
        self.assertFalse(segments[0]["active"])
        self.assertEqual(segments[1]["start_s"], 500.0)
        self.assertTrue(segments[1]["active"])


class RefreshEventLabelsTests(unittest.TestCase):
    """Labels must be re-resolved against the experiment-control timeline's
    *current* label mode on every sync - not trusted from whatever was
    baked in when the event was recorded - or toggling the mode
    mid-measurement has no visible effect until the next step transition."""

    def _make_experiment_control_window(self, *, label_for_step) -> SimpleNamespace:
        return SimpleNamespace(
            _read_experiment_control_steps=lambda: [SimpleNamespace(step=1), SimpleNamespace(step=2)],
            _experiment_control_step_label_for_overlay=label_for_step,
        )

    def test_relabels_using_the_current_mode(self) -> None:
        ecw = self._make_experiment_control_window(label_for_step=lambda step: f"fresh-label-{step.step}")
        window = _make_window(_experiment_control_window=ecw)
        events = [{"step_index": 1, "label": "stale-comment-label"}]

        refreshed = _refresh_sensorgram_control_step_event_labels(window, events)

        self.assertEqual(refreshed[0]["label"], "fresh-label-1")
        # Original event dict must be untouched (a new dict is returned).
        self.assertEqual(events[0]["label"], "stale-comment-label")

    def test_no_experiment_control_window_leaves_events_unchanged(self) -> None:
        window = _make_window(_experiment_control_window=None)
        events = [{"step_index": 1, "label": "stale-comment-label"}]

        refreshed = _refresh_sensorgram_control_step_event_labels(window, events)

        self.assertIs(refreshed, events)

    def test_event_with_no_step_index_is_left_unchanged(self) -> None:
        ecw = self._make_experiment_control_window(label_for_step=lambda step: "should-not-be-used")
        window = _make_window(_experiment_control_window=ecw)
        events = [{"label": "boundary-or-pause-event"}]  # no step_index at all

        refreshed = _refresh_sensorgram_control_step_event_labels(window, events)

        self.assertEqual(refreshed[0]["label"], "boundary-or-pause-event")

    def test_out_of_range_step_index_is_left_unchanged(self) -> None:
        ecw = self._make_experiment_control_window(label_for_step=lambda step: "should-not-be-used")
        window = _make_window(_experiment_control_window=ecw)
        events = [{"step_index": 99, "label": "original"}]

        refreshed = _refresh_sensorgram_control_step_event_labels(window, events)

        self.assertEqual(refreshed[0]["label"], "original")

    def test_resolution_error_leaves_the_event_unchanged(self) -> None:
        def _raise(_step):
            raise RuntimeError("boom")

        ecw = self._make_experiment_control_window(label_for_step=_raise)
        window = _make_window(_experiment_control_window=ecw)
        events = [{"step_index": 1, "label": "original"}]

        refreshed = _refresh_sensorgram_control_step_event_labels(window, events)

        self.assertEqual(refreshed[0]["label"], "original")


if __name__ == "__main__":
    unittest.main()
