"""Integration test for Device Manager's double-click-to-settings popup
(added when the standalone "Hardware devices…" dialog was folded into
Device Manager). Builds the QMenu directly via _build_device_settings_menu
- never calls .exec(), which would block on the menu's own event loop -
and inspects its actions/widgets.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6 import QtWidgets
from PyQt6.QtWidgets import QCheckBox, QDoubleSpinBox, QWidgetAction

from lspr_app.device.device_lifecycle import DeviceLifecycleController
from lspr_app.device.device_manager import DeviceCommunicationService
from lspr_app.device.device_types import PUMP, SELECTOR, SWITCH
from lspr_app.gui.device_console_dialog import DeviceManagerDialog


class _FakeService:
    """Minimal DeviceCommunicationService stand-in, just enough for
    DeviceManagerDialog.__init__ -> refresh_all() to run without error."""

    def list_profiles(self):
        return []

    def scan_passive(self):
        return []

    def list_devices(self):
        return []


def _widget_actions(menu) -> list[QWidgetAction]:
    return [a for a in menu.actions() if isinstance(a, QWidgetAction)]


def _fake_window(**kwargs) -> QtWidgets.QWidget:
    """A real QWidget (DeviceManagerDialog's QDialog parent must be one) with
    extra attributes stapled on to stand in for MainWindow."""
    widget = QtWidgets.QWidget()
    for key, value in kwargs.items():
        setattr(widget, key, value)
    return widget


class DeviceSettingsMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_dialog(self, parent_window) -> DeviceManagerDialog:
        # Keep parent_window alive for the rest of the test: once its last
        # Python reference drops, Qt destroys the (parentless) QWidget
        # immediately, which cascades to destroy this child dialog too -
        # any later addCleanup(dialog.deleteLater) would then hit an
        # already-deleted C++ object.
        self._parent_window = parent_window
        with patch.object(DeviceCommunicationService, "shared", return_value=_FakeService()):
            dialog = DeviceManagerDialog(parent_window)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_pump_menu_has_only_the_enable_row(self) -> None:
        parent_window = _fake_window(_apply_device_enablement=MagicMock())
        dialog = self._make_dialog(parent_window)
        fake_controller = MagicMock()
        fake_controller.enabled_devices.return_value = {PUMP: True, SWITCH: True, SELECTOR: True}
        fake_controller.probe_for.return_value = None
        with patch.object(DeviceLifecycleController, "shared", return_value=fake_controller):
            menu = dialog._build_device_settings_menu(PUMP)
        widget_actions = _widget_actions(menu)
        self.assertEqual(len(widget_actions), 1)
        checkbox = widget_actions[0].defaultWidget().findChild(QCheckBox)
        self.assertIsNotNone(checkbox)
        self.assertTrue(checkbox.isChecked())

    def test_switch_menu_gains_frequency_row_only_for_arduino_valve(self) -> None:
        parent_window = _fake_window(
            _apply_device_enablement=MagicMock(),
            _set_environment_poll_interval_s=MagicMock(),
        )
        dialog = self._make_dialog(parent_window)
        fake_controller = MagicMock()
        fake_controller.enabled_devices.return_value = {PUMP: True, SWITCH: True, SELECTOR: True}
        fake_controller.probe_for.return_value = MagicMock(controller_type="arduino-valve")
        with patch.object(DeviceLifecycleController, "shared", return_value=fake_controller):
            menu = dialog._build_device_settings_menu(SWITCH)
        widget_actions = _widget_actions(menu)
        self.assertEqual(len(widget_actions), 2)
        spin = widget_actions[1].defaultWidget().findChild(QDoubleSpinBox)
        self.assertIsNotNone(spin)

    def test_switch_menu_has_no_frequency_row_for_itsybitsy(self) -> None:
        parent_window = _fake_window(_apply_device_enablement=MagicMock())
        dialog = self._make_dialog(parent_window)
        fake_controller = MagicMock()
        fake_controller.enabled_devices.return_value = {PUMP: True, SWITCH: True, SELECTOR: True}
        fake_controller.probe_for.return_value = MagicMock(controller_type="itsybitsy-32u4-valve")
        with patch.object(DeviceLifecycleController, "shared", return_value=fake_controller):
            menu = dialog._build_device_settings_menu(SWITCH)
        self.assertEqual(len(_widget_actions(menu)), 1)

    def test_switch_menu_has_no_frequency_row_when_disconnected(self) -> None:
        parent_window = _fake_window(_apply_device_enablement=MagicMock())
        dialog = self._make_dialog(parent_window)
        fake_controller = MagicMock()
        fake_controller.enabled_devices.return_value = {PUMP: True, SWITCH: True, SELECTOR: True}
        fake_controller.probe_for.return_value = None
        with patch.object(DeviceLifecycleController, "shared", return_value=fake_controller):
            menu = dialog._build_device_settings_menu(SWITCH)
        self.assertEqual(len(_widget_actions(menu)), 1)

    def test_toggling_enable_checkbox_calls_apply_device_enablement(self) -> None:
        parent_window = _fake_window(_apply_device_enablement=MagicMock())
        dialog = self._make_dialog(parent_window)
        fake_controller = MagicMock()
        fake_controller.enabled_devices.return_value = {PUMP: True, SWITCH: True, SELECTOR: True}
        fake_controller.probe_for.return_value = None
        with patch.object(DeviceLifecycleController, "shared", return_value=fake_controller):
            menu = dialog._build_device_settings_menu(SELECTOR)
            checkbox = _widget_actions(menu)[0].defaultWidget().findChild(QCheckBox)
            checkbox.setChecked(False)
        parent_window._apply_device_enablement.assert_called_once()
        called_with = parent_window._apply_device_enablement.call_args[0][0]
        self.assertEqual(called_with[SELECTOR], False)


if __name__ == "__main__":
    unittest.main()
