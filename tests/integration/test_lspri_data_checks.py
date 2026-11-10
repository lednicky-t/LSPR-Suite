"""The data-quality checks and the image-motion check (2026-11-07): the thread helper (`BackgroundRun`), the two modules (`DataChecksModule`, `ImageMotionModule`), the report
window, the Sensorgram's "Checks" button and the Analysis tab's "Motion" group with their presenters. Real modules and a real scan on the small two-step dataset of
`test_lspri_plan_bar` (6 cubes, steps = cubes 0-2 and 3-5, two ROIs); driven through methods, signals and button clicks, never by screen coordinate.

Pinned: a run happens off the GUI thread and ends in a report; the fitted traces are shared with the store (no ROI is fitted twice); a changed input makes the report out of date
and the change undone makes it current again; the pair choices are the complete pairs; a failing run is reported, not raised; the button's dot follows the state; a click opens the
window and runs only when the report is not current; Copy and Save give the same report; the motion check reads one plane per cube and says which steps it measured.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtGui import QGuiApplication

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.analysis import scan_trace
    from lspr_imaging_app.analysis.check_report import to_text
    from lspr_imaging_app.analysis.image_motion import StepMotion
    from lspr_imaging_app.analysis.trace_store import TraceStore
    from lspr_imaging_app.panels.check_report_window import DEFAULT_SIZE, CheckReportWindow
    from lspr_imaging_app.panels.image.analysis_tab import AnalysisTab
    from lspr_imaging_app.panels.image.motion_presenter import MotionPresenter
    from lspr_imaging_app.panels.sensorgram.checks_group import ChecksControls
    from lspr_imaging_app.panels.sensorgram.checks_presenter import ChecksPresenter
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.selection import data_checks_module
    from lspr_imaging_app.selection.background_run import BackgroundRun
    from lspr_imaging_app.selection.data_checks_module import CURRENT, FAILED, NOT_RUN, RUNNING, STALE, DataChecksModule
    from lspr_imaging_app.selection.image_motion_module import ImageMotionModule
    from lspr_imaging_app.selection.cube_display_module import MODE_CUBE
    from lspr_imaging_app.storage.ui_state_keys import CHECKS_WINDOW_NUMBERS, CHECKS_WINDOW_SIZE, MOTION_WINDOW_NUMBERS, MOTION_WINDOW_SIZE
    from lspr_imaging_app.undo import undo_manager
    from tests._qt_fonts import load_system_fonts
    from tests.integration.test_lspri_plan_bar import CENTERS, _pump, _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

load_system_fonts()


class BackgroundRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.run = BackgroundRun("test-run")
        self.done: list[tuple] = []
        self.progress: list[tuple] = []
        self.threads: list[int] = []
        self.run.done.connect(self._on_done)
        self.run.progress.connect(lambda fraction, message: self.progress.append((fraction, message)))

    def tearDown(self) -> None:
        self.run.shutdown()

    def _on_done(self, token: object, result: object, error: object) -> None:
        self.threads.append(threading.get_ident())
        self.done.append((token, result, error, self.run.is_current(token)))

    def test_the_work_runs_on_another_thread_and_the_result_arrives_on_this_one(self) -> None:
        worker: list[int] = []
        token = self.run.start(lambda cancelled, report: worker.append(threading.get_ident()) or "result")
        self.assertTrue(self.run.is_running())
        self.assertTrue(_pump(lambda: bool(self.done)))
        self.assertEqual(self.done[0], (token, "result", None, True))
        self.assertNotEqual(worker[0], threading.get_ident())
        self.assertEqual(self.threads, [threading.get_ident()])
        self.assertFalse(self.run.is_running())

    def test_an_exception_comes_back_as_the_error(self) -> None:
        def boom(cancelled, report):
            raise ValueError("bad input")

        self.run.start(boom)
        self.assertTrue(_pump(lambda: bool(self.done)))
        _token, result, error, _current = self.done[0]
        self.assertIsNone(result)
        self.assertIsInstance(error, ValueError)

    def test_the_newest_run_wins_but_the_replaced_one_still_reports(self) -> None:
        seen_cancel = threading.Event()
        release = threading.Event()

        def first(cancelled, report):
            release.wait(5)
            if cancelled():
                seen_cancel.set()
            report(0.5, "from the old run")
            return "old"

        old = self.run.start(first)
        new = self.run.start(lambda cancelled, report: (report(0.25, "new"), "new")[1])
        release.set()
        self.assertTrue(_pump(lambda: len(self.done) == 2))
        by_token = {entry[0]: entry for entry in self.done}
        self.assertEqual(by_token[old][1:], ("old", None, False))  # arrives, but is no longer the current run
        self.assertEqual(by_token[new][1:], ("new", None, True))
        self.assertTrue(seen_cancel.is_set())
        self.assertEqual([m for _f, m in self.progress], ["new"])  # the old run's progress is dropped

    def test_cancel_is_seen_by_the_work_and_shutdown_waits_for_it(self) -> None:
        stopped = threading.Event()

        def loop(cancelled, report):
            while not cancelled():
                stopped.wait(0.005)
            return "stopped"

        self.run.start(loop)
        self.run.cancel()
        self.run.shutdown()
        self.assertFalse(self.run.is_running())


class ChecksBase(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.m = app_rewrite._build_modules()
        self.m.session_autosave._suspended = True
        self.row = AnalysisTab()
        app_rewrite._wire_analysis_scan(self.m, self.row, UiStateStore({}))
        self.m.scan_results._timer.setInterval(30)
        self.m.cube_display.set_mode(MODE_CUBE)
        self.store = TraceStore(self.m.scan_results, in_process=False)  # the fits run in this process, so a test can count them
        self.checks = DataChecksModule(
            self.m.scan_results, self.m.roi_toolbox, self.m.dataset, self.m.analysis_settings, self.m.experiment_plan, self.store
        )
        self.motion = ImageMotionModule(self.m.dataset, self.m.experiment_plan)
        self.fits: list[int] = []
        self._real_metric_trace = scan_trace.metric_trace

        def spy(stored, roi_id, formula_key, metric):
            self.fits.append(roi_id)
            return self._real_metric_trace(stored, roi_id, formula_key, metric)

        scan_trace.metric_trace = spy
        self.scanned: list[tuple] = []
        self.m.analysis_scan.task_finished.connect(lambda *args: self.scanned.append(args))
        self.m.dataset.load_dataset(_write_dataset(self.root))
        for cx, cy, _c0 in CENTERS.values():
            self.m.roi_toolbox.add_roi(cx, cy, sample_diameter_px=6.0)
        self.row.run_button.click()
        self.assertTrue(_pump(lambda: bool(self.scanned)))
        self.assertTrue(_pump(lambda: self.m.scan_results.stored() is not None))
        undo_manager.clear()
        self.finished: list[tuple] = []
        self.checks.finished.connect(lambda outcome, message: self.finished.append((outcome, message)))
        self.notices: list[str] = []
        self.checks.notice.connect(self.notices.append)
        self.started: list[str] = []
        self.checks.started.connect(self.started.append)

    def tearDown(self) -> None:
        scan_trace.metric_trace = self._real_metric_trace
        self.checks.shutdown()
        self.motion.shutdown()
        self.m.data_checks.shutdown()
        self.m.image_motion.shutdown()
        self.m.step_table.shutdown()
        self.m.analysis_scan.shutdown()
        self.m.scan_results.shutdown()
        self.row.deleteLater()
        # Carry the deletions out now (2026-11-10). `deleteLater()` alone only posts a request that no test ever delivers, so the widgets of
        # every test stayed alive until Python's garbage collector freed them at some later moment, which crashed the process (access
        # violation) at some collector timings: an unrelated code change could switch the crash on.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self._tmp.cleanup()
        undo_manager.clear()

    def _check(self) -> None:
        before = len(self.finished)
        self.assertTrue(self.checks.run())
        self.assertTrue(_pump(lambda: len(self.finished) > before and not self.checks.is_running()), "the checks never finished")
        self.assertEqual(self.finished[-1][0], "completed", self.finished[-1])

    def _set_pair(self, number: int = 1, baseline: int = 0, signal: int = 1) -> None:
        self.m.experiment_plan.set_role("baseline", baseline, number)
        self.m.experiment_plan.set_role("signal", signal, number)


class DataChecksModuleTests(ChecksBase):
    def test_a_run_goes_off_the_gui_thread_fits_each_roi_once_and_ends_in_a_report(self) -> None:
        self.assertEqual(self.checks.status(), NOT_RUN)
        self.assertTrue(self.checks.run())
        self.assertEqual(self.checks.status(), RUNNING)
        self.assertEqual(self.started, ["checking the data"])
        self.assertTrue(_pump(lambda: bool(self.finished) and not self.checks.is_running()))
        self.assertEqual(self.finished[-1][0], "completed")
        self.assertEqual(self.checks.status(), CURRENT)
        report = self.checks.report()
        self.assertIsNotNone(report)
        self.assertIn("2 ROIs, 6 cubes, 2 steps", report.subtitle)
        self.assertIn("polynomial fit of order 3, centroid of the top 20 %", report.subtitle)
        self.assertEqual(sorted(self.fits), [1, 2])
        self.assertEqual(sorted(self.store.cached()), [1, 2])  # kept in the store the Sensorgram and the ROI table share
        self._check()
        self.assertEqual(len(self.fits), 2)  # nothing fitted again

    def test_a_changed_input_makes_the_report_stale_and_changing_it_back_makes_it_current(self) -> None:
        self._check()
        changes: list[int] = []
        self.checks.state_changed.connect(lambda: changes.append(1))
        metric = self.m.analysis_settings.metric_settings()
        self.m.analysis_settings.set_metric_settings(replace(metric, poly_order=5))
        self.assertEqual(self.checks.status(), STALE)
        self.assertIsNotNone(self.checks.report())  # kept, marked
        self.assertTrue(changes)
        self.m.analysis_settings.set_metric_settings(metric)
        self.assertEqual(self.checks.status(), CURRENT)

    def test_a_new_evaluation_pair_changes_what_the_report_is_made_from(self) -> None:
        self._check()
        self._set_pair()
        self.assertEqual(self.checks.status(), STALE)
        self._check()
        self.assertIn("Pair 1", self.checks.report().subtitle)
        self.assertTrue(any(f.check == "pair" for f in self.checks.report().findings))  # 3 cubes per step: too few for the pair checks, and it says so

    def test_the_pair_choices_are_the_complete_pairs(self) -> None:
        self.assertEqual(self.checks.pair_choices(), [])
        self.assertIsNone(self.checks.pair())
        self.m.experiment_plan.set_role("baseline", 0, 3)  # pair 3 has no signal: not offered
        self.assertEqual(self.checks.pair_choices(), [])
        self._set_pair(1, 0, 1)
        self._set_pair(2, 1, 0)
        choices = self.checks.pair_choices()
        self.assertEqual([n for n, _text in choices], [1, 2])
        self.assertIn("B1 = 1", choices[0][1])
        self.assertIn("S1 = 2", choices[0][1])
        self.assertEqual(self.checks.pair(), 1)  # nothing chosen: the first
        chosen: list[object] = []
        self.checks.pair_changed.connect(chosen.append)
        self.checks.set_pair(2)
        self.assertEqual((self.checks.pair(), chosen), (2, [2]))
        self.checks.set_pair(2)
        self.assertEqual(chosen, [2])  # the same choice again says nothing
        self.checks.set_pair(9)  # a pair that is not complete falls back to the first
        self.assertEqual(self.checks.pair(), 1)

    def test_a_failing_run_is_reported_not_raised_and_the_next_run_clears_it(self) -> None:
        with mock.patch.object(data_checks_module, "run_diagnostics", side_effect=RuntimeError("boom")):
            self.assertTrue(self.checks.run())
            self.assertTrue(_pump(lambda: bool(self.finished) and not self.checks.is_running()))
        self.assertEqual(self.finished[-1][0], "failed")
        self.assertEqual(self.checks.status(), FAILED)
        self.assertIn("boom", self.checks.failure())
        self._check()
        self.assertEqual(self.checks.status(), CURRENT)
        self.assertIsNone(self.checks.failure())

    def test_another_dataset_drops_the_report(self) -> None:
        self._check()
        self.m.dataset.clear_dataset()
        self.assertTrue(_pump(lambda: self.checks.status() == NOT_RUN))
        self.assertIsNone(self.checks.report())

    def test_nothing_to_check_is_explained(self) -> None:
        m2 = app_rewrite._build_modules()
        try:
            empty = DataChecksModule(m2.scan_results, m2.roi_toolbox, m2.dataset, m2.analysis_settings, m2.experiment_plan, TraceStore(m2.scan_results, in_process=False))
            said: list[str] = []
            empty.notice.connect(said.append)
            self.assertFalse(empty.run())
            self.assertIn("No scan", said[0])
            self.assertEqual(empty.status(), NOT_RUN)
        finally:
            m2.scan_results.shutdown()
            m2.analysis_scan.shutdown()


class ChecksScreenTests(ChecksBase):
    def setUp(self) -> None:
        super().setUp()
        self.holder = QtWidgets.QWidget()
        self.controls = ChecksControls(self.holder)
        self.saved_to: Path | None = None
        self.window = CheckReportWindow(
            "Data checks", size_key=CHECKS_WINDOW_SIZE, numbers_key=CHECKS_WINDOW_NUMBERS, ui_state=UiStateStore({}), with_pairs=True,
            choose_path=lambda _parent, _name: self.saved_to,
        )
        self.presenter = ChecksPresenter(self.checks, self.controls, self.window)

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.holder.deleteLater()
        super().tearDown()

    def _open_and_wait(self) -> None:
        before = len(self.finished)
        self.controls.button().click()
        self.assertTrue(_pump(lambda: len(self.finished) > before and not self.checks.is_running()))

    def test_the_button_is_grey_until_a_run_then_the_window_opens_and_the_checks_run(self) -> None:
        self.assertTrue(self.controls.is_available())
        self.assertEqual(self.controls.dot_name(), "grey")
        self.assertFalse(self.window.isVisible())
        self.controls.button().click()
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.controls.dot_name(), "blue")  # running
        self.assertEqual(self.window.run_button.text(), "Cancel")
        self.assertTrue(_pump(lambda: bool(self.finished) and not self.checks.is_running()))
        self.assertIn(self.controls.dot_name(), ("green", "amber"))
        self.assertEqual(self.window.run_button.text(), "Run again")
        self.assertTrue(self.window.status_text().startswith("2 ROIs"))
        self.assertIn("polynomial", self.window.status_text())
        self.assertTrue(self.window.table_visible() or self.window.numbers_button.isVisibleTo(self.window))  # the table is there, folded

    def test_a_click_on_a_current_report_opens_the_window_without_running_again(self) -> None:
        self._open_and_wait()
        self.window.close()
        runs = len(self.started)
        self.controls.button().click()
        self.assertTrue(self.window.isVisible())
        self.assertEqual(len(self.started), runs)

    def test_the_dot_and_the_window_say_when_the_report_is_out_of_date(self) -> None:
        self._open_and_wait()
        metric = self.m.analysis_settings.metric_settings()
        self.m.analysis_settings.set_metric_settings(replace(metric, poly_order=6))
        self.assertEqual(self.controls.dot_name(), "grey")
        self.assertIn("Out of date", self.window.status_text())
        self.assertIn("out of date", self.window.findings_text())  # the report stays, marked
        self.controls.button().click()  # not current: opening runs the checks again
        self.assertTrue(_pump(lambda: len(self.finished) == 2 and not self.checks.is_running()))
        self.assertIn(self.controls.dot_name(), ("green", "amber"))

    def test_copy_gives_the_report_as_text_and_save_writes_json_and_csv(self) -> None:
        self._open_and_wait()
        report = self.checks.report()
        self.window.copy_button.click()
        self.assertEqual(QGuiApplication.clipboard().text(), to_text(report))
        for name, check in (("report.json", lambda text: self.assertIn("findings", json.loads(text))), ("table.csv", lambda text: self.assertTrue(text.startswith("Step,")))):
            self.saved_to = self.root / name
            self.window.save_button.click()
            saved = f"Saved {self.saved_to}"
            self.assertTrue(_pump(lambda: self.window.status_text() == saved and self.window.save_button.isEnabled()), self.window.status_text())
            check(self.saved_to.read_text(encoding="utf-8"))

    def test_a_pick_in_the_pair_menu_runs_the_checks_for_that_pair(self) -> None:
        self._set_pair(1, 0, 1)
        self._set_pair(2, 1, 0)
        self._open_and_wait()
        self.assertTrue(self.window.pair_combo.isVisibleTo(self.window))
        self.assertEqual(self.window.pair_combo.count(), 2)
        before = len(self.finished)
        self.window.pair_combo.setCurrentIndex(1)
        self.window.pair_combo.activated.emit(1)
        self.assertEqual(self.checks.pair(), 2)
        self.assertTrue(_pump(lambda: len(self.finished) > before and not self.checks.is_running()))
        self.assertIn("Pair 2", self.window.status_text())

    def test_without_a_pair_the_window_says_only_the_step_checks_are_made(self) -> None:
        self._open_and_wait()
        self.assertFalse(self.window.pair_combo.isVisibleTo(self.window))
        self.assertIn("only the step checks", self.window.pair_label.text())

    def test_a_refusal_is_written_in_the_window(self) -> None:
        self.m.experiment_plan  # the dataset has steps; take the scan away instead
        with mock.patch.object(self.m.scan_results, "stored", return_value=None):
            self.controls.button().click()
        self.assertTrue(self.window.isVisible())
        self.assertIn("No scan", self.window.status_text())


class MotionTests(ChecksBase):
    def test_every_step_is_measured_on_one_plane_per_cube(self) -> None:
        done: list[tuple] = []
        self.motion.finished.connect(lambda outcome, message: done.append((outcome, message)))
        self.assertTrue(self.motion.run(604.0))  # the nearest wavelength of the dataset is 600
        self.assertTrue(self.motion.is_running())
        self.assertTrue(_pump(lambda: bool(done) and not self.motion.is_running()))
        self.assertEqual(done[-1][0], "completed")
        report = self.motion.report()
        self.assertIn("600 nm", report.subtitle)
        self.assertEqual(len(report.table.rows), 2)
        self.assertEqual([row[1] for row in report.table.rows], [3, 3])
        for row in report.table.rows:
            self.assertLess(max(row[2:6]), 1.0)  # the images do not move in this dataset

    def _motion_window(self) -> CheckReportWindow:
        return CheckReportWindow("Image motion", size_key=MOTION_WINDOW_SIZE, numbers_key=MOTION_WINDOW_NUMBERS, ui_state=UiStateStore({}), compact=True)

    def test_nothing_to_measure_is_explained_on_the_status_bar(self) -> None:
        m2 = app_rewrite._build_modules()
        window = self._motion_window()
        try:
            empty = ImageMotionModule(m2.dataset, m2.experiment_plan)
            said: list[str] = []
            empty.notice.connect(said.append)
            self.assertFalse(empty.run(600.0))
            self.assertIn("Open a dataset", said[0])
            presenter = MotionPresenter(empty, self.row, window, lambda: 600.0)  # the same refusal through the button: a status-bar sentence, no window
            announced: list[str] = []
            self.row.status_message.connect(announced.append)
            self.row.motion_button.click()
            self.assertIn("Open a dataset", announced[-1])
            self.assertFalse(window.isVisible())
            self.assertFalse(presenter.task.is_active())  # nothing started
        finally:
            window.deleteLater()
            m2.scan_results.shutdown()
            m2.analysis_scan.shutdown()

    def test_a_still_image_is_one_status_sentence_at_the_wavelength_on_screen_and_no_window(self) -> None:
        window = self._motion_window()
        on_screen = [600.0]
        presenter = MotionPresenter(self.motion, self.row, window, lambda: on_screen[0])
        said: list[str] = []
        self.row.status_message.connect(said.append)
        try:
            done: list[tuple] = []
            self.motion.finished.connect(lambda outcome, message: done.append((outcome, message)))
            self.row.motion_button.click()
            self.assertTrue(self.row.is_motion_running())  # the button is the Stop meanwhile
            self.assertFalse(window.isVisible())  # runs in the background: nothing opens
            self.assertTrue(_pump(lambda: bool(done) and not self.motion.is_running()))
            self.assertFalse(self.row.is_motion_running())
            self.assertFalse(window.isVisible())
            self.assertEqual(len(said), 1)
            self.assertIn("No image motion over the limits at 600 nm in 2 steps", said[0])
            on_screen[0] = 540.0  # the next run measures the plane that is on screen then
            self.row.motion_button.click()
            self.assertTrue(_pump(lambda: len(done) == 2 and not self.motion.is_running()))
            self.assertIn("540 nm", said[-1])
            self.assertFalse(window.isVisible())
            self.row.motion_button.click()  # start, then stop
            self.assertTrue(self.row.is_motion_running())
            self.row.motion_button.click()
            self.assertTrue(_pump(lambda: len(done) == 3 and not self.motion.is_running()))
            self.assertIn(done[-1][0], ("cancelled", "completed"))
            self.assertIs(presenter.task.is_active(), False)
        finally:
            window.deleteLater()

    def test_a_moving_step_opens_the_compact_report_and_says_so_on_the_status_bar(self) -> None:
        window = self._motion_window()
        presenter = MotionPresenter(self.motion, self.row, window, lambda: 600.0)  # kept: an unreferenced presenter is freed and its connections die
        said: list[str] = []
        self.row.status_message.connect(said.append)

        def moving(label, cubes, read_plane, cancelled=None, on_plane=None):
            return StepMotion(label, tuple(cubes), np.zeros((len(cubes), 2)), (0.2, 0.1), (0.5, 0.2))

        try:
            done: list[tuple] = []
            self.motion.finished.connect(lambda outcome, message: done.append((outcome, message)))
            with mock.patch("lspr_imaging_app.selection.image_motion_module.step_motion", moving):
                self.row.motion_button.click()
                self.assertTrue(_pump(lambda: bool(done) and not self.motion.is_running()))
            self.assertTrue(window.isVisible())
            self.assertIn("the image moves", window.findings_text())
            self.assertTrue(window.run_button.isHidden())  # compact: the ribbon button runs it
            self.assertLess(window.width(), DEFAULT_SIZE[0])
            self.assertIn("over the limits in 2 of 2 steps", said[-1])
            self.assertFalse(self.row.is_motion_running())
            self.assertIs(presenter.task.is_active(), False)
        finally:
            window.close()
            window.deleteLater()


class WiringTests(unittest.TestCase):
    def test_the_app_wiring_remembers_the_pair_and_builds_the_motion_presenter(self) -> None:
        m = app_rewrite._build_modules()
        row = AnalysisTab()
        window = QtWidgets.QMainWindow()
        ui_state = UiStateStore({"sensorgram/checks_pair": 2})
        from lspr_imaging_app.panels.sensorgram import SensorgramPanel

        panel = SensorgramPanel(
            m.scan_results, m.roi_toolbox, m.dataset, m.selection, m.analysis_settings, m.cube_display, trace_in_process=False, mask=m.mask,
            experiment_plan=m.experiment_plan,
        )
        try:
            checks = app_rewrite._wire_data_checks(window, m, panel, ui_state)
            motion = app_rewrite._wire_image_motion(window, m, row, ui_state)
            self.assertFalse(hasattr(row, "motion_wavelength"))  # no box since 2026-11-10: the plane on screen is measured
            m.data_checks.set_pair(3)
            self.assertEqual(ui_state.get("sensorgram/checks_pair"), 3)
            self.assertEqual({checks.task.task_id, motion.task.task_id}, {"checks.data", "checks.motion"})
            self.assertFalse(panel.checks_controls.is_available())  # no dataset: nothing to check yet
        finally:
            panel.shutdown()
            m.data_checks.shutdown()
            m.image_motion.shutdown()
            m.scan_results.shutdown()
            m.analysis_scan.shutdown()
            panel.deleteLater()
            row.deleteLater()


if __name__ == "__main__":
    unittest.main()
