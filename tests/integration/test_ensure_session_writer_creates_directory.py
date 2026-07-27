"""ensure_session_writer() must create its target directory before
constructing the writer - without this, AsyncHDF5MeasurementWriter's
background thread fails to open the file with no visible error (no
on_error callback is wired for the session writer), silently disabling the
whole always-on session archive (raw spectra, metrics, and environment
readings alike) whenever the target folder doesn't already exist yet (a
fresh install, or an unset/not-yet-created project destination).

Found while manually verifying the new environment-reading feature: a
fresh scratch directory reproduced this exactly. See
storage/measurement_archive.py's _session_file_path().
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.models import ProcessingSettings, Spectrum
from lspr_app.storage.measurement_archive import ensure_session_writer


def _make_spectrum() -> Spectrum:
    return Spectrum(
        wavelengths_nm=np.asarray([600.0, 610.0, 620.0], dtype=np.float64),
        values=np.asarray([1.0, 2.0, 3.0], dtype=np.float64),
        y_label="sample",
        acquired_at=datetime.now(timezone.utc),
    )


class EnsureSessionWriterCreatesDirectoryTests(unittest.TestCase):
    def test_creates_missing_nested_target_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target_dir = Path(temp_dir) / "not_created_yet" / "nested"
            self.assertFalse(target_dir.exists())

            window = SimpleNamespace(
                _session=SimpleNamespace(name="demo_session"),
                recording_project_destination=lambda: str(target_dir),
                recording_experiment_name=lambda: "",
                _current_processing_settings=lambda: ProcessingSettings(),
            )

            writer = ensure_session_writer(window, _make_spectrum())
            try:
                self.assertIsNotNone(writer)
                self.assertTrue(target_dir.is_dir())
                path = Path(getattr(window, "_session_writer_path"))
                self.assertEqual(path.parent, target_dir)
            finally:
                if writer is not None:
                    writer.close()

            # The file must actually exist on disk once fully closed - not
            # just a writer object whose background thread silently died.
            self.assertTrue(path.exists())

    def test_second_call_reuses_the_same_writer_without_recreating_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target_dir = Path(temp_dir) / "reuse_target"
            window = SimpleNamespace(
                _session=SimpleNamespace(name="demo_session"),
                recording_project_destination=lambda: str(target_dir),
                recording_experiment_name=lambda: "",
                _current_processing_settings=lambda: ProcessingSettings(),
            )

            first = ensure_session_writer(window, _make_spectrum())
            second = ensure_session_writer(window, _make_spectrum())
            try:
                self.assertIs(first, second)
            finally:
                first.close()


if __name__ == "__main__":
    unittest.main()
