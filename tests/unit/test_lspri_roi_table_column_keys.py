"""How the ROI / Groups table names a column the user hid (`panels/roi_table/column_keys.py`, 2026-11-06): by what it is, not by its running number, and in a form the saved
state can hold. No Qt widgets."""

from __future__ import annotations

import json
import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.selection.step_table_module import StepColumn
    from lspr_imaging_app.panels.roi_table.column_keys import column_key, key_from_payload, key_to_payload
    from lspr_imaging_app.panels.roi_table.rows import COLUMN_COUNT
    from lspr_imaging_app.plan.state import added_ref, measured_ref
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def step(ref, title="H2O") -> StepColumn:
    return StepColumn(ref, 1, title, "", (), "#4e79a7")


def compare(uid: int, pair: int, op: str) -> StepColumn:
    return StepColumn(("cmp", uid), 0, "S1 − B1", "", (), None, "compare", "nm", None, "difference", True, "S1 - B1", op, pair)


class KeyTest(unittest.TestCase):
    def test_a_step_is_named_by_its_reference_and_a_comparison_by_pair_and_operation_not_by_its_running_number(self) -> None:
        ref = measured_ref(1, 1_700_000_000_000, "H2O_--_ch1F")
        self.assertEqual(column_key(step(ref)), ref)
        self.assertEqual(column_key(compare(7, 1, "difference")), ("cmp", 1, "difference"))
        self.assertEqual(column_key(compare(7, 1, "difference")), column_key(compare(1, 1, "difference")))  # the uid changes at every start
        self.assertNotEqual(column_key(compare(1, 1, "difference")), column_key(compare(1, 1, "snr")))  # another calculation is another column

    def test_every_fixed_column_has_a_key_the_first_one_too(self) -> None:
        for number in range(COLUMN_COUNT):
            self.assertEqual(key_from_payload(key_to_payload(number)), number)


class PayloadTest(unittest.TestCase):
    KEYS = [0, 1, 2, COLUMN_COUNT - 1, measured_ref(2, 1_700_000_005_000, "10perEG_-_ch1F"), added_ref(4), ("cmp", 2, "snr")]

    def test_every_kind_of_key_survives_the_saved_state(self) -> None:
        for key in self.KEYS:
            saved = json.loads(json.dumps(key_to_payload(key)))  # through JSON, as the state file does
            self.assertEqual(key_from_payload(saved), key, key)

    def test_what_cannot_be_read_is_skipped(self) -> None:
        for raw in (None, True, False, "x", COLUMN_COUNT, -1, 2.5, [], {}, {"compare": [1]}, {"compare": ["a", "snr"]}, {"compare": [True, "snr"]}, {"step": "no"}, {"added": "no"}, {"junk": 1}):
            self.assertIsNone(key_from_payload(raw), raw)


if __name__ == "__main__":
    unittest.main()
