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
from lspr_app.device.serial_controllers import ControllerError, ControllerPort, controller_port_priority
from lspr_app.device.valve_controllers import ArduinoValveController, ItsyBitsy32U4ValveController, LegacyValveController


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


class IsProbablePortTests(unittest.TestCase):
    """Regression coverage for a real board seen in the field: an
    Arduino-family valve controller (firmware answers asn/mod/at/ah
    correctly) wired through an FTDI FT232R USB-serial adapter instead of
    CH340 or native USB, so it doesn't say "Arduino" anywhere and its VID
    is FTDI's (0403), not Arduino LLC's (2341) or QinHeng's (1A86). Before
    this was recognized, only LegacyValveController matched the port, was
    tried alone, and failed (it doesn't speak the "vi" legacy protocol) -
    detection failed outright even though ArduinoValveController would
    have worked immediately.
    """

    def _ftdi_port(self) -> ControllerPort:
        return ControllerPort(
            device="COM14",
            description="USB Serial Port (COM14)",
            hwid="USB VID:PID=0403:6001 SER=AM00PFW2A",
        )

    def test_arduino_valve_controller_recognizes_ftdi_vid(self) -> None:
        self.assertTrue(ArduinoValveController.is_probable_port(self._ftdi_port()))

    def test_itsybitsy_does_not_claim_a_plain_ftdi_port(self) -> None:
        self.assertFalse(ItsyBitsy32U4ValveController.is_probable_port(self._ftdi_port()))

    def test_ftdi_vid_still_excluded_when_itsybitsy_vid_also_present(self) -> None:
        # 239A (Adafruit/ItsyBitsy) must still win the exclusion even if a
        # hwid string somehow also mentions FTDI-like text.
        port = ControllerPort(device="COM5", description="USB Serial Device", hwid="USB VID:PID=239A:8071")
        self.assertFalse(ArduinoValveController.is_probable_port(port))

    def test_arduino_outranks_legacy_for_an_ftdi_port(self) -> None:
        # Both classes now match an FTDI-VID port - Arduino's higher
        # priority (20 vs 10) must make it the one actually tried first.
        port = self._ftdi_port()
        self.assertTrue(ArduinoValveController.is_probable_port(port))
        self.assertTrue(LegacyValveController.is_probable_port(port))
        self.assertEqual(controller_port_priority(port), ArduinoValveController.priority)


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
