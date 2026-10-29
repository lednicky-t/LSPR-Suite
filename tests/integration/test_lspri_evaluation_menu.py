"""The Sensorgram's Evaluation pop-up (2026-10-30): one ribbon icon, a table of B1 / S1 / B2 / S2 ... with a step picker per row, and icons to add or remove a pair.

`EvaluationControls` is driven on its own (no panel, no dataset): steps and pairs are handed in as the plan module would hand them, picks are made through the pickers' own
signals, and the pop-up is shown offscreen once to check that an open menu grows when a pair is added. Nothing is clicked by screen coordinate. The wiring to the plan module and the
bar is in `test_lspri_plan_bar.py` (a second pair picked in the pop-up); the plan edit is in `test_lspri_plan_structure*.py`.
"""

from __future__ import annotations

import importlib
import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    importlib.import_module("lspr_imaging_app.app_rewrite")  # first: importing panels.sensorgram before panels.image is a circular import (the app's own order is fine)
    from lspr_imaging_app.panels.sensorgram import evaluation_group as eg
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.plan.edits import EffectiveStep
    from lspr_imaging_app.plan.state import EvalPair
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _steps(count: int = 3) -> list[EffectiveStep]:
    return [
        EffectiveStep(
            step=i + 1, label=f"sol{i + 1}", start_unix_ms=i * 1000, end_unix_ms=(i + 1) * 1000, color=None, measured_label=f"sol{i + 1}",
            measured_start_unix_ms=i * 1000, measured_end_unix_ms=(i + 1) * 1000, order=i + 1,
        )
        for i in range(count)
    ]


