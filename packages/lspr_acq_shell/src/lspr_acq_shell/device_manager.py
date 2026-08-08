"""`DeviceCommunicationService` - the single owner of hardware I/O for every
connected pump/switch/selector device: profiles, connections, command
dispatch, and the two-lock discipline that keeps bookkeeping reads fast
while hardware I/O stays serialized.

Extracted from singleLSPR Acquisition's `device/device_manager.py` (Phase 1,
2026-08-08), part of the fluidics device framework verbatim move (§4.2) -
preserved exactly, same two-lock design, same locking comments, no logic
changes. This is concurrency-sensitive, real-hardware-I/O code with a
documented incident history (see
apps/sLSPR/acq/docs/device-layer/DEVICE_LAYER_AUDIT_2026.md) - real
pump/valve/selector hardware re-verification after this move is still
outstanding, same caveat the 2026-08-06 registry generalization already
carries.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from collections import deque
from time import perf_counter
from typing import Callable

from lspr_acq_shell.amf_mswitch import AMFSwitchController, amf_tools_available, detect_amf_selector_devices
from lspr_acq_shell.device_driver import DeviceDriver
from lspr_acq_shell.communication_models import (
    DeviceCommand,
    DeviceCommandResult,
    DeviceEvent,
    DeviceLifecycleState,
    DeviceProfile,
    DeviceStatus,
    PortDescriptor,
    PortRefreshData,
    ProbeResult,
    next_device_label,
    new_device_profile,
)
from lspr_acq_shell.connection_registry import release_port, snapshot_port_ownership
from lspr_acq_shell.device_types import PUMP, SELECTOR, SWITCH
from lspr_acq_shell.port_assignments import device_assignment_label, get_port_assignment, set_port_assignment
from lspr_acq_shell.reglo_icc import PumpProbe, RegloICCClient
from lspr_acq_shell.serial_controllers import ControllerError, SerialController
from lspr_acq_shell.valve_controllers import detect_valve_controller
from lspr_acq_shell.settings_store import load_app_setting, save_app_setting


_DEVICE_PROFILE_SETTING_KEY = "device_profiles"
_DEVICE_COMM_SERVICE: "DeviceCommunicationService" | None = None
_DEVICE_COMM_SERVICE_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)


def _normalize_endpoint_value(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.casefold() in {"none", "null", "n/a", "-"}:
        return None
    return text


def _normalize_device_type(value: object) -> str:
    text = str(value or "unknown").strip() or "unknown"
    lowered = text.casefold()
    if lowered == "valve":
        return "switch"
    if lowered == "mswitch":
        return "selector"
    return text


def _normalize_optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.casefold() in {"none", "null", "n/a", "-"}:
        return None
    return text


def extract_usb_fingerprint(hwid: str) -> str:
    """Extract a stable device fingerprint from a USB port HWID string.

    Looks for SER= (USB serial number) first, then falls back to VID:PID.
    Returns an empty string if no useful identifier can be extracted.
    """
    for part in str(hwid or "").split():
        if part.upper().startswith("SER="):
            serial = part[4:].strip()
            if serial and serial.upper() not in {"0", "000000", "NONE", "NULL", "N/A", ""}:
                return f"usb-ser:{serial}"
    return ""

@dataclass(frozen=True, slots=True)
class _LabelMigration:
    new_label: str
    display_name: str
    role: str
    # Stored in profile metadata so the original label can be traced after migration.
    # None means the old label is not worth preserving (e.g. generic "valve_1").
    legacy_label: str | None


_LEGACY_LABEL_MIGRATIONS: dict[str, _LabelMigration] = {
    "pump_main":    _LabelMigration("pump_1",     "Main Pump",      "sample_pump",   "pump_main"),
    "pump_aux":     _LabelMigration("pump_2",     "Aux Pump",       "aux_pump",      "pump_aux"),
    "pump_waste":   _LabelMigration("pump_3",     "Waste Pump",     "waste_pump",    "pump_waste"),
    # "valve_*" labels from before the rename to "switch"
    "valve_inlet":  _LabelMigration("switch_1",   "Inlet Switch",   "inlet_switch",  "valve_inlet"),
    "valve_outlet": _LabelMigration("switch_2",   "Outlet Switch",  "outlet_switch", "valve_outlet"),
    "valve_1":      _LabelMigration("switch_1",   "Inlet Switch",   "inlet_switch",  None),
    "valve_2":      _LabelMigration("switch_2",   "Outlet Switch",  "outlet_switch", None),
    # "switch_main" was an old label for the AMF selector (before "selector" type was established)
    "switch_main":  _LabelMigration("selector_1", "Main Selector",  "main_selector", "switch_main"),
}


# ── Driver connect-factory registry ──────────────────────────────────────────────
#
# Generalizes _connect_impl()'s construction step (2026-08-08, LSPRi acq Phase 2):
# reglo_icc/amf-mswitch/valve-detect stay exactly as they were (three hardcoded
# branches below, untouched), but a new driver key (e.g. a camera or
# illumination-source driver in a different app built on this same service) can
# now register itself here instead of needing a fourth hardcoded branch. This
# mirrors the "register instead of hardcode a branch" idiom
# device_lifecycle.register_device_family() already established for discovery -
# this is the matching generalization for *connection construction*, which that
# earlier pass did not cover (discovery and connect are different steps: a family
# can be discoverable via register_device_family() and still have had no way to
# actually connect, before this).

DriverConnectFactory = Callable[[str], tuple[object, dict[str, str]]]

_DRIVER_CONNECT_FACTORIES: dict[str, DriverConnectFactory] = {}


def register_driver_connect_factory(driver_key: str, factory: DriverConnectFactory) -> None:
    """Register how to construct-and-connect a connection object for a profile
    whose ``driver`` field equals *driver_key*.

    ``factory(endpoint)`` must construct the driver, call its own connect
    method itself (this service never calls a second connect step), and
    return ``(connection, identity)``:

    - ``connection`` must expose ``._claim_owner`` (per-instance
      ``f"{controller_type}:{id(self)}"`` - the invariant every existing
      driver already follows, see
      ``apps/sLSPR/acq/docs/device-layer/DEVICE_LAYER_AUDIT_2026.md``),
      ``.is_connected()``, and ``.close()`` - the same surface
      ``RegloICCClient``/``AMFSwitchController``/valve controllers already
      implement and this service already calls generically via ``getattr()``
      in ``disconnect()``/``status()``/``is_connected()``. Only
      ``_connect_impl()``'s construction step was ever hardcoded to three
      concrete classes - the rest of the lifecycle was already generic.
    - ``identity`` is a flat ``dict[str, str]`` passed straight to
      ``_make_status()`` (``model``/``serial_number``/... - whatever fields
      the driver has; no fixed shape required beyond "string values").

    Checked in ``_connect_impl()`` *before* the existing valve catch-all
    branch (which matches on "any non-empty/non-auto/non-unknown driver
    string" and would otherwise silently swallow a new driver key by routing
    it into ``detect_valve_controller()``) - registering a driver key here
    always takes priority over that catch-all.
    """
    _DRIVER_CONNECT_FACTORIES[driver_key] = factory


class DeviceCommunicationService:
    def __init__(self) -> None:
        # RLock (reentrant) because connect() calls disconnect() internally.
        self._lock = threading.RLock()
        # Separate, fast lock guarding self._profiles/_connections/_last_errors/
        # _events only - see _state() below. RLock because list_statuses() calls
        # status() reentrantly while already holding it. Never held while
        # acquiring self._lock (one-way nesting only: self._lock may acquire
        # this lock, never the reverse), so the two locks cannot deadlock each
        # other regardless of thread count. See docs/device-layer/
        # DEVICE_LAYER_AUDIT_2026.md item 29 (B4) for the full design.
        self._state_lock = threading.RLock()
        self._debug_timing = False
        self._deep_timing = False
        self._timing_records: deque[tuple[str, float, float]] = deque(maxlen=500)
        self._profiles: dict[str, DeviceProfile] = {}
        self._connections: dict[str, object] = {}
        self._connection_owners: dict[str, str] = {}
        self._last_errors: dict[str, str] = {}
        # Labels with a send_command() dispatch currently in flight - status
        # reads report BUSY (see _make_status) while a label is in here.
        # Status/display only, not a correctness guard: device_io_pool() is
        # already single-lane, so overlapping real commands can't happen
        # regardless. State-lock protected like every other bookkeeping
        # dict here.
        self._busy_labels: set[str] = set()
        self._events: deque[DeviceEvent] = deque(maxlen=1000)
        self.load_profiles()

    @classmethod
    def shared(cls) -> "DeviceCommunicationService":
        global _DEVICE_COMM_SERVICE
        if _DEVICE_COMM_SERVICE is None:
            with _DEVICE_COMM_SERVICE_LOCK:
                if _DEVICE_COMM_SERVICE is None:
                    _DEVICE_COMM_SERVICE = cls()
                    _DEVICE_COMM_SERVICE.ensure_default_profiles()
        return _DEVICE_COMM_SERVICE

    def configure_timing(self, *, debug: bool = False, deep: bool = False) -> None:
        self._debug_timing = debug
        self._deep_timing = deep

    @contextmanager
    def _device_lock(self, operation: str):
        """Acquire the hardware-I/O lock, recording wait time when timing is enabled.

        The slow, real-hardware-touching public methods (connect/disconnect/
        send_command/probe_endpoint/refresh_device_ports) and the profile-
        mutation methods serialize through this lock — device communication
        is intentionally sequential, service-wide, regardless of which device
        is involved. Use debug/deep diagnostic profiles to measure contention
        between concurrent callers. Re-entrant: connect() calls disconnect()
        internally; both use this lock.

        This does NOT guard self._profiles/_connections/_last_errors/_events -
        see _state() for that. Methods that only read that bookkeeping
        (is_connected, status, list_statuses, connection, list_profiles,
        get_profile, list_events) never acquire this lock at all, so they
        never block behind a slow operation here. See docs/device-layer/
        DEVICE_LAYER_AUDIT_2026.md item 29 (B4).
        """
        t0 = perf_counter()
        with self._lock:
            if self._debug_timing:
                wait_ms = (perf_counter() - t0) * 1000.0
                self._timing_records.append((operation, wait_ms, t0))
                if wait_ms > 5.0:
                    _LOGGER.debug("[device-timing] %.1f ms wait — %s", wait_ms, operation)
            yield

    @contextmanager
    def _state(self, operation: str):
        """Acquire the fast state lock guarding self._profiles/_connections/
        _last_errors/_events, recording wait time (prefixed "state:") when
        timing is enabled.

        Kept deliberately separate from _device_lock (the slow hardware-I/O
        lock) so pure bookkeeping reads/writes never wait behind real device
        I/O. Critical sections here must stay short - no hardware I/O, no
        file I/O, no calls back into _device_lock - or this defeats the whole
        point of the split. Never acquired while already holding _device_lock
        from the *reverse* direction (i.e. this may be nested inside
        _device_lock, never the other way around) - that one-way nesting is
        what makes deadlock impossible between the two locks. See
        docs/device-layer/DEVICE_LAYER_AUDIT_2026.md item 29 (B4).
        """
        t0 = perf_counter()
        with self._state_lock:
            if self._debug_timing:
                wait_ms = (perf_counter() - t0) * 1000.0
                self._timing_records.append((f"state:{operation}", wait_ms, t0))
                if wait_ms > 5.0:
                    _LOGGER.debug("[device-timing] %.1f ms wait — state:%s", wait_ms, operation)
            yield

    def timing_summary(self) -> dict[str, dict] | None:
        if not self._debug_timing or not self._timing_records:
            return None
        by_op: dict[str, list[float]] = {}
        for op, wait_ms, _ in self._timing_records:
            by_op.setdefault(op, []).append(wait_ms)
        return {
            op: {
                "count": len(times),
                "avg_ms": sum(times) / len(times),
                "max_ms": max(times),
                "min_ms": min(times),
            }
            for op, times in sorted(by_op.items())
        }

    def list_profiles(self) -> list[DeviceProfile]:
        with self._state("list_profiles"):
            return list(self._profiles.values())

    def get_profile(self, label: str) -> DeviceProfile | None:
        with self._state(f"get_profile:{label}"):
            return self._profiles.get(self._canonical_label(label))

    def save_profile(self, profile: DeviceProfile) -> None:
        with self._device_lock("save_profile"):
            original_label = str(profile.label or "").strip()
            normalized = self._normalize_profile(profile)
            with self._state("save_profile_commit"):
                if original_label and original_label != normalized.label:
                    self._profiles.pop(original_label, None)
                self._profiles[normalized.label] = normalized
            self.save_profiles()

    def delete_profile(self, label: str) -> None:
        with self._device_lock("delete_profile"):
            normalized_label = self._canonical_label(label)
            if not normalized_label:
                return
            self._disconnect_impl(normalized_label)
            with self._state(f"delete_profile_commit:{normalized_label}"):
                self._profiles.pop(normalized_label, None)
            self.save_profiles()

    def update_profile_identity(
        self,
        label: str,
        *,
        fingerprint: str | None = None,
        identity: dict[str, str] | None = None,
    ) -> DeviceProfile | None:
        """Update an existing profile's fingerprint/identity in place, without
        touching its label or endpoint and without searching any other
        profile.

        Used by the canonical device lifecycle (pump_1/switch_1/selector_1,
        see device_lifecycle.py) to record what physical unit is currently
        plugged in. Deliberately separate from find_or_create_profile(),
        which resolves a label by searching for a fingerprint/endpoint match
        across *all* profiles - correct for the general multi-profile Device
        Manager discovery flow, but wrong for the fixed canonical labels: if
        a stale profile elsewhere already held a matching fingerprint,
        find_or_create_profile would silently connect the real device under
        that other label instead of the canonical one, which is exactly what
        every other connectivity check in the app (device_label_for())
        assumes it can rely on. See
        apps/sLSPR/acq/docs/device-layer/DEVICE_LAYER_AUDIT_2026.md for the
        incident this fixes. Returns None if no profile exists at *label*
        yet - callers should call
        register_endpoint_assignment()/ensure_device_profile() first to
        guarantee one does.
        """
        with self._device_lock(f"update_profile_identity:{label}"):
            with self._state(f"update_profile_identity_read:{label}"):
                normalized_label = self._canonical_label(label)
                profile = self._profiles.get(normalized_label)
            if profile is None:
                return None
            updated = replace(
                profile,
                fingerprint=str(fingerprint) if fingerprint is not None else profile.fingerprint,
                identity=dict(identity) if identity is not None else dict(profile.identity),
            )
            with self._state(f"update_profile_identity_commit:{normalized_label}"):
                self._profiles[normalized_label] = updated
            self.save_profiles()
            return updated

    def scan_passive(self) -> list[PortDescriptor]:
        ownership = snapshot_port_ownership()
        ports = SerialController.list_ports()
        return [
            PortDescriptor(
                port=port.device,
                description=port.description,
                hwid=port.hwid,
                assignment=get_port_assignment(port.device),
                owner=ownership.get(port.device, ""),
                last_probe="",
            )
            for port in ports
        ]

    def probe_endpoint(self, endpoint: str, expected_type: str | None = None) -> ProbeResult:
        # Locked like every other public method that touches real hardware
        # (connect/disconnect/send_command/refresh_device_ports): this is
        # reachable synchronously from the GUI thread (Device Manager's
        # Probe/assign tab) and does real serial/AMF-SDK I/O, so it must not
        # be allowed to run concurrently with a connect/command/discover
        # happening on the device I/O pool's worker thread. Previously
        # unlocked - found during the 2026-07-21 device-layer audit (see
        # docs/device-layer/DEVICE_LAYER_AUDIT_2026.md); refresh_device_ports'
        # R7/R8 fix comment claimed it was "the one method missing the lock",
        # which turned out to be incomplete.
        with self._device_lock(f"probe_endpoint:{endpoint}"):
            return self._probe_endpoint_impl(endpoint, expected_type)

    def _probe_endpoint_impl(self, endpoint: str, expected_type: str | None = None) -> ProbeResult:
        started = perf_counter()
        endpoint = str(endpoint or "").strip()
        if not endpoint:
            result = ProbeResult(endpoint="", detected_type=None, driver=None, identity={}, success=False, error="No endpoint selected.", duration_ms=0.0)
            self._record_event(label=None, endpoint=None, owner="device_comm:probe", action="probe", command=None, result="fail", duration_ms=0.0, message=result.error or "")
            return result

        expected = str(expected_type or "auto").strip().casefold()
        pump_error = None
        valve_error = None
        switch_error = None
        owner = "device_comm:probe"

        try:
            if expected in {"pump", "reglo", "reglo-icc", "auto"}:
                pump = RegloICCClient.probe_port(endpoint)
                identity = {
                    "model": pump.model,
                    "serial_number": pump.serial_number,
                    "protocol_version": pump.protocol_version,
                    "channel_count": str(pump.channel_count),
                }
                result = ProbeResult(endpoint, "pump", "reglo_icc", identity, True, None, (perf_counter() - started) * 1000.0)
                self._record_event(label="pump", endpoint=endpoint, owner=owner, action="probe", command=None, result="success", duration_ms=result.duration_ms, message="REGLO ICC probe")
                return result
        except Exception as pump_exc:
            pump_error = str(pump_exc)

        try:
            if expected in {"switch", "valve", "auto"}:
                controller, probe = detect_valve_controller(endpoint)
                try:
                    identity = {
                        "model": probe.model,
                        "serial_number": probe.serial_number or "",
                        "protocol_version": probe.protocol_version or "",
                        "controller_type": probe.controller_type,
                    }
                    result = ProbeResult(endpoint, "switch", probe.controller_type, identity, True, None, (perf_counter() - started) * 1000.0)
                    self._record_event(label="switch", endpoint=endpoint, owner=owner, action="probe", command=None, result="success", duration_ms=result.duration_ms, message=probe.model)
                    return result
                finally:
                    controller.close()
        except Exception as valve_exc:
            valve_error = str(valve_exc)

        try:
            if expected in {"selector", "auto"} and amf_tools_available():
                controller, probe = self._probe_selector(endpoint)
                try:
                    identity = {
                        "model": probe.model,
                        "serial_number": probe.serial_number or "",
                        "protocol_version": probe.protocol_version or "",
                        "controller_type": probe.controller_type,
                    }
                    result = ProbeResult(endpoint, "selector", probe.controller_type, identity, True, None, (perf_counter() - started) * 1000.0)
                    self._record_event(label="selector", endpoint=endpoint, owner=owner, action="probe", command=None, result="success", duration_ms=result.duration_ms, message=probe.model)
                    return result
                finally:
                    controller.close()
        except Exception as switch_exc:
            switch_error = str(switch_exc)

        error = "; ".join(e for e in (pump_error, valve_error, switch_error) if e) or "No matching device detected."
        result = ProbeResult(endpoint, None, None, {}, False, error, (perf_counter() - started) * 1000.0)
        self._record_event(label=None, endpoint=endpoint, owner=owner, action="probe", command=None, result="fail", duration_ms=result.duration_ms, message=error)
        return result

    def connect(self, label: str, *, cached_pump_probe: PumpProbe | None = None) -> DeviceStatus:
        with self._device_lock(f"connect:{label}"):
            return self._connect_impl(label, cached_pump_probe=cached_pump_probe)

    def _connect_impl(self, label: str, *, cached_pump_probe: PumpProbe | None = None) -> DeviceStatus:
        profile = self._require_profile(label)
        self._disconnect_impl(label)
        endpoint = str(profile.endpoint or "").strip()
        if not endpoint:
            raise ControllerError(f"Device profile {label!r} has no endpoint.")

        try:
            if profile.driver == "reglo_icc" or profile.type == "pump":
                client = RegloICCClient()
                client.connect(endpoint)
                probe = cached_pump_probe if cached_pump_probe is not None else client.get_probe()
                with self._state(f"connect_commit:{profile.label}"):
                    self._connections[profile.label] = client
                    self._connection_owners[profile.label] = client._claim_owner
                    self._last_errors.pop(profile.label, None)
                    status = self._make_status(profile.label, profile, True, None, {
                        "model": probe.model,
                        "serial_number": probe.serial_number,
                        "protocol_version": probe.protocol_version,
                        "channel_count": str(probe.channel_count),
                    })
                self._record_event(label=profile.label, endpoint=endpoint, owner=client._claim_owner, action="connect", command=None, result="success", duration_ms=0.0, message=probe.model)
                return status

            if profile.driver == "amf-mswitch" or profile.type == "selector":
                controller = AMFSwitchController()
                controller.connect(endpoint)
                probe = controller.get_probe()
                with self._state(f"connect_commit:{profile.label}"):
                    self._connections[profile.label] = controller
                    self._connection_owners[profile.label] = controller._claim_owner
                    self._last_errors.pop(profile.label, None)
                    status = self._make_status(profile.label, profile, True, None, {
                        "model": probe.model,
                        "serial_number": probe.serial_number or "",
                        "protocol_version": probe.protocol_version or "",
                        "controller_type": probe.controller_type,
                    })
                self._record_event(label=profile.label, endpoint=endpoint, owner=self._connection_owners[profile.label], action="connect", command=None, result="success", duration_ms=0.0, message=probe.model)
                return status

            factory = _DRIVER_CONNECT_FACTORIES.get(profile.driver)
            if factory is not None:
                connection, identity = factory(endpoint)
                with self._state(f"connect_commit:{profile.label}"):
                    self._connections[profile.label] = connection
                    self._connection_owners[profile.label] = connection._claim_owner
                    self._last_errors.pop(profile.label, None)
                    status = self._make_status(profile.label, profile, True, None, identity)
                self._record_event(label=profile.label, endpoint=endpoint, owner=connection._claim_owner, action="connect", command=None, result="success", duration_ms=0.0, message=identity.get("model", ""))
                return status

            if profile.type in {"switch", "valve"} or profile.driver not in {"auto", "unknown", ""}:
                controller, probe = detect_valve_controller(endpoint)
                with self._state(f"connect_commit:{profile.label}"):
                    self._connections[profile.label] = controller
                    self._connection_owners[profile.label] = controller._claim_owner
                    self._last_errors.pop(profile.label, None)
                    status = self._make_status(profile.label, profile, True, None, {
                        "model": probe.model,
                        "serial_number": probe.serial_number or "",
                        "protocol_version": probe.protocol_version or "",
                        "controller_type": probe.controller_type,
                    })
                self._record_event(label=profile.label, endpoint=endpoint, owner=controller._claim_owner, action="connect", command=None, result="success", duration_ms=0.0, message=probe.model)
                return status

            raise ControllerError(
                f"Cannot connect {label!r}: device type {profile.type!r} / driver {profile.driver!r} is unresolved. "
                "Probe the port first or assign a device type."
            )
        except Exception as exc:
            with self._state(f"connect_fail:{profile.label}"):
                self._last_errors[profile.label] = str(exc)
            self._record_event(label=profile.label, endpoint=endpoint, owner=f"device_comm:{profile.label}", action="connect", command=None, result="fail", duration_ms=0.0, message=str(exc))
            raise

    def disconnect(self, label: str) -> DeviceStatus:
        with self._device_lock(f"disconnect:{label}"):
            return self._disconnect_impl(label)

    def _disconnect_impl(self, label: str) -> DeviceStatus:
        normalized = self._canonical_label(label)
        # Pop self._connections BEFORE the real close() I/O below (not after):
        # a concurrent fast reader (is_connected/status/connection, which only
        # take _state_lock) must never observe "connected" for a connection
        # whose teardown is already irreversibly in progress. See
        # docs/device-layer/DEVICE_LAYER_AUDIT_2026.md item 29 (B4).
        with self._state(f"disconnect_pop:{normalized}"):
            profile = self._profiles.get(normalized)
            connection = self._connections.pop(normalized, None)
        owner = self._connection_owners.pop(normalized, None)
        endpoint = getattr(connection, "port", None) if connection is not None else (profile.endpoint if profile is not None else None)
        if endpoint and owner:
            release_port(endpoint, owner)
        if connection is not None:
            try:
                close = getattr(connection, "close", None)
                if callable(close):
                    close()
            finally:
                with self._state(f"disconnect_clear_error:{normalized}"):
                    self._last_errors.pop(normalized, None)
        if profile is None and connection is None:
            raise ControllerError(f"Unknown device label {label!r}.")
        if profile is None:
            profile = new_device_profile(label=normalized, type="unknown", driver=type(connection).__name__, endpoint=getattr(connection, "port", None))
        self._record_event(label=normalized, endpoint=str(endpoint or "") or None, owner=owner or "", action="disconnect", command=None, result="success", duration_ms=0.0, message="")
        with self._state(f"disconnect_status:{normalized}"):
            last_error = self._last_errors.get(normalized)
            return self._make_status(normalized, profile, False, last_error, {})

    def disconnect_device(self, label: str) -> None:
        self.disconnect(label)

    def send_command(self, label: str, command: DeviceCommand) -> DeviceCommandResult:
        with self._device_lock(f"send_command:{label}"):
            started = perf_counter()
            normalized = str(label or "").strip()
            with self._state(f"send_command_lookup:{normalized}"):
                connection = self._connections.get(normalized)
            if connection is None:
                return DeviceCommandResult(normalized, command.command_type, False, None, "Device is not connected.", 0.0)

            with self._state(f"send_command_mark_busy:{normalized}"):
                self._busy_labels.add(normalized)
            try:
                try:
                    response = self._dispatch_command(connection, command)
                    with self._state(f"send_command_clear_error:{normalized}"):
                        self._last_errors.pop(normalized, None)
                    result = DeviceCommandResult(normalized, command.command_type, True, response, None, (perf_counter() - started) * 1000.0)
                    self._record_event(label=normalized, endpoint=str(getattr(connection, "port", None) or ""), owner=self._connection_owners.get(normalized, ""), action="command", command=command.command_type, result="success", duration_ms=result.duration_ms, message="")
                    return result
                except Exception as exc:
                    with self._state(f"send_command_set_error:{normalized}"):
                        self._last_errors[normalized] = str(exc)
                    result = DeviceCommandResult(normalized, command.command_type, False, None, str(exc), (perf_counter() - started) * 1000.0)
                    self._record_event(label=normalized, endpoint=str(getattr(connection, "port", None) or ""), owner=self._connection_owners.get(normalized, ""), action="command", command=command.command_type, result="fail", duration_ms=result.duration_ms, message=str(exc))
                    return result
            finally:
                with self._state(f"send_command_clear_busy:{normalized}"):
                    self._busy_labels.discard(normalized)

    def status(self, label: str) -> DeviceStatus:
        with self._state(f"status:{label}"):
            normalized = self._canonical_label(label)
            profile = self._profiles.get(normalized)
            if profile is None:
                connection = self._connections.get(normalized)
                if connection is None:
                    raise ControllerError(f"Unknown device label {label!r}.")
                profile = new_device_profile(
                    label=normalized,
                    type="unknown",
                    driver=type(connection).__name__,
                    endpoint=getattr(connection, "port", None),
                )
            connection = self._connections.get(normalized)
            connected = bool(connection is not None and getattr(connection, "is_connected", lambda: False)())
            return self._make_status(normalized, profile, connected, self._last_errors.get(normalized), dict(profile.identity))

    def list_statuses(self) -> list[DeviceStatus]:
        # Reentrant: calls self.status() per label while already holding
        # _state_lock - safe because _state_lock is an RLock. See _state().
        with self._state("list_statuses"):
            labels = list(self._profiles)
            for label in self._connections:
                if label not in self._profiles:
                    labels.append(label)
            return [self.status(label) for label in labels]

    # Backward-compatible name used by older UI code.
    def list_devices(self) -> list[DeviceStatus]:
        return self.list_statuses()

    def connection(self, label: str) -> object | None:
        with self._state(f"connection:{label}"):
            return self._connections.get(self._canonical_label(label))

    def is_connected(self, label: str) -> bool:
        with self._state(f"is_connected:{label}"):
            connection = self._connections.get(self._canonical_label(label))
            return bool(connection is not None and getattr(connection, "is_connected", lambda: False)())

    def ensure_default_profiles(self) -> None:
        with self._device_lock("ensure_default_profiles"):
            _default = {"source": "default"}
            # Only the 3 canonical devices (see device_lifecycle's device family
            # registry - register_device_family()/device_label_for()) are ever
            # driven by the experiment-control pump plan / step runner. Extra profiles
            # (a second pump, a second switch, etc.) are a real but uncommon setup - the
            # user creates those explicitly via the Device Manager's deep-debug tools,
            # they are not auto-seeded here.
            defaults = (
                new_device_profile(label="pump_1", type="pump", driver="reglo_icc", role="sample_pump", display_name="Main Pump", metadata=_default),
                new_device_profile(label="switch_1", type="switch", driver="auto", role="inlet_switch", display_name="Inlet Switch", metadata=_default),
                new_device_profile(label="selector_1", type="selector", driver="amf-mswitch", role="main_selector", display_name="Main Selector", metadata=_default),
            )
            with self._state("ensure_default_profiles_commit"):
                if not self._profiles:
                    for profile in defaults:
                        self._profiles[profile.label] = profile
                else:
                    for profile in defaults:
                        self._profiles.setdefault(profile.label, profile)
            self.save_profiles()

    def register_endpoint_assignment(
        self,
        label: str,
        endpoint: str,
        device_type: str = "auto",
        driver: str = "auto",
        role: str | None = None,
        *,
        mark_manual: bool = True,
    ) -> DeviceProfile:
        """Register/update a device profile's endpoint.

        *mark_manual* controls whether *endpoint* is also pinned in the
        persistent port-assignment store (``set_port_assignment``), which
        future scans treat as an explicit, permanent user override that
        outranks live discovery. Callers that merely attempt a connect
        (automatic startup auto-connect, or a user clicking "Connect" on
        whatever the scan currently has selected) must pass
        ``mark_manual=False`` - pinning on every attempt, success or
        failure, is what let stale/wrong ports (from COM numbers shifting
        across sessions) permanently outrank fresh scan results. Only a
        deliberate, dedicated "assign this port" action should pin.
        """
        with self._device_lock(f"register_endpoint:{label}"):
            normalized_type = _normalize_device_type(device_type or "auto")
            normalized_endpoint = _normalize_endpoint_value(endpoint)
            with self._state(f"register_endpoint_commit:{label}"):
                normalized_label = str(label or "").strip()
                if not normalized_label:
                    normalized_label = next_device_label(set(self._profiles), device_type if device_type != "auto" else "device")
                normalized_label = self._canonical_label(normalized_label)
                profile = new_device_profile(label=normalized_label, type=normalized_type, driver=driver, endpoint=normalized_endpoint, role=role)
                self._profiles[normalized_label] = profile
            if normalized_endpoint and mark_manual:
                set_port_assignment(normalized_endpoint, device_assignment_label(normalized_type))
            self.save_profiles()
            return profile

    def find_or_create_profile(
        self,
        *,
        device_type: str,
        fingerprint: str,
        endpoint: str,
        identity: dict[str, str],
        driver: str = "auto",
        display_name: str | None = None,
        role: str | None = None,
    ) -> DeviceProfile:
        """Return an existing profile that matches (type, fingerprint) or create a new one.

        If a matching profile is found on a different endpoint (COM port changed), the
        endpoint is updated in place and persisted. On creation, the next available label
        for *device_type* is auto-assigned (pump_1, pump_2, ...).
        """
        # Each branch below is restructured as: state-lock block does the
        # lookup/mutation and computes the result, lock released, then
        # save_profiles() (file I/O) runs unlocked - never do file I/O while
        # holding _state_lock. See docs/device-layer/DEVICE_LAYER_AUDIT_2026.md
        # item 29 (B4). The whole method still runs under the outer I/O lock
        # (self._device_lock), which continues to serialize this against every
        # other connect/disconnect/profile-mutation call exactly as before -
        # only the nested state-lock sections are new.
        with self._device_lock("find_or_create_profile"):
            endpoint = _normalize_endpoint_value(endpoint) or ""

            with self._state("find_or_create_profile_fingerprint_match"):
                result: DeviceProfile | None = None
                mutated = False
                if fingerprint:
                    for profile in self._profiles.values():
                        if profile.type == device_type and profile.fingerprint == fingerprint:
                            if profile.endpoint != endpoint:
                                result = replace(profile, endpoint=endpoint, identity=dict(identity))
                                self._profiles[profile.label] = result
                                mutated = True
                            else:
                                result = profile
                            break
            if result is not None:
                if mutated:
                    self.save_profiles()
                return result

            with self._state("find_or_create_profile_endpoint_match"):
                result = None
                for profile in self._profiles.values():
                    if profile.type == device_type and _normalize_endpoint_value(profile.endpoint) == endpoint:
                        result = replace(profile, endpoint=endpoint, identity=dict(identity))
                        self._profiles[profile.label] = result
                        break
            if result is not None:
                self.save_profiles()
                return result

            with self._state("find_or_create_profile_reuse"):
                reusable_profiles = [
                    profile
                    for profile in self._profiles.values()
                    if profile.type == device_type and _normalize_endpoint_value(profile.endpoint) is None
                ]
                result = None
                if reusable_profiles:
                    reusable_profiles.sort(key=lambda profile: (profile.metadata.get("source") != "default", profile.label))
                    reused_profile = reusable_profiles[0]
                    result = replace(reused_profile, endpoint=endpoint, identity=dict(identity))
                    self._profiles[reused_profile.label] = result
            if result is not None:
                self.save_profiles()
                return result

            with self._state("find_or_create_profile_create"):
                label = next_device_label(set(self._profiles), device_type)
                new_profile = new_device_profile(
                    label=label,
                    type=device_type,
                    driver=driver,
                    endpoint=endpoint,
                    role=role,
                    display_name=display_name,
                    fingerprint=fingerprint,
                )
                new_profile = replace(new_profile, identity=dict(identity))
                self._profiles[label] = new_profile
            self.save_profiles()
            return new_profile

    def list_events(self) -> list[DeviceEvent]:
        with self._state("list_events"):
            return list(self._events)

    def load_profiles(self) -> None:
        with self._device_lock("load_profiles"):
            raw = load_app_setting(_DEVICE_PROFILE_SETTING_KEY, [])
            with self._state("load_profiles_commit"):
                self._profiles.clear()
                if isinstance(raw, dict):
                    raw = raw.get("devices", [])
                if not isinstance(raw, list):
                    return
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    profile = self._profile_from_payload(item)
                    if profile is None:
                        continue
                    self._profiles[profile.label] = profile

    def save_profiles(self) -> None:
        # Snapshot under the fast state lock (protects self._profiles), then
        # do the real file write (save_app_setting) unlocked - file I/O must
        # never happen while holding _state_lock. See _state()'s docstring.
        with self._state("save_profiles_snapshot"):
            snapshot = list(self._profiles.values())
        payload = {"devices": [asdict(profile) for profile in snapshot]}
        save_app_setting(_DEVICE_PROFILE_SETTING_KEY, payload)

    def _profile_from_payload(self, item: dict[str, object]) -> DeviceProfile | None:
        raw_label = str(item.get("label", "")).strip()
        if not raw_label:
            return None
        migrated = _LEGACY_LABEL_MIGRATIONS.get(raw_label)
        if migrated is not None:
            metadata = dict(item.get("metadata", {}) or {})
            metadata.setdefault("legacy_label", migrated.legacy_label or str(item.get("label", "")).strip())
            raw_label = migrated.new_label
            display_name = migrated.display_name
            role = migrated.role
        else:
            metadata = {str(k): v for k, v in dict(item.get("metadata", {}) or {}).items()}
            display_name = _normalize_optional_text(item.get("display_name", ""))
            role = _normalize_optional_text(item.get("role", ""))

        normalized_type = _normalize_device_type(item.get("type", "unknown"))
        uuid = str(item.get("uuid", "")).strip() or new_device_profile(label=raw_label, type=normalized_type, driver=str(item.get("driver", "auto") or "auto")).uuid
        profile = DeviceProfile(
            uuid=uuid,
            label=raw_label,
            type=normalized_type,
            driver=str(item.get("driver", "auto") or "auto"),
            endpoint=_normalize_endpoint_value(item.get("endpoint", "")),
            role=role,
            display_name=display_name,
            identity={str(k): str(v) for k, v in dict(item.get("identity", {}) or {}).items()},
            enabled=bool(item.get("enabled", True)),
            metadata=metadata,
            fingerprint=str(item.get("fingerprint", "") or "").strip(),
        )
        return self._normalize_profile(profile)

    def _record_event(
        self,
        *,
        label: str | None,
        endpoint: str | None,
        owner: str,
        action: str,
        command: str | None,
        result: str,
        duration_ms: float,
        message: str,
    ) -> None:
        event = DeviceEvent(
            timestamp_s=perf_counter(),
            label=label,
            endpoint=endpoint,
            owner=owner,
            action=action,
            command=command,
            result=result,
            duration_ms=max(float(duration_ms), 0.0),
            message=message,
        )
        with self._state("record_event"):
            self._events.append(event)

    def _normalize_profile(self, profile: DeviceProfile) -> DeviceProfile:
        label = str(profile.label or "").strip()
        if not label:
            raise ControllerError("Device label is required.")
        display_name = profile.display_name or None
        metadata = dict(profile.metadata or {})
        if label in _LEGACY_LABEL_MIGRATIONS:
            m = _LEGACY_LABEL_MIGRATIONS[label]
            metadata.setdefault("legacy_label", m.legacy_label or label)
            label = m.new_label
            display_name = display_name or m.display_name
            role = profile.role or m.role
        else:
            role = profile.role
        return DeviceProfile(
            uuid=str(profile.uuid or "").strip() or new_device_profile(label=label, type=_normalize_device_type(profile.type), driver=profile.driver).uuid,
            label=label,
            type=_normalize_device_type(profile.type),
            driver=str(profile.driver or "auto"),
            endpoint=_normalize_endpoint_value(profile.endpoint),
            role=_normalize_optional_text(role),
            display_name=_normalize_optional_text(display_name),
            identity={str(k): str(v) for k, v in dict(profile.identity or {}).items()},
            enabled=bool(profile.enabled),
            metadata=metadata,
            fingerprint=str(profile.fingerprint or "").strip(),
        )

    def _dispatch_command(self, connection: object, command: DeviceCommand) -> object | None:
        if isinstance(connection, DeviceDriver):
            return connection.execute_command(command)
        raise ControllerError(f"Unsupported command type {command.command_type!r} for {type(connection).__name__}.")

    def _require_profile(self, label: str) -> DeviceProfile:
        normalized = self._canonical_label(label)
        with self._state(f"require_profile:{normalized}"):
            profile = self._profiles.get(normalized)
        if profile is None:
            raise ControllerError(f"Unknown device label {label!r}.")
        return profile

    def _canonical_label(self, label: str) -> str:
        normalized = str(label or "").strip()
        migrated = _LEGACY_LABEL_MIGRATIONS.get(normalized)
        if migrated is not None:
            return migrated.new_label
        return normalized

    def _make_status(self, label: str, profile: DeviceProfile, connected: bool, last_error: str | None, identity: dict[str, str]) -> DeviceStatus:
        # Callers already hold _state_lock (via _state(...)) when they reach
        # here, so reading _busy_labels needs no extra locking.
        if connected and label in self._busy_labels:
            state = DeviceLifecycleState.BUSY
        elif connected:
            state = DeviceLifecycleState.CONNECTED
        elif last_error is not None:
            state = DeviceLifecycleState.ERROR
        else:
            state = DeviceLifecycleState.DISCONNECTED
        return DeviceStatus(
            uuid=profile.uuid,
            label=label,
            type=profile.type,
            driver=profile.driver,
            endpoint=profile.endpoint,
            connected=connected,
            state=state,
            last_error=last_error,
            identity=identity,
            display_name=profile.display_name,
            role=profile.role,
        )

    def _probe_selector(self, endpoint: str):
        controller = AMFSwitchController()
        controller.connect(endpoint)
        return controller, controller.get_probe()

    def refresh_device_ports(self, generation: int) -> object:
        # Locked like every other public mutator (connect/disconnect/send_command):
        # the AMF vendor SDK enumeration here is not guaranteed thread-safe against
        # a concurrent connect/command on the same physical device (see R7/R8 in
        # docs/device-layer/DEVICE_LAYER_AUDIT_2026.md). This was the one method
        # missing the lock.
        with self._device_lock("refresh_device_ports"):
            pump_ports = RegloICCClient.list_ports()
            valve_ports = SerialController.list_ports()
            selector_devices = detect_amf_selector_devices() if amf_tools_available() else []
            return PortRefreshData(
                generation=generation,
                ports_by_family={
                    PUMP: list(pump_ports),
                    SWITCH: list(valve_ports),
                    SELECTOR: list(selector_devices),
                },
                amf_tools_available=amf_tools_available(),
            )

    def probe_pump_port(self, port: str):
        return RegloICCClient.probe_port(port)
