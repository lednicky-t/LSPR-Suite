"""Characterization tests for the experiment-control run/hold/pause/stop
state machine (Phase 2, LSPRi acq experiment-control reuse - Tier 2, written
2026-08-09 BEFORE any restructuring).

This locks in the *current* behavior of the safety-critical logic that
decides when pump/valve/selector rotary-switch commands are sent to real
hardware, so a later extraction into a shared `lspr_acq_shell` state machine
(driven through a host-callback interface) can be checked against it rather
than trusted by inspection. Only 10 tests
(`test_experiment_control_step_navigation.py`) covered this logic before this
file - not enough for a rewrite of code this consequential.

Every stubbed collaborator here (`_apply_step_to_pump_async`,
`_sync_experiment_control_timeline`, `_read_experiment_control_steps`, etc.)
is exactly the "host" contract `lspr_acq_shell.experiment_control_run_loop.PlanRunLoopMixin`
now documents.

Follows the existing bare-`__new__` + stubbed-collaborator pattern from
`test_experiment_control_step_navigation.py` rather than constructing a real
Qt window.

UPDATE 2026-08-09, after the actual extraction: the state-machine methods
under test are now inherited from `PlanRunLoopMixin` rather than defined
directly on `ExperimentControlWindow` - moved verbatim, so every test below
still exercises the exact same code, just via the mixin. All 10 pre-existing
tests in `test_experiment_control_step_navigation.py` passed against the
moved code with zero changes; this file needed exactly one change - the
`monotonic()` patch target moved from `lspr_app.gui.experiment_control_window`
to `lspr_acq_shell.experiment_control_run_loop`, since that's where the
function is actually called from now. `PlanRunLoopMixin` is the real owner
of this logic; testing it through `ExperimentControlWindow` (rather than a
second, parallel bare-mixin fixture) was kept deliberately, since the
mixin's whole point is running unmodified inside a real window - this is the
same code path either way.
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_acq_shell.pump_plan import PumpChannelStep, PumpPlanStep
from lspr_app.gui.experiment_control_window import ExperimentControlWindow


_MODULE = "lspr_acq_shell.experiment_control_run_loop"


def _make_step(step_index: int, *, duration_s: float = 30.0, description: str = "") -> PumpPlanStep:
    return PumpPlanStep(
        step=step_index,
        duration_s=duration_s,
        start_s=0.0,
        end_s=duration_s,
        color="#4E79A7",
        valve=f"valve{step_index}",
        switch_position=step_index,
        description=description,
        channels=[PumpChannelStep(flow_ul_min=10.0, direction="CW") for _ in range(6)],
    )


class _FakeTimer:
    """Stand-in for QTimer's start/stop/isActive contract used by the plan timer."""

    def __init__(self) -> None:
        self.start_calls: list[int] = []
        self.stop_calls = 0
        self._active = False

    def start(self, ms: int) -> None:
        self.start_calls.append(int(ms))
        self._active = True

    def stop(self) -> None:
        self.stop_calls += 1
        self._active = False

    def isActive(self) -> bool:  # noqa: N802 - Qt naming convention being mimicked
        return self._active


