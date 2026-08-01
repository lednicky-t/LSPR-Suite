"""Integration tests for the Device Manager Devices tab's per-device detail
pages (left device list + right detail panel - see
gui/device_console_dialog.py).

Replaces the old double-click-to-open QMenu this file covered previously:
that popup was deleted when Device Manager was redesigned around always-
visible per-device pages (Stats / Capabilities / Defaults & limits), one per
device_key in _DEVICE_PAGE_ORDER (Spectrometer/Pump/Switch/Selector).
"""
from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6 import QtWidgets

from lspr_app.device.device_lifecycle import DeviceLifecycleController
from lspr_app.device.device_manager import DeviceCommunicationService
from lspr_app.device.device_types import PUMP, SELECTOR, SWITCH
from lspr_app.gui.device_console_dialog import DeviceManagerDialog
from lspr_app.storage.device_manager_settings import DeviceManagerSettings


class _FakeService:
    """Minimal DeviceCommunicationService stand-in, just enough for
    DeviceManagerDialog.__init__ -> refresh_all() to run without error."""

    def list_profiles(self):
        return []

    def scan_passive(self):
        return []

    def list_devices(self):
        return []


def _fake_window(**kwargs) -> QtWidgets.QWidget:
    """A real QWidget (DeviceManagerDialog's QDialog parent must be one) with
    extra attributes stapled on to stand in for MainWindow."""
    widget = QtWidgets.QWidget()
    widget._device_manager_settings = DeviceManagerSettings()
    for key, value in kwargs.items():
        setattr(widget, key, value)
    return widget


class DeviceDetailPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _patch_controller(self, fake_controller=None) -> MagicMock:
        # refresh_all() (called at the end of DeviceManagerDialog.__init__)
        # now reads DeviceLifecycleController.shared().enabled_devices() on
        # every refresh, so this must stay patched for the dialog's whole
        # lifetime in a test, not just around one call - kept active via
        # enterContext (torn down automatically at test teardown) rather than
        # a `with` block that would exit before the test body finishes.
        fake_controller = fake_controller or MagicMock()
        fake_controller.enabled_devices.return_value = {PUMP: True, SWITCH: True, SELECTOR: True}
        self.enterContext(patch.object(DeviceLifecycleController, "shared", return_value=fake_controller))
        return fake_controller

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

    def test_device_list_has_all_four_devices_in_order(self) -> None:
        self._patch_controller()
        dialog = self._make_dialog(_fake_window())
        labels = [dialog._device_list.item(i).text() for i in range(dialog._device_list.count())]
        self.assertEqual(
            labels,
            ["Spectrometer", "Pump (Reglo ICC)", "Switch valve", "Selector rotary valve (AMF M-Switch)"],
        )

    def test_pump_page_has_tube_diameter_default_and_no_capabilities_row(self) -> None:
        self._patch_controller()
        dialog = self._make_dialog(_fake_window())
        pump_page = dialog._device_pages[PUMP]
        self.assertNotIn("poll_interval_row", pump_page)
        self.assertIn("tube_mm_spin", pump_page)

    def test_switch_page_shows_temp_humidity_row_for_arduino_valve(self) -> None:
        self._patch_controller()
        dialog = self._make_dialog(_fake_window())
        status = SimpleNamespace(
            connected=True, state="connected", endpoint="COM3", identity={"controller_type": "arduino-valve"},
        )
        profile = SimpleNamespace(endpoint="COM3", fingerprint="fp", identity={"controller_type": "arduino-valve"})

        dialog._refresh_canonical_device_page(SWITCH, status, profile, True)

        page = dialog._device_pages[SWITCH]
        # isVisible() would be False regardless of setVisible() here, since
        # the dialog itself is never shown in this test - isHidden() checks
        # the widget's own explicit visibility flag instead of the whole
        # ancestor chain.
        self.assertFalse(page["poll_interval_row"].isHidden())
        self.assertIn("Temperature sensor: yes", page["capabilities_label"].text())
        self.assertIn("Humidity sensor: yes", page["capabilities_label"].text())

    def test_switch_page_hides_temp_humidity_row_for_itsybitsy(self) -> None:
        self._patch_controller()
        dialog = self._make_dialog(_fake_window())
        status = SimpleNamespace(
            connected=True, state="connected", endpoint="COM4", identity={"controller_type": "itsybitsy-32u4-valve"},
        )
        profile = SimpleNamespace(
            endpoint="COM4", fingerprint="fp2", identity={"controller_type": "itsybitsy-32u4-valve"},
        )

        dialog._refresh_canonical_device_page(SWITCH, status, profile, True)

        page = dialog._device_pages[SWITCH]
        self.assertTrue(page["poll_interval_row"].isHidden())
        self.assertIn("Temperature sensor: no", page["capabilities_label"].text())
        self.assertIn("Humidity sensor: no", page["capabilities_label"].text())

    def test_switch_page_hides_temp_humidity_row_when_never_connected(self) -> None:
        self._patch_controller()
        dialog = self._make_dialog(_fake_window())

        dialog._refresh_canonical_device_page(SWITCH, None, None, True)

        page = dialog._device_pages[SWITCH]
        self.assertTrue(page["poll_interval_row"].isHidden())

    def test_toggling_enable_checkbox_calls_apply_device_enablement(self) -> None:
        fake_controller = self._patch_controller()
        parent_window = _fake_window(_apply_device_enablement=MagicMock())
        dialog = self._make_dialog(parent_window)

        checkbox = dialog._device_pages[SELECTOR]["enabled_check"]
        checkbox.setChecked(False)

        parent_window._apply_device_enablement.assert_called_once()
        called_with = parent_window._apply_device_enablement.call_args[0][0]
        self.assertEqual(called_with[SELECTOR], False)
        fake_controller.enabled_devices.assert_called()


if __name__ == "__main__":
    unittest.main()
