"""Coverage for the Experiment Control plan table's cell copy/paste feature
(ExperimentControlEditingController in gui/experiment_control_editing.py),
gated behind "table edit mode" (the pencil icon next to Add/Duplicate/Remove
step).

This exercises the real ExperimentControlWindow and controller - not mocks -
because the two bugs this guards against were both integration-level: turning
edit mode on crashed with ``AttributeError: 'ExperimentControlEditingController'
object has no attribute '_runtime_active'``, and copy/paste itself crashed
with ``AttributeError`` for three ExperimentControlWindow methods
(``_experiment_control_read_cell_value`` / ``_experiment_control_value_to_text``
/ ``_experiment_control_write_row_value``) that were referenced by the
controller but never defined - i.e. the feature had never actually been
exercised end-to-end before.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from lspr_app.domain.pump_plan import PumpChannelStep, PumpPlanStep
from lspr_app.gui.experiment_control_window import ExperimentControlWindow


# Held at module scope: QApplication.instance() or QApplication([]) would otherwise
# construct-and-immediately-garbage-collect a new QApplication if nothing keeps a
# Python reference to it, crashing the process natively on the next widget construction.
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


class EditModeToggleTests(unittest.TestCase):
    def test_enabling_edit_mode_does_not_raise(self) -> None:
        # Regression test: set_edit_mode(True) used to crash with
        # AttributeError because _runtime_active()/_runtime_row() were never
        # defined on ExperimentControlEditingController (only on the sibling
        # _SelectionOverlay class).
        window = _make_window_with_two_steps()
        controller = window._experiment_control_edit_controller
        controller.set_edit_mode(True)
        self.assertTrue(controller.edit_mode)
        controller.set_edit_mode(False)
        self.assertFalse(controller.edit_mode)


class CopyPasteMethodTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = _make_window_with_two_steps()
        self.controller = self.window._experiment_control_edit_controller
        self.controller.set_edit_mode(True)
        self.valve_col = self.window._valve_column()
        self.comment_col = self.window._description_column()
        self.flow_col = self.window._flow_rate_column(0)
        self.duration_col = 1

    def test_same_kind_single_cell_copy_paste(self) -> None:
        self.controller._set_selection([(0, self.flow_col)])
        self.controller.copy_selection()
        self.controller._set_selection([(1, self.flow_col)])
        self.controller.paste_selection()

        after = self.window._read_experiment_control_steps()
        self.assertEqual(after[1].channels[0].flow_ul_min, 42.0)

    def test_incompatible_kind_paste_is_rejected(self) -> None:
        self.controller._set_selection([(0, self.flow_col)])
        self.controller.copy_selection()
        before_valve = self.window._read_experiment_control_steps()[1].valve

        self.controller._set_selection([(1, self.valve_col)])
        self.controller.paste_selection()

        after = self.window._read_experiment_control_steps()
        self.assertEqual(after[1].valve, before_valve)

    def test_aligned_block_copy_paste(self) -> None:
        self.controller._set_selection([(0, self.valve_col), (0, self.comment_col)])
        self.controller.copy_selection()
        self.controller._set_selection([(1, self.valve_col)])
        self.controller.paste_selection()

        after = self.window._read_experiment_control_steps()
        self.assertEqual(after[1].valve, "Open")
        self.assertEqual(after[1].description, "Row A")

    def test_step_and_time_columns_are_never_paste_targets(self) -> None:
        self.controller._set_selection([(0, self.duration_col)])
        self.controller.copy_selection()
        before = self.window._read_experiment_control_steps()[1]

        self.controller._set_selection([(1, 0)])  # step-number column
        self.controller.paste_selection()
        after = self.window._read_experiment_control_steps()
        self.assertEqual(after[1].step, before.step)


class CopyPasteKeyboardEventTests(unittest.TestCase):
    def test_ctrl_c_then_ctrl_v_round_trips_through_real_key_events(self) -> None:
        window = _make_window_with_two_steps()
        controller = window._experiment_control_edit_controller
        controller.set_edit_mode(True)
        comment_col = window._description_column()

        controller._set_selection([(0, comment_col)])
        QApplication.sendEvent(
            window.plan_table, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier, "c")
        )
        self.assertIn("Row A", QApplication.clipboard().text())

        controller._set_selection([(1, comment_col)])
        QApplication.sendEvent(
            window.plan_table, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier, "v")
        )
        after = window._read_experiment_control_steps()
        self.assertEqual(after[1].description, "Row A")


if __name__ == "__main__":
    unittest.main()