def _make_controller(
    steps: list[PumpPlanStep] | None = None,
    *,
    running: bool = False,
    holding: bool = False,
    paused: bool = False,
    active_row: int | None = None,
    selected_row: int | None = 0,
    pump_connected: bool = True,
) -> ExperimentControlWindow:
    controller = ExperimentControlWindow.__new__(ExperimentControlWindow)
    controller._plan_running = running
    controller._plan_holding = holding
    controller._plan_paused = paused
    controller._plan_active_row = active_row
    controller._plan_elapsed_s = 0.0
    controller._plan_resume_elapsed_s = 0.0
    controller._plan_runtime_s = 0.0
    controller._plan_resume_runtime_s = 0.0
    controller._plan_started_monotonic = None
    controller._step_started_monotonic = None
    controller._measurement_started_monotonic = None
    controller._applied_plan_step = None
    controller._paused_plan_step = None
    controller._step_apply_inflight = 0
    controller._pending_experiment_control_start_after_recording = None
    controller._plan_timer = _FakeTimer()
    controller._experiment_control_pause_template = PumpPlanStep(
        step=0, duration_s=0.0, color="#B44A4A", valve="Open", switch_position=1,
        description="Pause", channels=[PumpChannelStep() for _ in range(6)],
    )
    controller.record_with_flow_button = SimpleNamespace(isChecked=lambda: False)
    controller.recording_controller = None

    steps = steps if steps is not None else []
    controller._read_experiment_control_steps = lambda: steps
    controller._selected_experiment_control_row = lambda: selected_row

    calls = {
        "select_row": [], "runtime_row": [], "sync_timeline": [], "apply_step": [],
        "toggle_button": 0, "status_messages": [], "emit_state": [], "stop_all_channels": 0,
        "load_selected_step": 0, "update_timeline_selection": 0,
    }
    controller._calls = calls

    controller._select_experiment_control_plan_row = lambda row: calls["select_row"].append(row)
    # _set_experiment_control_runtime_row is deliberately NOT stubbed - its
    # real implementation is small glue that calls straight through to the
    # already-stubbed _apply_step_to_pump_async/_sync_experiment_control_timeline/
    # _emit_experimental_control_state, so leaving it real lets the jump/apply
    # tests characterize the actual call chain instead of a hand-simulated one.
    controller._sync_experiment_control_timeline = lambda s, row, **kw: calls["sync_timeline"].append((row, kw))
    controller._apply_step_to_pump_async = lambda step, *, start, on_success=None: calls["apply_step"].append(
        (step, start, on_success)
    )
    controller._update_experiment_control_toggle_button = lambda: calls.__setitem__(
        "toggle_button", calls["toggle_button"] + 1
    )
    controller._set_status_message = lambda msg: calls["status_messages"].append(msg)
    controller._emit_experimental_control_state = lambda event, step=None, *, status="": calls["emit_state"].append(
        (event, step, status)
    )
    controller._service_device_connected = lambda name: pump_connected if name == "pump" else True
    controller._stop_all_channels = lambda: calls.__setitem__("stop_all_channels", calls["stop_all_channels"] + 1)
    controller._load_selected_step_into_editor = lambda: calls.__setitem__(
        "load_selected_step", calls["load_selected_step"] + 1
    )
    controller._update_timeline_selection = lambda: calls.__setitem__(
        "update_timeline_selection", calls["update_timeline_selection"] + 1
    )
    controller._request_recording_control = lambda action: True
    return controller


class RuntimeHelperTests(unittest.TestCase):
    """The small clock/flag primitives everything else is built on."""

    def test_set_plan_runtime_flags_sets_all_three_independently(self) -> None:
        controller = _make_controller()
        ExperimentControlWindow._set_plan_runtime_flags(controller, running=True, holding=False, paused=False)
        self.assertTrue(controller._plan_running)
        self.assertFalse(controller._plan_holding)
        self.assertFalse(controller._plan_paused)

    def test_capture_plan_elapsed_from_clock_with_no_started_monotonic_returns_existing_value(self) -> None:
        controller = _make_controller()
        controller._plan_started_monotonic = None
        controller._plan_elapsed_s = 12.5
        result = ExperimentControlWindow._capture_plan_elapsed_from_clock(controller)
        self.assertEqual(result, 12.5)
        self.assertEqual(controller._plan_elapsed_s, 12.5)

    def test_capture_plan_elapsed_from_clock_adds_real_elapsed_time(self) -> None:
        controller = _make_controller()
        with patch(f"{_MODULE}.monotonic", return_value=130.0):
            controller._plan_started_monotonic = 100.0
            controller._plan_resume_elapsed_s = 5.0
            result = ExperimentControlWindow._capture_plan_elapsed_from_clock(controller)
        self.assertEqual(result, 35.0)
        self.assertEqual(controller._plan_elapsed_s, 35.0)
        self.assertEqual(controller._plan_resume_elapsed_s, 35.0)

    def test_reset_plan_runtime_counters_zeroes_all_four(self) -> None:
        controller = _make_controller()
        controller._plan_elapsed_s = 1.0
        controller._plan_resume_elapsed_s = 2.0
        controller._plan_runtime_s = 3.0
        controller._plan_resume_runtime_s = 4.0
        ExperimentControlWindow._reset_plan_runtime_counters(controller)
        self.assertEqual(
            (controller._plan_elapsed_s, controller._plan_resume_elapsed_s, controller._plan_runtime_s, controller._plan_resume_runtime_s),
            (0.0, 0.0, 0.0, 0.0),
        )

    def test_ensure_measurement_started_only_stamps_once(self) -> None:
        controller = _make_controller()
        with patch(f"{_MODULE}.monotonic", side_effect=[50.0, 999.0]):
            ExperimentControlWindow._ensure_measurement_started(controller)
            first = controller._measurement_started_monotonic
            ExperimentControlWindow._ensure_measurement_started(controller)
        self.assertEqual(first, 50.0)
        self.assertEqual(controller._measurement_started_monotonic, 50.0)


