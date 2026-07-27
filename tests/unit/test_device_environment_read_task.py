"""Unit tests for DeviceEnvironmentReadTask (gui/device_lifecycle_task.py) -
the periodic ambient temperature/humidity poll of the Switch device. See
docs/hardware/arduino_valve_controller_protocol.md.

Uses a MagicMock in place of DeviceCommunicationService - no hardware, no
QApplication required (task.run() is called directly, and signal emission
in the same thread doesn't need a running event loop).
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

from lspr_app.device.communication_models import DeviceCommandResult
from lspr_app.gui.device_lifecycle_task import DeviceEnvironmentReadTask


def _run_task(fake_service: MagicMock) -> tuple[object, object]:
    task = DeviceEnvironmentReadTask()
    results: list[tuple[object, object]] = []
    task.signals.finished.connect(lambda temperature_c, humidity_percent: results.append((temperature_c, humidity_percent)))
    with patch(
        "lspr_app.gui.device_lifecycle_task.DeviceCommunicationService.shared",
        return_value=fake_service,
    ):
        task.run()
    assert len(results) == 1
    return results[0]


class DeviceEnvironmentReadTaskTests(unittest.TestCase):
    def test_both_readings_succeed(self) -> None:
        service = MagicMock()
        service.send_command.side_effect = [
            DeviceCommandResult("switch_1", "switch.read_ambient_temperature", True, 23.4, None, 5.0),
            DeviceCommandResult("switch_1", "switch.read_humidity", True, 41.2, None, 5.0),
        ]

        temperature_c, humidity_percent = _run_task(service)

        self.assertEqual(temperature_c, 23.4)
        self.assertEqual(humidity_percent, 41.2)
        self.assertEqual(service.send_command.call_count, 2)

    def test_unsupported_controller_yields_none_none(self) -> None:
        # Matches what a connected ItsyBitsy/Legacy controller (or nothing
        # connected at all) actually returns - send_command() never raises,
        # it reports success=False (see device_manager.py's send_command).
        service = MagicMock()
        service.send_command.side_effect = [
            DeviceCommandResult("switch_1", "switch.read_ambient_temperature", False, None, "not supported", 1.0),
            DeviceCommandResult("switch_1", "switch.read_humidity", False, None, "not supported", 1.0),
        ]

        temperature_c, humidity_percent = _run_task(service)

        self.assertIsNone(temperature_c)
        self.assertIsNone(humidity_percent)

    def test_partial_success_keeps_the_successful_value(self) -> None:
        service = MagicMock()
        service.send_command.side_effect = [
            DeviceCommandResult("switch_1", "switch.read_ambient_temperature", True, 22.1, None, 5.0),
            DeviceCommandResult("switch_1", "switch.read_humidity", False, None, "timeout", 1.0),
        ]

        temperature_c, humidity_percent = _run_task(service)

        self.assertEqual(temperature_c, 22.1)
        self.assertIsNone(humidity_percent)

    def test_non_numeric_response_is_ignored_not_crashed_on(self) -> None:
        service = MagicMock()
        service.send_command.side_effect = [
            DeviceCommandResult("switch_1", "switch.read_ambient_temperature", True, "err", None, 5.0),
            DeviceCommandResult("switch_1", "switch.read_humidity", True, 41.2, None, 5.0),
        ]

        temperature_c, humidity_percent = _run_task(service)

        self.assertIsNone(temperature_c)
        self.assertEqual(humidity_percent, 41.2)


if __name__ == "__main__":
    unittest.main()
