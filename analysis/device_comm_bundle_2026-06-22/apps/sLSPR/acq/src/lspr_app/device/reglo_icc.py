from __future__ import annotations

from dataclasses import dataclass
from math import floor, log10
from time import monotonic, sleep

import serial

from lspr_app.device.connection_registry import claim_port, release_port, try_claim_port
from serial.tools import list_ports


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


class RegloICCError(RuntimeError):
    pass


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


class RegloICCClient:
    def __init__(self) -> None:
        self._serial: serial.Serial | None = None
        self.port: str | None = None
        self._claim_owner = "reglo-icc"

    @staticmethod
    def list_ports() -> list[PumpPort]:
        return [
            PumpPort(device=port.device, description=port.description, hwid=port.hwid)
            for port in list_ports.comports()
        ]

    @classmethod
    def probe_port(cls, port: str) -> PumpProbe:
        client = cls()
        try:
            client.connect(port)
            return client.get_probe()
        finally:
            client.close()

    def connect(self, port: str, timeout_s: float = 0.35) -> None:
        self.close()
        if not try_claim_port(port, self._claim_owner):
            raise RegloICCError(f"Port {port} is busy.")
        try:
            self._serial = serial.Serial(
                port=port,
                baudrate=9600,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=timeout_s,
                write_timeout=timeout_s,
            )
        except Exception:
            release_port(port, self._claim_owner)
            raise
        self.port = port
        claim_port(port, self._claim_owner)

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None
                if self.port is not None:
                    release_port(self.port, self._claim_owner)
                self.port = None

    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def get_probe(self) -> PumpProbe:
        protocol_version = self.query("0x!")
        serial_number = self.query("0xS")
        channel_count = int(self.query("0xA"))
        model = self.query("0#")
        return PumpProbe(
            port=self.port or "",
            protocol_version=protocol_version,
            serial_number=serial_number,
            channel_count=channel_count,
            model=model,
        )

    def query(self, command: str) -> str:
        response = self.send(command)
        if response in {"*", "#", "+", "-"}:
            return response
        return response

    def send(self, command: str, idle_timeout_s: float = 0.03, max_wait_s: float = 0.75) -> str:
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
            raise RegloICCError(f"No response from pump for command {command!r}.")
        if raw == "#":
            raise RegloICCError(f"Pump rejected command {command!r}.")
        return raw

    def start_channel(self, channel: int) -> None:
        self._expect_status(f"{channel}H")

    def stop_channel(self, channel: int) -> None:
        self._expect_status(f"{channel}I")

    def stop_all(self, channel_count: int = 4) -> None:
        for channel in range(1, channel_count + 1):
            self.stop_channel(channel)

    def start_channels(self, channels: list[int]) -> None:
        for channel in channels:
            self.start_channel(channel)

    def stop_channels(self, channels: list[int]) -> None:
        for channel in channels:
            self.stop_channel(channel)

    def configure_channel(
        self,
        channel: int,
        flow_ul_min: float,
        direction: str,
        tube_mm: float,
    ) -> None:
        flow_ul_min = max(float(flow_ul_min), 0.0)
        direction = str(direction or "OFF").upper()
        self._expect_status(f"{channel}+{self._encode_tube_mm(tube_mm)}")
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
        start: bool,
    ) -> None:
        flow_ul_min = max(float(flow_ul_min), 0.0)
        direction = str(direction or "OFF").upper()
        self.configure_channel(channel, flow_ul_min, direction, tube_mm)
        if flow_ul_min <= 0.0 or direction == "OFF":
            self.stop_channel(channel)
            return
        if start:
            self.start_channel(channel)

    def _expect_status(self, command: str) -> None:
        response = self.send(command)
        if response != "*":
            raise RegloICCError(f"Unexpected pump response {response!r} for {command!r}.")

    @staticmethod
    def _encode_tube_mm(tube_mm: float) -> str:
        hundredths = int(round(max(float(tube_mm), 0.0) * 100.0))
        return f"{hundredths:04d}"

    @staticmethod
    def _encode_volume_type2(value_ml: float) -> str:
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
