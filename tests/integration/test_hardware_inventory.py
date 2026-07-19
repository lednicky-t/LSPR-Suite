from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
import sys

if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import lspr_app.device.port_assignments as port_assignments
from lspr_app.device.hardware_inventory import scan_connected_serial_devices
from lspr_app.device.reglo_icc import PumpProbe
from lspr_app.device.serial_controllers import ControllerPort, ControllerProbe


class _FakeMatchedValveController:
    controller_type = "legacy-valve"
    priority = 10
    connect_calls = 0

    @classmethod
    def is_probable_port(cls, port: ControllerPort) -> bool:
        return port.device == "COM4"

    def __init__(self) -> None:
        self.port: str | None = None
        self.closed = False

    def connect(self, port: str) -> None:
        type(self).connect_calls += 1
        self.port = port

    def get_probe(self) -> ControllerProbe:
        return ControllerProbe(
            port=self.port or "",
            controller_type=self.controller_type,
            model="Legacy valve controller",
            serial_number="V456",
            protocol_version="legacy-vi",
        )

    def close(self) -> None:
        self.closed = True


class _FakeUnmatchedItsyController:
    controller_type = "itsybitsy-32u4-valve"
    priority = 30
    connect_calls = 0

    @classmethod
    def is_probable_port(cls, port: ControllerPort) -> bool:
        return False

    def __init__(self) -> None:
        self.port: str | None = None
        self.closed = False

    def connect(self, port: str) -> None:
        type(self).connect_calls += 1
        self.port = port

    def get_probe(self) -> ControllerProbe:
        return ControllerProbe(
            port=self.port or "",
            controller_type=self.controller_type,
            model="ItsyBitsy 32u4 valve controller",
            serial_number="I123",
            protocol_version="itsy-vi",
        )

    def close(self) -> None:
        self.closed = True


class HardwareInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        # port_assignments caches assignments at process scope; reset it so this
        # test never observes real settings left behind by another test/process.
        port_assignments._assignment_cache = None
        self.addCleanup(setattr, port_assignments, "_assignment_cache", None)

    def test_scan_connected_serial_devices_classifies_probable_ports(self) -> None:
        ports = [
            ControllerPort(device="COM3", description="Reglo pump", hwid="USB VID:PID=265C:0001"),
            ControllerPort(device="COM4", description="Valve controller", hwid="USB VID:PID=2341:0043"),
            ControllerPort(device="COM5", description="Unknown", hwid="USB VID:PID=0000:0000"),
        ]

        with (
            patch("lspr_app.device.hardware_inventory.SerialController.list_ports", return_value=ports) as list_ports_mock,
            patch("lspr_app.device.hardware_inventory.is_probable_reglo_port", side_effect=lambda port: port.device == "COM3"),
            patch("lspr_app.device.hardware_inventory.RegloICCClient.probe_port", return_value=PumpProbe(
                port="COM3",
                protocol_version="1.0",
                serial_number="P123",
                channel_count=4,
                model="Reglo ICC",
            )),
            patch(
                "lspr_app.device.hardware_inventory.registered_controllers",
                return_value=(_FakeMatchedValveController, _FakeUnmatchedItsyController),
            ),
            # Force every port to "auto" so this test doesn't depend on the
            # real machine's saved manual port assignments (get_port_assignment
            # and should_probe_port_for_role both read through this cache).
            patch("lspr_app.device.port_assignments.load_app_setting", return_value={}),
        ):
            inventory = scan_connected_serial_devices()

        self.assertEqual(list_ports_mock.call_count, 1)
        self.assertEqual(len(inventory), 3)

        pump_record = inventory[0]
        self.assertEqual(pump_record.port, "COM3")
        self.assertEqual(pump_record.recognition_state, "confirmed")
        self.assertIn("Pump controller", pump_record.recognized_device)
        self.assertIn("Reglo ICC", pump_record.recognized_device)
        self.assertIn("serial P123", pump_record.details)

        valve_record = inventory[1]
        self.assertEqual(valve_record.port, "COM4")
        self.assertEqual(valve_record.recognition_state, "confirmed")
        self.assertIn("Legacy Valve", valve_record.recognized_device)
        self.assertIn("Legacy valve controller", valve_record.recognized_device)
        self.assertIn("serial V456", valve_record.details)
        self.assertEqual(_FakeMatchedValveController.connect_calls, 1)
        self.assertEqual(_FakeUnmatchedItsyController.connect_calls, 0)

        unknown_record = inventory[2]
        self.assertEqual(unknown_record.port, "COM5")
        self.assertEqual(unknown_record.recognition_state, "unrecognized")
        self.assertEqual(unknown_record.recognized_device, "Unrecognized")


if __name__ == "__main__":
    unittest.main()
