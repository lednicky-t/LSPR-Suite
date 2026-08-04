"""Unit tests for the switch-solution "details" fields (concentration/
concentration_unit/notes) added alongside the pre-existing per-port Solution
label - the minimal alternative to a full solution registry (see
apps/sLSPR/acq/docs/measurement_file_format.md, "Solutions").

Written against the production ExperimentControlWindow methods using a
duck-typed fake `self`, same pattern as tests/unit/test_device_connections.py -
no QApplication or real dialog needed, since these methods only read/build
plain data.
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

from lspr_app.gui.experiment_control_window import ExperimentControlWindow


# The methods under test call other real ExperimentControlWindow methods on
# self (switch_solution_hdf5_payload -> switch_solution_hdf5_rows/
# switch_solution_detail_hdf5_rows -> _switch_solution_label/
# _switch_solution_detail), so `self` needs to be a MagicMock with those real
# implementations bound, not a plain SimpleNamespace - same pattern as
# tests/unit/test_device_connections.py.
_REAL_DISPATCH_METHODS = (
    "_switch_solution_label",
    "_switch_solution_detail",
    "switch_solution_hdf5_rows",
    "switch_solution_detail_hdf5_rows",
    "switch_solution_hdf5_payload",
)


def _fake_window(details: list[dict[str, str]] | None = None) -> MagicMock:
    m = MagicMock()
    for name in _REAL_DISPATCH_METHODS:
        real = getattr(ExperimentControlWindow, name)
        setattr(m, name, real.__get__(m, ExperimentControlWindow))
    m._switch_solution_mode = True
    m._switch_solution_labels = ["empty"] * 12
    m._switch_solution_details = details if details is not None else [{} for _ in range(12)]
    m._valve_state_labels = {"Open": "Open", "Close": "Close"}
    m._valve_state_colors = {"Open": "#4E79A7", "Close": "#B44A4A"}
    m._color_palette_entries = []
    return m


class SwitchSolutionDetailTests(unittest.TestCase):
    def test_detail_lookup_defaults_to_empty_dict_for_missing_entries(self) -> None:
        window = _fake_window(details=[])
        detail = ExperimentControlWindow._switch_solution_detail(window, 1)
        self.assertEqual(detail, {})

    def test_detail_lookup_returns_stored_entry(self) -> None:
        details = [{} for _ in range(12)]
        details[0] = {"concentration": "10 mM", "concentration_unit": "mM", "notes": "fresh"}
        window = _fake_window(details=details)
        self.assertEqual(
            ExperimentControlWindow._switch_solution_detail(window, 1),
            {"concentration": "10 mM", "concentration_unit": "mM", "notes": "fresh"},
        )

    def test_detail_hdf5_rows_default_to_empty_strings(self) -> None:
        window = _fake_window()
        rows = ExperimentControlWindow.switch_solution_detail_hdf5_rows(window)
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[0], ["1", "", "", ""])
        self.assertEqual(rows[11], ["12", "", "", ""])

    def test_detail_hdf5_rows_reflect_stored_values(self) -> None:
        details = [{} for _ in range(12)]
        details[2] = {"concentration": "5 uM", "concentration_unit": "uM", "notes": "batch 7"}
        window = _fake_window(details=details)
        rows = ExperimentControlWindow.switch_solution_detail_hdf5_rows(window)
        self.assertEqual(rows[2], ["3", "5 uM", "uM", "batch 7"])

    def test_payload_includes_details_alongside_existing_label_keys(self) -> None:
        details = [{} for _ in range(12)]
        details[0] = {"concentration": "10 mM", "concentration_unit": "mM", "notes": "fresh"}
        window = _fake_window(details=details)
        payload = ExperimentControlWindow.switch_solution_hdf5_payload(window)
        # Pre-existing keys must still be present and untouched.
        self.assertIn("switch_solution_mode", payload)
        self.assertIn("switch_solution_labels", payload)
        self.assertIn("switch_solution_rows", payload)
        # New, additive keys.
        self.assertEqual(payload["switch_solution_details"][0], details[0])
        self.assertEqual(payload["switch_solution_detail_rows"][0], ["1", "10 mM", "mM", "fresh"])


if __name__ == "__main__":
    unittest.main()
