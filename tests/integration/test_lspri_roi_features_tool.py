"""The Mask tab's "ROI features" tool on a real `ImagePanel` (2026-10-29): Process, preview, Apply, and when a result is dropped.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see `test_lspri_rewrite_analysis_core.py`'s docstring.

The dataset is the synthetic array of `tests/unit/test_lspri_roi_features.py` written as two frames (500 and 600 nm): 16 dark disks,
ROI 5 with a dark spot on its reference ring and ROI 9 with a bright streak across its sample disk, and 16 ROIs placed on them.
Process really runs on worker processes (the tuned count of `lspr_core.perf`), so these tests take a few seconds each.

What is pinned: the tool finds those two ROIs and previews exactly the pixels it holds; Apply adds them to the mask in effect at the
frame, at the scope the toggle says, as one undo step; Apply is off until a result exists for the frame on screen; any change of the
ROIs drops the result, and a change while it runs refuses it; a second run with other numbers reuses the fit; the numbers are remembered.
A real `QApplication` in-process, never `.exec()`.
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
    from lspr_imaging_app.image_tools.mask_scope import MaskScope
    from lspr_imaging_app.image_tools.roi_feature_task import RoiFeatureTask
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.model import AreaRoiDetectionSettings
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from tests.unit.test_lspri_roi_features import _grid_centres, build_image
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

FRAME = (0, 500.0)
OTHER_FRAME = (0, 600.0)


def _pump_until(condition, seconds: float = 90.0) -> bool:
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
    for seed, wavelength in ((5, 500.0), (6, 600.0)):
        path = root / f"c0_w{int(wavelength)}.tif"
        image, _valid = build_image(seed=seed)
        tifffile.imwrite(str(path), image)
        records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=0), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


def _toolbox_with_the_array() -> RoiToolbox:
    toolbox = RoiToolbox()
    toolbox.set_detection_settings(AreaRoiDetectionSettings(reference_inner_diameter_px=48.0, reference_outer_diameter_px=58.0))
    for cx, cy in _grid_centres():  # ROI k = 4 j + i + 1, the ids the synthetic features are named by
        toolbox.add_roi(cx, cy, sample_diameter_px=32.0)
    return toolbox


class RoiFeaturesToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.dataset_model = _write_dataset(self.root)
        self.dataset = DatasetModule()
        self.mask = MaskModule()
        self.scope = MaskScopeModule()
        self.selection = SelectionModule()
        self.toolbox = _toolbox_with_the_array()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), self.mask, ChromaticModule(), BackgroundModule(), self.toolbox,
            self.selection, ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(), mask_scope=self.scope,
        )
        self.tab = self.panel._mask_tab
        self.tool = self.tab.features_panel
        self.task: RoiFeatureTask = self.panel.roi_features
        self.preview = self.tab.edit_preview
        self.layer = self.panel._edit_preview_layer
        self.messages: list[str] = []
        self.task.completed.connect(lambda summary: self.messages.append("completed"))
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
        self.tab.edit_tool.set_tool(MaskEditTool.ROI_FEATURES)
        self.panel._tool_ribbon.set_category("Mask")

    def _process(self) -> None:
        before = len(self.messages)
        self.tool._process_button.click()
        self.assertTrue(_pump_until(lambda: len(self.messages) > before and not self.task.is_running()), "the run did not finish")

    # -- Process and the preview -------------------------------------------------------------------------------------

    def test_process_finds_the_injected_features_and_previews_exactly_those_pixels(self) -> None:
        self._load()
        self._open_tool()
        self.assertIsNone(self.preview.current())  # nothing found yet
        self._process()
        self.assertEqual(self.messages[-1], "completed")
        result = self.task.result()
        self.assertIsNotNone(result)
        self.assertEqual(result.frame, FRAME)
        self.assertEqual({f.roi_id for f in result.findings}, {5, 9})
        self.assertEqual(result.rois_judged, 16)
        self.assertEqual(result.pixels.shape, (400, 400))  # raw pixels of the frame
        self.assertTrue(result.pixels.any())
        self.assertTrue(_pump_until(lambda: self.preview.current() is not None, 5.0))
        np.testing.assert_array_equal(self.preview.current().pixels, result.pixels)
        self.assertTrue(self.layer.item.isVisible())

    def test_the_preview_follows_the_frame_it_was_made_for(self) -> None:
        self._load()
        self._open_tool()
        self._process()
        self.assertTrue(_pump_until(lambda: self.preview.current() is not None, 5.0))
        self.selection.set_wavelength(OTHER_FRAME[1])
        self.assertTrue(_pump_until(lambda: self.preview.frame() == OTHER_FRAME, 10.0))
        self.assertIsNone(self.preview.current())  # the pixels describe another image: never drawn on this one
        self.assertFalse(self.tool._apply_button.isEnabled())
        self.assertIn("500", self.tool._apply_button.toolTip())  # and the tooltip says which image they are for
        self.selection.set_wavelength(FRAME[1])
        self.assertTrue(_pump_until(lambda: self.preview.current() is not None, 10.0))  # back on it: they are shown again

    def test_nothing_is_added_until_apply_and_the_buttons_follow_the_state(self) -> None:
        self.assertFalse(self.tool._process_button.isEnabled())  # no image yet
        self._load()
        self._open_tool()
        self.assertTrue(self.tool._process_button.isEnabled())
        self.assertFalse(self.tool._apply_button.isEnabled())
        self.assertIn("Process", self.tool._apply_button.toolTip())
        self._process()
        self.assertTrue(self.tool._apply_button.isEnabled())
        self.assertIsNone(self.mask.resolve_mask_source(FRAME))  # finding changes no mask

    def test_the_finder_ignores_the_mask_so_a_mask_edit_keeps_the_result(self) -> None:
        self._load()
        self._open_tool()
        self._process()
        first = self.task.result()
        self.mask.set_mask_change(FRAME, "persistent", np.ones((400, 400), dtype=bool))
        _pump(0.2)
        self.assertIs(self.task.result(), first)

    # -- Apply -----------------------------------------------------------------------------------------------------------

    def test_apply_adds_the_pixels_to_the_mask_at_the_persistent_scope(self) -> None:
        self._load()
        self._open_tool()
        self.scope.set_scope(MaskScope.PERSISTENT)
        self._process()
        found = self.task.result().pixels
        self.tool._apply_button.click()
        _pump(0.2)
        _frame, mask, scope = self.mask.resolve_mask_source(FRAME)
        self.assertEqual(scope, "persistent")
        np.testing.assert_array_equal(mask, found)

    def test_apply_adds_to_the_mask_already_in_effect_and_at_the_individual_scope(self) -> None:
        self._load()
        self._open_tool()
        existing = np.zeros((400, 400), dtype=bool)
        existing[5:15, 5:15] = True
        self.mask.set_mask_change(FRAME, "individual", existing)
        self.scope.set_scope(MaskScope.INDIVIDUAL)
        self._process()
        found = self.task.result().pixels
        self.tool._apply_button.click()
        _pump(0.2)
        _frame, mask, scope = self.mask.resolve_mask_source(FRAME)
        self.assertEqual(scope, "individual")
        np.testing.assert_array_equal(mask, existing | found)  # merged with what was there, nothing taken away
        self.assertTrue(mask[7, 7])

    def test_apply_is_one_mask_change(self) -> None:
        self._load()
        self._open_tool()
        changes = []
        self.mask.mask_changed.connect(changes.append)
        self._process()
        self.tool._apply_button.click()
        _pump(0.2)
        self.assertEqual(len(changes), 1)  # (mask edits are not on the undo stack, like the other tools' Add; see MaskModule)

    def test_apply_stays_inside_the_area_selection(self) -> None:
        self._load()
        self._open_tool()
        self._process()
        found = self.task.result().pixels
        ys, xs = np.nonzero(found)
        middle = int(np.median(xs))
        region = np.zeros((400, 400), dtype=bool)
        region[:, :middle] = True
        self.panel.selection_raw_mask = lambda shape: region  # the area selection, as the editors ask for it
        self.tool._apply_button.click()
        _pump(0.2)
        _frame, mask, _scope = self.mask.resolve_mask_source(FRAME)
        np.testing.assert_array_equal(mask, found & region)

    # -- when a result is dropped or refused -------------------------------------------------------------------------

    def test_a_change_of_the_rois_drops_the_result(self) -> None:
        self._load()
        self._open_tool()
        self._process()
        self.assertIsNotNone(self.task.result())
        self.toolbox.add_roi(10.0, 10.0, sample_diameter_px=10.0)  # any ROI edit
        self.assertIsNone(self.task.result())
        self.assertIsNone(self.preview.current())
        self.assertFalse(self.tool._apply_button.isEnabled())

    def test_a_change_while_it_runs_refuses_the_result(self) -> None:
        self._load()
        self._open_tool()
        before = len(self.messages)
        self.tool._process_button.click()
        self.assertTrue(self.task.is_running())
        self.assertFalse(self.tool._process_button.isEnabled())  # one run at a time
        self.toolbox.add_roi(10.0, 10.0, sample_diameter_px=10.0)  # the layout the run started with is gone
        self.assertTrue(_pump_until(lambda: len(self.messages) > before and not self.task.is_running()))
        self.assertIn("changed while it ran", self.messages[-1])
        self.assertIsNone(self.task.result())

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

    def test_too_few_rois_and_no_dataset_say_so(self) -> None:
        task = RoiFeatureTask(GeometryModule(), BackgroundModule(), ChromaticModule(), RoiToolbox(), DatasetModule())
        said: list[str] = []
        task.failed.connect(said.append)
        task.start(FRAME, self.tool.params())
        self.assertIn("Load a dataset", said[-1])
        self._load()
        few = RoiToolbox()
        for cx, cy in _grid_centres()[:3]:
            few.add_roi(cx, cy, sample_diameter_px=32.0)
        task = RoiFeatureTask(GeometryModule(), BackgroundModule(), ChromaticModule(), few, self.dataset)
        task.failed.connect(said.append)
        task.start(FRAME, self.tool.params())
        self.assertIn("at least 8 ROIs", said[-1])
        self.assertIn("there are 3", said[-1])

    # -- the numbers ---------------------------------------------------------------------------------------------------

    def test_a_second_run_with_other_numbers_reuses_the_fit(self) -> None:
        self._load()
        self._open_tool()
        summaries = []
        self.task.completed.connect(summaries.append)
        self._process()
        first = self.task.result()
        self.tool._pop.margin_spin.setValue(3)
        self.tool._pop.share_spin.setValue(100)  # keep every ROI, so the bigger margin shows in the pixels
        self._process()
        self.assertFalse(summaries[0].fit_kept)
        self.assertTrue(summaries[1].fit_kept)
        self.assertLess(summaries[1].seconds, summaries[0].seconds)
        second = self.task.result()
        self.assertGreater(int(second.pixels.sum()), int(first.pixels.sum()))
        self.assertEqual(second.params.margin_px, 3)
        # and the same numbers again give the first result back, pixel for pixel
        self.tool._pop.margin_spin.setValue(1)
        self.tool._pop.share_spin.setValue(40)
        self._process()
        np.testing.assert_array_equal(self.task.result().pixels, first.pixels)

    def test_changed_numbers_are_flagged_on_the_process_button(self) -> None:
        self._load()
        self._open_tool()
        self._process()
        self.assertNotIn("settings changed", self.tool._process_button.toolTip())
        self.tool._pop.strength_spin.setValue(8.0)
        self.assertIn("settings changed", self.tool._process_button.toolTip())

    def test_the_numbers_are_remembered_and_have_working_defaults(self) -> None:
        pop = self.tool._pop
        self.assertEqual(self.tool.params().strength, 5.0)
        store = UiStateStore(
            {
                "image/mask_features/strength": 6.5, "image/mask_features/extent": 2.0, "image/mask_features/max_width_px": 25,
                "image/mask_features/margin_px": 2, "image/mask_features/max_share_percent": 30,
            }
        )
        self.tool.bind_ui_state(store)
        params = self.tool.params()
        self.assertEqual((params.strength, params.extent, params.max_width_px, params.margin_px, params.max_share), (6.5, 2.0, 25.0, 2, 0.3))
        pop.strength_spin.setValue(7.0)  # a later change is written down
        self.assertEqual(store.get("image/mask_features/strength"), 7.0)
        pop.reset_button.click()
        self.assertEqual(self.tool.params().strength, 5.0)

    def test_the_tool_is_in_the_picker_and_is_remembered_by_name(self) -> None:
        labels = [action.text() for action in self.tab._edit_picker._menu.actions()]
        self.assertIn("ROI features", labels)
        self.assertEqual(MaskEditTool("roi_features"), MaskEditTool.ROI_FEATURES)
        self.tab.edit_tool.set_tool(MaskEditTool.ROI_FEATURES)
        self.assertIs(self.tab._edit_stack.currentWidget(), self.tool)


if __name__ == "__main__":
    unittest.main()
