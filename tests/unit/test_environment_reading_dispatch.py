"""handle_environment_reading() (gui/acquisition_controller.py) writes one
temperature/humidity reading to whichever HDF5 writer(s) are active -
mirrors _archive_to_session_writer_if_available's "write to both if
present" pattern for raw spectra (see test_archive_to_session_writer.py).
"""
from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.acquisition_controller import handle_environment_reading


class _FakeEnvironmentWriter:
    def __init__(self) -> None:
        self.readings: list[tuple[int, float | None, float | None]] = []

    def append_environment_reading(self, timestamp_utc_ms, temperature_c, humidity_percent) -> None:
        self.readings.append((timestamp_utc_ms, temperature_c, humidity_percent))


class HandleEnvironmentReadingTests(unittest.TestCase):
    def test_writes_to_session_writer_when_no_recording_is_active(self) -> None:
        session_writer = _FakeEnvironmentWriter()
        window = SimpleNamespace(_measurement_writer=None)

        with patch("lspr_app.storage.measurement_archive.ensure_session_writer", return_value=session_writer):
            handle_environment_reading(window, 23.4, 41.2)

        self.assertEqual(len(session_writer.readings), 1)
        _timestamp, temperature_c, humidity_percent = session_writer.readings[0]
        self.assertEqual(temperature_c, 23.4)
        self.assertEqual(humidity_percent, 41.2)

    def test_writes_to_both_session_and_measurement_writer_when_recording(self) -> None:
        session_writer = _FakeEnvironmentWriter()
        measurement_writer = _FakeEnvironmentWriter()
        window = SimpleNamespace(_measurement_writer=measurement_writer)

        with patch("lspr_app.storage.measurement_archive.ensure_session_writer", return_value=session_writer):
            handle_environment_reading(window, 22.1, 55.0)

        self.assertEqual(len(session_writer.readings), 1)
        self.assertEqual(len(measurement_writer.readings), 1)
        self.assertEqual(session_writer.readings[0][1:], (22.1, 55.0))
        self.assertEqual(measurement_writer.readings[0][1:], (22.1, 55.0))

    def test_no_writers_available_is_a_silent_no_op(self) -> None:
        window = SimpleNamespace(_measurement_writer=None)

        with patch("lspr_app.storage.measurement_archive.ensure_session_writer", return_value=None):
            handle_environment_reading(window, 23.4, 41.2)  # must not raise

    def test_both_values_none_skips_without_even_checking_for_a_writer(self) -> None:
        # Nothing readable this tick (e.g. an ItsyBitsy/Legacy controller is
        # connected instead) - must not create a session writer as a side
        # effect of a poll that found nothing.
        window = SimpleNamespace(_measurement_writer=None)

        with patch("lspr_app.storage.measurement_archive.ensure_session_writer") as mock_ensure:
            handle_environment_reading(window, None, None)

        mock_ensure.assert_not_called()

    def test_partial_reading_is_still_recorded(self) -> None:
        session_writer = _FakeEnvironmentWriter()
        window = SimpleNamespace(_measurement_writer=None)

        with patch("lspr_app.storage.measurement_archive.ensure_session_writer", return_value=session_writer):
            handle_environment_reading(window, 22.1, None)

        self.assertEqual(session_writer.readings[0][1:], (22.1, None))

    def test_session_writer_error_does_not_prevent_measurement_writer_write(self) -> None:
        class _RaisingWriter:
            def append_environment_reading(self, *args, **kwargs) -> None:
                raise RuntimeError("disk full")

        measurement_writer = _FakeEnvironmentWriter()
        window = SimpleNamespace(_measurement_writer=measurement_writer)

        with patch("lspr_app.storage.measurement_archive.ensure_session_writer", return_value=_RaisingWriter()):
            handle_environment_reading(window, 23.4, 41.2)  # must not raise

        self.assertEqual(len(measurement_writer.readings), 1)


if __name__ == "__main__":
    unittest.main()
