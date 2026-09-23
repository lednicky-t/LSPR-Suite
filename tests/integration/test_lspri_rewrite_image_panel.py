"""Tests for the LSPRimaging Evaluation rewrite's Image panel.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring for why.

A real `QApplication` built in-process, never `.exec()`; real widgets; real
TIFF files; driven entirely by direct method and signal calls rather than
screen coordinates - the same pattern
`tests/integration/test_lspri_preferences_dialog.py` established and that
AGENTS.md's "prefer widgets that are directly callable" rule exists to make
possible.

The property most worth pinning here is the one-way flow: a gesture becomes
a *command* on a module, the module emits, and the panel redraws because of
that. It is what makes undo work with no undo code in the panel, and it is
easy to break by "just" mutating state directly in a handler.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools import (
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.render import RenderRequest, RenderResult
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

_CIRCLE_POINTS = 48  # must match panel._CIRCLE_POINTS


def _pump(seconds: float = 0.5) -> None:
    """Let the coalescing redraw timer fire and the render thread's result
    reach the GUI thread. The panel deliberately renders off-thread, so a
    test cannot assert on the image without giving that a chance to land."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    rng = np.random.default_rng(3)
    for cube in (0, 1):
        for wavelength in (500.0, 550.0, 600.0):
            if cube == 1 and wavelength == 550.0:
                continue  # cube 1 is short a wavelength, on purpose
            path = root / f"c{cube}_w{int(wavelength)}.tif"
            frame = rng.uniform(100.0, 200.0, size=(64, 80)).astype(np.float32)
            frame[28:33, 38:43] += 4000.0
            tifffile.imwrite(str(path), frame)
            records.append(
                ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path)
            )
    return ImageDataset(folder=root, records=records, source_format="image_stack")


class RewriteImagePanelTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.dataset_model = _write_dataset(self.root)

        self.dataset = DatasetModule()
        self.geometry = GeometryModule()
        self.mask = MaskModule()
        self.chromatic = ChromaticModule()
        self.background = BackgroundModule()
        self.roi_toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.panel = ImagePanel(
            self.dataset, self.geometry, self.mask, self.chromatic,
            self.background, self.roi_toolbox, self.selection,
        )

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self._tmp.cleanup()

    def _load(self) -> None:
        self.dataset.load_dataset(self.dataset_model)
        _pump()

    # -- lifecycle ------------------------------------------------------

    def test_empty_before_a_dataset_is_loaded(self) -> None:
        self.assertFalse(self.panel._cube_spin.isEnabled())
        self.assertIn("No dataset", self.panel._status.text())

    def test_renders_a_real_image_after_load(self) -> None:
        self._load()
        self.assertTrue(self.panel._cube_spin.isEnabled())
        self.assertEqual((self.panel._cube_spin.minimum(), self.panel._cube_spin.maximum()), (0, 1))
        self.assertIsNotNone(self.panel._image_item.image)
        self.assertEqual(self.panel._image_item.image.shape, (64, 80))

    def test_rendering_happens_off_the_gui_thread(self) -> None:
        """CLAUDE.md: long image processing must not run on the main
        thread. Pinned because it is invisible when it regresses - the
        panel keeps working, it just freezes under load."""
        self.assertTrue(self.panel._renderer._thread.is_alive())
        self.assertEqual(self.panel._renderer._thread.name, "ImageRenderer")
        self.assertNotEqual(self.panel._renderer._thread.ident, __import__("threading").get_ident())

    def test_dataset_cleared_resets_the_panel(self) -> None:
        """`DatasetModule.clear_dataset` clears only its own reference and
        expects each holder of dataset-derived state to reset itself. This
        panel is the first real subscriber to that contract."""
        self._load()
        self.dataset.clear_dataset()
        _pump()
        self.assertIsNone(self.panel._image_item.image)
        self.assertFalse(self.panel._cube_spin.isEnabled())
        self.assertIn("No dataset", self.panel._status.text())

    # -- geometry -------------------------------------------------------

    def test_crop_changes_the_displayed_image(self) -> None:
        self._load()
        self.geometry.set_image_tools_enabled(True)
        self.geometry.set_crop(8, 6, 48, 40)
        _pump()
        self.assertEqual(self.panel._image_item.image.shape, (40, 48))

        self.geometry.set_image_tools_enabled(False)
        _pump()
        self.assertEqual(self.panel._image_item.image.shape, (64, 80))

    # -- overlays -------------------------------------------------------

    def test_overlay_draws_one_sample_and_two_reference_circles_per_roi(self) -> None:
        self._load()
        self.roi_toolbox.add_roi(40.0, 30.0, sample_radius_px=5.0)
        self.roi_toolbox.add_roi(60.0, 45.0, sample_radius_px=5.0)
        _pump()

        sample_x, _ = self.panel._sample_curve.getData()
        reference_x, _ = self.panel._reference_curve.getData()
        # N circles joined by N-1 NaN separators in a single PlotDataItem.
        self.assertEqual(len(sample_x), 2 * _CIRCLE_POINTS + 1)
        self.assertEqual(len(reference_x), 4 * _CIRCLE_POINTS + 3)

    def test_selection_moves_an_roi_to_the_highlight_curve(self) -> None:
        self._load()
        roi_a = self.roi_toolbox.add_roi(40.0, 30.0, sample_radius_px=5.0)
        self.roi_toolbox.add_roi(60.0, 45.0, sample_radius_px=5.0)
        self.selection.set_roi_selection({roi_a})
        _pump()

        self.assertEqual(len(self.panel._selection_curve.getData()[0]), _CIRCLE_POINTS)
        self.assertEqual(len(self.panel._sample_curve.getData()[0]), _CIRCLE_POINTS)

    # -- interaction ----------------------------------------------------

    def test_hit_testing_by_image_coordinate(self) -> None:
        self._load()
        roi_a = self.roi_toolbox.add_roi(40.0, 30.0, sample_radius_px=5.0)
        roi_b = self.roi_toolbox.add_roi(60.0, 45.0, sample_radius_px=5.0)

        self.assertEqual(self.panel.roi_at(40.0, 30.0), roi_a)
        self.assertEqual(self.panel.roi_at(44.0, 30.0), roi_a)  # just inside the radius
        self.assertEqual(self.panel.roi_at(60.0, 45.0), roi_b)
        self.assertIsNone(self.panel.roi_at(5.0, 5.0))

    def test_a_drag_is_a_command_and_is_therefore_undoable(self) -> None:
        """The one-way-flow property. The panel contains no undo code at
        all; undo works because the drag went through `RoiToolbox`, whose
        `revert()` re-emits exactly what the original call emitted."""
        self._load()
        roi_a = self.roi_toolbox.add_roi(40.0, 30.0, sample_radius_px=5.0)
        reasons: list[str] = []
        self.roi_toolbox.geometry_changed.connect(lambda change: reasons.append(change.reason))

        self.panel._on_drag(roi_a, 45.0, 35.0)
        self.assertIn("moved", reasons)
        self.assertEqual(self.roi_toolbox.roi_by_id(roi_a).center_x, 45.0)

        undo_manager.undo()
        self.assertEqual(self.roi_toolbox.roi_by_id(roi_a).center_x, 40.0)

    # -- uneven datasets and failures -----------------------------------

    def test_a_cube_short_a_wavelength_snaps_instead_of_erroring(self) -> None:
        self._load()
        self.selection.set_wavelength(550.0)
        self.panel._cube_spin.setValue(1)  # the signal a real click produces
        _pump()

        self.assertIn(self.panel._current_wavelength(), (500.0, 600.0))
        self.assertIsNotNone(self.panel._image_item.image)
        self.assertNotIn("Cannot show", self.panel._status.text())

    def test_a_missing_frame_is_reported_not_raised(self) -> None:
        self._load()
        _pump()  # let any coalesced redraw settle so it cannot supersede ours
        serial = self.panel._latest_serial + 1000
        self.panel._latest_serial = serial
        self.panel._renderer.submit(
            RenderRequest(
                cube_index=9, wavelength_nm=999.0,
                geometry=self.geometry.settings(), background=self.background.settings(),
                authored_mask=None, mask_warp_affine=None,
                rois=(), detection=self.roi_toolbox.detection_settings(),
                serial=serial,
            )
        )
        _pump()
        self.assertIn("Cannot show", self.panel._status.text())

    def test_a_stale_render_result_is_dropped(self) -> None:
        """"Latest request wins": a frame superseded while in flight must
        not overwrite the newer one that already arrived."""
        self._load()
        before = self.panel._status.text()
        stale = RenderRequest(
            cube_index=0, wavelength_nm=500.0,
            geometry=self.geometry.settings(), background=self.background.settings(),
            authored_mask=None, mask_warp_affine=None,
            rois=(), detection=self.roi_toolbox.detection_settings(),
            serial=self.panel._latest_serial - 1,
        )
        self.panel._on_rendered(RenderResult(request=stale, image=np.zeros((4, 4), dtype=np.float32)))
        self.assertEqual(self.panel._status.text(), before)


if __name__ == "__main__":
    unittest.main()
