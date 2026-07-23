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
    record_sensorgram_control_step_event,
    sensorgram_control_step_overlay_current_elapsed_s,
)


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


if __name__ == "__main__":
    unittest.main()