class BeginExperimentPlanRunTests(unittest.TestCase):
    def test_begin_run_sets_flags_active_row_and_applies_first_step(self) -> None:
        steps = [_make_step(1), _make_step(2)]
        controller = _make_controller(steps)
        with patch(f"{_MODULE}.monotonic", return_value=1000.0):
            ExperimentControlWindow._begin_experiment_plan_run(controller, 0, steps)

        self.assertTrue(controller._plan_running)
        self.assertFalse(controller._plan_holding)
        self.assertFalse(controller._plan_paused)
        self.assertEqual(controller._plan_active_row, 0)
        self.assertEqual(controller._plan_started_monotonic, 1000.0)
        self.assertEqual(controller._step_started_monotonic, 1000.0)
        # _activate_experiment_control_step_for_elapsed(0.0, force=True) must
        # have dispatched the first step to hardware with start=True.
        self.assertEqual(len(controller._calls["apply_step"]), 1)
        applied_step, start, _on_success = controller._calls["apply_step"][0]
        self.assertIs(applied_step, steps[0])
        self.assertTrue(start)
        self.assertEqual(controller._calls["emit_state"][-1][0], "plan_started")
        self.assertGreaterEqual(controller._calls["toggle_button"], 1)

    def test_begin_run_resets_runtime_counters(self) -> None:
        steps = [_make_step(1)]
        controller = _make_controller(steps)
        controller._plan_elapsed_s = 99.0
        controller._plan_runtime_s = 99.0
        with patch(f"{_MODULE}.monotonic", return_value=0.0):
            ExperimentControlWindow._begin_experiment_plan_run(controller, 0, steps)
        self.assertEqual(controller._plan_elapsed_s, 0.0)
        self.assertEqual(controller._plan_runtime_s, 0.0)


class EnterHoldStateTests(unittest.TestCase):
    def test_hold_from_idle_is_a_no_op(self) -> None:
        controller = _make_controller(running=False, holding=False, paused=False)
        ExperimentControlWindow._enter_hold_state(controller)
        self.assertFalse(controller._plan_holding)
        self.assertEqual(controller._calls["emit_state"], [])

    def test_hold_from_running_freezes_time_and_does_not_touch_hardware(self) -> None:
        steps = [_make_step(1, duration_s=60.0)]
        controller = _make_controller(steps, running=True, active_row=0)
        controller._applied_plan_step = steps[0]
        with patch(f"{_MODULE}.monotonic", side_effect=[500.0, 500.0]):
            controller._plan_started_monotonic = 470.0
            controller._plan_resume_elapsed_s = 0.0
            ExperimentControlWindow._enter_hold_state(controller)

        self.assertFalse(controller._plan_running)
        self.assertTrue(controller._plan_holding)
        self.assertFalse(controller._plan_paused)
        self.assertIsNone(controller._plan_started_monotonic)
        self.assertEqual(controller._plan_elapsed_s, 30.0)  # captured from clock, frozen
        # Hold must NOT send any hardware command (it does not stop flow).
        self.assertEqual(controller._calls["apply_step"], [])
        self.assertEqual(controller._calls["emit_state"][-1], ("plan_hold", steps[0], ""))

    def test_hold_from_holding_is_a_no_op_guarded_by_running_only(self) -> None:
        # _enter_hold_state only proceeds if _plan_running is True - already
        # holding must not re-enter and re-freeze the clock.
        controller = _make_controller(running=False, holding=True)
        ExperimentControlWindow._enter_hold_state(controller)
        self.assertEqual(controller._calls["emit_state"], [])


