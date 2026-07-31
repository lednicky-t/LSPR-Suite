"""Coverage for caching acquired Dark/Reference spectra in the active user's
settings file so they survive an app restart ("session reset") - see
storage/app_config.py's save_dark_reference_cache/load_dark_reference_cache,
and gui/main_window_state.py's _restore_cached_dark_reference.

_restore_cached_dark_reference must run against window._session *after*
apply_source_mode_for has swapped it between _hardware_session and
_simulation_session, or the restored spectra land on the session about to be
discarded and are silently lost - a real bug caught via live testing, not
just a hypothetical.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
import tempfile

from tests._paths import ensure_repo_paths


ensure_repo_paths()

import sys

APP_SRC = Path(__file__).resolve().parents[2] / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.models import Spectrum
from lspr_app.storage.app_config import load_dark_reference_cache, save_dark_reference_cache
from lspr_app.gui.main_window_state import _restore_cached_dark_reference


def _make_spectrum(kind: str) -> Spectrum:
    return Spectrum(
        wavelengths_nm=[400.0, 500.0, 600.0],
        values=[100.0, 200.0, 300.0],
        y_label="Intensity (counts)",
        acquired_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        metadata={"kind": kind, "integration_time_ms": 4.5},
    )


class DarkReferenceCacheRoundTripTests(unittest.TestCase):
    def test_round_trips_both_spectra(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lspr_settings.json"
            dark = _make_spectrum("dark")
            reference = _make_spectrum("reference")

            save_dark_reference_cache(dark, reference, path=path)
            restored_dark, restored_reference = load_dark_reference_cache(path=path)

            self.assertIsNotNone(restored_dark)
            self.assertIsNotNone(restored_reference)
            self.assertEqual(list(restored_dark.wavelengths_nm), [400.0, 500.0, 600.0])
            self.assertEqual(list(restored_dark.values), [100.0, 200.0, 300.0])
            self.assertEqual(restored_dark.metadata["kind"], "dark")

    def test_round_trips_dark_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lspr_settings.json"
            save_dark_reference_cache(_make_spectrum("dark"), None, path=path)

            restored_dark, restored_reference = load_dark_reference_cache(path=path)

            self.assertIsNotNone(restored_dark)
            self.assertIsNone(restored_reference)

    def test_missing_file_returns_none_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "does_not_exist.json"
            restored_dark, restored_reference = load_dark_reference_cache(path=path)

            self.assertIsNone(restored_dark)
            self.assertIsNone(restored_reference)


class _FakeSession:
    def __init__(self) -> None:
        self.dark = None
        self.reference = None

    def set_dark(self, spectrum) -> None:
        self.dark = spectrum

    def set_reference(self, spectrum) -> None:
        self.reference = spectrum


class RestoreCachedDarkReferenceTests(unittest.TestCase):
    def test_restores_onto_whichever_session_is_currently_active(self) -> None:
        # Simulates the ordering that matters: this must be called against
        # window._session *after* it's been swapped to the session that's
        # actually going to stay active (see apply_acquisition_state_to_widgets).
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lspr_settings.json"
            save_dark_reference_cache(_make_spectrum("dark"), _make_spectrum("reference"), path=path)

            hardware_session = _FakeSession()
            simulation_session = _FakeSession()
            window = type("_FakeWindow", (), {})()

            from unittest.mock import patch

            with patch(
                "lspr_app.gui.main_window_state.load_dark_reference_cache",
                return_value=load_dark_reference_cache(path=path),
            ):
                # Session swapped to simulation, matching what
                # apply_source_mode_for would have just done.
                window._session = simulation_session
                _restore_cached_dark_reference(window)

            self.assertIsNotNone(simulation_session.dark)
            self.assertIsNotNone(simulation_session.reference)
            self.assertIsNone(hardware_session.dark)
            self.assertIsNone(hardware_session.reference)


if __name__ == "__main__":
    unittest.main()
