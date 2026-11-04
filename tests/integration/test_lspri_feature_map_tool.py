"""The Mask tab's "Feature map" tool on a real `ImagePanel` (2026-11-04): Process, kinds, preview, Apply, and when a result is dropped.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see `test_lspri_rewrite_analysis_core.py`'s docstring.

The dataset is the synthetic disk array of `tests/unit/test_lspri_feature_map.py` (7 x 5 disks with a plasmon-like contrast spectrum,
injected dirt, a stub on a disk edge, a notch, extra nanostructure, a damaged band, bright and faint dirt), written as two cubes of 10
wavelengths (two noise draws: the tool averages them). Process runs on 2 worker processes (`set_worker_processes(2)`), and the tests
share the slow part (`prepare`): every test writes the same files, the task's own keep is keyed by frame, cubes, geometry and chromatic
matrices, so reusing it is exact. A full run per test (~14 s) timed out next to other files. The tests that need a full run (the
reuse test itself, Cancel, a change while running) do one. Worker counts are pinned by `tests/unit/test_lspri_feature_map_run.py`.

What is pinned: Process maps the injected kinds and the preview shows exactly the enabled kinds' pixels; a kind's check box acts at
once, without a new run; moving ROIs keeps the result (the map has no ROIs), a new rotation or chromatic model drops it, and one while
it runs refuses it; Apply adds the enabled kinds at the scope the toggle says, merged, in one mask change, inside the area selection;
a second run with other limits reuses the slow part and the same limits give the same pixels back; the settings are remembered; the
tool is in the picker and a remembered "roi_features" choice still opens it. A real `QApplication` in-process, never `.exec()`.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

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
        MaskEditTool,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.image_tools.mask import feature_map as fm
    from lspr_imaging_app.image_tools.mask_scope import MaskScope
    from lspr_imaging_app.panels.feature_map_task import FeatureMapTask, choose_cubes
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from tests.unit import test_lspri_feature_map as synth
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

WAVELENGTHS = [500.0 + 10.0 * i for i in range(synth.N_WL)]
FRAME = (0, WAVELENGTHS[0])
OTHER_FRAME = (0, WAVELENGTHS[1])
SHAPE = (synth.H, synth.W)


def _pump_until(condition, seconds: float = 120.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def _pump(seconds: float = 0.3) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    for cube, seed in ((0, 2), (1, 4)):
        logs = synth.picture(seed=seed)
        for w, wavelength in enumerate(WAVELENGTHS):
            intensity = np.where(np.isfinite(logs[w]), 1000.0 * np.exp(logs[w]), 0.0).astype(np.float32)  # no data -> 0 (no light)
            path = root / f"c{cube}_w{int(wavelength)}.tif"
            tifffile.imwrite(str(path), intensity)
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


class FeatureMapToolTest(unittest.TestCase):
    _shared_keep = None  # the slow part of the first full run, handed to later tests (see the module docstring)

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.dataset_model = _write_dataset(self.root)
        self.dataset = DatasetModule()
        self.mask = MaskModule()
        self.scope = MaskScopeModule()
        self.selection = SelectionModule()
        self.geometry = GeometryModule()
        self.chromatic = ChromaticModule()
        self.toolbox = RoiToolbox()
        self.panel = ImagePanel(
            self.dataset, self.geometry, self.mask, self.chromatic, BackgroundModule(), self.toolbox,
            self.selection, ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(), mask_scope=self.scope,
        )
        self.tab = self.panel._mask_tab
        self.tool = self.tab.features_panel
        self.task: FeatureMapTask = self.panel.feature_map
        self.task.set_worker_processes(2)  # a small pool: the default (every core but one) per run timed out next to other files
        self.preview = self.tab.edit_preview
        self.layer = self.panel._edit_preview_layer
        self.messages: list[str] = []
        self.summaries: list = []
        self.task.completed.connect(lambda summary: (self.messages.append("completed"), self.summaries.append(summary)))
        self.task.failed.connect(lambda message: self.messages.append(message))

    def tearDown(self) -> None:
        self.task.shutdown()
        self.panel._renderer.stop()
        self._tmp.cleanup()

    def _load(self) -> None:
        self.dataset.load_dataset(self.dataset_model)
        _pump_until(lambda: self.panel._image_item.image is not None and self.panel._image_item.levels is not None, 20.0)
        _pump()

    def _open_tool(self) -> None:
        self.tab.edit_tool.set_tool(MaskEditTool.FEATURE_MAP)
        self.panel._tool_ribbon.set_category("Mask")

    def _process(self, *, full: bool = False) -> None:
        if not full and FeatureMapToolTest._shared_keep is not None and self.task._keep is None:
            self.task._keep = FeatureMapToolTest._shared_keep  # same files, same key: the task reuses it only if the key matches
        before = len(self.messages)
        self.tool._process_button.click()
        self.assertTrue(_pump_until(lambda: len(self.messages) > before and not self.task.is_running()), "the run did not finish")
        self.assertEqual(self.messages[-1], "completed", self.messages[-1])
        if FeatureMapToolTest._shared_keep is None and self.task._keep is not None and self.task._keep.key[0] == FRAME:
            FeatureMapToolTest._shared_keep = self.task._keep

    def _kind_at(self, kind: str, region: np.ndarray) -> bool:
        return bool(self.task.result().kind_pixels[kind][region].any())

    # -- Process, kinds and the preview -------------------------------------------------------------------------------

    def test_process_maps_the_injected_kinds_and_previews_the_enabled_ones(self) -> None:
        self._load()
        self._open_tool()
        self.assertIsNone(self.preview.current())  # nothing found yet
        self._process()
        result = self.task.result()
        self.assertEqual(result.frame, FRAME)
        self.assertEqual(result.cubes, (0, 1))  # both cubes averaged (12 asked, 2 there)
        self.assertEqual(result.disks, len(synth.LATTICE))
        self.assertTrue(self._kind_at(fm.DIRT_DARK, synth._spot(synth.SPECK, 1.5)))
        self.assertTrue(self._kind_at(fm.STRUCTURE_MISSING, synth._spot(synth.NOTCH, 2.0)))
        self.assertTrue(self._kind_at(fm.STRUCTURE_EXTRA, synth._spot(synth.EXTRA, 1.5)))
        self.assertTrue(self._kind_at(fm.DIRT_BRIGHT, synth._spot(synth.BRIGHT, 1.5)))
        self.assertTrue(self._kind_at(fm.DIRT_FAINT, synth._spot(synth.FAINT, 2.0)))
        for kind, pixels in result.kind_pixels.items():
            self.assertEqual(pixels.shape, SHAPE, kind)  # raw pixels of the frame
        enabled = self.task.pixels_for(FRAME)
        expected = np.zeros(SHAPE, dtype=bool)
        for kind in fm.FeatureMapSettings().kinds:  # every kind but faint dirt, by default
            expected |= result.kind_pixels[kind]
        np.testing.assert_array_equal(enabled, expected)
        self.assertFalse(enabled[synth._spot(synth.FAINT, 2.0)].any())  # faint dirt is not added by default
        self.assertTrue(_pump_until(lambda: self.preview.current() is not None, 5.0))
        np.testing.assert_array_equal(self.preview.current().pixels, enabled)
        self.assertTrue(self.layer.item.isVisible())

    def test_a_kind_check_box_acts_at_once_without_a_new_run(self) -> None:
        self._load()
        self._open_tool()
        self._process()
        runs = len(self.summaries)
        notch = synth._spot(synth.NOTCH, 2.0)
        self.assertTrue(self.task.pixels_for(FRAME)[notch].any())
        self.tool.popover().kind_checks[fm.STRUCTURE_MISSING].setChecked(False)
        self.assertFalse(self.task.pixels_for(FRAME)[notch].any())
        self.assertTrue(_pump_until(lambda: self.preview.current() is not None and not self.preview.current().pixels[notch].any(), 5.0))
        self.tool.popover().kind_checks[fm.DIRT_FAINT].setChecked(True)
        self.assertTrue(self.task.pixels_for(FRAME)[synth._spot(synth.FAINT, 2.0)].any())
        self.assertEqual(len(self.summaries), runs)  # no run was needed

    def test_the_preview_follows_the_frame_it_was_made_for(self) -> None:
        self._load()
        self._open_tool()
        self._process()
        self.assertTrue(_pump_until(lambda: self.preview.current() is not None, 5.0))
        self.selection.set_wavelength(OTHER_FRAME[1])
        self.assertTrue(_pump_until(lambda: self.preview.frame() == OTHER_FRAME, 10.0))
        self.assertIsNone(self.preview.current())
        self.assertFalse(self.tool._apply_button.isEnabled())
        self.assertIn(f"{FRAME[1]:g}", self.tool._apply_button.toolTip())
        self.selection.set_wavelength(FRAME[1])
        self.assertTrue(_pump_until(lambda: self.preview.current() is not None, 10.0))

    # -- what keeps and what drops the result ----------------------------------------------------------------------------

    def test_moving_rois_keeps_the_result(self) -> None:
        self._load()
        self._open_tool()
        self._process()
        first = self.task.result()
        self.toolbox.add_roi(100.0, 100.0, sample_diameter_px=30.0)  # the map has no ROIs: a ROI edit does not touch it
        _pump(0.2)
        self.assertIs(self.task.result(), first)
        self.assertTrue(self.tool._apply_button.isEnabled())

    def test_a_new_rotation_or_chromatic_model_drops_the_result(self) -> None:
        self._load()
        self._open_tool()
        self._process()
        self.geometry.set_rotation(1.0)
        _pump(0.2)
        self.assertIsNone(self.task.result())
        self.assertFalse(self.tool._apply_button.isEnabled())
        self.geometry.set_rotation(0.0)
        _pump_until(lambda: self.panel._image_item.image is not None, 10.0)
        _pump()
        self._process()
        self.chromatic.set_correction_enabled(True)  # a real user action: the chromatic correction switched on
        self.assertIsNone(self.task.result())

    def test_a_change_while_it_runs_refuses_the_result(self) -> None:
        self._load()
        self._open_tool()
        before = len(self.messages)
        self.tool._process_button.click()
        self.assertTrue(self.task.is_running())
        self.assertTrue(self.tool._shows_stop)  # one run at a time: the button is now Stop
        self.chromatic.set_correction_enabled(True)  # the picture the run started with is gone (a real user action)
        self.assertTrue(_pump_until(lambda: len(self.messages) > before and not self.task.is_running()))
        self.assertIn("changed while it ran", self.messages[-1])
        self.assertIsNone(self.task.result())

    def test_the_mask_is_not_looked_at_so_a_mask_edit_keeps_the_result(self) -> None:
        self._load()
        self._open_tool()
        self._process()
        first = self.task.result()
        self.mask.set_mask_change(FRAME, "persistent", np.ones(SHAPE, dtype=bool))
        _pump(0.2)
        self.assertIs(self.task.result(), first)

    def test_the_run_button_turns_into_stop_and_stops_the_run(self) -> None:
        self._load()
        self._open_tool()
        self.assertFalse(self.tool._shows_stop)  # idle: the green play
        self.assertIn("Map every small feature", self.tool._process_button.toolTip())
        before = len(self.messages)
        cancelled = []
        self.task.cancelled.connect(lambda: cancelled.append(True))
        self.tool._process_button.click()  # Run
        self.assertTrue(self.task.is_running())
        self.assertTrue(self.tool._shows_stop)  # the red stop
        self.assertTrue(self.tool._process_button.isEnabled())
        self.assertIn("Stop the feature map", self.tool._process_button.toolTip())
        self.assertFalse(self.tool._settings_button.isEnabled())
        self.tool._process_button.click()  # Stop
        self.assertTrue(_pump_until(lambda: not self.task.is_running()))
        _pump(0.1)
        self.assertEqual(cancelled, [True])
        self.assertEqual(len(self.messages), before)  # neither completed nor failed
        self.assertIsNone(self.task.result())
        self.assertIsNone(self.mask.resolve_mask_source(FRAME))
        self.assertFalse(self.tool._shows_stop)  # back to Run
        self.assertTrue(self.tool._settings_button.isEnabled())

    def test_stopping_keeps_an_earlier_result(self) -> None:
        self._load()
        self._open_tool()
        self._process()
        first = self.task.result()
        self.tool.popover().cubes_spin.setValue(1)  # another cube count: a full run
        self.tool._process_button.click()
        self.assertTrue(self.task.is_running())
        self.tool.stop()
        self.assertTrue(_pump_until(lambda: not self.task.is_running()))
        self.assertIs(self.task.result(), first)

    def test_cancel_changes_nothing(self) -> None:
        self._load()
        self._open_tool()
        self.tool._process_button.click()
        self.task.cancel()
        self.assertTrue(_pump_until(lambda: not self.task.is_running()))
        _pump(0.1)
        self.assertIsNone(self.task.result())
        self.assertIsNone(self.mask.resolve_mask_source(FRAME))

    def test_a_new_dataset_drops_the_result(self) -> None:
        self._load()
        self._open_tool()
        self._process()
        self.dataset.clear_dataset()
        _pump(0.2)
        self.assertIsNone(self.task.result())

    def test_no_dataset_and_too_few_wavelengths_say_so(self) -> None:
        task = FeatureMapTask(GeometryModule(), ChromaticModule(), DatasetModule(), processes=1)
        said: list[str] = []
        task.failed.connect(said.append)
        task.start(FRAME, fm.FeatureMapSettings())
        self.assertIn("Load a dataset", said[-1])
        self._load()
        two = FeatureMapTask(GeometryModule(), ChromaticModule(), self.dataset, lambda c, w: w < 520.0, processes=1)
        two.failed.connect(said.append)
        two.start(FRAME, fm.FeatureMapSettings())
        self.assertIn("at least 3 wavelengths", said[-1])

    # -- Apply -----------------------------------------------------------------------------------------------------------

    def test_apply_adds_the_enabled_kinds_at_the_persistent_scope(self) -> None:
        self._load()
        self._open_tool()
        self.scope.set_scope(MaskScope.PERSISTENT)
        self._process()
        found = np.array(self.task.pixels_for(FRAME))
        self.tool._apply_button.click()
        _pump(0.2)
        _frame, mask, scope = self.mask.resolve_mask_source(FRAME)
        self.assertEqual(scope, "persistent")
        np.testing.assert_array_equal(mask, found)

    def test_apply_merges_with_the_mask_in_effect_at_the_individual_scope_in_one_change(self) -> None:
        self._load()
        self._open_tool()
        existing = np.zeros(SHAPE, dtype=bool)
        existing[5:15, 300:310] = True
        self.mask.set_mask_change(FRAME, "individual", existing)
        self.scope.set_scope(MaskScope.INDIVIDUAL)
        self._process()
        found = np.array(self.task.pixels_for(FRAME))
        changes = []
        self.mask.mask_changed.connect(changes.append)
        self.tool._apply_button.click()
        _pump(0.2)
        _frame, mask, scope = self.mask.resolve_mask_source(FRAME)
        self.assertEqual(scope, "individual")
        np.testing.assert_array_equal(mask, existing | found)  # merged with what was there, nothing taken away
        self.assertEqual(len(changes), 1)

    def test_apply_stays_inside_the_area_selection(self) -> None:
        self._load()
        self._open_tool()
        self._process()
        found = np.array(self.task.pixels_for(FRAME))
        region = np.zeros(SHAPE, dtype=bool)
        region[:, : SHAPE[1] // 2] = True
        self.panel.selection_raw_mask = lambda shape: region  # the area selection, as the editors ask for it
        self.tool._apply_button.click()
        _pump(0.2)
        _frame, mask, _scope = self.mask.resolve_mask_source(FRAME)
        np.testing.assert_array_equal(mask, found & region)

    # -- the settings ------------------------------------------------------------------------------------------------------

    def test_other_limits_reuse_the_slow_part_and_the_same_limits_give_the_same_pixels(self) -> None:
        self._load()
        self._open_tool()
        self._process(full=True)
        first = np.array(self.task.pixels_for(FRAME))
        self.tool.popover().margin_spin.setValue(3)
        self.assertIn("settings changed", self.tool._process_button.toolTip())
        self._process()
        self.assertFalse(self.summaries[0].kept)
        self.assertTrue(self.summaries[1].kept)
        self.assertLess(self.summaries[1].seconds, self.summaries[0].seconds)
        self.assertGreater(int(self.task.pixels_for(FRAME).sum()), int(first.sum()))  # a bigger margin
        self.tool.popover().margin_spin.setValue(1)
        self._process()
        np.testing.assert_array_equal(self.task.pixels_for(FRAME), first)

    def test_the_settings_are_remembered_and_have_working_defaults(self) -> None:
        pop = self.tool.popover()
        self.assertEqual(self.tool.settings().dirt_min, 0.02)
        store = UiStateStore(
            {
                "image/feature_map/dirt_percent": 3.5, "image/feature_map/structure_percent": 20, "image/feature_map/certainty": 6.0,
                "image/feature_map/edge_percent": 40, "image/feature_map/margin_px": 2, "image/feature_map/cubes": 5,
                "image/feature_map/kind_dirt_faint": True, "image/feature_map/kind_dirt_bright": False,
            }
        )
        self.tool.bind_ui_state(store)
        s = self.tool.settings()
        self.assertEqual((s.dirt_min, s.structure_min, s.certainty, s.core_share, s.grow_px), (0.035, 0.2, 6.0, 0.4, 2))
        self.assertEqual(pop.cubes(), 5)
        self.assertIn(fm.DIRT_FAINT, self.task.kinds())  # the task follows the remembered kinds
        self.assertNotIn(fm.DIRT_BRIGHT, self.task.kinds())
        pop.certainty_spin.setValue(7.0)  # a later change is written down
        self.assertEqual(store.get("image/feature_map/certainty"), 7.0)
        pop.reset_button.click()
        self.assertEqual(self.tool.settings(), fm.FeatureMapSettings())
        self.assertEqual(self.task.kinds(), fm.FeatureMapSettings().kinds)

    def test_the_tool_is_in_the_picker_and_a_remembered_choice_still_opens_it(self) -> None:
        labels = [action.text() for action in self.tab._edit_picker._menu.actions()]
        self.assertIn("Feature map", labels)
        self.assertEqual(MaskEditTool("roi_features"), MaskEditTool.FEATURE_MAP)  # the value stored by the former ROI features tool
        self.tab.edit_tool.set_tool(MaskEditTool.FEATURE_MAP)
        self.assertIs(self.tab._edit_stack.currentWidget(), self.tool)


class ChooseCubesTest(unittest.TestCase):
    def test_from_the_cube_on_screen_on_and_made_up_from_before_at_the_end(self) -> None:
        self.assertEqual(choose_cubes(range(10), 2, 3), (2, 3, 4))
        self.assertEqual(choose_cubes(range(10), 8, 4), (6, 7, 8, 9))
        self.assertEqual(choose_cubes(range(3), 0, 12), (0, 1, 2))
        self.assertEqual(choose_cubes([], 0, 3), ())


if __name__ == "__main__":
    unittest.main()