class EnterPauseStateTests(unittest.TestCase):
    def test_pause_from_idle_is_a_no_op(self) -> None:
        controller = _make_controller(running=False, holding=False, paused=False)
        ExperimentControlWindow._enter_pause_state(controller)
        self.assertFalse(controller._plan_paused)
        self.assertEqual(controller._calls["emit_state"], [])

    def test_pause_from_running_applies_the_pause_template_with_start_false(self) -> None:
        steps = [_make_step(1, duration_s=60.0)]
        controller = _make_controller(steps, running=True, active_row=0)
        controller._applied_plan_step = steps[0]
        with patch(f"{_MODULE}.monotonic", return_value=1000.0):
            controller._plan_started_monotonic = 970.0
            ExperimentControlWindow._enter_pause_state(controller)

        self.assertFalse(controller._plan_running)
        self.assertTrue(controller._plan_paused)
        self.assertEqual(controller._paused_plan_step.step, steps[0].step)
        self.assertIsNot(controller._paused_plan_step, steps[0])  # must be a deep copy
        self.assertEqual(len(controller._calls["apply_step"]), 1)
        applied_step, start, _ = controller._calls["apply_step"][0]
        self.assertEqual(applied_step.step, 0)  # the pause template, not the plan step
        self.assertFalse(start)
        self.assertEqual(controller._calls["emit_state"][-1][0], "plan_pause")

    def test_pause_from_holding_does_not_double_capture_clock(self) -> None:
        # _capture_plan_elapsed_from_clock is only called when _plan_running
        # is True; from holding, _plan_started_monotonic is already None so
        # calling it again would just re-read the already-frozen value - but
        # the guard exists explicitly in the source, so pin the observable
        # outcome: elapsed stays exactly what it was when hold captured it.
        controller = _make_controller(running=False, holding=True, active_row=0)
        controller._plan_elapsed_s = 42.0
        controller._plan_started_monotonic = None
        ExperimentControlWindow._enter_pause_state(controller)
        self.assertEqual(controller._plan_elapsed_s, 42.0)
        self.assertTrue(controller._plan_paused)

    def test_pause_with_explicit_restore_step_overrides_applied_step(self) -> None:
        steps = [_make_step(1)]
        controller = _make_controller(steps, running=True, active_row=0)
        controller._applied_plan_step = steps[0]
        restore = _make_step(9, description="explicit restore")
        with patch(f"{_MODULE}.monotonic", return_value=1.0):
            controller._plan_started_monotonic = 0.0
            ExperimentControlWindow._enter_pause_state(controller, restore_step=restore)
        self.assertEqual(controller._paused_plan_step.step, 9)


class StopExperimentPlanTests(unittest.TestCase):
    def test_stop_while_running_sends_stop_all_channels_when_pump_connected(self) -> None:
        steps = [_make_step(1, duration_s=60.0)]
        controller = _make_controller(steps, running=True, active_row=0, pump_connected=True)
        with patch(f"{_MODULE}.monotonic", return_value=100.0):
            controller._plan_started_monotonic = 90.0
            ExperimentControlWindow._stop_experiment_plan(controller, steps[0])

        self.assertFalse(controller._plan_running)
        self.assertFalse(controller._plan_holding)
        self.assertFalse(controller._plan_paused)
        self.assertIsNone(controller._plan_started_monotonic)
        self.assertIsNone(controller._applied_plan_step)
        self.assertIsNone(controller._paused_plan_step)
        self.assertEqual(controller._plan_timer.stop_calls, 1)
        self.assertEqual(controller._calls["stop_all_channels"], 1)
        self.assertEqual(controller._calls["emit_state"][-1][0], "plan_stopped")

    def test_stop_while_pump_disconnected_sets_a_status_message_instead(self) -> None:
        steps = [_make_step(1)]
        controller = _make_controller(steps, running=True, active_row=0, pump_connected=False)
        with patch(f"{_MODULE}.monotonic", return_value=10.0):
            controller._plan_started_monotonic = 5.0
            ExperimentControlWindow._stop_experiment_plan(controller, steps[0])
        self.assertEqual(controller._calls["stop_all_channels"], 0)
        self.assertIn("Experiment plan stopped.", controller._calls["status_messages"])

    def test_stop_while_already_idle_resets_elapsed_to_zero(self) -> None:
        steps = [_make_step(1)]
        controller = _make_controller(steps, running=False, holding=False, paused=False, active_row=0)
        controller._plan_elapsed_s = 77.0
        ExperimentControlWindow._stop_experiment_plan(controller, None)
        self.assertEqual(controller._plan_elapsed_s, 0.0)

    def test_stop_while_running_does_not_reset_elapsed_before_capturing_it(self) -> None:
        # The running/holding/paused check that guards the elapsed-reset-to-
        # zero happens BEFORE _set_plan_runtime_flags clears the flags -
        # stopping mid-run must not discard the just-captured elapsed value.
        steps = [_make_step(1, duration_s=60.0)]
        controller = _make_controller(steps, running=True, active_row=0)
        with patch(f"{_MODULE}.monotonic", return_value=40.0):
            controller._plan_started_monotonic = 25.0
            controller._plan_resume_elapsed_s = 0.0
            ExperimentControlWindow._stop_experiment_plan(controller, steps[0])
        self.assertEqual(controller._plan_elapsed_s, 15.0)


