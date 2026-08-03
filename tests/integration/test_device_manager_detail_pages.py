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
from PyQt6.QtCore import Qt

from lspr_app.device.device_lifecycle import DeviceLifecycleController
from lspr_app.device.device_manager import DeviceCommunicationService
from lspr_app.device.device_types import PUMP, SELECTOR, SWITCH
from lspr_app.device.simulated import SimulatedSpectrometer
from lspr_app.gui import device_console_dialog as dcd
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

    def test_pump_page_has_backsteps_and_max_flow_defaults(self) -> None:
        # Regression test: Backsteps (drip-free dispensing, manual sec.
        # 6.4.3) and the Experiment Control flow-rate soft cap are the two
        # newer "Defaults & limits" fields alongside tube diameter - see
        # storage/device_manager_settings.py's PumpDefaults.
        self._patch_controller()
        settings = DeviceManagerSettings()
        settings.pump.backsteps = 15
        settings.pump.max_flow_ul_min = 250.0
        dialog = self._make_dialog(_fake_window(_device_manager_settings=settings))
        pump_page = dialog._device_pages[PUMP]

        self.assertIn("backsteps_spin", pump_page)
        self.assertIn("max_flow_spin", pump_page)
        self.assertEqual(pump_page["backsteps_spin"].value(), 15)
        self.assertEqual(pump_page["max_flow_spin"].value(), 250.0)

    def test_pump_page_has_roller_count_combo_defaulting_to_current_setting(self) -> None:
        # Regression test: cassette-head roller count (6/8/12, manual sec.
        # 6.12-6.13) is the third "Defaults & limits" field - must match the
        # physical cassette head or the pump's mL/min conversion is skewed.
        self._patch_controller()
        settings = DeviceManagerSettings()
        settings.pump.roller_count = 12
        dialog = self._make_dialog(_fake_window(_device_manager_settings=settings))
        pump_page = dialog._device_pages[PUMP]

        combo = pump_page["roller_count_combo"]
        self.assertEqual([combo.itemData(i) for i in range(combo.count())], [6, 8, 12])
        self.assertEqual(combo.currentData(), 12)

    def test_changing_roller_count_combo_calls_setter_on_parent(self) -> None:
        self._patch_controller()
        parent_window = _fake_window(_set_pump_default_roller_count=MagicMock())
        dialog = self._make_dialog(parent_window)

        combo = dialog._device_pages[PUMP]["roller_count_combo"]
        combo.setCurrentIndex(combo.findData(6))

        parent_window._set_pump_default_roller_count.assert_called_once_with(6)

    def test_pump_page_has_four_calibration_channel_labels(self) -> None:
        self._patch_controller()
        dialog = self._make_dialog(_fake_window())
        labels = dialog._device_pages[PUMP]["calibration_channel_labels"]
        self.assertEqual(len(labels), 4)
        for label in labels:
            self.assertEqual(label.text(), "-")

    def test_refresh_calibration_status_shows_not_connected(self) -> None:
        self._patch_controller()
        dialog = self._make_dialog(_fake_window())
        fake_service = MagicMock()
        fake_service.status.return_value = SimpleNamespace(connected=False)
        dialog._service = fake_service

        dialog._refresh_pump_calibration_status()

        for label in dialog._device_pages[PUMP]["calibration_channel_labels"]:
            self.assertEqual(label.text(), "Pump not connected.")

    def test_refresh_calibration_status_shows_hours_and_warns_past_threshold(self) -> None:
        # Regression test for the "time since last calibration" indicator:
        # PUMP_CALIBRATION_WARNING_HOURS is our own placeholder (the manual
        # gives no recalibration-interval guidance) - below it should read
        # as a plain status, at/above it should read as a soft warning.
        self._patch_controller()
        dialog = self._make_dialog(_fake_window())
        fake_service = MagicMock()
        fake_service.status.return_value = SimpleNamespace(connected=True)

        def _send_command(_label, command):
            channel = command.payload["channel"]
            seconds = 3600.0 if channel == 1 else 3600.0 * (dcd.PUMP_CALIBRATION_WARNING_HOURS + 1)
            return SimpleNamespace(success=True, response=seconds, error=None)

        fake_service.send_command.side_effect = _send_command
        dialog._service = fake_service

        dialog._refresh_pump_calibration_status()

        labels = dialog._device_pages[PUMP]["calibration_channel_labels"]
        self.assertIn("1.0 h since last calibration", labels[0].text())
        self.assertNotIn("recalibrating", labels[0].text())
        self.assertIn("consider recalibrating", labels[1].text())

    def test_refresh_calibration_status_shows_command_error(self) -> None:
        self._patch_controller()
        dialog = self._make_dialog(_fake_window())
        fake_service = MagicMock()
        fake_service.status.return_value = SimpleNamespace(connected=True)
        fake_service.send_command.return_value = SimpleNamespace(success=False, response=None, error="timeout")
        dialog._service = fake_service

        dialog._refresh_pump_calibration_status()

        for label in dialog._device_pages[PUMP]["calibration_channel_labels"]:
            self.assertIn("Error", label.text())

    def test_pump_page_has_open_calibration_button_instead_of_refresh(self) -> None:
        # Regression test: the calibration status used to have its own
        # manual "Refresh" button; it's now refreshed automatically whenever
        # the Pump page becomes visible (see _on_device_list_row_changed/
        # showEvent) and this button opens the calibration pop-out window
        # instead - see _open_pump_calibration_window.
        self._patch_controller()
        dialog = self._make_dialog(_fake_window())
        button = dialog._device_pages[PUMP]["open_calibration_button"]
        self.assertEqual(button.text(), "Open Pump Calibration...")

    def test_selecting_pump_in_device_list_triggers_calibration_refresh(self) -> None:
        self._patch_controller()
        dialog = self._make_dialog(_fake_window())
        fake_service = MagicMock()
        fake_service.status.return_value = SimpleNamespace(connected=True)
        fake_service.send_command.return_value = SimpleNamespace(success=True, response=3600.0, error=None)
        dialog._service = fake_service

        pump_row = dcd._DEVICE_PAGE_ORDER.index(PUMP)
        dialog._device_list.setCurrentRow(pump_row)

        fake_service.send_command.assert_called()
        for label in dialog._device_pages[PUMP]["calibration_channel_labels"]:
            self.assertIn("1.0 h", label.text())

    def test_selecting_a_non_pump_device_does_not_query_calibration(self) -> None:
        self._patch_controller()
        dialog = self._make_dialog(_fake_window())
        fake_service = MagicMock()
        fake_service.status.return_value = SimpleNamespace(connected=True)
        dialog._service = fake_service

        switch_row = dcd._DEVICE_PAGE_ORDER.index(SWITCH)
        dialog._device_list.setCurrentRow(switch_row)

        fake_service.send_command.assert_not_called()

    def test_open_calibration_button_lazily_creates_and_reuses_the_same_window(self) -> None:
        self._patch_controller()
        dialog = self._make_dialog(_fake_window())
        self.assertIsNone(dialog._pump_calibration_window)

        dialog._open_pump_calibration_window()
        first_window = dialog._pump_calibration_window
        self.assertIsNotNone(first_window)

        dialog._open_pump_calibration_window()
        self.assertIs(dialog._pump_calibration_window, first_window)

    def test_changing_backsteps_spin_calls_setter_on_parent(self) -> None:
        self._patch_controller()
        parent_window = _fake_window(_set_pump_default_backsteps=MagicMock())
        dialog = self._make_dialog(parent_window)

        dialog._device_pages[PUMP]["backsteps_spin"].setValue(33)

        parent_window._set_pump_default_backsteps.assert_called_once_with(33)

    def test_changing_max_flow_spin_calls_setter_on_parent(self) -> None:
        self._patch_controller()
        parent_window = _fake_window(_set_pump_default_max_flow_ul_min=MagicMock())
        dialog = self._make_dialog(parent_window)

        dialog._device_pages[PUMP]["max_flow_spin"].setValue(500.0)

        parent_window._set_pump_default_max_flow_ul_min.assert_called_once_with(500.0)

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

    def test_spectrometer_dot_distinguishes_simulated_from_real_and_disconnected(self) -> None:
        # Regression test: the simulated backend used to be shown with the
        # same green "connected" dot as a real spectrometer, which reads as
        # "hardware is plugged in" when it isn't (see refresh_connected_devices
        # / _refresh_spectrometer_page).
        self._patch_controller()

        # Keep every parent_window/dialog pair alive for the whole test: once
        # a parentless QWidget's last Python reference drops, Qt destroys it
        # (and cascades to its child dialog) immediately - see the comment on
        # _make_dialog above.
        keep_alive: list[tuple[QtWidgets.QWidget, DeviceManagerDialog]] = []

        def dot_color(spectrometer):
            parent_window = _fake_window(_spectrometer=spectrometer)
            with patch.object(DeviceCommunicationService, "shared", return_value=_FakeService()):
                dialog = DeviceManagerDialog(parent_window)
            keep_alive.append((parent_window, dialog))
            item = dialog._device_list.item(0)
            self.assertEqual(item.data(Qt.ItemDataRole.UserRole), "spectrometer")
            return item.icon().pixmap(14, 14).toImage().pixelColor(7, 7)

        self.assertEqual(dot_color(None), dcd._STATUS_COLOR_DISCONNECTED)
        self.assertEqual(dot_color(SimulatedSpectrometer()), dcd._STATUS_COLOR_SIMULATED)

        real_spectrometer = SimpleNamespace(device_name=lambda: "Ocean HR4000")
        self.assertEqual(dot_color(real_spectrometer), dcd._STATUS_COLOR_CONNECTED)

        for _parent_window, dialog in keep_alive:
            dialog.deleteLater()

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


