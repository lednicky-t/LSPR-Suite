"""Hardware probe script for sLSPR device connections.

Run this with devices physically connected to verify that each device type
connects and disconnects correctly through the unified DeviceCommunicationService.

Usage:
    python tools/probe_devices.py                 # auto-scan all ports for all devices
    python tools/probe_devices.py --pump COM3     # test pump on a specific port
    python tools/probe_devices.py --switch COM4   # test switch on a specific port
    python tools/probe_devices.py --selector COM5 # test selector on a specific port

Exit codes:
    0 — all tested devices connected successfully
    1 — one or more devices failed to connect
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Make sure the app package is importable
REPO_ROOT = Path(__file__).resolve().parents[1]
APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
for p in (
    str(REPO_ROOT / "packages" / "lspr_core" / "src"),
    str(REPO_ROOT / "packages" / "lspr_ui" / "src"),
    str(REPO_ROOT / "packages" / "lspr_io" / "src"),
    str(APP_SRC),
):
    if p not in sys.path:
        sys.path.insert(0, p)

from lspr_app.device.device_manager import DeviceCommunicationService
from lspr_app.device.device_types import PUMP, SELECTOR, SWITCH

try:
    import serial.tools.list_ports as _list_ports
    def list_ports() -> list[str]:
        return [str(p.device) for p in _list_ports.comports()]
except ImportError:
    def list_ports() -> list[str]:
        return []


# ── formatting ────────────────────────────────────────────────────────────────

GREEN = "\033[92m"
RED   = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

def ok(msg: str) -> None:   print(f"  {GREEN}✓{RESET}  {msg}")
def fail(msg: str) -> None: print(f"  {RED}✗{RESET}  {msg}")
def info(msg: str) -> None: print(f"  {YELLOW}·{RESET}  {msg}")


# ── per-device probes ─────────────────────────────────────────────────────────

def probe_pump(service: DeviceCommunicationService, port: str) -> bool:
    print(f"\n[PUMP] Probing {port} ...")
    try:
        probe = service.probe_pump_port(port)
    except Exception as exc:
        fail(f"probe_pump_port raised: {exc}")
        return False
    if probe is None:
        fail("probe returned None — no pump detected on this port")
        return False
    ok(f"Detected: model={probe.model!r}  channels={probe.channel_count}  port={probe.port}")

    info("Registering profile and connecting ...")
    service.register_endpoint_assignment("pump_1", port, device_type=PUMP, driver="reglo_icc", role="sample_pump")
    try:
        t0 = time.monotonic()
        status = service.connect("pump_1")
        elapsed = (time.monotonic() - t0) * 1000
    except Exception as exc:
        fail(f"service.connect raised: {exc}")
        return False
    if not status.connected:
        fail(f"status.connected=False after connect  state={status.state!r}  error={status.last_error!r}")
        return False
    ok(f"Connected in {elapsed:.0f} ms  state={status.state!r}")

    info("Disconnecting ...")
    service.disconnect_device("pump_1")
    status_after = service.status("pump_1") if hasattr(service, "status") else None
    if status_after is not None and status_after.connected:
        fail("Still shows connected after disconnect_device")
        return False
    ok("Disconnected cleanly")
    return True


def probe_switch(service: DeviceCommunicationService, port: str) -> bool:
    print(f"\n[SWITCH] Probing {port} ...")
    service.register_endpoint_assignment("switch_1", port, device_type=SWITCH, driver="auto", role="inlet_switch")
    try:
        t0 = time.monotonic()
        status = service.connect("switch_1")
        elapsed = (time.monotonic() - t0) * 1000
    except Exception as exc:
        fail(f"service.connect raised: {exc}")
        return False
    if not status.connected:
        fail(f"status.connected=False  state={status.state!r}  error={status.last_error!r}")
        return False
    id_info = "  ".join(f"{k}={v!r}" for k, v in status.identity.items() if v)
    ok(f"Connected in {elapsed:.0f} ms  endpoint={status.endpoint}  {id_info}")

    info("Sending switch.set_position:left ...")
    from lspr_app.device.communication_models import DeviceCommand
    result = service.send_command("switch_1", DeviceCommand("switch.set_position", {"position": "left"}))
    if result.success:
        ok("Position command accepted")
    else:
        info(f"Position command returned error (may be unsupported): {result.error}")

    info("Disconnecting ...")
    service.disconnect_device("switch_1")
    ok("Disconnected cleanly")
    return True


def probe_selector(service: DeviceCommunicationService, port: str) -> bool:
    print(f"\n[SELECTOR] Probing {port} ...")
    try:
        import lspr_app.device.amf_mswitch  # noqa: F401
    except ImportError:
        fail("AMFTools not installed — selector cannot be tested (pip install AMFTools)")
        return False

    service.register_endpoint_assignment("selector_1", port, device_type=SELECTOR, driver="amf-mswitch", role="main_selector")
    try:
        t0 = time.monotonic()
        status = service.connect("selector_1")
        elapsed = (time.monotonic() - t0) * 1000
    except Exception as exc:
        fail(f"service.connect raised: {exc}")
        return False
    if not status.connected:
        fail(f"status.connected=False  state={status.state!r}  error={status.last_error!r}")
        return False
    id_info = "  ".join(f"{k}={v!r}" for k, v in status.identity.items() if v)
    ok(f"Connected in {elapsed:.0f} ms  endpoint={status.endpoint}  {id_info}")

    info("Disconnecting ...")
    service.disconnect_device("selector_1")
    ok("Disconnected cleanly")
    return True


# ── auto-scan ─────────────────────────────────────────────────────────────────

def auto_scan(service: DeviceCommunicationService) -> bool:
    ports = list_ports()
    if not ports:
        fail("No serial ports found — is anything plugged in?")
        return False
    print(f"\nAvailable ports: {', '.join(ports)}")

    all_ok = True
    pump_found = False
    for port in ports:
        info(f"Trying pump on {port} ...")
        try:
            probe = service.probe_pump_port(port)
        except Exception:
            probe = None
        if probe is not None and probe.model not in ("err", "ERR", ""):
            ok(f"Pump detected on {port}: {probe.model}")
            pump_found = True
            if not probe_pump(service, port):
                all_ok = False
            break
    if not pump_found:
        info("No pump detected on any port")

    print()
    for port in ports:
        info(f"Trying switch on {port} ...")
        service.register_endpoint_assignment("switch_1", port, device_type=SWITCH, driver="auto", role="inlet_switch")
        try:
            status = service.connect("switch_1")
            if status.connected:
                ok(f"Switch detected on {port}")
                service.disconnect_device("switch_1")
                if not probe_switch(service, port):
                    all_ok = False
                break
            service.disconnect_device("switch_1")
        except Exception:
            service.disconnect_device("switch_1")
    else:
        info("No switch detected on any port")

    return all_ok


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Probe sLSPR devices via DeviceCommunicationService")
    parser.add_argument("--pump",     metavar="PORT", help="Test pump on this port")
    parser.add_argument("--switch",   metavar="PORT", help="Test switch on this port")
    parser.add_argument("--selector", metavar="PORT", help="Test selector on this port")
    args = parser.parse_args()

    service = DeviceCommunicationService.shared()
    results: list[bool] = []

    if args.pump:
        results.append(probe_pump(service, args.pump))
    if args.switch:
        results.append(probe_switch(service, args.switch))
    if args.selector:
        results.append(probe_selector(service, args.selector))
    if not any([args.pump, args.switch, args.selector]):
        results.append(auto_scan(service))

    print()
    if all(results):
        ok("All device probes passed.")
        return 0
    else:
        fail(f"{results.count(False)} of {len(results)} probe(s) failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
