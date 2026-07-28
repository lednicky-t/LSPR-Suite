"""Pump-display feature coverage:

- ``sanitize_pump_display_text`` filters to printable ASCII and truncates to
  the pump's 16-character display width (Reglo ICC manual, section 14.6.13
  "String": printable ASCII only, no embedded request-delimiter/CR).
- ``RegloICCClient.set_display_text`` sends the sanitized text via the
  documented ``xN`` ("Set pump's temporary display name") command,
  addressed to pump 0 - the only command of the two plausible candidates
  (``xN`` vs the originally-tried ``DA``) with an actual worked example in
  the manual for this exact use case (section 18.6.2).

Whether the pump display is shown at all, and whether the plan table
highlights the 16-character limit, are global ExperimentControlWindow
settings (``_pump_display_enabled``/``_pump_display_highlight_enabled``),
not per-step ``PumpPlanStep`` fields - see "Pump Display" in
docs/experiment-control/pump_control_guide.md. That GUI-level behavior isn't
covered here since it needs a live QApplication/window.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.device.reglo_icc import PUMP_DISPLAY_MAX_LENGTH, RegloICCClient, sanitize_pump_display_text
from lspr_app.device.communication_models import DeviceCommand


class SanitizePumpDisplayTextTests(unittest.TestCase):
    def test_truncates_to_16_characters(self) -> None:
        text = "This comment is much longer than sixteen characters"
        result = sanitize_pump_display_text(text)
        self.assertEqual(len(result), PUMP_DISPLAY_MAX_LENGTH)
        self.assertEqual(result, text[:PUMP_DISPLAY_MAX_LENGTH])

    def test_short_text_is_unchanged(self) -> None:
        self.assertEqual(sanitize_pump_display_text("Rinse buffer"), "Rinse buffer")

    def test_strips_non_printable_and_control_characters(self) -> None:
        # \r would terminate the request early; \n and \t aren't printable ASCII.
        self.assertEqual(sanitize_pump_display_text("A\r\nB\tC"), "ABC")

    def test_strips_non_ascii_characters(self) -> None:
        self.assertEqual(sanitize_pump_display_text("café µL/min"), "caf L/min")

    def test_keeps_printable_ascii_punctuation(self) -> None:
        text = "Step 1/5: pH=7.4 (ok)"
        self.assertEqual(sanitize_pump_display_text(text), text[:PUMP_DISPLAY_MAX_LENGTH])

    def test_empty_input(self) -> None:
        self.assertEqual(sanitize_pump_display_text(""), "")

    def test_custom_max_length(self) -> None:
        self.assertEqual(sanitize_pump_display_text("abcdefgh", max_length=4), "abcd")


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

    def flush(self) -> None:
        pass

    def read_all(self) -> bytes:
        response, self._pending_response = self._pending_response, b""
        return response


class RegloICCSetDisplayTextTests(unittest.TestCase):
    def _client_with_fake_serial(self) -> tuple[RegloICCClient, _FakeSerial]:
        client = RegloICCClient()
        fake_serial = _FakeSerial()
        client._serial = fake_serial  # bypass connect(); no real port needed
        client.port = "FAKE"
        return client, fake_serial

    def test_sends_xn_command_addressed_to_pump_zero(self) -> None:
        client, fake_serial = self._client_with_fake_serial()
        client.set_display_text("Rinse buffer")
        self.assertEqual(fake_serial.written, [b"0xNRinse buffer\r"])

    def test_truncates_and_sanitizes_before_sending(self) -> None:
        client, fake_serial = self._client_with_fake_serial()
        client.set_display_text("This comment is much longer than sixteen characters")
        self.assertEqual(fake_serial.written, [b"0xNThis comment is \r"])

    def test_execute_command_dispatches_pump_set_display(self) -> None:
        client, fake_serial = self._client_with_fake_serial()
        client.execute_command(DeviceCommand("pump.set_display", {"text": "Wash"}))
        self.assertEqual(fake_serial.written, [b"0xNWash\r"])


if __name__ == "__main__":
    unittest.main()
