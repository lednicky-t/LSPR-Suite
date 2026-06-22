from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from lspr_app.device.port_assignments import get_port_assignment, should_probe_port_for_role
from lspr_app.device.probe_diagnostics import record_port_probe_event
from lspr_app.device.reglo_icc import PumpPort, RegloICCClient, is_probable_reglo_port
from lspr_app.device.serial_controllers import ControllerPort, SerialController, registered_controllers
from lspr_app.device import valve_controllers  # noqa: F401  # Ensure serial controller registrations are loaded.


@dataclass(slots=True)
class ConnectedSerialDevice:
    port: str
    description: str
    hwid: str
    recognized_device: str
    recognition_state: str
    manual_assignment: str = "auto"
    details: str = ""


def _format_controller_label(controller_type: str | None, model: str | None) -> str:
    kind = str(controller_type or "controller").replace("-", " ").strip()
    if model:
        return f"{kind.title()}: {model}"
    return kind.title()


def _passive_record(port: ControllerPort) -> ConnectedSerialDevice:
    assignment = get_port_assignment(port.device)
    recognized = "COM port"
    if assignment != "auto":
        recognized = f"Assigned {assignment} controller"
    return ConnectedSerialDevice(
        port=port.device,
        description=port.description,
        hwid=port.hwid,
        recognized_device=recognized,
        recognition_state="passive",
        manual_assignment=assignment,
        details="Passive inventory only.",
    )


def _probe_pump_device(port: ControllerPort) -> ConnectedSerialDevice:
    assignment = get_port_assignment(port.device)
    if not should_probe_port_for_role(port.device, "pump"):
        return _passive_record(port)
    pump_port = PumpPort(device=port.device, description=port.description, hwid=port.hwid)
    if not is_probable_reglo_port(pump_port):
        return ConnectedSerialDevice(
            port=port.device,
            description=port.description,
            hwid=port.hwid,
            recognized_device="Unrecognized",
            recognition_state="unrecognized",
            manual_assignment=assignment,
        )

    started = perf_counter()
    try:
        probe = RegloICCClient.probe_port(port.device)
    except Exception as exc:
        record_port_probe_event(
            phase="inventory",
            port=port.device,
            owner="inventory:pump",
            role="pump",
            assignment=assignment,
            action="probe",
            command="0x!",
            result="fail",
            duration_ms=(perf_counter() - started) * 1000.0,
            message=str(exc),
        )
        return ConnectedSerialDevice(
            port=port.device,
            description=port.description,
            hwid=port.hwid,
            recognized_device="Likely pump controller",
            recognition_state="probable",
            manual_assignment=assignment,
            details=str(exc),
        )
    record_port_probe_event(
        phase="inventory",
        port=port.device,
        owner="inventory:pump",
        role="pump",
        assignment=assignment,
        action="probe",
        command="0x!",
        result="success",
        duration_ms=(perf_counter() - started) * 1000.0,
        message=probe.model or "Pump controller",
    )

    details = "; ".join(
        part
        for part in (
            f"serial {probe.serial_number}" if probe.serial_number else "",
            f"protocol {probe.protocol_version}" if probe.protocol_version else "",
            f"{probe.channel_count} channels" if probe.channel_count else "",
        )
        if part
    )
    model = probe.model or "Pump controller"
    return ConnectedSerialDevice(
        port=port.device,
        description=port.description,
        hwid=port.hwid,
        recognized_device=f"Pump controller: {model}",
        recognition_state="confirmed",
        manual_assignment=assignment,
        details=details,
    )


def _matching_controller_classes(port: ControllerPort) -> list[type[SerialController]]:
    return sorted(
        [controller_cls for controller_cls in registered_controllers() if controller_cls.is_probable_port(port)],
        key=lambda cls: cls.priority,
        reverse=True,
    )


def _probe_controller_device(port: ControllerPort) -> ConnectedSerialDevice:
    assignment = get_port_assignment(port.device)
    if not should_probe_port_for_role(port.device, "valve"):
        return _passive_record(port)
    candidate_classes = _matching_controller_classes(port)
    if not candidate_classes:
        return ConnectedSerialDevice(
            port=port.device,
            description=port.description,
            hwid=port.hwid,
            recognized_device="Unrecognized",
            recognition_state="unrecognized",
            manual_assignment=assignment,
        )

    errors: list[str] = []
    for controller_cls in candidate_classes:
        controller = controller_cls()
        started = perf_counter()
        try:
            controller.connect(port.device)
            probe = controller.get_probe()
            record_port_probe_event(
                phase="inventory",
                port=port.device,
                owner=f"inventory:{controller_cls.controller_type}",
                role="valve",
                assignment=assignment,
                action="probe",
                command=None,
                result="success",
                duration_ms=(perf_counter() - started) * 1000.0,
                message=probe.model,
            )
            details = "; ".join(
                part
                for part in (
                    f"serial {probe.serial_number}" if probe.serial_number else "",
                    f"protocol {probe.protocol_version}" if probe.protocol_version else "",
                )
                if part
            )
            label = _format_controller_label(probe.controller_type, probe.model)
            return ConnectedSerialDevice(
                port=port.device,
                description=port.description,
                hwid=port.hwid,
                recognized_device=label,
                recognition_state="confirmed",
                manual_assignment=assignment,
                details=details,
            )
        except Exception as exc:
            record_port_probe_event(
                phase="inventory",
                port=port.device,
                owner=f"inventory:{controller_cls.controller_type}",
                role="valve",
                assignment=assignment,
                action="probe",
                command=None,
                result="fail",
                duration_ms=(perf_counter() - started) * 1000.0,
                message=str(exc),
            )
            errors.append(f"{controller_cls.controller_type}: {exc}")
        finally:
            try:
                controller.close()
            except Exception:
                pass

    if errors:
        return ConnectedSerialDevice(
            port=port.device,
            description=port.description,
            hwid=port.hwid,
            recognized_device="Likely serial controller",
            recognition_state="probable",
            manual_assignment=assignment,
            details="; ".join(errors),
        )
    return ConnectedSerialDevice(
        port=port.device,
        description=port.description,
        hwid=port.hwid,
        recognized_device="Unrecognized",
        recognition_state="unrecognized",
        manual_assignment=assignment,
    )


def scan_connected_serial_devices(*, passive: bool = False) -> list[ConnectedSerialDevice]:
    ports = SerialController.list_ports()
    inventory: list[ConnectedSerialDevice] = []
    for port in ports:
        if passive:
            inventory.append(_passive_record(port))
            continue
        record = _probe_pump_device(port)
        if record.recognition_state == "unrecognized":
            record = _probe_controller_device(port)
        inventory.append(record)
    return inventory
