"""Unit tests for the device connection logic in ExperimentControlWindow.

Tests are written against the production methods using duck-typed fake objects so
no Qt application or real hardware is required.  Each test exercises exactly the
behaviour we care about — label resolution, profile registration, service
delegation, connect/disconnect handlers, and health-check logic — without
touching any Qt widget.

Pattern used throughout:
    ExperimentControlWindow.<method>(fake_self, ...)

where ``fake_self`` is a ``MagicMock`` configured with only the attributes the
method under test actually reads or writes.
"""
from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.device.communication_models import DeviceStatus
from lspr_app.device.device_types import PUMP, SELECTOR, SWITCH
from lspr_app.gui.experiment_control_window import ExperimentControlWindow


# ── helpers ───────────────────────────────────────────────────────────────────

def _fake_self(**kwargs) -> MagicMock:
    """Return a MagicMock wired with the real ``_device_label_for`` and any
    extra keyword attributes."""
    m = MagicMock()
    # Wire the real label resolver (it never reads self, so None is fine)
    m._device_label_for.side_effect = lambda key: ExperimentControlWindow._device_label_for(None, key)
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


def _device_status(label: str, *, port: str = "COM3", connected: bool = True, **identity) -> DeviceStatus:
    return DeviceStatus(
        uuid="test-uuid",
        label=label,
        type="switch",
        driver="auto",
        endpoint=port,
        connected=connected,
        state="connected" if connected else "disconnected",
        identity=dict(identity),
    )


# ── label resolution ──────────────────────────────────────────────────────────

class DeviceLabelTests(unittest.TestCase):
    """_device_label_for maps device-type keys to canonical service labels."""

    def _label(self, key: str) -> str:
        return ExperimentControlWindow._device_label_for(None, key)

    def test_pump_label(self) -> None:
        self.assertEqual(self._label(PUMP), "pump_1")

    def test_switch_label(self) -> None:
        self.assertEqual(self._label(SWITCH), "switch_1")

    def test_selector_label(self) -> None:
        self.assertEqual(self._label(SELECTOR), "selector_1")

    def test_unknown_key_falls_back_to_key_main(self) -> None:
        self.assertEqual(self._label("foo"), "foo_main")


# ── profile registration ──────────────────────────────────────────────────────

class EnsureDeviceProfileTests(unittest.TestCase):
    """_ensure_device_profile calls register_endpoint_assignment with the
    correct label, port, device_type, driver, and role for each device."""

    def _call(self, device_key: str, port: str, driver: str):
        fake = _fake_self()
        result = ExperimentControlWindow._ensure_device_profile(fake, device_key, port, driver=driver)
        return result, fake._device_comm_service.register_endpoint_assignment

    def test_pump_registers_correct_args(self) -> None:
        label, reg = self._call(PUMP, "COM3", "reglo_icc")
        self.assertEqual(label, "pump_1")
        reg.assert_called_once_with("pump_1", "COM3", device_type=PUMP, driver="reglo_icc", role="sample_pump")

    def test_switch_registers_correct_args(self) -> None:
        label, reg = self._call(SWITCH, "COM4", "auto")
        self.assertEqual(label, "switch_1")
        reg.assert_called_once_with("switch_1", "COM4", device_type=SWITCH, driver="auto", role="inlet_switch")

    def test_selector_registers_correct_args(self) -> None:
        label, reg = self._call(SELECTOR, "COM5", "amf-mswitch")
        self.assertEqual(label, "selector_1")
        reg.assert_called_once_with("selector_1", "COM5", device_type=SELECTOR, driver="amf-mswitch", role="main_selector")


# ── service delegation ────────────────────────────────────────────────────────

class ServiceDeviceConnectedTests(unittest.TestCase):
    """_service_device_connected delegates to service.is_connected with the
    correct label."""

    def _call(self, device_key: str, is_connected: bool) -> bool:
        fake = _fake_self()
        fake._device_comm_service.is_connected.return_value = is_connected
        return ExperimentControlWindow._service_device_connected(fake, device_key)

    def test_returns_true_when_service_says_connected(self) -> None:
        self.assertTrue(self._call(PUMP, True))

    def test_returns_false_when_service_says_not_connected(self) -> None:
        self.assertFalse(self._call(SWITCH, False))

    def test_uses_correct_label_for_selector(self) -> None:
        fake = _fake_self()
        fake._device_comm_service.is_connected.return_value = True
        ExperimentControlWindow._service_device_connected(fake, SELECTOR)
        fake._device_comm_service.is_connected.assert_called_once_with("selector_1")


