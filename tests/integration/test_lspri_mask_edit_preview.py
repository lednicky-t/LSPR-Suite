"""The Mask tab's edit preview and the Threshold / Local contrast candidates (2026-10-29; reduced 2026-11-15).

2026-11-15: the Threshold, Local contrast and Morphology *previews* and the Mask-tab Add / Subtract buttons of those tools went when the
tools moved into the selection toolbar ("Select by value" and "Modify selection": `test_lspri_selection_by_value.py`,
`test_lspri_selection_modify_brush.py`). What is pinned here: the worker that computes candidates, `MaskEditPreview.request_candidate`
(what "Select by value" asks), and that the preview tint now belongs to the Feature map tool alone (its own tests:
`test_lspri_feature_map_tool.py`).

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see `test_lspri_rewrite_analysis_core.py`'s docstring.

Two layers, each tested where it lives:

- `MaskCandidates` (the worker): computes off the calling thread, caches, drops stale results, keeps previews bounded.
- `MaskEditPreview` on a real `ImagePanel`: `request_candidate` gives exactly the pixels the mask tools compute; nothing is tinted for the
  Eraser; the eye is one remembered state.

A real `QApplication` in-process, never `.exec()`, driven by direct calls and signals (same pattern as
`test_lspri_rewrite_image_panel.py`, whose dataset fixture this repeats on a smaller scale).
"""

from __future__ import annotations

import sys
import tempfile
import threading
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
    from lspr_imaging_app.image_tools.mask import raster_tools
    from lspr_imaging_app.image_tools.mask_candidates import MaskCandidates
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.mask_tool_settings import push_tool_settings as _push_tool_settings
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

FRAME = (0, 500.0)


def _pump_until(condition, seconds: float = 20.0) -> bool:
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


class MaskCandidatesTest(unittest.TestCase):
    """The worker object alone: no panel, no dataset."""

    def setUp(self) -> None:
        self.candidates = MaskCandidates()
        self.results: list[object] = []

    def tearDown(self) -> None:
        self.candidates.shutdown()

    def _record(self, value: object) -> None:
        self.results.append(value)

    def test_a_result_arrives_on_the_calling_thread_and_is_cached(self) -> None:
        main = threading.get_ident()
        computed_on: list[int] = []
        arrived_on: list[int] = []

        def compute() -> np.ndarray:
            computed_on.append(threading.get_ident())
            return np.ones((3, 3), dtype=bool)

        self.candidates.request(("relative", 1), compute, lambda r: (arrived_on.append(threading.get_ident()), self._record(r)))
        self.assertTrue(_pump_until(lambda: bool(self.results), 5.0))
        self.assertNotEqual(computed_on[0], main)  # off the GUI thread
        self.assertEqual(arrived_on[0], main)  # back on it
        # A second request for the same key is answered at once from the cache, without computing again.
        self.candidates.request(("relative", 1), compute, self._record)
        self.assertEqual(len(self.results), 2)
        self.assertEqual(len(computed_on), 1)
        np.testing.assert_array_equal(self.results[1], np.ones((3, 3), dtype=bool))

    def test_a_failed_computation_delivers_none_and_is_not_cached(self) -> None:
        def broken() -> np.ndarray:
            raise RuntimeError("no plane")

        with self.assertLogs("lspr_imaging_app.image_tools.mask_candidates", level="ERROR"):
            self.candidates.request(("relative", 2), broken, self._record)
            self.assertTrue(_pump_until(lambda: bool(self.results), 5.0))
        self.assertEqual(self.results, [None])
        self.assertIsNone(self.candidates.cached(("relative", 2)))

    def test_invalidate_makes_a_running_result_stale(self) -> None:
        gate = threading.Event()

        def slow() -> np.ndarray:
            gate.wait(10.0)
            return np.ones((2, 2), dtype=bool)

        self.candidates.request(("relative", 3), slow, self._record)
        self.candidates.invalidate()  # the dataset was replaced while it ran
        gate.set()
        self.assertTrue(_pump_until(lambda: bool(self.results), 5.0))
        self.assertEqual(self.results, [None])
        self.assertIsNone(self.candidates.cached(("relative", 3)))

    def test_previews_do_not_stack_and_only_the_newest_waiting_one_runs(self) -> None:
        gate = threading.Event()
        started: list[str] = []

        def first() -> np.ndarray:
            started.append("a")
            gate.wait(10.0)
            return np.zeros((2, 2), dtype=bool)

        def make(name: str):
            def compute() -> np.ndarray:
                started.append(name)
                return np.zeros((2, 2), dtype=bool)

            return compute

        self.candidates.request(("relative", "a"), first, lambda r: self._record("a"), preview=True)
        self.candidates.request(("relative", "b"), make("b"), lambda r: self._record("b"), preview=True)
        self.candidates.request(("relative", "c"), make("c"), lambda r: self._record("c"), preview=True)  # replaces b
        _pump(0.2)
        self.assertEqual(started, ["a"])  # b and c wait; nothing runs next to a
        gate.set()
        self.assertTrue(_pump_until(lambda: "c" in self.results, 5.0))
        self.assertEqual(started, ["a", "c"])  # b was never computed
        self.assertEqual(self.results, ["a", "c"])

    def test_a_request_that_is_not_a_preview_starts_at_once_next_to_a_running_preview(self) -> None:
        gate = threading.Event()
        started: list[str] = []

        def slow() -> np.ndarray:
            started.append("preview")
            gate.wait(10.0)
            return np.zeros((2, 2), dtype=bool)

        def quick() -> np.ndarray:
            started.append("apply")
            return np.zeros((2, 2), dtype=bool)

        self.candidates.request(("relative", "p"), slow, lambda r: self._record("p"), preview=True)
        self.candidates.request(("relative", "q"), quick, lambda r: self._record("q"))
        self.assertTrue(_pump_until(lambda: "q" in self.results, 5.0))
        self.assertEqual(started[0], "preview")
        self.assertIn("apply", started)
        gate.set()
        self.assertTrue(_pump_until(lambda: "p" in self.results, 5.0))

    def test_the_same_key_asked_twice_is_computed_once(self) -> None:
        gate = threading.Event()
        count: list[int] = []

        def compute() -> np.ndarray:
            count.append(1)
            gate.wait(10.0)
            return np.ones((2, 2), dtype=bool)

        self.candidates.request(("relative", "k"), compute, lambda r: self._record("first"), preview=True)
        self.candidates.request(("relative", "k"), compute, lambda r: self._record("second"))  # the button press joins it
        gate.set()
        self.assertTrue(_pump_until(lambda: len(self.results) == 2, 5.0))
        self.assertEqual(len(count), 1)


