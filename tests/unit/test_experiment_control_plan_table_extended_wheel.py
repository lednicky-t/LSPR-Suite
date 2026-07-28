"""Experiment-control plan-table 'extended' (per-channel) view coverage:

- Direction cells must be click-to-toggle (mouse or Space/Enter) and
  scroll-to-toggle CW/CCW, and must paint as the same rotation-arrow glyph
  used by the manual-control panel's Dir buttons (direction_glyph in
  experiment_control_builders.py), not plain "CW"/"CCW" text.
- Tube-diameter cells must be scroll-adjustable, same as the flow-rate
  column already was - tube diameter is a shared per-channel setting (it
  backs the manual_tube_spins spinbox), so scrolling any row's tube cell
  for a channel moves that channel's spinbox and therefore every row.
- Flow-rate wheel-scroll must step by 5 by default and by 1 while Ctrl is
  held.

See docs/experiment-control/experiment_plan_execution_model.md.
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtWidgets import QApplication

from lspr_app.domain.pump_plan import (
    ACTIVE_PUMP_CHANNELS,
    TUBE_DIAMETER_OPTIONS,
    PumpChannelStep,
    PumpPlanStep,
    nearest_tube_diameter_option,
)
from lspr_app.gui.experiment_control_builders import direction_glyph
from lspr_app.gui.experiment_control_window import ExperimentControlWindow
from lspr_app.gui.flow_plan_model import ExperimentPlanDirectionDelegate, ExperimentPlanTableModel


def _ensure_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _make_model_with_one_step(*, flow_ul_min: float = 10.0, direction: str = "CW") -> ExperimentPlanTableModel:
    model = ExperimentPlanTableModel(["col"] * 24, None)
    channels = [PumpChannelStep(flow_ul_min=flow_ul_min, direction=direction) for _ in range(ACTIVE_PUMP_CHANNELS)]
    step = PumpPlanStep(step=1, duration_s=60.0, channels=channels)
    model.set_single_step(step)
    return model


class _FakeTubeSpin:
    """Stand-in for the real manual_tube_spins TubeDiameterComboBox entries.

    _cycle_plan_table_cell_by_wheel only calls .value()/.step() on these, so
    a plain fake covers the logic under test without constructing real
    QComboBox widgets alongside a bare ExperimentControlWindow.__new__()
    controller - that combination reliably segfaults under pytest (confirmed
    via isolated repro), likely because __new__ leaves the controller's
    C++/sip side improperly constructed and something in real QWidget
    construction touches it during teardown/GC.

    Mirrors TubeDiameterComboBox's index-into-TUBE_DIAMETER_OPTIONS model
    (not a free 0.01 mm step) - the pump only accepts these 26 diameters,
    see pump_plan.TUBE_DIAMETER_OPTIONS.
    """

    def __init__(self, mm: float = 0.25) -> None:
        self._index = TUBE_DIAMETER_OPTIONS.index(nearest_tube_diameter_option(mm))

    def value(self) -> float:
        return TUBE_DIAMETER_OPTIONS[self._index].mm

    def setValue(self, mm: float) -> None:
        self._index = TUBE_DIAMETER_OPTIONS.index(nearest_tube_diameter_option(mm))

    def step(self, delta: int) -> None:
        self._index = max(0, min(self._index + int(delta), len(TUBE_DIAMETER_OPTIONS) - 1))


def _make_controller(model: ExperimentPlanTableModel, *, current_row: int = 0) -> ExperimentControlWindow:
    controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
    controller._time_unit_mode = "s"
    controller.plan_table = SimpleNamespace(currentRow=lambda: current_row, model=lambda: model)
    controller.manual_tube_spins = [_FakeTubeSpin() for _ in range(ACTIVE_PUMP_CHANNELS)]
    return controller


class DirectionGlyphMappingTests(unittest.TestCase):
    """The paint() path renders direction_glyph(direction) - confirm it
    resolves to the same rotation-arrow characters as the manual-control
    panel's Dir button (set_direction_button/create_direction_button)."""

    def test_cw_is_the_clockwise_arrow(self) -> None:
        self.assertEqual(direction_glyph("CW"), "↻")

    def test_ccw_is_the_counterclockwise_arrow(self) -> None:
        self.assertEqual(direction_glyph("CCW"), "↺")

    def test_unrecognized_value_falls_back_to_cw(self) -> None:
        self.assertEqual(direction_glyph("sideways"), "↻")


