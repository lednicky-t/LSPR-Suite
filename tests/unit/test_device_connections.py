"""Unit tests for the device-label/connection-query helpers still owned by
ExperimentControlWindow.

Tests are written against the production methods using duck-typed fake objects so
no Qt application or real hardware is required.

Pattern used throughout:
    ExperimentControlWindow.<method>(fake_self, ...)

where ``fake_self`` is a ``MagicMock`` configured with only the attributes the
method under test actually reads or writes.

Note: the manual connect/disconnect dispatch this file used to cover
(_connect_device, _disconnect_device, _handle_device_connect_finished,
_ensure_device_profile, per-device _disconnect_* wrappers, etc.) was deleted
from experiment_control_window.py on 2026-07-21 - it was only reachable from
widgets that were never placed in any layout (see
docs/device-layer/DEVICE_LAYER_AUDIT_2026.md, "orphaned connect widgets").
Manual connect/disconnect for the canonical pump/valve/selector now goes
through DeviceManagerDialog -> DeviceLifecycleController directly; that path
is exercised by test_device_lifecycle.py, not here.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.device.device_types import PUMP, SELECTOR, SWITCH
from lspr_app.gui.experiment_control_window import ExperimentControlWindow


# ── helpers ───────────────────────────────────────────────────────────────────

# _device_label_for is pure orchestration (no Qt widgets) - wired to its real
# implementation so calling it on a MagicMock `self` actually exercises the
# label-mapping logic instead of silently no-op'ing as an auto-mocked attribute.
_REAL_DISPATCH_METHODS = ("_device_label_for",)


def _fake_self(**kwargs) -> MagicMock:
    """Return a MagicMock wired with the real dispatch methods (see
    _REAL_DISPATCH_METHODS) and any extra keyword attributes."""
    m = MagicMock()
    for name in _REAL_DISPATCH_METHODS:
        real = getattr(ExperimentControlWindow, name)
        setattr(m, name, real.__get__(m, ExperimentControlWindow))
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


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


if __name__ == "__main__":
    unittest.main()
