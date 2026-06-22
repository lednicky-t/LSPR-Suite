from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep

import serial
from serial.tools import list_ports


@dataclass(slots=True)
class ArduinoValvePort:
    device: str
    description: str
    hwid: str


@dataclass(slots=True)
class ArduinoValveProbe:
    port: str
    protocol_version: str
    serial_number: str
    model: str


class ArduinoValveError(RuntimeError):
    pass


def is_probable_arduino_port(port: ArduinoValvePort) -> bool:
    description = port.description.upper()
    hwid = port.hwid.upper()
    return (
        "ARDUINO" in description
        or "CH340" in description
        or "ATMEGA" in description
        or "USB SERIAL" in description
        or "2341" in hwid
        or "1A86" in hwid
        or "067B" in hwid
    )


class ArduinoValveClient:
    def __init__(self) -> None:
        self._serial: serial.Serial | None = None
        self.port: str | None = None

    @staticmethod
    def list_ports() -> list[ArduinoValvePort]:
        return [
            ArduinoValvePort(device=port.device, description=port.description, hwid=port.hwid)
            for port in list_ports.comports()
        ]

    @classmethod
    def probe_port(cls, port: str) -> ArduinoValveProbe:
        client = cls()
        try:
            client.connect(port)
            return client.get_probe()
        finally:
            client.close()

    def connect(self, port: str, timeout_s: float = 0.35) -> None:
        self.close()
        self._serial = serial.Serial(
            port=port,
            baudrate=115200,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=timeout_s,
            write_timeout=timeout_s,
        )
        self.port = port
        sleep(2.0)

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None
                self.port = None

    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def _write(self, command: str) -> None:
        if self._serial is None:
            raise ArduinoValveError("Valve controller is not connected.")
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        self._serial.write((command + "\n").encode("ascii"))
        self._serial.flush()

    def _read_line(self, max_wait_s: float = 0.75) -> str:
        if self._serial is None:
            raise ArduinoValveError("Valve controller is not connected.")
        started = monotonic()
        chunks: list[bytes] = []
        while monotonic() - started < max_wait_s:
            chunk = self._serial.readline()
            if chunk:
                return chunk.decode("ascii", errors="replace").strip()
        raise ArduinoValveError("No response from valve controller.")

    def query(self, command: str) -> str:
        self._write(command)
        return self._read_line()

    def get_probe(self) -> ArduinoValveProbe:
        protocol_version = self.query("asn")
        serial_number = protocol_version
        model = self.query("mod")
        return ArduinoValveProbe(
            port=self.port or "",
            protocol_version=protocol_version,
            serial_number=serial_number,
            model=model,
        )

    def set_position(self, valve: str) -> None:
        command = self._valve_command(valve)
        if not command:
            raise ArduinoValveError(f"Unsupported valve state: {valve!r}")
        self._write(command)

    @staticmethod
    def _valve_command(valve: str) -> str:
        normalized = str(valve or "").strip().lower()
        if normalized in {"left", "l", "open", "o"}:
            return "vl"
        if normalized in {"right", "r", "close", "c"}:
            return "vr"
        return ""
