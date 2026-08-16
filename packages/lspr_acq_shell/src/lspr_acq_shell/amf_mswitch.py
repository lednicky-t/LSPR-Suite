"""AMF switch rotary valve (selector) driver, via the proprietary `amfTools`
SDK (bypasses `pyserial` entirely - a device-layer audit finding that ruled
out a simulated-device test double for this driver specifically).

Extracted from singleLSPR Acquisition's `device/amf_mswitch.py` (Phase 1,
2026-08-08), part of the fluidics device framework verbatim move (§4.2) -
preserved exactly, no logic changes.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
from time import sleep
from typing import Callable, TypeVar

from lspr_acq_shell.communication_models import DeviceCommand
from lspr_acq_shell.connection_registry import claim_port, release_port, try_claim_port
from lspr_acq_shell.device_driver import DeviceDriver
from lspr_acq_shell.serial_controllers import ControllerError, ControllerProbe

try:  # Optional proprietary dependency.
    import amfTools  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    amfTools = None

_LOGGER = logging.getLogger("lspr_app.device")
_T = TypeVar("_T")


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


class AMFSwitchController(DeviceDriver):
    controller_type = "amf-mswitch"
    # Retry policy for _call_with_retry (see below) - confirmed with the
    # maintainer that resending a move/home command is safe for this device:
    # a lost response doesn't mean the switch didn't move, and resending a
    # move-to-position command is an idempotent no-op in that case.
    _MAX_COMMAND_ATTEMPTS = 3
    _RETRY_DELAY_S = 0.15

    def __init__(self) -> None:
        self._amf = None
        self.port: str | None = None
        # Per-instance, not self.controller_type (shared by every instance of
        # this class) - matches RegloICCClient's pattern. A class-level claim
        # owner would let two different AMFSwitchController instances both
        # "successfully" claim the same port (try_claim_port treats a second
        # claim under an already-registered owner name as a no-op re-claim,
        # not a conflict), silently defeating the busy-port check.
        self._claim_owner = f"{self.controller_type}:{id(self)}"

    def _call_with_retry(self, operation: Callable[[], _T], description: str) -> _T:
        """Run *operation*, retrying on failure and reconnecting first.

        Unlike SerialController's version, this retries any exception, not
        just a distinguished timeout - the amfTools library doesn't expose a
        separate "no response" exception type to catch more precisely. This
        is acceptable here because move/home commands were confirmed safe to
        resend blindly (see _MAX_COMMAND_ATTEMPTS above); retrying a
        deterministic error (e.g. an invalid target position) just fails the
        same way again rather than causing harm.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self._MAX_COMMAND_ATTEMPTS + 1):
            try:
                return operation()
            except Exception as exc:
                last_exc = exc
                _LOGGER.warning(
                    "%s: %s failed (attempt %d/%d): %s",
                    self.controller_type, description, attempt, self._MAX_COMMAND_ATTEMPTS, exc,
                )
                if self.port is not None:
                    port = self.port
                    try:
                        self.close()
                        self.connect(port)
                    except Exception:
                        # Reopening the port failed too - further attempts
                        # would just fail identically, so stop now and
                        # surface the original error rather than a
                        # follow-on "not connected" from the next attempt.
                        _LOGGER.warning("%s: reconnect after dropped connection failed", self.controller_type, exc_info=True)
                        raise exc from None
            if attempt < self._MAX_COMMAND_ATTEMPTS:
                sleep(self._RETRY_DELAY_S)
        assert last_exc is not None
        raise last_exc

    def connect(self, port: str) -> None:
        if amfTools is None:
            raise ControllerError("AMFTools library is not installed.")
        self.close()
        if not try_claim_port(port, self._claim_owner):
            raise ControllerError(f"Port {port} is busy.")
        try:
            self._amf = amfTools.AMF(port)
            canonical = str(self._amf.getSerialPort())
        except Exception as exc:
            self._amf = None
            self.port = None
            release_port(port, self._claim_owner)
            raise ControllerError(str(exc)) from exc
        if canonical and canonical != port:
            # AMF normalized the port name — swap the claim to the canonical form.
            release_port(port, self._claim_owner)
            claim_port(canonical, self._claim_owner)
        self.port = canonical or port

    def close(self) -> None:
        if self._amf is not None:
            try:
                self._amf.disconnect()
            finally:
                if self.port is not None:
                    release_port(self.port, self._claim_owner)
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

    def execute_command(self, command: DeviceCommand) -> object | None:
        command_type = str(command.command_type or "").strip().casefold()
        payload = dict(command.payload or {})
        if command_type == "switch.home":
            self.home(block=bool(payload.get("block", True)))
            return None
        if command_type == "switch.move_to":
            self.move_to(int(payload.get("position", 1)), block=bool(payload.get("block", True)))
            return None
        if command_type == "switch.get_position":
            return self.get_position()
        raise ControllerError(f"Unsupported command type {command.command_type!r} for {type(self).__name__}.")

    def get_position(self) -> int:
        return self._call_with_retry(self._get_position_once, "get_position")

    def _get_position_once(self) -> int:
        if self._amf is None:
            raise ControllerError("AMF switch is not connected.")
        return int(self._amf.getValvePosition())

    def get_port_count(self) -> int:
        return self._call_with_retry(self._get_port_count_once, "get_port_count")

    def _get_port_count_once(self) -> int:
        if self._amf is None:
            raise ControllerError("AMF switch is not connected.")
        return int(self._amf.getPortNumber())

    def is_homed(self) -> bool:
        return self._call_with_retry(self._is_homed_once, "is_homed")

    def _is_homed_once(self) -> bool:
        if self._amf is None:
            raise ControllerError("AMF switch is not connected.")
        return bool(self._amf.getHomeStatus())

    def home(self, block: bool = True) -> None:
        self._call_with_retry(lambda: self._home_once(block), "home")

    def _home_once(self, block: bool) -> None:
        if self._amf is None:
            raise ControllerError("AMF switch is not connected.")
        self._amf.home(block=block)

    def move_to(self, target: int, block: bool = True) -> None:
        target_int = int(target)
        if target_int < 1:
            raise ControllerError("AMF switch port must be >= 1.")
        self._call_with_retry(lambda: self._move_to_once(target_int, block), f"move_to {target_int}")

    def _move_to_once(self, target_int: int, block: bool) -> None:
        if self._amf is None:
            raise ControllerError("AMF switch is not connected.")
        self._amf.valveShortestPath(target_int, block=block)


detect_amf_selector_devices = detect_amf_mswitch_devices
