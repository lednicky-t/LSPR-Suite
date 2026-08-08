"""Common interface every hardware device driver must implement.

All three driver families (pump, switch/valve, selector) implement this
contract.  The connection lifecycle is identical across all of them; only the
probe return type and the device-specific commands differ.

Extracted from singleLSPR Acquisition's `device/device_driver.py` (Phase 1,
2026-08-08), part of the fluidics device framework verbatim move (§4.2).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from lspr_acq_shell.communication_models import DeviceCommand


class DeviceError(RuntimeError):
    """Base class for all hardware device errors.

    Both ``ControllerError`` (serial/AMF drivers) and ``RegloICCError`` (pump
    driver) inherit from this class, so callers can catch either the specific
    type or ``DeviceError`` to handle any hardware failure uniformly.
    """


class DeviceTimeoutError(DeviceError):
    """A command got no response within its timeout window.

    Distinct from other ``DeviceError`` cases (an explicit device rejection,
    a malformed response) because it's the one failure mode that's safe to
    retry: the command may never have reached the device, or its response
    may have been lost - either way, resending the exact same command is a
    lost-response recovery, not a repeat of an answer that was already given.
    Retry logic (see SerialController._call_with_retry) catches this
    specifically rather than DeviceError broadly, so it never retries a
    command the device actively responded to.
    """


class DeviceDriver(ABC):
    """Abstract base class for hardware device drivers.

    Guarantees that every driver exposes the four connection-lifecycle methods
    that ``DeviceCommunicationService`` relies on.  Device-specific behaviour
    (commands, probe fields) lives in the concrete subclasses.
    """

    @abstractmethod
    def connect(self, endpoint: str) -> None:
        """Open a connection to the device at *endpoint* (port, USB path, …)."""

    @abstractmethod
    def close(self) -> None:
        """Close the connection and release any claimed port resources."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Return ``True`` if the device connection is currently open."""

    @abstractmethod
    def get_probe(self) -> object:
        """Return a probe dataclass with identity information about the device.

        The concrete return type differs per driver family
        (``PumpProbe`` for the pump, ``ControllerProbe`` for switch/selector).
        """

    @abstractmethod
    def execute_command(self, command: DeviceCommand) -> object | None:
        """Execute a typed device command and return an optional response value.

        Raise ``ControllerError`` (or a driver-specific subclass) for unknown
        or unsupported command types.
        """
