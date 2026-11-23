"""The out-of-date check of the scan file waits while nothing shows its answer (2026-11-23).

Two separate reasons hold it: the user's Live / Paused choice (`set_paused`) and "the Spectra and Sensorgram docks are both hidden"
(`set_displays_hidden`). They must not overwrite each other: the user's choice is what it was when the displays come back. Reason: the check's
snapshot of the app's inputs runs on the GUI thread and froze the window for seconds after every mask edit while both panels were hidden.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.analysis.scan_results import ScanResultsModule
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _module() -> ScanResultsModule:
    module = ScanResultsModule(lambda: None, scan_running=lambda: False)
    module._stored = object()  # a scan file is loaded: a request for a check is not ignored
    module._path = Path("scan.h5")
    return module


class CheckHoldTests(unittest.TestCase):
    def test_a_request_while_displays_are_hidden_waits_and_runs_when_one_is_shown(self) -> None:
        module = _module()
        module.set_displays_hidden(True)
        module.request_check()
        self.assertFalse(module._timer.isActive())
        self.assertTrue(module.check_is_waiting())
        module.set_displays_hidden(False)
        self.assertTrue(module._timer.isActive())
        self.assertFalse(module.check_is_waiting())

    def test_nothing_requested_means_nothing_runs_when_the_displays_come_back(self) -> None:
        module = _module()
        module.set_displays_hidden(True)
        module.set_displays_hidden(False)
        self.assertFalse(module._timer.isActive())

    def test_a_check_already_waiting_on_its_timer_is_held_back_by_hiding(self) -> None:
        module = _module()
        module.request_check()
        self.assertTrue(module._timer.isActive())
        module.set_displays_hidden(True)
        self.assertFalse(module._timer.isActive())
        self.assertTrue(module.check_is_waiting())

    def test_the_users_pause_survives_the_displays_hiding_and_coming_back(self) -> None:
        module = _module()
        module.set_paused(True)
        module.set_displays_hidden(True)
        module.request_check()
        module.set_displays_hidden(False)  # the displays are back, but the user still has it paused
        self.assertFalse(module._timer.isActive())
        self.assertTrue(module.check_is_waiting())
        module.set_paused(False)
        self.assertTrue(module._timer.isActive())

    def test_going_live_while_the_displays_are_hidden_does_not_start_a_check(self) -> None:
        module = _module()
        module.set_paused(True)
        module.set_displays_hidden(True)
        module.request_check()
        module.set_paused(False)
        self.assertFalse(module._timer.isActive())
        self.assertTrue(module.check_is_waiting())
        module.set_displays_hidden(False)
        self.assertTrue(module._timer.isActive())

    def test_a_forced_start_while_held_waits_instead_of_running(self) -> None:
        module = _module()
        module.set_displays_hidden(True)
        module._start_check()  # e.g. the check right after the file was paired
        self.assertFalse(module.is_checking())
        self.assertTrue(module.check_is_waiting())


class _FakeDock(QObject):
    visibilityChanged = pyqtSignal(bool)


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def set_displays_hidden(self, hidden: bool) -> None:
        self.calls.append(hidden)


class DockWiringTests(unittest.TestCase):
    def test_the_check_is_held_only_while_both_docks_are_hidden(self) -> None:
        recorder, spectra, sensorgram = _Recorder(), _FakeDock(), _FakeDock()
        app_rewrite._wire_check_follows_displays(recorder, {"Spectra": spectra, "Sensorgram": sensorgram})
        spectra.visibilityChanged.emit(False)
        self.assertEqual(recorder.calls[-1], False)  # the Sensorgram is still shown
        sensorgram.visibilityChanged.emit(False)
        self.assertEqual(recorder.calls[-1], True)
        spectra.visibilityChanged.emit(True)
        self.assertEqual(recorder.calls[-1], False)


if __name__ == "__main__":
    unittest.main()