class DirectionDelegateToggleTests(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_app()
        self.model = _make_model_with_one_step(direction="CW")
        # Go through the real __init__ (not __new__-bypass) - QStyledItemDelegate
        # is a QObject subclass, and skipping __init__ leaves its C++ side
        # improperly constructed, which crashes later during garbage
        # collection rather than at the point of misuse. A bare SimpleNamespace
        # with a None plan_table stands in for the real window/table.
        self.delegate = ExperimentPlanDirectionDelegate(SimpleNamespace(plan_table=None))
        self.index = self.model.index(0, 5)  # channel 0's direction column

    def test_toggle_flips_cw_to_ccw(self) -> None:
        self.assertTrue(self.delegate._toggle(self.model, self.index))
        self.assertEqual(self.model.data(self.index, Qt.ItemDataRole.EditRole), "CCW")

    def test_toggle_flips_ccw_back_to_cw(self) -> None:
        self.delegate._toggle(self.model, self.index)
        self.assertTrue(self.delegate._toggle(self.model, self.index))
        self.assertEqual(self.model.data(self.index, Qt.ItemDataRole.EditRole), "CW")

    def test_create_editor_returns_none_so_clicking_toggles_instead_of_opening_a_popup(self) -> None:
        self.assertIsNone(self.delegate.createEditor(None, None, self.index))

    def test_left_click_release_toggles(self) -> None:
        event = SimpleNamespace(
            type=lambda: QEvent.Type.MouseButtonRelease,
            button=lambda: Qt.MouseButton.LeftButton,
        )
        self.assertTrue(self.delegate.editorEvent(event, self.model, None, self.index))
        self.assertEqual(self.model.data(self.index, Qt.ItemDataRole.EditRole), "CCW")

    def test_right_click_release_does_not_toggle(self) -> None:
        event = SimpleNamespace(
            type=lambda: QEvent.Type.MouseButtonRelease,
            button=lambda: Qt.MouseButton.RightButton,
        )
        self.assertFalse(self.delegate.editorEvent(event, self.model, None, self.index))
        self.assertEqual(self.model.data(self.index, Qt.ItemDataRole.EditRole), "CW")

    def test_space_key_toggles(self) -> None:
        event = SimpleNamespace(type=lambda: QEvent.Type.KeyPress, key=lambda: Qt.Key.Key_Space)
        self.assertTrue(self.delegate.editorEvent(event, self.model, None, self.index))
        self.assertEqual(self.model.data(self.index, Qt.ItemDataRole.EditRole), "CCW")

    def test_enter_key_toggles(self) -> None:
        event = SimpleNamespace(type=lambda: QEvent.Type.KeyPress, key=lambda: Qt.Key.Key_Return)
        self.assertTrue(self.delegate.editorEvent(event, self.model, None, self.index))
        self.assertEqual(self.model.data(self.index, Qt.ItemDataRole.EditRole), "CCW")

    def test_unrelated_key_does_not_toggle(self) -> None:
        event = SimpleNamespace(type=lambda: QEvent.Type.KeyPress, key=lambda: Qt.Key.Key_A)
        self.assertFalse(self.delegate.editorEvent(event, self.model, None, self.index))
        self.assertEqual(self.model.data(self.index, Qt.ItemDataRole.EditRole), "CW")

    def test_unrelated_event_type_returns_false(self) -> None:
        event = SimpleNamespace(type=lambda: QEvent.Type.Paint)
        self.assertFalse(self.delegate.editorEvent(event, self.model, None, self.index))


class CyclePlanTableCellByWheelDirectionTests(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_app()
        self.model = _make_model_with_one_step(direction="CW")
        self.controller = _make_controller(self.model)
        self.index = self.model.index(0, 5)  # channel 0's direction column

    def test_scroll_up_toggles_cw_to_ccw(self) -> None:
        result = ExperimentControlWindow._cycle_plan_table_cell_by_wheel(self.controller, self.index, 120)
        self.assertTrue(result)
        self.assertEqual(self.model.data(self.index, Qt.ItemDataRole.EditRole), "CCW")

    def test_scroll_down_also_toggles_since_direction_is_only_two_states(self) -> None:
        result = ExperimentControlWindow._cycle_plan_table_cell_by_wheel(self.controller, self.index, -120)
        self.assertTrue(result)
        self.assertEqual(self.model.data(self.index, Qt.ItemDataRole.EditRole), "CCW")

    def test_row_under_cursor_that_is_not_the_selected_row_is_ignored(self) -> None:
        self.controller.plan_table = SimpleNamespace(currentRow=lambda: 1, model=lambda: self.model)
        result = ExperimentControlWindow._cycle_plan_table_cell_by_wheel(self.controller, self.index, 120)
        self.assertFalse(result)
        self.assertEqual(self.model.data(self.index, Qt.ItemDataRole.EditRole), "CW")


class CyclePlanTableCellByWheelTubeTests(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_app()
        self.model = _make_model_with_one_step()
        self.controller = _make_controller(self.model)
        self.index = self.model.index(0, 6)  # channel 0's tube column

    def test_scroll_up_increases_that_channels_tube_spinbox(self) -> None:
        result = ExperimentControlWindow._cycle_plan_table_cell_by_wheel(self.controller, self.index, 120)
        self.assertTrue(result)
        # Steps to the *next* supported diameter (0.38 mm), not +0.01 mm - the
        # pump only accepts the 26 catalog sizes in TUBE_DIAMETER_OPTIONS.
        self.assertAlmostEqual(self.controller.manual_tube_spins[0].value(), 0.38, places=2)

    def test_scroll_down_decreases_that_channels_tube_spinbox(self) -> None:
        ExperimentControlWindow._cycle_plan_table_cell_by_wheel(self.controller, self.index, -120)
        self.assertAlmostEqual(self.controller.manual_tube_spins[0].value(), 0.19, places=2)

    def test_other_channels_tube_spinboxes_are_unaffected(self) -> None:
        ExperimentControlWindow._cycle_plan_table_cell_by_wheel(self.controller, self.index, 120)
        for channel_index in (1, 2, 3):
            self.assertAlmostEqual(self.controller.manual_tube_spins[channel_index].value(), 0.25, places=2)

    def test_scrolling_channel_ones_tube_column_moves_channel_ones_spinbox(self) -> None:
        index = self.model.index(0, 9)  # channel 1's tube column
        ExperimentControlWindow._cycle_plan_table_cell_by_wheel(self.controller, index, 120)
        self.assertAlmostEqual(self.controller.manual_tube_spins[1].value(), 0.38, places=2)
        self.assertAlmostEqual(self.controller.manual_tube_spins[0].value(), 0.25, places=2)


class CyclePlanTableCellByWheelRateStepTests(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_app()
        self.model = _make_model_with_one_step(flow_ul_min=10.0)
        self.controller = _make_controller(self.model)
        self.index = self.model.index(0, 4)  # channel 0's rate column

    def test_scroll_up_without_modifier_increases_by_five(self) -> None:
        ExperimentControlWindow._cycle_plan_table_cell_by_wheel(self.controller, self.index, 120)
        self.assertEqual(self.model.data(self.index, Qt.ItemDataRole.EditRole), "15")

    def test_scroll_down_without_modifier_decreases_by_five(self) -> None:
        ExperimentControlWindow._cycle_plan_table_cell_by_wheel(self.controller, self.index, -120)
        self.assertEqual(self.model.data(self.index, Qt.ItemDataRole.EditRole), "5")

    def test_scroll_up_with_ctrl_increases_by_one(self) -> None:
        ExperimentControlWindow._cycle_plan_table_cell_by_wheel(
            self.controller, self.index, 120, Qt.KeyboardModifier.ControlModifier
        )
        self.assertEqual(self.model.data(self.index, Qt.ItemDataRole.EditRole), "11")

    def test_scroll_down_with_ctrl_decreases_by_one(self) -> None:
        ExperimentControlWindow._cycle_plan_table_cell_by_wheel(
            self.controller, self.index, -120, Qt.KeyboardModifier.ControlModifier
        )
        self.assertEqual(self.model.data(self.index, Qt.ItemDataRole.EditRole), "9")

    def test_rate_never_goes_below_zero(self) -> None:
        model = _make_model_with_one_step(flow_ul_min=2.0)
        controller = _make_controller(model)
        index = model.index(0, 4)
        ExperimentControlWindow._cycle_plan_table_cell_by_wheel(controller, index, -120)
        self.assertEqual(model.data(index, Qt.ItemDataRole.EditRole), "0")


if __name__ == "__main__":
    unittest.main()
