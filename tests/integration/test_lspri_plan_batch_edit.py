"""The Sensorgram's "Batch edit" group (2026-11-26): shrink or offset many plan steps, with a live preview on the plan bar.

Three layers: `ExperimentPlanModule.plan_batch` / `apply_batch` (a fake dataset; one undo step), the two pop-ups (`BatchEditControls`: what they emit, what they remember) and the real
Sensorgram panel with a real scan (preview on the bar while a pop-up is open, the tick applies, selection kept). The arithmetic is in `tests/unit/test_lspri_plan_batch_edit.py`.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from PyQt6.QtCore import QObject, pyqtSignal

    from lspr_core import ImagingAcquisitionMetadata, ImagingPlan, ImagingPlanStep
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.panels.image.analysis_tab import AnalysisTab
    from lspr_imaging_app.panels.sensorgram.batch_group import BatchEditControls
    from lspr_imaging_app.panels.sensorgram.panel import SensorgramPanel
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.plan import batch_edit as be
    from lspr_imaging_app.selection.cube_display_module import MODE_CUBE
    from lspr_imaging_app.selection.experiment_plan_module import ExperimentPlanModule
    from lspr_imaging_app.undo import undo_manager
    from tests.integration.test_lspri_plan_bar import CENTERS, CUBE_MS, T0, _pump, _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class _FakeDataset(QObject):
    dataset_loaded = pyqtSignal(object)
    dataset_cleared = pyqtSignal()

    def __init__(self, metadata) -> None:
        super().__init__()
        self.metadata = metadata

    def acquisition_metadata(self):
        return self.metadata

    def cube_start_unix_ms(self, cube_index):
        return 2_000 * int(cube_index)

    def data_end_unix_ms(self):
        return 81_000


class ModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        plan = ImagingPlan(steps=[
            ImagingPlanStep(step=1, label="A", start_unix_ms=10_000, end_unix_ms=30_000),
            ImagingPlanStep(step=2, label="B", start_unix_ms=30_000, end_unix_ms=50_000),
        ])
        self.module = ExperimentPlanModule(_FakeDataset(ImagingAcquisitionMetadata(source_format="test", plan=plan)))
        self.grid = self.module.snap_grid_unix_ms(list(range(41)))
        self.reasons: list[str] = []
        self.notices: list[str] = []
        self.module.steps_changed.connect(self.reasons.append)
        self.module.notice.connect(self.notices.append)

    def tearDown(self) -> None:
        undo_manager.clear()

    def _bounds(self):
        return [(s.start_unix_ms, s.end_unix_ms) for s in self.module.steps()]

    def test_the_preview_changes_nothing(self) -> None:
        plan = self.module.plan_batch(be.BatchSpec(be.KIND_SHRINK, be.UNIT_CUBES, 2), (0, 1), self.grid)
        self.assertEqual(len(plan.changes), 2)
        self.assertEqual(self._bounds(), [(10_000, 30_000), (30_000, 50_000)])
        self.assertEqual((self.reasons, self.notices, undo_manager.can_undo), ([], [], False))

    def test_apply_is_one_undo_step_that_says_what_it_did(self) -> None:
        plan = self.module.apply_batch(be.BatchSpec(be.KIND_SHRINK, be.UNIT_CUBES, 2), (0, 1), self.grid)
        self.assertEqual(len(plan.changes), 2)
        self.assertEqual(self._bounds(), [(12_000, 28_000), (32_000, 48_000)])  # a gap opened between the two steps
        self.assertEqual(self.reasons, ["boundary"])  # what the views redraw on
        self.assertEqual(self.notices, ["2 steps shrunk."])
        undo_manager.undo()
        self.assertEqual(self._bounds(), [(10_000, 30_000), (30_000, 50_000)])
        self.assertFalse(undo_manager.can_undo)  # the whole edit was one entry

    def test_an_edit_that_changes_nothing_pushes_no_undo_step_but_says_so(self) -> None:
        self.module.apply_batch(be.BatchSpec(be.KIND_SHRINK, be.UNIT_TIME, 1), (0, 1), self.grid)  # under half a cube
        self.assertEqual((self.reasons, undo_manager.can_undo), ([], False))
        self.assertIn("No step shrunk", self.notices[0])

    def test_an_offset_can_overlap_and_is_undone_as_one_step(self) -> None:
        self.module.apply_batch(be.BatchSpec(be.KIND_OFFSET, be.UNIT_TIME, 10), (0,), self.grid)
        self.assertEqual(self._bounds(), [(20_000, 40_000), (30_000, 50_000)])
        undo_manager.undo()
        self.assertEqual(self._bounds(), [(10_000, 30_000), (30_000, 50_000)])


class ControlsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.host = QtWidgets.QWidget()
        self.controls = BatchEditControls(self.host)
        self.controls.set_state(True, "All 2 steps")
        self.previews: list = []
        self.cleared = 0
        self.applied: list = []
        self.controls.preview_requested.connect(self.previews.append)
        self.controls.preview_cleared.connect(lambda: setattr(self, "cleared", self.cleared + 1))
        self.controls.apply_requested.connect(self.applied.append)

    def tearDown(self) -> None:
        self.host.deleteLater()

    def test_the_buttons_follow_what_is_available(self) -> None:
        self.assertEqual([w.isEnabled() for w in self.controls.widgets()], [True, True])
        self.controls.set_state(False, "")
        self.assertEqual([w.isEnabled() for w in self.controls.widgets()], [False, False])
        self.controls.set_state(True, "All 2 steps", has_axis=False)  # no traces: an edge has no cubes to land on
        self.assertEqual([w.isEnabled() for w in self.controls.widgets()], [False, False])

    def test_opening_a_pop_up_asks_for_a_preview_and_closing_it_clears_it(self) -> None:
        menu = self.controls.menu(be.KIND_SHRINK)
        menu.aboutToShow.emit()
        self.assertEqual(self.previews[-1], be.BatchSpec(be.KIND_SHRINK, be.UNIT_CUBES, 1.0, be.SIDE_BOTH))
        menu.aboutToHide.emit()
        self.assertEqual(self.cleared, 1)

    def test_a_changed_setting_asks_for_a_new_preview(self) -> None:
        panel = self.controls.panel(be.KIND_SHRINK)
        panel._unit_buttons[be.UNIT_TIME].click()
        self.assertEqual((self.previews[-1].unit, panel._box.decimals(), panel._box.suffix()), (be.UNIT_TIME, 1, " s"))
        panel._side_buttons[be.SIDE_END].click()
        self.assertEqual(self.previews[-1].side, be.SIDE_END)
        panel._box.setValue(7.5)
        self.assertEqual(self.previews[-1].amount, 7.5)

    def test_each_unit_keeps_its_own_amount(self) -> None:
        panel = self.controls.panel(be.KIND_SHRINK)
        panel._box.setValue(3)
        panel._unit_buttons[be.UNIT_PERCENT].click()
        panel._box.setValue(25)
        panel._unit_buttons[be.UNIT_CUBES].click()
        self.assertEqual(panel.spec().amount, 3.0)
        panel._unit_buttons[be.UNIT_PERCENT].click()
        self.assertEqual(panel.spec().amount, 25.0)

    def test_an_offset_has_no_side_and_no_percentage_and_takes_negative_amounts(self) -> None:
        panel = self.controls.panel(be.KIND_OFFSET)
        self.assertEqual((panel._side_buttons, sorted(panel._unit_buttons)), ({}, [be.UNIT_CUBES, be.UNIT_TIME]))
        panel._box.setValue(-3)
        self.assertEqual(panel.spec(), be.BatchSpec(be.KIND_OFFSET, be.UNIT_CUBES, -3.0, be.SIDE_BOTH))

    def test_the_tick_applies_and_the_cross_does_not(self) -> None:
        menu = self.controls.menu(be.KIND_OFFSET)
        panel = self.controls.panel(be.KIND_OFFSET)
        panel.cancel_button.click()
        self.assertEqual(self.applied, [])
        panel.apply_button.click()
        self.assertEqual(self.applied, [be.BatchSpec(be.KIND_OFFSET, be.UNIT_CUBES, 1.0, be.SIDE_BOTH)])
        self.assertFalse(menu.isVisible())

    def test_the_status_line_can_switch_the_tick_off(self) -> None:
        self.controls.set_status(be.KIND_SHRINK, "No step would shrink.", problem=True, can_apply=False)
        self.assertFalse(self.controls.panel(be.KIND_SHRINK).apply_button.isEnabled())
        self.assertEqual(self.controls.panel(be.KIND_SHRINK)._status.text(), "No step would shrink.")

    def test_settings_are_remembered(self) -> None:
        store = UiStateStore({})
        self.controls.restore_ui_state(store)
        panel = self.controls.panel(be.KIND_SHRINK)
        panel._unit_buttons[be.UNIT_PERCENT].click()
        panel._box.setValue(15)
        panel._side_buttons[be.SIDE_START].click()
        other = BatchEditControls(self.host)
        other.restore_ui_state(store)
        spec = other.panel(be.KIND_SHRINK).spec()
        self.assertEqual((spec.unit, spec.amount, spec.side), (be.UNIT_PERCENT, 15.0, be.SIDE_START))


class PanelTest(unittest.TestCase):
    """The real panel with a real scan: steps 1 (cubes 0 - 2) and 2 (cubes 3 - 5), a cube every 3 s."""

    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.m = app_rewrite._build_modules()
        self.m.session_autosave._suspended = True
        self.workflow = AnalysisTab()
        app_rewrite._wire_analysis_scan(self.m, self.workflow, UiStateStore({}))
        self.m.scan_results._timer.setInterval(30)
        self.m.cube_display.set_mode(MODE_CUBE)
        self.panel = SensorgramPanel(
            self.m.scan_results, self.m.roi_toolbox, self.m.dataset, self.m.selection, self.m.analysis_settings, self.m.cube_display,
            trace_in_process=False, mask=self.m.mask, experiment_plan=self.m.experiment_plan,
        )
        self.panel.resize(900, 420)
        self.panel.show()
        self.finished: list[tuple] = []
        self.m.analysis_scan.task_finished.connect(lambda *args: self.finished.append(args))
        self.m.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        for cx, cy, _c0 in CENTERS.values():
            self.m.roi_toolbox.add_roi(cx, cy, sample_diameter_px=6.0)
        self.workflow.run_button.click()
        self.assertTrue(_pump(lambda: bool(self.finished)))
        self.assertTrue(_pump(lambda: self.m.scan_results.stored() is not None))
        self.m.selection.set_roi_selection({1, 2})
        self.assertTrue(_pump(lambda: self.panel._last is not None and self.panel._job is None), "the panel never drew")
        self.bar = self.panel._plot.plan_bar()
        self.controls = self.panel._steps.batch_controls
        undo_manager.clear()

    def tearDown(self) -> None:
        self.panel.shutdown()
        self.m.analysis_scan.shutdown()
        self.m.scan_results.shutdown()
        self.panel.deleteLater()
        self.workflow.deleteLater()
        self._tmp.cleanup()
        undo_manager.clear()

    def _starts(self) -> list[int]:
        return [s.start_unix_ms - T0 for s in self.m.experiment_plan.steps()]

    def test_the_group_is_on_the_experimental_plan_tab_and_enabled_with_traces(self) -> None:
        self.assertEqual([w.isEnabled() for w in self.controls.widgets()], [True, True])
        self.assertIn(self.controls.shrink_button(), self.panel._plan_ribbon.findChildren(type(self.controls.shrink_button())))

    def test_with_no_step_selected_the_preview_covers_every_step(self) -> None:
        menu = self.controls.menu(be.KIND_SHRINK)
        menu.aboutToShow.emit()
        self.assertEqual(len(self.bar.preview()), 2)
        self.assertEqual(self.controls.panel(be.KIND_SHRINK)._target.text(), "All 2 steps")
        self.assertIn("2 steps will shrink", self.controls.panel(be.KIND_SHRINK)._status.text())
        self.assertEqual(self._starts(), [0, 3 * CUBE_MS])  # the preview changed nothing
        menu.aboutToHide.emit()
        self.assertEqual(self.bar.preview(), ())

    def test_with_a_selection_only_the_selected_steps_are_previewed(self) -> None:
        self.bar.select(1)
        menu = self.controls.menu(be.KIND_SHRINK)
        menu.aboutToShow.emit()
        self.assertEqual(len(self.bar.preview()), 1)
        self.assertEqual(self.controls.panel(be.KIND_SHRINK)._target.text(), "1 selected step")
        menu.aboutToHide.emit()

    def test_changing_a_setting_while_open_redraws_the_preview(self) -> None:
        menu = self.controls.menu(be.KIND_SHRINK)
        menu.aboutToShow.emit()
        first = self.bar.preview()
        self.controls.panel(be.KIND_SHRINK)._box.setValue(2)
        self.assertNotEqual(self.bar.preview(), first)
        menu.aboutToHide.emit()

    def test_the_tick_applies_one_undo_step_and_keeps_the_selection(self) -> None:
        self.bar.select(1)
        panel = self.controls.panel(be.KIND_SHRINK)
        panel._side_buttons[be.SIDE_START].click()
        self.controls.menu(be.KIND_SHRINK).aboutToShow.emit()
        panel.apply_button.click()
        self.assertEqual(self._starts(), [0, 4 * CUBE_MS])  # step 2 lost one cube at its start; step 1 was not selected
        self.assertEqual(self.bar.selected_indices(), (1,))
        self.assertEqual(self.bar.preview(), ())
        undo_manager.undo()
        self.assertEqual(self._starts(), [0, 3 * CUBE_MS])
        self.assertFalse(undo_manager.can_undo)

    def test_an_offset_moves_what_can_move_and_the_bar_follows(self) -> None:
        # the data has 6 cubes and step 2 ends where the data ends: one cube later is outside the data, so step 2 stays; step 1 moves and now overlaps it
        panel = self.controls.panel(be.KIND_OFFSET)
        panel._box.setValue(1)
        self.controls.menu(be.KIND_OFFSET).aboutToShow.emit()
        self.assertIn("1 step would leave the data", panel._status.text())
        panel.apply_button.click()
        self.assertEqual(self._starts(), [CUBE_MS, 3 * CUBE_MS])
        steps = self.bar.steps()
        self.assertEqual([round(s.x_start, 6) for s in steps], [1.0, 3.0])
        self.assertEqual(round(steps[0].x_end, 6), 4.0)  # step 1 ends at cube 4, step 2 starts at cube 3: they overlap, which is allowed


if __name__ == "__main__":
    unittest.main()
