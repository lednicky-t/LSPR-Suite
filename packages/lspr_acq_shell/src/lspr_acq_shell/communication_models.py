"""Data models shared by the device communication/lifecycle layer - lifecycle
states, port/device descriptors, command/response/event types.

Extracted from singleLSPR Acquisition's `device/communication_models.py`
(Phase 1, 2026-08-08), part of the fluidics device framework verbatim move
(§4.2) - preserved exactly, no logic changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from lspr_acq_shell.device_types import PUMP, SELECTOR, SWITCH


class DeviceLifecycleState(str, Enum):
    """All valid lifecycle states for a hardware device connection.

    Inherits from ``str`` so that enum values compare equal to the plain
    string literals already used throughout the GUI
    (e.g. ``status.state == "connected"`` still works without change).

    Transitions:
        DISCONNECTED → CONNECTED  : successful connect()
        CONNECTED    → DISCONNECTED: successful disconnect()
        *            → ERROR      : connect() raised an exception
        ERROR        → DISCONNECTED: explicit disconnect() clears the error
        ERROR        → CONNECTED  : successful reconnect()
        DISCONNECTED → DISCOVERED : device found by scan but not yet connected
        DISCOVERED   → CONNECTED  : user-triggered connect after scan
        CONNECTED    → BUSY       : send_command() dispatch begins
        BUSY         → CONNECTED  : send_command() dispatch completes, device still reports connected
        BUSY         → ERROR      : dispatch completes but the device no longer reports connected,
                                     and an error was recorded
    """

    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    ERROR = "error"
    DISCOVERED = "discovered"
    BUSY = "busy"


@dataclass(frozen=True, slots=True)
class PortDescriptor:
    port: str
    description: str
    hwid: str
    assignment: str = "auto"
    owner: str = ""
    last_probe: str = ""


@dataclass(frozen=True, slots=True)
class PortRefreshData:
    """One refresh_device_ports() result: candidate ports/devices for every
    registered device family (see device_lifecycle.register_device_family),
    keyed by family key (``PUMP``/``SWITCH``/``SELECTOR`` today - a future
    family, e.g. a camera, is just another key in ``ports_by_family``, not a
    new field here).

    ``pump_ports``/``valve_ports``/``selector_devices`` remain as read-only
    properties for existing call sites that read them directly - new code
    should prefer ``ports_for(key)``.
    """

    generation: int
    ports_by_family: dict[str, list[object]] = field(default_factory=dict)
    amf_tools_available: bool = False

    def ports_for(self, family_key: str) -> list[object]:
        return list(self.ports_by_family.get(family_key, []))

    @property
    def pump_ports(self) -> list[object]:
        return self.ports_for(PUMP)

    @property
    def valve_ports(self) -> list[object]:
        return self.ports_for(SWITCH)

    @property
    def selector_devices(self) -> list[object]:
        return self.ports_for(SELECTOR)


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    uuid: str
    label: str
    type: str
    driver: str
    endpoint: str | None = None
    role: str | None = None
    display_name: str | None = None
    identity: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""


def new_device_profile(
    *,
    label: str,
    type: str,
    driver: str,
    endpoint: str | None = None,
    role: str | None = None,
    display_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    fingerprint: str = "",
) -> DeviceProfile:
    return DeviceProfile(
        uuid=str(uuid4()),
        label=label,
        type=type,
        driver=driver,
        endpoint=endpoint,
        role=role,
        display_name=display_name,
        metadata=dict(metadata) if metadata else {},
        fingerprint=str(fingerprint or "").strip(),
    )


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    uuid: str
    label: str
    type: str
    driver: str
    endpoint: str | None
    connected: bool
    state: str
    last_error: str | None = None
    identity: dict[str, str] = field(default_factory=dict)
    display_name: str | None = None
    role: str | None = None


def device_inventory_rows(statuses: list[DeviceStatus]) -> list[list[str]]:
    """Flatten DeviceStatus objects into plain string rows for
    HDF5MeasurementWriter.write_device_inventory() - column order must match
    lspr_io.LSPR_DEVICE_INVENTORY_COLUMNS (label, type, role, driver, endpoint,
    display_name, model, serial_number, connected). Keeping this here (not in
    storage/hdf5_export.py) means the storage layer never needs to import a
    device-layer dataclass - every other _upsert_table caller in that module
    already only ever receives plain string rows, not domain objects."""
    rows: list[list[str]] = []
    for status in statuses:
        rows.append(
            [
                status.label,
                status.type,
                status.role or "",
                status.driver,
                status.endpoint or "",
                status.display_name or "",
                status.identity.get("model", ""),
                status.identity.get("serial_number", ""),
                "true" if status.connected else "false",
            ]
        )
    return rows


@dataclass(frozen=True, slots=True)
class ProbeResult:
    endpoint: str
    detected_type: str | None
    driver: str | None
    identity: dict[str, str]
    success: bool
    error: str | None
    duration_ms: float


@dataclass(frozen=True, slots=True)
class DeviceCommand:
    command_type: str
    payload: dict[str, object] = field(default_factory=dict)
    timeout_s: float = 2.0


@dataclass(frozen=True, slots=True)
class DeviceCommandResult:
    label: str
    command_type: str
    success: bool
    response: object | None
    error: str | None
    duration_ms: float


@dataclass(frozen=True, slots=True)
class DeviceEvent:
    timestamp_s: float
    label: str | None
    endpoint: str | None
    owner: str
    action: str
    command: str | None
    result: str
    duration_ms: float
    message: str


def next_device_label(existing_labels: set[str], device_type: str) -> str:
    prefix = f"{device_type}_"
    numbers: list[int] = []
    for label in existing_labels:
        if label.startswith(prefix):
            suffix = label[len(prefix):]
            if suffix.isdigit():
                numbers.append(int(suffix))
    next_number = 1
    while next_number in numbers:
        next_number += 1
    return f"{prefix}{next_number}"
