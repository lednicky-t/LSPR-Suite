from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import monotonic
import logging


@dataclass(frozen=True, slots=True)
class PortProbeEvent:
    timestamp: float
    phase: str
    port: str
    owner: str
    role: str
    assignment: str
    action: str
    command: str | None
    result: str
    duration_ms: float
    message: str


_PORT_PROBE_EVENTS: deque[PortProbeEvent] = deque(maxlen=500)
_LOGGER = logging.getLogger("lspr_app.usb_probe")


def record_port_probe_event(
    *,
    phase: str,
    port: str,
    owner: str,
    role: str,
    assignment: str,
    action: str,
    command: str | None = None,
    result: str = "success",
    duration_ms: float = 0.0,
    message: str = "",
) -> PortProbeEvent:
    event = PortProbeEvent(
        timestamp=monotonic(),
        phase=str(phase or ""),
        port=str(port or ""),
        owner=str(owner or ""),
        role=str(role or ""),
        assignment=str(assignment or "auto"),
        action=str(action or ""),
        command=command,
        result=str(result or ""),
        duration_ms=max(float(duration_ms), 0.0),
        message=str(message or ""),
    )
    _PORT_PROBE_EVENTS.append(event)
    _LOGGER.info(
        "USB probe | phase=%s role=%s port=%s assignment=%s action=%s command=%s result=%s duration=%.1fms owner=%s message=%s",
        event.phase,
        event.role,
        event.port,
        event.assignment,
        event.action,
        event.command or "",
        event.result,
        event.duration_ms,
        event.owner,
        event.message,
    )
    return event


def snapshot_port_probe_events() -> tuple[PortProbeEvent, ...]:
    return tuple(_PORT_PROBE_EVENTS)
