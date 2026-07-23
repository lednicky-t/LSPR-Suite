"""Unit coverage for gui/sensorgram_time_anchor.py - the single source of
truth for "which time anchor applies right now" that replaced several
independently-drifting reimplementations of the same selection logic
(bugs C1/C5/C6/C7 in docs/sensorgram_improvements.md).
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

from lspr_app.gui.sensorgram_time_anchor import (
    display_time_anchor,
    measurement_to_session_offset_s,
    session_time_anchor,
)


def _make_window(**overrides) -> SimpleNamespace:
    window = SimpleNamespace(
        _measurement_active=False,
        _measurement_started_at=None,
        _live_trace_started_at=None,
        _metric_archive_started_at=None,
    )
    for key, value in overrides.items():
        setattr(window, key, value)
    return window


class SessionTimeAnchorTests(unittest.TestCase):
    def test_prefers_metric_archive_started_at(self) -> None:
        session_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        window = _make_window(
            _metric_archive_started_at=session_at,
            _live_trace_started_at=session_at + timedelta(seconds=999.0),
        )
        self.assertEqual(session_time_anchor(window), session_at)

    def test_falls_back_to_live_trace_started_at(self) -> None:
        live_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        window = _make_window(_live_trace_started_at=live_at)
        self.assertEqual(session_time_anchor(window), live_at)

    def test_none_when_nothing_is_set(self) -> None:
        window = _make_window()
        self.assertIsNone(session_time_anchor(window))

    def test_missing_attributes_do_not_raise(self) -> None:
        window = SimpleNamespace()
        self.assertIsNone(session_time_anchor(window))


class DisplayTimeAnchorTests(unittest.TestCase):
    def test_measurement_relative_while_recording(self) -> None:
        measurement_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        session_at = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        window = _make_window(
            _measurement_active=True,
            _measurement_started_at=measurement_at,
            _metric_archive_started_at=session_at,
        )
        self.assertEqual(display_time_anchor(window), measurement_at)

    def test_session_relative_when_not_recording(self) -> None:
        session_at = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        window = _make_window(
            _measurement_active=False,
            _measurement_started_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            _metric_archive_started_at=session_at,
        )
        self.assertEqual(display_time_anchor(window), session_at)

    def test_falls_back_to_session_anchor_if_measurement_active_but_no_started_at(self) -> None:
        # Defensive edge case: _measurement_active and _measurement_started_at
        # are always set together by start_measurement_run, but a still-valid
        # session anchor is a better fallback than silently returning None.
        session_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        window = _make_window(_measurement_active=True, _metric_archive_started_at=session_at)
        self.assertEqual(display_time_anchor(window), session_at)

    def test_none_when_recording_flag_is_false_and_no_session_anchor(self) -> None:
        window = _make_window()
        self.assertIsNone(display_time_anchor(window))


class MeasurementToSessionOffsetTests(unittest.TestCase):
    def test_computes_the_gap_between_session_and_measurement_start(self) -> None:
        session_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        measurement_at = session_at + timedelta(seconds=100.0)
        window = _make_window(_measurement_started_at=measurement_at, _metric_archive_started_at=session_at)

        self.assertEqual(measurement_to_session_offset_s(window), 100.0)

    def test_negative_offset_is_not_clamped(self) -> None:
        session_at = datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc)
        measurement_at = session_at - timedelta(seconds=10.0)
        window = _make_window(_measurement_started_at=measurement_at, _metric_archive_started_at=session_at)

        self.assertEqual(measurement_to_session_offset_s(window), -10.0)

    def test_none_when_measurement_started_at_is_missing(self) -> None:
        window = _make_window(_metric_archive_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertIsNone(measurement_to_session_offset_s(window))

    def test_none_when_session_anchor_is_unavailable(self) -> None:
        window = _make_window(_measurement_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertIsNone(measurement_to_session_offset_s(window))


if __name__ == "__main__":
    unittest.main()
