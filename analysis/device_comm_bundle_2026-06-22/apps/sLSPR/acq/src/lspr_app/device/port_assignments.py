from __future__ import annotations

from typing import Literal

from lspr_app.storage.app_config import load_app_setting, save_app_setting

DeviceAssignment = Literal["auto", "pump", "valve"]

_APP_SETTING_KEY = "manual_port_assignments"
_ASSIGNMENT_LABELS: dict[str, str] = {
    "auto": "Auto",
    "pump": "Pump controller",
    "valve": "Valve controller",
}


def normalize_device_assignment(value: object) -> DeviceAssignment:
    assignment = str(value or "auto").strip().casefold()
    assignment = assignment.replace(" controller", "")
    assignment = assignment.replace("-", " ")
    assignment = assignment.replace("_", " ")
    if assignment in {"pump", "pump ctrl", "pump control"}:
        return "pump"
    if assignment in {"valve", "valve ctrl", "valve control"}:
        return "valve"
    return "auto"


def _load_port_assignments() -> dict[str, DeviceAssignment]:
    raw = load_app_setting(_APP_SETTING_KEY, {})
    if not isinstance(raw, dict):
        return {}
    assignments: dict[str, DeviceAssignment] = {}
    for raw_port, raw_assignment in raw.items():
        port = str(raw_port or "").strip().upper()
        if not port:
            continue
        assignments[port] = normalize_device_assignment(raw_assignment)
    return assignments


def _save_port_assignments(assignments: dict[str, DeviceAssignment]) -> None:
    payload = {port: assignment for port, assignment in sorted(assignments.items()) if assignment != "auto"}
    save_app_setting(_APP_SETTING_KEY, payload)


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
    if role_name not in {"pump", "valve"}:
        return True
    if assignment == "auto":
        return True
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