class AdvanceExperimentControlProgressTests(unittest.TestCase):
    """The auto-advance timer callback - the single piece of code that
    actually moves the plan forward on its own, unattended, on real
    hardware."""

    def test_idle_tick_is_a_no_op(self) -> None:
        controller = _make_controller([_make_step(1)], running=False, holding=False, paused=False)
        ExperimentControlWindow._advance_experiment_control_progress(controller)
        self.assertEqual(controller._calls["apply_step"], [])
        self.assertEqual(controller._calls["sync_timeline"], [])

    def test_holding_tick_resyncs_timeline_without_advancing(self) -> None:
        steps = [_make_step(1)]
        controller = _make_controller(steps, holding=True, active_row=0)
        ExperimentControlWindow._advance_experiment_control_progress(controller)
        self.assertEqual(controller._calls["apply_step"], [])
        self.assertEqual(len(controller._calls["sync_timeline"]), 1)

    def test_paused_tick_with_no_steps_does_not_even_resync(self) -> None:
        controller = _make_controller([], paused=True, active_row=0)
        ExperimentControlWindow._advance_experiment_control_progress(controller)
        self.assertEqual(controller._calls["sync_timeline"], [])

    def test_running_tick_with_step_apply_in_flight_retries_without_advancing(self) -> None:
        steps = [_make_step(1, duration_s=10.0)]
        controller = _make_controller(steps, running=True, active_row=0)
        controller._step_apply_inflight = 1  # _step_apply_pending -> True
        with patch(f"{_MODULE}.monotonic", return_value=100.0):
            controller._plan_started_monotonic = 0.0  # way past duration if it were checked
            ExperimentControlWindow._advance_experiment_control_progress(controller)
        self.assertEqual(controller._calls["apply_step"], [])
        self.assertIn(50, controller._plan_timer.start_calls)

    def test_running_tick_before_step_duration_just_updates_elapsed(self) -> None:
        steps = [_make_step(1, duration_s=30.0)]
        controller = _make_controller(steps, running=True, active_row=0)
        with patch(f"{_MODULE}.monotonic", return_value=10.0):
            controller._plan_started_monotonic = 0.0
            controller._plan_resume_elapsed_s = 0.0
            ExperimentControlWindow._advance_experiment_control_progress(controller)
        self.assertEqual(controller._plan_elapsed_s, 10.0)
        self.assertEqual(controller._calls["apply_step"], [])
        self.assertEqual(len(controller._calls["sync_timeline"]), 1)

    def test_running_tick_past_step_duration_advances_to_next_step(self) -> None:
        steps = [_make_step(1, duration_s=30.0), _make_step(2, duration_s=30.0)]
        controller = _make_controller(steps, running=True, active_row=0)
        with patch(f"{_MODULE}.monotonic", return_value=31.0):
            controller._plan_started_monotonic = 0.0
            controller._plan_resume_elapsed_s = 0.0
            ExperimentControlWindow._advance_experiment_control_progress(controller)
        self.assertEqual(controller._plan_active_row, 1)
        self.assertEqual(controller._plan_elapsed_s, 0.0)
        self.assertEqual(controller._plan_started_monotonic, 31.0)
        self.assertEqual(len(controller._calls["apply_step"]), 1)
        applied_step, start, _ = controller._calls["apply_step"][0]
        self.assertIs(applied_step, steps[1])
        self.assertTrue(start)

    def test_running_tick_past_last_step_stops_and_reports_finished(self) -> None:
        steps = [_make_step(1, duration_s=30.0)]
        controller = _make_controller(steps, running=True, active_row=0)
        stop_calls: list[int] = []

        def _stop() -> None:
            stop_calls.append(1)
            controller._plan_running = False

        controller._stop_experiment_control = _stop
        with patch(f"{_MODULE}.monotonic", return_value=31.0):
            controller._plan_started_monotonic = 0.0
            controller._plan_resume_elapsed_s = 0.0
            ExperimentControlWindow._advance_experiment_control_progress(controller)

        self.assertEqual(stop_calls, [1])
        self.assertIn("Experiment plan finished.", controller._calls["status_messages"])
        # Must not have applied a next step - there isn't one.
        self.assertEqual(controller._calls["apply_step"], [])

    def test_running_tick_with_no_steps_stops_the_plan(self) -> None:
        controller = _make_controller([], running=True, active_row=0)
        stop_calls: list[int] = []
        controller._stop_experiment_control = lambda: stop_calls.append(1)
        with patch(f"{_MODULE}.monotonic", return_value=1.0):
            controller._plan_started_monotonic = 0.0
            ExperimentControlWindow._advance_experiment_control_progress(controller)
        self.assertEqual(stop_calls, [1])