# ── disconnect ────────────────────────────────────────────────────────────────

class DisconnectTests(unittest.TestCase):
    """Each disconnect method calls service.disconnect_device with the right
    label and clears internal state."""

    def test_disconnect_pump_calls_service(self) -> None:
        fake = _fake_self()
        fake._probe = MagicMock()
        ExperimentControlWindow._disconnect_pump(fake)
        fake._device_comm_service.disconnect_device.assert_called_once_with("pump_1")
        self.assertIsNone(fake._probe)

    def test_disconnect_switch_calls_service(self) -> None:
        fake = _fake_self()
        fake._valve_probe = MagicMock()
        ExperimentControlWindow._disconnect_valve_controller(fake)
        fake._device_comm_service.disconnect_device.assert_called_once_with("switch_1")
        self.assertIsNone(fake._valve_probe)

    def test_disconnect_selector_calls_service(self) -> None:
        fake = _fake_self()
        fake._mswitch_probe = MagicMock()
        fake._mswitch_connect_in_progress = True
        fake._mswitch_connect_task = MagicMock()
        ExperimentControlWindow._disconnect_mswitch_controller(fake)
        fake._device_comm_service.disconnect_device.assert_called_once_with("selector_1")
        self.assertIsNone(fake._mswitch_probe)
        self.assertFalse(fake._mswitch_connect_in_progress)
        self.assertIsNone(fake._mswitch_connect_task)


# ── valve connect handler ─────────────────────────────────────────────────────

class ValveConnectHandlerTests(unittest.TestCase):
    """_handle_valve_connect_finished handles success and failure payloads."""

    def _fake(self) -> MagicMock:
        fake = _fake_self()
        fake._valve_connect_in_progress = True
        fake._valve_connect_task = MagicMock()
        fake._valve_probe = None
        return fake

    def test_success_builds_probe_and_clears_task(self) -> None:
        fake = self._fake()
        status = _device_status(
            "switch_1", port="COM4",
            controller_type="itsy", model="ItsyBitsy Switch", serial_number="SN42",
        )
        payload = ("COM4", status, None)

        ExperimentControlWindow._handle_valve_connect_finished(fake, payload)

        self.assertFalse(fake._valve_connect_in_progress)
        self.assertIsNone(fake._valve_connect_task)
        self.assertIsNotNone(fake._valve_probe)
        self.assertEqual(fake._valve_probe.port, "COM4")
        self.assertEqual(fake._valve_probe.model, "ItsyBitsy Switch")
        fake.valve_availability_changed.emit.assert_called_once_with(fake._valve_probe)

    def test_failure_disconnects_and_emits_none(self) -> None:
        fake = self._fake()
        payload = ("COM4", None, "timeout")

        ExperimentControlWindow._handle_valve_connect_finished(fake, payload)

        self.assertFalse(fake._valve_connect_in_progress)
        self.assertIsNone(fake._valve_connect_task)
        self.assertIsNone(fake._valve_probe)
        fake._device_comm_service.disconnect_device.assert_called_once_with("switch_1")
        fake.valve_availability_changed.emit.assert_called_once_with(None)

    def test_unexpected_payload_emits_none(self) -> None:
        fake = self._fake()
        ExperimentControlWindow._handle_valve_connect_finished(fake, "bad")
        fake.valve_availability_changed.emit.assert_called_once_with(None)


# ── selector connect handler ──────────────────────────────────────────────────

