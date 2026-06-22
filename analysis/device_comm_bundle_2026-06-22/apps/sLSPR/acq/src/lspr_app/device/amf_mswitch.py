from __future__ import annotations

import os
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from io import StringIO

from lspr_app.device.connection_registry import claim_port, release_port, try_claim_port
from lspr_app.device.serial_controllers import ControllerError, ControllerProbe

try:  # Optional proprietary dependency.
    import amfTools  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    amfTools = None


def amf_tools_available() -> bool:
    return amfTools is not None


@contextmanager
def _suppress_console_output():
    devnull_fd = os.open(os.devnull, os.O_RDWR)
    saved_stdout_fd = os.dup(1)
    saved_stderr_fd = os.dup(2)
    try:
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
        yield
    finally:
        try:
            os.dup2(saved_stdout_fd, 1)
            os.dup2(saved_stderr_fd, 2)
        finally:
            os.close(saved_stdout_fd)
            os.close(saved_stderr_fd)
            os.close(devnull_fd)


def detect_amf_mswitch_devices() -> list[ControllerProbe]:
    if amfTools is None:
        return []
    probes: list[ControllerProbe] = []
    seen_ports: set[str] = set()
    try:
        sink = StringIO()
        with _suppress_console_output(), redirect_stdout(sink), redirect_stderr(sink):
            devices = amfTools.util.getProductList()
    except Exception:
        return []
    for device in devices:
        device_family = str(getattr(device, "deviceFamily", "") or "").upper()
        device_type = str(getattr(device, "deviceType", "") or "").upper()
        if "RVM" not in device_family and "RVM" not in device_type:
            continue
        port = str(getattr(device, "comPort", "") or "")
        if not port or port in seen_ports:
            continue
        seen_ports.add(port)
        probes.append(
            ControllerProbe(
                port=port,
                controller_type="amf-mswitch",
                model=str(getattr(device, "deviceType", "") or getattr(device, "deviceFamily", "") or "AMF switch"),
                serial_number=str(getattr(device, "serialnumber", "") or "") or None,
                protocol_version=str(getattr(device, "connectionMode", "") or "") or None,
            )
        )
    return probes


class AMFSwitchController:
    controller_type = "amf-mswitch"

    def __init__(self) -> None:
        self._amf = None
        self.port: str | None = None

    def connect(self, port: str) -> None:
        if amfTools is None:
            raise ControllerError("AMFTools library is not installed.")
        self.close()
        if not try_claim_port(port, self.controller_type):
            raise ControllerError(f"Port {port} is busy.")
        try:
            self._amf = amfTools.AMF(port)
            self.port = str(self._amf.getSerialPort())
        except Exception as exc:
            self._amf = None
            self.port = None
            release_port(port, self.controller_type)
            raise ControllerError(str(exc)) from exc
        claim_port(self.port or port, self.controller_type)

    def close(self) -> None:
        if self._amf is not None:
            try:
                self._amf.disconnect()
            finally:
                if self.port is not None:
                    release_port(self.port, self.controller_type)
                self._amf = None
                self.port = None

    def is_connected(self) -> bool:
        return self._amf is not None

    def get_probe(self) -> ControllerProbe:
        if self._amf is None:
            raise ControllerError("AMF switch is not connected.")
        info = {}
        try:
            info = self._amf.getDeviceInformation(full=False)
        except Exception:
            info = {}
        model = str(info.get("typeProduct") or getattr(self._amf, "getType", lambda: "AMF switch")() or "AMF switch")
        serial_number = info.get("serialNumber") or None
        protocol_version = info.get("currentStatus") or info.get("productAddress") or None
        return ControllerProbe(
            port=str(info.get("serialPort") or self.port or ""),
            controller_type=self.controller_type,
            model=model,
            serial_number=str(serial_number) if serial_number is not None else None,
            protocol_version=str(protocol_version) if protocol_version is not None else None,
        )

    def get_position(self) -> int:
        if self._amf is None:
            raise ControllerError("AMF switch is not connected.")
        return int(self._amf.getValvePosition())

    def get_port_count(self) -> int:
        if self._amf is None:
            raise ControllerError("AMF switch is not connected.")
        return int(self._amf.getPortNumber())

    def is_homed(self) -> bool:
        if self._amf is None:
            raise ControllerError("AMF switch is not connected.")
        return bool(self._amf.getHomeStatus())

    def home(self, block: bool = True) -> None:
        if self._amf is None:
            raise ControllerError("AMF switch is not connected.")
        self._amf.home(block=block)

    def move_to(self, target: int, block: bool = True) -> None:
        if self._amf is None:
            raise ControllerError("AMF switch is not connected.")
        target_int = int(target)
        if target_int < 1:
            raise ControllerError("AMF switch port must be >= 1.")
        self._amf.valveShortestPath(target_int, block=block)