def _write_dataset(root: Path) -> ImageDataset:
    """Two frames of noise (100-200) with a bright 5 x 5 patch at rows 28-32, columns 38-42 (+4000)."""
    records = []
    rng = np.random.default_rng(3)
    for cube in (0, 1):
        for wavelength in (500.0, 600.0):
            path = root / f"c{cube}_w{int(wavelength)}.tif"
            frame = rng.uniform(100.0, 200.0, size=(64, 80)).astype(np.float32)
            frame[28:33, 38:43] += 4000.0
            tifffile.imwrite(str(path), frame)
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


class MaskEditPreviewPanelTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.dataset_model = _write_dataset(self.root)
        self.dataset = DatasetModule()
        self.mask = MaskModule()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), self.mask, ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(),
        )
        self.tab = self.panel._mask_tab
        self.preview = self.tab.edit_preview
        self.layer = self.panel._edit_preview_layer
        # A threshold that picks out the bright patch and nothing of the noise (sigma 10 px, 300 % of the local background).
        _push_tool_settings(self.mask, relative_threshold_fraction=3.0, relative_profile_sigma_px=10.0)

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self._tmp.cleanup()

    def _load(self) -> None:
        self.dataset.load_dataset(self.dataset_model)
        _pump_until(lambda: self.panel._image_item.image is not None and self.panel._image_item.levels is not None)
        _pump()

    def _candidate(self, kind: str, frame=FRAME) -> np.ndarray | None:
        got: list[object] = []
        self.preview.request_candidate(kind, frame, got.append)
        self.assertTrue(_pump_until(lambda: len(got) == 1))
        return got[0]

    def test_a_threshold_candidate_is_exactly_what_the_mask_tool_computes(self) -> None:
        self._load()
        plane = tifffile.imread(str(self.root / "c0_w500.tif"))
        expected = raster_tools.create_relative_contrast_mask(plane, sigma_px=10.0, threshold_fraction=3.0)
        self.assertTrue(expected[30, 40])  # the fixture really selects the patch
        np.testing.assert_array_equal(self._candidate("relative"), expected)

    def test_a_local_contrast_candidate_is_exactly_what_the_mask_tool_computes(self) -> None:
        self._load()
        _push_tool_settings(self.mask, local_contrast_sigma_px=5.0, local_contrast_z_threshold=4.0)
        plane = tifffile.imread(str(self.root / "c0_w500.tif"))
        expected = raster_tools.create_local_contrast_mask(plane, sigma_px=5.0, z_threshold=4.0)
        np.testing.assert_array_equal(self._candidate("local_contrast"), expected)

    def test_a_candidate_is_cached_and_follows_the_numbers_and_the_frame(self) -> None:
        self._load()
        first = self._candidate("relative")
        again = self._candidate("relative")
        self.assertIs(again, first, "the same frame and numbers: the cached array")
        _push_tool_settings(self.mask, relative_threshold_fraction=0.02)  # 2 %: now the noise is selected too
        self.assertGreater(int(np.count_nonzero(self._candidate("relative"))), int(np.count_nonzero(first)))
        other = self._candidate("relative", (1, 600.0))
        self.assertEqual(other.shape, first.shape)

    def test_an_unknown_kind_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.preview.request_candidate("morphology", FRAME, lambda _pixels: None)

    def test_nothing_is_tinted_for_the_eraser_or_off_the_mask_tab(self) -> None:
        self._load()
        self.tab.edit_tool.set_tool(MaskEditTool.ERASER)
        self.panel._tool_ribbon.set_category("Mask")
        _pump(0.3)
        self.assertIsNone(self.preview.current())
        self.assertFalse(self.layer.item.isVisible())

    def test_the_eye_button_is_one_remembered_state(self) -> None:
        eye = self.tab.features_panel._toggle
        self.assertTrue(eye.isChecked())
        eye.click()
        self.assertFalse(self.preview.shown())

        store = UiStateStore({"image/mask_edit_preview": True})
        self.panel.restore_ui_state(store)  # a saved "on" comes back
        self.assertTrue(self.preview.shown())
        self.assertTrue(eye.isChecked())
        eye.click()  # and a later change is written down
        self.assertIs(store.get("image/mask_edit_preview"), False)


if __name__ == "__main__":
    unittest.main()
