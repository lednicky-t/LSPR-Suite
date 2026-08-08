"""Moved here from apps/sLSPR/acq/tests (Phase 1 shell extraction,
2026-08-08) - lspr_acq_shell.port_assignments is the real owner of the
module state (_assignment_cache) and settings-store calls this test patches;
apps/sLSPR/acq/src/lspr_app/device/port_assignments.py is a thin re-export
shim with no state of its own to test.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._paths import ensure_repo_paths


ensure_repo_paths()

import lspr_acq_shell.port_assignments as port_assignments
from lspr_acq_shell.port_assignments import (
    clear_port_assignment,
    device_assignment_label,
    get_port_assignment,
    normalize_device_assignment,
    set_port_assignment,
)


class PortAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        # The module caches assignments at process scope; reset it so this test
        # never observes real settings left behind by another test/process.
        port_assignments._assignment_cache = None
        self.addCleanup(setattr, port_assignments, "_assignment_cache", None)

    def test_normalizes_assignment_values(self) -> None:
        self.assertEqual(normalize_device_assignment("Pump controller"), "pump")
        self.assertEqual(normalize_device_assignment("Switch controller"), "switch")
        self.assertEqual(normalize_device_assignment("valve"), "switch")
        self.assertEqual(normalize_device_assignment("unknown"), "auto")

    def test_get_set_and_clear_assignment(self) -> None:
        saved_payloads: list[dict[str, str]] = []

        with (
            patch("lspr_acq_shell.port_assignments.load_app_setting", return_value={}),
            patch("lspr_acq_shell.port_assignments.save_app_setting", side_effect=lambda key, value: saved_payloads.append(value)),
        ):
            self.assertEqual(get_port_assignment("COM6"), "auto")
            self.assertEqual(set_port_assignment("COM6", "valve"), "switch")
            self.assertEqual(device_assignment_label("switch"), "Switch controller")
            self.assertEqual(set_port_assignment("COM6", "auto"), "auto")
            clear_port_assignment("COM6")

        self.assertEqual(saved_payloads[0], {"COM6": "switch"})
        self.assertEqual(saved_payloads[-1], {})


if __name__ == "__main__":
    unittest.main()
