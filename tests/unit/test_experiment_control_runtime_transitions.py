from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
import sys

if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui.experiment_control_window import ExperimentControlWindow


class _FakeTimer:
    def __init__(self) -> None:
        self.active = False

    def isActive(self) -> bool:
        return self.active

    def stop(self) -> None:
        self.active = False

    def start(self, _interval_ms: int | None = None) -> None:
        self.active = True


class _FakeButton:
    def __init__(self) -> None:
        self.visible = None
        self.enabled = None
        self.icon = None
        self.tooltip = None
        self.checked = None

    def setVisible(self, value: bool) -> None:
        self.visible = value

    def setEnabled(self, value: bool) -> None:
        self.enabled = value

    def setIcon(self, value) -> None:
        self.icon = value

    def setToolTip(self, value: str) -> None:
        self.tooltip = value

    def setChecked(self, value: bool) -> None:
        self.checked = value


def _make_step(step_index: int) -> SimpleNamespace:
    channels = [SimpleNamespace(flow_ul_min=0.0, direction="OFF") for _ in range(6)]
    return SimpleNamespace(
        step=step_index,
        valve=f"Valve {step_index}",
        switch_position=step_index + 1,
        channels=channels,
    )


class ExperimentControlRuntimeTransitionTests(unittest.TestCase):
    def test_pause_can_start_from_stopped_state(self) -> None:
        controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
        steps = [_make_step(1), _make_step(2), _make_step(3)]
        selected_rows: list[int] = []
        messages: list[str] = []
        emitted: list[tuple[str, int | None, str]] = []

        controller._plan_running = False
        controller._plan_holding = False
        controller._plan_paused = False
        controller.recording_controller = None
        controller.record_with_flow_button = SimpleNamespace(isChecked=lambda: False)
        controller._pending_experiment_control_start_after_recording = None
        controller._plan_elapsed_s = 7.5
        controller._plan_resume_elapsed_s = 5.0
        controller._plan_runtime_s = 4.0
        controller._plan_resume_runtime_s = 3.0
        controller._plan_started_monotonic = None
        controller._step_started_monotonic = None
        controller._measurement_started_monotonic = None
        controller._plan_active_row = None
        controller._paused_plan_step = None
        controller._applied_plan_step = None
        controller._plan_timer = _FakeTimer()
        controller._selected_experiment_control_row = lambda: 1
        controller._read_experiment_control_steps = lambda: steps
        controller._select_experiment_control_plan_row = lambda row: selected_rows.append(row)
        recording_actions: list[str] = []
        controller._request_recording_control = lambda action: recording_actions.append(action) or True
        def _apply_pause_state() -> bool:
            controller._applied_plan_step = _make_step(0)
            return True

        controller._apply_pause_state = _apply_pause_state
        controller._update_experiment_control_toggle_button = lambda: None
        controller._set_status_message = lambda message: messages.append(message)
        controller._emit_experimental_control_state = (
            lambda event, step=None, status="": emitted.append((event, None if step is None else int(step.step), status))
        )

        ExperimentControlWindow._start_paused_experiment_control(controller)

        self.assertTrue(controller._plan_paused)
        self.assertFalse(controller._plan_running)
        self.assertFalse(controller._plan_holding)
        self.assertEqual(controller._plan_active_row, 1)
        self.assertTrue(controller._plan_timer.isActive())
        self.assertEqual(selected_rows, [])
        self.assertEqual(recording_actions, ["start"])
        self.assertEqual(emitted, [("plan_pause", 0, "started in pause state")])
        self.assertTrue(messages)
        self.assertIn("started in pause state", messages[-1])
        self.assertIsNotNone(controller._measurement_started_monotonic)
        self.assertIsNot(controller._paused_plan_step, steps[1])
        self.assertEqual(controller._paused_plan_step.step, steps[1].step)
        self.assertEqual(controller._paused_plan_step.valve, steps[1].valve)
        self.assertEqual(controller._paused_plan_step.switch_position, steps[1].switch_position)
        self.assertEqual(
            [channel.flow_ul_min for channel in controller._paused_plan_step.channels],
            [channel.flow_ul_min for channel in steps[1].channels],
        )

    def test_running_state_starts_blink_timer_for_play_button(self) -> None:
        controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
        controller._plan_running = True
        controller._plan_holding = False
        controller._plan_paused = False
        controller._plan_hold_blink_frame = 0
        controller._plan_hold_blink_timer = _FakeTimer()
        controller._theme_mode = "dark"
        controller._read_experiment_control_steps = lambda: []
        controller._play_plan_button_icon = lambda *, active: object()
        controller._hold_plan_button_icon = lambda *, active: object()
        controller._runtime_pause_button_icon = lambda *, active=False: object()
        controller.plan_toggle_button = _FakeButton()
        controller.hold_plan_button = _FakeButton()
        controller.pause_plan_button = _FakeButton()
        controller.stop_plan_button = _FakeButton()

        ExperimentControlWindow._update_experiment_control_toggle_button(controller)

        self.assertTrue(controller._plan_hold_blink_timer.isActive())
        self.assertIsNotNone(controller.plan_toggle_button.icon)
        self.assertIn("resume", str(controller.plan_toggle_button.tooltip).lower())

    def test_stop_button_remains_visible_while_paused(self) -> None:
        controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
        controller._plan_running = False
        controller._plan_holding = False
        controller._plan_paused = True
        controller._plan_hold_blink_frame = 0
        controller._plan_hold_blink_timer = _FakeTimer()
        controller._theme_mode = "dark"
        controller._read_experiment_control_steps = lambda: []
        controller._play_plan_button_icon = lambda *, active: object()
        controller._hold_plan_button_icon = lambda *, active: object()
        controller._runtime_pause_button_icon = lambda *, active=False: object()
        controller.plan_toggle_button = _FakeButton()
        controller.hold_plan_button = _FakeButton()
        controller.pause_plan_button = _FakeButton()
        controller.stop_plan_button = _FakeButton()

        ExperimentControlWindow._update_experiment_control_toggle_button(controller)

        self.assertTrue(controller.stop_plan_button.visible)
        self.assertTrue(controller.stop_plan_button.enabled)

    def test_timeline_row_prefers_runtime_cursor_while_running(self) -> None:
        controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
        controller._plan_running = True
        controller._plan_holding = False
        controller._plan_paused = False
        controller._plan_active_row = 3
        controller._selected_experiment_control_row = lambda: 1

        self.assertEqual(ExperimentControlWindow._experiment_control_timeline_row(controller), 3)

    def test_timeline_row_falls_back_to_editor_cursor_when_stopped(self) -> None:
        controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
        controller._plan_running = False
        controller._plan_holding = False
        controller._plan_paused = False
        controller._plan_active_row = 3
        controller._selected_experiment_control_row = lambda: 1

        self.assertEqual(ExperimentControlWindow._experiment_control_timeline_row(controller), 1)


if __name__ == "__main__":
    unittest.main()
