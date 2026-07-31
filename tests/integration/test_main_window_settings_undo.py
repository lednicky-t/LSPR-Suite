"""Coverage for undo/redo (Ctrl+Z / Ctrl+Shift+Z) of the main window's
always-visible acquisition/processing controls (integration time, averages,
dark/nonlinearity correction, smoothing, baseline, range, ...). These have no
dialog/Cancel to fall back on - changes apply immediately - so they get the
same shared undo history as the experiment control plan table (see
gui/undo_support.py and MainWindow.undo_stack).

Changes are debounced (see MainWindow._track_acquisition_setting_for_undo /
_track_processing_setting_for_undo) so a spinbox drag or a burst of
wheel-scroll ticks becomes one undo step, not one per tick - these tests
bypass the real 600ms wait by forcing the commit timers to fire immediately,
except for one test that verifies the real timer actually debounces.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

from lspr_app.device.simulated import SimulatedSpectrometer
from lspr_app.domain.session import MeasurementSession
from lspr_app.gui.main_window import MainWindow
from lspr_app.storage import user_profile as up


_APP = QApplication.instance() or QApplication([])


def _make_main_window() -> MainWindow:
    with patch.object(MainWindow, "_start_hardware_initialization", lambda self: None):
        return MainWindow(SimulatedSpectrometer(), MeasurementSession(), None)


def _spin_event_loop(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class _IsolatedSettingsTestCase(unittest.TestCase):
    """Base class for any test that constructs a real MainWindow.

    MainWindow loads its processing/acquisition/UI-state settings from the
    machine-wide lspr_settings.json (see storage/user_profile.py -
    GLOBAL_CONFIG_PATH lives under platformdirs.user_config_dir, not the
    repo), so without this isolation these tests silently pick up whatever
    was last saved by an actual manual run of the app on this machine - e.g.
    a smoothing_window value already clamped at its spin box's range max,
    which makes a "+3" test edit a no-op instead of a real change. Same
    pattern as test_user_profile.py's _TempConfigDirTestCase.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        patches = [
            patch.object(up, "_SHARED_CONFIG_DIR", base),
            patch.object(up, "_REGISTRY_PATH", base / "lspr_users.json"),
            patch.object(up, "GLOBAL_CONFIG_PATH", base / "lspr_settings.json"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class AcquisitionSettingsUndoTests(_IsolatedSettingsTestCase):
    def test_integration_time_change_undoes_and_redoes(self) -> None:
        window = _make_main_window()
        before = window.integration_spin.value()

        window.integration_spin.setValue(before + 25.0)
        self.assertEqual(window.undo_stack.count(), 0, "should be debounced, not committed yet")

        window._acquisition_undo_commit_timer.stop()
        window._commit_acquisition_undo()
        self.assertEqual(window.undo_stack.count(), 1)
        self.assertEqual(window.integration_spin.value(), before + 25.0)

        window.undo_stack.undo()
        self.assertEqual(window.integration_spin.value(), before)

        window.undo_stack.redo()
        self.assertEqual(window.integration_spin.value(), before + 25.0)

    def test_dark_correction_checkbox_undoes(self) -> None:
        window = _make_main_window()
        before = window.correct_dark_check.isChecked()

        window.correct_dark_check.setChecked(not before)
        window._acquisition_undo_commit_timer.stop()
        window._commit_acquisition_undo()

        window.undo_stack.undo()
        self.assertEqual(window.correct_dark_check.isChecked(), before)

    def test_real_debounce_coalesces_a_burst_into_one_entry(self) -> None:
        window = _make_main_window()
        before = window.averages_spin.value()

        for step in (1, 2, 3):
            window.averages_spin.setValue(before + step)
            _spin_event_loop(50)  # well under the 600ms debounce window

        self.assertEqual(window.undo_stack.count(), 0)
        _spin_event_loop(900)  # let the real debounce timer fire

        self.assertEqual(window.undo_stack.count(), 1)
        self.assertEqual(window.averages_spin.value(), before + 3)
        window.undo_stack.undo()
        self.assertEqual(window.averages_spin.value(), before)


class ProcessingSettingsUndoTests(_IsolatedSettingsTestCase):
    def test_smoothing_window_change_undoes_and_redoes(self) -> None:
        window = _make_main_window()
        before = window.smoothing_window_spin.value()

        window.smoothing_window_spin.setValue(before + 3)
        self.assertEqual(window.undo_stack.count(), 0)

        window._processing_undo_commit_timer.stop()
        window._commit_processing_undo()
        self.assertEqual(window.undo_stack.count(), 1)

        window.undo_stack.undo()
        self.assertEqual(window.smoothing_window_spin.value(), before)

        window.undo_stack.redo()
        self.assertEqual(window.smoothing_window_spin.value(), before + 3)

    def test_no_op_change_does_not_push_a_command(self) -> None:
        window = _make_main_window()
        current = window.smoothing_window_spin.value()

        window.smoothing_window_spin.setValue(current)  # no real change
        window._processing_undo_commit_timer.stop()
        window._commit_processing_undo()
        self.assertEqual(window.undo_stack.count(), 0)


class SharedUndoStackTests(_IsolatedSettingsTestCase):
    def test_experiment_control_window_shares_the_same_stack(self) -> None:
        window = _make_main_window()
        self.assertIs(window._experiment_control_window.undo_stack, window.undo_stack)

    def test_history_size_preference_sets_the_limit(self) -> None:
        window = _make_main_window()
        window._set_undo_history_size(7)
        self.assertEqual(window.undo_stack.undoLimit(), 7)


if __name__ == "__main__":
    unittest.main()
