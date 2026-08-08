"""Concrete SerialController subclasses for the switch/valve device family
(Arduino, ItsyBitsy 32u4, and legacy firmware variants).

Extracted from singleLSPR Acquisition's `device/valve_controllers.py`
(Phase 1, 2026-08-08), part of the fluidics device framework verbatim move
(§4.2) - preserved exactly, no logic changes.
"""

from __future__ import annotations

from lspr_acq_shell.serial_controllers import (
    ControllerCapabilities,
    ControllerError,
    ControllerPort,
    ControllerProbe,
    SerialController,
    detect_controller,
    register_controller,
)


@register_controller
class ArduinoValveController(SerialController):
    controller_type = "arduino-valve"
    priority = 20
    capabilities = ControllerCapabilities(has_temperature_sensor=True, has_humidity_sensor=True)
    _BAUD_RATE = 115200
    _TIMEOUT = 0.35
    _BOOTLOADER_WAIT_S = 2.0  # Arduino resets via DTR on open; bootloader runs ~2 s

    @classmethod
    def is_probable_port(cls, port: ControllerPort) -> bool:
        description = port.description.upper()
        hwid = port.hwid.upper()
        return (
            "ARDUINO" in description
            or "CH340" in description
            or "ATMEGA" in description
            or "FTDI" in description
            or "2341" in hwid  # Arduino LLC VID
            or "1A86" in hwid  # QinHeng CH340 VID
            or "0403" in hwid  # FTDI VID - some Arduino-family boards (e.g.
            # Nano clones/custom builds) use an FTDI USB-serial adapter
            # instead of CH340 or native USB. Confirmed against a real
            # board: hwid "VID:PID=0403:6001", firmware answers asn/mod/
            # at/ah correctly at 115200 baud - it just doesn't announce
            # itself as "Arduino" in the description or VID.
        ) and "239A" not in hwid  # exclude Adafruit/ItsyBitsy

    def get_probe(self) -> ControllerProbe:
        protocol_version = self.query("asn")
        model = self.query("mod")
        return ControllerProbe(
            port=self.port or "",
            controller_type=self.controller_type,
            model=model or "Arduino valve controller",
            serial_number=protocol_version or None,
            protocol_version=protocol_version or None,
        )

    def set_position(self, position: str) -> None:
        normalized = str(position or "").strip().lower()
        if normalized in {"open", "o", "left", "l"}:
            self._write("vl")
            return
        if normalized in {"close", "c", "right", "r"}:
            self._write("vr")
            return
        raise ControllerError(f"Unsupported valve state for Arduino controller: {position!r}")

    def read_ambient_temperature(self) -> float:
        """Read the controller's onboard ambient temperature sensor (°C).

        Command recovered from the sibling LSPR-LCTF project's main.py
        (Ambient_temperature()), which talks to the same asn/mod-identified
        Arduino firmware family as this driver - see
        apps/sLSPR/acq/docs/hardware/arduino_valve_controller_protocol.md.
        """
        response = self.query("at")
        try:
            return float(response)
        except (TypeError, ValueError):
            raise ControllerError(
                f"Unexpected ambient temperature reading from Arduino controller: {response!r}"
            ) from None

    def read_humidity(self) -> float:
        """Read the controller's onboard ambient humidity sensor (% RH).

        Command recovered from the sibling LSPR-LCTF project's main.py
        (Humidity()) - see apps/sLSPR/acq/docs/hardware/arduino_valve_controller_protocol.md.
        """
        response = self.query("ah")
        try:
            return float(response)
        except (TypeError, ValueError):
            raise ControllerError(
                f"Unexpected humidity reading from Arduino controller: {response!r}"
            ) from None


