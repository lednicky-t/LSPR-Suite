"""MainWindow.closeEvent must cancel an in-progress bulk sensorgram
calculation before waiting on background threads to finish - previously only
an in-progress OME-Zarr export was cancelled on close, leaving a running
"Start analysis" sensorgram (the one FunctionWorker task that can legitimately
run for minutes) as the most likely candidate for a daemon thread still alive
when the interpreter starts shutting down - the confirmed trigger condition
for this app's documented native PyQt6-sip crash on close (see project memory
lspri_pyqt6_sip_crash_on_close). This can't prove the native crash is
prevented (it's a probabilistic third-party bug), but it can prove the
cancellation this fix relies on actually fires.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.main_window import MainWindow  # noqa: E402


class TestCloseCancelsRunningSensorgram(unittest.TestCase):
    def test_close_sets_the_sensorgram_cancel_event_when_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(Path(tmp), fast_startup=True)
            try:
                window._sensorgram_running = True
                window._sensorgram_cancel_event = threading.Event()
                window.close()
                self.assertTrue(
                    window._sensorgram_cancel_event.is_set(),
                    "closeEvent should cancel an in-progress sensorgram run the same way the Stop button does",
                )
            finally:
                window._state.dataset = None
                window.deleteLater()

    def test_close_is_a_no_op_when_no_sensorgram_is_running(self) -> None:
        """Regression guard: the common case (nothing running) must not
        raise just because _sensorgram_running/_sensorgram_cancel_event are
        being touched in closeEvent now."""
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(Path(tmp), fast_startup=True)
            try:
                self.assertFalse(window._sensorgram_running)
                self.assertIsNone(window._sensorgram_cancel_event)
                window.close()
            finally:
                window._state.dataset = None
                window.deleteLater()


if __name__ == "__main__":
    unittest.main()