class PumpCalibrationTabTests(unittest.TestCase):
    """Coverage for the deep-debug "Pump Calibration" test-bench tab (see
    DeviceManagerDialog._build_pump_calibration_tab) - exercises the button
    handlers against a fake DeviceCommunicationService rather than real
    hardware, checking the exact DeviceCommand types/payloads dispatched."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _patch_controller(self) -> MagicMock:
        fake_controller = MagicMock()
        fake_controller.enabled_devices.return_value = {PUMP: True, SWITCH: True, SELECTOR: True}
        self.enterContext(patch.object(DeviceLifecycleController, "shared", return_value=fake_controller))
        return fake_controller

    def _make_dialog_with_fake_send(self) -> tuple[DeviceManagerDialog, MagicMock]:
        parent_window = _fake_window()
        self._parent_window = parent_window
        with patch.object(DeviceCommunicationService, "shared", return_value=_FakeService()):
            dialog = DeviceManagerDialog(parent_window)
        self.addCleanup(dialog.deleteLater)
        fake_service = MagicMock()
        dialog._service = fake_service
        return dialog, fake_service

    def test_apply_settings_sends_direction_target_and_duration(self) -> None:
        self._patch_controller()
        dialog, fake_service = self._make_dialog_with_fake_send()
        fake_service.send_command.return_value = SimpleNamespace(success=True, response="*", error=None)

        dialog._cal_channel_spin.setValue(2)
        dialog._cal_direction_combo.setCurrentIndex(dialog._cal_direction_combo.findData("CCW"))
        dialog._cal_target_volume_spin.setValue(50.0)
        dialog._cal_time_spin.setValue(30.0)

        dialog._apply_pump_calibration_settings()

        calls = fake_service.send_command.call_args_list
        self.assertEqual(len(calls), 3)
        labels = [call.args[0] for call in calls]
        self.assertTrue(all(label == "pump_1" for label in labels))
        commands = [call.args[1] for call in calls]
        self.assertEqual(commands[0].command_type, "pump.calibration.set_direction")
        self.assertEqual(commands[0].payload, {"channel": 2, "direction": "CCW"})
        self.assertEqual(commands[1].command_type, "pump.calibration.set_target_volume_ml")
        self.assertEqual(commands[1].payload, {"channel": 2, "volume_ml": 50.0})
        self.assertEqual(commands[2].command_type, "pump.calibration.set_time_s")
        self.assertEqual(commands[2].payload, {"channel": 2, "seconds": 30.0})

    def test_start_calibration_sends_start_command_for_selected_channel(self) -> None:
        self._patch_controller()
        dialog, fake_service = self._make_dialog_with_fake_send()
        fake_service.send_command.return_value = SimpleNamespace(success=True, response="*", error=None)
        dialog._cal_channel_spin.setValue(3)

        dialog._start_pump_calibration()

        label, command = fake_service.send_command.call_args.args
        self.assertEqual(label, "pump_1")
        self.assertEqual(command.command_type, "pump.calibration.start")
        self.assertEqual(command.payload, {"channel": 3})

    def test_cancel_calibration_sends_cancel_command(self) -> None:
        self._patch_controller()
        dialog, fake_service = self._make_dialog_with_fake_send()
        fake_service.send_command.return_value = SimpleNamespace(success=True, response="*", error=None)
        dialog._cal_channel_spin.setValue(1)

        dialog._cancel_pump_calibration()

        _label, command = fake_service.send_command.call_args.args
        self.assertEqual(command.command_type, "pump.calibration.cancel")

    def test_submit_measured_volume_logs_deviation_against_target(self) -> None:
        self._patch_controller()
        dialog, fake_service = self._make_dialog_with_fake_send()
        dialog._cal_target_volume_spin.setValue(100.0)
        dialog._cal_measured_volume_spin.setValue(102.0)
        fake_service.send_command.return_value = SimpleNamespace(success=True, response=102.0, error=None)

        dialog._submit_pump_calibration_measured_volume()

        _label, command = fake_service.send_command.call_args.args
        self.assertEqual(command.command_type, "pump.calibration.set_measured_volume_ml")
        self.assertEqual(command.payload["volume_ml"], 102.0)
        self.assertIn("+2.00%", dialog._cal_result.toPlainText())

    def test_read_roller_step_volume_updates_readout_label(self) -> None:
        self._patch_controller()
        dialog, fake_service = self._make_dialog_with_fake_send()
        fake_service.send_command.return_value = SimpleNamespace(success=True, response=0.012345, error=None)

        dialog._read_pump_roller_step_volume()

        self.assertIn("0.012345", dialog._cal_roller_step_volume_label.text())

    def test_write_roller_step_volume_sends_set_command(self) -> None:
        self._patch_controller()
        dialog, fake_service = self._make_dialog_with_fake_send()
        fake_service.send_command.return_value = SimpleNamespace(success=True, response="*", error=None)
        dialog._cal_channel_spin.setValue(4)
        dialog._cal_roller_step_volume_write_spin.setValue(0.02)

        dialog._write_pump_roller_step_volume()

        label, command = fake_service.send_command.call_args.args
        self.assertEqual(command.command_type, "pump.roller_step_volume.set")
        self.assertEqual(command.payload, {"channel": 4, "volume_ml": 0.02})

    def test_refresh_time_since_last_updates_label_with_hours_and_raw_seconds(self) -> None:
        self._patch_controller()
        dialog, fake_service = self._make_dialog_with_fake_send()
        fake_service.send_command.return_value = SimpleNamespace(success=True, response=7200.0, error=None)

        dialog._refresh_pump_calibration_time_since_last()

        self.assertIn("2.00 h", dialog._cal_time_since_last_label.text())
        self.assertIn("7200.0 s", dialog._cal_time_since_last_label.text())

    def test_failed_command_is_logged_without_raising(self) -> None:
        self._patch_controller()
        dialog, fake_service = self._make_dialog_with_fake_send()
        fake_service.send_command.return_value = SimpleNamespace(success=False, response=None, error="No response")

        dialog._start_pump_calibration()

        self.assertIn("FAILED", dialog._cal_result.toPlainText())
        self.assertIn("No response", dialog._cal_result.toPlainText())

    def test_implied_flow_rate_label_updates_from_target_and_duration(self) -> None:
        self._patch_controller()
        dialog, _fake_service = self._make_dialog_with_fake_send()

        dialog._cal_target_volume_spin.setValue(10.0)
        dialog._cal_time_spin.setValue(60.0)

        self.assertEqual(dialog._cal_implied_flow_label.text(), "10 mL/min")

    def test_why_button_sends_get_start_failure_reason_command(self) -> None:
        self._patch_controller()
        dialog, fake_service = self._make_dialog_with_fake_send()
        fake_service.send_command.return_value = SimpleNamespace(
            success=True,
            response="requested flow rate exceeds the max the pump/tubing can achieve, or flow is set to 0 "
            "(limited by max achievable flow rate (mL/min))",
            error=None,
        )
        dialog._cal_channel_spin.setValue(1)

        dialog._diagnose_pump_calibration_start_failure()

        label, command = fake_service.send_command.call_args.args
        self.assertEqual(command.command_type, "pump.get_start_failure_reason")
        self.assertEqual(command.payload, {"channel": 1})
        self.assertIn("flow rate", dialog._cal_result.toPlainText())


if __name__ == "__main__":
    unittest.main()
