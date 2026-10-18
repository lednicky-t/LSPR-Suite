"""Options -> "ROI Overlay Cache" (2026-10-14): the memory budget of the ROI overlay cache, chosen in the menu, saved with the
other UI state, applied at once, and overridable at start-up by `LSPR_OVERLAY_CACHE_MB`.

The cache itself is pinned in `test_lspri_rewrite_overlay_layer_cache.py`. Menus are driven through their actions
(`trigger()`), never by screen coordinate.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import pyqtgraph as pg
    from PyQt6.QtWidgets import QApplication

    from lspr_imaging_app.app_rewrite import build_main_window
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.overlay_cache import (
        BUDGET_CHOICES_MB,
        DEFAULT_BUDGET_MB,
        OverlayLayers,
        environment_budget_mb,
    )
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.storage.ui_state_keys import IMAGE_OVERLAY_CACHE_MB
except ImportError as error:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(error)
    raise

_APP = QApplication.instance() or QApplication([])
_MB = 1024 * 1024


class BudgetChangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plot = pg.PlotWidget()
        self.layers = OverlayLayers(self.plot.getPlotItem(), budget_bytes=64 * _MB)

    def test_zero_switches_the_cache_off_and_drops_what_it_held(self) -> None:
        self.layers.begin(("epoch",), ("state", 0))
        self.layers.set_budget_bytes(0)
        self.assertFalse(self.layers.enabled)
        self.assertEqual(self.layers.cached_states, 0)
        self.assertFalse(self.layers.begin(("epoch",), ("state", 0)))
        self.assertEqual(self.layers.status_text(), "Off: the overlay is painted directly")

    def test_a_budget_is_taken_up_again_after_off(self) -> None:
        self.layers.set_budget_bytes(0)
        self.layers.set_budget_bytes(128 * _MB)
        self.assertTrue(self.layers.enabled)
        self.assertEqual(self.layers.budget_megabytes, 128.0)
        self.assertIn("nothing cached yet", self.layers.status_text())

    def test_a_smaller_budget_than_the_pictures_use_drops_them(self) -> None:
        effect = self.layers._effects[0]
        effect._entries[("state", 0)] = (None, None, 10 * _MB)  # a stand-in picture of 10 MB
        self.layers._budget.used = 10 * _MB
        self.layers.set_budget_bytes(5 * _MB)
        self.assertEqual(self.layers.cached_states, 0)
        self.assertEqual(self.layers._budget.used, 0)


class EnvironmentTests(unittest.TestCase):
    def test_environment_value(self) -> None:
        self.assertIsNone(environment_budget_mb({}))
        self.assertEqual(environment_budget_mb({"LSPR_OVERLAY_CACHE_MB": "64"}), 64.0)
        self.assertEqual(environment_budget_mb({"LSPR_OVERLAY_CACHE_MB": "0"}), 0.0)
        for bad in ("lots", "-5", "nan", "inf"):
            with self.subTest(value=bad), self.assertLogs("lspr_imaging_app.panels.image.overlay_cache", level="WARNING"):
                self.assertIsNone(environment_budget_mb({"LSPR_OVERLAY_CACHE_MB": bad}))


class PanelOptionTests(unittest.TestCase):
    """The panel's side: property, setter, saved value, restore, environment override."""

    def setUp(self) -> None:
        from lspr_imaging_app.dataset import DatasetModule
        from lspr_imaging_app.image_tools import BackgroundModule, ChromaticModule, GeometryModule, MaskModule, MaskScopeModule
        from lspr_imaging_app.image_tools import ActiveToolModule
        from lspr_imaging_app.roi import RoiToolbox
        from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule

        self.chromatic = ChromaticModule()
        self.roi_toolbox = RoiToolbox()
        self.panel = ImagePanel(
            DatasetModule(), GeometryModule(), MaskModule(), self.chromatic, BackgroundModule(), self.roi_toolbox,
            SelectionModule(), ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(),
        )

    def tearDown(self) -> None:
        self.panel._renderer.stop()

    def test_default_is_256_mb(self) -> None:
        self.assertEqual(DEFAULT_BUDGET_MB, 256)
        self.assertEqual(self.panel.overlay_cache_mb, 256)

    def test_the_choice_is_applied_at_once_and_saved(self) -> None:
        store = UiStateStore({})
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LSPR_OVERLAY_CACHE_MB", None)
            self.panel.restore_ui_state(store)
        self.assertEqual(self.panel.overlay_cache_mb, 256)
        self.panel.set_overlay_cache_mb(0)
        self.assertEqual(self.panel.overlay_cache_mb, 0)
        self.assertFalse(self.panel._roi_overlay.layers.enabled)
        self.assertEqual(store.get(IMAGE_OVERLAY_CACHE_MB.key), 0)
        self.panel.set_overlay_cache_mb(512)
        self.assertTrue(self.panel._roi_overlay.layers.enabled)
        self.assertEqual(store.get(IMAGE_OVERLAY_CACHE_MB.key), 512)

    def test_the_saved_choice_comes_back_and_a_bad_saved_value_falls_back_to_the_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LSPR_OVERLAY_CACHE_MB", None)
            self.panel.restore_ui_state(UiStateStore({IMAGE_OVERLAY_CACHE_MB.key: 128}))
            self.assertEqual(self.panel.overlay_cache_mb, 128)
            self.panel.restore_ui_state(UiStateStore({IMAGE_OVERLAY_CACHE_MB.key: "lots"}))
            self.assertEqual(self.panel.overlay_cache_mb, 256)

    def test_the_environment_variable_wins_for_this_run_and_does_not_overwrite_the_saved_choice(self) -> None:
        store = UiStateStore({IMAGE_OVERLAY_CACHE_MB.key: 512})
        with patch.dict(os.environ, {"LSPR_OVERLAY_CACHE_MB": "0"}):
            self.panel.restore_ui_state(store)
        self.assertEqual(self.panel.overlay_cache_mb, 0)
        self.assertEqual(store.get(IMAGE_OVERLAY_CACHE_MB.key), 512, "restoring must not save what it just read")

    def test_turning_the_cache_off_leaves_the_overlay_of_the_frame_on_screen(self) -> None:
        """After cache hits the overlay items hold an earlier wavelength; switching off must re-draw them for the frame shown."""
        from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
        import tifffile

        root = Path(tempfile.mkdtemp())
        records = []
        for wavelength in (500.0, 550.0, 600.0):
            path = root / f"w{int(wavelength)}.tif"
            tifffile.imwrite(str(path), np.full((64, 80), 200.0, dtype=np.float32))
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=0), path=path))
        dataset = ImageDataset(folder=root, records=records, source_format="image_stack")
        rendered: list[bool] = []
        self.panel.image_rendered.connect(lambda *_args: rendered.append(True))
        self.panel._dataset.load_dataset(dataset)
        # Let the first render land: the view range settles then and stays put. Wait for the render itself, not for a
        # fixed time: on a busy machine (the full suite, CI) it can take seconds, and a view range that changes later
        # clears the cache in the middle of the scenario below.
        deadline = time.monotonic() + 30.0
        while not rendered and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        self.assertTrue(rendered, "the first render never landed")
        for _drain in range(10):  # the auto-range update that follows is queued, not immediate
            _APP.processEvents()
            time.sleep(0.01)
        roi_id = self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        shifts = {w: np.array([[1.0, 0.0, 1.5 * i], [0.0, 1.0, -i]]) for i, w in enumerate((500.0, 550.0, 600.0))}
        selection = self.panel._selection
        with patch.object(self.chromatic, "affine_for", side_effect=lambda frame: shifts[frame[1]]):
            for _lap in range(2):
                for wavelength in (500.0, 550.0, 600.0):
                    selection.set_wavelength(wavelength)
                    self.panel._draw_roi_overlay(only_if_changed=True)
                    self.panel._view.grab()
            selection.set_wavelength(600.0)
            self.panel._draw_roi_overlay(only_if_changed=True)  # a hit: the items still hold 500 nm, the last one drawn
            stale_xs, _ = self.panel._roi_overlay.sample_circles.outline_xy()
            stale = self.roi_toolbox.display_position(roi_id, (0, 500.0), shifts[500.0])
            self.assertAlmostEqual(float(np.nanmean(stale_xs[:-1])), stale[0], places=6, msg="the scenario must start with stale items")
            self.panel.set_overlay_cache_mb(0)
            xs, _ys = self.panel._roi_overlay.sample_circles.outline_xy()
            expected = self.roi_toolbox.display_position(roi_id, (0, 600.0), shifts[600.0])
            self.assertAlmostEqual(float(np.nanmean(xs[:-1])), expected[0], places=6, msg="stale overlay left on screen")


class OptionsMenuTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = build_main_window()
        self.panel = self.window.findChild(ImagePanel)

    def tearDown(self) -> None:
        self.window.close()

    def _submenu(self):
        options = next(a.menu() for a in self.window.menuBar().actions() if a.text() == "&Options")
        return next(a.menu() for a in options.actions() if a.menu() is not None and a.text() == "ROI Overlay Cache")

    def test_the_menu_lists_off_and_the_sizes_with_the_default_marked_and_checked(self) -> None:
        self.assertIsNotNone(self.panel)
        actions = [a for a in self._submenu().actions() if not a.isSeparator()]
        self.assertFalse(actions[0].isEnabled(), "the first line is information, not a choice")
        labels = [a.text() for a in actions[1:]]
        self.assertEqual(labels, ["Off", "128 MB", "256 MB (default)", "512 MB", "1024 MB"])
        self.assertEqual(tuple(BUDGET_CHOICES_MB), (0, 128, 256, 512, 1024))
        checked = [a.text() for a in actions[1:] if a.isChecked()]
        self.assertEqual(checked, [f"{self.panel.overlay_cache_mb} MB" + (" (default)" if self.panel.overlay_cache_mb == 256 else "")])

    def test_choosing_a_size_sets_the_panel_and_the_checkmark_follows(self) -> None:
        menu = self._submenu()
        by_label = {a.text(): a for a in menu.actions() if not a.isSeparator()}
        by_label["Off"].trigger()
        self.assertEqual(self.panel.overlay_cache_mb, 0)
        menu.aboutToShow.emit()
        self.assertTrue(by_label["Off"].isChecked())
        self.assertIn("Off", [a.text() for a in menu.actions()][0])
        by_label["512 MB"].trigger()
        self.assertEqual(self.panel.overlay_cache_mb, 512)
        menu.aboutToShow.emit()
        self.assertTrue(by_label["512 MB"].isChecked())
        self.assertFalse(by_label["Off"].isChecked())


if __name__ == "__main__":
    unittest.main()
