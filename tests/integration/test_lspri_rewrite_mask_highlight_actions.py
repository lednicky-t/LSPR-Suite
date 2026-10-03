"""Tests for the LSPRimaging Evaluation rewrite's "Mask" section
histogram-highlight actions (`panels/workflow/mask_highlight_actions.py`).

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring for why.

Same pattern as `test_lspri_rewrite_image_panel.py`/
`test_lspri_rewrite_histogram_panel.py`: a real `QApplication`, real
widgets, a real TIFF-backed dataset, driven entirely by direct
method/signal calls rather than screen coordinates.

The property most worth pinning here: +/- act on whichever scope
(Persistent/Individual) is currently toggled, and Persistent applies to
its whole cube and every cube after it (maintainer's explicit expectation,
2026-09-30 - "canonical should be whole cube and cubes onwards"), which is
`MaskModule.resolve_mask_source`'s own pre-existing resolution order, not
new logic this widget adds.
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
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.workflow.mask_highlight_actions import MaskHighlightActions
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

_IMAGE_SHAPE = (64, 80)
_PATCH = (slice(10, 20), slice(10, 20))  # rows 10-19, cols 10-19
_PATCH_VALUE = 4000.0
_BACKGROUND_RANGE = (100.0, 200.0)
_HIGHLIGHT_RANGE = (3000.0, 5000.0)  # isolates the patch, excludes the background


def _pump(seconds: float = 0.5) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _write_dataset(root: Path) -> ImageDataset:
    rng = np.random.default_rng(11)
    records = []
    for cube in (0, 1):
        frame = rng.uniform(*_BACKGROUND_RANGE, size=_IMAGE_SHAPE).astype(np.float32)
        frame[_PATCH] += _PATCH_VALUE
        path = root / f"c{cube}_w500.tif"
        tifffile.imwrite(str(path), frame)
        records.append(ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


class MaskHighlightActionsTest(unittest.TestCase):
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
        self.highlight_range = HighlightRangeModule()
        self.mask_scope = MaskScopeModule()
        self.image_panel = ImagePanel(
            self.dataset, self.geometry, self.mask, self.chromatic,
            self.background, self.roi_toolbox, self.selection, ActiveToolModule(), ReferenceFrameModule(),
            self.highlight_range,
            mask_scope=self.mask_scope,
        )
        self.actions = MaskHighlightActions(
            self.mask, self.geometry, self.chromatic, self.dataset, self.highlight_range, self.image_panel,
            self.mask_scope,
        )

    def tearDown(self) -> None:
        self.image_panel._renderer.stop()
        self._tmp.cleanup()

    def _load(self) -> None:
        self.dataset.load_dataset(self.dataset_model)
        _pump()

    # -- button enablement ----------------------------------------------

    def test_buttons_disabled_until_both_an_image_and_a_highlight_range_exist(self) -> None:
        self.assertFalse(self.actions._add_button.isEnabled())
        self.assertFalse(self.actions._subtract_button.isEnabled())

        self._load()
        self.assertFalse(self.actions._add_button.isEnabled(), "an image alone isn't enough")

        self.highlight_range.set_range(*_HIGHLIGHT_RANGE)
        self.assertTrue(self.actions._add_button.isEnabled())
        self.assertTrue(self.actions._subtract_button.isEnabled())

        self.dataset.clear_dataset()
        _pump()
        self.assertFalse(self.actions._add_button.isEnabled(), "clearing the dataset must re-disable both")

    # -- add / subtract ---------------------------------------------------

    def test_add_masks_exactly_the_highlighted_patch_in_raw_space(self) -> None:
        """Identity geometry (no crop/rotate/flip) - raw and processed space
        coincide, so the resulting raw mask should be True at exactly the
        bright patch's own pixel coordinates."""
        self._load()
        self.highlight_range.set_range(*_HIGHLIGHT_RANGE)
        self.actions._add_button.click()

        resolution = self.mask.resolve_mask_source((0, 500.0))
        self.assertIsNotNone(resolution)
        _frame, resolved_mask, scope = resolution
        self.assertEqual(scope, "persistent")
        expected = np.zeros(_IMAGE_SHAPE, dtype=bool)
        expected[_PATCH] = True
        np.testing.assert_array_equal(resolved_mask, expected)

    def test_subtract_removes_the_highlighted_patch(self) -> None:
        self._load()
        self.highlight_range.set_range(*_HIGHLIGHT_RANGE)
        self.actions._add_button.click()
        self.actions._subtract_button.click()

        resolution = self.mask.resolve_mask_source((0, 500.0))
        self.assertIsNotNone(resolution)
        _frame, resolved_mask, _scope = resolution
        self.assertFalse(bool(np.any(resolved_mask)))

    # -- scope toggle -------------------------------------------------------

    def test_persistent_scope_applies_to_this_cube_and_every_cube_after(self) -> None:
        """Maintainer's explicit expectation, 2026-09-30: persistent should
        cover the whole cube it was set on and every cube after it - this is
        `MaskModule.resolve_mask_source`'s own pre-existing resolution
        order (persistent changes keyed by cube index, most recent-at-or-
        before wins), confirmed here end-to-end through the real UI action
        rather than only unit-tested on the module directly."""
        self._load()
        self.selection.set_cube(0)
        self.highlight_range.set_range(*_HIGHLIGHT_RANGE)
        self.assertTrue(self.actions._scope_toggle._persistent_button.isChecked())
        self.actions._add_button.click()

        for cube in (0, 1):
            resolution = self.mask.resolve_mask_source((cube, 500.0))
            self.assertIsNotNone(resolution, f"cube {cube} should see the persistent mask")
            self.assertEqual(resolution[2], "persistent")

    def test_individual_scope_only_affects_its_own_exact_frame(self) -> None:
        self._load()
        self.selection.set_cube(0)
        self.highlight_range.set_range(*_HIGHLIGHT_RANGE)
        self.actions._scope_toggle._individual_button.click()
        self.assertFalse(self.actions._scope_toggle._persistent_button.isChecked())
        self.actions._add_button.click()

        own_frame = self.mask.resolve_mask_source((0, 500.0))
        self.assertIsNotNone(own_frame)
        self.assertEqual(own_frame[2], "individual")

        other_cube = self.mask.resolve_mask_source((1, 500.0))
        self.assertIsNone(other_cube, "an individual edit must not leak into another cube")

    def test_scope_toggle_is_mutually_exclusive(self) -> None:
        self.actions._scope_toggle._individual_button.click()
        self.assertTrue(self.actions._scope_toggle._individual_button.isChecked())
        self.assertFalse(self.actions._scope_toggle._persistent_button.isChecked())

        self.actions._scope_toggle._persistent_button.click()
        self.assertTrue(self.actions._scope_toggle._persistent_button.isChecked())
        self.assertFalse(self.actions._scope_toggle._individual_button.isChecked())

    def test_scope_toggle_stays_in_sync_with_the_image_panels_own_copy(self) -> None:
        """The whole point of sharing `MaskScopeModule` rather than each
        widget owning its own private buttons (maintainer request,
        2026-10-02: copy the Persistent/Individual icons into the Image
        panel's "Mask" tab too) - clicking either toggle must update both,
        since Add/Subtract here always acts on whichever scope is selected
        and the two toggles must never be able to disagree about it."""
        image_panel_toggle = self.image_panel._mask_scope_toggle

        image_panel_toggle._individual_button.click()
        self.assertTrue(self.actions._scope_toggle._individual_button.isChecked())
        self.assertEqual(self.mask_scope.scope().value, "individual")

        self.actions._scope_toggle._persistent_button.click()
        self.assertTrue(image_panel_toggle._persistent_button.isChecked())
        self.assertEqual(self.mask_scope.scope().value, "persistent")


if __name__ == "__main__":
    unittest.main()
