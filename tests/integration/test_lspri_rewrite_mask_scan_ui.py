"""The Mask tab's "Scan" and "Auto mask" groups and the scan's pointers on the Mask rows (2026-10-17).

A real Image panel over a small dataset (6 cubes x 3 wavelengths, a dark blob crossing the field in cube 3); the scan runs for real on
its worker thread. What is worth pinning: a scan only *marks* frames (no mask changes), the pointers sit in the upper half of both
rows in their own colour, dismissing is reversible and skipped by the "Events" stepping mode, and the controls are remembered. The
separate Auto mask step then turns standing particle pointers into masks (ribbon wand, or the menu of a pointer), as one undo step.
Design: apps/LSPRi/eva/docs/auto_transient_scan_and_mask_plan_2026-10-16.md. One process per file.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile
from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QContextMenuEvent

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel, mask_rows
    from lspr_imaging_app.panels.image.data_axis_slider import axis_x
    from lspr_imaging_app.panels.image.slideshow import MODE_EVENTS
    from lspr_imaging_app.panels.image.timeline_strip import KIND_EVENT, KIND_EVENT_DISMISSED
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_image_panel import _pump
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

_CUBES = 6
_WAVELENGTHS = (500.0, 550.0, 600.0)
_HEIGHT, _WIDTH = 160, 400
_BLOB_X = {500.0: 80.0, 550.0: 200.0, 600.0: 320.0}  # in cube 3: 120 px a frame


def _frame(cube: int, wavelength: float) -> np.ndarray:
    index = _WAVELENGTHS.index(wavelength)
    rng = np.random.default_rng(cube * 10 + index)
    y, x = np.mgrid[0:_HEIGHT, 0:_WIDTH].astype(np.float32)
    frame = np.full((_HEIGHT, _WIDTH), 30000.0, dtype=np.float32) * (1.0 + 0.1 * index) * (1.0 + 0.002 * cube)
    frame[((x % 40) - 20) ** 2 + ((y % 40) - 20) ** 2 < 64] *= 0.5
    frame *= 1.0 + 0.004 * rng.standard_normal(frame.shape).astype(np.float32)
    if cube == 3:
        frame[(x - _BLOB_X[wavelength]) ** 2 + (y - 80.0) ** 2 <= 36] *= 0.2
    return frame


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    for cube in range(_CUBES):
        for wavelength in _WAVELENGTHS:
            path = root / f"c{cube}_w{int(wavelength)}.tif"
            tifffile.imwrite(str(path), _frame(cube, wavelength))
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


class MaskScanUiTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.mask = MaskModule()
        self.selection = SelectionModule()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), self.mask, ChromaticModule(), BackgroundModule(), RoiToolbox(),
            self.selection, ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), analysis_running=lambda: False,
        )
        self.panel.resize(1000, 780)
        self.panel.show()
        self.events = self.panel._transient_events
        self.scan = self.panel._transient_scan
        self.controls = self.panel._mask_tab.scan_controls
        self.rows = self.panel._mask_rows
        self.ribbon = self.panel._slider_ribbon
        self.messages: list[str] = []
        self.panel.tool_status_changed.connect(self.messages.append)
        self.finished: list[str] = []
        self.scan.task_finished.connect(lambda _id, outcome, _text: self.finished.append(outcome))

    def tearDown(self) -> None:
        self.scan.shutdown()
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()
        undo_manager.clear()

    # -- helpers -----------------------------------------------------------------------------------

    def load(self) -> None:
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump()

    def run_scan(self) -> None:
        self.load()
        self.controls._scan_button.click()
        deadline = time.monotonic() + 30.0
        while not self.finished and time.monotonic() < deadline:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.01)
        _pump(0.3)  # the rows rebuild after a coalescing pause

    def go_to(self, cube: int, wavelength: float = 550.0) -> None:
        self.selection.set_cube(cube)
        self.selection.set_wavelength(wavelength)
        _pump(0.1)

    def frame(self) -> tuple[int, float]:
        return int(self.selection.current_cube()), float(self.selection.current_wavelength())

    def right_click(self, strip, index: int, count: int, y: int, choice: str | None):
        seen: dict[str, object] = {}

        def fake_menu(_parent, *, header, entries, pos=None):
            seen["header"], seen["entries"] = header, list(entries)
            return choice

        x = round(axis_x(index, count, strip.width(), strip._view()))
        with mock.patch.object(mask_rows, "show_entries_menu", side_effect=fake_menu):
            event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(x, y), strip.mapToGlobal(QPoint(x, y)))
            QtWidgets.QApplication.sendEvent(strip, event)
        return seen.get("header"), seen.get("entries", [])

    # -- the group ----------------------------------------------------------------------------------------

    def test_the_scan_group_sits_between_manual_edit_and_png_behind_dividers(self) -> None:
        tab = self.panel._mask_tab
        page = self.panel._tool_ribbon._stack.widget(2)
        scan_group = tab._scan_controls.parentWidget()
        self.assertIs(scan_group.parentWidget(), page)
        self.assertEqual(tab._scan_label.text(), "Scan")
        layout = page.layout()
        edit_group = tab._edit_stack.parentWidget().parentWidget()
        png_group = tab._png_actions.parentWidget()
        order = [layout.indexOf(w) for w in (edit_group, tab._separators[3], scan_group, tab._separators[4], png_group)]
        self.assertEqual(order, sorted(order))
        self.assertTrue(all(i >= 0 for i in order))

    def test_nothing_to_scan_without_a_dataset_and_no_pointers_to_clear(self) -> None:
        self.assertFalse(self.controls._scan_button.isEnabled())
        self.assertFalse(self.controls._clear_button.isEnabled())
        self.controls.start_scan()
        self.assertIn("Load a dataset first", self.messages[-1])
        self.load()
        self.assertTrue(self.controls._scan_button.isEnabled())

    # -- a scan marks, it does not mask -------------------------------------------------------------------

    def test_a_scan_marks_the_frames_and_changes_no_mask(self) -> None:
        self.run_scan()
        self.assertEqual(self.finished, ["completed"])
        self.assertEqual({r.event.frame for r in self.events.records()}, {(3, w) for w in _WAVELENGTHS})
        self.assertEqual(self.mask.mask_changes(), ())  # the scan only points
        self.assertIn("nothing was masked", self.messages[-1])
        self.assertTrue(self.controls._clear_button.isEnabled())
        self.assertTrue(self.controls._scan_button.isEnabled())  # the group is usable again

    def test_the_pointers_are_pink_ticks_on_both_rows_and_say_what_they_are(self) -> None:
        self.run_scan()
        self.go_to(3)
        cube_marks = {m.index: m for m in self.rows.cube_strip.event_marks()}
        self.assertEqual(list(cube_marks), [3])
        self.assertEqual(cube_marks[3].kind, KIND_EVENT)
        self.assertIn("particle passage 500-600 nm", cube_marks[3].tip)
        self.assertIn("3 frames", cube_marks[3].tip)
        self.assertEqual([m.index for m in self.rows.wavelength_strip.event_marks()], [0, 1, 2])
        self.assertIn("passage 1", self.rows.wavelength_strip.event_marks()[1].tip)
        for strip in (self.rows.cube_strip, self.rows.wavelength_strip):
            self.assertEqual(strip._event_color, mask_rows.EVENT_COLOR)
            self.assertEqual(strip.marks(), ())  # a pointer is not a mask mark
        self.go_to(1)
        self.assertEqual(self.rows.wavelength_strip.event_marks(), ())  # the wavelength row follows the cube shown

    def test_the_pointers_do_not_hide_a_mask_mark_and_each_has_its_own_menu(self) -> None:
        self.run_scan()
        self.mask.set_mask_change((3, 550.0), "individual", np.zeros((_HEIGHT, _WIDTH), dtype=bool))
        _pump(0.3)
        strip, count = self.rows.cube_strip, 6
        header, entries = self.right_click(strip, 3, count, y=12, choice=None)  # lower half: the mask mark's menu
        self.assertIn("individual mask", header)
        header, entries = self.right_click(strip, 3, count, y=2, choice=None)  # upper half: the pointer's
        self.assertIn("scan pointers", header)
        self.assertEqual([e.key for e in entries], [mask_rows.GO_TO, mask_rows.MASK_EVENTS, mask_rows.DISMISS, mask_rows.RESTORE])
        self.assertEqual([e.enabled for e in entries], [True, True, True, False])

    def test_dismissing_fades_the_pointer_and_restoring_brings_it_back(self) -> None:
        self.run_scan()
        strip, count = self.rows.cube_strip, 6
        self.right_click(strip, 3, count, y=2, choice=mask_rows.DISMISS)
        _pump(0.3)
        self.assertEqual(self.events.counts(), (0, 3))
        self.assertEqual(strip.event_marks()[0].kind, KIND_EVENT_DISMISSED)
        self.assertIn("3 events dismissed", self.messages[-1])
        header, entries = self.right_click(strip, 3, count, y=2, choice=mask_rows.RESTORE)
        _pump(0.3)
        self.assertEqual(self.events.counts(), (3, 0))
        self.assertEqual(strip.event_marks()[0].kind, KIND_EVENT)

    def test_the_wavelength_row_menu_dismisses_one_frame(self) -> None:
        self.run_scan()
        self.go_to(3)
        strip = self.rows.wavelength_strip
        self.right_click(strip, 1, 3, y=2, choice=mask_rows.DISMISS)  # 550 nm
        _pump(0.3)
        self.assertEqual({r.event.frame[1] for r in self.events.records() if r.dismissed}, {550.0})
        self.assertEqual([m.kind for m in strip.event_marks()], [KIND_EVENT, KIND_EVENT_DISMISSED, KIND_EVENT])

    # -- stepping through the pointers --------------------------------------------------------------------

    def test_events_is_a_stepping_choice_and_skips_dismissed_cubes(self) -> None:
        self.run_scan()
        panel = self.ribbon._settings_panel
        panel._mode_events.click()
        self.assertEqual(self.ribbon.settings().mode, MODE_EVENTS)
        self.assertTrue(panel._mode_events.isChecked())
        self.assertFalse(panel._mode_mask.isChecked())
        self.assertIn("scan pointer", self.ribbon._next.toolTip())
        self.go_to(0)
        self.ribbon._next.click()
        self.assertEqual(self.frame()[0], 3)
        self.events.dismiss([r.id for r in self.events.records()])
        self.go_to(0)
        self.assertEqual(self.rows.event_state_cubes(), ())
        self.ribbon._next.click()
        self.assertNotEqual(self.frame()[0], 3)  # a dismissed cube is not a stop

    def test_the_events_stepping_choice_is_remembered(self) -> None:
        self.panel.restore_ui_state(UiStateStore({"image/slideshow_mode": "events"}))
        self.assertEqual(self.ribbon.settings().mode, MODE_EVENTS)

    # -- the controls --------------------------------------------------------------------------------------

    def test_the_controls_are_remembered_and_drive_the_scan_settings(self) -> None:
        store = UiStateStore({"image/mask_scan/sigma": 80, "image/mask_scan/stride": 2, "image/mask_scan/follow_passages": False})
        self.panel.restore_ui_state(store)
        params = self.controls.scan_params()
        self.assertEqual((params.detection.sigma_factor, params.stride, params.fill_gaps), (80.0, 2, False))
        self.controls._sigma_spin.setValue(45)
        self.assertEqual(store.get("image/mask_scan/sigma"), 45)

    def test_the_defaults_are_the_measured_ones(self) -> None:
        params = self.controls.scan_params()
        self.assertEqual((params.detection.sigma_factor, params.stride, params.fill_gaps), (60.0, 3, True))

    def test_clearing_removes_the_pointers_and_says_how_many(self) -> None:
        self.run_scan()
        self.controls._clear_button.click()
        _pump(0.3)
        self.assertFalse(self.events.has_events())
        self.assertEqual(self.rows.cube_strip.event_marks(), ())
        self.assertIn("3 pointers cleared", self.messages[-1])
        self.assertFalse(self.controls._clear_button.isEnabled())

    def test_the_controls_are_locked_while_a_scan_runs(self) -> None:
        self.load()
        with mock.patch.object(self.scan, "is_running", return_value=True):
            self.scan.running_changed.emit(True)
            for widget in (self.controls._scan_button, self.controls._sigma_spin, self.controls._stride_spin, self.controls._fill_button):
                self.assertFalse(widget.isEnabled())
        self.scan.running_changed.emit(False)
        self.assertTrue(self.controls._scan_button.isEnabled())

    # -- the scan range ----------------------------------------------------------------------------------

    def test_only_the_cubes_in_view_are_scanned_when_the_switch_is_on(self) -> None:
        self.load()
        window = self.panel._cube_slider.axis_window()
        self.assertIsNone(self.panel._visible_cube_range())  # nothing zoomed: every cube is in view
        self.controls._range_button.setChecked(True)
        self.assertIsNone(self.controls.scan_params().cube_range)  # on, but the whole axis is shown: the whole dataset
        window.zoom(10.0, 0.0)  # the deepest zoom, anchored at the left: 5 of the 6 cubes
        self.assertEqual(self.panel._visible_cube_range(), (0, 4))
        self.assertEqual(self.controls.scan_params().cube_range, (0, 4))
        window.pan(5.0)  # to the right end
        self.assertEqual(self.controls.scan_params().cube_range, (1, 5))
        self.controls._range_button.setChecked(False)
        self.assertIsNone(self.controls.scan_params().cube_range)  # off: always everything

    def test_a_scan_of_a_stretch_says_so_and_keeps_the_pointers_outside_it(self) -> None:
        self.run_scan()
        before = [r.event.frame for r in self.events.records()]
        self.finished.clear()
        self.panel._cube_slider.axis_window().zoom(10.0, 1.0)  # the right end: cubes 1-5
        self.controls._range_button.setChecked(True)
        self.assertEqual(self.controls.scan_params().cube_range, (1, 5))
        self.controls._scan_button.click()
        self.assertIn("in cubes 1-5", self.messages[-1])
        deadline = time.monotonic() + 30.0
        while not self.finished and time.monotonic() < deadline:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.01)
        self.assertEqual([r.event.frame for r in self.events.records()], before)  # cube 3 is inside the stretch: found again
        self.assertEqual(self.events.info().settings["cube_range"], [1, 5])

    def test_the_settings_popup_holds_the_controls_and_its_button_says_what_a_scan_would_use(self) -> None:
        button = self.controls._settings_button
        self.assertIn("sensitivity 60 sigma, every 3rd wavelength, following passages through faint frames, the whole dataset", button.toolTip())
        self.assertIs(button.menu().actions()[0].defaultWidget(), self.controls._pop)
        self.controls._sigma_spin.setValue(45)
        self.controls._stride_spin.setValue(1)
        self.controls._fill_button.setChecked(False)
        self.controls._range_button.setChecked(True)
        self.assertIn("sensitivity 45 sigma, every wavelength, only the cubes in view", button.toolTip())
        self.assertNotIn("following passages", button.toolTip())

    def test_the_range_switch_is_remembered(self) -> None:
        store = UiStateStore({"image/mask_scan/visible_only": True})
        self.panel.restore_ui_state(store)
        self.assertTrue(self.controls._range_button.isChecked())

    # -- Auto mask: pointers -> masks ---------------------------------------------------------------------

    def wait_for_auto_mask(self) -> None:
        deadline = time.monotonic() + 20.0
        while self.panel._mask_auto.is_running() and time.monotonic() < deadline:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.005)
        _pump(0.3)

    def test_the_auto_mask_group_sits_after_scan_and_before_png(self) -> None:
        tab = self.panel._mask_tab
        page = self.panel._tool_ribbon._stack.widget(2)
        auto_group = tab._auto_controls.parentWidget()
        self.assertEqual(tab._auto_label.text(), "Auto mask")
        layout = page.layout()
        scan_group = tab._scan_controls.parentWidget()
        png_group = tab._png_actions.parentWidget()
        order = [layout.indexOf(w) for w in (scan_group, tab._separators[4], auto_group, tab._separators[5], png_group)]
        self.assertEqual(order, sorted(order))
        self.assertTrue(all(i >= 0 for i in order))

    def test_nothing_to_mask_before_a_scan_and_the_tooltip_says_why(self) -> None:
        self.load()
        auto = self.panel._mask_tab.auto_controls
        self.assertFalse(auto._apply_button.isEnabled())
        self.assertIn("scan first", auto._apply_button.toolTip())
        self.run_scan_loaded()
        self.assertTrue(auto._apply_button.isEnabled())
        self.assertIn("3 particle pointers", auto._apply_button.toolTip())
        self.assertIn("(3 frames)", auto._apply_button.toolTip())

    def run_scan_loaded(self) -> None:
        self.controls._scan_button.click()
        deadline = time.monotonic() + 30.0
        while not self.finished and time.monotonic() < deadline:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.01)
        _pump(0.3)

    def test_the_wand_makes_an_individual_mask_at_every_frame_with_a_pointer_in_one_undo_step(self) -> None:
        self.run_scan()
        persistent = np.zeros((_HEIGHT, _WIDTH), dtype=bool)
        persistent[140:150, 10:30] = True
        self.mask.set_mask_change((0, 500.0), "persistent", persistent)
        undo_manager.clear()
        self.panel._mask_tab.auto_controls._apply_button.click()
        self.wait_for_auto_mask()
        self.assertEqual(self.mask.individual_cubes(), {3: _WAVELENGTHS})
        for wavelength, x in _BLOB_X.items():
            authored, plane, scope = self.mask.resolve_mask_source((3, wavelength))
            self.assertEqual((authored, scope), ((3, wavelength), "individual"))
            self.assertTrue(plane[80, int(x)], "the particle is masked")
            self.assertTrue(plane[80, int(x) + 8], "with a margin around it (6 px radius contour + 3 px)")
            self.assertTrue(np.all(plane[persistent]), "merged with the persistent mask in effect")
        self.assertIn("Auto mask: 3 masks from 3 particle events (+3 px)", self.messages[-1])
        self.assertEqual(self.events.counts(), (3, 0))  # making masks does not touch the pointers
        self.assertEqual(undo_manager.undo_label, "Auto mask (3 frames)")
        undo_manager.undo()
        self.assertEqual(self.mask.individual_cubes(), {})
        self.assertFalse(undo_manager.can_undo)
        _pump(0.3)
        self.assertEqual(self.rows.cube_strip.marks(), (self.rows.cube_strip.marks()[0],))  # only the persistent mask's mark is left

    def test_the_feathering_box_grows_the_masks_and_is_remembered(self) -> None:
        store = UiStateStore({"image/mask_auto/dilate_px": 0})
        self.panel.restore_ui_state(store)
        auto = self.panel._mask_tab.auto_controls
        self.assertEqual(auto.dilate_px(), 0)
        self.run_scan()
        auto._apply_button.click()
        self.wait_for_auto_mask()
        thin = int(self.mask.resolve_mask_source((3, 550.0))[1].sum())
        undo_manager.undo()
        auto._dilate_spin.setValue(4)
        self.assertEqual(store.get("image/mask_auto/dilate_px"), 4)
        auto._apply_button.click()
        self.wait_for_auto_mask()
        self.assertGreater(int(self.mask.resolve_mask_source((3, 550.0))[1].sum()), thin + 100)

    def test_dismissed_pointers_are_not_masked(self) -> None:
        self.run_scan()
        middle = [r.id for r in self.events.records() if r.event.frame[1] == 550.0]
        self.events.dismiss(middle)
        _pump(0.2)
        self.panel._mask_tab.auto_controls._apply_button.click()
        self.wait_for_auto_mask()
        self.assertEqual(self.mask.individual_cubes(), {3: (500.0, 600.0)})
        self.assertIn("2 particle pointers", self.panel._mask_tab.auto_controls._apply_button.toolTip())

    def test_the_cube_pointer_menu_masks_the_events_of_that_cube(self) -> None:
        self.run_scan()
        header, entries = self.right_click(self.rows.cube_strip, 3, 6, y=2, choice=mask_rows.MASK_EVENTS)
        self.wait_for_auto_mask()
        self.assertEqual(self.mask.individual_cubes(), {3: _WAVELENGTHS})
        mask_entry = next(e for e in entries if e.key == mask_rows.MASK_EVENTS)
        self.assertIn("3 frames", mask_entry.text)
        self.assertTrue(mask_entry.enabled)

    def test_the_frame_pointer_menu_masks_only_that_frame_and_is_greyed_when_dismissed(self) -> None:
        self.run_scan()
        self.go_to(3)
        self.right_click(self.rows.wavelength_strip, 1, 3, y=2, choice=mask_rows.MASK_EVENTS)  # 550 nm
        self.wait_for_auto_mask()
        self.assertEqual(self.mask.individual_cubes(), {3: (550.0,)})
        self.events.dismiss([r.id for r in self.events.records() if r.event.frame[1] == 500.0])
        _pump(0.2)
        _header, entries = self.right_click(self.rows.wavelength_strip, 0, 3, y=2, choice=None)
        self.assertFalse(next(e for e in entries if e.key == mask_rows.MASK_EVENTS).enabled)

    def test_global_and_artefact_pointers_are_never_masked(self) -> None:
        self.run_scan()
        from dataclasses import replace

        from lspr_imaging_app.image_tools.mask.transient_detection import KIND_ARTEFACT, KIND_GLOBAL
        from lspr_imaging_app.image_tools.mask.transient_events_state import EventRecord, TransientEventsState

        records = self.events.records()
        changed = tuple(
            EventRecord(r.id, replace(r.event, kind=KIND_GLOBAL if i == 0 else KIND_ARTEFACT if i == 1 else r.event.kind), r.dismissed)
            for i, r in enumerate(records)
        )
        self.events.restore_state(TransientEventsState(self.events.info(), changed))
        self.panel._mask_tab.auto_controls._apply_button.click()
        self.wait_for_auto_mask()
        self.assertEqual(len(self.mask.individual_cubes()[3]), 1)

    def test_the_wand_is_locked_while_a_scan_runs_and_without_a_dataset(self) -> None:
        self.assertFalse(self.panel._mask_tab.auto_controls._apply_button.isEnabled())
        self.run_scan()
        auto = self.panel._mask_tab.auto_controls
        with mock.patch.object(self.scan, "is_running", return_value=True):
            self.scan.running_changed.emit(True)
            self.assertFalse(auto._apply_button.isEnabled())
        self.scan.running_changed.emit(False)
        self.assertTrue(auto._apply_button.isEnabled())

    def test_a_pointer_of_a_cube_the_dataset_does_not_have_is_not_drawn(self) -> None:
        self.run_scan()
        records = self.events.state().records
        self.events.restore_state(type(self.events.state())(self.events.info(), records))
        self.dataset.clear_dataset()
        _pump(0.3)
        self.assertEqual(self.rows.cube_strip.event_marks(), ())


if __name__ == "__main__":
    unittest.main()
