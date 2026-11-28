"""`launch_wiring.start_gui_stall_watchdog` (2026-11-28): the GUI thread's timer beats the watchdog, and the watching thread starts and
stops with the app. The watchdog is a stand-in that counts calls; no real thread, no real stall."""

from __future__ import annotations

import sys
import time
import unittest
from types import SimpleNamespace

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app import launch_wiring  # noqa: E402


class _FakeWatchdog:
    def __init__(self) -> None:
        self.beats = 0
        self.started = 0
        self.stopped = 0

    def beat(self) -> None:
        self.beats += 1

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


class GuiStallWiringTests(unittest.TestCase):
    def test_the_timer_beats_and_the_watcher_starts_once(self) -> None:
        watchdog = _FakeWatchdog()
        launch_wiring.start_gui_stall_watchdog(_APP, SimpleNamespace(gui_stall=watchdog))  # type: ignore[arg-type]
        deadline = time.monotonic() + 3.0
        while watchdog.beats < 2 and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        self.assertGreaterEqual(watchdog.beats, 2)
        self.assertEqual(watchdog.started, 1)

    def test_the_watcher_is_stopped_when_the_app_quits(self) -> None:
        watchdog = _FakeWatchdog()
        launch_wiring.start_gui_stall_watchdog(_APP, SimpleNamespace(gui_stall=watchdog))  # type: ignore[arg-type]
        _APP.aboutToQuit.emit()
        self.assertEqual(watchdog.stopped, 1)

    def test_without_a_watchdog_nothing_happens(self) -> None:
        launch_wiring.start_gui_stall_watchdog(_APP, SimpleNamespace(gui_stall=None))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
