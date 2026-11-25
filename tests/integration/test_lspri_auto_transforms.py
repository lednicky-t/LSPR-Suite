"""Image tools tab, Rotation / Crop groups: Auto rotate (on the array finder) and Auto crop (on the ROI lattice) end to end.

Real `AutoTransforms`, a real `ArrayFindTask` thread, `RoiToolbox`, `SelectionModule`, `GeometryModule`; the plane loader returns a synthetic array image and the dialogs are
replaced by recorded answers. Replaces the old Array group's tests of the same two buttons (2026-11-25). One process per file (offscreen Qt).
"""

from __future__ import annotations

import sys
import time
import types
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
    from lspr_imaging_app.panels.image.auto_transforms import MAX_TILT_DEG, TASK_ID, AutoTransforms
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.undo import undo_manager

    from tests._array_synth import synth
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QApplication.instance() or QApplication([])
REFERENCE_WL = 600.0


def _pump(until=lambda: False, timeout: float = 60.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        _APP.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return until()


class AutoTransformsTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.image, self.truth = synth(shape=(360, 480))
        self.geometry = GeometryModule()
        self.toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.selection.set_wavelength(REFERENCE_WL)
        self.asked: list[str] = []
        self.told: list[str] = []
        self.answers: list[bool] = []
        self.statuses: list[str] = []
        self.reference: tuple[int, float] | None = (0, REFERENCE_WL)
        self.actions = AutoTransforms(
            toolbox=self.toolbox,
            selection=self.selection,
            geometry=self.geometry,
            background=BackgroundModule(),
            chromatic=ChromaticModule(),
            load_plane=lambda cube, wavelength: self.image.copy(),
            has_dataset=lambda: True,
            raw_plane_shape=lambda: self.image.shape,
            resolve_reference=lambda: self.reference,
            ask=self._ask,
            tell=lambda title, text: self.told.append(text),
        )
        self.actions.status.connect(self.statuses.append)
        self.task_ids: list[str] = []
        self.actions.task_progress.connect(lambda task_id, _label, _fraction, _message: self.task_ids.append(task_id))
        self.outcomes: list[tuple[str, str]] = []
        self.actions.task_finished.connect(lambda task_id, outcome, _message: self.outcomes.append((task_id, outcome)))

    def tearDown(self) -> None:
        self.actions.shutdown()
        undo_manager.clear()

    def _ask(self, title: str, text: str) -> bool:
        self.asked.append(text)
        return self.answers.pop(0) if self.answers else True

    def run_and_wait(self, click) -> None:
        click()
        if self.actions.is_running():
            self.assertTrue(_pump(lambda: not self.actions.is_running()), "Auto rotate did not finish")
        _pump(timeout=0.2)

    def _add_lattice(self, rows: int = 4, cols: int = 6, pitch: float = 70.0, origin: tuple[float, float] = (70.0, 60.0), diameter: float = 26.0) -> None:
        for j in range(rows):
            for i in range(cols):
                self.toolbox.add_roi(origin[0] + i * pitch, origin[1] + j * pitch, sample_diameter_px=diameter)

    def _add_tilted_lattice(self, tilt_deg: float, rows: int = 4, cols: int = 6, pitch: float = 70.0) -> None:
        """The lattice turned about its first node by ``tilt_deg`` (clockwise on the screen, y down), moved so that every ROI is inside the image."""
        tilt = np.radians(tilt_deg)
        for j in range(rows):
            for i in range(cols):
                x, y = i * pitch, j * pitch
                self.toolbox.add_roi(70.0 + x * np.cos(tilt) - y * np.sin(tilt), 60.0 + x * np.sin(tilt) + y * np.cos(tilt), sample_diameter_px=26.0)

    # -- Auto rotate -------------------------------------------------------------------------------------------------------------

    def test_auto_rotate_levels_even_a_small_tilt_without_asking_or_placing_rois(self) -> None:
        self.image, self.truth = synth(tilt_deg=0.6, shape=(340, 500), origin=(80.0, 50.0))  # below the old 1 degree question: no question any more
        self.run_and_wait(self.actions.auto_rotate)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.toolbox.rois(), ())
        self.assertAlmostEqual(self.geometry.settings().rotation_angle_deg, 0.6, delta=0.15)
        self.assertTrue(self.statuses[-1].startswith("Auto rotate: levelled"), self.statuses[-1])
        # A second pass measures the image as it is now shown (rotated by the first): the residual tilt is small, so the rotation barely moves.
        first = self.geometry.settings().rotation_angle_deg
        self.run_and_wait(self.actions.auto_rotate)
        self.assertAlmostEqual(self.geometry.settings().rotation_angle_deg, first, delta=0.15)
        self.assertIn("already level", self.statuses[-1])

    def test_auto_rotate_reports_progress_and_the_end_under_its_own_task_id(self) -> None:
        self.run_and_wait(self.actions.auto_rotate)
        self.assertTrue(self.task_ids)
        self.assertEqual(set(self.task_ids), {TASK_ID})
        self.assertIn((TASK_ID, "completed"), self.outcomes)

    def test_auto_rotate_with_a_selected_roi_uses_it_as_the_guide_and_leaves_it_alone(self) -> None:
        self.image, self.truth = synth(tilt_deg=1.5)
        x, y = self.truth[(2, 3)]
        self.toolbox.add_roi(x, y, sample_diameter_px=20.0)
        before = [(r.center_x, r.center_y, r.shape) for r in self.toolbox.rois()]
        self.selection.set_roi_selection({1})
        self.run_and_wait(self.actions.auto_rotate)
        self.assertAlmostEqual(self.geometry.settings().rotation_angle_deg, 1.5, delta=0.15)
        self.assertEqual([(r.center_x, r.center_y, r.shape) for r in self.toolbox.rois()], before)

    def test_started_on_another_wavelength_asks_and_jumps_to_the_reference(self) -> None:
        self.selection.set_wavelength(500.0)
        self.answers = [False]
        self.run_and_wait(self.actions.auto_rotate)
        self.assertFalse(self.actions.is_running())  # declined: nothing ran
        self.assertEqual(self.selection.current_wavelength(), 500.0)
        self.assertIn("reference wavelength (600 nm)", self.asked[0])
        self.image, self.truth = synth(tilt_deg=0.8)
        self.answers = [True]
        self.run_and_wait(self.actions.auto_rotate)
        self.assertEqual(self.selection.current_wavelength(), REFERENCE_WL)
        self.assertAlmostEqual(self.geometry.settings().rotation_angle_deg, 0.8, delta=0.15)

    def test_auto_rotate_refusals_are_titled_and_shown(self) -> None:
        self.reference = None
        self.actions.auto_rotate()
        self.assertTrue(self.statuses[-1].startswith("Auto rotate:"), self.statuses[-1])
        self.assertTrue(any("reference frame" in text for text in self.told))
        self.assertFalse(self.actions.is_running())
        self.reference = (0, 0.0)  # the dark frame
        self.actions.auto_rotate()
        self.assertTrue(any("dark frame" in text for text in self.told))

    def test_no_array_in_the_image_is_reported_and_changes_nothing(self) -> None:
        self.image = np.full((200, 200), 500.0, dtype=np.float32)
        self.run_and_wait(self.actions.auto_rotate)
        self.assertEqual(self.geometry.settings().rotation_angle_deg, 0.0)
        self.assertEqual(self.toolbox.rois(), ())
        self.assertTrue(self.told)
        self.assertTrue(self.statuses[-1].startswith("Auto rotate:"))
        self.assertFalse(self.actions.is_running())

    def test_a_tilt_the_finder_could_have_mixed_up_is_not_applied(self) -> None:
        self.actions._finish_auto_rotate(types.SimpleNamespace(ordered=True, tilt_deg=MAX_TILT_DEG + 10.0))
        self.assertEqual(self.geometry.settings().rotation_angle_deg, 0.0)
        self.assertIn("too large to trust", self.statuses[-1])
        self.actions._finish_auto_rotate(types.SimpleNamespace(ordered=False, tilt_deg=float("nan")))  # a free set of spots has no tilt
        self.assertIn("no tilt could be measured", self.statuses[-1])
        self.assertEqual(self.geometry.settings().rotation_angle_deg, 0.0)

    def test_cancel_stops_the_search_and_says_so(self) -> None:
        self.actions.auto_rotate()
        self.assertTrue(self.actions.is_running())
        self.actions.cancel()
        self.assertTrue(_pump(lambda: not self.actions.is_running()))
        _pump(timeout=0.2)
        self.assertTrue(self.statuses[-1].startswith("Auto rotate:"), self.statuses[-1])  # "cancelled" (or done, if the search was faster than the click)

    def test_the_finder_pop_outs_settings_are_the_ones_it_runs_with(self) -> None:
        seen: list[object] = []
        original = self.actions._task.search

        def spy(key, seeds, settings, **kwargs):
            seen.append(settings)
            return original(key, seeds, settings, **kwargs)

        self.actions._task.search = spy
        from lspr_imaging_app.roi.array_finder import FinderSettings

        self.actions._finder_settings = lambda: FinderSettings(similarity=0.7, seeds_are_corners=True, place_missing=True)
        self.run_and_wait(self.actions.auto_rotate)
        self.assertEqual(seen[0].similarity, 0.7)
        self.assertFalse(seen[0].seeds_are_corners)  # a tilt needs the whole array, never the box between two corners
        self.assertFalse(seen[0].place_missing)

    # -- Auto crop ---------------------------------------------------------------------------------------------------------------

    def test_auto_crop_keeps_half_a_pitch_around_the_rois_and_nothing_else(self) -> None:
        # 4 x 6 spots, 70 px pitch, centres from (70, 60): half a pitch (35 px) of border on every side.
        self.image = np.zeros((320, 480), dtype=np.float32)
        self._add_lattice()
        before = [(r.center_x, r.center_y) for r in self.toolbox.rois()]
        self.actions.auto_crop()  # synchronous: no image is read, no thread
        crop = self.geometry.settings().crop
        self.assertTrue(crop.enabled)
        self.assertEqual((crop.x, crop.y), (70 - 35, 60 - 35))
        self.assertEqual((crop.width, crop.height), (6 * 70, 4 * 70))
        self.assertEqual(self.geometry.settings().rotation_angle_deg, 0.0)  # Auto crop never rotates
        self.assertIn("6 x 4 ROIs at pitch", self.statuses[-1])
        self.assertEqual(undo_manager.undo_label, "Crop")
        undo_manager.undo()
        self.assertFalse(self.geometry.settings().crop.enabled)
        self.assertEqual([(r.center_x, r.center_y) for r in self.toolbox.rois()], before)

    def test_auto_crop_run_twice_reproduces_the_same_rectangle(self) -> None:
        """A second run measures the full canvas, not the already-cropped image, so the crop is replaced rather than cut into again. Shown by moving the ROIs the way the
        app's `RoiGeometrySync` does on a crop edit (it is not wired into this test)."""
        self.image = np.zeros((320, 480), dtype=np.float32)
        self._add_lattice()
        self.actions.auto_crop()
        first = replace(self.geometry.settings().crop)
        dx, dy = float(first.x), float(first.y)
        self.toolbox.remap_all(lambda x, y: (x - dx, y - dy), lambda mask: mask)
        self.actions.auto_crop()
        second = self.geometry.settings().crop
        self.assertEqual((second.x, second.y, second.width, second.height), (first.x, first.y, first.width, first.height))

    def test_auto_crop_keeps_the_edge_rois_inside_when_the_ring_reaches_past_half_the_pitch(self) -> None:
        """A 40 px pitch with 26 px disks: the default rim reaches 21 px from the centre, past the 20 px half-pitch, so the margin grows rather than cutting measurement
        pixels off the outermost spots."""
        self.image = np.zeros((280, 360), dtype=np.float32)
        self._add_lattice(rows=3, cols=4, pitch=40.0, origin=(90.0, 80.0))
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
        """2026-10-21 report: "the leveling array is still present even though I run it" - Auto rotate comes first in the maintainer's order of work, when there is no ROI
        lattice to measure the tilt on."""
        self.image, self.truth = synth(tilt_deg=0.6, shape=(340, 500), origin=(80.0, 50.0))
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
        self.run_and_wait(self.actions.auto_rotate)  # a level image: "already level" (tilt below the field's resolution) or a hair's levelling
        self.assertTrue(self.actions.auto_crop_readiness().levelled)

    def test_the_measured_lattice_overrules_what_auto_rotate_remembered(self) -> None:
        """Once the ROIs are there the tick is a measurement again, so a rotation made in between cannot leave a stale tick - and the ROI lattice has the last word."""
        self._add_tilted_lattice(3.0)
        self.actions._levelled_at = self.geometry.settings().rotation_angle_deg  # as if Auto rotate had run
        readiness = self.actions.auto_crop_readiness()
        self.assertFalse(readiness.levelled)
        self.assertIn("tilt", readiness.level_note)

    def test_auto_crop_readiness_measures_each_dependency(self) -> None:
        empty = self.actions.auto_crop_readiness()
        self.assertEqual((empty.levelled, empty.rois, empty.chromatic, empty.ready), (None, False, False, False))
        self._add_lattice()
        level = self.actions.auto_crop_readiness()
        self.assertTrue(level.levelled)  # a level lattice: the tick is earned, not assumed
        self.assertAlmostEqual(level.tilt_deg, 0.0, delta=0.05)
        self.assertEqual((level.rois, level.chromatic, level.ready), (True, False, False))
        self.toolbox.delete_rois(tuple(r.area_roi_id for r in self.toolbox.rois()))
        self._add_tilted_lattice(3.0)  # measured from the ROI centres, so the tick is withheld
        tilted = self.actions.auto_crop_readiness()
        self.assertFalse(tilted.levelled)
        self.assertAlmostEqual(tilted.tilt_deg, 3.0, delta=0.2)

    def test_readiness_never_walks_the_fitted_chromatic_models(self) -> None:
        """2026-10-21 regression: the readiness check unioned every ROI's read box over every fitted chromatic affine. In the maintainer's 314-cube session (8478 fitted
        models) that cost ~300 ms **per sample of a ROI drag**. It only ever needed the ROI centres."""
        self._add_lattice()
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
        self._add_lattice()
        _pump(timeout=0.4)  # (the placing itself starts the timer)
        ids = tuple(r.area_roi_id for r in self.toolbox.rois())
        emitted: list[object] = []
        self.actions.auto_crop_readiness_changed.connect(emitted.append)
        for _ in range(20):
            self.toolbox.translate_rois(ids, 1.0, 0.0)
        self.assertEqual(emitted, [])  # nothing yet: it waits for the drag to go quiet
        _pump(timeout=0.5)
        self.assertEqual(len(emitted), 1)

    def test_the_rotation_group_button_asks_for_auto_rotate_and_the_crop_button_guides_when_not_ready(self) -> None:
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
        section._auto_rotate_button.click()
        self.assertEqual(rotates, [True])
        self.assertFalse(section._auto_rotate_button.icon().isNull())

        # Nothing done yet: amber, all three steps offered, and a click guides instead of cropping.
        section.set_auto_crop_state(levelled=None, rois=False, chromatic=False, level_note="run Auto rotate, or place the ROIs to measure it")
        self.assertEqual(len(section._auto_crop_menu.actions()), 3)
        self.assertIn("✗ Level the array", section._auto_crop_button.toolTip())
        section._auto_crop_button.click()
        self.addCleanup(section._auto_crop_menu.close)
        self.assertEqual(crops, [])
        rows = section._auto_crop_menu.actions()
        rows[0].trigger()
        self.assertEqual(rotates, [True, True])  # "Level the array" runs Auto rotate here
        rows[1].trigger()
        rows[2].trigger()
        self.assertEqual(navigations, ["rois", "chromatic"])  # the other two are a trip to another tab

        # All three met: green, no menu rows, and the click crops.
        section.set_auto_crop_state(levelled=True, rois=True, chromatic=True, level_note="tilt +0.03°")
        self.assertEqual(section._auto_crop_menu.actions(), [])
        self.assertIn("Click to crop.", section._auto_crop_button.toolTip())
        section._auto_crop_button.click()
        self.assertEqual(crops, [True])


if __name__ == "__main__":
    unittest.main()
