"""Unit coverage for the generic app-wide undo/redo building blocks
(SnapshotCommand/push_snapshot in gui/undo_support.py) - see
docs/architecture (or the module's own docstring) for why undo/redo in this
app is snapshot-based rather than per-field diffing.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6.QtGui import QUndoStack
from PyQt6.QtWidgets import QApplication

from lspr_app.gui.undo_support import DEFAULT_UNDO_HISTORY_SIZE, SnapshotCommand, push_snapshot


_APP = QApplication.instance() or QApplication([])


class SnapshotCommandTests(unittest.TestCase):
    def test_redo_applies_after_and_undo_applies_before(self) -> None:
        applied: list[int] = []
        command = SnapshotCommand("Change", before=1, after=2, apply=applied.append)

        command.redo()
        self.assertEqual(applied, [2])

        command.undo()
        self.assertEqual(applied, [2, 1])

    def test_apply_receives_independent_copies(self) -> None:
        # Mutating the value handed to apply() must not corrupt the command's
        # own stored before/after snapshots for the *next* redo/undo call.
        received: list[list[int]] = []

        def apply(value: list[int]) -> None:
            received.append(list(value))  # snapshot what arrived, before mutating it
            value.append(999)  # caller-side mutation should not leak back into the command

        command = SnapshotCommand("Change", before=[1], after=[1, 2], apply=apply)
        command.redo()
        command.undo()
        command.redo()

        self.assertEqual(received, [[1, 2], [1], [1, 2]])


class PushSnapshotTests(unittest.TestCase):
    def test_pushes_and_applies_when_changed(self) -> None:
        stack = QUndoStack()
        applied: list[str] = []
        push_snapshot(stack, "Rename", "old", "new", apply=applied.append)

        self.assertEqual(stack.count(), 1)
        self.assertEqual(applied, ["new"])

        stack.undo()
        self.assertEqual(applied, ["new", "old"])

    def test_no_op_when_before_equals_after(self) -> None:
        stack = QUndoStack()
        applied: list[str] = []
        push_snapshot(stack, "Rename", "same", "same", apply=applied.append)

        self.assertEqual(stack.count(), 0)
        self.assertEqual(applied, [])

    def test_no_op_and_no_apply_when_stack_is_none(self) -> None:
        applied: list[str] = []
        push_snapshot(None, "Rename", "old", "new", apply=applied.append)

        self.assertEqual(applied, [])

    def test_history_size_can_be_limited(self) -> None:
        stack = QUndoStack()
        stack.setUndoLimit(2)
        for i in range(5):
            push_snapshot(stack, f"Step {i}", i, i + 1, apply=lambda _v: None)
        self.assertLessEqual(stack.count(), 2)

    def test_default_history_size_is_positive(self) -> None:
        self.assertGreater(DEFAULT_UNDO_HISTORY_SIZE, 0)


if __name__ == "__main__":
    unittest.main()
