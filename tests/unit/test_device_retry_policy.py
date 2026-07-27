"""Unit tests for the device-layer retry/reconnect policy added to
SerialController (shared by RegloICCClient and the valve/switch
controllers) and AMFSwitchController.

Uses a MagicMock in place of a real pyserial.Serial handle so no hardware
is required. See device/serial_controllers.py's _call_with_retry and
device/amf_mswitch.py's _call_with_retry for the implementation.
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

import serial

from lspr_app.device.amf_mswitch import AMFSwitchController
from lspr_app.device.device_driver import DeviceTimeoutError
from lspr_app.device.reglo_icc import RegloICCClient
from lspr_app.device.serial_controllers import ControllerError, ControllerProbe, SerialController


class _FakeController(SerialController):
    controller_type = "fake"
    _RETRY_DELAY_S = 0.0  # keep tests fast

    def get_probe(self) -> ControllerProbe:
        return ControllerProbe(port="", controller_type="fake", model="fake")


def _connected_fake_controller() -> _FakeController:
    controller = _FakeController()
    controller._serial = MagicMock()
    controller._serial.is_open = True
    controller.port = "FAKE"
    return controller


class SerialControllerRetryTests(unittest.TestCase):
    def test_query_retries_on_timeout_then_raises(self) -> None:
        controller = _connected_fake_controller()
        controller._serial.readline.return_value = b""  # never responds
        attempt_count = {"n": 0}
        original_read_line = controller._read_line

        def counting_read_line(*args, **kwargs):
            attempt_count["n"] += 1
            return original_read_line(*args, **kwargs)

        controller._read_line = counting_read_line

        with self.assertRaises(DeviceTimeoutError):
            controller.query("test", max_wait_s=0.01)

        # One call to _read_line per retry attempt - readline() itself is
        # called many times per attempt (it spins until max_wait_s elapses),
        # so counting that would measure the busy-wait, not the retry count.
        self.assertEqual(attempt_count["n"], controller._MAX_COMMAND_ATTEMPTS)

    def test_query_recovers_after_a_transient_timeout(self) -> None:
        controller = _connected_fake_controller()
        call_count = {"n": 0}

        def flaky_readline():
            call_count["n"] += 1
            return b"" if call_count["n"] < 2 else b"OK\n"

        controller._serial.readline.side_effect = flaky_readline

        result = controller.query("test", max_wait_s=0.01)

        self.assertEqual(result, "OK")
        self.assertEqual(call_count["n"], 2)

    def test_explicit_rejection_is_not_retried(self) -> None:
        # set_position on the base class raises ControllerError immediately
        # (unsupported) without touching the serial port at all - confirms
        # _call_with_retry's plain-ControllerError path takes exactly one
        # attempt, not _MAX_COMMAND_ATTEMPTS.
        controller = _connected_fake_controller()
        with self.assertRaises(ControllerError):
            controller.set_position("anything")
        controller._serial.write.assert_not_called()

    def test_dropped_connection_triggers_one_reconnect_attempt(self) -> None:
        # If the reconnect itself also fails, _call_with_retry gives up
        # immediately rather than burning through the remaining attempts
        # (each of which would just hit "not connected" instead of the real
        # cause) - see the "raise exc from None" path in _call_with_retry.
        controller = _connected_fake_controller()
        controller._serial.write.side_effect = serial.SerialException("device disconnected")
        reconnect_calls = []

        def spy_connect(port):
            reconnect_calls.append(port)
            raise serial.SerialException("still gone")

        controller.connect = spy_connect
        with self.assertRaises(serial.SerialException) as ctx:
            controller.query("test", max_wait_s=0.01)

        self.assertEqual(len(reconnect_calls), 1)
        # The original, more informative error propagates - not the
        # reconnect attempt's own failure.
        self.assertIn("device disconnected", str(ctx.exception))

    def test_reconnect_succeeds_then_retry_succeeds(self) -> None:
        controller = _connected_fake_controller()
        state = {"attempt": 0}

        def flaky_write(_data):
            state["attempt"] += 1
            if state["attempt"] == 1:
                raise serial.SerialException("device disconnected")

        controller._serial.write.side_effect = flaky_write
        controller._serial.readline.return_value = b"OK\n"

        def spy_connect(port):
            # Simulate a successful reopen: give the controller a fresh,
            # working serial mock.
            controller._serial = MagicMock()
            controller._serial.is_open = True
            controller._serial.write.side_effect = flaky_write
            controller._serial.readline.return_value = b"OK\n"
            controller.port = port

        controller.connect = spy_connect

        result = controller.query("test", max_wait_s=0.01)

        self.assertEqual(result, "OK")


class RegloICCSharesSerialControllerTests(unittest.TestCase):
    def test_reglo_icc_client_is_a_serial_controller(self) -> None:
        client = RegloICCClient()
        self.assertIsInstance(client, SerialController)
        self.assertEqual(client.controller_type, "reglo-icc")
        self.assertEqual(client._BAUD_RATE, 9600)
        self.assertFalse(client.is_connected())

    def test_pump_send_retries_on_timeout(self) -> None:
        client = RegloICCClient()
        client._serial = MagicMock()
        client._serial.is_open = True
        client.port = "FAKE"
        client._serial.read_all.return_value = b""  # never responds
        client._RETRY_DELAY_S = 0.0
        attempt_count = {"n": 0}
        original_send_once = client._send_once

        def counting_send_once(*args, **kwargs):
            attempt_count["n"] += 1
            return original_send_once(*args, **kwargs)

        client._send_once = counting_send_once

        with self.assertRaises(DeviceTimeoutError):
            client.send("0x!", max_wait_s=0.01)

        self.assertEqual(attempt_count["n"], client._MAX_COMMAND_ATTEMPTS)

    def test_pump_rejection_is_not_retried(self) -> None:
        client = RegloICCClient()
        client._serial = MagicMock()
        client._serial.is_open = True
        client.port = "FAKE"
        client._serial.read_all.return_value = b"#"
        client._RETRY_DELAY_S = 0.0

        with self.assertRaises(Exception) as ctx:
            client.send("0H", max_wait_s=0.01)

        self.assertIn("rejected", str(ctx.exception))
        # Only one attempt: rejection is a real answer, not a lost response.
        self.assertEqual(client._serial.read_all.call_count, 1)


class AMFSwitchRetryTests(unittest.TestCase):
    def test_move_to_invalid_position_fails_fast_without_retry(self) -> None:
        controller = AMFSwitchController()
        controller._RETRY_DELAY_S = 0.0
        with self.assertRaises(Exception):
            controller.move_to(0)
        # No amf handle was ever touched - validation happens before any
        # retry-wrapped device call.
        self.assertIsNone(controller._amf)

    def test_get_position_retries_transient_failures(self) -> None:
        controller = AMFSwitchController()
        controller._RETRY_DELAY_S = 0.0
        controller.port = "FAKE"
        fake_amf = MagicMock()
        call_count = {"n": 0}

        def flaky_get_position():
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise RuntimeError("transient AMF error")
            return 3

        fake_amf.getValvePosition.side_effect = flaky_get_position
        controller._amf = fake_amf
        # Prevent the reconnect branch from touching real hardware - it
        # should still be exercised (port is set) but must not raise.
        controller.connect = MagicMock()
        controller.close = MagicMock()

        result = controller.get_position()

        self.assertEqual(result, 3)
        self.assertEqual(call_count["n"], 2)


if __name__ == "__main__":
    unittest.main()
