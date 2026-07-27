"""Unit tests for ArduinoValveController's ambient temperature/humidity
reads (recovered protocol - see docs/hardware/arduino_valve_controller_protocol.md)
and their dispatch through SerialController.execute_command().

Uses a MagicMock in place of a real pyserial.Serial handle, same pattern as
test_device_retry_policy.py - no hardware required.
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

from lspr_app.device.communication_models import DeviceCommand
from lspr_app.device.serial_controllers import ControllerError
from lspr_app.device.valve_controllers import ArduinoValveController, ItsyBitsy32U4ValveController


def _connected(controller):
    controller._serial = MagicMock()
    controller._serial.is_open = True
    controller.port = "FAKE"
    return controller


class ArduinoValveControllerEnvironmentReadTests(unittest.TestCase):
    def test_read_ambient_temperature_parses_response(self) -> None:
        controller = _connected(ArduinoValveController())
        controller._serial.readline.return_value = b"23.4\n"

        value = controller.read_ambient_temperature()

        self.assertEqual(value, 23.4)
        controller._serial.write.assert_called_once_with(b"at\n")

    def test_read_humidity_parses_response(self) -> None:
        controller = _connected(ArduinoValveController())
        controller._serial.readline.return_value = b"41.2\n"

        value = controller.read_humidity()

        self.assertEqual(value, 41.2)
        controller._serial.write.assert_called_once_with(b"ah\n")

    def test_read_ambient_temperature_raises_on_unparseable_response(self) -> None:
        controller = _connected(ArduinoValveController())
        controller._serial.readline.return_value = b"err\n"

        with self.assertRaises(ControllerError):
            controller.read_ambient_temperature()

    def test_read_humidity_raises_on_unparseable_response(self) -> None:
        controller = _connected(ArduinoValveController())
        controller._serial.readline.return_value = b"err\n"

        with self.assertRaises(ControllerError):
            controller.read_humidity()

    def test_itsybitsy_firmware_does_not_support_environment_reads(self) -> None:
        # The ItsyBitsy 32u4 firmware in this repo has no sensor code at all -
        # these must fail fast without touching the serial port.
        controller = _connected(ItsyBitsy32U4ValveController())

        with self.assertRaises(ControllerError):
            controller.read_ambient_temperature()
        with self.assertRaises(ControllerError):
            controller.read_humidity()
        controller._serial.write.assert_not_called()


class ExecuteCommandDispatchTests(unittest.TestCase):
    def test_switch_read_ambient_temperature_dispatches(self) -> None:
        controller = _connected(ArduinoValveController())
        controller._serial.readline.return_value = b"22.1\n"

        result = controller.execute_command(DeviceCommand("switch.read_ambient_temperature", {}))

        self.assertEqual(result, 22.1)

    def test_valve_read_humidity_dispatches(self) -> None:
        controller = _connected(ArduinoValveController())
        controller._serial.readline.return_value = b"55.0\n"

        result = controller.execute_command(DeviceCommand("valve.read_humidity", {}))

        self.assertEqual(result, 55.0)

    def test_unsupported_controller_raises_via_dispatch(self) -> None:
        controller = _connected(ItsyBitsy32U4ValveController())

        with self.assertRaises(ControllerError):
            controller.execute_command(DeviceCommand("switch.read_humidity", {}))


if __name__ == "__main__":
    unittest.main()
