"""Coverage for the pump's calibration commands (manual sec. 6.4.4 / 16.2
ref 5.0, 18.5) and the roller-step-volume constant (ref 6.33/6.34).

The front-panel/manual procedure is one-channel-at-a-time and interactive:
configure a target volume/duration/direction, start the run (dispenses
fluid), then hand-enter the actual measured volume, which recomputes the
pump's internal roller-step volume for that channel. RegloICCClient exposes
each step individually, plus direct get/set access to the resulting
roller-step-volume constant ("r") so it can be written without running the
interactive procedure at all.
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
from lspr_app.device.reglo_icc import RegloICCClient, RegloICCError


class _FakeSerial:
    """Minimal stand-in for ``serial.Serial``. Responses are queued via
    ``queue_response`` (as raw bytes including the terminator); each write
    consumes the next queued response, defaulting to '*' if none is queued."""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self._responses: list[bytes] = []
        self._pending_response = b""

    def queue_response(self, response: bytes) -> None:
        self._responses.append(response)

    def reset_input_buffer(self) -> None:
        pass

    def reset_output_buffer(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        self.written.append(data)
        self._pending_response = self._responses.pop(0) if self._responses else b"*\r"

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


class VolumeType1DecodeTests(unittest.TestCase):
    def test_decodes_positive_exponent(self) -> None:
        self.assertAlmostEqual(RegloICCClient._decode_volume_type1("1000E+2"), 100.0)

    def test_decodes_negative_exponent(self) -> None:
        self.assertAlmostEqual(RegloICCClient._decode_volume_type1("1300E-3"), 1.3e-3)

    def test_round_trips_via_simulated_response_format(self) -> None:
        # _encode_volume_type2 targets the Set-request format, which omits
        # the "E" character the Get/response format (decoded here) requires
        # (see _encode_volume_type2's docstring) - simulate what the pump's
        # own response would look like by inserting "E" before the sign.
        for value in (0.0002, 1.3, 100.0, 3596.4, 35000.0):
            encoded = RegloICCClient._encode_volume_type2(value)
            mantissa_digits, sign_and_exponent = encoded[:4], encoded[4:]
            simulated_response = f"{mantissa_digits}E{sign_and_exponent}"
            decoded = RegloICCClient._decode_volume_type1(simulated_response)
            self.assertAlmostEqual(decoded, value, delta=max(value * 1e-3, 1e-6))

    def test_encoder_output_omits_e_character(self) -> None:
        # Documents the asymmetry: Set requests (sec. 18.3.3 worked example)
        # have no "E" between mantissa and exponent, unlike the Get/response
        # format decoded by _decode_volume_type1.
        self.assertEqual(RegloICCClient._encode_volume_type2(0.0013), "1300-3")

    def test_malformed_response_raises(self) -> None:
        with self.assertRaises(RegloICCError):
            RegloICCClient._decode_volume_type1("not-a-volume")


class TimeTypeEncodeDecodeTests(unittest.TestCase):
    def test_encode_uses_tenths_of_a_second(self) -> None:
        self.assertEqual(RegloICCClient._encode_time_type(30.0), "00000300")

    def test_decode_uses_tenths_of_a_second(self) -> None:
        self.assertAlmostEqual(RegloICCClient._decode_time_type_seconds("00000300"), 30.0)

    def test_round_trips(self) -> None:
        for seconds in (0.0, 0.1, 30.0, 3600.0, 359640.0):
            self.assertAlmostEqual(
                RegloICCClient._decode_time_type_seconds(RegloICCClient._encode_time_type(seconds)), seconds,
            )

    def test_clamps_to_max_representable_duration(self) -> None:
        # 999 hours = 3,596,400 s = 35,964,000 tenths-of-a-second (manual
        # sec. 14.6.11/14.6.12's stated range).
        self.assertEqual(RegloICCClient._encode_time_type(10_000_000.0), "35964000")

    def test_decode_empty_string_is_zero(self) -> None:
        self.assertEqual(RegloICCClient._decode_time_type_seconds(""), 0.0)


class CalibrationDirectionTests(unittest.TestCase):
    def test_get_direction_cw(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"J\r")
        self.assertEqual(client.get_calibration_direction(2), "CW")
        self.assertEqual(fake_serial.written, [b"2xR\r"])

    def test_get_direction_ccw(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"K\r")
        self.assertEqual(client.get_calibration_direction(1), "CCW")

    def test_set_direction_cw_sends_j(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.set_calibration_direction(1, "CW")
        self.assertEqual(fake_serial.written, [b"1xRJ\r"])

    def test_set_direction_ccw_sends_k(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.set_calibration_direction(1, "CCW")
        self.assertEqual(fake_serial.written, [b"1xRK\r"])


class CalibrationVolumeAndTimeTests(unittest.TestCase):
    def test_get_target_volume(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"1000E+2\r")
        self.assertAlmostEqual(client.get_calibration_target_volume_ml(3), 100.0)
        self.assertEqual(fake_serial.written, [b"3xU\r"])

    def test_set_target_volume_sends_encoded_value_and_returns_confirmed_volume(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"1000E+2\r")
        confirmed = client.set_calibration_target_volume_ml(4, 100.0)
        self.assertEqual(fake_serial.written, [b"4xU1000+2\r"])
        self.assertAlmostEqual(confirmed, 100.0)

    def test_set_measured_volume_sends_encoded_value_and_returns_confirmed_volume(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"1010E+2\r")
        confirmed = client.set_calibration_measured_volume_ml(4, 101.0)
        self.assertEqual(fake_serial.written, [b"4xV1010+2\r"])
        self.assertAlmostEqual(confirmed, 101.0)

    def test_get_and_set_calibration_time(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.set_calibration_time_s(1, 30.0)
        self.assertEqual(fake_serial.written, [b"1xW00000300\r"])

        fake_serial.queue_response(b"00000300\r")
        self.assertAlmostEqual(client.get_calibration_time_s(3), 30.0)

    def test_time_since_last_calibration(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"03596400\r")
        # 3,596,400 raw units * 0.1 s/unit = 359,640.0 s (~99.9 h) under our
        # formal-spec decoding - see _decode_time_type_seconds's note; the
        # manual's own worked example for this exact response describes it
        # as 999 hours instead, a 10x discrepancy. Verify against real
        # hardware before trusting either number.
        self.assertAlmostEqual(client.get_time_since_last_calibration_s(3), 359_640.0)
        self.assertEqual(fake_serial.written, [b"3xX\r"])


class CalibrationRunControlTests(unittest.TestCase):
    def test_start_calibration_sends_xy(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.start_calibration(2)
        self.assertEqual(fake_serial.written, [b"2xY\r"])

    def test_cancel_calibration_sends_xz(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.cancel_calibration(2)
        self.assertEqual(fake_serial.written, [b"2xZ\r"])


class StartFailureDiagnosisTests(unittest.TestCase):
    """Coverage for the "-" ("cannot run") response documented in manual
    sec. 15.1, and the "xe" diagnostic (sec. 2.7) that explains it - used by
    start_channel ("H") and start_calibration ("xY")."""

    def test_get_start_failure_reason_decodes_cause_and_limit(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"R R\r")
        reason = client.get_start_failure_reason(1)
        self.assertIn("flow rate", reason)
        self.assertIn("limited by max achievable flow rate", reason)
        self.assertEqual(fake_serial.written, [b"1xe\r"])

    def test_get_start_failure_reason_handles_cycle_count_cause(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"C C\r")
        reason = client.get_start_failure_reason(1)
        self.assertIn("cycle count is 0", reason)

    def test_get_start_failure_reason_handles_unrecognized_code(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"Z\r")
        reason = client.get_start_failure_reason(1)
        self.assertIn("unrecognized cause code", reason)

    def test_start_calibration_raises_with_diagnosis_on_dash_response(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"-\r")  # response to "1xY"
        fake_serial.queue_response(b"R R\r")  # response to the follow-up "1xe"
        with self.assertRaises(RegloICCError) as ctx:
            client.start_calibration(1)
        self.assertIn("refused to start channel 1", str(ctx.exception))
        self.assertIn("flow rate", str(ctx.exception))
        self.assertEqual(fake_serial.written, [b"1xY\r", b"1xe\r"])

    def test_start_calibration_raises_even_if_xe_itself_fails(self) -> None:
        # The follow-up "xe" query is best-effort - if it also fails, the
        # original "-" failure must still be raised (with a fallback hint)
        # rather than masking it with an unrelated exception.
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"-\r")
        fake_serial.queue_response(b"#\r")  # "xe" itself rejected
        with self.assertRaises(RegloICCError) as ctx:
            client.start_calibration(1)
        self.assertIn("refused to start channel 1", str(ctx.exception))

    def test_start_channel_also_uses_dash_diagnosis(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"-\r")
        fake_serial.queue_response(b"V C\r")
        with self.assertRaises(RegloICCError) as ctx:
            client.start_channel(3)
        self.assertIn("refused to start channel 3", str(ctx.exception))
        self.assertIn("exceeds the max", str(ctx.exception))
        self.assertEqual(fake_serial.written, [b"3H\r", b"3xe\r"])

    def test_execute_command_get_start_failure_reason(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"R R\r")
        result = client.execute_command(DeviceCommand("pump.get_start_failure_reason", {"channel": 1}))
        self.assertIn("flow rate", result)


class RollerStepVolumeTests(unittest.TestCase):
    def test_get_roller_step_volume(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"1234E-2\r")
        self.assertAlmostEqual(client.get_roller_step_volume_ml(1), 0.01234)
        self.assertEqual(fake_serial.written, [b"1r\r"])

    def test_set_roller_step_volume_direct_without_running_calibration(self) -> None:
        # This is the key capability for calibrating all channels in one
        # physical run: dispense every channel simultaneously, measure each
        # channel's real output by hand, compute each channel's own
        # roller-step-volume constant, then push it here - no interactive
        # per-channel dispense-then-measure procedure required.
        client, fake_serial = _client_with_fake_serial()
        client.set_roller_step_volume_ml(2, 0.01234)
        self.assertEqual(fake_serial.written, [b"2r1234-2\r"])


class ExecuteCommandCalibrationDispatchTests(unittest.TestCase):
    def test_get_direction(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"J\r")
        result = client.execute_command(DeviceCommand("pump.calibration.get_direction", {"channel": 1}))
        self.assertEqual(result, "CW")

    def test_set_direction(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.execute_command(DeviceCommand("pump.calibration.set_direction", {"channel": 1, "direction": "CCW"}))
        self.assertEqual(fake_serial.written, [b"1xRK\r"])

    def test_set_target_volume_returns_confirmed_value(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"1000E+2\r")
        result = client.execute_command(
            DeviceCommand("pump.calibration.set_target_volume_ml", {"channel": 1, "volume_ml": 100.0})
        )
        self.assertAlmostEqual(result, 100.0)

    def test_set_measured_volume_returns_confirmed_value(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"1010E+2\r")
        result = client.execute_command(
            DeviceCommand("pump.calibration.set_measured_volume_ml", {"channel": 1, "volume_ml": 101.0})
        )
        self.assertAlmostEqual(result, 101.0)

    def test_time_since_last_s(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"00003600\r")
        result = client.execute_command(DeviceCommand("pump.calibration.time_since_last_s", {"channel": 2}))
        self.assertAlmostEqual(result, 360.0)

    def test_start_and_cancel(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        client.execute_command(DeviceCommand("pump.calibration.start", {"channel": 3}))
        client.execute_command(DeviceCommand("pump.calibration.cancel", {"channel": 3}))
        self.assertEqual(fake_serial.written, [b"3xY\r", b"3xZ\r"])

    def test_roller_step_volume_get_and_set(self) -> None:
        client, fake_serial = _client_with_fake_serial()
        fake_serial.queue_response(b"1234E-2\r")
        result = client.execute_command(DeviceCommand("pump.roller_step_volume.get", {"channel": 1}))
        self.assertAlmostEqual(result, 0.01234)

        client.execute_command(DeviceCommand("pump.roller_step_volume.set", {"channel": 1, "volume_ml": 0.02}))
        self.assertEqual(fake_serial.written[-1], b"1r2000-2\r")


if __name__ == "__main__":
    unittest.main()
