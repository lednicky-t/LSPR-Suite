"""Image panel > Tuning ribbon tab (ROI position tuning, step D1; 2026-10-29): the real buttons, the real `RoiTuningModule` and a small real dataset.

Driven through `QToolButton.click()` and the controls' own setters, never by screen coordinate. The window is not shown: the modules of the app are built
(`app_rewrite._build_modules`), a `TuningTab` is made and `_wire_roi_tuning` connects it, exactly as `build_main_window` does. Part one is the tab alone (what its
controls emit and when they are enabled), part two is the wiring (remembered controls reach the module, a refusal reaches the status bar, a whole trial through the
buttons, the export, Clear, out of date). Design: docs/roi_position_tuning_plan_2026-10-28.md (step D1).
"""

from __future__ import annotations

import functools
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import tifffile
from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.analysis import roi_tuning_module as tm
    from lspr_imaging_app.analysis.roi_tuning_objectives import DEFAULT_OBJECTIVE_KEY, OBJECTIVES
    from lspr_imaging_app.analysis.settings import MetricSettings
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.panels.image.tuning_tab import TuningTab
    from lspr_imaging_app.panels.ui_state import UiStateStore
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
H, W = 64, 80
CUBES = 6


def _pump(until, seconds: float = 90.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if until():
            return True
        time.sleep(0.005)
    return bool(until())


class TabAloneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tab = TuningTab()
        self.seen: list[tuple] = []
        for name in ("run_requested", "stop_requested", "lattice_changed", "objective_changed", "crosses_toggled", "interpolate_toggled", "best_toggled",
                     "accept_requested", "clear_requested", "export_requested", "status_message"):
            getattr(self.tab, name).connect(lambda *args, name=name: self.seen.append((name, *args)))

    def tearDown(self) -> None:
        self.tab.deleteLater()

    def test_the_defaults_are_the_normal_lattice_and_the_smoothed_snr(self) -> None:
        self.assertEqual((self.tab.lattice_size(), self.tab.pitch_px(), self.tab.objective_key()), (5, 1.0, DEFAULT_OBJECTIVE_KEY))
        self.assertTrue(self.tab.crosses_button.isChecked())
        self.assertFalse(self.tab.best_button.isChecked())
        self.assertEqual([self.tab.size_combo.itemData(i) for i in range(self.tab.size_combo.count())], list(tm.LATTICE_SIZES))

    def test_the_rank_by_menu_lists_every_objective_with_its_tooltip(self) -> None:
        combo = self.tab.objective_combo
        self.assertEqual([combo.itemData(i) for i in range(combo.count())], [o.key for o in OBJECTIVES])
        for i, objective in enumerate(OBJECTIVES):
            self.assertEqual(combo.itemText(i), objective.label)
            self.assertEqual(combo.itemData(i, Qt.ItemDataRole.ToolTipRole), objective.tooltip)

    def test_try_asks_for_a_trial_and_while_one_runs_the_same_button_stops_it(self) -> None:
        self.tab.run_button.click()
        self.assertEqual(self.seen, [("run_requested",)])
        self.seen.clear()
        self.tab.set_state(tm.RUNNING, "")
        self.tab.run_button.click()
        self.assertEqual(self.seen, [("stop_requested",)])
        self.assertTrue(self.tab.is_running())

    def test_try_is_never_greyed_out_so_that_it_can_say_why_it_cannot(self) -> None:
        self.tab.set_state(tm.IDLE, "Select at least one ROI first.")
        self.assertTrue(self.tab.run_button.isEnabled())
        self.assertIn("Select at least one ROI first.", self.tab.run_button.toolTip())

    def test_the_lattice_cannot_be_changed_under_a_running_trial(self) -> None:
        self.tab.set_state(tm.RUNNING, "")
        self.assertFalse(self.tab.size_combo.isEnabled())
        self.assertFalse(self.tab.pitch_spin.isEnabled())
        self.tab.set_state(tm.IDLE, "")
        self.assertTrue(self.tab.size_combo.isEnabled() and self.tab.pitch_spin.isEnabled())

    def test_editing_the_lattice_announces_size_and_pitch(self) -> None:
        self.tab.size_combo.setCurrentIndex(self.tab.size_combo.findData(7))
        self.tab.pitch_spin.setValue(2.0)
        self.assertEqual(self.seen, [("lattice_changed", 7, 1.0), ("lattice_changed", 7, 2.0)])

    def test_changing_rank_by_announces_the_key(self) -> None:
        self.tab.objective_combo.setCurrentIndex(self.tab.objective_combo.findData("signal"))
        self.assertEqual(self.seen, [("objective_changed", "signal")])

    def test_the_view_buttons_clear_and_export_wait_for_a_result(self) -> None:
        buttons = (self.tab.best_button, self.tab.clear_button, self.tab.export_button)
        for state in (tm.IDLE, tm.RUNNING):
            self.tab.set_state(state, "")
            self.assertFalse(any(b.isEnabled() for b in buttons), state)
        for state in (tm.READY, tm.STALE):
            self.tab.set_state(state, "")
            self.assertTrue(all(b.isEnabled() for b in buttons), state)

    def test_everything_is_one_group_with_four_icons_and_the_rest_is_in_the_settings_pop_up(self) -> None:
        from PyQt6.QtWidgets import QLabel, QToolButton

        captions = [label.text() for label in self.tab.findChildren(QLabel) if label.parent() is not None and label.parent().parent() is self.tab]
        self.assertEqual(captions, ["Position tuning"])  # one caption for the whole row
        popup = self.tab.settings_button.menu()
        for inner in (
            self.tab.size_combo, self.tab.pitch_spin, self.tab.objective_combo, self.tab.crosses_button, self.tab.interpolate_button, self.tab.clear_button,
            self.tab.export_button,
        ):
            self.assertTrue(popup.isAncestorOf(inner), inner)
        row = [b for b in self.tab.findChildren(QToolButton) if not popup.isAncestorOf(b)]
        self.assertEqual({id(b) for b in row}, {id(self.tab.run_button), id(self.tab.settings_button), id(self.tab.best_button), id(self.tab.accept_button)})

    def test_the_settings_pop_up_is_always_reachable_and_the_crosses_are_on_by_default_and_switchable_without_a_result(self) -> None:
        for state in (tm.IDLE, tm.RUNNING, tm.READY, tm.STALE):
            self.tab.set_state(state, "")
            self.assertTrue(self.tab.settings_button.isEnabled(), state)
            self.assertTrue(self.tab.crosses_button.isEnabled(), state)  # a view setting, usable before the first trial
        self.assertTrue(self.tab.crosses_button.isChecked())
        self.assertFalse(self.tab.settings_button.icon().isNull())

    def test_interpolation_is_a_switch_in_the_settings_pop_up_that_is_off_by_default_and_always_usable(self) -> None:
        self.assertFalse(self.tab.interpolate_button.isChecked())
        for state in (tm.IDLE, tm.RUNNING, tm.READY, tm.STALE):
            self.tab.set_state(state, "")
            self.assertTrue(self.tab.interpolate_button.isEnabled(), state)  # a setting of how a result is read: usable before the first trial
        self.tab.interpolate_button.click()
        self.tab.interpolate_button.click()
        self.assertEqual(self.seen, [("interpolate_toggled", True), ("interpolate_toggled", False)])
        self.assertIn("Interpolate", self.tab.interpolate_button.toolTip())
        self.assertFalse(self.tab.interpolate_button.icon().isNull())

    def test_the_run_button_is_named_for_what_it_does(self) -> None:
        self.assertTrue(self.tab.run_button.toolTip().startswith("Run tuning"))

    def test_accept_is_live_only_for_a_current_result_in_which_a_roi_would_move(self) -> None:
        for state, can_accept, expected in (
            (tm.IDLE, False, False), (tm.RUNNING, True, False), (tm.READY, False, False), (tm.READY, True, True), (tm.STALE, True, False),
        ):
            self.tab.set_state(state, "", can_accept)
            self.assertEqual(self.tab.accept_button.isEnabled(), expected, (state, can_accept))
        self.seen.clear()
        self.tab.set_state(tm.READY, "", True)
        self.tab.accept_button.click()
        self.assertEqual(self.seen, [("accept_requested",)])

    def test_the_view_buttons_clear_and_export_emit_their_requests(self) -> None:
        self.tab.set_state(tm.READY, "")
        self.tab.crosses_button.click()
        self.tab.best_button.click()
        self.tab.clear_button.click()
        self.tab.export_button.click()
        self.assertEqual(self.seen, [("crosses_toggled", False), ("best_toggled", True), ("clear_requested",), ("export_requested",)])

    def test_the_text_of_the_last_trial_is_in_the_tooltip_the_info_text_and_on_demand_the_status_bar(self) -> None:
        self.tab.set_state(tm.READY, "25 positions x 3 ROIs x 8 cubes.")
        self.tab.set_status("2 of 3 ROIs would move <by up to 1.4 px>.", announce=True)
        self.assertIn("2 of 3 ROIs would move", self.tab.run_button.toolTip())
        self.assertIn("25 positions x 3 ROIs x 8 cubes.", self.tab.info_html())
        self.assertIn("&lt;by up to 1.4 px&gt;", self.tab.info_html())  # the text is escaped, never read as markup
        self.assertEqual(self.seen, [("status_message", "2 of 3 ROIs would move <by up to 1.4 px>.")])
        self.seen.clear()
        self.tab.set_status("quiet")
        self.assertEqual(self.seen, [])

    def test_a_theme_switch_redraws_without_losing_the_state(self) -> None:
        self.tab.set_state(tm.READY, "")
        self.tab.best_button.setChecked(True)
        self.tab.refresh_theme()
        self.assertFalse(self.tab.run_button.icon().isNull() or self.tab.best_button.icon().isNull())
        self.assertTrue(self.tab.best_button.isChecked())
        self.assertTrue(self.tab.clear_button.isEnabled())


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    rng = np.random.default_rng(3)
    base = rng.uniform(900.0, 1100.0, size=(H, W)).astype(np.float32)
    for cube in range(CUBES):
        for wavelength in (500.0, 520.0, 540.0, 560.0, 580.0):
            path = root / f"cube{cube}_wl{int(wavelength)}.tif"
            tifffile.imwrite(str(path), base + cube * 40.0 + (wavelength - 500.0) * 0.5 + rng.normal(0, 5.0, (H, W)).astype(np.float32))
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


class WiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.exports: list[Path] = []
        self.export_path: Path | None = self.root / "tuning.csv"
        self.status: list[str] = []

    def tearDown(self) -> None:
        if hasattr(self, "m"):
            self.m.roi_tuning.task.shutdown()
            self.tab.deleteLater()
        self._tmp.cleanup()

    def _wire(self, saved: dict | None = None) -> None:
        self.m = app_rewrite._build_modules()
        # The app's module fits in a helper process; here they run in the task thread, and a trial runs in this process (a fixed count).
        self.m.roi_tuning.task.set_helper_process(False)
        self.m.roi_tuning.run = functools.partial(tm.RoiTuningModule.run, self.m.roi_tuning, processes=1)
        self.m.analysis_settings.set_metric_settings(MetricSettings(fit_method="none"))
        self.tab = TuningTab()
        self.tab.status_message.connect(self.status.append)
        self.ui_state = UiStateStore(dict(saved or {}))
        self.views: list = []
        app_rewrite._wire_roi_tuning(
            self.m, self.tab, self.ui_state, show_view=self.views.append, choose_export_path=lambda _parent, default: self.export_path
        )

    def _open_dataset_with_plan(self) -> None:
        self.m.dataset.load_dataset(_write_dataset(self.root))
        self.m.roi_toolbox.add_roi(20.0, 30.0, sample_diameter_px=6.0)
        self.m.roi_toolbox.add_roi(45.0, 30.0, sample_diameter_px=6.0)
        self.m.roi_toolbox.add_roi(65.0, 30.0, sample_diameter_px=6.0)
        baseline, signal = SimpleNamespace(ref=("a", 1), order=1), SimpleNamespace(ref=("a", 2), order=2)
        plan = self.m.experiment_plan
        plan.baseline_step = lambda pair=1: baseline  # the plan machinery has its own tests; here the tab needs two steps with cubes
        plan.signal_step = lambda pair=1: signal
        plan.cubes_of = lambda step: (0, 1, 2) if step is baseline else (3, 4, 5)
        self.m.selection.set_roi_selection({1, 2, 3})  # last: the tab's tooltip is refreshed by the selection

    def _try_to_ready(self) -> None:
        self.tab.run_button.click()
        self.assertTrue(_pump(lambda: self.tab.state() in (tm.READY, tm.IDLE) and not self.m.roi_tuning.task.is_running() and self.m.roi_tuning.state() != tm.RUNNING))
        _APP.processEvents()

    # -- remembered controls --------------------------------------------------------------------------------------------------

    def test_remembered_controls_come_back_and_reach_the_module(self) -> None:
        self._wire({
            "tuning/lattice_size": 7, "tuning/pitch_px": 2.0, "tuning/objective": "signal", "tuning/show_crosses": False, "tuning/show_best": True,
        })
        self.assertEqual((self.tab.lattice_size(), self.tab.pitch_px(), self.tab.objective_key()), (7, 2.0, "signal"))
        lattice = self.m.roi_tuning.lattice()
        self.assertEqual((lattice.size, lattice.pitch_px), (7, 2.0))
        self.assertEqual(self.m.roi_tuning.objective_key(), "signal")
        self.assertEqual((self.m.roi_tuning.show_crosses(), self.m.roi_tuning.show_best()), (False, True))

    def test_interpolation_is_remembered_and_reaches_the_module(self) -> None:
        self._wire({"tuning/interpolate": True})
        self.assertTrue(self.tab.interpolate_button.isChecked())
        self.assertTrue(self.m.roi_tuning.interpolate())
        self.tab.interpolate_button.click()
        self.assertFalse(self.m.roi_tuning.interpolate())
        self.assertIs(self.ui_state.get("tuning/interpolate"), False)

    def test_interpolation_is_off_when_nothing_is_remembered(self) -> None:
        self._wire()
        self.assertFalse(self.tab.interpolate_button.isChecked())
        self.assertFalse(self.m.roi_tuning.interpolate())

    def test_a_saved_value_that_is_not_valid_is_ignored(self) -> None:
        self._wire({"tuning/lattice_size": 4, "tuning/objective": "turbo", "tuning/pitch_px": 999.0})
        self.assertEqual((self.tab.lattice_size(), self.tab.pitch_px(), self.tab.objective_key()), (5, 1.0, DEFAULT_OBJECTIVE_KEY))

    def test_changing_a_control_reaches_the_module_and_is_remembered(self) -> None:
        self._wire()
        self.tab.size_combo.setCurrentIndex(self.tab.size_combo.findData(3))
        self.tab.pitch_spin.setValue(0.5)
        self.tab.objective_combo.setCurrentIndex(self.tab.objective_combo.findData("noise"))
        self.assertEqual((self.m.roi_tuning.lattice().size, self.m.roi_tuning.lattice().pitch_px), (3, 0.5))
        self.assertEqual(self.m.roi_tuning.objective_key(), "noise")
        self.assertEqual(
            (self.ui_state.get("tuning/lattice_size"), self.ui_state.get("tuning/pitch_px"), self.ui_state.get("tuning/objective")), (3, 0.5, "noise")
        )
        self.tab.set_state(tm.READY, "")  # the view buttons are live only with a result
        self.tab.best_button.click()
        self.assertTrue(self.m.roi_tuning.show_best())
        self.assertIs(self.ui_state.get("tuning/show_best"), True)

    # -- Try ------------------------------------------------------------------------------------------------------------------

    def test_try_without_a_selection_says_why_on_the_status_bar(self) -> None:
        self._wire()
        self.tab.run_button.click()
        self.assertEqual(self.status[-1], "Select at least one ROI first.")
        self.assertEqual(self.m.roi_tuning.state(), tm.IDLE)

    def test_try_without_the_two_plan_steps_says_so(self) -> None:
        self._wire()
        self.m.dataset.load_dataset(_write_dataset(self.root))
        self.m.roi_toolbox.add_roi(20.0, 30.0, sample_diameter_px=6.0)
        self.m.selection.set_roi_selection({1})
        self.tab.run_button.click()
        self.assertIn("baseline step (B1) and a signal step (S1)", self.status[-1])

    def test_the_try_tooltip_follows_the_selection(self) -> None:
        self._wire()
        self._open_dataset_with_plan()
        self.assertIn("25 positions x 3 ROIs x 6 cubes", self.tab.run_button.toolTip())
        self.m.selection.set_roi_selection({1})
        self.assertIn("25 positions x 1 ROIs x 6 cubes", self.tab.run_button.toolTip())
        self.m.selection.set_roi_selection(set())
        self.assertIn("Select at least one ROI first.", self.tab.run_button.toolTip())

    def test_try_is_refused_while_a_scan_is_running(self) -> None:
        self._wire()
        self._open_dataset_with_plan()
        self.m.analysis_scan.is_running = lambda: True
        self.tab.run_button.click()
        self.assertIn("An analysis scan is running", self.status[-1])
        self.assertEqual(self.m.roi_tuning.state(), tm.IDLE)

    # -- a whole trial through the buttons ------------------------------------------------------------------------------------

    def test_a_trial_through_the_buttons_then_export_and_clear(self) -> None:
        self._wire()
        self.m.roi_tuning.set_lattice(3, 1.0)
        self._open_dataset_with_plan()
        self.tab.size_combo.setCurrentIndex(self.tab.size_combo.findData(3))
        self.tab.run_button.click()
        self.assertTrue(self.tab.is_running())
        self.assertEqual(self.tab.run_button.toolTip(), "Stop the trial. Nothing is kept from a stopped trial.")
        self.assertTrue(_pump(lambda: self.m.roi_tuning.state() == tm.READY), self.m.roi_tuning.message())
        _APP.processEvents()
        self.assertEqual(self.tab.state(), tm.READY)
        self.assertTrue(self.tab.clear_button.isEnabled() and self.tab.export_button.isEnabled())
        self.assertEqual(self.status[-1], self.m.roi_tuning.message())  # the outcome was announced
        self.assertEqual(self.m.roi_tuning.result().matrix.mean.shape[:3], (9, 6, 3))
        self.assertEqual(self.tab.status_text(), self.m.roi_tuning.message())

        self.tab.export_button.click()
        self.assertTrue(_pump(lambda: self.export_path.exists() and any("Wrote" in s for s in self.status)))
        self.assertEqual(len(self.export_path.read_text(encoding="utf-8").splitlines()), 1 + 9 * 3)

        self.tab.clear_button.click()
        self.assertEqual((self.m.roi_tuning.state(), self.tab.state()), (tm.IDLE, tm.IDLE))
        self.assertFalse(self.tab.clear_button.isEnabled())

    def test_the_image_panel_is_handed_the_drawing_data_and_every_change_of_it(self) -> None:
        self._wire()
        self.assertEqual(self.views, [None])  # at start: nothing to draw
        self.m.roi_tuning.set_lattice(3, 1.0)
        self._open_dataset_with_plan()
        self.tab.run_button.click()
        self.assertTrue(_pump(lambda: self.m.roi_tuning.state() == tm.READY))
        view = self.views[-1]
        self.assertEqual((view.roi_ids, view.show_crosses, view.show_best, view.stale), ((1, 2, 3), True, False, False))
        self.assertEqual(view.scores.shape, (9, 3))
        self.tab.best_button.click()  # the star: the ROIs at their best centres
        self.assertTrue(self.views[-1].show_best)
        self.assertGreater(self.views[-1].revision, view.revision)
        self.tab.crosses_button.click()
        self.assertFalse(self.views[-1].show_crosses)
        self.m.roi_toolbox.add_roi(30.0, 50.0, sample_diameter_px=6.0)  # out of date
        _APP.processEvents()
        self.assertTrue(self.views[-1].stale)
        self.tab.clear_button.click()
        self.assertIsNone(self.views[-1])

    def test_accept_through_the_button_moves_the_rois_in_one_undo_step(self) -> None:
        from tests.integration.test_lspri_roi_tuning_accept import ranking_for
        from lspr_imaging_app.undo import undo_manager

        self._wire()
        self.m.roi_tuning.set_lattice(3, 2.0)
        self._open_dataset_with_plan()
        self.assertFalse(self.tab.accept_button.isEnabled())
        self.tab.run_button.click()
        self.assertTrue(_pump(lambda: self.m.roi_tuning.state() == tm.READY))
        self.m.roi_tuning._ranking = ranking_for(self.m.roi_tuning.result().matrix, [8, 4, 0])  # ROI 1 to (+2,+2), ROI 3 to (-2,-2): the metric of this dataset is arbitrary
        self.m.roi_tuning.state_changed.emit()
        self.assertTrue(self.tab.accept_button.isEnabled())
        before = [(r.center_x, r.center_y) for r in self.m.roi_toolbox.rois()]
        undo_manager.clear()
        self.tab.accept_button.click()
        after = [(r.center_x, r.center_y) for r in self.m.roi_toolbox.rois()]
        self.assertEqual(after[0], (before[0][0] + 2.0, before[0][1] + 2.0))
        self.assertEqual(after[1], before[1])
        self.assertEqual(after[2], (before[2][0] - 2.0, before[2][1] - 2.0))
        self.assertTrue(_pump(lambda: self.tab.state() == tm.IDLE and not self.m.roi_tuning.task.is_running()))
        self.assertTrue(any("Moved 2 ROIs" in line for line in self.status), self.status)  # announced on the status bar
        self.assertEqual(self.views[-1], None)  # the crosses are gone: the trial is spent
        self.assertFalse(self.tab.accept_button.isEnabled())
        undo_manager.undo()
        self.assertEqual([(r.center_x, r.center_y) for r in self.m.roi_toolbox.rois()], before)
        self.assertFalse(undo_manager.can_undo)
        undo_manager.clear()

    def test_accept_is_refused_while_a_scan_is_running(self) -> None:
        self._wire()
        self.m.roi_tuning.set_lattice(3, 1.0)
        self._open_dataset_with_plan()
        self.tab.run_button.click()
        self.assertTrue(_pump(lambda: self.m.roi_tuning.state() == tm.READY))
        before = [(r.center_x, r.center_y) for r in self.m.roi_toolbox.rois()]
        self.m.analysis_scan.is_running = lambda: True
        self.tab.accept_requested.emit()
        self.assertIn("An analysis scan is running", self.status[-1])
        self.assertEqual([(r.center_x, r.center_y) for r in self.m.roi_toolbox.rois()], before)

    def test_stop_ends_a_trial_and_keeps_nothing(self) -> None:
        self._wire()
        self.m.roi_tuning.set_lattice(3, 1.0)
        self._open_dataset_with_plan()
        self.tab.run_button.click()
        self.tab.run_button.click()  # the same button is the Stop now
        self.assertTrue(_pump(lambda: self.m.roi_tuning.state() != tm.RUNNING))
        self.assertEqual((self.m.roi_tuning.state(), self.tab.state()), (tm.IDLE, tm.IDLE))
        self.assertIsNone(self.m.roi_tuning.result())

    def test_a_change_to_the_rois_marks_the_result_out_of_date_and_shows_it(self) -> None:
        self._wire()
        self.m.roi_tuning.set_lattice(3, 1.0)
        self._open_dataset_with_plan()
        self.tab.run_button.click()
        self.assertTrue(_pump(lambda: self.m.roi_tuning.state() == tm.READY))
        self.m.roi_toolbox.add_roi(30.0, 50.0, sample_diameter_px=6.0)  # a ROI geometry change
        _APP.processEvents()
        self.assertEqual((self.m.roi_tuning.state(), self.tab.state()), (tm.STALE, tm.STALE))
        self.assertIn("run it again", self.tab.status_text())
        self.assertTrue(self.tab.export_button.isEnabled())  # the old numbers can still be looked at

    def test_closing_the_dataset_drops_the_result_and_empties_the_tab(self) -> None:
        self._wire()
        self.m.roi_tuning.set_lattice(3, 1.0)
        self._open_dataset_with_plan()
        self.tab.run_button.click()
        self.assertTrue(_pump(lambda: self.m.roi_tuning.state() == tm.READY))
        self.m.dataset.clear_dataset()
        _APP.processEvents()
        self.assertEqual((self.m.roi_tuning.state(), self.tab.state()), (tm.IDLE, tm.IDLE))

    def test_a_cancelled_file_dialog_exports_nothing(self) -> None:
        self._wire()
        self.m.roi_tuning.set_lattice(3, 1.0)
        self._open_dataset_with_plan()
        self.tab.run_button.click()
        self.assertTrue(_pump(lambda: self.m.roi_tuning.state() == tm.READY))
        self.export_path = None
        self.tab.export_button.click()
        _pump(lambda: False, 0.3)
        self.assertFalse((self.root / "tuning.csv").exists())


if __name__ == "__main__":
    unittest.main()
