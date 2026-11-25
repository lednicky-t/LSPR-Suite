"""Find* (the array finder's pop-out): a real `ArrayFindPanel`, `ArrayFindController`, `ArrayFindTask` (real threads), `RoiToolbox` and a pyqtgraph plot on a synthetic image.

No dataset and nothing drawn to the screen is looked at: the checks read state (outlines counted, messages, ROIs made)."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))
from tests._paths import ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

try:
    import numpy as np  # noqa: F401
    import pyqtgraph as pg
    from PyQt6.QtWidgets import QApplication, QWidget

    from _array_synth import synth
    from lspr_imaging_app.domain.roi_shapes import Border, Circle
    from lspr_imaging_app.image_tools import ChromaticModule, GeometryModule
    from lspr_imaging_app.image_tools.background.module import BackgroundModule
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.image_tools.mask.module import MaskModule
    from lspr_imaging_app.panels.image.array_find import ArrayFindController
    from lspr_imaging_app.panels.image.array_find_panel import ArrayFindPanel, strictness_values
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.array_find_task import ArrayFindTask
    from lspr_imaging_app.selection import AreaSelectionModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QApplication.instance() or QApplication([])
IMAGE, CENTRES = synth(rows=4, cols=5, missing=((1, 1),), tilt_deg=1.0)


def _wait(controller: "ArrayFindController", seconds: float = 20.0) -> None:
    """Run the event loop until the search and the build have answered (the threads' results arrive as queued signals)."""
    controller._timer.stop()
    controller._recompute()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _APP.processEvents()
        if not controller._pending and not controller._task.is_searching():
            _APP.processEvents()
            return
        time.sleep(0.005)
    raise AssertionError("the finder did not answer in time")


class StrictnessTest(unittest.TestCase):
    def test_the_middle_of_the_slider_is_the_default_set(self) -> None:
        similarity, strength, position, size = strictness_values(0.5)
        self.assertAlmostEqual(similarity, 0.6)
        self.assertAlmostEqual(strength, 0.35)
        self.assertAlmostEqual(position, 0.05)
        self.assertAlmostEqual(size, 0.25)
        strict, tolerant = strictness_values(0.0), strictness_values(1.0)
        self.assertGreater(strict[0], tolerant[0])  # strict wants more similarity ...
        self.assertLess(strict[2], tolerant[2])  # ... and less distance from the node

    def test_the_slider_sets_the_numbers_and_the_numbers_alone_set_nothing_else(self) -> None:
        panel = ArrayFindPanel()
        seen: list[int] = []
        panel.settings_changed.connect(lambda: seen.append(1))
        panel.strictness.setValue(100)
        self.assertAlmostEqual(panel.settings().similarity, 0.4)
        self.assertAlmostEqual(panel.settings().position_tol, 0.1625, delta=0.005)  # (the field shows whole per cent)
        panel.similarity.setValue(0.9)
        self.assertEqual(panel.strictness.value(), 100)  # editing a number leaves the slider where it is
        self.assertEqual(panel.settings().similarity, 0.9)
        self.assertEqual(len(seen), 2)  # one report per change, not one per number the slider moved


class StatsRowsTest(unittest.TestCase):
    """What was found is read from rows, in the unit the app shows lengths in."""

    @classmethod
    def setUpClass(cls) -> None:
        from lspr_imaging_app.roi.array_finder import FinderSeed, FinderSettings, build_array, find_candidates

        x, y = CENTRES[(0, 0)]
        settings = FinderSettings()
        cls.result = build_array(find_candidates(IMAGE, (FinderSeed(x, y, 20.0, Circle(20.0)),), settings), settings)

    def test_each_number_has_a_row_and_lengths_follow_px_or_um(self) -> None:
        geometry = GeometryModule()
        panel = ArrayFindPanel(geometry)
        panel.set_result(self.result)
        values = {key: label.text() for key, label in panel._stat_values.items()}
        self.assertTrue(values["array"].startswith("4 × 5"))
        self.assertRegex(values["pitch"], r"^44\.\d × 44\.\d px$")  # the unit once, at the end
        self.assertEqual(values["size"], "20.0 px")
        self.assertIn("1 empty", values["spots"])
        self.assertTrue(panel._steps.text().startswith("Steps:"))
        geometry.restore_settings(GeometrySettings(calibration_enabled=True, microns_per_pixel_x=0.5, microns_per_pixel_y=0.5, display_units="um"))
        self.assertRegex(panel._stat_values["pitch"].text(), r"^22\.\d\d × 22\.\d\d µm$")  # rewritten when the unit changed anywhere in the app
        self.assertEqual(panel._stat_values["size"].text(), "10.00 µm")
        panel.set_result(None)
        self.assertFalse(panel._stat_values["array"].isVisibleTo(panel) and panel._stats.isVisibleTo(panel))


class ControllerTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.area = AreaSelectionModule()
        self.host = QWidget()
        self.plot = pg.PlotItem()
        self.task = ArrayFindTask()
        self.mask = MaskModule()
        self.statuses: list[str] = []
        self.searches = 0
        original = self.task.search

        def counted(*args, **kwargs):
            self.searches += 1
            return original(*args, **kwargs)

        self.task.search = counted  # type: ignore[method-assign]
        self.controller = ArrayFindController(
            plot=self.plot, toolbox=self.toolbox, selection=self.selection, area_selection=self.area, image_shape=lambda: IMAGE.shape,
            chromatic=ChromaticModule(), geometry=GeometryModule(), background=BackgroundModule(), mask=self.mask, task=self.task, current_frame=lambda: (0, 600.0),
            resolve_reference=lambda: (0, 600.0), load_plane=lambda _cube, _wavelength: IMAGE, has_dataset=lambda: True, host=self.host,
        )
        self.controller.status.connect(self.statuses.append)
        x, y = CENTRES[(0, 0)]
        self.seed = self.toolbox.add_shaped_roi(x + 0.5, y - 0.5, Circle(20.0), border=Border(2.0, 3.0))
        undo_manager.clear()

    def tearDown(self) -> None:
        self.controller.close()
        self.task.shutdown()
        self.host.close()
        undo_manager.clear()

    def test_one_selected_roi_previews_the_array_and_apply_makes_it_in_one_undo_step(self) -> None:
        self.selection.set_roi_selection({self.seed})
        self.controller.open()
        _wait(self.controller)
        self.assertEqual(self.controller.ghost_count(), 18 + 1)  # 18 new ROIs + the one empty node
        self.assertTrue(self.controller.panel.apply_button.isEnabled())
        self.assertIn("4 × 5", self.controller.panel._stat_values["array"].text())
        self.controller.apply()
        self.assertEqual(len(self.toolbox.rois()), 19)
        self.assertFalse(self.controller.is_open())
        undo_manager.undo()
        self.assertEqual(len(self.toolbox.rois()), 1)

    def test_a_slider_move_builds_again_without_a_new_search(self) -> None:
        self.selection.set_roi_selection({self.seed})
        self.controller.open()
        _wait(self.controller)
        self.assertEqual(self.searches, 1)
        self.controller.panel.strictness.setValue(90)
        _wait(self.controller)
        self.controller.panel.place_missing.setChecked(True)
        _wait(self.controller)
        self.assertEqual(self.searches, 1)  # the numbers act on the cached candidates
        self.assertEqual(self.controller.ghost_count(), 19)  # the empty node is now a ROI to make, not a grey outline

    def test_a_mask_is_part_of_the_search_and_the_switch_decides_whether_it_counts(self) -> None:
        masked = np.zeros(IMAGE.shape, dtype=bool)
        masked[100:120, 100:120] = True
        self.mask.set_mask_change((0, 600.0), "persistent", masked)
        self.selection.set_roi_selection({self.seed})
        self.controller.open()
        _wait(self.controller)
        self.assertEqual(self.searches, 1)
        self.assertIn("masked pixels", self.controller.panel.message())
        self.controller.panel.strictness.setValue(70)  # a slider does not search again
        _wait(self.controller)
        self.assertEqual(self.searches, 1)
        self.controller.panel.use_mask.setChecked(False)  # the picture the search reads changes
        _wait(self.controller)
        self.assertEqual(self.searches, 2)
        self.assertNotIn("masked pixels", self.controller.panel.message())

    def test_no_guide_still_finds_the_array(self) -> None:
        self.controller.panel.fix_size.setChecked(False)
        self.controller.open()
        _wait(self.controller)
        self.assertTrue(self.controller.panel.apply_button.isEnabled())
        self.assertEqual(self.controller.panel.guide.text(), "Guide: none")
        self.assertIn("most common", self.controller.panel.guide.toolTip())

    def test_an_area_selection_is_a_guide(self) -> None:
        x, y = CENTRES[(0, 0)]
        self.area.set_rectangle(x - 12, y - 12, x + 12, y + 12, shape=IMAGE.shape)
        self.controller.open()
        _wait(self.controller)
        self.assertIn("area selection", self.controller.panel.guide.text())
        self.assertTrue(self.controller.panel.apply_button.isEnabled())

    def test_a_seed_that_is_in_an_array_is_refused_with_a_reason(self) -> None:
        self.selection.set_roi_selection({self.seed})
        self.controller.open()
        _wait(self.controller)
        self.controller.apply()
        member = self.toolbox.rois()[0].area_roi_id
        self.selection.set_roi_selection({member})
        self.controller.open()
        _wait(self.controller)
        self.assertIn("already belongs", self.controller.panel.message())
        self.assertFalse(self.controller.panel.apply_button.isEnabled())
        self.assertEqual(self.controller.ghost_count(), 0)

    def test_closing_makes_nothing_and_clears_the_outlines(self) -> None:
        self.selection.set_roi_selection({self.seed})
        self.controller.open()
        _wait(self.controller)
        self.controller.close()
        self.assertEqual(self.controller.ghost_count(), 0)
        self.assertEqual(len(self.toolbox.rois()), 1)

    def test_settings_are_remembered(self) -> None:
        saved: list[dict] = []
        store = UiStateStore({"array_find/strictness": 0.8, "array_find/place_missing": True, "array_find/number_by": "columns"}, on_changed=saved.append)
        self.controller.bind_ui_state(store)
        self.assertEqual(self.controller.panel.strictness.value(), 80)
        self.assertTrue(self.controller.panel.place_missing.isChecked())
        self.assertEqual(self.controller.panel.numbering.by(), "columns")
        self.controller.panel.fix_size.setChecked(False)
        store.flush()  # (the store writes after a short pause; flush is what quitting does)
        self.assertIs(saved[-1]["array_find/fix_size"], False)


if __name__ == "__main__":
    unittest.main()
