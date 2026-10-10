"""Image panel ROIs tab, "Array" group: the buttons' behaviour end to end (controls -> thread -> toolbox).

Real `ArrayControls`, `ArrayAction` (a real thread), `RoiToolbox`, `SelectionModule`, `GeometryModule`; the
plane loader returns a synthetic array image and the dialogs are replaced by recorded answers. One process
per file (offscreen Qt).
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from PyQt6.QtWidgets import QApplication

    from lspr_imaging_app.image_tools import BackgroundModule, ChromaticModule, GeometryModule
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.panels.image.array_actions import ArrayActions
    from lspr_imaging_app.panels.image.array_controls import ArrayControls, LengthField
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.array_task import ArrayAction
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.undo import undo_manager

    from tests.unit.test_lspri_roi_array_pipeline import _image
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

_APP = QApplication.instance() or QApplication([])
REFERENCE_WL = 600.0


def _pump(until=lambda: False, timeout: float = 30.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        _APP.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return until()


class ArrayActionsTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.image, self.truth = _image()
        self.image_for = lambda: self.image
        self.geometry = GeometryModule()
        self.toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.selection.set_wavelength(REFERENCE_WL)
        self.controls = ArrayControls(self.geometry)
        self.action = ArrayAction()
        self.asked: list[str] = []
        self.told: list[str] = []
        self.answers: list[bool] = []
        self.statuses: list[str] = []
        self.analysis_running = False
        self.reference: tuple[int, float] | None = (0, REFERENCE_WL)
        self.actions = ArrayActions(
            self.controls,
            self.action,
            toolbox=self.toolbox,
            selection=self.selection,
            geometry=self.geometry,
            background=BackgroundModule(),
            chromatic=ChromaticModule(),
            load_plane=lambda cube, wavelength: self.image_for().copy(),
            has_dataset=lambda: True,
            resolve_reference=lambda: self.reference,
            analysis_running=lambda: self.analysis_running,
            ask=self._ask,
            tell=lambda title, text: self.told.append(text),
        )
        self.actions.status.connect(self.statuses.append)
        self.controls.popover().edge_model.setCurrentIndex(self.controls.popover().edge_model.findData("half_max"))

    def tearDown(self) -> None:
        self.action.shutdown()
        undo_manager.clear()

    def _ask(self, title: str, text: str) -> bool:
        self.asked.append(text)
        return self.answers.pop(0) if self.answers else True

    def run_and_wait(self, click) -> None:
        click()
        if self.action.is_running():
            self.assertTrue(_pump(lambda: not self.action.is_running()), "the action did not finish")
        _pump(timeout=0.2)

    def _add_grid(self) -> None:
        """2 rows x 3 columns, added in scrambled order: ids 1..6 at known spots."""
        spots = [(60, 10), (10, 50), (10, 10), (60, 50), (35, 10), (35, 50)]  # x, y
        for x, y in spots:
            self.toolbox.add_roi(float(x), float(y), sample_diameter_px=10.0)

    def _id_at(self, x: float, y: float) -> int:
        return next(r.area_roi_id for r in self.toolbox.rois() if (r.center_x, r.center_y) == (x, y))

    def test_reorder_by_rows_and_by_columns_numbers_from_the_top_left(self) -> None:
        self._add_grid()
        self.controls._reorder.reorder_requested.emit("rows")
        ids = [self._id_at(x, y) for y in (10, 50) for x in (10, 35, 60)]
        self.assertEqual(ids, [1, 2, 3, 4, 5, 6])
        self.controls._reorder.reorder_requested.emit("columns")
        ids = [self._id_at(x, y) for x in (10, 35, 60) for y in (10, 50)]
        self.assertEqual(ids, [1, 2, 3, 4, 5, 6])
        undo_manager.undo()  # one undo step each: back to the row numbering
        self.assertEqual(self._id_at(35, 10), 2)

    def test_reorder_works_on_the_selection_only_and_keeps_other_numbers(self) -> None:
        self._add_grid()
        before = {(r.center_x, r.center_y): r.area_roi_id for r in self.toolbox.rois()}
        chosen = [2, 4, 6]  # (10,50), (60,50), (35,50): the bottom row
        self.selection.set_roi_selection(set(chosen))
        self.controls._reorder.reorder_requested.emit("rows")
        for spot, roi_id in before.items():
            if roi_id not in chosen:
                self.assertEqual(self._id_at(*spot), roi_id)
        self.assertEqual([self._id_at(x, 50) for x in (10, 35, 60)], [2, 4, 6])

    def test_reorder_is_refused_while_an_analysis_runs(self) -> None:
        self._add_grid()
        self.analysis_running = True
        self.controls._reorder.reorder_requested.emit("rows")
        self.assertEqual(self._id_at(60, 10), 1)  # unchanged
        self.assertTrue(self.told)

    def test_auto_detect_stores_the_array_as_one_undo_step(self) -> None:
        self.run_and_wait(self.controls._run.click)
        self.assertEqual(len(self.toolbox.rois()), 24)
        (array,) = self.toolbox.array_groups()
        self.assertEqual((array.rows, array.cols), (4, 6))
        centres = np.array([(r.center_x, r.center_y) for r in self.toolbox.rois()])
        self.assertLess(np.abs(centres - self.truth).max(), 0.8)
        self.assertTrue(any("4 x 6" in s for s in self.statuses))
        self.assertEqual(self.told, [])
        undo_manager.undo()
        self.assertEqual(self.toolbox.rois(), ())
        self.assertFalse(undo_manager.can_undo)

    def test_replaces_only_the_selected_rois(self) -> None:
        for x in (10.0, 20.0, 30.0):
            self.toolbox.add_roi(x, 5.0)
        undo_manager.clear()
        self.selection.set_roi_selection({2})
        self.run_and_wait(self.controls._run.click)
        self.assertEqual(len(self.toolbox.rois()), 2 + 24)  # ROI 2 replaced by the 24 of the array
        self.assertEqual([r.center_x for r in self.toolbox.rois()[:2]], [10.0, 30.0])

    def test_started_on_another_wavelength_asks_and_jumps_to_the_reference(self) -> None:
        self.selection.set_wavelength(500.0)
        self.answers = [False]
        self.run_and_wait(self.controls._run.click)
        self.assertEqual(self.toolbox.rois(), ())  # declined: nothing ran
        self.assertEqual(self.selection.current_wavelength(), 500.0)
        self.assertIn("reference wavelength (600 nm)", self.asked[0])
        self.answers = [True]
        self.run_and_wait(self.controls._run.click)
        self.assertEqual(self.selection.current_wavelength(), REFERENCE_WL)
        self.assertEqual(len(self.toolbox.rois()), 24)

    def test_refused_while_an_analysis_runs_and_without_a_reference_frame(self) -> None:
        self.analysis_running = True
        self.run_and_wait(self.controls._run.click)
        self.assertTrue(any("analysis is running" in t for t in self.told))
        self.assertEqual(self.toolbox.rois(), ())
        self.analysis_running = False
        self.reference = None
        self.run_and_wait(self.controls._run.click)
        self.assertTrue(any("reference frame" in t for t in self.told))

    def test_refine_moves_the_selected_rois_onto_their_spots(self) -> None:
        for x, y in self.truth[:6]:
            self.toolbox.add_roi(float(x) + 3.0, float(y) - 3.0, sample_diameter_px=22.0)
        undo_manager.clear()
        self.selection.set_roi_selection({1, 2, 3})
        self.assertTrue(self.controls._refine.isEnabled())
        self.run_and_wait(self.controls._refine.click)
        moved = np.array([(self.toolbox.roi_by_id(i).center_x, self.toolbox.roi_by_id(i).center_y) for i in (1, 2, 3)])
        self.assertLess(np.abs(moved - self.truth[:3]).max(), 0.8)
        untouched = self.toolbox.roi_by_id(4)
        self.assertAlmostEqual(untouched.center_x, float(self.truth[3, 0]) + 3.0, places=1)
        self.assertGreater(self.toolbox.roi_by_id(1).sample_diameter_px, 23.0)
        self.assertIsNotNone(self.toolbox.roi_by_id(1).reference_inner_diameter_px)
        undo_manager.undo()
        self.assertAlmostEqual(self.toolbox.roi_by_id(1).center_x, float(self.truth[0, 0]) + 3.0, places=1)
        self.assertFalse(undo_manager.can_undo)

    def test_refine_on_a_later_cube_writes_a_timeline_change_not_the_base(self) -> None:
        from lspr_imaging_app.roi.scope import RoiEditTarget, RoiScopeModule

        cube = {"now": 1}
        target = RoiEditTarget(RoiScopeModule(), lambda: cube["now"], lambda: 0)
        self.actions._edit_target = target
        for x, y in self.truth[:3]:
            self.toolbox.add_roi(float(x) + 3.0, float(y) - 3.0, sample_diameter_px=22.0)
        undo_manager.clear()
        self.selection.set_roi_selection({1})
        self.run_and_wait(self.controls._refine.click)
        self.assertAlmostEqual(self.toolbox.geometry_at(1, 1).center_x, float(self.truth[0, 0]), delta=0.8)
        self.assertAlmostEqual(self.toolbox.geometry_at(1, 0).center_x, float(self.truth[0, 0]) + 3.0, places=1)  # cube 0 untouched
        self.assertEqual(self.toolbox.timeline_cubes(), (1,))

    def test_refine_needs_a_selection(self) -> None:
        self.assertFalse(self.controls._refine.isEnabled())
        self.controls.refine_requested.emit()
        self.assertTrue(any("Select the ROIs" in t for t in self.told))

    def test_manual_placement_needs_the_numbers_then_places_the_lattice(self) -> None:
        self.controls.set_mode("manual")
        self.run_and_wait(self.controls._run.click)
        self.assertTrue(any("Manual placement needs" in t for t in self.told))
        pop = self.controls.popover()
        pop.rows.setValue(2)
        pop.cols.setValue(3)
        pop.diameter.set_value_px(30.0)
        pop.pitch_x.set_value_px(60.0)
        pop.pitch_y.set_value_px(70.0)
        self.controls.set_anchor_px(100.0, 50.0)
        self.run_and_wait(self.controls._run.click)
        self.assertEqual(len(self.toolbox.rois()), 6)
        roi = self.toolbox.roi_by_id(6)
        self.assertEqual((roi.center_x, roi.center_y), (220.0, 120.0))
        self.assertEqual(roi.sample_diameter_px, 30.0)
        self.assertFalse(any(r.inferred for r in self.toolbox.rois()))  # typed positions are not "inferred"

    def test_a_tilt_above_two_degrees_offers_to_rotate_and_detects_again(self) -> None:
        self.image, self.truth = _image(tilt=3.0, origin=(80.0, 50.0), shape=(340, 500))
        self.answers = [True]
        self.run_and_wait(self.controls._run.click)
        self.assertTrue(_pump(lambda: len(self.toolbox.rois()) == 24 and not self.action.is_running()))
        self.assertTrue(any("tilted by" in q for q in self.asked))
        self.assertAlmostEqual(self.geometry.settings().rotation_angle_deg, 3.0, delta=0.4)
        (array,) = self.toolbox.array_groups()
        self.assertLess(abs(array.rotation_deg), 0.5)  # detected again on the levelled image

    def test_declining_the_rotation_keeps_the_tilted_array(self) -> None:
        self.image, self.truth = _image(tilt=3.0, origin=(80.0, 50.0), shape=(340, 500))
        self.answers = [False]
        self.run_and_wait(self.controls._run.click)
        self.assertEqual(self.geometry.settings().rotation_angle_deg, 0.0)
        (array,) = self.toolbox.array_groups()
        self.assertAlmostEqual(array.rotation_deg, 3.0, delta=0.4)

    def test_no_array_in_the_image_is_reported_not_stored(self) -> None:
        self.image = np.full((200, 200), 500.0, dtype=np.float32)
        self.run_and_wait(self.controls._run.click)
        self.assertEqual(self.toolbox.rois(), ())
        self.assertEqual(len(self.told), 1)
        self.assertTrue(self.statuses[-1].startswith("Array:"))

    def test_the_run_button_cancels_a_running_action(self) -> None:
        self.controls._run.click()
        self.assertTrue(self.action.is_running())
        self.assertTrue(self.controls._running)
        self.controls._run.click()  # now it is "cancel"
        self.assertTrue(_pump(lambda: not self.action.is_running()))
        _pump(timeout=0.2)
        self.assertFalse(self.controls._running)


class ControlsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.geometry = GeometryModule()

    def test_settings_reflect_the_controls_and_auto_ignores_the_array_fields(self) -> None:
        controls = ArrayControls(self.geometry)
        pop = controls.popover()
        pop.rows.setValue(5)
        pop.diameter.set_value_px(41.5)
        self.assertIsNone(controls.settings().prior.rows)  # Auto: no priors
        controls.set_mode("semi")
        settings = controls.settings()
        self.assertEqual((settings.prior.rows, settings.prior.diameter_px), (5, 41.5))
        self.assertIsNone(settings.prior.cols)  # 0 = auto
        pop.edge_fraction.setValue(20.0)
        self.assertAlmostEqual(controls.settings().edge.fraction, 0.2)

    def test_length_fields_keep_pixels_and_show_the_app_wide_unit(self) -> None:
        self.geometry.restore_settings(
            GeometrySettings(calibration_enabled=True, microns_per_pixel_x=0.5, microns_per_pixel_y=0.5, display_units="um")
        )
        field = LengthField(self.geometry)
        field.set_value_px(40.0)
        self.assertAlmostEqual(field._spin.value(), 20.0)  # shown in um
        self.assertEqual(field._unit.text(), "µm")
        field._spin.setValue(25.0)  # typed in um
        self.assertAlmostEqual(field.value_px(), 50.0)
        self.geometry.set_display_units("px")
        self.assertAlmostEqual(field._spin.value(), 50.0)
        self.assertEqual(field._unit.text(), "px")
        field._spin.setValue(41.3)
        self.assertAlmostEqual(field.value_px(), 41.3)

    def test_unit_button_is_disabled_without_a_calibration(self) -> None:
        field = LengthField(self.geometry)
        self.assertFalse(field._unit.isEnabled())

    def test_remembered_across_restarts_in_pixels_and_restoring_saves_nothing(self) -> None:
        saved: list[dict] = []
        store = UiStateStore({"array/diameter_px": 42.5, "array/mode": "manual", "array/edge_model": "half_max"}, on_changed=saved.append)
        controls = ArrayControls(self.geometry)
        controls.bind_ui_state(store)
        self.assertEqual(controls.mode(), "manual")
        self.assertAlmostEqual(controls.popover().diameter.value_px(), 42.5)
        self.assertEqual(controls.popover().edge_model.currentData(), "half_max")
        store.flush()
        self.assertEqual(saved, [], "restoring must not save what it just read")
        controls.popover().rows.setValue(7)
        controls.popover().pitch_x.set_value_px(80.4)
        controls.popover().pitch_x.value_changed.emit(80.4)
        store.flush()
        self.assertEqual(saved[-1]["array/rows"], 7)
        self.assertAlmostEqual(saved[-1]["array/pitch_x_px"], 80.4)

    def test_overlap_rule_is_stated_in_the_run_button_tooltip(self) -> None:
        controls = ArrayControls(self.geometry)
        self.assertIn("never counts pixels of sample disks", controls._run.toolTip())


if __name__ == "__main__":
    unittest.main()