class SelectorConnectHandlerTests(unittest.TestCase):
    """_handle_mswitch_connect_finished handles success and failure payloads."""

    def _fake(self) -> MagicMock:
        fake = _fake_self()
        fake._mswitch_connect_in_progress = True
        fake._mswitch_connect_task = MagicMock()
        fake._mswitch_probe = None
        return fake

    def test_success_builds_probe_and_clears_task(self) -> None:
        fake = self._fake()
        status = _device_status(
            "selector_1", port="COM5",
            controller_type="amf-mswitch", model="AMF M-Switch",
        )
        payload = ("COM5", status, None)

        ExperimentControlWindow._handle_mswitch_connect_finished(fake, payload)

        self.assertFalse(fake._mswitch_connect_in_progress)
        self.assertIsNone(fake._mswitch_connect_task)
        self.assertIsNotNone(fake._mswitch_probe)
        self.assertEqual(fake._mswitch_probe.port, "COM5")
        self.assertEqual(fake._mswitch_probe.model, "AMF M-Switch")
        fake.mswitch_availability_changed.emit.assert_called_once_with(fake._mswitch_probe)

    def test_failure_disconnects_and_emits_none(self) -> None:
        fake = self._fake()
        payload = ("COM5", None, "device not found")

        ExperimentControlWindow._handle_mswitch_connect_finished(fake, payload)

        self.assertFalse(fake._mswitch_connect_in_progress)
        self.assertIsNone(fake._mswitch_connect_task)
        self.assertIsNone(fake._mswitch_probe)
        fake._device_comm_service.disconnect_device.assert_called_once_with("selector_1")
        fake.mswitch_availability_changed.emit.assert_called_once_with(None)

    def test_unexpected_payload_emits_none(self) -> None:
        fake = self._fake()
        ExperimentControlWindow._handle_mswitch_connect_finished(fake, 42)
        fake.mswitch_availability_changed.emit.assert_called_once_with(None)


# ── health check / synchronize ────────────────────────────────────────────────

class SynchronizeDeviceConnectionsTests(unittest.TestCase):
    """synchronize_device_connections disconnects a device when its port
    disappears, and leaves it alone when the port is still present."""

    _AVAILABLE = [SimpleNamespace(device="COM3"), SimpleNamespace(device="COM4")]

    def _fake_with_probes(self, pump_port="COM3", switch_port="COM4", selector_port="COM5"):
        fake = _fake_self()
        fake._connection_sync_in_progress = False
        fake._connect_in_progress = False
        fake._valve_connect_in_progress = False
        fake._mswitch_connect_in_progress = False
        fake._probe = SimpleNamespace(port=pump_port) if pump_port else None
        fake._valve_probe = SimpleNamespace(port=switch_port) if switch_port else None
        fake._mswitch_probe = SimpleNamespace(port=selector_port) if selector_port else None
        return fake

    def _run(self, fake, connected_keys=(), available_ports=None):
        if available_ports is None:
            available_ports = self._AVAILABLE
        connected_set = set(connected_keys)
        fake._service_device_connected.side_effect = lambda key: key in connected_set

        with patch(
            "lspr_app.gui.experiment_control_window.SerialController.list_ports",
            return_value=available_ports,
        ):
            ExperimentControlWindow.synchronize_device_connections(fake)

    def test_disconnects_pump_when_port_gone(self) -> None:
        fake = self._fake_with_probes(pump_port="COM9")  # COM9 not in available list
        self._run(fake, connected_keys=(PUMP,))
        fake._disconnect_pump.assert_called_once()

    def test_does_not_disconnect_pump_when_port_present(self) -> None:
        fake = self._fake_with_probes(pump_port="COM3")  # COM3 is available
        self._run(fake, connected_keys=(PUMP,))
        fake._disconnect_pump.assert_not_called()

    def test_disconnects_switch_when_port_gone(self) -> None:
        fake = self._fake_with_probes(switch_port="COM9")
        self._run(fake, connected_keys=(SWITCH,))
        fake._disconnect_valve_controller.assert_called_once()

    def test_does_not_disconnect_switch_when_port_present(self) -> None:
        fake = self._fake_with_probes(switch_port="COM4")
        self._run(fake, connected_keys=(SWITCH,))
        fake._disconnect_valve_controller.assert_not_called()

    def test_disconnects_selector_when_port_gone(self) -> None:
        fake = self._fake_with_probes(selector_port="COM9")
        self._run(fake, connected_keys=(SELECTOR,))
        fake._disconnect_mswitch_controller.assert_called_once()

    def test_does_not_disconnect_selector_when_port_present(self) -> None:
        fake = self._fake_with_probes(selector_port="COM3")
        self._run(fake, connected_keys=(SELECTOR,))
        fake._disconnect_mswitch_controller.assert_not_called()

    def test_skips_sync_when_already_in_progress(self) -> None:
        fake = self._fake_with_probes()
        fake._connection_sync_in_progress = True
        self._run(fake)
        fake._disconnect_pump.assert_not_called()

    def test_skips_sync_when_connect_in_progress(self) -> None:
        fake = self._fake_with_probes()
        fake._connect_in_progress = True
        self._run(fake)
        fake._disconnect_pump.assert_not_called()


if __name__ == "__main__":
    unittest.main()
