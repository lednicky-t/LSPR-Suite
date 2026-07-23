"""_archive_to_session_writer_if_available() writes each raw spectrum's
elapsed time into the session HDF5 file's time_series dataset (required by
the schema, read by the evaluation app). It must anchor that elapsed time
to a value that survives Record/Stop cycles - _live_trace_started_at does
not (stop_measurement_run clears it and nothing restores it while live
acquisition keeps running), which silently wrote near-zero elapsed times
for every spectrum recorded after the first Stop in a session. See
docs/sensorgram_improvements.md.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.models import Spectrum
from lspr_app.gui.acquisition_controller import _archive_to_session_writer_if_available


class _FakeSessionWriter:
    def __init__(self) -> None:
        self.batches: list[tuple[list[Spectrum], list[float], list[float]]] = []

    def append_batch(self, spectra, time_series_s, peak_positions_nm) -> None:
        self.batches.append((list(spectra), list(time_series_s), list(peak_positions_nm)))


def _make_spectrum(acquired_at: datetime) -> Spectrum:
    return Spectrum(
        wavelengths_nm=np.asarray([610.0, 620.0], dtype=np.float64),
        values=np.asarray([1.0, 2.0], dtype=np.float64),
        y_label="sample",
        acquired_at=acquired_at,
    )


class ArchiveToSessionWriterTests(unittest.TestCase):
    def test_prefers_the_stable_session_anchor_over_live_trace_started_at(self) -> None:
        session_started_at = datetime(2026, 1, 2, 3, 0, 0, tzinfo=timezone.utc)
        spectrum = _make_spectrum(session_started_at + timedelta(seconds=42.0))
        writer = _FakeSessionWriter()
        window = SimpleNamespace(
            _measurement_writer=None,
            _metric_archive_started_at=session_started_at,
            _live_trace_started_at=session_started_at + timedelta(seconds=1000.0),  # stale, must be ignored
        )

        with patch("lspr_app.storage.measurement_archive.ensure_session_writer", return_value=writer):
            _archive_to_session_writer_if_available(window, spectrum)

        self.assertEqual(len(writer.batches), 1)
        _spectra, times, _peaks = writer.batches[0]
        self.assertEqual(times, [42.0])

    def test_stays_session_relative_after_live_trace_started_at_is_cleared(self) -> None:
        # Reproduces the post-Stop scenario directly: without the fix this
        # fell back to elapsed_s = 0.0 for every subsequent spectrum,
        # silently corrupting the session file's time_series dataset for
        # the rest of the session.
        session_started_at = datetime(2026, 1, 2, 3, 0, 0, tzinfo=timezone.utc)
        spectrum = _make_spectrum(session_started_at + timedelta(seconds=150.0))
        writer = _FakeSessionWriter()
        window = SimpleNamespace(
            _measurement_writer=None,
            _metric_archive_started_at=session_started_at,
            _live_trace_started_at=None,
        )

        with patch("lspr_app.storage.measurement_archive.ensure_session_writer", return_value=writer):
            _archive_to_session_writer_if_available(window, spectrum)

        _spectra, times, _peaks = writer.batches[0]
        self.assertEqual(times, [150.0])

    def test_falls_back_to_live_trace_started_at_before_session_writer_exists(self) -> None:
        live_started_at = datetime(2026, 1, 2, 3, 0, 0, tzinfo=timezone.utc)
        spectrum = _make_spectrum(live_started_at + timedelta(seconds=3.0))
        writer = _FakeSessionWriter()
        window = SimpleNamespace(
            _measurement_writer=None,
            _metric_archive_started_at=None,
            _live_trace_started_at=live_started_at,
        )

        with patch("lspr_app.storage.measurement_archive.ensure_session_writer", return_value=writer):
            _archive_to_session_writer_if_available(window, spectrum)

        _spectra, times, _peaks = writer.batches[0]
        self.assertEqual(times, [3.0])

    def test_falls_back_to_zero_when_no_anchor_is_available(self) -> None:
        spectrum = _make_spectrum(datetime(2026, 1, 2, 3, 0, 0, tzinfo=timezone.utc))
        writer = _FakeSessionWriter()
        window = SimpleNamespace(
            _measurement_writer=None,
            _metric_archive_started_at=None,
            _live_trace_started_at=None,
        )

        with patch("lspr_app.storage.measurement_archive.ensure_session_writer", return_value=writer):
            _archive_to_session_writer_if_available(window, spectrum)

        _spectra, times, _peaks = writer.batches[0]
        self.assertEqual(times, [0.0])

    def test_skips_writing_when_session_writer_is_the_measurement_writer(self) -> None:
        writer = _FakeSessionWriter()
        window = SimpleNamespace(
            _measurement_writer=writer,
            _metric_archive_started_at=None,
            _live_trace_started_at=None,
        )
        spectrum = _make_spectrum(datetime(2026, 1, 2, 3, 0, 0, tzinfo=timezone.utc))

        with patch("lspr_app.storage.measurement_archive.ensure_session_writer", return_value=writer):
            _archive_to_session_writer_if_available(window, spectrum)

        self.assertEqual(writer.batches, [])

    def test_no_session_writer_available_is_a_silent_no_op(self) -> None:
        window = SimpleNamespace(
            _measurement_writer=None,
            _metric_archive_started_at=None,
            _live_trace_started_at=None,
        )
        spectrum = _make_spectrum(datetime(2026, 1, 2, 3, 0, 0, tzinfo=timezone.utc))

        with patch("lspr_app.storage.measurement_archive.ensure_session_writer", return_value=None):
            _archive_to_session_writer_if_available(window, spectrum)  # must not raise


if __name__ == "__main__":
    unittest.main()
