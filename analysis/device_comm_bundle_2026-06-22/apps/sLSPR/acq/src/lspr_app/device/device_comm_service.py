from __future__ import annotations

from dataclasses import dataclass

from lspr_app.device.amf_mswitch import AMFSwitchController, amf_tools_available, detect_amf_mswitch_devices
from lspr_app.device.reglo_icc import RegloICCClient, PumpProbe
from lspr_app.device.serial_controllers import ControllerProbe, SerialController
from lspr_app.device.valve_controllers import detect_valve_controller


@dataclass(slots=True)
class PortRefreshData:
    generation: int
    pump_ports: list[object]
    valve_ports: list[object]
    mswitch_devices: list[object]
    amf_tools_available: bool


def refresh_device_ports(generation: int) -> PortRefreshData:
    pump_ports = RegloICCClient.list_ports()
    valve_ports = SerialController.list_ports()
    available = amf_tools_available()
    if available:
        mswitch_devices = detect_amf_mswitch_devices()
    else:
        mswitch_devices = []
    return PortRefreshData(
        generation=generation,
        pump_ports=list(pump_ports),
        valve_ports=list(valve_ports),
        mswitch_devices=list(mswitch_devices),
        amf_tools_available=available,
    )


def probe_pump_port(port: str) -> PumpProbe:
    return RegloICCClient.probe_port(port)


def connect_valve_port(port: str) -> tuple[SerialController, ControllerProbe]:
    return detect_valve_controller(port)


def connect_mswitch_port(port: str) -> tuple[AMFSwitchController, ControllerProbe]:
    client = AMFSwitchController()
    client.connect(port)
    return client, client.get_probe()
