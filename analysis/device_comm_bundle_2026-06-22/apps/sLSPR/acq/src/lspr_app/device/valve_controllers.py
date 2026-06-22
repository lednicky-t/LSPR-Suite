from __future__ import annotations

from dataclasses import dataclass
from time import sleep

import serial

from lspr_app.device.connection_registry import claim_port, release_port, try_claim_port
from lspr_app.device.serial_controllers import (
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

    def connect(self, port: str) -> None:
        self.close()
        if not try_claim_port(port, self.controller_type):
            raise ControllerError(f"Port {port} is busy.")
        try:
            self._serial = serial.Serial(
                port=port,
                baudrate=115200,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=0.35,
                write_timeout=0.35,
            )
        except Exception:
            release_port(port, self.controller_type)
            raise
        self.port = port
        claim_port(port, self.controller_type)
        sleep(2.0)

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                if self.port is not None:
                    release_port(self.port, self.controller_type)
                self._serial = None
                self.port = None

    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

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


@register_controller
class ItsyBitsy32U4ValveController(ArduinoValveController):
    controller_type = "itsybitsy-32u4-valve"
    priority = 30

    @classmethod
    def is_probable_port(cls, port: ControllerPort) -> bool:
        description = port.description.upper()
        hwid = port.hwid.upper()
        return (
            "ITSYBITSY" in description
            or "ADAFRUIT" in description
            or "239A" in hwid
        )

    def connect(self, port: str) -> None:
        self.close()
        if not try_claim_port(port, self.controller_type):
            raise ControllerError(f"Port {port} is busy.")
        try:
            self._serial = serial.Serial(
                port=port,
                baudrate=115200,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=1.0,
                write_timeout=1.0,
            )
        except Exception:
            release_port(port, self.controller_type)
            raise
        self.port = port
        claim_port(port, self.controller_type)
        sleep(3.0)
        if self._serial is not None:
            self._serial.reset_input_buffer()

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


@register_controller
class LegacyValveController(SerialController):
    controller_type = "legacy-valve"
    priority = 10

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

    def connect(self, port: str) -> None:
        self.close()
        if not try_claim_port(port, self.controller_type):
            raise ControllerError(f"Port {port} is busy.")
        try:
            self._serial = serial.Serial(
                port=port,
                baudrate=9600,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=1.0,
                write_timeout=1.0,
            )
        except Exception:
            release_port(port, self.controller_type)
            raise
        self.port = port
        claim_port(port, self.controller_type)
        sleep(0.5)
        self.query("vi", max_wait_s=1.0)

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                if self.port is not None:
                    release_port(self.port, self.controller_type)
                self._serial = None
                self.port = None

    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

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