class SchedulePlanTimerTests(unittest.TestCase):
    def test_does_not_restart_an_already_active_timer(self) -> None:
        controller = _make_controller([_make_step(1)], running=True, active_row=0)
        controller._plan_timer._active = True
        ExperimentControlWindow._schedule_plan_timer(controller)
        self.assertEqual(controller._plan_timer.start_calls, [])

    def test_idle_does_not_start_the_timer(self) -> None:
        controller = _make_controller([], running=False, holding=False, paused=False)
        ExperimentControlWindow._schedule_plan_timer(controller)
        self.assertEqual(controller._plan_timer.start_calls, [])

    def test_holding_uses_the_150ms_polling_cadence(self) -> None:
        controller = _make_controller([_make_step(1)], holding=True, active_row=0)
        ExperimentControlWindow._schedule_plan_timer(controller)
        self.assertEqual(controller._plan_timer.start_calls, [150])

    def test_paused_uses_the_150ms_polling_cadence(self) -> None:
        controller = _make_controller([_make_step(1)], paused=True, active_row=0)
        ExperimentControlWindow._schedule_plan_timer(controller)
        self.assertEqual(controller._plan_timer.start_calls, [150])

    def test_running_with_no_started_monotonic_falls_back_to_150ms(self) -> None:
        controller = _make_controller([_make_step(1)], running=True, active_row=0)
        controller._plan_started_monotonic = None
        ExperimentControlWindow._schedule_plan_timer(controller)
        self.assertEqual(controller._plan_timer.start_calls, [150])

    def test_running_with_invalid_active_row_falls_back_to_150ms(self) -> None:
        controller = _make_controller([_make_step(1)], running=True, active_row=None)
        controller._plan_started_monotonic = 0.0
        ExperimentControlWindow._schedule_plan_timer(controller)
        self.assertEqual(controller._plan_timer.start_calls, [150])

    def test_running_schedules_the_remaining_time_in_the_current_step(self) -> None:
        steps = [_make_step(1, duration_s=30.0)]
        controller = _make_controller(steps, running=True, active_row=0)
        with patch(f"{_MODULE}.monotonic", return_value=20.0):
            controller._plan_started_monotonic = 10.0  # 10s elapsed, 20s remain -> clamped to 150ms max
            controller._plan_resume_elapsed_s = 0.0
            ExperimentControlWindow._schedule_plan_timer(controller)
        self.assertEqual(controller._plan_timer.start_calls, [150])

    def test_running_clamps_to_1ms_minimum_when_step_already_overdue(self) -> None:
        steps = [_make_step(1, duration_s=10.0)]
        controller = _make_controller(steps, running=True, active_row=0)
        with patch(f"{_MODULE}.monotonic", return_value=999.0):
            controller._plan_started_monotonic = 0.0
            controller._plan_resume_elapsed_s = 0.0
            ExperimentControlWindow._schedule_plan_timer(controller)
        self.assertEqual(controller._plan_timer.start_calls, [1])

    def test_running_fires_close_to_step_end_when_almost_done(self) -> None:
        steps = [_make_step(1, duration_s=30.0)]
        controller = _make_controller(steps, running=True, active_row=0)
        with patch(f"{_MODULE}.monotonic", return_value=29.95):
            controller._plan_started_monotonic = 0.0
            controller._plan_resume_elapsed_s = 0.0
            ExperimentControlWindow._schedule_plan_timer(controller)
        self.assertEqual(controller._plan_timer.start_calls, [50])


