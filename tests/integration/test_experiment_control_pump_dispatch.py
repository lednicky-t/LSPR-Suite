"""Regression coverage for a real reported bug: a plan step's pump channel
with a nonzero flow rate but the raw (never-explicitly-set) "OFF" direction
default was silently skipped when applying a step to hardware - only
channels whose direction cell had actually been clicked/edited ever moved.

Root cause: the plan table's direction column has always *displayed* the
raw "OFF" default as "CW" (flow_plan_model.normalized_pump_direction never
returns "OFF"), so a channel that was only ever given a flow rate looks,
in the table, identical to one with an explicitly-chosen CW direction.
_plan_step_commands (experiment_control_window.py) used to read the raw,
un-normalized direction value to decide whether a channel was "active",
so it silently blocked exactly those never-touched channels even though
the table showed nothing wrong. Confirmed against a real exported plan
file where every non-CH1 channel had `direction: 'OFF'` despite nonzero
flow rates.

Uses a real ExperimentControlWindow (not a fake self) - _plan_step_commands
reads real widget state (manual_tube_spins) that only exists once the
window is actually built, same reasoning as
test_experiment_control_copy_paste.py.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6.QtWidgets import QApplication

from lspr_app.domain.pump_plan import PumpChannelStep, PumpPlanStep
from lspr_app.gui.experiment_control_window import ExperimentControlWindow


# Held at module scope: QApplication.instance() or QApplication([]) would otherwise
# construct-and-immediately-garbage-collect a new QApplication if nothing keeps a
# Python reference to it, crashing the process natively on the next widget construction.
_APP = QApplication.instance() or QApplication([])


def _make_window_with_pump_connected() -> ExperimentControlWindow:
    window = ExperimentControlWindow(
        {},
        known_probe=None,
        theme_mode="dark",
        initial_mswitch_devices=[],
        auto_connect_devices=False,
        show_runtime_controls=True,
    )
    fake_service = MagicMock()
    fake_service.is_connected.side_effect = lambda label: label == "pump_1"
    fake_service.connection.return_value = None
    window._device_comm_service = fake_service
    return window


def _commands_by_type(commands, command_type: str) -> dict[int, dict]:
    return {cmd.payload["channel"]: cmd.payload for cmd in commands if cmd.command_type == command_type}


class PumpChannelOffDirectionDispatchTests(unittest.TestCase):
    def test_flow_only_channel_with_raw_off_direction_is_still_configured_and_started(self) -> None:
        # Exactly the reported real-world shape: CH3 has a real flow rate,
        # but its direction cell was never touched (raw default "OFF") -
        # the table would still show "CW" for it.
        step = PumpPlanStep(
            step=1, duration_s=60.0, valve="Open", switch_position=1,
            channels=[
                PumpChannelStep(flow_ul_min=20.0, direction="CW"),
                PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
                PumpChannelStep(flow_ul_min=10.0, direction="OFF"),
                PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
            ],
        )
        window = _make_window_with_pump_connected()

        commands, _needs_mswitch_refresh, _status = window._plan_step_commands(step, start=True)

        configured = _commands_by_type(commands, "pump.set_flow")
        started = _commands_by_type(commands, "pump.start")

        self.assertIn(1, configured)
        self.assertIn(3, configured, "CH3 (flow>0, raw direction OFF) must still be configured")
        self.assertNotIn(2, configured, "CH2 (flow=0) must stay unconfigured")
        self.assertNotIn(4, configured, "CH4 (flow=0) must stay unconfigured")

        self.assertIn(1, started)
        self.assertIn(3, started, "CH3 (flow>0, raw direction OFF) must still be started")

        self.assertEqual(configured[3]["direction"], "CW")
        self.assertEqual(configured[3]["flow_ul_min"], 10.0)

    def test_zero_flow_channel_stays_inactive_regardless_of_direction(self) -> None:
        step = PumpPlanStep(
            step=1, duration_s=60.0, valve="Open", switch_position=1,
            channels=[PumpChannelStep(flow_ul_min=0.0, direction="CW")] + [PumpChannelStep() for _ in range(3)],
        )
        window = _make_window_with_pump_connected()

        commands, _, _ = window._plan_step_commands(step, start=True)

        configured = _commands_by_type(commands, "pump.set_flow")
        self.assertNotIn(1, configured)

    def test_all_four_channels_with_off_direction_and_flow_are_dispatched(self) -> None:
        # The exact multi-channel shape from the user's real exported plan
        # file's step 3: CH1 explicitly CW, CH2/CH3 nonzero flow with raw
        # "OFF" direction, CH4 zero flow.
        step = PumpPlanStep(
            step=3, duration_s=60.0, valve="Open", switch_position=1,
            channels=[
                PumpChannelStep(flow_ul_min=10.0, direction="CW"),
                PumpChannelStep(flow_ul_min=30.0, direction="OFF"),
                PumpChannelStep(flow_ul_min=30.0, direction="OFF"),
                PumpChannelStep(flow_ul_min=0.0, direction="OFF"),
            ],
        )
        window = _make_window_with_pump_connected()

        commands, _, _ = window._plan_step_commands(step, start=True)
        started = _commands_by_type(commands, "pump.start")

        self.assertEqual(set(started.keys()), {1, 2, 3})


if __name__ == "__main__":
    unittest.main()
