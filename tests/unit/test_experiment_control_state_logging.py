"""Durable device-state/failure logging regression test.

Before this fix, _handle_experimental_control_state_recorded only wrote a
device state/failure event into the HDF5 file when an official measurement
was actively recording (self._measurement_active). Outside of that window -
during setup, between measurements, while paused - a device failure left no
durable record anywhere in the file, only the in-app status bar/log. This
test proves the always-on session file now receives every event regardless
of _measurement_active, while the per-measurement file keeps its existing,
narrower gating unchanged.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.main_window import MainWindow


class _FakeWriter:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def append_flow_state(self, row: dict[str, object]) -> None:
        self.rows.append(row)


def _make_window(*, measurement_active: bool, measurement_writer: object | None) -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    window._update_window_mode_label = lambda: None
    window._measurement_active = measurement_active
    window._measurement_writer = measurement_writer
    window._measurement_started_at = None
    window._metric_archive_started_at = None
    window._record_sensorgram_control_step_event = lambda row: None
    window._sync_sensorgram_control_step_overlay = lambda: None
    return window


def _payload() -> dict[str, object]:
    return {
        "event": "step_applied",
        "status": "M-switch not connected",
        "pump_connected": True,
        "valve_connected": True,
        "switch_connected": False,
    }


class ExperimentalControlStateLoggingTests(unittest.TestCase):
    def test_event_reaches_session_writer_even_when_no_measurement_is_active(self) -> None:
        window = _make_window(measurement_active=False, measurement_writer=None)
        session_writer = _FakeWriter()
        with patch(
            "lspr_app.storage.measurement_archive.ensure_session_writer",
            return_value=session_writer,
        ):
            window._handle_experimental_control_state_recorded(_payload())

        self.assertEqual(len(session_writer.rows), 1)
        row = session_writer.rows[0]
        self.assertEqual(row["status"], "M-switch not connected")
        self.assertFalse(row["switch_connected"])
        self.assertIn("timestamp_utc_ms", row)
        self.assertIn("t_ms", row)
        self.assertEqual(row["t_ms"], 0)  # no _metric_archive_started_at anchor yet

    def test_event_reaches_both_writers_when_measurement_is_active(self) -> None:
        measurement_writer = _FakeWriter()
        window = _make_window(measurement_active=True, measurement_writer=measurement_writer)
        session_writer = _FakeWriter()
        with patch(
            "lspr_app.storage.measurement_archive.ensure_session_writer",
            return_value=session_writer,
        ):
            window._handle_experimental_control_state_recorded(_payload())

        self.assertEqual(len(session_writer.rows), 1)
        self.assertEqual(len(measurement_writer.rows), 1)
        self.assertEqual(measurement_writer.rows[0]["status"], "M-switch not connected")

    def test_session_write_failure_does_not_prevent_measurement_write(self) -> None:
        measurement_writer = _FakeWriter()
        window = _make_window(measurement_active=True, measurement_writer=measurement_writer)

        class _RaisingWriter:
            def append_flow_state(self, row: dict[str, object]) -> None:
                raise RuntimeError("disk full")

        with patch(
            "lspr_app.storage.measurement_archive.ensure_session_writer",
            return_value=_RaisingWriter(),
        ):
            window._handle_experimental_control_state_recorded(_payload())

        self.assertEqual(len(measurement_writer.rows), 1)

    def test_session_writer_not_yet_available_is_a_silent_no_op(self) -> None:
        window = _make_window(measurement_active=False, measurement_writer=None)
        with patch(
            "lspr_app.storage.measurement_archive.ensure_session_writer",
            return_value=None,
        ):
            # Must not raise even though no writer exists yet (matches
            # ensure_session_writer's existing "not ready until the first
            # spectrum is processed" semantics elsewhere).
            window._handle_experimental_control_state_recorded(_payload())

    def test_non_dict_payload_is_ignored(self) -> None:
        window = _make_window(measurement_active=False, measurement_writer=None)
        session_writer = _FakeWriter()
        with patch(
            "lspr_app.storage.measurement_archive.ensure_session_writer",
            return_value=session_writer,
        ):
            window._handle_experimental_control_state_recorded(None)

        self.assertEqual(session_writer.rows, [])

    def test_session_t_ms_is_relative_to_metric_archive_start(self) -> None:
        window = _make_window(measurement_active=False, measurement_writer=None)
        window._metric_archive_started_at = datetime.now(timezone.utc)
        session_writer = _FakeWriter()
        with patch(
            "lspr_app.storage.measurement_archive.ensure_session_writer",
            return_value=session_writer,
        ):
            window._handle_experimental_control_state_recorded(_payload())

        row = session_writer.rows[0]
        self.assertGreaterEqual(row["t_ms"], 0)
        self.assertLess(row["t_ms"], 1000)  # test runs well under a second


if __name__ == "__main__":
    unittest.main()
