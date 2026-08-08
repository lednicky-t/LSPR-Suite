"""Background task for applying a single pump-plan step to the hardware.

When the experiment control panel advances to a new step it fans out a batch
of device commands (flow rate, direction, valve, switch rotary valve port) on
a thread-pool thread so the GUI stays responsive.

Extracted from singleLSPR Acquisition's `gui/experiment_control_step_runner.py`
(Phase 2, LSPRi acq experiment-control reuse - Tier 0 extraction, 2026-08-09)
verbatim - zero window coupling to begin with; the actual hardware I/O it
calls into (DeviceCommunicationService) was already shared here since Phase 1's
fluidics device layer move, so this is the piece that finally makes the whole
capture->execute path (plan step -> commands -> DeviceCommunicationService)
reusable end to end, not just the device layer underneath it.

Classes
-------
``_PlannedCommand``
    Single device command to execute: label, type, payload, description.
``_StepApplyResult``
    Result returned by the task: success flag, messages, mswitch refresh flag.
``_StepApplySignals``
    Qt signals for :class:`_StepApplyRunnable`.
``_StepApplyRunnable``
    ``QRunnable`` that sends the command batch and emits
    :attr:`_StepApplySignals.done` when finished.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from lspr_acq_shell.communication_models import DeviceCommand
from lspr_acq_shell.device_manager import DeviceCommunicationService
from lspr_acq_shell.pump_plan import PumpPlanStep


_LOGGER = logging.getLogger("lspr_app.experiment_control")


@dataclass(slots=True)
class _PlannedCommand:
    """A single device command queued for step execution.

    Attributes:
        label: Human-readable label for logging.
        command_type: Device command type string.
        payload: Command payload dict passed to the device service.
        description: Full description used in log messages.
        is_switch_move: Whether this command changes the switch rotary valve
            port (triggers a position refresh after the step).
    """

    label: str
    command_type: str
    payload: dict
    description: str
    is_switch_move: bool = False


@dataclass(slots=True)
class _StepApplyResult:
    """Outcome of a single step-apply task.

    Attributes:
        success: ``True`` if all commands completed without an exception.
        step: The :class:`~lspr_acq_shell.pump_plan.PumpPlanStep` that was
            applied.
        status_messages: Accumulated warning / error messages for display in
            the status bar.
        needs_mswitch_refresh: ``True`` if a switch rotary valve move
            occurred and its position should be re-queried.
        on_success: Optional callback the dispatcher wants run only if
            ``success`` is ``True``, once this specific result comes back.
            Carried on the result object itself (not a shared queue on the
            caller) so overlapping dispatches - e.g. a manual step jump
            fired while an auto-advance's switch rotary valve move is still
            in flight - can never mix up which callback belongs to which
            completion.
    """

    success: bool
    step: PumpPlanStep
    status_messages: list[str]
    needs_mswitch_refresh: bool
    on_success: Callable[[], None] | None = None


class _StepApplySignals(QObject):
    """Qt signals for :class:`_StepApplyRunnable`."""

    done = pyqtSignal(object)


class _StepApplyRunnable(QRunnable):
    """Thread-pool task that sends a batch of device commands for one plan step.

    Iterates over *commands*, sends each via *device_service*, and collects
    any failures into the result's ``status_messages`` list.  Emits
    :attr:`signals.done` with a :class:`_StepApplyResult` when finished —
    whether or not all commands succeeded.
    """

    def __init__(
        self,
        device_service: DeviceCommunicationService,
        commands: list[_PlannedCommand],
        step: PumpPlanStep,
        needs_mswitch_refresh: bool,
        pre_status: list[str],
        on_success: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.signals = _StepApplySignals()
        self._device_service = device_service
        self._commands = commands
        self._step = step
        self._needs_mswitch_refresh = needs_mswitch_refresh
        self._pre_status = pre_status
        self._on_success = on_success
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover - thread-pool path
        status_messages = list(self._pre_status)
        success = True
        needs_mswitch_refresh = self._needs_mswitch_refresh
        try:
            for cmd in self._commands:
                result = self._device_service.send_command(cmd.label, DeviceCommand(cmd.command_type, cmd.payload))
                if result.success:
                    if cmd.is_switch_move:
                        needs_mswitch_refresh = True
                    _LOGGER.info("Step command OK | %s", cmd.description)
                else:
                    _LOGGER.warning("Step command failed | %s | error=%s", cmd.description, result.error)
                    status_messages.append(f"{cmd.command_type} failed")
        except Exception as exc:
            _LOGGER.error("Step apply runnable failed | step=%s error=%s", self._step.step, exc)
            success = False
            status_messages.append(f"Error: {exc}")
        self.signals.done.emit(_StepApplyResult(
            success=success,
            step=self._step,
            status_messages=status_messages,
            needs_mswitch_refresh=needs_mswitch_refresh,
            on_success=self._on_success,
        ))
