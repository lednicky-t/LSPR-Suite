"""Unit tests for lspr_app.device.device_lifecycle.

No QApplication and no real hardware required: DeviceLifecycleController is
pure Python, and the DeviceCommunicationService it talks to is replaced by a
small fake that records calls and returns configurable canned responses.
RegloICCClient.probe_port (a classmethod called directly, not through the
service) is patched where it's imported into device_lifecycle's namespace.
"""
from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.device.communication_models import DeviceCommandResult, DeviceStatus, PortRefreshData, ProbeResult
from lspr_app.device.device_types import PUMP, SELECTOR, SWITCH
from lspr_app.device.reglo_icc import PumpProbe
from lspr_app.device.serial_controllers import ControllerPort, ControllerProbe
from lspr_app.device import device_lifecycle as dl


# ── fakes ─────────────────────────────────────────────────────────────────────

@dataclass
class _FakeProfile:
    label: str
    endpoint: str = ""


class FakeDeviceCommunicationService:
    """Minimal stand-in for DeviceCommunicationService, recording calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.connected: dict[str, bool] = {}
        self.connect_should_fail: dict[str, str] = {}
        self.probe_endpoint_result: ProbeResult | None = None
        self.command_results: dict[str, DeviceCommandResult] = {}
        self.default_command_success = True
        self.refresh_calls = 0
        self.port_refresh_data = PortRefreshData(
            generation=0, pump_ports=[], valve_ports=[], selector_devices=[], amf_tools_available=True,
        )

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    def find_or_create_profile(self, *, device_type, fingerprint, endpoint, identity, driver="auto", display_name=None, role=None):
        self._record("find_or_create_profile", device_type=device_type, fingerprint=fingerprint, endpoint=endpoint)
        return _FakeProfile(label=f"{device_type}_1", endpoint=endpoint)

    def register_endpoint_assignment(self, label, endpoint, device_type="auto", driver="auto", role=None, *, mark_manual=True):
        self._record("register_endpoint_assignment", label, endpoint, device_type=device_type, driver=driver, role=role, mark_manual=mark_manual)
        return _FakeProfile(label=label, endpoint=endpoint)

    def update_profile_identity(self, label, *, fingerprint=None, identity=None):
        self._record("update_profile_identity", label, fingerprint=fingerprint, identity=identity)
        return _FakeProfile(label=label)

    def connect(self, label, *, cached_pump_probe=None):
        self._record("connect", label, cached_pump_probe=cached_pump_probe)
        if label in self.connect_should_fail:
            raise RuntimeError(self.connect_should_fail[label])
        self.connected[label] = True
        return DeviceStatus(
            uuid="u", label=label, type="", driver="", endpoint="COM9", connected=True, state="connected",
            identity={"model": "Fake", "serial_number": "1", "protocol_version": "1", "channel_count": "4"},
        )

    def disconnect(self, label):
        self._record("disconnect", label)
        self.connected[label] = False
        return DeviceStatus(uuid="u", label=label, type="", driver="", endpoint=None, connected=False, state="disconnected")

    def disconnect_device(self, label):
        self.disconnect(label)

    def is_connected(self, label):
        return bool(self.connected.get(label, False))

    def connection(self, label):
        return object() if self.connected.get(label) else None

    def probe_endpoint(self, endpoint, expected_type=None):
        self._record("probe_endpoint", endpoint, expected_type)
        if self.probe_endpoint_result is not None:
            return self.probe_endpoint_result
        return ProbeResult(endpoint=endpoint, detected_type="valve", driver="legacy-valve", identity={"model": "Fake valve"}, success=True, error=None, duration_ms=1.0)

    def send_command(self, label, command):
        self._record("send_command", label, command.command_type)
        if label in self.command_results:
            return self.command_results[label]
        return DeviceCommandResult(label=label, command_type=command.command_type, success=self.default_command_success, response=None, error=None, duration_ms=1.0)

    def refresh_device_ports(self, generation):
        self.refresh_calls += 1
        return self.port_refresh_data


def _controller(service: FakeDeviceCommunicationService) -> dl.DeviceLifecycleController:
    return dl.DeviceLifecycleController(service)


# ── port ranking ──────────────────────────────────────────────────────────────

class PumpPortRankingTests(unittest.TestCase):
    def test_manual_assignment_wins_over_likely(self) -> None:
        manual = ControllerPort(device="COM4", description="manual", hwid="")
        likely = ControllerPort(device="COM5", description="Reglo", hwid="VID_265C&PID_0001")
        with patch.object(dl, "get_port_assignment", side_effect=lambda p: "pump" if p == "COM4" else "auto"), \
             patch.object(dl, "is_probable_reglo_port", return_value=True), \
             patch.object(dl, "should_probe_port_for_role", return_value=True):
            self.assertIs(dl.best_pump_port([likely, manual]), manual)

    def test_likely_wins_when_nothing_manual(self) -> None:
        likely = ControllerPort(device="COM5", description="Reglo", hwid="")
        unlikely = ControllerPort(device="COM6", description="Other", hwid="")
        with patch.object(dl, "get_port_assignment", return_value="auto"), \
             patch.object(dl, "is_probable_reglo_port", side_effect=lambda p: p.device == "COM5"), \
             patch.object(dl, "should_probe_port_for_role", return_value=True):
            self.assertIs(dl.best_pump_port([unlikely, likely]), likely)

    def test_empty_when_inconclusive(self) -> None:
        port = ControllerPort(device="COM7", description="Other", hwid="")
        with patch.object(dl, "get_port_assignment", return_value="auto"), \
             patch.object(dl, "is_probable_reglo_port", return_value=False), \
             patch.object(dl, "should_probe_port_for_role", return_value=True):
            self.assertIsNone(dl.best_pump_port([port]))

    def test_no_ports_returns_none(self) -> None:
        self.assertIsNone(dl.best_pump_port([]))


class ValvePortRankingTests(unittest.TestCase):
    def test_manual_assignment_wins_over_ranked(self) -> None:
        manual = ControllerPort(device="COM4", description="manual", hwid="")
        ranked = ControllerPort(device="COM11", description="ItsyBitsy", hwid="")
        with patch.object(dl, "get_port_assignment", side_effect=lambda p: "switch" if p == "COM4" else "auto"), \
             patch.object(dl, "controller_port_priority", return_value=30), \
             patch.object(dl, "should_probe_port_for_role", return_value=True):
            self.assertIs(dl.best_valve_port([ranked, manual]), manual)

    def test_highest_priority_wins_when_nothing_manual(self) -> None:
        low = ControllerPort(device="COM4", description="Arduino", hwid="")
        high = ControllerPort(device="COM11", description="ItsyBitsy", hwid="")
        with patch.object(dl, "get_port_assignment", return_value="auto"), \
             patch.object(dl, "controller_port_priority", side_effect=lambda p: 30 if p.device == "COM11" else 20), \
             patch.object(dl, "should_probe_port_for_role", return_value=True):
            self.assertIs(dl.best_valve_port([low, high]), high)

    def test_empty_when_no_ports(self) -> None:
        self.assertIsNone(dl.best_valve_port([]))


class SelectorPortRankingTests(unittest.TestCase):
    def test_returns_first_discovered_device(self) -> None:
        first = ControllerProbe(port="COM9", controller_type="amf-mswitch", model="RVMFS")
        second = ControllerProbe(port="COM10", controller_type="amf-mswitch", model="RVMFS")
        self.assertIs(dl.best_selector_port([first, second]), first)

    def test_empty_when_none_discovered(self) -> None:
        self.assertIsNone(dl.best_selector_port([]))


# ── post-connect hooks ────────────────────────────────────────────────────────

class PostConnectHookTests(unittest.TestCase):
    def test_only_selector_has_a_registered_hook(self) -> None:
        self.assertIsNone(dl._POST_CONNECT_HOOKS.get(PUMP))
        self.assertIsNone(dl._POST_CONNECT_HOOKS.get(SWITCH))
        self.assertIsNotNone(dl._POST_CONNECT_HOOKS.get(SELECTOR))

    def test_connect_and_setup_runs_hook_only_for_selector(self) -> None:
        service = FakeDeviceCommunicationService()
        ctrl = _controller(service)
        events: list[dl.DeviceLifecycleEvent] = []
        ctrl._connect_and_setup(PUMP, "pump_1", "COM3", events.append)
        self.assertNotIn("send_command", [c[0] for c in service.calls])
        self.assertEqual(events[-1].stage, dl.STAGE_READY)

    def test_selector_hook_runs_and_home_command_is_sent(self) -> None:
        service = FakeDeviceCommunicationService()
        ctrl = _controller(service)
        events: list[dl.DeviceLifecycleEvent] = []
        ctrl._connect_and_setup(SELECTOR, "selector_1", "COM9", events.append)
        # send_command called positionally: (label, command) -> args tuple has command_type at index 1
        sent_types = [c[1][1] for c in service.calls if c[0] == "send_command"]
        self.assertIn("switch.home", sent_types)
        self.assertEqual(events[-1].stage, dl.STAGE_READY)
        self.assertTrue(events[-1].connected)

    def test_hook_failure_yields_failed_stage_but_stays_connected(self) -> None:
        service = FakeDeviceCommunicationService()
        service.default_command_success = False
        ctrl = _controller(service)
        events: list[dl.DeviceLifecycleEvent] = []
        ctrl._connect_and_setup(SELECTOR, "selector_1", "COM9", events.append)
        last = events[-1]
        self.assertEqual(last.stage, dl.STAGE_FAILED)
        self.assertTrue(last.connected)  # connected=True: the connect succeeded, only homing failed
        self.assertIsNotNone(last.error)

    def test_plain_connect_failure_yields_not_connected(self) -> None:
        service = FakeDeviceCommunicationService()
        service.connect_should_fail["pump_1"] = "Port busy."
        ctrl = _controller(service)
        events: list[dl.DeviceLifecycleEvent] = []
        ctrl._connect_and_setup(PUMP, "pump_1", "COM3", events.append)
        last = events[-1]
        self.assertEqual(last.stage, dl.STAGE_FAILED)
        self.assertFalse(last.connected)


# ── run_full_cycle sequencing ─────────────────────────────────────────────────

class RunFullCycleTests(unittest.TestCase):
    def test_sequencing_order_and_single_port_refresh(self) -> None:
        service = FakeDeviceCommunicationService()
        service.port_refresh_data = PortRefreshData(
            generation=0,
            pump_ports=[ControllerPort(device="COM3", description="Reglo", hwid="VID_265C&PID_0001")],
            valve_ports=[ControllerPort(device="COM4", description="ItsyBitsy", hwid="VID_239A")],
            selector_devices=[ControllerProbe(port="COM9", controller_type="amf-mswitch", model="RVMFS")],
            amf_tools_available=True,
        )
        service.probe_endpoint_result = ProbeResult(endpoint="COM4", detected_type="itsybitsy-32u4-valve", driver="itsybitsy-32u4-valve", identity={"model": "ItsyBitsy"}, success=True, error=None, duration_ms=1.0)
        ctrl = _controller(service)
        events: list[dl.DeviceLifecycleEvent] = []

        fake_pump_probe = PumpProbe(port="COM3", protocol_version="1", serial_number="SN1", channel_count=4, model="Reglo ICC")
        with patch.object(dl.RegloICCClient, "probe_port", return_value=fake_pump_probe), \
             patch.object(dl, "is_probable_reglo_port", return_value=True), \
             patch.object(dl, "should_probe_port_for_role", return_value=True), \
             patch.object(dl, "get_port_assignment", return_value="auto"), \
             patch.object(dl, "controller_port_priority", return_value=30), \
             patch("lspr_app.device.ocean.OceanSpectrometer", side_effect=RuntimeError("no hardware")):
            report = ctrl.run_full_cycle(events.append)

        self.assertEqual(service.refresh_calls, 1)
        device_key_order = [e.device_key for e in events if e.terminal]
        self.assertEqual(device_key_order, ["spectrometer", PUMP, SWITCH, SELECTOR])
        self.assertEqual(report.by_device[PUMP].stage, dl.STAGE_READY)
        self.assertEqual(report.by_device[SWITCH].stage, dl.STAGE_READY)
        self.assertEqual(report.by_device[SELECTOR].stage, dl.STAGE_READY)
        self.assertIsNone(report.spectrometer)  # simulation fallback

    def test_missing_device_reports_missing_stage(self) -> None:
        service = FakeDeviceCommunicationService()
        ctrl = _controller(service)
        events: list[dl.DeviceLifecycleEvent] = []
        with patch("lspr_app.device.ocean.OceanSpectrometer", side_effect=RuntimeError("no hardware")):
            report = ctrl.run_full_cycle(events.append)
        self.assertEqual(report.by_device[PUMP].stage, dl.STAGE_MISSING)
        self.assertEqual(report.by_device[SWITCH].stage, dl.STAGE_MISSING)
        self.assertEqual(report.by_device[SELECTOR].stage, dl.STAGE_MISSING)


# ── canonical label resolution (2026-07-22 regression) ──────────────────────────
#
# Bug: _discover_and_connect_* used to resolve which profile to connect via
# find_or_create_profile(), which searches *all* profiles by fingerprint/
# endpoint. If a stale duplicate profile elsewhere already held a matching
# fingerprint (e.g. a leftover "selector_2" from an earlier session), the real
# device connected under that other label instead of the fixed canonical one
# (selector_1) - every other connectivity check in the app (device_label_for())
# assumes the canonical label is authoritative, so the device looked connected
# in the status strip but every experiment-plan command against it was silently
# skipped as "controller not connected". Fixed by always resolving through
# ensure_device_profile() (the same mechanism the manual "Connect" button
# already used) and recording fingerprint/identity via the new, non-searching
# update_profile_identity() instead. These tests prove discovery never calls
# find_or_create_profile() and always targets the fixed canonical label.

class CanonicalLabelResolutionTests(unittest.TestCase):
    def test_pump_discovery_uses_canonical_label_not_find_or_create_profile(self) -> None:
        service = FakeDeviceCommunicationService()
        ctrl = _controller(service)
        fake_probe = PumpProbe(port="COM3", protocol_version="1", serial_number="SN1", channel_count=4, model="Reglo ICC")
        ports = [ControllerPort(device="COM3", description="Reglo", hwid="VID_265C&PID_0001")]

        with patch.object(dl.RegloICCClient, "probe_port", return_value=fake_probe):
            event = ctrl._discover_and_connect_pump(ports, lambda _e: None)

        self.assertEqual(event.stage, dl.STAGE_READY)
        call_names = [c[0] for c in service.calls]
        self.assertNotIn("find_or_create_profile", call_names)
        self.assertIn("register_endpoint_assignment", call_names)
        self.assertIn("update_profile_identity", call_names)
        register_call = next(c for c in service.calls if c[0] == "register_endpoint_assignment")
        self.assertEqual(register_call[1][0], "pump_1")
        identity_call = next(c for c in service.calls if c[0] == "update_profile_identity")
        self.assertEqual(identity_call[1][0], "pump_1")
        self.assertEqual(identity_call[2]["fingerprint"], "reglo-icc:SN1")
        connect_call = next(c for c in service.calls if c[0] == "connect")
        self.assertEqual(connect_call[1][0], "pump_1")

    def test_valve_discovery_uses_canonical_label_not_find_or_create_profile(self) -> None:
        service = FakeDeviceCommunicationService()
        service.probe_endpoint_result = ProbeResult(
            endpoint="COM4", detected_type="itsybitsy-32u4-valve", driver="itsybitsy-32u4-valve",
            identity={"model": "ItsyBitsy"}, success=True, error=None, duration_ms=1.0,
        )
        ctrl = _controller(service)
        ports = [ControllerPort(device="COM4", description="ItsyBitsy", hwid="VID_239A")]

        with patch.object(dl, "controller_port_priority", return_value=30):
            event = ctrl._discover_and_connect_valve(ports, lambda _e: None)

        self.assertEqual(event.stage, dl.STAGE_READY)
        call_names = [c[0] for c in service.calls]
        self.assertNotIn("find_or_create_profile", call_names)
        register_call = next(c for c in service.calls if c[0] == "register_endpoint_assignment")
        self.assertEqual(register_call[1][0], "switch_1")
        identity_call = next(c for c in service.calls if c[0] == "update_profile_identity")
        self.assertEqual(identity_call[1][0], "switch_1")
        connect_call = next(c for c in service.calls if c[0] == "connect")
        self.assertEqual(connect_call[1][0], "switch_1")

    def test_selector_discovery_uses_canonical_label_not_find_or_create_profile(self) -> None:
        # This is the exact scenario reported: a stale "selector_2" profile
        # elsewhere already holds this fingerprint. find_or_create_profile()
        # would have returned that label; the fix must never call it at all.
        service = FakeDeviceCommunicationService()
        ctrl = _controller(service)
        device = ControllerProbe(port="COM9", controller_type="amf-mswitch", model="RVMFS", serial_number="SN-EXISTS-ON-SELECTOR-2")

        event = ctrl._discover_and_connect_selector([device], lambda _e: None)

        self.assertEqual(event.stage, dl.STAGE_READY)
        call_names = [c[0] for c in service.calls]
        self.assertNotIn("find_or_create_profile", call_names)
        register_call = next(c for c in service.calls if c[0] == "register_endpoint_assignment")
        self.assertEqual(register_call[1][0], "selector_1")
        identity_call = next(c for c in service.calls if c[0] == "update_profile_identity")
        self.assertEqual(identity_call[1][0], "selector_1")
        self.assertEqual(identity_call[2]["fingerprint"], "amf-selector:SN-EXISTS-ON-SELECTOR-2")
        connect_call = next(c for c in service.calls if c[0] == "connect")
        self.assertEqual(connect_call[1][0], "selector_1")


# ── single-flight ─────────────────────────────────────────────────────────────

class SingleFlightTests(unittest.TestCase):
    def test_request_connect_no_ops_when_busy(self) -> None:
        service = FakeDeviceCommunicationService()
        ctrl = _controller(service)
        ctrl._busy.add(PUMP)
        result = ctrl.request_connect(PUMP, "COM3")
        self.assertFalse(result)
        self.assertNotIn("connect", [c[0] for c in service.calls])

    def test_request_connect_clears_busy_after_completion(self) -> None:
        service = FakeDeviceCommunicationService()
        ctrl = _controller(service)
        ctrl.request_connect(PUMP, "COM3")
        self.assertFalse(ctrl.is_busy(PUMP))

    def test_request_disconnect_no_ops_when_busy(self) -> None:
        service = FakeDeviceCommunicationService()
        ctrl = _controller(service)
        ctrl._busy.add(PUMP)
        ctrl.request_disconnect(PUMP)
        self.assertNotIn("disconnect", [c[0] for c in service.calls])


# ── shutdown ordering ─────────────────────────────────────────────────────────

class ShutdownAllTests(unittest.TestCase):
    def test_stop_all_called_before_any_disconnect(self) -> None:
        service = FakeDeviceCommunicationService()
        service.connected = {"pump_1": True, "switch_1": True, "selector_1": True}
        ctrl = _controller(service)
        ctrl.shutdown_all()
        names = [c[0] for c in service.calls]
        stop_all_index = next(i for i, n in enumerate(names) if n == "send_command")
        first_disconnect_index = next(i for i, n in enumerate(names) if n == "disconnect")
        self.assertLess(stop_all_index, first_disconnect_index)

    def test_selector_disconnect_sends_no_home_or_move_command(self) -> None:
        service = FakeDeviceCommunicationService()
        service.connected = {"selector_1": True}
        ctrl = _controller(service)
        ctrl.shutdown_all()
        selector_commands = [c[2] for c in service.calls if c[0] == "send_command"]
        self.assertEqual(selector_commands, [])  # only the pump's stop_all could ever appear, and pump isn't connected here

    def test_disconnect_order_is_switch_then_selector_then_pump(self) -> None:
        service = FakeDeviceCommunicationService()
        service.connected = {"pump_1": True, "switch_1": True, "selector_1": True}
        ctrl = _controller(service)
        ctrl.shutdown_all()
        disconnect_labels = [c[1][0] for c in service.calls if c[0] == "disconnect"]
        self.assertEqual(disconnect_labels, ["switch_1", "selector_1", "pump_1"])

    def test_nothing_connected_is_a_no_op(self) -> None:
        service = FakeDeviceCommunicationService()
        ctrl = _controller(service)
        ctrl.shutdown_all()
        self.assertEqual(service.calls, [])


if __name__ == "__main__":
    unittest.main()
