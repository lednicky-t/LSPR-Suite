"""Pump-display feature coverage:

- ``sanitize_pump_display_text`` filters to printable ASCII and truncates to
  the pump's 16-character display width (Reglo ICC manual, section 14.6.13
  "String": printable ASCII only, no embedded request-delimiter/CR).
- ``PumpPlanStep.show_on_pump_display`` round-trips through the
  ``to_core_experiment_step``/``from_core_experiment_step`` conversion used
  for HDF5 export/import and plan retiming.
- ``RegloICCClient.set_display_text`` sends the sanitized text via the
  documented ``DA`` (write letters) command, addressed to pump 0.
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
from lspr_app.domain.pump_plan import PumpPlanStep, from_core_experiment_step, to_core_experiment_step


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


class PumpPlanStepShowOnPumpDisplayRoundTripTests(unittest.TestCase):
    def test_round_trips_through_core_step(self) -> None:
        step = PumpPlanStep(step=1, description="Load sample", show_on_pump_display=True)
        core_step = to_core_experiment_step(step)
        self.assertTrue(core_step.devices["show_on_pump_display"])

        restored = from_core_experiment_step(core_step)
        self.assertTrue(restored.show_on_pump_display)

    def test_defaults_to_false(self) -> None:
        step = PumpPlanStep(step=1, description="Load sample")
        core_step = to_core_experiment_step(step)
        self.assertFalse(core_step.devices["show_on_pump_display"])

        restored = from_core_experiment_step(core_step)
        self.assertFalse(restored.show_on_pump_display)

    def test_missing_devices_key_defaults_to_template_value(self) -> None:
        # Older plans (or a template step from before this field existed) won't have
        # the key at all - from_core_experiment_step must not crash, and should keep
        # whatever the template (destination step) already had.
        step = PumpPlanStep(step=1)
        core_step = to_core_experiment_step(step)
        del core_step.devices["show_on_pump_display"]
        template = PumpPlanStep(step=1, show_on_pump_display=True)
        restored = from_core_experiment_step(core_step, template=template)
        self.assertTrue(restored.show_on_pump_display)


class PumpPlanStepHighlightPumpDisplayLimitRoundTripTests(unittest.TestCase):
    def test_round_trips_through_core_step(self) -> None:
        step = PumpPlanStep(
            step=1, description="Load sample", show_on_pump_display=True, highlight_pump_display_limit=True
        )
        core_step = to_core_experiment_step(step)
        self.assertTrue(core_step.devices["highlight_pump_display_limit"])

        restored = from_core_experiment_step(core_step)
        self.assertTrue(restored.highlight_pump_display_limit)

    def test_defaults_to_false(self) -> None:
        step = PumpPlanStep(step=1, description="Load sample", show_on_pump_display=True)
        core_step = to_core_experiment_step(step)
        self.assertFalse(core_step.devices["highlight_pump_display_limit"])

        restored = from_core_experiment_step(core_step)
        self.assertFalse(restored.highlight_pump_display_limit)

    def test_auto_deactivates_when_show_on_pump_display_is_false(self) -> None:
        # The highlight can't outlive the main toggle - a step with the display send
        # off must never persist with the highlight still on, at either conversion step.
        step = PumpPlanStep(
            step=1, description="Load sample", show_on_pump_display=False, highlight_pump_display_limit=True
        )
        core_step = to_core_experiment_step(step)
        self.assertFalse(core_step.devices["highlight_pump_display_limit"])

        restored = from_core_experiment_step(core_step)
        self.assertFalse(restored.highlight_pump_display_limit)

    def test_missing_devices_key_defaults_to_template_value_when_show_on_pump_display_true(self) -> None:
        step = PumpPlanStep(step=1, show_on_pump_display=True)
        core_step = to_core_experiment_step(step)
        del core_step.devices["highlight_pump_display_limit"]
        template = PumpPlanStep(step=1, show_on_pump_display=True, highlight_pump_display_limit=True)
        restored = from_core_experiment_step(core_step, template=template)
        self.assertTrue(restored.highlight_pump_display_limit)


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

    def test_sends_da_command_addressed_to_pump_zero(self) -> None:
        client, fake_serial = self._client_with_fake_serial()
        client.set_display_text("Rinse buffer")
        self.assertEqual(fake_serial.written, [b"0DARinse buffer\r"])

    def test_truncates_and_sanitizes_before_sending(self) -> None:
        client, fake_serial = self._client_with_fake_serial()
        client.set_display_text("This comment is much longer than sixteen characters")
        self.assertEqual(fake_serial.written, [b"0DAThis comment is \r"])

    def test_execute_command_dispatches_pump_set_display(self) -> None:
        client, fake_serial = self._client_with_fake_serial()
        client.execute_command(DeviceCommand("pump.set_display", {"text": "Wash"}))
        self.assertEqual(fake_serial.written, [b"0DAWash\r"])


if __name__ == "__main__":
    unittest.main()
