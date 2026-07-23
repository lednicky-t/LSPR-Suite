"""Double-clicking a step on the sensorgram/experiment-control timeline
must not move devices unless the plan is actually running, holding, or
paused.

_apply_selected_experiment_control_step (wired to the timeline widget's
step_double_activated signal) used to unconditionally apply the
double-clicked step to hardware even when nothing was running - creating
an unstoppable state, since Stop is gated on
_plan_running/_plan_holding/_plan_paused, none of which a bare double-click
outside those states ever sets. Outside those three states, double-click
should only select the row (matching what single-click already does),
never touch hardware.
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

from lspr_app.gui.experiment_control_window import ExperimentControlWindow


def _make_step(step_index: int) -> SimpleNamespace:
    channels = [SimpleNamespace(flow_ul_min=0.0, direction="OFF") for _ in range(6)]
    return SimpleNamespace(step=step_index, valve=f"Valve {step_index}", switch_position=step_index + 1, channels=channels)


def _make_controller(*, running: bool, holding: bool, paused: bool) -> tuple[ExperimentControlWindow, dict[str, list]]:
    steps = [_make_step(1), _make_step(2), _make_step(3)]
    calls: dict[str, list] = {
        "apply_step_to_pump_async": [],
        "set_experiment_control_runtime_row": [],
        "resume_after_manual_step_change": [],
        "select_experiment_control_plan_row": [],
    }
    controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
    controller._plan_running = running
    controller._plan_holding = holding
    controller._plan_paused = paused
    controller._read_experiment_control_steps = lambda: steps
    controller._apply_step_to_pump_async = lambda step, **kwargs: calls["apply_step_to_pump_async"].append((step, kwargs))
    controller._set_experiment_control_runtime_row = lambda row, **kwargs: calls["set_experiment_control_runtime_row"].append((row, kwargs))
    controller._resume_experiment_control_after_manual_step_change = lambda row, **kwargs: calls["resume_after_manual_step_change"].append((row, kwargs))
    controller._select_experiment_control_plan_row = lambda row: calls["select_experiment_control_plan_row"].append(row)
    controller._load_selected_step_into_editor = lambda: None
    controller._update_timeline_selection = lambda: None
    controller._set_status_message = lambda _text: None
    return controller, calls


class ApplySelectedStepWhileIdleTests(unittest.TestCase):
    def test_double_click_while_idle_never_touches_hardware(self) -> None:
        controller, calls = _make_controller(running=False, holding=False, paused=False)

        ExperimentControlWindow._apply_selected_experiment_control_step(controller, 1)

        self.assertEqual(calls["apply_step_to_pump_async"], [])

    def test_double_click_while_idle_still_selects_the_row(self) -> None:
        controller, calls = _make_controller(running=False, holding=False, paused=False)

        ExperimentControlWindow._apply_selected_experiment_control_step(controller, 1)

        self.assertEqual(calls["select_experiment_control_plan_row"], [1])

    def test_out_of_range_row_is_ignored(self) -> None:
        controller, calls = _make_controller(running=False, holding=False, paused=False)

        ExperimentControlWindow._apply_selected_experiment_control_step(controller, 99)

        self.assertEqual(calls["select_experiment_control_plan_row"], [])
        self.assertEqual(calls["apply_step_to_pump_async"], [])


class ApplySelectedStepWhileActiveTests(unittest.TestCase):
    """Regression guard: the three active states must keep behaving exactly
    as before - only the idle (not running/holding/paused) case changed."""

    def test_double_click_while_running_applies_via_runtime_row(self) -> None:
        controller, calls = _make_controller(running=True, holding=False, paused=False)

        ExperimentControlWindow._apply_selected_experiment_control_step(controller, 1)

        self.assertEqual(len(calls["set_experiment_control_runtime_row"]), 1)
        row, kwargs = calls["set_experiment_control_runtime_row"][0]
        self.assertEqual(row, 1)
        self.assertTrue(kwargs.get("apply_step"))
        self.assertEqual(calls["apply_step_to_pump_async"], [])

    def test_double_click_while_holding_resumes_from_the_step(self) -> None:
        controller, calls = _make_controller(running=False, holding=True, paused=False)

        ExperimentControlWindow._apply_selected_experiment_control_step(controller, 2)

        self.assertEqual(len(calls["resume_after_manual_step_change"]), 1)
        row, _kwargs = calls["resume_after_manual_step_change"][0]
        self.assertEqual(row, 2)

    def test_double_click_while_paused_resumes_from_the_step(self) -> None:
        controller, calls = _make_controller(running=False, holding=False, paused=True)

        ExperimentControlWindow._apply_selected_experiment_control_step(controller, 0)

        self.assertEqual(len(calls["resume_after_manual_step_change"]), 1)
        row, _kwargs = calls["resume_after_manual_step_change"][0]
        self.assertEqual(row, 0)


if __name__ == "__main__":
    unittest.main()