@register_controller
class ItsyBitsy32U4ValveController(ArduinoValveController):
    controller_type = "itsybitsy-32u4-valve"
    priority = 30
    # Overrides the Arduino parent's capabilities (not just its read methods
    # below) - this firmware build has no sensor hardware at all, unlike the
    # standard Arduino valve controller it otherwise shares a protocol with.
    capabilities = ControllerCapabilities()
    _TIMEOUT = 1.0
    _BOOTLOADER_WAIT_S = 3.0  # 32u4 bootloader is slower than standard Arduino; needs ~3 s

    @classmethod
    def is_probable_port(cls, port: ControllerPort) -> bool:
        description = port.description.upper()
        hwid = port.hwid.upper()
        return (
            "ITSYBITSY" in description
            or "ADAFRUIT" in description
            or "239A" in hwid
        )

    def get_probe(self) -> ControllerProbe:
        protocol_version = self.query("asn", max_wait_s=1.25)
        model = self.query("mod", max_wait_s=1.25)
        return ControllerProbe(
            port=self.port or "",
            controller_type=self.controller_type,
            model=model or "ItsyBitsy 32u4 valve controller",
            serial_number=protocol_version or None,
            protocol_version=protocol_version or None,
        )

    def read_ambient_temperature(self) -> float:
        # Overrides ArduinoValveController's implementation deliberately: the
        # ItsyBitsy firmware in this repo (firmware/itsybitsy32u4_valve_controller)
        # has no sensor code or "at" command at all, so failing fast here with
        # a clear message beats sending a doomed query and getting a confusing
        # "unexpected reading: 'err'" from the generic float-parse failure.
        raise ControllerError("ItsyBitsy 32u4 valve controller firmware has no ambient temperature sensor.")

    def read_humidity(self) -> float:
        raise ControllerError("ItsyBitsy 32u4 valve controller firmware has no humidity sensor.")


@register_controller
class LegacyValveController(SerialController):
    controller_type = "legacy-valve"
    priority = 10
    _BAUD_RATE = 9600
    _TIMEOUT = 1.0
    _BOOTLOADER_WAIT_S = 0.5

    def __init__(self, channel_count: int = 4) -> None:
        super().__init__()
        self._channel_count = max(int(channel_count), 1)

    @classmethod
    def is_probable_port(cls, port: ControllerPort) -> bool:
        description = port.description.upper()
        hwid = port.hwid.upper()
        return (
            "USB SERIAL" in description
            or "FTDI" in description
            or "CH340" in description
            or "ATMEGA" in description
            or "ARDUINO" in description
            or "2341" in hwid
            or "1A86" in hwid
            or "0403" in hwid
            or "067B" in hwid
        )

    def _post_connect(self) -> None:
        self.query("vi", max_wait_s=1.0)  # legacy handshake confirms firmware is alive

    def get_probe(self) -> ControllerProbe:
        serial_number = self.query("vi", max_wait_s=1.0)
        return ControllerProbe(
            port=self.port or "",
            controller_type=self.controller_type,
            model="Legacy valve controller",
            serial_number=serial_number or None,
            protocol_version="legacy-vi",
        )

    def set_position(self, position: str) -> None:
        normalized = str(position or "").strip().lower()
        if normalized in {"open", "o", "left", "l", "on", "true", "1"}:
            self.set_channel_states([True] * self._channel_count)
            return
        if normalized in {"close", "c", "right", "r", "off", "false", "0"}:
            self.set_channel_states([False] * self._channel_count)
            return
        raise ControllerError(f"Unsupported valve state for legacy controller: {position!r}")

    def set_channel_state(self, channel_index: int, enabled: bool) -> None:
        if channel_index < 1:
            raise ControllerError("Channel index must be >= 1.")
        command = f"ve{channel_index}" if enabled else f"va{channel_index}"
        self._write(command)

    def set_channel_states(self, states: list[bool]) -> None:
        for channel_index, enabled in enumerate(states, start=1):
            if channel_index > self._channel_count:
                break
            self.set_channel_state(channel_index, enabled)

    def stop(self) -> None:
        self._write("va0")


def detect_valve_controller(port: str) -> tuple[SerialController, ControllerProbe]:
    return detect_controller(port)