class JumpToExperimentControlStepTests(unittest.TestCase):
    def test_jump_while_idle_only_selects_the_row(self) -> None:
        steps = [_make_step(1), _make_step(2)]
        controller = _make_controller(steps, running=False, holding=False, paused=False)
        ExperimentControlWindow._jump_to_experiment_control_step(controller, 1)
        self.assertEqual(controller._calls["select_row"], [1])
        self.assertEqual(controller._calls["apply_step"], [])
        self.assertEqual(controller._calls["load_selected_step"], 1)

    def test_jump_while_running_resets_the_clock_and_applies_the_new_step(self) -> None:
        steps = [_make_step(1, duration_s=30.0), _make_step(2, duration_s=30.0)]
        controller = _make_controller(steps, running=True, active_row=0)
        controller._plan_elapsed_s = 20.0
        with patch(f"{_MODULE}.monotonic", return_value=500.0):
            ExperimentControlWindow._jump_to_experiment_control_step(controller, 1)
        self.assertEqual(controller._plan_active_row, 1)
        self.assertEqual(controller._plan_elapsed_s, 0.0)
        self.assertEqual(controller._plan_started_monotonic, 500.0)
        self.assertEqual(len(controller._calls["apply_step"]), 1)
        applied_step, start, _ = controller._calls["apply_step"][0]
        self.assertIs(applied_step, steps[1])
        self.assertTrue(start)
        # Plan stays running - a manual jump is not a resume-from-pause.
        self.assertTrue(controller._plan_running)

    def test_jump_while_paused_resumes_the_plan_at_the_new_row(self) -> None:
        steps = [_make_step(1), _make_step(2)]
        controller = _make_controller(steps, paused=True, active_row=0)
        with patch(f"{_MODULE}.monotonic", return_value=0.0):
            ExperimentControlWindow._jump_to_experiment_control_step(controller, 1)
        self.assertTrue(controller._plan_running)
        self.assertFalse(controller._plan_paused)
        self.assertEqual(controller._calls["emit_state"][-1][0], "plan_resume")

    def test_jump_out_of_range_is_ignored(self) -> None:
        steps = [_make_step(1)]
        controller = _make_controller(steps, running=False)
        ExperimentControlWindow._jump_to_experiment_control_step(controller, 5)
        self.assertEqual(controller._calls["select_row"], [])


class ApplySelectedExperimentControlStepTests(unittest.TestCase):
    def test_apply_while_idle_delegates_to_jump(self) -> None:
        steps = [_make_step(1), _make_step(2)]
        controller = _make_controller(steps, running=False, holding=False, paused=False)
        jump_calls: list[int] = []
        controller._jump_to_experiment_control_step = lambda row: jump_calls.append(row)
        ExperimentControlWindow._apply_selected_experiment_control_step(controller, 1)
        self.assertEqual(jump_calls, [1])

    def test_apply_while_running_applies_in_place_without_resetting_the_clock(self) -> None:
        steps = [_make_step(1), _make_step(2)]
        controller = _make_controller(steps, running=True, active_row=0)
        ExperimentControlWindow._apply_selected_experiment_control_step(controller, 1)
        self.assertEqual(len(controller._calls["apply_step"]), 1)
        applied_step, start, _ = controller._calls["apply_step"][0]
        self.assertIs(applied_step, steps[1])
        self.assertTrue(start)
        self.assertEqual(controller._calls["emit_state"][-1][0], "step_apply")

    def test_apply_while_holding_resumes_the_plan(self) -> None:
        steps = [_make_step(1), _make_step(2)]
        controller = _make_controller(steps, holding=True, active_row=0)
        with patch(f"{_MODULE}.monotonic", return_value=0.0):
            ExperimentControlWindow._apply_selected_experiment_control_step(controller, 1)
        self.assertTrue(controller._plan_running)
        self.assertFalse(controller._plan_holding)


