"""Manual pump/switch port-assignment persistence (Preferences > port
assignment overrides) - lets a user pin a specific serial port to a role
instead of relying on auto-probing.

Extracted from singleLSPR Acquisition's `device/port_assignments.py` (Phase 1,
2026-08-08), part of the fluidics device framework verbatim move (§4.2).
Settings persistence now goes through `lspr_acq_shell.settings_store`
directly instead of `lspr_app.storage.app_config` - same underlying engine
(that module is itself a shim over this one as of Phase 1 item 1.3.1), just
without the app-layer indirection now that this file lives beside it.
"""

from __future__ import annotations

from typing import Literal

from lspr_acq_shell.settings_store import load_app_setting, save_app_setting

DeviceAssignment = Literal["auto", "pump", "switch"]

_APP_SETTING_KEY = "manual_port_assignments"
_ASSIGNMENT_LABELS: dict[str, str] = {
    "auto": "Auto",
    "pump": "Pump controller",
    "switch": "Switch controller",
}

_assignment_cache: dict[str, DeviceAssignment] | None = None


def normalize_device_assignment(value: object) -> DeviceAssignment:
    assignment = str(value or "auto").strip().casefold()
    assignment = assignment.replace(" controller", "")
    assignment = assignment.replace("-", " ")
    assignment = assignment.replace("_", " ")
    if assignment in {"pump", "pump ctrl", "pump control"}:
        return "pump"
    if assignment in {"switch", "switch ctrl", "switch control", "valve", "valve ctrl", "valve control"}:
        return "switch"
    return "auto"


def _load_port_assignments() -> dict[str, DeviceAssignment]:
    global _assignment_cache
    if _assignment_cache is not None:
        return _assignment_cache
    raw = load_app_setting(_APP_SETTING_KEY, {})
    if not isinstance(raw, dict):
        _assignment_cache = {}
        return _assignment_cache
    assignments: dict[str, DeviceAssignment] = {}
    for raw_port, raw_assignment in raw.items():
        port = str(raw_port or "").strip().upper()
        if not port:
            continue
        assignments[port] = normalize_device_assignment(raw_assignment)
    _assignment_cache = assignments
    return _assignment_cache


def _save_port_assignments(assignments: dict[str, DeviceAssignment]) -> None:
    global _assignment_cache
    payload = {port: assignment for port, assignment in sorted(assignments.items()) if assignment != "auto"}
    save_app_setting(_APP_SETTING_KEY, payload)
    _assignment_cache = dict(assignments)


def snapshot_port_assignments() -> dict[str, DeviceAssignment]:
    return dict(_load_port_assignments())


def get_port_assignment(port: str) -> DeviceAssignment:
    normalized_port = str(port or "").strip().upper()
    if not normalized_port:
        return "auto"
    return _load_port_assignments().get(normalized_port, "auto")


def should_probe_port_for_role(port: str, role: str) -> bool:
    assignment = get_port_assignment(port)
    role_name = str(role or "").strip().casefold()
    if role_name not in {"pump", "switch"}:
        return True
    if assignment == "auto":
        return True
    # Accept both "switch" and legacy "valve" assignment for the switch role
    if role_name == "switch":
        return assignment in {"switch", "valve"}
    return assignment == role_name


def set_port_assignment(port: str, assignment: object) -> DeviceAssignment:
    normalized_port = str(port or "").strip().upper()
    normalized_assignment = normalize_device_assignment(assignment)
    if not normalized_port:
        return normalized_assignment
    assignments = _load_port_assignments()
    if normalized_assignment == "auto":
        assignments.pop(normalized_port, None)
    else:
        assignments[normalized_port] = normalized_assignment
    _save_port_assignments(assignments)
    return normalized_assignment


def clear_port_assignment(port: str) -> None:
    set_port_assignment(port, "auto")


def device_assignment_label(assignment: object) -> str:
    normalized_assignment = normalize_device_assignment(assignment)
    return _ASSIGNMENT_LABELS.get(normalized_assignment, "Auto")


def assignment_for_port_label(port: str) -> str:
    return device_assignment_label(get_port_assignment(port))
