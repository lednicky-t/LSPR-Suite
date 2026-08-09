"""Pure decision logic for what hardware commands a pump-plan step transition
requires.

Extracted from singleLSPR Acquisition's `gui/experiment_control_window.py`
(`_plan_step_commands`, Phase 2, LSPRi acq experiment-control reuse - Tier 2
extraction, 2026-08-09). This is the one piece of the experiment-control
panel that actually decides what physically happens to the pump/valve/
selector hardware on a step transition - the dispatch mechanism that sends
those commands (`_StepApplyRunnable`) was already shared in Tier 0.

The original method read almost everything it needed from `self` (the Qt
window), including one live widget value (`manual_tube_spins[i].value()`
for tube diameter). Tracing every read (not guessing) turned up exactly
that one widget dependency plus a handful of device-connection flags and
settings - all trivially passable as explicit parameters instead. This
function has no Qt/window dependency at all: same step diff in, same
command list out, callable from a unit test with no GUI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from lspr_acq_shell.experiment_control_step_runner import _PlannedCommand
from lspr_acq_shell.pump_plan import PumpPlanStep, normalized_pump_direction

_LOGGER = logging.getLogger("lspr_app.experiment_control")


@dataclass(slots=True, frozen=True)
class StepCommandContext:
    """Everything `plan_step_commands` needs that isn't the step diff itself.

    One field per real dependency traced out of the original window method -
    see this module's docstring. Grouped into a dataclass (rather than a long
    parameter list) so a caller building it from `self.*` reads has one place
    to look, and so LSPRi acq's own experiment-control window builds the
    exact same shape.
    """

    wait_for_mswitch_first: bool
    pump_label: str
    valve_label: str
    switch_label: str
    pump_connected: bool
    valve_connected: bool
    mswitch_connected: bool
    tube_mm_by_channel: list[float]
    pump_backsteps: int
    pump_roller_count: int
    pump_display_enabled: bool
    plan_running: bool = False
    plan_holding: bool = False
    switch_controller_type: str | None = None
    switch_port: str | None = None


def plan_step_commands(
    step: PumpPlanStep,
    previous: PumpPlanStep | None,
    context: StepCommandContext,
    *,
    start: bool,
) -> tuple[list[_PlannedCommand], bool, list[str]]:
    """Build an ordered command list for a step transition.

    Pure function: no Qt, no device I/O, no `self`. Returns
    ``(commands, needs_mswitch_refresh, pre_status_messages)`` - identical
    contract to the original `ExperimentControlWindow._plan_step_commands`.
    """
    status_messages: list[str] = []
    commands: list[_PlannedCommand] = []

    valve = str(step.valve or "").strip()
    previous_valve = str(previous.valve or "").strip().lower() if previous is not None else ""
    switch_position = int(max(min(int(step.switch_position), 12), 1))
    previous_switch = int(max(min(int(previous.switch_position), 12), 1)) if previous is not None else -1
    switch_changed = switch_position != previous_switch
    wait_for_switch_first = bool(context.wait_for_mswitch_first and switch_changed)

    pump_label = context.pump_label
    valve_label = context.valve_label
    switch_label = context.switch_label
    pump_connected = context.pump_connected
    valve_connected = context.valve_connected
    mswitch_connected = context.mswitch_connected

    channels_to_stop: list[int] = []
    channels_to_start: list[int] = []
    channels_to_configure: list[tuple[int, float, str, float]] = []
    channels_to_restart_after_switch: list[int] = []

    _LOGGER.info(
        "Applying experiment-plan step | step=%s valve=%s previous_valve=%s controller=%s port=%s running=%s holding=%s start=%s",
        step.step,
        valve or "-",
        str(previous.valve or "").strip() or "-" if previous is not None else "-",
        context.switch_controller_type,
        context.switch_port,
        context.plan_running,
        context.plan_holding,
        start,
    )

    if pump_connected:
        for index, channel in enumerate(step.channels, start=1):
            # Normalize exactly like the table's own display/write path
            # (flow_plan_model.normalized_pump_direction) - a channel's raw
            # direction defaults to "OFF" until its cell is explicitly
            # touched, but the table has always *displayed* that default as
            # "CW" (the function never returns "OFF"). Reading the raw value
            # here instead meant a channel whose direction cell nobody ever
            # clicked - despite showing "CW" - was silently skipped even
            # with a real flow rate set, since only this dispatch code (not
            # the table) still treated "OFF" as a real third state blocking
            # the channel.
            direction = normalized_pump_direction(channel.direction)
            active = channel.flow_ul_min > 0.0
            tube_mm = context.tube_mm_by_channel[index - 1]
            previous_channel = previous.channels[index - 1] if previous is not None else None
            previous_direction = (
                normalized_pump_direction(previous_channel.direction) if previous_channel is not None else "CW"
            )
            previous_active = previous_channel is not None and previous_channel.flow_ul_min > 0.0
            previous_flow = float(previous_channel.flow_ul_min) if previous_channel is not None else 0.0
            channel_changed = (
                previous is None
                or previous_channel is None
                or previous_direction != direction
                or abs(previous_flow - float(channel.flow_ul_min)) > 1e-9
            )
            if previous_active and (not active or channel_changed or (wait_for_switch_first and switch_changed)):
                channels_to_stop.append(index)
            if wait_for_switch_first and switch_changed and previous_active and active and not channel_changed:
                channels_to_restart_after_switch.append(index)
            if active and channel_changed:
                channels_to_configure.append((index, float(channel.flow_ul_min), direction, tube_mm))
                if start:
                    channels_to_start.append(index)
            elif active and start and not previous_active:
                channels_to_start.append(index)
    else:
        _LOGGER.warning("Pump controller offline; skipping pump channel updates | step=%s", step.step)
        status_messages.append("Pump controller not connected.")

    effective_starts_after_switch = list(channels_to_start)
    if wait_for_switch_first and switch_changed:
        for index in channels_to_restart_after_switch:
            if index not in effective_starts_after_switch:
                effective_starts_after_switch.append(index)

    def _pump_stop_cmds(indices: list[int]) -> list[_PlannedCommand]:
        return [_PlannedCommand(pump_label, "pump.stop", {"channel": i}, f"pump.stop ch={i}") for i in indices]

    def _pump_configure_cmds() -> list[_PlannedCommand]:
        return [
            _PlannedCommand(
                pump_label, "pump.set_flow",
                {
                    "channel": i, "flow_ul_min": fl, "direction": d, "tube_mm": t,
                    "backsteps": context.pump_backsteps, "roller_count": context.pump_roller_count, "start": False,
                },
                f"pump.set_flow ch={i} flow={fl:.2f} dir={d}",
            )
            for i, fl, d, t in channels_to_configure
        ]

    def _pump_start_cmds(indices: list[int]) -> list[_PlannedCommand]:
        return [_PlannedCommand(pump_label, "pump.start", {"channel": i}, f"pump.start ch={i}") for i in indices]

    def _valve_cmd() -> list[_PlannedCommand]:
        if not (valve and valve.lower() != previous_valve):
            return []
        if valve_connected:
            return [_PlannedCommand(valve_label, "switch.set_position", {"position": valve}, f"switch.set_position pos={valve}")]
        status_messages.append("Switch controller not connected.")
        _LOGGER.warning("Valve command skipped | controller not connected | step=%s valve=%s", step.step, valve)
        return []

    def _switch_cmd() -> list[_PlannedCommand]:
        if not switch_changed:
            return []
        if mswitch_connected:
            return [_PlannedCommand(
                switch_label, "switch.move_to", {"position": switch_position, "block": True},
                f"switch.move_to pos={switch_position}", is_switch_move=True,
            )]
        status_messages.append("Switch rotary valve not connected.")
        _LOGGER.warning("Switch rotary valve command skipped | controller not connected | step=%s switch=%s", step.step, switch_position)
        return []

    def _pump_display_cmd() -> list[_PlannedCommand]:
        # Always sent (not diffed against the previous step) so a step with the
        # option off reliably clears whatever the previous step left showing,
        # instead of leaving a stale comment on the pump's display.
        if not pump_connected:
            return []
        text = str(step.description or "").strip() if context.pump_display_enabled else ""
        return [_PlannedCommand(pump_label, "pump.set_display", {"text": text}, f"pump.set_display text={text!r}")]

    if wait_for_switch_first:
        if pump_connected:
            commands.extend(_pump_stop_cmds(channels_to_stop))
        commands.extend(_switch_cmd())
        commands.extend(_valve_cmd())
        if pump_connected:
            commands.extend(_pump_configure_cmds())
            commands.extend(_pump_start_cmds(effective_starts_after_switch))
            commands.extend(_pump_display_cmd())
    else:
        if pump_connected:
            commands.extend(_pump_stop_cmds(channels_to_stop))
            commands.extend(_pump_configure_cmds())
            commands.extend(_pump_start_cmds(channels_to_start))
            commands.extend(_pump_display_cmd())
        commands.extend(_valve_cmd())
        commands.extend(_switch_cmd())

    needs_mswitch_refresh = any(c.is_switch_move for c in commands)
    return commands, needs_mswitch_refresh, status_messages