class EvaluationMenuTest(unittest.TestCase):
    def setUp(self) -> None:
        self.host = QtWidgets.QWidget()
        self.controls = eg.EvaluationControls(self.host)
        self.steps = _steps()
        self.chosen: list[tuple] = []
        self.removed: list[int] = []
        self.controls.role_chosen.connect(lambda role, ref, pair: self.chosen.append((role, ref, pair)))
        self.controls.pair_removed.connect(self.removed.append)

    def tearDown(self) -> None:
        self.controls._menu.close()
        self.host.deleteLater()

    def _show(self, pairs=()) -> None:
        self.controls.set_steps(self.steps, None, pairs)

    def _names(self) -> list[str]:
        return [label.text() for label in self.controls._labels]

    # -- the ribbon: one icon -----------------------------------------------------------------------------------------------

    def test_the_group_is_one_icon_that_is_greyed_out_without_steps(self) -> None:
        self.assertEqual(len(self.controls.widgets()), 1)
        button = self.controls.widgets()[0]
        self.assertFalse(button.icon().isNull())
        self.assertFalse(self.controls._add.icon().isNull() or self.controls._remove.icon().isNull())
        self.assertFalse(button.isEnabled())
        self.assertEqual(button.toolTip(), eg.NO_STEPS_TIP)
        self._show()
        self.assertTrue(button.isEnabled())
        self.assertEqual(button.toolTip(), eg.BUTTON_TIP)
        self.controls.set_steps((), None, ())
        self.assertFalse(button.isEnabled())

    # -- the table ----------------------------------------------------------------------------------------------------------

    def test_one_pair_to_begin_with_the_role_in_column_one_and_the_picker_in_column_two(self) -> None:
        self._show()
        self.assertEqual(self._names(), ["B1", "S1"])
        grid = self.controls._grid
        for row, (label, picker) in enumerate(zip(self.controls._labels, self.controls._pickers)):
            self.assertEqual((grid.getItemPosition(grid.indexOf(label))[:2], grid.getItemPosition(grid.indexOf(picker))[:2]), ((row, 0), (row, 1)))
        picker = self.controls._picker(1, "baseline")
        self.assertEqual([picker.itemText(i) for i in range(picker.count())], ["not set", "1 · sol1", "2 · sol2", "3 · sol3"])

    def test_add_gives_the_next_pair_and_remove_takes_the_last_one(self) -> None:
        self._show()
        self.controls._add.click()
        self.controls._add.click()
        self.assertEqual(self._names(), ["B1", "S1", "B2", "S2", "B3", "S3"])
        self.assertEqual(self.controls.pair_count(), 3)
        self.assertEqual(len(self.controls._pickers), 6)
        self.controls._remove.click()
        self.assertEqual(self._names(), ["B1", "S1", "B2", "S2"])
        self.assertEqual(self.removed, [3])  # the owner was told which pair goes

    def test_the_first_pair_stays_and_there_is_a_top_limit(self) -> None:
        self._show()
        self.assertFalse(self.controls._remove.isEnabled())
        self.controls._remove.click()
        self.assertEqual((self.controls.pair_count(), self.removed), (1, []))
        for _ in range(eg.MAX_PAIRS + 3):
            self.controls._add.click()
        self.assertEqual(self.controls.pair_count(), eg.MAX_PAIRS)
        self.assertFalse(self.controls._add.isEnabled())
        self.assertEqual(self.controls._add.toolTip(), eg.ADD_FULL_TIP)
        self.assertTrue(self.controls._remove.isEnabled())

    def test_a_new_row_lists_the_steps_at_once(self) -> None:
        self._show()
        self.controls._add.click()
        picker = self.controls._picker(2, "signal")
        self.assertEqual(picker.count(), len(self.steps) + 1)
        self.assertEqual(picker.currentIndex(), 0)

    # -- picks and roles ----------------------------------------------------------------------------------------------------

    def test_a_pick_reports_role_step_and_pair_but_showing_roles_reports_nothing(self) -> None:
        refs = [step.ref for step in self.steps]
        self._show([EvalPair(1, baseline=refs[0], signal=refs[1])])
        self.assertEqual(self.chosen, [])
        self.assertEqual((self.controls.baseline_ref(), self.controls.signal_ref()), (refs[0], refs[1]))
        self.controls._add.click()
        picker = self.controls._picker(2, "baseline")
        picker.setCurrentIndex(3)
        picker.activated.emit(3)  # the user picked the third step for B2
        self.assertEqual(self.chosen, [("baseline", refs[2], 2)])
        picker.setCurrentIndex(0)
        picker.activated.emit(0)  # "not set"
        self.assertEqual(self.chosen[-1], ("baseline", None, 2))

    def test_a_pair_set_in_the_plan_is_always_shown_and_the_count_does_not_fall_by_itself(self) -> None:
        refs = [step.ref for step in self.steps]
        self._show([EvalPair(3, signal=refs[2])])  # a session, an undo or the right-click menu set pair 3
        self.assertEqual((self.controls.pair_count(), self._names()[-2:]), (3, ["B3", "S3"]))
        self.assertEqual(self.controls.signal_ref(3), refs[2])
        self._show()  # the role is gone again: the rows stay until the user removes them
        self.assertEqual(self.controls.pair_count(), 3)
        self.assertEqual(self.controls.signal_ref(3), None)

    def test_a_pair_set_past_the_limit_is_shown_up_to_the_limit(self) -> None:
        self._show([EvalPair(eg.MAX_PAIRS + 2, baseline=self.steps[0].ref)])
        self.assertEqual(self.controls.pair_count(), eg.MAX_PAIRS)

    # -- remembered ---------------------------------------------------------------------------------------------------------

    def test_the_number_of_pairs_is_remembered(self) -> None:
        store = UiStateStore({})
        self.controls.restore_ui_state(store)
        self._show()
        self.controls._add.click()
        self.controls._add.click()
        self.assertEqual(store.get("sensorgram/eval_pairs"), 3)
        self.controls._remove.click()
        self.assertEqual(store.get("sensorgram/eval_pairs"), 2)
        other = eg.EvaluationControls(self.host)
        other.restore_ui_state(UiStateStore({"sensorgram/eval_pairs": 4}))
        self.assertEqual(other.pair_count(), 4)
        self.assertEqual(len(other._pickers), 8)

    def test_a_remembered_count_that_makes_no_sense_falls_back(self) -> None:
        for saved, expected in ((0, 1), (-3, 1), (99, eg.MAX_PAIRS), ("many", 1), (True, 1)):
            other = eg.EvaluationControls(self.host)
            other.restore_ui_state(UiStateStore({"sensorgram/eval_pairs": saved}))
            self.assertEqual(other.pair_count(), expected, saved)

    # -- the open pop-up ----------------------------------------------------------------------------------------------------

    def test_an_open_popup_grows_when_a_pair_is_added_and_shrinks_when_it_is_removed(self) -> None:
        self._show()
        menu, panel = self.controls._menu, self.controls._panel
        menu.popup(QPoint(40, 40))
        _APP.processEvents()
        one = menu.height()
        self.assertGreaterEqual(menu.height(), panel.height())
        self.controls._add.click()
        _APP.processEvents()
        two = menu.height()
        self.assertGreater(two, one)
        self.assertGreaterEqual(menu.height(), panel.height())  # nothing is cut off at the bottom
        self.assertTrue(all(p.isVisible() for p in self.controls._pickers))  # the new rows were shown
        self.controls._remove.click()
        _APP.processEvents()
        self.assertEqual(menu.height(), one)


if __name__ == "__main__":
    unittest.main()
