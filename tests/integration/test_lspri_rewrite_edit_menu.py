"""Edit -> Undo / Redo (Ctrl+Z, Ctrl+Y / Ctrl+Shift+Z).

Every command already pushed onto the shared `undo_manager`; before 2026-10-07
nothing could pop it (the Edit menu was empty). Driven through the actions'
`trigger()` (what a key press or a click fires), never by screen coordinate.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch.**
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtGui import QKeySequence
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMainWindow, QMenu

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.app_rewrite import _wire_edit_menu, build_main_window
    from lspr_imaging_app.undo import FunctionCommand, UndoManager, undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


class _FakeEngine:
    def __init__(self) -> None:
        self.running = False

    def is_running(self) -> bool:
        return self.running


class _Counter:
    """State that a command changes, so undo/redo are observable."""

    def __init__(self) -> None:
        self.value = 0

    def push(self, manager: UndoManager, label: str) -> None:
        self.value += 1
        manager.push(FunctionCommand(label, undo_fn=self._down, redo_fn=self._up))

    def _down(self) -> None:
        self.value -= 1

    def _up(self) -> None:
        self.value += 1


class EditMenuTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = QMainWindow()
        self.window.statusBar()
        self.menu = QMenu("&Edit", self.window)
        self.engine = _FakeEngine()
        self.manager = UndoManager()
        self.undo, self.redo = _wire_edit_menu(self.menu, self.window, self.engine, manager=self.manager)
        self.counter = _Counter()

    def tearDown(self) -> None:
        self.window.deleteLater()

    def test_both_start_disabled_with_plain_labels(self) -> None:
        self.assertFalse(self.undo.isEnabled() or self.redo.isEnabled())
        self.assertEqual((self.undo.text(), self.redo.text()), ("Undo", "Redo"))

    def test_undo_and_redo_follow_the_stack_and_name_the_command(self) -> None:
        self.counter.push(self.manager, "Move ROI")
        self.assertTrue(self.undo.isEnabled())
        self.assertFalse(self.redo.isEnabled())
        self.assertEqual(self.undo.text(), "Undo Move ROI")

        self.undo.trigger()
        self.assertEqual(self.counter.value, 0)
        self.assertFalse(self.undo.isEnabled())
        self.assertTrue(self.redo.isEnabled())
        self.assertEqual(self.redo.text(), "Redo Move ROI")
        self.assertIn("Undid: Move ROI", self.window.statusBar().currentMessage())

        self.redo.trigger()
        self.assertEqual(self.counter.value, 1)
        self.assertTrue(self.undo.isEnabled())
        self.assertIn("Redid: Move ROI", self.window.statusBar().currentMessage())

    def test_the_shortcuts_are_ctrl_z_and_ctrl_y_or_ctrl_shift_z_everywhere_in_the_app(self) -> None:
        self.assertIn(QKeySequence("Ctrl+Z"), self.undo.shortcuts())
        redo_keys = self.redo.shortcuts()
        self.assertIn(QKeySequence("Ctrl+Shift+Z"), redo_keys)
        self.assertIn(QKeySequence("Ctrl+Y"), redo_keys)
        for action in (self.undo, self.redo):  # also from a floating panel (its own top-level window)
            self.assertEqual(action.shortcutContext(), Qt.ShortcutContext.ApplicationShortcut)

    def test_refused_while_an_analysis_runs_and_says_why(self) -> None:
        self.counter.push(self.manager, "Delete ROI")
        self.engine.running = True
        self.undo.trigger()  # what a Ctrl+Z press would fire if the menu state were stale
        self.assertEqual(self.counter.value, 1, "nothing was undone")
        self.assertTrue(self.manager.can_undo)
        self.assertIn("analysis is running", self.window.statusBar().currentMessage())
        self.menu.aboutToShow.emit()  # opening the menu re-checks
        self.assertFalse(self.undo.isEnabled())
        self.engine.running = False
        self.menu.aboutToShow.emit()
        self.assertTrue(self.undo.isEnabled())

    def test_disabled_during_a_gesture_then_back_when_it_ends(self) -> None:
        self.counter.push(self.manager, "Move ROI")
        self.manager.begin_batch("Drag")
        self.menu.aboutToShow.emit()
        self.assertFalse(self.undo.isEnabled())
        self.manager.end_batch()
        self.assertTrue(self.undo.isEnabled())


class UndoManagerGestureTest(unittest.TestCase):
    def test_undo_and_redo_do_nothing_while_a_batch_is_open(self) -> None:
        manager = UndoManager()
        counter = _Counter()
        counter.push(manager, "Edit")
        manager.undo()
        self.assertEqual(counter.value, 0)
        manager.begin_batch("Drag")
        manager.redo()  # a gesture is in progress: neither direction may touch the stack
        manager.undo()
        self.assertEqual(counter.value, 0)
        self.assertFalse(manager.can_undo or manager.can_redo)
        manager.cancel_batch()
        self.assertTrue(manager.can_redo)
        manager.redo()
        self.assertEqual(counter.value, 1)


class EditMenuInTheAppTest(unittest.TestCase):
    """The real window: a ROI added through the toolbox is undone from the
    Edit menu the user sees."""

    def setUp(self) -> None:
        undo_manager.clear()
        self.window = build_main_window()

    def tearDown(self) -> None:
        undo_manager.clear()
        self.window.deleteLater()

    def _edit_action(self, prefix: str):
        menu = next(a.menu() for a in self.window.menuBar().actions() if a.text() == "&Edit")
        return next(a for a in menu.actions() if a.text().startswith(prefix))

    def test_the_edit_menu_holds_undo_and_redo(self) -> None:
        self.assertFalse(self._edit_action("Undo").isEnabled())
        self.assertFalse(self._edit_action("Redo").isEnabled())

    def test_undoing_an_added_roi_removes_it_and_redo_brings_it_back(self) -> None:
        from lspr_imaging_app.roi import RoiToolbox

        toolbox = self.window.findChild(RoiToolbox)
        if toolbox is None:  # modules are plain Python objects in the app; reach it through the ROI table panel
            from lspr_imaging_app.panels.roi_table import RoiTablePanel

            toolbox = self.window.findChild(RoiTablePanel)._toolbox
        toolbox.add_roi(20.0, 30.0)
        self.assertEqual(len(toolbox.rois()), 1)
        undo = self._edit_action("Undo")
        self.assertTrue(undo.isEnabled())
        undo.trigger()
        self.assertEqual(len(toolbox.rois()), 0)
        self._edit_action("Redo").trigger()
        self.assertEqual(len(toolbox.rois()), 1)


if __name__ == "__main__":
    unittest.main()
