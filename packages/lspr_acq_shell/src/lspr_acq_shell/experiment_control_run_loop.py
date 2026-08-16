"""Shared run/hold/pause/stop state machine for the pump/valve/selector
experiment-control panel.

Extracted from singleLSPR Acquisition's `gui/experiment_control_window.py`
(Phase 2, LSPRi acq experiment-control reuse - Tier 2, second half,
2026-08-09), verbatim: every method body below is an unmodified copy of the
original (same `self.*` reads/writes), moved into `PlanRunLoopMixin` rather
than rewritten against an explicit host object. See the design rationale
below for why a mixin, not composition, was chosen after the state was
traced.

Real coupling found before moving anything (same discipline as every prior
tier): the run/hold/pause/stop loop's guard flags
(`_plan_running`/`_plan_holding`/`_plan_paused`/`_plan_active_row`/etc.) are
read at 250+ sites across the 6,165-line window file - almost entirely
editing-lock guard conditions elsewhere in the file, not the state machine
itself. A composition design (a separate `PlanRunController` object owning
this state, with the window's `_plan_running` etc. becoming properties that
delegate to it) would have required either touching every one of those 250+
sites or adding property indirection with a fallback for bare-`__new__`-
constructed test doubles (an anti-pattern - see CLAUDE.md's guidance against
backwards-compatibility shims). It would also have broken every existing
test in this area, including the 53-test characterization suite written
specifically as this extraction's safety net
(`tests/unit/test_experiment_control_run_loop_characterization.py`) and the
pre-existing `test_experiment_control_step_navigation.py`, both of which
construct `ExperimentControlWindow.__new__(...)` and set state directly as
plain attributes.

A mixin avoids all of that: `_plan_running` and friends stay exactly what
they always were - plain instance attributes on the window object itself,
initialized in `ExperimentControlWindow.__init__` exactly as before (no
changes there at all). Only the *method definitions* move to this shared
base class; `ExperimentControlWindow(PlanRunLoopMixin, QWidget)` picks them
up via normal Python attribute resolution. Every external read site, and
every existing test that does `ExperimentControlWindow.__new__(...)` +
`ExperimentControlWindow._some_method(instance, ...)`, keeps working
completely unchanged - Python resolves `_some_method` through the MRO
whether it is defined directly on `ExperimentControlWindow` or inherited
from this mixin.

For LSPRi acq's own experiment-control window to reuse this class, it must
provide the following methods/attributes under these exact names (duck-
typed, not a formal `Protocol` - this project doesn't use one for GUI
wiring elsewhere, and the contract is small enough that a paragraph here is
clearer than a class with no behavior of its own):

- ``_read_experiment_control_steps() -> list[PumpPlanStep]``
- ``_selected_experiment_control_row() -> int | None``
- ``_select_experiment_control_plan_row(row: int | None) -> None``
- ``_apply_step_to_pump_async(step, *, start: bool, on_success=None) -> None``
- ``_step_apply_pending`` (property, ``bool``)
- ``_sync_experiment_control_timeline(steps, plan_row, *, refresh_status=False) -> None``
- ``_update_experiment_control_toggle_button() -> None``
- ``_set_status_message(msg: str) -> None``
- ``_emit_experimental_control_state(event, step=None, *, status="") -> None``
- ``_service_device_connected(device_key: str) -> bool``
- ``_stop_all_channels() -> None``
- ``_pause_row_step() -> PumpPlanStep``
- ``_request_recording_control(action: str) -> bool``
- ``_load_selected_step_into_editor() -> None``
- ``_update_timeline_selection() -> None``
- ``_run_gui_callback_timed(label: str, callback: Callable[[], None]) -> None``
- ``record_with_flow_button`` (a ``QAbstractButton``-like object with ``isChecked()``)
- ``recording_controller`` (optional attribute; if present, may have ``_measurement_active: bool``)
- ``_plan_timer`` (a ``QTimer``-like object with ``start(ms)``/``stop()``/``isActive()``)

Plus the plain instance attributes this class reads/writes directly:
``_plan_running``, ``_plan_holding``, ``_plan_paused``, ``_plan_active_row``,
``_plan_elapsed_s``, ``_plan_resume_elapsed_s``, ``_plan_runtime_s``,
``_plan_resume_runtime_s``, ``_plan_started_monotonic``,
``_step_started_monotonic``, ``_measurement_started_monotonic``,
``_applied_plan_step``, ``_paused_plan_step``,
``_pending_experiment_control_start_after_recording``.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from time import monotonic

from lspr_acq_shell.experiment_control_runtime import ExperimentRuntimeSnapshot, experiment_runtime_snapshot
from lspr_acq_shell.pump_plan import PumpPlanStep

_LOGGER = logging.getLogger("lspr_app.experiment_control")


class PlanRunLoopMixin:
    """Run/hold/pause/stop state machine for a pump-plan experiment-control window.

    See this module's docstring for the mixin design rationale and the full
    host-method contract a concrete window class must provide.
    """

    # ═══════════════════════════════════════════════════════════════════
    # Runtime clock/flag primitives
    # ═══════════════════════════════════════════════════════════════════

    def _set_plan_runtime_flags(self, *, running: bool, holding: bool, paused: bool) -> None:
        self._plan_running = bool(running)
        self._plan_holding = bool(holding)
        self._plan_paused = bool(paused)

    def _capture_plan_elapsed_from_clock(self) -> float:
        if self._plan_started_monotonic is None:
            return max(float(self._plan_elapsed_s), 0.0)
        elapsed = self._plan_resume_elapsed_s + max(monotonic() - self._plan_started_monotonic, 0.0)
        self._plan_elapsed_s = elapsed
        self._plan_resume_elapsed_s = elapsed
        return elapsed

    def _reset_plan_runtime_counters(self) -> None:
        self._plan_elapsed_s = 0.0
        self._plan_resume_elapsed_s = 0.0
        self._plan_runtime_s = 0.0
        self._plan_resume_runtime_s = 0.0

    def _ensure_measurement_started(self) -> None:
        if self._measurement_started_monotonic is None:
            self._measurement_started_monotonic = monotonic()

    def _experiment_runtime_snapshot(self) -> ExperimentRuntimeSnapshot:
        return experiment_runtime_snapshot(
            running=self._plan_running,
            holding=self._plan_holding,
            paused=self._plan_paused,
            recording=bool(self.__dict__.get("_measurement_started_monotonic") is not None),
            has_steps=bool(self._read_experiment_control_steps()),
        )

    def _timeline_progress_for_display(self) -> float | None:
        if self._plan_running or self._plan_holding or self._plan_paused:
            row = self._plan_active_row if self._plan_active_row is not None else self._selected_experiment_control_row()
            steps = self._read_experiment_control_steps()
            if row is not None and 0 <= row < len(steps):
                return max(float(steps[row].start_s) + max(float(self._plan_elapsed_s), 0.0), 0.0)
            return max(float(self._plan_elapsed_s), 0.0)
        return None

    def _plan_runtime_for_display(self) -> float:
        if self._measurement_started_monotonic is not None:
            return max(monotonic() - self._measurement_started_monotonic, 0.0)
        return max(float(self._plan_runtime_s), 0.0)

    def _step_runtime_for_display(self) -> float:
        if self._step_started_monotonic is not None:
            return max(monotonic() - self._step_started_monotonic, 0.0)
        return max(float(self._plan_resume_runtime_s), 0.0)

    def _apply_pause_state(self) -> None:
        step = self._pause_row_step()
        if step is None:
            return
        # Dispatched async - _apply_step_to_pump_async already catches and
        # logs internally, so no try/except is needed here (unlike the old
        # synchronous call this replaces).
        self._apply_step_to_pump_async(step, start=False)

    # ═══════════════════════════════════════════════════════════════════
    # Step transitions (run/hold/pause internals)
    # ═══════════════════════════════════════════════════════════════════

    def _resume_experiment_plan(
        self,
        *,
        restore_step: PumpPlanStep | None = None,
        status_message: str,
        log_message: str,
        emit_event: str,
        emit_step: PumpPlanStep | None = None,
    ) -> None:
        if restore_step is not None:
            self._apply_step_to_pump_async(restore_step, start=True)
        self._set_plan_runtime_flags(running=True, holding=False, paused=False)
        self._plan_started_monotonic = monotonic()
        self._plan_timer.stop()
        self._schedule_plan_timer()
        self._update_experiment_control_toggle_button()
        self._set_status_message(status_message)
        _LOGGER.info(log_message)
        steps = self._read_experiment_control_steps()
        if self._plan_active_row is not None and 0 <= self._plan_active_row < len(steps):
            self._emit_experimental_control_state(emit_event, emit_step or steps[self._plan_active_row])

    def _begin_experiment_plan_run(self, row: int, steps: list[PumpPlanStep]) -> None:
        self._reset_plan_runtime_counters()
        self._ensure_measurement_started()
        self._step_started_monotonic = monotonic()
        self._set_plan_runtime_flags(running=True, holding=False, paused=False)
        self._plan_active_row = row
        self._plan_started_monotonic = monotonic()
        self._update_experiment_control_toggle_button()
        self._activate_experiment_control_step_for_elapsed(0.0, force=True)
        self._schedule_plan_timer()
        self._set_status_message(f"Running experiment plan from step {self._plan_active_row + 1 if self._plan_active_row is not None else 1}.")
        _LOGGER.info("Experiment plan started | step=%s", self._plan_active_row + 1 if self._plan_active_row is not None else 1)
        if self._plan_active_row is not None and 0 <= self._plan_active_row < len(steps):
            self._emit_experimental_control_state("plan_started", steps[self._plan_active_row])

    def _begin_paused_experiment_plan_run(self, row: int, steps: list[PumpPlanStep]) -> None:
        self._paused_plan_step = deepcopy(steps[row])
        self._plan_active_row = row
        self._plan_timer.stop()
        self._reset_plan_runtime_counters()
        self._ensure_measurement_started()
        self._set_plan_runtime_flags(running=False, holding=False, paused=True)
        self._plan_started_monotonic = None
        self._step_started_monotonic = None
        self._schedule_plan_timer()
        self._apply_pause_state()
        self._update_experiment_control_toggle_button()
        self._set_status_message(
            f"Experiment plan started in pause state on step {self._plan_active_row + 1 if self._plan_active_row is not None else 1}."
        )
        _LOGGER.info(
            "Experiment plan started in pause state | step=%s",
            self._plan_active_row + 1 if self._plan_active_row is not None else 1,
        )
        if self._plan_active_row is not None and 0 <= self._plan_active_row < len(steps):
            self._emit_experimental_control_state("plan_pause", self._applied_plan_step, status="started in pause state")

    def _resume_experiment_control_after_manual_step_change(
        self,
        row: int,
        *,
        status_message: str,
        log_message: str,
        emit_event: str,
    ) -> None:
        steps = self._read_experiment_control_steps()
        if row < 0 or row >= len(steps):
            return
        # Dispatched async and not gated on the result: plan-runtime state
        # always advances here, exactly like every other step-apply trigger.
        # A hardware failure is surfaced via the status bar text a moment
        # later (and durably logged to the session HDF5 file regardless of
        # active recording - see _handle_experimental_control_state_recorded
        # in main_window.py) rather than silently blocking the resume.
        self._apply_step_to_pump_async(steps[row], start=True)
        self._paused_plan_step = None
        self._reset_plan_runtime_counters()
        self._ensure_measurement_started()
        self._set_plan_runtime_flags(running=True, holding=False, paused=False)
        self._plan_active_row = row
        self._plan_elapsed_s = 0.0
        self._plan_resume_elapsed_s = 0.0
        self._plan_started_monotonic = monotonic()
        self._step_started_monotonic = monotonic()
        self._plan_timer.stop()
        self._schedule_plan_timer()
        self._update_experiment_control_toggle_button()
        self._sync_experiment_control_timeline(steps, row, refresh_status=True)
        self._set_status_message(status_message)
        _LOGGER.info(log_message)
        self._emit_experimental_control_state(emit_event, steps[row])

    def _queue_experiment_control_start_after_recording(self, *, paused: bool, row: int | None) -> None:
        self._pending_experiment_control_start_after_recording = (bool(paused), row)

    def _run_pending_experiment_control_start_after_recording(self) -> None:
        pending = self._pending_experiment_control_start_after_recording
        if pending is None:
            return
        self._pending_experiment_control_start_after_recording = None
        paused, row = pending
        steps = self._read_experiment_control_steps()
        if not steps:
            self._set_status_message("Experiment plan is empty.")
            return
        if row is None or not (0 <= int(row) < len(steps)):
            row = self._selected_experiment_control_row()
            if row is None:
                row = 0
                self._select_experiment_control_plan_row(0)
        if paused:
            self._begin_paused_experiment_plan_run(int(row), steps)
        else:
            self._begin_experiment_plan_run(int(row), steps)

    def _enter_hold_state(self) -> None:
        if not self._plan_running:
            return
        # HOLD freezes plan time and cursor position, but does not stop recording.
        self._capture_plan_elapsed_from_clock()
        self._set_plan_runtime_flags(running=False, holding=True, paused=False)
        self._plan_started_monotonic = None
        self._plan_runtime_s = self._step_runtime_for_display()
        self._update_experiment_control_toggle_button()
        self._set_status_message("Experiment plan hold.")
        _LOGGER.info("Experiment plan hold.")
        self._emit_experimental_control_state("plan_hold", self._applied_plan_step)

    def _enter_pause_state(self, *, restore_step: PumpPlanStep | None = None) -> None:
        if not (self._plan_running or self._plan_holding):
            return
        if self._plan_running:
            self._capture_plan_elapsed_from_clock()
        self._paused_plan_step = deepcopy(self._applied_plan_step) if self._applied_plan_step is not None else None
        if restore_step is not None:
            self._paused_plan_step = deepcopy(restore_step)
        self._apply_pause_state()
        self._set_plan_runtime_flags(running=False, holding=False, paused=True)
        self._plan_started_monotonic = None
        self._plan_runtime_s = self._step_runtime_for_display()
        self._step_started_monotonic = None
        self._update_experiment_control_toggle_button()
        self._set_status_message("Experiment plan paused.")
        _LOGGER.info("Experiment plan paused.")
        self._emit_experimental_control_state("plan_pause", self._applied_plan_step)

    def _stop_experiment_plan(self, last_step: PumpPlanStep | None) -> None:
        steps = self._read_experiment_control_steps()
        target_row = self._plan_active_row
        if target_row is None:
            target_row = self._selected_experiment_control_row()
        if target_row is None and steps:
            target_row = 0
        if steps and target_row is not None:
            target_row = min(max(int(target_row), 0), len(steps) - 1)
            self._plan_active_row = target_row
            if not (self._plan_running or self._plan_holding or self._plan_paused):
                self._plan_elapsed_s = 0.0
            self._plan_resume_elapsed_s = self._plan_elapsed_s
            self._sync_experiment_control_timeline(steps, target_row)
        if self._plan_running:
            self._capture_plan_elapsed_from_clock()
        self._set_plan_runtime_flags(running=False, holding=False, paused=False)
        self._plan_started_monotonic = None
        self._step_started_monotonic = None
        self._measurement_started_monotonic = None
        self._plan_runtime_s = self._plan_runtime_for_display()
        self._plan_resume_runtime_s = self._step_runtime_for_display()
        self._applied_plan_step = None
        self._paused_plan_step = None
        self._plan_timer.stop()
        self._update_experiment_control_toggle_button()
        if self._service_device_connected("pump"):
            self._stop_all_channels()
        else:
            self._set_status_message("Experiment plan stopped.")
        _LOGGER.info("Experiment plan stopped.")
        self._emit_experimental_control_state("plan_stopped", last_step)

    # ═══════════════════════════════════════════════════════════════════
    # Manual row navigation (jump/apply/next/previous)
    # ═══════════════════════════════════════════════════════════════════

    def _set_experiment_control_runtime_row(
        self,
        row: int,
        *,
        event: str,
        status: str = "",
        apply_step: bool = False,
        refresh_status: bool = True,
    ) -> None:
        steps = self._read_experiment_control_steps()
        if row < 0 or row >= len(steps):
            return
        self._plan_active_row = row
        if apply_step:
            self._apply_step_to_pump_async(steps[row], start=True)
        self._sync_experiment_control_timeline(steps, row, refresh_status=refresh_status)
        self._emit_experimental_control_state(event, steps[row], status=status)

    def _jump_to_experiment_control_step(self, row: int) -> None:
        steps = self._read_experiment_control_steps()
        if row < 0 or row >= len(steps):
            return
        if self._plan_running or self._plan_holding or self._plan_paused:
            if self._plan_running:
                self._plan_active_row = row
                self._plan_elapsed_s = 0.0
                self._plan_resume_elapsed_s = 0.0
                self._plan_started_monotonic = monotonic()
                self._step_started_monotonic = monotonic()
                self._set_experiment_control_runtime_row(
                    row,
                    event="step_jump",
                    apply_step=True,
                )
                self._plan_runtime_s = self._step_runtime_for_display()
                self._plan_resume_runtime_s = self._plan_runtime_s
                return
            self._resume_experiment_control_after_manual_step_change(
                row,
                status_message=f"Running experiment plan from step {row + 1}.",
                log_message=f"Experiment plan resumed on step {row + 1} after manual step change.",
                emit_event="plan_resume",
            )
            return
        self._select_experiment_control_plan_row(row)
        self._load_selected_step_into_editor()
        self._update_timeline_selection()
        self._set_status_message(f"Selected experiment-plan step {row + 1}.")

    def _apply_selected_experiment_control_step(self, row: int) -> None:
        steps = self._read_experiment_control_steps()
        if row < 0 or row >= len(steps):
            return
        if self._plan_running:
            self._set_experiment_control_runtime_row(
                row,
                event="step_apply",
                apply_step=True,
            )
            return
        if self._plan_holding:
            self._resume_experiment_control_after_manual_step_change(
                row,
                status_message=f"Running experiment plan from step {row + 1}.",
                log_message=f"Experiment plan resumed on step {row + 1} after manual step apply.",
                emit_event="plan_resume",
            )
            return
        if self._plan_paused:
            self._resume_experiment_control_after_manual_step_change(
                row,
                status_message=f"Running experiment plan from step {row + 1}.",
                log_message=f"Experiment plan resumed on step {row + 1} after manual step apply.",
                emit_event="plan_resume",
            )
            return
        # Not running/holding/paused - only select the row. Applying the
        # step to hardware here would start devices moving with no way to
        # stop them: Stop is gated on _plan_running/_plan_holding/
        # _plan_paused, none of which double-clicking a step outside those
        # states ever sets, leaving the plan in an unstoppable "device is
        # moving but nothing is running" state.
        self._jump_to_experiment_control_step(row)

    def _move_to_relative_experiment_control_step(self, delta: int) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            return
        running = self._plan_running
        active = self._plan_running or self._plan_holding or self._plan_paused
        row = self._plan_active_row if active else self._selected_experiment_control_row()
        if row is None:
            row = 0
        raw_target = row + delta
        if active and raw_target >= len(steps):
            # Pressing Next past the last step finishes the plan, mirroring
            # what _advance_experiment_control_progress already does when
            # auto-advance reaches the end. Without this, target below just
            # clamps to the last step (same row), and the running branch's
            # elapsed-time reset would restart that same last step from 0
            # instead of finishing - "Next" on the last step should finish
            # the plan, not replay it.
            self._plan_elapsed_s = max(float(steps[-1].duration_s), 0.0)
            self._plan_resume_elapsed_s = self._plan_elapsed_s
            self._stop_experiment_control()
            self._set_status_message("Experiment plan finished.")
            _LOGGER.info("Experiment plan finished.")
            return
        target = min(max(raw_target, 0), len(steps) - 1)
        if active:
            if running:
                # Mirrors _jump_to_experiment_control_step's reset - without
                # it, the new step's elapsed/ETA tracking kept accumulating
                # from wherever the previous step left off instead of
                # restarting at 0 (elapsed = _plan_resume_elapsed_s + time
                # since _plan_started_monotonic, neither of which this used
                # to touch).
                self._plan_elapsed_s = 0.0
                self._plan_resume_elapsed_s = 0.0
                self._plan_started_monotonic = monotonic()
                self._set_experiment_control_runtime_row(
                    target,
                    event="step_jump",
                    apply_step=True,
                )
                self._step_started_monotonic = monotonic()
                self._plan_runtime_s = self._step_runtime_for_display()
                self._plan_resume_runtime_s = self._plan_runtime_s
            else:
                self._resume_experiment_control_after_manual_step_change(
                    target,
                    status_message=f"Running experiment plan from step {target + 1}.",
                    log_message=f"Experiment plan resumed on step {target + 1} after step navigation.",
                    emit_event="plan_resume",
                )
        else:
            self._jump_to_experiment_control_step(target)
        if not (self._plan_running or self._plan_holding or self._plan_paused):
            self._set_status_message(f"Selected experiment-plan step {target + 1}.")

    # ═══════════════════════════════════════════════════════════════════
    # Runtime state machine: core run/hold/pause/stop loop
    # ═══════════════════════════════════════════════════════════════════

    def _run_experiment_control(self) -> None:
        self._start_or_resume_experiment_control()

    def _start_or_resume_experiment_control(self) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            self._set_status_message("Experiment plan is empty.")
            return
        if self._plan_running:
            return
        if self._plan_holding or self._plan_paused:
            restore_step = None
            if self._plan_paused and self._paused_plan_step is not None:
                restore_step = deepcopy(self._paused_plan_step)
                self._paused_plan_step = None
            self._resume_experiment_plan(
                restore_step=restore_step,
                status_message=f"Resumed experiment plan on step {self._plan_active_row + 1 if self._plan_active_row is not None else 1}.",
                log_message=f"Experiment plan resumed | step={self._plan_active_row + 1 if self._plan_active_row is not None else 1}",
                emit_event="plan_resume",
            )
            return
        recording_active = bool(getattr(getattr(self, "recording_controller", None), "_measurement_active", False))
        if self.record_with_flow_button.isChecked() and not recording_active:
            row = self._selected_experiment_control_row()
            if row is None:
                row = 0
                self._select_experiment_control_plan_row(0)
            self._queue_experiment_control_start_after_recording(paused=False, row=row)
        if not self._request_recording_control("start"):
            self._pending_experiment_control_start_after_recording = None
            self._set_status_message("Experiment plan start cancelled because recording was not started.")
            return
        if self._pending_experiment_control_start_after_recording is not None:
            return
        row = self._selected_experiment_control_row()
        if row is None:
            row = 0
            self._select_experiment_control_plan_row(0)
        self._begin_experiment_plan_run(row, steps)

    def _hold_experiment_control(self) -> None:
        self._enter_hold_state()

    def _pause_experiment_control(self) -> None:
        self._enter_pause_state()

    def _stop_experiment_control(self) -> None:
        self._stop_experiment_plan(self._applied_plan_step)

    def _schedule_plan_timer(self, steps: list | None = None) -> None:
        if self._plan_timer.isActive():
            return
        if not self._plan_running and not self._plan_holding and not self._plan_paused:
            return
        if not self._plan_running or self._plan_started_monotonic is None or self._plan_holding or self._plan_paused:
            self._plan_timer.start(150)
            return
        if steps is None:
            steps = self._read_experiment_control_steps()
        active_row = self._plan_active_row
        if active_row is None or not steps or not (0 <= active_row < len(steps)):
            self._plan_timer.start(150)
            return
        step = steps[active_row]
        elapsed = self._plan_resume_elapsed_s + max(monotonic() - self._plan_started_monotonic, 0.0)
        remaining_ms = int((float(step.duration_s) - elapsed) * 1000)
        self._plan_timer.start(max(1, min(150, remaining_ms)))

    def _advance_experiment_control_progress(self) -> None:
        steps: list | None = None

        def _callback() -> None:
            nonlocal steps
            if self._plan_holding or self._plan_paused:
                steps = self._read_experiment_control_steps()
                if steps:
                    self._sync_experiment_control_timeline(steps, self._plan_active_row, refresh_status=True)
                return
            if not self._plan_running or self._plan_started_monotonic is None:
                return
            if self._step_apply_pending:
                # Previous step's device commands still running; wait and retry.
                self._plan_timer.start(50)
                return
            steps = self._read_experiment_control_steps()
            if not steps:
                self._stop_experiment_control()
                return
            elapsed = self._plan_resume_elapsed_s + max(monotonic() - self._plan_started_monotonic, 0.0)
            current_row = self._plan_active_row if self._plan_active_row is not None else self._selected_experiment_control_row()
            if current_row is None or not (0 <= current_row < len(steps)):
                current_row = 0
            current_step = steps[current_row]
            if elapsed >= max(float(current_step.duration_s), 0.0):
                next_row = current_row + 1
                if next_row >= len(steps):
                    self._plan_elapsed_s = max(float(current_step.duration_s), 0.0)
                    self._plan_resume_elapsed_s = self._plan_elapsed_s
                    self._stop_experiment_control()
                    self._set_status_message("Experiment plan finished.")
                    _LOGGER.info("Experiment plan finished.")
                    return
                self._plan_active_row = next_row
                self._apply_step_to_pump_async(steps[next_row], start=True)
                self._plan_elapsed_s = 0.0
                self._plan_resume_elapsed_s = 0.0
                self._plan_started_monotonic = monotonic()
                self._step_started_monotonic = monotonic()
                self._sync_experiment_control_timeline(steps, next_row, refresh_status=True)
                return
            self._plan_elapsed_s = elapsed
            self._sync_experiment_control_timeline(steps, current_row, refresh_status=True)

        self._run_gui_callback_timed("experiment_control_progress", _callback)
        self._schedule_plan_timer(steps)

    def _activate_experiment_control_step_for_elapsed(self, elapsed_s: float, *, force: bool) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            self._plan_active_row = None
            self._plan_elapsed_s = 0.0
            return
        self._plan_elapsed_s = max(float(elapsed_s), 0.0)
        target_row = self._plan_active_row if self._plan_active_row is not None else self._selected_experiment_control_row()
        if target_row is None or not (0 <= target_row < len(steps)):
            target_row = 0
        if force or target_row != self._plan_active_row:
            self._plan_active_row = target_row
            self._apply_step_to_pump_async(steps[target_row], start=True)
        self._sync_experiment_control_timeline(steps, target_row)
