"""Experiment-control plan step navigation and status-line ETA math.

Two related but distinct bugs, both found while investigating why the
sensorgram's per-step timing looked wrong after using the Next button:

- Bug A: _move_to_relative_experiment_control_step (the Next button while
  the plan is running) didn't reset _plan_elapsed_s/_plan_resume_elapsed_s/
  _plan_started_monotonic the way its sibling _jump_to_experiment_control_step
  already did, so the new step's elapsed-time tracking kept accumulating
  from wherever the previous step left off instead of restarting at 0.
- Bug B: _refresh_status_line's "Step left"/"Plan left" display mixed
  plan-cumulative step.end_s/total_end_s with step-relative _plan_elapsed_s
  directly, a unit mismatch that only happened to look right on the first
  step of a plan.

See docs/sensorgram_improvements.md.
"""

from __future__ import annotations

import sys
import unittest
from time import monotonic
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.experiment_control_window import ExperimentControlWindow


def _make_step(step_index: int, *, start_s: float, duration_s: float) -> SimpleNamespace:
    channels = [SimpleNamespace(flow_ul_min=0.0, direction="OFF") for _ in range(6)]
    return SimpleNamespace(
        step=step_index,
        valve=f"Valve {step_index}",
        switch_position=step_index + 1,
        channels=channels,
        start_s=start_s,
        end_s=start_s + duration_s,
        duration_s=duration_s,
    )


class MoveToRelativeStepResetsElapsedTimeTests(unittest.TestCase):
    """Bug A: pressing Next while the plan is running."""

    def _make_controller(self, steps: list[SimpleNamespace]) -> ExperimentControlWindow:
        controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
        controller._plan_running = True
        controller._plan_holding = False
        controller._plan_paused = False
        controller._plan_active_row = 0
        # Simulates "step 1 has been running for 30s" - the exact scenario
        # reported: the elapsed value that must NOT carry over to step 2.
        controller._plan_elapsed_s = 30.0
        controller._plan_resume_elapsed_s = 30.0
        controller._plan_started_monotonic = monotonic() - 30.0
        controller._step_started_monotonic = monotonic() - 30.0
        controller._plan_runtime_s = 0.0
        controller._plan_resume_runtime_s = 0.0
        controller._selected_experiment_control_row = lambda: 0
        controller._read_experiment_control_steps = lambda: steps
        runtime_row_calls: list[int] = []

        def _set_runtime_row(row: int, *, event: str, apply_step: bool = False, **_kwargs) -> None:
            runtime_row_calls.append(row)
            controller._plan_active_row = row

        controller._set_experiment_control_runtime_row = _set_runtime_row
        controller._step_runtime_for_display = lambda: 0.0
        controller._set_status_message = lambda _msg: None
        controller._runtime_row_calls = runtime_row_calls
        return controller

    def test_next_button_resets_elapsed_time_for_the_new_step(self) -> None:
        steps = [
            _make_step(1, start_s=0.0, duration_s=30.0),
            _make_step(2, start_s=30.0, duration_s=30.0),
        ]
        controller = self._make_controller(steps)

        ExperimentControlWindow._move_to_relative_experiment_control_step(controller, 1)

        self.assertEqual(controller._runtime_row_calls, [1])
        self.assertEqual(controller._plan_active_row, 1)
        self.assertEqual(controller._plan_elapsed_s, 0.0)
        self.assertEqual(controller._plan_resume_elapsed_s, 0.0)
        # Must be freshly stamped "now", not the stale value from 30s ago.
        self.assertGreater(controller._plan_started_monotonic, monotonic() - 1.0)

    def test_previous_button_also_resets_elapsed_time(self) -> None:
        steps = [
            _make_step(1, start_s=0.0, duration_s=30.0),
            _make_step(2, start_s=30.0, duration_s=30.0),
        ]
        controller = self._make_controller(steps)
        controller._plan_active_row = 1

        ExperimentControlWindow._move_to_relative_experiment_control_step(controller, -1)

        self.assertEqual(controller._plan_active_row, 0)
        self.assertEqual(controller._plan_elapsed_s, 0.0)
        self.assertEqual(controller._plan_resume_elapsed_s, 0.0)