class StartOrResumeExperimentControlTests(unittest.TestCase):
    def test_empty_plan_sets_a_status_message_and_does_nothing_else(self) -> None:
        controller = _make_controller([])
        ExperimentControlWindow._start_or_resume_experiment_control(controller)
        self.assertIn("Experiment plan is empty.", controller._calls["status_messages"])
        self.assertEqual(controller._calls["apply_step"], [])

    def test_already_running_is_a_no_op(self) -> None:
        steps = [_make_step(1)]
        controller = _make_controller(steps, running=True, active_row=0)
        ExperimentControlWindow._start_or_resume_experiment_control(controller)
        self.assertEqual(controller._calls["apply_step"], [])

    def test_holding_resumes_via_resume_experiment_plan(self) -> None:
        steps = [_make_step(1)]
        controller = _make_controller(steps, holding=True, active_row=0)
        with patch(f"{_MODULE}.monotonic", return_value=0.0):
            ExperimentControlWindow._start_or_resume_experiment_control(controller)
        self.assertTrue(controller._plan_running)
        self.assertFalse(controller._plan_holding)

    def test_paused_resumes_and_restores_the_paused_step(self) -> None:
        steps = [_make_step(1)]
        controller = _make_controller(steps, paused=True, active_row=0)
        restore = _make_step(9, description="paused snapshot")
        controller._paused_plan_step = restore
        with patch(f"{_MODULE}.monotonic", return_value=0.0):
            ExperimentControlWindow._start_or_resume_experiment_control(controller)
        self.assertTrue(controller._plan_running)
        self.assertIsNone(controller._paused_plan_step)
        self.assertEqual(len(controller._calls["apply_step"]), 1)
        applied_step, start, _ = controller._calls["apply_step"][0]
        self.assertEqual(applied_step.step, 9)
        self.assertTrue(start)

    def test_idle_start_begins_the_plan_from_the_selected_row(self) -> None:
        steps = [_make_step(1), _make_step(2)]
        controller = _make_controller(steps, running=False, holding=False, paused=False, selected_row=1)
        with patch(f"{_MODULE}.monotonic", return_value=0.0):
            ExperimentControlWindow._start_or_resume_experiment_control(controller)
        self.assertTrue(controller._plan_running)
        self.assertEqual(controller._plan_active_row, 1)

    def test_idle_start_with_no_selection_defaults_to_row_zero(self) -> None:
        steps = [_make_step(1), _make_step(2)]
        controller = _make_controller(steps, running=False, holding=False, paused=False, selected_row=None)
        with patch(f"{_MODULE}.monotonic", return_value=0.0):
            ExperimentControlWindow._start_or_resume_experiment_control(controller)
        self.assertEqual(controller._calls["select_row"], [0])
        self.assertEqual(controller._plan_active_row, 0)

    def test_recording_not_started_cancels_the_start(self) -> None:
        steps = [_make_step(1)]
        controller = _make_controller(steps, running=False, holding=False, paused=False, selected_row=0)
        controller._request_recording_control = lambda action: False
        ExperimentControlWindow._start_or_resume_experiment_control(controller)
        self.assertFalse(controller._plan_running)
        self.assertIn(
            "Experiment plan start cancelled because recording was not started.",
            controller._calls["status_messages"],
        )


class RunHoldPauseStopEntryPointsTests(unittest.TestCase):
    """The thin public methods GUI buttons/shortcuts actually call - confirm
    each one delegates to the exact state transition it should."""

    def test_run_delegates_to_start_or_resume(self) -> None:
        steps = [_make_step(1)]
        controller = _make_controller(steps, running=False, holding=False, paused=False, selected_row=0)
        with patch(f"{_MODULE}.monotonic", return_value=0.0):
            ExperimentControlWindow._run_experiment_control(controller)
        self.assertTrue(controller._plan_running)

    def test_hold_delegates_to_enter_hold_state(self) -> None:
        steps = [_make_step(1)]
        controller = _make_controller(steps, running=True, active_row=0)
        with patch(f"{_MODULE}.monotonic", return_value=0.0):
            controller._plan_started_monotonic = 0.0
            ExperimentControlWindow._hold_experiment_control(controller)
        self.assertTrue(controller._plan_holding)

    def test_pause_delegates_to_enter_pause_state(self) -> None:
        steps = [_make_step(1)]
        controller = _make_controller(steps, running=True, active_row=0)
        with patch(f"{_MODULE}.monotonic", return_value=0.0):
            controller._plan_started_monotonic = 0.0
            ExperimentControlWindow._pause_experiment_control(controller)
        self.assertTrue(controller._plan_paused)

    def test_stop_delegates_to_stop_experiment_plan_with_the_applied_step(self) -> None:
        steps = [_make_step(1)]
        controller = _make_controller(steps, running=True, active_row=0)
        controller._applied_plan_step = steps[0]
        with patch(f"{_MODULE}.monotonic", return_value=0.0):
            controller._plan_started_monotonic = 0.0
            ExperimentControlWindow._stop_experiment_control(controller)
        self.assertFalse(controller._plan_running)
        self.assertEqual(controller._calls["emit_state"][-1][1], steps[0])


if __name__ == "__main__":
    unittest.main()
