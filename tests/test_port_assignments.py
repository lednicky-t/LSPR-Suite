from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
import sys

if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.device.port_assignments import (
    clear_port_assignment,
    device_assignment_label,
    get_port_assignment,
    normalize_device_assignment,
    set_port_assignment,
)


class PortAssignmentTests(unittest.TestCase):
    def test_normalizes_assignment_values(self) -> None:
        self.assertEqual(normalize_device_assignment("Pump controller"), "pump")
        self.assertEqual(normalize_device_assignment("valve"), "valve")
        self.assertEqual(normalize_device_assignment("unknown"), "auto")

    def test_get_set_and_clear_assignment(self) -> None:
        saved_payloads: list[dict[str, str]] = []

        with (
            patch("lspr_app.device.port_assignments.load_app_setting", return_value={}),
            patch("lspr_app.device.port_assignments.save_app_setting", side_effect=lambda key, value: saved_payloads.append(value)),
        ):
            self.assertEqual(get_port_assignment("COM6"), "auto")
            self.assertEqual(set_port_assignment("COM6", "valve"), "valve")
            self.assertEqual(device_assignment_label("valve"), "Valve controller")
            self.assertEqual(set_port_assignment("COM6", "auto"), "auto")
            clear_port_assignment("COM6")

        self.assertEqual(saved_payloads[0], {"COM6": "valve"})
        self.assertEqual(saved_payloads[-1], {})


if __name__ == "__main__":
    unittest.main()