class FinishOnNextPastLastStepTests(unittest.TestCase):
    """Pressing Next while already on the last step must finish the plan,
    not clamp to (and replay) the same last step."""

    def _make_controller(self, steps: list[SimpleNamespace], *, running: bool, holding: bool, paused: bool, active_row: int) -> ExperimentControlWindow:
        controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
        controller._plan_running = running
        controller._plan_holding = holding
        controller._plan_paused = paused
        controller._plan_active_row = active_row
        controller._plan_elapsed_s = 12.0
        controller._plan_resume_elapsed_s = 12.0
        controller._plan_started_monotonic = monotonic() - 12.0
        controller._step_started_monotonic = monotonic() - 12.0
        controller._plan_runtime_s = 0.0
        controller._plan_resume_runtime_s = 0.0
        controller._selected_experiment_control_row = lambda: active_row
        controller._read_experiment_control_steps = lambda: steps
        controller._step_runtime_for_display = lambda: 0.0
        status_messages: list[str] = []
        controller._set_status_message = lambda msg: status_messages.append(msg)
        controller._status_messages = status_messages
        runtime_row_calls: list[int] = []
        controller._set_experiment_control_runtime_row = lambda row, **kwargs: runtime_row_calls.append(row)
        controller._runtime_row_calls = runtime_row_calls
        stop_calls: list[int] = []

        def _stop_experiment_control() -> None:
            stop_calls.append(1)
            controller._plan_running = False
            controller._plan_holding = False
            controller._plan_paused = False

        controller._stop_experiment_control = _stop_experiment_control
        controller._stop_calls = stop_calls
        return controller

    def test_next_on_last_step_while_running_finishes_the_plan(self) -> None:
        steps = [
            _make_step(1, start_s=0.0, duration_s=30.0),
            _make_step(2, start_s=30.0, duration_s=30.0),
        ]
        controller = self._make_controller(steps, running=True, holding=False, paused=False, active_row=1)

        ExperimentControlWindow._move_to_relative_experiment_control_step(controller, 1)

        self.assertEqual(controller._stop_calls, [1])
        self.assertIn("Experiment plan finished.", controller._status_messages)
        # Must not have replayed the last step from scratch.
        self.assertEqual(controller._runtime_row_calls, [])

    def test_next_on_last_step_while_holding_finishes_the_plan(self) -> None:
        steps = [_make_step(1, start_s=0.0, duration_s=30.0)]
        controller = self._make_controller(steps, running=False, holding=True, paused=False, active_row=0)

        ExperimentControlWindow._move_to_relative_experiment_control_step(controller, 1)

        self.assertEqual(controller._stop_calls, [1])

    def test_next_on_last_step_while_paused_finishes_the_plan(self) -> None:
        steps = [_make_step(1, start_s=0.0, duration_s=30.0)]
        controller = self._make_controller(steps, running=False, holding=False, paused=True, active_row=0)

        ExperimentControlWindow._move_to_relative_experiment_control_step(controller, 1)

        self.assertEqual(controller._stop_calls, [1])

    def test_next_before_the_last_step_still_advances_normally(self) -> None:
        # Regression guard: only the actual "past the end" case should
        # trigger finishing - a normal advance must be unaffected.
        steps = [
            _make_step(1, start_s=0.0, duration_s=30.0),
            _make_step(2, start_s=30.0, duration_s=30.0),
            _make_step(3, start_s=60.0, duration_s=30.0),
        ]
        controller = self._make_controller(steps, running=True, holding=False, paused=False, active_row=0)

        ExperimentControlWindow._move_to_relative_experiment_control_step(controller, 1)

        self.assertEqual(controller._stop_calls, [])
        self.assertEqual(controller._runtime_row_calls, [1])

    def test_next_past_last_step_while_idle_just_clamps_without_finishing(self) -> None:
        # Not running/holding/paused - there's nothing to "finish"; must
        # keep the existing clamp-and-select behavior.
        steps = [_make_step(1, start_s=0.0, duration_s=30.0)]
        controller = self._make_controller(steps, running=False, holding=False, paused=False, active_row=0)
        jump_calls: list[int] = []
        controller._jump_to_experiment_control_step = lambda row: jump_calls.append(row)

        ExperimentControlWindow._move_to_relative_experiment_control_step(controller, 1)

        self.assertEqual(controller._stop_calls, [])
        self.assertEqual(jump_calls, [0])


class RefreshStatusLineEtaMathTests(unittest.TestCase):
    """Bug B: "Step left" / "Plan left" must use the plan-cumulative
    elapsed position (step.start_s + _plan_elapsed_s), not the raw
    step-relative _plan_elapsed_s directly."""

    def _make_controller(self, steps: list[SimpleNamespace], *, active_row: int, plan_elapsed_s: float) -> ExperimentControlWindow:
        controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
        controller._plan_running = True
        controller._plan_holding = False
        controller._plan_paused = False
        controller._plan_active_row = active_row
        controller._plan_elapsed_s = plan_elapsed_s
        controller._time_unit_mode = "s"
        controller._status_message_base = "Status"
        controller._read_experiment_control_steps = lambda: steps
        controller.connection_status_label = SimpleNamespace(setText=lambda text: setattr(controller, "_last_status_text", text))
        return controller

    def test_first_step_eta_was_already_correct(self) -> None:
        # Regression guard: the bug was invisible on step 1 (start_s=0), so
        # this must keep behaving exactly as before.
        steps = [
            _make_step(1, start_s=0.0, duration_s=30.0),
            _make_step(2, start_s=30.0, duration_s=30.0),
            _make_step(3, start_s=60.0, duration_s=30.0),
        ]
        controller = self._make_controller(steps, active_row=0, plan_elapsed_s=10.0)

        ExperimentControlWindow._refresh_status_line(controller)

        self.assertIn("Step left: 20 s", controller._last_status_text)
        self.assertIn("Plan left: 80 s", controller._last_status_text)

    def test_second_step_eta_uses_cumulative_plan_position(self) -> None:
        steps = [
            _make_step(1, start_s=0.0, duration_s=30.0),
            _make_step(2, start_s=30.0, duration_s=30.0),
            _make_step(3, start_s=60.0, duration_s=30.0),
        ]
        # 5s into step 2 (plan-cumulative position = 30 + 5 = 35s of 90s).
        controller = self._make_controller(steps, active_row=1, plan_elapsed_s=5.0)

        ExperimentControlWindow._refresh_status_line(controller)

        self.assertIn("Step left: 25 s", controller._last_status_text)
        self.assertIn("Plan left: 55 s", controller._last_status_text)

    def test_third_step_eta_uses_cumulative_plan_position(self) -> None:
        steps = [
            _make_step(1, start_s=0.0, duration_s=30.0),
            _make_step(2, start_s=30.0, duration_s=30.0),
            _make_step(3, start_s=60.0, duration_s=30.0),
        ]
        # 20s into step 3 (plan-cumulative position = 60 + 20 = 80s of 90s).
        controller = self._make_controller(steps, active_row=2, plan_elapsed_s=20.0)

        ExperimentControlWindow._refresh_status_line(controller)

        self.assertIn("Step left: 10 s", controller._last_status_text)
        self.assertIn("Plan left: 10 s", controller._last_status_text)


if __name__ == "__main__":
    unittest.main()
