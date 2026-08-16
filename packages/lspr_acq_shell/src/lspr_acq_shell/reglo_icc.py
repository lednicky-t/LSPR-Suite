"""Ismatec Reglo ICC peristaltic pump driver.

Extracted from singleLSPR Acquisition's `device/reglo_icc.py` (Phase 1,
2026-08-08), part of the fluidics device framework verbatim move (§4.2).

`ACTIVE_PUMP_CHANNELS`/`VALID_ROLLER_COUNTS`/`DEFAULT_ROLLER_COUNT` moved here
too (previously in `domain/pump_plan.py`, a plan/domain file - these are
pump-hardware facts, not plan-execution facts, so this driver module is their
more natural owner). sLSPR acq's `pump_plan.py` now imports them from here
instead of defining them, so every existing consumer (GUI plan-table/dialog
code) is unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor, log10
from time import monotonic, sleep

from lspr_acq_shell.communication_models import DeviceCommand
from lspr_acq_shell.device_driver import DeviceError, DeviceTimeoutError
from lspr_acq_shell.serial_controllers import SerialController
from serial.tools import list_ports

# Pump-hardware constants (moved from sLSPR acq's domain/pump_plan.py, see
# module docstring above).
ACTIVE_PUMP_CHANNELS = 4

# Reglo ICC cassette heads are interchangeable and come in 6/8/12-roller
# variants (manual sec. 6.2's "xt" command lists these as the only valid
# roller counts). 8 is the standard head, and the one the manual's own
# Tubing Size and Flow Rate Chart (sec. 13) is computed for.
VALID_ROLLER_COUNTS = (6, 8, 12)
DEFAULT_ROLLER_COUNT = 8


@dataclass(slots=True)
class PumpPort:
    device: str
    description: str
    hwid: str


@dataclass(slots=True)
class PumpProbe:
    port: str
    protocol_version: str
    serial_number: str
    channel_count: int
    model: str


class RegloICCError(DeviceError):
    pass


PUMP_DISPLAY_MAX_LENGTH = 16


def sanitize_pump_display_text(text: str, max_length: int = PUMP_DISPLAY_MAX_LENGTH) -> str:
    """Reduce *text* to what the pump's display protocol will actually accept.

    Per the Reglo ICC manual's ``String`` data type (used by the ``D``/``DA``
    display-write commands): only printable ASCII (0x20-0x7E) is allowed, and
    the request delimiter (carriage return) cannot appear in the value. The
    physical display is also a single ~16-character line, so the result is
    truncated to *max_length*.
    """
    cleaned = "".join(ch for ch in str(text or "") if 0x20 <= ord(ch) <= 0x7E)
    return cleaned[: max(int(max_length), 0)]


def is_probable_reglo_port(port: PumpPort) -> bool:
    description = port.description.upper()
    hwid = port.hwid.upper()
    return (
        "265C:0001" in hwid
        or "ISMATEC" in description
        or "REGLO" in description
        or "ISMATEC" in hwid
        or "REGLO" in hwid
    )


class RegloICCClient(SerialController):
    # Shares SerialController's connect()/close()/is_connected()/claim-owner
    # lifecycle (see serial_controllers.py) instead of reimplementing the
    # same claim-port-then-open-serial-then-rollback-on-failure pattern
    # independently - this used to be a near-verbatim copy of it. Note: a
    # busy port now raises ControllerError (from serial_controllers.py)
    # rather than RegloICCError, since that check now happens in the shared
    # base class; nothing in this codebase catches RegloICCError specifically
    # for that case, only DeviceError more broadly.
    controller_type = "reglo-icc"
    _BAUD_RATE = 9600
    _TIMEOUT = 0.35

    @staticmethod
    def list_ports() -> list[PumpPort]:
        return [
            PumpPort(device=port.device, description=port.description, hwid=port.hwid)
            for port in list_ports.comports()
            if str(getattr(port, "device", "") or "").strip()
        ]

    @classmethod
    def probe_port(cls, port: str) -> PumpProbe:
        client = cls()
        try:
            client.connect(port)
            return client.get_probe()
        finally:
            client.close()

    def get_probe(self) -> PumpProbe:
        # Try broadcast address (0) first; fall back to pump address 1 if rejected.
        # Some Reglo ICC units in single-pump RS-232 mode only answer to address 1.
        try:
            addr = self._discover_pump_address()
        except (RegloICCError, DeviceTimeoutError):
            addr = "0"
        protocol_version = self.query(f"{addr}x!")
        serial_number = self.query(f"{addr}xS")
        raw_channels = self.query(f"{addr}xA")
        try:
            channel_count = int(raw_channels.split()[0])
        except (ValueError, IndexError):
            channel_count = 0
        model = self.query(f"{addr}#")
        return PumpProbe(
            port=self.port or "",
            protocol_version=protocol_version,
            serial_number=serial_number,
            channel_count=channel_count,
            model=model,
        )

    def execute_command(self, command: DeviceCommand) -> object | None:
        command_type = str(command.command_type or "").strip().casefold()
        payload = dict(command.payload or {})
        if command_type == "pump.stop_all":
            self.stop_all(int(payload.get("channel_count", 4)))
            return None
        if command_type == "pump.start":
            self.start_channel(int(payload.get("channel", 1)))
            return None
        if command_type == "pump.stop":
            self.stop_channel(int(payload.get("channel", 1)))
            return None
        if command_type == "pump.set_flow":
            self.apply_channel(
                int(payload.get("channel", 1)),
                float(payload.get("flow_ul_min", 0.0)),
                str(payload.get("direction", "OFF")),
                float(payload.get("tube_mm", 0.0)),
                backsteps=int(payload.get("backsteps", 0)),
                roller_count=int(payload.get("roller_count", DEFAULT_ROLLER_COUNT)),
                start=bool(payload.get("start", False)),
            )
            return None
        if command_type == "pump.set_display":
            self.set_display_text(str(payload.get("text", "")))
            return None
        if command_type == "pump.calibration.get_direction":
            return self.get_calibration_direction(int(payload.get("channel", 1)))
        if command_type == "pump.calibration.set_direction":
            self.set_calibration_direction(int(payload.get("channel", 1)), str(payload.get("direction", "CW")))
            return None
        if command_type == "pump.calibration.get_target_volume_ml":
            return self.get_calibration_target_volume_ml(int(payload.get("channel", 1)))
        if command_type == "pump.calibration.set_target_volume_ml":
            return self.set_calibration_target_volume_ml(int(payload.get("channel", 1)), float(payload.get("volume_ml", 0.0)))
        if command_type == "pump.calibration.set_measured_volume_ml":
            return self.set_calibration_measured_volume_ml(int(payload.get("channel", 1)), float(payload.get("volume_ml", 0.0)))
        if command_type == "pump.calibration.get_time_s":
            return self.get_calibration_time_s(int(payload.get("channel", 1)))
        if command_type == "pump.calibration.set_time_s":
            self.set_calibration_time_s(int(payload.get("channel", 1)), float(payload.get("seconds", 0.0)))
            return None
        if command_type == "pump.calibration.time_since_last_s":
            return self.get_time_since_last_calibration_s(int(payload.get("channel", 1)))
        if command_type == "pump.calibration.start":
            self.start_calibration(int(payload.get("channel", 1)))
            return None
        if command_type == "pump.calibration.cancel":
            self.cancel_calibration(int(payload.get("channel", 1)))
            return None
        if command_type == "pump.roller_step_volume.get":
            return self.get_roller_step_volume_ml(int(payload.get("channel", 1)))
        if command_type == "pump.roller_step_volume.set":
            self.set_roller_step_volume_ml(int(payload.get("channel", 1)), float(payload.get("volume_ml", 0.0)))
            return None
        if command_type == "pump.get_start_failure_reason":
            return self.get_start_failure_reason(int(payload.get("channel", 1)))
        if command_type in {"pump.query", "raw.query"}:
            return self.query(str(payload.get("command", "")))
        raise RegloICCError(f"Unsupported command type {command.command_type!r} for {type(self).__name__}.")

    def _discover_pump_address(self) -> str:
        """Return the pump address string ('0' or '1') that responds to x!."""
        try:
            self.query("0x!")
            return "0"
        except (RegloICCError, DeviceTimeoutError):
            self.query("1x!")  # raises if address 1 also fails
            return "1"

    def query(self, command: str) -> str:
        return self.send(command)

    def send(self, command: str, idle_timeout_s: float = 0.03, max_wait_s: float = 0.75) -> str:
        # Retries a lost response (see _call_with_retry on SerialController,
        # inherited here) - confirmed safe for this pump's start/stop/set-flow
        # commands: a lost response doesn't mean the pump didn't apply the
        # command, and resending it is a safe no-op in that case. Does not
        # retry an explicit pump rejection ("#") below - that's a real answer.
        return self._call_with_retry(
            lambda: self._send_once(command, idle_timeout_s, max_wait_s), f"send {command!r}"
        )

    def _send_once(self, command: str, idle_timeout_s: float, max_wait_s: float) -> str:
        if self._serial is None:
            raise RegloICCError("Pump is not connected.")

        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        self._serial.write((command + "\r").encode("ascii"))
        self._serial.flush()

        chunks: list[bytes] = []
        started = monotonic()
        last_rx = started
        while monotonic() - started < max_wait_s:
            chunk = self._serial.read_all()
            if chunk:
                chunks.append(chunk)
                last_rx = monotonic()
                joined = b"".join(chunks)
                if b"\r" in joined or joined.endswith((b"*", b"#", b"+", b"-")):
                    break
            elif chunks and monotonic() - last_rx >= idle_timeout_s:
                break
            else:
                sleep(0.005)

        raw = b"".join(chunks).decode("ascii", errors="replace").strip()
        if not raw:
            raise DeviceTimeoutError(f"No response from pump for command {command!r}.")
        if raw == "#":
            raise RegloICCError(f"Pump rejected command {command!r}.")
        return raw

    def start_channel(self, channel: int) -> None:
        self._expect_start_status(f"{channel}H", channel)

    def stop_channel(self, channel: int) -> None:
        self._expect_status(f"{channel}I")

    def stop_all(self, channel_count: int = 4) -> None:
        for channel in range(1, channel_count + 1):
            self.stop_channel(channel)

    def set_display_text(self, text: str) -> None:
        """Write *text* to the pump's display while it's under remote control.

        Uses the ``xN`` ("Set pump's temporary display name") command,
        addressed to pump 0 since this is a per-pump, not per-channel,
        parameter. *text* is sanitized to printable ASCII and truncated to
        16 characters first - see :func:`sanitize_pump_display_text`.

        Switched from the originally-implemented ``DA`` ("Write letters to
        the pump to display...", manual section 6.20): that command is
        only ever listed in the summary command table - unlike nearly
        every other command in the manual, it has no worked example
        anywhere in section 18, which was the first real hint it might not
        be the intended way to show custom text. ``xN`` (section 6.5) does
        have one, for exactly this use case (section 18.6.2: "Set the
        pump's display name to 'Reagent A.'" -> request `0xNReagent A[CR]`
        -> response `*`). Both are per-pump String commands addressed to
        pump 0 the same way, so the only change here is the command code.

        Still UNVERIFIED against real hardware as of this change - see
        "Pump Display" in apps/sLSPR/acq/docs/experiment-control/pump_control_guide.md.
        """
        sanitized = sanitize_pump_display_text(text)
        self._expect_status(f"0xN{sanitized}")

    def configure_channel(
        self,
        channel: int,
        flow_ul_min: float,
        direction: str,
        tube_mm: float,
        backsteps: int = 0,
        roller_count: int = DEFAULT_ROLLER_COUNT,
    ) -> None:
        flow_ul_min = max(float(flow_ul_min), 0.0)
        direction = str(direction or "OFF").upper()
        self._expect_status(f"{channel}+{self._encode_tube_mm(tube_mm)}")
        self.set_roller_count(channel, roller_count)
        self.set_backsteps(channel, backsteps)
        if flow_ul_min <= 0.0 or direction == "OFF":
            return
        self._expect_status(f"{channel}{'J' if direction == 'CW' else 'K'}")
        self._expect_status(f"{channel}M")
        self.send(f"{channel}f{self._encode_volume_type2(flow_ul_min / 1000.0)}")

    def apply_channel(
        self,
        channel: int,
        flow_ul_min: float,
        direction: str,
        tube_mm: float,
        *,
        backsteps: int = 0,
        roller_count: int = DEFAULT_ROLLER_COUNT,
        start: bool,
    ) -> None:
        flow_ul_min = max(float(flow_ul_min), 0.0)
        direction = str(direction or "OFF").upper()
        self.configure_channel(channel, flow_ul_min, direction, tube_mm, backsteps, roller_count)
        if flow_ul_min <= 0.0 or direction == "OFF":
            self.stop_channel(channel)
            return
        if start:
            self.start_channel(channel)

    def set_backsteps(self, channel: int, backsteps: int) -> None:
        """Set the number of roller backsteps used for drip-free dispensing.

        Manual sec. 6.4.3/16.2 (ref. 4.3-4.4): the "%" command, Discrete
        Type 2 (4-digit, right-justified). Range 0-100; 0 is the pump's own
        factory default (no backstep correction).
        """
        backsteps = max(0, min(int(backsteps), 100))
        self._expect_status(f"{channel}%{backsteps:04d}")

    def set_roller_count(self, channel: int, roller_count: int) -> None:
        """Set the number of rollers on the cassette head installed for *channel*.

        Manual sec. 6.12/6.13 (ref. 6.12-6.13): the "xB" command, Discrete
        Type 2 (4-digit, right-justified). Must match the physically
        installed cassette head - the pump uses this (together with tube
        diameter and calibration) to convert a mL/min flow-rate target into
        an actual roller speed, so a mismatch here silently skews every
        flow rate sent via configure_channel/apply_channel. Falls back to
        DEFAULT_ROLLER_COUNT for any value outside the pump's supported set
        rather than sending a command the pump would reject.
        """
        roller_count = int(roller_count)
        if roller_count not in VALID_ROLLER_COUNTS:
            roller_count = DEFAULT_ROLLER_COUNT
        self._expect_status(f"{channel}xB{roller_count:04d}")

    # --- Calibration (manual sec. 6.4.4 / 16.2 ref 5.0, 18.5) ---------------
    #
    # The front-panel/§18.5 procedure is inherently one-channel-at-a-time and
    # interactive: configure a target volume + duration + direction, start
    # the run (physically dispenses), then hand-enter the actual measured
    # volume, which the pump uses to recompute its internal calibrated
    # roller-step volume for that channel. These methods expose each step of
    # that procedure individually. get_roller_step_volume_ml/
    # set_roller_step_volume_ml (the "r" command, ref 6.33/6.34) expose the
    # resulting calibration constant directly - this is what lets all
    # channels be dispensed simultaneously (e.g. run every channel for 10
    # minutes, measure each one's real output by hand, then push each
    # channel's own corrected constant here) instead of the interactive
    # procedure's one-channel-at-a-time flow.

    def get_calibration_direction(self, channel: int) -> str:
        """Manual ref 5.1 ("xR", Get). Returns "CW" or "CCW"."""
        return "CW" if self.query(f"{channel}xR").strip().upper() == "J" else "CCW"

    def set_calibration_direction(self, channel: int, direction: str) -> None:
        """Manual ref 5.2 ("xR", Set)."""
        code = "J" if str(direction or "CW").upper() == "CW" else "K"
        self._expect_status(f"{channel}xR{code}")

    def get_calibration_target_volume_ml(self, channel: int) -> float:
        """Manual ref 5.3 ("xU", Get) - the configured calibration-run target volume."""
        return self._decode_volume_type1(self.query(f"{channel}xU"))

    def set_calibration_target_volume_ml(self, channel: int, volume_ml: float) -> float:
        """Manual ref 5.4 ("xU", Set). Unlike most Set commands this echoes
        back the accepted volume (Volume Type 1) rather than a plain "*"
        status, so the confirmed value is returned."""
        response = self.send(f"{channel}xU{self._encode_volume_type2(max(float(volume_ml), 0.0))}")
        return self._decode_volume_type1(response)

    def set_calibration_measured_volume_ml(self, channel: int, volume_ml: float) -> float:
        """Manual ref 5.5 ("xV", Set) - enter the volume actually measured
        for the just-completed calibration run; this is the step that
        applies the correction. Echoes back the accepted volume."""
        response = self.send(f"{channel}xV{self._encode_volume_type2(max(float(volume_ml), 0.0))}")
        return self._decode_volume_type1(response)

    def get_calibration_time_s(self, channel: int) -> float:
        """Manual ref 5.6 ("xW", Get) - configured calibration-run duration.
        See _decode_time_type_seconds for a note on a unit ambiguity in the
        manual's own worked examples for this data type."""
        return self._decode_time_type_seconds(self.query(f"{channel}xW"))

    def set_calibration_time_s(self, channel: int, seconds: float) -> None:
        """Manual ref 5.7 ("xW", Set)."""
        self._expect_status(f"{channel}xW{self._encode_time_type(seconds)}")

    def get_time_since_last_calibration_s(self, channel: int) -> float:
        """Manual ref 5.8 ("xX", Get) - elapsed time since this channel was
        last calibrated. See _decode_time_type_seconds for a note on a unit
        ambiguity in the manual's own worked example for this exact command
        (sec. 18.5.4)."""
        return self._decode_time_type_seconds(self.query(f"{channel}xX"))

    def start_calibration(self, channel: int) -> None:
        """Manual ref 5.9 ("xY", Set) - physically starts the pump; it
        dispenses the configured target volume/time on this channel."""
        self._expect_start_status(f"{channel}xY", channel)

    def cancel_calibration(self, channel: int) -> None:
        """Manual ref 5.10 ("xZ", Set) - cancels an in-progress calibration run."""
        self._expect_status(f"{channel}xZ")

    def get_roller_step_volume_ml(self, channel: int) -> float:
        """Manual ref 6.33 ("r", Get) - the pump's current calibrated
        roller-step volume (mL dispensed per roller step) for this channel,
        derived from calibration/tube diameter/roller count. Reset when the
        tube diameter changes or the pump-wide calibration reset ("000000")
        is sent."""
        return self._decode_volume_type1(self.query(f"{channel}r"))

    def set_roller_step_volume_ml(self, channel: int, volume_ml: float) -> None:
        """Manual ref 6.34 ("r", Set) - directly overwrite the calibrated
        roller-step volume for this channel, bypassing the interactive
        dispense-then-measure procedure entirely."""
        self._expect_status(f"{channel}r{self._encode_volume_type2(max(float(volume_ml), 0.0))}")

    def _expect_status(self, command: str) -> None:
        response = self.send(command)
        if response != "*":
            raise RegloICCError(f"Unexpected pump response {response!r} for {command!r}.")

    # "-" is a documented but command-specific "cannot run" response (manual
    # sec. 15.1) - the manual only explains its meaning for start-type
    # commands (sec. 2.1's "H", via the "xe" diagnostic in sec. 2.7). Kept
    # separate from _expect_status (used for many other Set commands where
    # "-" isn't documented at all) so only start_channel/start_calibration
    # attempt this specific diagnosis.
    _START_FAILURE_CAUSES = {
        "C": "cycle count is 0",
        "R": "requested flow rate exceeds the max the pump/tubing can achieve, or flow is set to 0",
        "V": "requested volume exceeds the max the pump/tubing can achieve",
    }
    _START_FAILURE_LIMITS = {
        "C": "limiting value is undefined",
        "R": "limited by max achievable flow rate (mL/min)",
        "V": "limited by max achievable volume (mL)",
    }

    def get_start_failure_reason(self, channel: int) -> str:
        """Manual sec. 2.7 ("xe", Get) - explains why a start-type command
        ("H"/"xY") just answered "-" ("cannot run") for *channel*. Response
        is two space-delimited single-character codes: Parameter #1 (cause)
        and Parameter #2 (the limiting value that was exceeded). Documented
        under Pump Drive (sec. 2.0) for "H" specifically - used here for
        "xY" too since the manual defines no separate equivalent for
        calibration and both are fundamentally "start dispensing" commands;
        verify this holds for your pump via the Calibration tab's "Why?"
        button.
        """
        raw = self.query(f"{channel}xe").strip()
        parts = raw.split()
        cause_code = (parts[0] if parts else "").upper()
        limit_code = (parts[1] if len(parts) > 1 else "").upper()
        cause = self._START_FAILURE_CAUSES.get(cause_code, f"unrecognized cause code {cause_code!r}" if cause_code else "no reason reported")
        limit = self._START_FAILURE_LIMITS.get(limit_code)
        return f"{cause} ({limit})" if limit else cause

    def _expect_start_status(self, command: str, channel: int) -> None:
        response = self.send(command)
        if response == "*":
            return
        if response == "-":
            try:
                reason = self.get_start_failure_reason(channel)
            except Exception:
                reason = None
            detail = f" - {reason}" if reason else " - run get_start_failure_reason() / the Calibration tab's \"Why?\" button for details"
            raise RegloICCError(f"Pump refused to start channel {channel} (manual sec. 2.7){detail}.")
        raise RegloICCError(f"Unexpected pump response {response!r} for {command!r}.")

    @staticmethod
    def _encode_tube_mm(tube_mm: float) -> str:
        hundredths = int(round(max(float(tube_mm), 0.0) * 100.0))
        return f"{hundredths:04d}"

    @staticmethod
    def _encode_volume_type2(value_ml: float) -> str:
        """Encode a volume for a Set request ("mmmmse", no "E" character).

        The manual's own worked examples (e.g. sec. 18.3.3: request
        "1f1300-3[CR]" to set 1.3 uL/min) show Set requests omitting the "E"
        that separates mantissa from exponent - only present in the Volume
        Type 1 Get/response format (sec. 14.6.10, e.g. that same example's
        response "1300E-3[CR][LF]"). "Volume Type 2" (used for Set) is never
        formally defined in the manual's Data Type Formats section, but every
        worked Set example confirms this "E"-less variant. Do not feed this
        method's output directly into _decode_volume_type1 - insert "E"
        first if you need to round-trip a value through both formats.
        """
        value_ml = max(float(value_ml), 0.0)
        if value_ml == 0.0:
            return "0000+0"

        exponent = floor(log10(value_ml))
        mantissa = value_ml / (10**exponent)
        while mantissa < 1.0:
            mantissa *= 10.0
            exponent -= 1
        while mantissa >= 10.0:
            mantissa /= 10.0
            exponent += 1

        mantissa_digits = int(round(mantissa * 1000.0))
        if mantissa_digits >= 10000:
            mantissa_digits //= 10
            exponent += 1

        exponent = max(min(exponent, 9), -9)
        sign = "+" if exponent >= 0 else "-"
        return f"{mantissa_digits:04d}{sign}{abs(exponent)}"

    @staticmethod
    def _decode_volume_type1(raw: str) -> float:
        """Decode a Volume Type 1 response ("mmmmEse", manual sec. 14.6.10)
        back to mL - the inverse of _encode_volume_type2. E.g. "1000E+2" ->
        1.000 x 10^2 = 100.0 mL."""
        raw = raw.strip()
        mantissa_str, separator, exponent_str = raw.partition("E")
        if not separator or not mantissa_str or not exponent_str:
            raise RegloICCError(f"Malformed Volume Type 1 response {raw!r}.")
        try:
            mantissa = int(mantissa_str) / 1000.0
            exponent = int(exponent_str)
        except ValueError as exc:
            raise RegloICCError(f"Malformed Volume Type 1 response {raw!r}.") from exc
        return mantissa * (10.0**exponent)

    @staticmethod
    def _encode_time_type(seconds: float) -> str:
        """Encode a duration for a Time Type 1/2 field ("xW" set - manual
        sec. 14.6.11/14.6.12): up to 8 digits, zero-padded, units of 0.1 s
        per the formal type definition.

        NOTE: several of the manual's own worked examples for Time Type
        commands (e.g. sec. 18.3.5/18.3.6, and "xX" in 18.5.4) describe the
        same raw digit strings as if they were plain whole seconds instead -
        inconsistent with the 0.1 s/unit definition stated in 14.6.11/
        14.6.12, and inconsistent with each other. This implementation
        follows the formal 0.1 s/unit definition, which is at least stated
        consistently across both time types. Verify against a real pump
        (see the Device Manager > Pump Calibration test tab) before relying
        on either interpretation.
        """
        tenths = int(round(max(float(seconds), 0.0) * 10.0))
        tenths = min(tenths, 35_964_000)
        return f"{tenths:08d}"

    @staticmethod
    def _decode_time_type_seconds(raw: str) -> float:
        """Decode a Time Type 1/2 response back to seconds. See
        _encode_time_type's note on the manual's internal inconsistency for
        this data type - this follows the same 0.1 s/unit convention."""
        raw = raw.strip()
        if not raw:
            return 0.0
        try:
            return int(raw) * 0.1
        except ValueError as exc:
            raise RegloICCError(f"Malformed Time Type response {raw!r}.") from exc
