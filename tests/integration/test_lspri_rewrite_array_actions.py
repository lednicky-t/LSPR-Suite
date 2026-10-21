"""Image panel ROIs tab, "Array" group: the buttons' behaviour end to end (controls -> thread -> toolbox).

Real `ArrayControls`, `ArrayAction` (a real thread), `RoiToolbox`, `SelectionModule`, `GeometryModule`; the
plane loader returns a synthetic array image and the dialogs are replaced by recorded answers. One process
per file (offscreen Qt).
"""

from __future__ import annotations

import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

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
    skip_if_lspri_rewrite_absent(exc)
    raise

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
            raw_plane_shape=lambda: self.image_for().shape,
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

    def test_auto_rotate_levels_even_a_small_tilt_without_asking_or_placing_rois(self) -> None:
        self.image, self.truth = _image(tilt=0.6, origin=(80.0, 50.0), shape=(340, 500))  # below the 1 deg question
        self.run_and_wait(self.actions.auto_rotate)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.toolbox.rois(), ())
        self.assertAlmostEqual(self.geometry.settings().rotation_angle_deg, 0.6, delta=0.15)
        self.assertTrue(self.statuses[-1].startswith("Auto rotate: levelled"), self.statuses[-1])
        # A second pass measures the levelled image: the residual tilt is small, so the rotation barely moves.
        first = self.geometry.settings().rotation_angle_deg
        self.run_and_wait(self.actions.auto_rotate)
        self.assertAlmostEqual(self.geometry.settings().rotation_angle_deg, first, delta=0.15)

    def test_auto_rotate_refusal_is_titled_and_does_not_hijack_the_next_detect(self) -> None:
        self.image, self.truth = _image()
        self.reference = None
        self.actions.auto_rotate()
        self.assertTrue(self.statuses[-1].startswith("Auto rotate:"))
        self.reference = (0, REFERENCE_WL)
        self.run_and_wait(self.controls._run.click)  # an ordinary Detect still places the array
        self.assertEqual(len(self.toolbox.rois()), 24)

    def test_auto_rotate_button_in_the_rotation_group_asks_for_it(self) -> None:
        from lspr_imaging_app.image_tools import ActiveToolModule
        from lspr_imaging_app.panels.image.transforms_settings import TransformsSection

        section = TransformsSection(self.geometry, ActiveToolModule())
        self.addCleanup(section.deleteLater)
        clicks: list[bool] = []
        section.auto_rotate_requested.connect(lambda: clicks.append(True))
        section._auto_rotate_button.click()
        self.assertEqual(clicks, [True])
        self.assertFalse(section._auto_rotate_button.icon().isNull())

    def test_auto_crop_keeps_half_a_pitch_around_the_rois_and_nothing_else(self) -> None:
        # 4 x 6 spots, 70 px pitch, centres from (70, 60): half a pitch (35 px) of border on every side.
        self.run_and_wait(self.controls._run.click)  # the ROIs are a dependency, placed by Detect
        self.assertEqual(len(self.toolbox.rois()), 24)
        before = [(r.center_x, r.center_y) for r in self.toolbox.rois()]
        self.actions.auto_crop()  # synchronous: no image is read, no thread
        crop = self.geometry.settings().crop
        self.assertTrue(crop.enabled)
        self.assertEqual((crop.x, crop.y), (70 - 35, 60 - 35))
        self.assertEqual((crop.width, crop.height), (6 * 70, 4 * 70))
        self.assertEqual(self.geometry.settings().rotation_angle_deg, 0.0)  # Auto crop never rotates any more
        self.assertIn("6 x 4 ROIs at pitch", self.statuses[-1])
        # One undo step, and the ROIs moved with the crop (RoiGeometrySync is not wired here, so they do not).
        self.assertEqual(undo_manager.undo_label, "Crop")
        undo_manager.undo()
        self.assertFalse(self.geometry.settings().crop.enabled)
        self.assertEqual([(r.center_x, r.center_y) for r in self.toolbox.rois()], before)

    def test_auto_crop_run_twice_reproduces_the_same_rectangle(self) -> None:
        """A second run measures the full canvas, not the already-cropped image, so the crop is replaced rather
        than cut into again - otherwise repeated clicks would eat the image. Shown by moving the ROIs the way
        the app's `RoiGeometrySync` does on a crop edit (it is not wired into this test)."""
        self.run_and_wait(self.controls._run.click)
        self.actions.auto_crop()
        first = replace(self.geometry.settings().crop)
        dx, dy = float(first.x), float(first.y)
        self.toolbox.remap_all(lambda x, y: (x - dx, y - dy), lambda mask: mask)
        self.actions.auto_crop()
        second = self.geometry.settings().crop
        self.assertEqual((second.x, second.y, second.width, second.height),
                         (first.x, first.y, first.width, first.height))

    def test_auto_crop_keeps_the_edge_rois_inside_when_the_ring_reaches_past_half_the_pitch(self) -> None:
        """A 40 px pitch with 26 px disks: the ring's outer radius is 0.86 x the diameter = 22 px, past the 20 px
        half-pitch, so the margin grows rather than cutting measurement pixels off the outermost spots."""
        self.image, self.truth = _image(rows=3, cols=4, pitch=40.0, diameter=26.0, origin=(90.0, 80.0), shape=(280, 360))
        self.run_and_wait(self.controls._run.click)
        self.actions.auto_crop()
        crop = self.geometry.settings().crop
        self.assertTrue(crop.enabled)
        self.assertLess(crop.x, 90 - 20)  # wider than half the pitch
        self.assertGreater(crop.x + crop.width, 90 + 3 * 40 + 20)
        self.assertIn("margin is wider than half the pitch", self.statuses[-1])

    def test_auto_crop_without_rois_says_so_and_leaves_the_geometry_alone(self) -> None:
        self.actions.auto_crop()
        self.assertTrue(self.statuses[-1].startswith("Auto crop: place the ROIs first"), self.statuses[-1])
        self.assertFalse(self.geometry.settings().crop.enabled)
        self.assertFalse(undo_manager.can_undo)

    def test_auto_rotate_ticks_the_level_step_before_any_roi_exists(self) -> None:
        """2026-10-21 report: "the leveling array is still present even though I run it" - Auto rotate comes first
        in the maintainer's order of work, when there is no ROI lattice to measure the tilt on."""
        self.image, self.truth = _image(tilt=0.6, origin=(80.0, 50.0), shape=(340, 500))
        self.assertIsNone(self.actions.auto_crop_readiness().levelled)
        self.run_and_wait(self.actions.auto_rotate)
        levelled = self.actions.auto_crop_readiness()
        self.assertTrue(levelled.levelled)  # earned by Auto rotate's own measurement, with no ROIs at all
        self.assertFalse(levelled.rois)
        self.assertIn("levelled by Auto rotate", levelled.level_note)
        # It stands only while the rotation is the one Auto rotate set: a later turn of the dial voids it.
        self.geometry.set_rotation(self.geometry.settings().rotation_angle_deg + 1.0)
        self.assertIsNone(self.actions.auto_crop_readiness().levelled)

    def test_an_already_level_array_also_ticks_the_level_step(self) -> None:
        self.run_and_wait(self.actions.auto_rotate)  # a level image: "already level", no rotation change
        self.assertIn("already level", self.statuses[-1])
        self.assertTrue(self.actions.auto_crop_readiness().levelled)

    def test_the_measured_lattice_overrules_what_auto_rotate_remembered(self) -> None:
        """Once the ROIs are there the tick is a measurement again, so a rotation made in between cannot leave a
        stale tick - and the ROI lattice has the last word."""
        self.image, self.truth = _image(tilt=3.0, origin=(80.0, 50.0), shape=(340, 500))
        self.answers = [False]  # decline the "rotate and detect again" offer: the stored array stays tilted
        self.run_and_wait(self.controls._run.click)
        self.actions._levelled_at = self.geometry.settings().rotation_angle_deg  # as if Auto rotate had run
        readiness = self.actions.auto_crop_readiness()
        self.assertFalse(readiness.levelled)
        self.assertIn("tilt +3", readiness.level_note)

    def test_auto_crop_readiness_measures_each_dependency(self) -> None:
        empty = self.actions.auto_crop_readiness()
        self.assertEqual((empty.levelled, empty.rois, empty.chromatic, empty.ready), (None, False, False, False))
        self.run_and_wait(self.controls._run.click)
        level = self.actions.auto_crop_readiness()
        self.assertTrue(level.levelled)  # a level array: the tick is earned, not assumed
        self.assertAlmostEqual(level.tilt_deg, 0.0, delta=0.05)
        self.assertEqual((level.rois, level.chromatic, level.ready), (True, False, False))
        # A tilted array that the user chose not to level: measured from the ROI centres, so the tick is withheld.
        self.toolbox.delete_rois(tuple(r.area_roi_id for r in self.toolbox.rois()))
        self.image, self.truth = _image(tilt=3.0, origin=(80.0, 50.0), shape=(340, 500))
        self.answers = [False]
        self.run_and_wait(self.controls._run.click)
        tilted = self.actions.auto_crop_readiness()
        self.assertFalse(tilted.levelled)
        self.assertAlmostEqual(tilted.tilt_deg, 3.0, delta=0.2)

    def test_readiness_never_walks_the_fitted_chromatic_models(self) -> None:
        """2026-10-21 regression: the readiness check unioned every ROI's read box over every fitted chromatic
        affine. In the maintainer's 314-cube session (8478 fitted models) that cost ~300 ms **per sample of a ROI
        drag** - measured 1.3 s at 8478 models, 277 ms at 3000. It only ever needed the ROI centres."""
        self.run_and_wait(self.controls._run.click)
        chromatic = self.actions._chromatic
        walked: list[int] = []
        original = chromatic.models
        chromatic.models = lambda: (walked.append(1), original())[1]
        self.addCleanup(lambda: setattr(chromatic, "models", original))
        self.assertTrue(self.actions.auto_crop_readiness().rois)
        self.toolbox.translate_rois(tuple(r.area_roi_id for r in self.toolbox.rois()), 1.0, 0.0)
        _pump(timeout=0.4)  # let the debounced readiness check run
        self.assertEqual(walked, [], "the readiness check must not build the fitted-model list")

    def test_a_burst_of_roi_edits_recomputes_the_checklist_once(self) -> None:
        """A drag emits `geometry_changed` per mouse step; the checklist is only a button colour and a tooltip."""
        self.run_and_wait(self.controls._run.click)
        ids = tuple(r.area_roi_id for r in self.toolbox.rois())
        emitted: list[object] = []
        self.actions.auto_crop_readiness_changed.connect(emitted.append)
        for _ in range(20):
            self.toolbox.translate_rois(ids, 1.0, 0.0)
        self.assertEqual(emitted, [])  # nothing yet: it waits for the drag to go quiet
        _pump(timeout=0.5)
        self.assertEqual(len(emitted), 1)

    def test_auto_crop_button_crops_when_ready_and_guides_when_not(self) -> None:
        from lspr_imaging_app.image_tools import ActiveToolModule
        from lspr_imaging_app.panels.image.transforms_settings import TransformsSection

        section = TransformsSection(self.geometry, ActiveToolModule())
        self.addCleanup(section.deleteLater)
        crops: list[bool] = []
        rotates: list[bool] = []
        navigations: list[str] = []
        section.auto_crop_requested.connect(lambda: crops.append(True))
        section.auto_rotate_requested.connect(lambda: rotates.append(True))
        section.navigate_requested.connect(navigations.append)

        # Nothing done yet: amber, all three steps offered, and a click guides instead of cropping.
        section.set_auto_crop_state(levelled=None, rois=False, chromatic=False,
                                    level_note="run Auto rotate, or place the ROIs to measure it")
        self.assertEqual(len(section._auto_crop_menu.actions()), 3)
        self.assertIn("✗ Level the array", section._auto_crop_button.toolTip())
        self.assertIn("run Auto rotate, or place the ROIs", section._auto_crop_button.toolTip())
        section._auto_crop_button.click()
        self.addCleanup(section._auto_crop_menu.close)
        self.assertEqual(crops, [])
        rows = section._auto_crop_menu.actions()
        rows[0].trigger()
        self.assertEqual(rotates, [True])  # "Level the array" runs Auto rotate here
        rows[1].trigger()
        rows[2].trigger()
        self.assertEqual(navigations, ["rois", "chromatic"])  # the other two are a trip to another tab

        # Only the chromatic correction left: one row, and the tilt is named.
        section.set_auto_crop_state(levelled=True, rois=True, chromatic=False, level_note="tilt +0.03°")
        self.assertEqual(len(section._auto_crop_menu.actions()), 1)
        self.assertIn("✓ Level the array - tilt +0.03", section._auto_crop_button.toolTip())

        # All three met: green, no menu rows, and the click crops.
        section.set_auto_crop_state(levelled=True, rois=True, chromatic=True, level_note="tilt +0.03°")
        self.assertEqual(section._auto_crop_menu.actions(), [])
        self.assertIn("Click to crop.", section._auto_crop_button.toolTip())
        section._auto_crop_button.click()
        self.assertEqual(crops, [True])

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
