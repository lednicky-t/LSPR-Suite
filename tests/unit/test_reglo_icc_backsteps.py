"""Coverage for the pump's roller-backstep and roller-count settings.

Backsteps (manual sec. 6.4.3 / 16.2, ref. 4.3-4.4): the "%" command sets the
backstep count, 0-100, using the protocol's Discrete Type 2 format (4-digit,
right-justified, zero-padded).

Roller count (manual sec. 6.12-6.13): the "xB" command sets the number of
rollers on the installed cassette head (6, 8, or 12 - manual sec. 6.2's "xt"
command lists these as the only valid counts), same Discrete Type 2 format.

Both are sent on every channel configure alongside the existing tube-diameter
("+") command - see RegloICCClient.configure_channel/set_backsteps/
set_roller_count. Order sent: tube diameter, roller count, backsteps.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.device.communication_models import DeviceCommand
from lspr_app.device.reglo_icc import RegloICCClient


class _FakeSerial:
    """Minimal stand-in for ``serial.Serial`` that answers every write with '*'."""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self._pending_response = b"*\r"

    def reset_input_buffer(self) -> None:
        pass

    def reset_output_buffer(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        self.written.append(data)
        self._pending_response = b"*\r"

    def flush(self) -> None:
        pass

    def read_all(self) -> bytes:
        response, self._pending_response = self._pending_response, b""
        return response


def _client_with_fake_serial() -> tuple[RegloICCClient, _FakeSerial]:
    client = RegloICCClient()
    fake_serial = _FakeSerial()
    client._serial = fake_serial  # bypass connect(); no real port needed
    client.port = "FAKE"
    return client, fake_serial


class SetBackstepsTests(unittest.TestCase):
    def test_sends_percent_command_zero_padded(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.set_backsteps(2, 7)
        self.assertEqual(fake_serial.written, [b"2%0007\r"])

    def test_clamps_negative_to_zero(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.set_backsteps(1, -5)
        self.assertEqual(fake_serial.written, [b"1%0000\r"])

    def test_clamps_above_100_to_100(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.set_backsteps(1, 250)
        self.assertEqual(fake_serial.written, [b"1%0100\r"])


class SetRollerCountTests(unittest.TestCase):
    def test_sends_xb_command_zero_padded(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.set_roller_count(2, 12)
        self.assertEqual(fake_serial.written, [b"2xB0012\r"])

    def test_accepts_all_valid_roller_counts(self) -> None:
        for roller_count in (6, 8, 12):
            client, fake_serial = _client_with_fake_serial()
            client.set_roller_count(1, roller_count)
            self.assertEqual(fake_serial.written, [f"1xB{roller_count:04d}\r".encode("ascii")])

    def test_falls_back_to_default_for_unsupported_count(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.set_roller_count(1, 10)
        self.assertEqual(fake_serial.written, [b"1xB0008\r"])


class ConfigureChannelSendsBackstepsAndRollerCountTests(unittest.TestCase):
    def test_configure_channel_sends_tube_diameter_then_roller_count_then_backsteps(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.configure_channel(3, 0.0, "OFF", 0.25, backsteps=10, roller_count=12)
        self.assertEqual(fake_serial.written, [b"3+0025\r", b"3xB0012\r", b"3%0010\r"])

    def test_configure_channel_defaults_backsteps_to_zero_and_roller_count_to_eight(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.configure_channel(1, 0.0, "OFF", 0.25)
        self.assertEqual(fake_serial.written, [b"1+0025\r", b"1xB0008\r", b"1%0000\r"])

    def test_execute_command_pump_set_flow_threads_backsteps_and_roller_count(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.execute_command(
            DeviceCommand(
                "pump.set_flow",
                {
                    "channel": 2, "flow_ul_min": 0.0, "direction": "OFF", "tube_mm": 0.13,
                    "backsteps": 42, "roller_count": 6,
                },
            )
        )
        self.assertEqual(fake_serial.written, [b"2+0013\r", b"2xB0006\r", b"2%0042\r", b"2I\r"])

    def test_execute_command_pump_set_flow_without_overrides_uses_defaults(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.execute_command(
            DeviceCommand(
                "pump.set_flow",
                {"channel": 2, "flow_ul_min": 0.0, "direction": "OFF", "tube_mm": 0.13},
            )
        )
        self.assertEqual(fake_serial.written, [b"2+0013\r", b"2xB0008\r", b"2%0000\r", b"2I\r"])


if __name__ == "__main__":
    unittest.main()
