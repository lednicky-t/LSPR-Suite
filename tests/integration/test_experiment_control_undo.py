"""Coverage for undo/redo (Ctrl+Z / Ctrl+Shift+Z) in the Experiment Control
plan table: cell edits, paste, row move/duplicate/remove, and the global
pump-display toggles. See gui/undo_support.py for the shared architecture and
main_window.py's ``self.undo_stack`` for how it's wired app-wide.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from lspr_app.domain.pump_plan import PumpChannelStep, PumpPlanStep
from lspr_app.gui.experiment_control_dialogs import ExperimentControlDialogs
from lspr_app.gui.experiment_control_window import ExperimentControlWindow


_APP = QApplication.instance() or QApplication([])


def _make_window_with_two_steps() -> ExperimentControlWindow:
    window = ExperimentControlWindow(
        {},
        known_probe=None,
        theme_mode="dark",
        initial_mswitch_devices=[],
        auto_connect_devices=False,
        show_runtime_controls=True,
    )
    steps = [
        PumpPlanStep(
            step=1,
            duration_s=60.0,
            valve="Open",
            switch_position=1,
            description="Row A",
            channels=[PumpChannelStep(flow_ul_min=42.0, direction="CW")] + [PumpChannelStep() for _ in range(3)],
        ),
        PumpPlanStep(
            step=2,
            duration_s=90.0,
            valve="Close",
            switch_position=2,
            description="Row B",
            channels=[PumpChannelStep(flow_ul_min=99.0, direction="CCW")] + [PumpChannelStep() for _ in range(3)],
        ),
    ]
    window._populate_experiment_control_table(steps, selected_row=0)
    return window


class CellEditUndoTests(unittest.TestCase):
    def test_undo_redo_restores_edited_value(self) -> None:
        window = _make_window_with_two_steps()
        model = window._plan_model
        self.assertIs(model._undo_stack, window.undo_stack)

        index = model.index(0, 1)  # duration column
        model.setData(index, 999.0, Qt.ItemDataRole.EditRole)
        self.assertEqual(window._read_experiment_control_steps()[0].duration_s, 999.0)
        self.assertEqual(window.undo_stack.count(), 1)

        window.undo_stack.undo()
        self.assertEqual(window._read_experiment_control_steps()[0].duration_s, 60.0)

        window.undo_stack.redo()
        self.assertEqual(window._read_experiment_control_steps()[0].duration_s, 999.0)

    def test_no_op_edit_does_not_push_a_command(self) -> None:
        window = _make_window_with_two_steps()
        model = window._plan_model
        index = model.index(0, 1)
        model.setData(index, 60.0, Qt.ItemDataRole.EditRole)  # same as the existing value
        self.assertEqual(window.undo_stack.count(), 0)


class RowOperationUndoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = _make_window_with_two_steps()
        self.controller = self.window._experiment_control_edit_controller
        self.controller.set_edit_mode(True)

    def test_duplicate_undo(self) -> None:
        self.controller._select_rows([0])
        self.controller.duplicate_selected_rows()
        self.assertEqual(len(self.window._read_experiment_control_steps()), 3)

        self.window.undo_stack.undo()
        self.assertEqual(len(self.window._read_experiment_control_steps()), 2)

        self.window.undo_stack.redo()
        self.assertEqual(len(self.window._read_experiment_control_steps()), 3)

    def test_remove_undo_restores_row(self) -> None:
        self.controller._select_rows([1])
        self.controller.remove_selected_rows()
        self.assertEqual(len(self.window._read_experiment_control_steps()), 1)

        self.window.undo_stack.undo()
        steps = self.window._read_experiment_control_steps()
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[1].description, "Row B")

    def test_move_undo_restores_order(self) -> None:
        self.controller._select_rows([1])
        self.controller.move_selected_rows(-1)
        self.assertEqual(
            [s.description for s in self.window._read_experiment_control_steps()], ["Row B", "Row A"]
        )

        self.window.undo_stack.undo()
        self.assertEqual(
            [s.description for s in self.window._read_experiment_control_steps()], ["Row A", "Row B"]
        )

    def test_paste_undo_restores_previous_cell_value(self) -> None:
        comment_col = self.window._description_column()
        self.controller._set_selection([(0, comment_col)])
        self.controller.copy_selection()
        self.controller._set_selection([(1, comment_col)])
        self.controller.paste_selection()
        self.assertEqual(self.window._read_experiment_control_steps()[1].description, "Row A")

        self.window.undo_stack.undo()
        self.assertEqual(self.window._read_experiment_control_steps()[1].description, "Row B")


class PumpDisplayToggleUndoTests(unittest.TestCase):
    def test_toggle_undo_restores_previous_state(self) -> None:
        window = _make_window_with_two_steps()
        self.assertEqual((window._pump_display_enabled, window._pump_display_highlight_enabled), (False, False))

        with patch.object(ExperimentControlDialogs, "edit_pump_display_settings", return_value=(True, True)):
            window._edit_pump_display_settings()

        self.assertTrue(window._pump_display_enabled)
        self.assertTrue(window._pump_display_highlight_enabled)
        self.assertEqual(window.undo_stack.count(), 1)

        window.undo_stack.undo()
        self.assertFalse(window._pump_display_enabled)
        self.assertFalse(window._pump_display_highlight_enabled)

        window.undo_stack.redo()
        self.assertTrue(window._pump_display_enabled)
        self.assertTrue(window._pump_display_highlight_enabled)


if __name__ == "__main__":
    unittest.main()
