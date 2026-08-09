"""Direct unit tests for the shared, pure step-command decision function
(Phase 2, LSPRi acq experiment-control reuse - Tier 2 extraction,
2026-08-09).

Tests the real owner (`lspr_acq_shell.experiment_control_step_decision`)
rather than going through `ExperimentControlWindow`'s shim - no Qt/window
construction needed at all, which is itself the point of the extraction:
`tests/integration/test_experiment_control_pump_dispatch.py` still covers
the same regression scenario through a real window (proving the wiring),
this file proves the decision logic itself is correct standalone.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_acq_shell.experiment_control_step_decision import StepCommandContext, plan_step_commands
from lspr_acq_shell.pump_plan import PumpChannelStep, PumpPlanStep


def _context(**overrides) -> StepCommandContext:
    defaults = dict(
        wait_for_mswitch_first=False,
        pump_label="pump_1",
        valve_label="switch_1",
        switch_label="selector_1",
        pump_connected=True,
        valve_connected=True,
        mswitch_connected=True,
        tube_mm_by_channel=[0.25] * 6,
        pump_backsteps=0,
        pump_roller_count=8,
        pump_display_enabled=True,
    )
    defaults.update(overrides)
    return StepCommandContext(**defaults)


def _step(*, valve: str = "load", switch_position: int = 1, channels: list[PumpChannelStep] | None = None) -> PumpPlanStep:
    return PumpPlanStep(
        step=1, duration_s=60.0, valve=valve, switch_position=switch_position,
        channels=channels or [PumpChannelStep() for _ in range(6)],
    )


def _by_type(commands, command_type: str) -> dict[int, dict]:
    return {cmd.payload["channel"]: cmd.payload for cmd in commands if cmd.command_type == command_type}


class ValveAndSwitchCommandsTests(unittest.TestCase):
    def test_valve_change_from_previous_emits_a_switch_set_position(self) -> None:
        previous = _step(valve="wash")
        step = _step(valve="load")
        commands, _needs_refresh, _status = plan_step_commands(step, previous, _context(), start=True)
        valve_cmds = [c for c in commands if c.command_type == "switch.set_position"]
        self.assertEqual(len(valve_cmds), 1)
        self.assertEqual(valve_cmds[0].payload["position"], "load")

    def test_same_valve_as_previous_emits_no_command(self) -> None:
        previous = _step(valve="load")
        step = _step(valve="load")
        commands, _, _ = plan_step_commands(step, previous, _context(), start=True)
        self.assertEqual([c for c in commands if c.command_type == "switch.set_position"], [])

    def test_switch_position_change_emits_a_move_to_marked_as_switch_move(self) -> None:
        previous = _step(switch_position=1)
        step = _step(switch_position=3)
        commands, needs_refresh, _status = plan_step_commands(step, previous, _context(), start=True)
        move_cmds = [c for c in commands if c.command_type == "switch.move_to"]
        self.assertEqual(len(move_cmds), 1)
        self.assertEqual(move_cmds[0].payload["position"], 3)
        self.assertTrue(move_cmds[0].is_switch_move)
        self.assertTrue(needs_refresh)

    def test_no_previous_step_still_treats_valve_as_changed_when_set(self) -> None:
        step = _step(valve="load")
        commands, _, _ = plan_step_commands(step, None, _context(), start=True)
        self.assertEqual(len([c for c in commands if c.command_type == "switch.set_position"]), 1)


class DisconnectedDeviceStatusMessageTests(unittest.TestCase):
    def test_pump_disconnected_skips_channel_updates_and_warns(self) -> None:
        step = _step(channels=[PumpChannelStep(flow_ul_min=10.0, direction="CW")] + [PumpChannelStep() for _ in range(5)])
        commands, _, status = plan_step_commands(step, None, _context(pump_connected=False), start=True)
        self.assertEqual([c for c in commands if c.command_type in ("pump.set_flow", "pump.start", "pump.stop")], [])
        self.assertIn("Pump controller not connected.", status)

    def test_valve_disconnected_skips_the_command_and_warns(self) -> None:
        previous = _step(valve="wash")
        step = _step(valve="load")
        commands, _, status = plan_step_commands(step, previous, _context(valve_connected=False), start=True)
        self.assertEqual([c for c in commands if c.command_type == "switch.set_position"], [])
        self.assertIn("Switch controller not connected.", status)

    def test_switch_disconnected_skips_the_command_and_warns(self) -> None:
        previous = _step(switch_position=1)
        step = _step(switch_position=2)
        commands, needs_refresh, status = plan_step_commands(step, previous, _context(mswitch_connected=False), start=True)
        self.assertEqual([c for c in commands if c.command_type == "switch.move_to"], [])
        self.assertIn("Switch rotary valve not connected.", status)
        self.assertFalse(needs_refresh)


class WaitForSwitchFirstOrderingTests(unittest.TestCase):
    def test_switch_move_is_ordered_before_pump_start_when_switch_changed(self) -> None:
        previous = _step(switch_position=1, channels=[PumpChannelStep(flow_ul_min=10.0, direction="CW")] + [PumpChannelStep() for _ in range(5)])
        step = _step(switch_position=2, channels=[PumpChannelStep(flow_ul_min=10.0, direction="CW")] + [PumpChannelStep() for _ in range(5)])
        commands, _, _ = plan_step_commands(step, previous, _context(wait_for_mswitch_first=True), start=True)
        types = [c.command_type for c in commands]
        self.assertIn("switch.move_to", types)
        self.assertLess(types.index("switch.move_to"), len(types))

    def test_ordering_is_unaffected_when_switch_did_not_change(self) -> None:
        previous = _step(switch_position=1)
        step = _step(switch_position=1)
        commands, _, _ = plan_step_commands(step, previous, _context(wait_for_mswitch_first=True), start=True)
        self.assertEqual([c for c in commands if c.command_type == "switch.move_to"], [])


class OffDirectionWithNonzeroFlowRegressionTests(unittest.TestCase):
    """Same real-world regression as
    tests/integration/test_experiment_control_pump_dispatch.py, exercised
    directly against the shared pure function instead of through a real Qt
    window - proves the extraction preserved the fix, not just the wiring."""

    def test_flow_only_channel_with_raw_off_direction_is_still_configured_and_started(self) -> None:
        step = _step(channels=[
            PumpChannelStep(flow_ul_min=20.0, direction="CW"),
            PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
            PumpChannelStep(flow_ul_min=10.0, direction="OFF"),
            PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
            PumpChannelStep(),
            PumpChannelStep(),
        ])
        commands, _, _ = plan_step_commands(step, None, _context(), start=True)

        configured = _by_type(commands, "pump.set_flow")
        started = _by_type(commands, "pump.start")

        self.assertIn(1, configured)
        self.assertIn(3, configured, "CH3 (flow>0, raw direction OFF) must still be configured")
        self.assertNotIn(2, configured)
        self.assertIn(1, started)
        self.assertIn(3, started, "CH3 (flow>0, raw direction OFF) must still be started")
        self.assertEqual(configured[3]["direction"], "CW")
        self.assertEqual(configured[3]["flow_ul_min"], 10.0)

    def test_zero_flow_channel_stays_inactive_regardless_of_direction(self) -> None:
        step = _step(channels=[PumpChannelStep(flow_ul_min=0.0, direction="CW")] + [PumpChannelStep() for _ in range(5)])
        commands, _, _ = plan_step_commands(step, None, _context(), start=True)
        self.assertNotIn(1, _by_type(commands, "pump.set_flow"))


class PumpDisplayCommandTests(unittest.TestCase):
    def test_display_command_always_sent_when_pump_connected_even_with_no_change(self) -> None:
        previous = _step()
        step = _step()
        commands, _, _ = plan_step_commands(step, previous, _context(pump_display_enabled=False), start=True)
        display_cmds = [c for c in commands if c.command_type == "pump.set_display"]
        self.assertEqual(len(display_cmds), 1)
        self.assertEqual(display_cmds[0].payload["text"], "")

    def test_display_disabled_globally_clears_the_pump_display_text(self) -> None:
        step = _step()
        step.description = "Wash step"
        commands, _, _ = plan_step_commands(step, None, _context(pump_display_enabled=False), start=True)
        display_cmds = [c for c in commands if c.command_type == "pump.set_display"]
        self.assertEqual(display_cmds[0].payload["text"], "")

    def test_display_enabled_shows_the_step_description(self) -> None:
        step = _step()
        step.description = "Wash step"
        commands, _, _ = plan_step_commands(step, None, _context(pump_display_enabled=True), start=True)
        display_cmds = [c for c in commands if c.command_type == "pump.set_display"]
        self.assertEqual(display_cmds[0].payload["text"], "Wash step")


class TubeDiameterPassthroughTests(unittest.TestCase):
    def test_configured_channel_uses_the_tube_diameter_for_its_own_index(self) -> None:
        step = _step(channels=[
            PumpChannelStep(flow_ul_min=10.0, direction="CW"),
            PumpChannelStep(flow_ul_min=20.0, direction="CW"),
        ] + [PumpChannelStep() for _ in range(4)])
        context = _context(tube_mm_by_channel=[0.13, 1.52, 0.25, 0.25, 0.25, 0.25])
        commands, _, _ = plan_step_commands(step, None, context, start=True)
        configured = _by_type(commands, "pump.set_flow")
        self.assertEqual(configured[1]["tube_mm"], 0.13)
        self.assertEqual(configured[2]["tube_mm"], 1.52)


if __name__ == "__main__":
    unittest.main()
