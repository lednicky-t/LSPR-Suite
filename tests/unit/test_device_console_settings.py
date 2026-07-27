"""Unit tests for the Device Manager per-device settings popup added when
"Hardware devices…" was folded into Device Manager (double-click a
canonical row -> QMenu with that device's settings).

Two pure/near-pure pieces are covered without needing a real QApplication
or QMenu event loop:
- device_settings_title(): the friendly name shown atop the popup,
  including the Switch row's dynamic controller-name suffix.
- set_environment_poll_interval_s(): persists + clamps the temp/humidity
  poll interval and updates the live QTimer.
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

from lspr_app.device.device_types import PUMP, SELECTOR, SWITCH
from lspr_app.device.device_lifecycle import DeviceLifecycleController
from lspr_app.gui.device_console_dialog import device_settings_title
from lspr_app.gui.main_window_state import set_environment_poll_interval_s


class DeviceSettingsTitleTests(unittest.TestCase):
    def _title(self, device_key: str, probe=None) -> str:
        fake_controller = MagicMock()
        fake_controller.probe_for.return_value = probe
        with patch.object(DeviceLifecycleController, "shared", return_value=fake_controller):
            return device_settings_title(device_key)

    def test_pump_title_is_static(self) -> None:
        self.assertEqual(self._title(PUMP), "Pump (Reglo ICC)")

    def test_selector_title_is_static(self) -> None:
        self.assertEqual(self._title(SELECTOR), "Selector rotary valve (AMF M-Switch)")

    def test_switch_title_with_no_probe_has_no_suffix(self) -> None:
        self.assertEqual(self._title(SWITCH, probe=None), "Switch valve")

    def test_switch_title_with_arduino_controller(self) -> None:
        probe = SimpleNamespace(controller_type="arduino-valve")
        self.assertEqual(self._title(SWITCH, probe=probe), "Switch valve (Arduino)")

    def test_switch_title_with_itsybitsy_controller(self) -> None:
        probe = SimpleNamespace(controller_type="itsybitsy-32u4-valve")
        self.assertEqual(self._title(SWITCH, probe=probe), "Switch valve (ItsyBitsy)")

    def test_switch_title_with_legacy_controller(self) -> None:
        probe = SimpleNamespace(controller_type="legacy-valve")
        self.assertEqual(self._title(SWITCH, probe=probe), "Switch valve (Legacy)")

    def test_switch_title_with_unknown_controller_type_has_no_suffix(self) -> None:
        probe = SimpleNamespace(controller_type="something-else")
        self.assertEqual(self._title(SWITCH, probe=probe), "Switch valve")


class SetEnvironmentPollIntervalTests(unittest.TestCase):
    def _window(self) -> SimpleNamespace:
        return SimpleNamespace(
            _environment_poll_timer=MagicMock(),
            status_label=MagicMock(),
            _log_info=MagicMock(),
        )

    def test_persists_and_updates_timer(self) -> None:
        window = self._window()
        with patch("lspr_app.storage.app_config.save_app_setting") as mock_save:
            set_environment_poll_interval_s(window, 10.0)
        self.assertEqual(window._environment_poll_interval_s, 10.0)
        window._environment_poll_timer.setInterval.assert_called_once_with(10_000)
        mock_save.assert_called_once_with("environment_poll_interval_s", 10.0)

    def test_clamps_below_minimum(self) -> None:
        window = self._window()
        with patch("lspr_app.storage.app_config.save_app_setting"):
            set_environment_poll_interval_s(window, 0.1)
        self.assertEqual(window._environment_poll_interval_s, 1.0)
        window._environment_poll_timer.setInterval.assert_called_once_with(1_000)

    def test_clamps_above_maximum(self) -> None:
        window = self._window()
        with patch("lspr_app.storage.app_config.save_app_setting"):
            set_environment_poll_interval_s(window, 10_000.0)
        self.assertEqual(window._environment_poll_interval_s, 300.0)
        window._environment_poll_timer.setInterval.assert_called_once_with(300_000)

    def test_missing_timer_does_not_raise(self) -> None:
        window = SimpleNamespace(status_label=MagicMock(), _log_info=MagicMock())
        with patch("lspr_app.storage.app_config.save_app_setting"):
            set_environment_poll_interval_s(window, 5.0)
        self.assertEqual(window._environment_poll_interval_s, 5.0)


if __name__ == "__main__":
    unittest.main()
