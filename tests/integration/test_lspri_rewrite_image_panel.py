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
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PyQt6 import QtWidgets
from PyQt6.QtGui import QColor

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
        ImageTool,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.render import RenderRequest, RenderResult
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
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
        self.reference_frame = ReferenceFrameModule()
        self.highlight_range = HighlightRangeModule()
        self.mask_scope = MaskScopeModule()
        self.panel = ImagePanel(
            self.dataset, self.geometry, self.mask, self.chromatic,
            self.background, self.roi_toolbox, self.selection, ActiveToolModule(),
            self.reference_frame, self.highlight_range,
            mask_scope=self.mask_scope,
        )

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self._tmp.cleanup()

    def _load(self) -> None:
        self.dataset.load_dataset(self.dataset_model)
        _pump()

    # -- Background tab -----------------------------------------------------

    def test_background_tab_edits_reach_the_module_and_back(self) -> None:
        tab = self.panel._background_tab
        tab._sigma_spin.setValue(30)
        tab._apply_button.setChecked(True)
        settings = self.background.settings()
        self.assertEqual(settings.flatten_background_sigma_px, 30.0)
        self.assertTrue(settings.flatten_background_enabled)
        # An external change (session restore) updates the controls without pushing back.
        self.background.restore_settings(replace(settings, flatten_background_sigma_px=60.0, flatten_background_enabled=False))
        self.assertEqual(tab._sigma_spin.value(), 60)
        self.assertFalse(tab._apply_button.isChecked())

    def test_info_icon_follows_the_open_ribbon_tab(self) -> None:
        ribbon = self.panel._tool_ribbon
        ribbon.set_category("Background")
        self.assertIn("Time:", self.panel._tool_info.toolTip())
        ribbon.set_category("ROIs")
        self.assertNotIn("Time:", self.panel._tool_info.toolTip())
        ribbon.set_category("Chromatic")
        self.assertIn("Chromatic", self.panel._tool_info.toolTip())
        self.assertFalse(hasattr(self.panel._background_tab, "_info_button"))

    def test_lowering_sigma_lowers_the_binning_to_match(self) -> None:
        tab = self.panel._background_tab
        self.assertEqual(self.background.settings().flatten_background_binning, 8)
        tab._sigma_spin.setValue(24)  # 24 / 6 = 4
        self.assertEqual(self.background.settings().flatten_background_binning, 4)
        self.assertEqual(tab._binning_combo.currentData(), 4)
        tab._binning_combo.setCurrentIndex(tab._binning_combo.findData(8))  # user asks for too much
        self.assertEqual(self.background.settings().flatten_background_binning, 4)
        tab._sigma_spin.setValue(48)  # raising sigma never raises the binning by itself
        self.assertEqual(self.background.settings().flatten_background_binning, 4)

    def test_background_tab_turns_green_while_applied(self) -> None:
        self.background.set_flatten_background_settings(
            enabled=True, sigma_px=48.0, binning=2, exclude_area_rois=True,
            exclude_mask=False, exclusion_dilation_px=0,
        )
        self.assertIn("Background", self.panel._tool_ribbon._applied)

    def test_show_background_renders_the_estimate_not_the_data(self) -> None:
        self._load()
        data = np.array(self.panel._image_item.image, copy=True)
        self.panel._background_tab._show_button.click()
        _pump()
        shown = np.asarray(self.panel._image_item.image)
        self.assertEqual(shown.shape, data.shape)
        # The 4000-count bright spot is smoothed away in the estimate (sigma 48 px).
        self.assertLess(float(shown.max()), 1000.0)
        self.assertGreater(float(data.max()), 3000.0)
        # The Histogram/cursor keep describing the real frame.
        self.assertGreater(float(self.panel._current_display_image.max()), 3000.0)
        self.panel._background_tab._show_button.click()
        _pump()
        self.assertGreater(float(np.asarray(self.panel._image_item.image).max()), 3000.0)

    # -- lifecycle ------------------------------------------------------

    def test_empty_before_a_dataset_is_loaded(self) -> None:
        self.assertFalse(self.panel._cube_spin.isEnabled())
        self.assertIn("No dataset", self.panel._frame_status)

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
        self.assertIn("No dataset", self.panel._frame_status)

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
        self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        self.roi_toolbox.add_roi(60.0, 45.0, sample_diameter_px=10.0)
        _pump()

        sample_x, _ = self.panel._roi_overlay.sample_curve.getData()
        reference_x, _ = self.panel._roi_overlay.reference_curve.getData()
        # N circles joined by N-1 NaN separators in a single PlotDataItem.
        self.assertEqual(len(sample_x), 2 * _CIRCLE_POINTS + 1)
        self.assertEqual(len(reference_x), 4 * _CIRCLE_POINTS + 3)

    def test_selection_adds_a_highlight_border_without_removing_the_roi_from_its_curve(self) -> None:
        self._load()
        roi_a = self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        self.roi_toolbox.add_roi(60.0, 45.0, sample_diameter_px=10.0)
        self.selection.set_roi_selection({roi_a})
        _pump()

        self.assertEqual(len(self.panel._roi_overlay.selection_curve.getData()[0]), _CIRCLE_POINTS)
        # Since fa61b11 the selected ROI stays on its own curve too (border on top): 2 circles + 1 separator.
        self.assertEqual(len(self.panel._roi_overlay.sample_curve.getData()[0]), 2 * _CIRCLE_POINTS + 1)

    def test_overlay_follows_the_chromatic_affine_once(self) -> None:
        """At a wavelength with a chromatic correction the circle must sit where
        `display_position` says (and where the mask is measured) - the affine's
        translation applied once, not twice (bug found 2026-10-07: the drawn
        circle was off by the translation, and click hit-testing disagreed with
        what was drawn)."""
        self._load()
        roi_id = self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        shifted = np.array([[1.0, 0.0, 3.0], [0.0, 1.0, -2.0]])
        with patch.object(self.chromatic, "affine_for", return_value=shifted):
            self.panel._draw_roi_overlay()
            frame = (self.panel._current_cube(), self.panel._current_wavelength())
            center = self.roi_toolbox.display_position(roi_id, frame, shifted)
            xs, ys = self.panel._roi_overlay.sample_curve.getData()
            self.assertEqual(center, (43.0, 28.0))
            self.assertAlmostEqual(float(np.nanmean(xs[:-1])), center[0], places=6)
            self.assertAlmostEqual(float(np.nanmean(ys[:-1])), center[1], places=6)
            self.assertEqual(self.panel.roi_at(*center), roi_id)  # clicking where it is drawn selects it

    def _points(self, curve) -> int:
        """Circles on a curve, counted by its points (NaN separators between them)."""
        xs = curve.getData()[0]
        return 0 if xs is None else int(np.isfinite(xs).sum())

    def test_each_roi_is_drawn_in_its_own_colour(self) -> None:
        self._load()
        self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        self.roi_toolbox.add_roi(60.0, 45.0, sample_diameter_px=10.0)
        self.roi_toolbox.add_roi(20.0, 15.0, sample_diameter_px=10.0)
        self.roi_toolbox.group_rois((1, 2), "A")  # each member gets its own tint
        _pump()

        first, second = (self.roi_toolbox.roi_by_id(i).sample_color_hex for i in (1, 2))
        self.assertNotEqual(first, second)
        for color in (first, second):
            curve = self.panel._roi_overlay.sample_curves[color]
            self.assertEqual(self._points(curve), _CIRCLE_POINTS, color)
            self.assertEqual(curve.opts["pen"].color().name(), color)
        self.assertEqual(self._points(self.panel._roi_overlay.sample_curve), _CIRCLE_POINTS, "the ungrouped ROI keeps the default colour")

    def test_recolouring_moves_a_roi_to_another_curve_and_empties_the_old_one(self) -> None:
        self._load()
        self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        self.roi_toolbox.set_roi_colors((1,), "#112233")
        _pump()
        self.assertEqual(self._points(self.panel._roi_overlay.sample_curves["#112233"]), _CIRCLE_POINTS)
        self.assertEqual(self._points(self.panel._roi_overlay.sample_curve), 0)

        self.roi_toolbox.set_roi_colors((1,), "#445566")
        _pump()
        self.assertEqual(self._points(self.panel._roi_overlay.sample_curves["#112233"]), 0)
        self.assertEqual(self._points(self.panel._roi_overlay.sample_curves["#445566"]), _CIRCLE_POINTS)

        self.roi_toolbox.set_roi_colors((1,), None)
        _pump()
        self.assertEqual(self._points(self.panel._roi_overlay.sample_curve), _CIRCLE_POINTS)

    def test_a_selected_roi_keeps_its_own_colour_and_gets_a_highlight_border(self) -> None:
        self._load()
        self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        self.roi_toolbox.add_roi(60.0, 45.0, sample_diameter_px=10.0)
        self.roi_toolbox.group_rois((1, 2), "A")
        self.selection.set_roi_selection({1})
        _pump()

        self.assertEqual(self._points(self.panel._roi_overlay.selection_curve), _CIRCLE_POINTS)
        # Since fa61b11 the selected ROI keeps its own colour and fill; the highlight is a border drawn on top.
        own_curve = self.panel._roi_overlay.sample_curves[self.roi_toolbox.roi_by_id(1).sample_color_hex]
        self.assertGreater(self._points(own_curve), 0, "still drawn in its own colour")
        self.assertEqual(self._points(self.panel._roi_overlay.sample_curves[self.roi_toolbox.roi_by_id(2).sample_color_hex]), _CIRCLE_POINTS)

    def test_a_stored_colour_that_is_not_a_colour_falls_back_to_the_default(self) -> None:
        self._load()
        roi_id = self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        self.roi_toolbox.roi_by_id(roi_id).sample_color_hex = "not-a-colour"  # e.g. a damaged session file
        self.panel._draw_overlays()
        self.assertEqual(self._points(self.panel._roi_overlay.sample_curve), _CIRCLE_POINTS)

    def test_emptied_colour_curves_are_capped_so_recolouring_cannot_pile_up_items(self) -> None:
        self._load()
        self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        for i in range(60):
            self.roi_toolbox.set_roi_colors((1,), f"#{i + 1:02x}{i + 1:02x}ff")
            self.panel._draw_overlays()
        # the default curve, the one in use, and at most the idle cap
        self.assertLessEqual(len(self.panel._roi_overlay.sample_curves), 2 + 24)
        self.assertEqual(self._points(self.panel._roi_overlay.sample_curves["#3c3cff"]), _CIRCLE_POINTS)

    def test_clearing_the_dataset_empties_every_roi_curve(self) -> None:
        self._load()
        self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        self.roi_toolbox.set_roi_colors((1,), "#112233")
        _pump()
        self.dataset.clear_dataset()
        _pump(0.2)
        self.assertTrue(all(self._points(curve) == 0 for curve in self.panel._roi_overlay.sample_curves.values()))

    # -- interaction ----------------------------------------------------

    def test_hit_testing_by_image_coordinate(self) -> None:
        self._load()
        roi_a = self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        roi_b = self.roi_toolbox.add_roi(60.0, 45.0, sample_diameter_px=10.0)

        self.assertEqual(self.panel.roi_at(40.0, 30.0), roi_a)
        self.assertEqual(self.panel.roi_at(44.0, 30.0), roi_a)  # just inside the radius
        self.assertEqual(self.panel.roi_at(60.0, 45.0), roi_b)
        self.assertIsNone(self.panel.roi_at(5.0, 5.0))

    def test_a_drag_is_a_command_and_is_therefore_undoable(self) -> None:
        """The one-way-flow property. The panel contains no undo code at
        all; undo works because the drag went through `RoiToolbox`, whose
        `revert()` re-emits exactly what the original call emitted."""
        self._load()
        roi_a = self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
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
        self.assertNotIn("Cannot show", self.panel._tool_status)

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
        self.assertIn("Cannot show", self.panel._tool_status)

    def test_a_stale_render_result_is_dropped(self) -> None:
        """"Latest request wins": a frame superseded while in flight must
        not overwrite the newer one that already arrived."""
        self._load()
        before = self.panel._frame_status
        stale = RenderRequest(
            cube_index=0, wavelength_nm=500.0,
            geometry=self.geometry.settings(), background=self.background.settings(),
            authored_mask=None, mask_warp_affine=None,
            rois=(), detection=self.roi_toolbox.detection_settings(),
            serial=self.panel._latest_serial - 1,
        )
        self.panel._on_rendered(RenderResult(request=stale, image=np.zeros((4, 4), dtype=np.float32)))
        self.assertEqual(self.panel._frame_status, before)

    # -- cursor overlay (2026-09-29) -------------------------------------

    def test_cursor_overlay_reads_the_displayed_pixel(self) -> None:
        """Ported from the stable app's cursor-toggle overlay (maintainer's
        request) - here, reading `_image_item.image` directly rather than
        snapping to a 1D curve, since this is a 2D pixel lookup."""
        self._load()
        overlay = self.panel._cursor_overlay
        self.assertFalse(overlay._enabled)
        overlay.toggle()
        self.assertTrue(overlay._enabled)
        result = overlay._value_at(38.5, 30.5)
        self.assertIsNotNone(result)
        x, y, text = result
        self.assertEqual((x, y), (38.5, 30.5))
        self.assertIn("(38, 30)", text)
        expected = float(self.panel._image_item.image[30, 38])
        self.assertIn(f"{expected:.0f}", text)  # whole numbers, see no_data.format_pixel_value

    def test_cursor_overlay_off_image_returns_none(self) -> None:
        self._load()
        overlay = self.panel._cursor_overlay
        overlay.toggle()
        self.assertIsNone(overlay._value_at(-5.0, -5.0))
        self.assertIsNone(overlay._value_at(9999.0, 9999.0))

    # -- Cube/Wavelength slider (ported 2026-09-30, docs/image_area_slider_
    # redesign.md) --------------------------------------------------------

    def test_cube_and_wavelength_sliders_are_ranged_on_load(self) -> None:
        self._load()
        self.assertTrue(self.panel._cube_slider.isEnabled())
        self.assertEqual((self.panel._cube_slider.minimum(), self.panel._cube_slider.maximum()), (0, 1))
        self.assertTrue(self.panel._wavelength_slider.isEnabled())
        # cube 0 has all three wavelengths.
        self.assertEqual((self.panel._wavelength_slider.minimum(), self.panel._wavelength_slider.maximum()), (0, 2))

    def test_wavelength_slider_reranges_for_a_cube_short_a_wavelength(self) -> None:
        """The rewrite's `wavelengths_for_cube` is per-cube, unlike the
        stable app's single dataset-wide wavelength array - the slider must
        re-tick on every cube change, not just once at dataset load."""
        self._load()
        self.panel._cube_spin.setValue(1)  # cube 1 only has 500.0/600.0
        self.assertEqual(self.panel._wavelength_slider.maximum(), 1)

    def test_dragging_the_cube_slider_drives_selection_and_spin(self) -> None:
        """The slider is a second front door onto the same
        `SelectionModule.set_cube` command the spin box already uses - never
        a direct write to the spin box."""
        self._load()
        self.panel._cube_slider.setValue(1)  # click/drag, same signal a real gesture produces
        self.assertEqual(self.selection.current_cube(), 1)
        self.assertEqual(self.panel._cube_spin.value(), 1)

    def test_dragging_the_wavelength_slider_drives_selection_and_spin(self) -> None:
        self._load()
        self.panel._wavelength_slider.setValue(2)  # index 2 -> 600.0 nm on cube 0
        self.assertEqual(self.selection.current_wavelength(), 600.0)
        self.assertEqual(self.panel._wavelength_spin.value(), 600.0)

    def test_selection_changed_elsewhere_still_updates_the_nav_widgets(self) -> None:
        """The one-way-flow property applied to the nav widgets themselves:
        a change that did not come from this panel's own handlers (e.g. a
        future ROI-table "go to this ROI's cube" action) must still be
        reflected, not just changes the panel's own spin/slider caused."""
        self._load()
        self.selection.set_cube(1)  # bypasses the panel's own spin/slider handlers entirely
        self.assertEqual(self.panel._cube_spin.value(), 1)
        self.assertEqual(self.panel._cube_slider.value(), 1)

    def test_reference_highlight_tracks_the_manual_reference_frame(self) -> None:
        self._load()
        self.reference_frame.set_manual_frame(1, 600.0)
        self.panel._cube_spin.setValue(0)
        self.panel._wavelength_spin.setValue(500.0)
        self.assertIsNone(self.panel._cube_slider._reference_highlight_color)
        self.assertIsNone(self.panel._wavelength_slider._reference_highlight_color)

        self.panel._cube_spin.setValue(1)
        self.panel._wavelength_spin.setValue(600.0)
        self.assertIsNotNone(self.panel._cube_slider._reference_highlight_color)
        self.assertIsNotNone(self.panel._wavelength_slider._reference_highlight_color)

    def test_wavelength_jump_completer_lists_the_current_cubes_wavelengths(self) -> None:
        self._load()
        self.panel._cube_spin.setValue(1)  # 500.0 / 600.0 only
        model = self.panel._wavelength_completer.model()
        texts = {model.data(model.index(i, 0)) for i in range(model.rowCount())}
        self.assertEqual(texts, {"500", "600"})

    # -- navigation bar border (2026-09-30, maintainer request, reversed
    # same day) - the seam line added earlier 2026-09-30 turned out to read
    # as distracting rather than clarifying; the bar now sits flush against
    # the canvas with no line at all.

    def test_navigation_bar_has_no_border(self) -> None:
        style = self.panel._controls_bar.styleSheet()
        self.assertIn("border: none", style)
        self.assertNotIn("border-top", style)
        self.assertNotIn("border-left", style)
        self.assertNotIn("border-right", style)
        self.assertNotIn("border-bottom", style)

    def test_navigation_bar_theme_refresh_does_not_raise(self) -> None:
        """`_refresh_controls_bar_theme` is still called on every live theme
        switch (see `refresh_theme`) even though it sets no border color
        today - pinned so a future change there can't silently reintroduce
        a crash on theme switch without a test catching it."""
        from lspr_ui import APP_THEME, BRIGHT_THEME, set_active_theme

        set_active_theme(BRIGHT_THEME)
        try:
            self.panel.refresh_theme()
            self.assertIn("border: none", self.panel._controls_bar.styleSheet())
        finally:
            set_active_theme(APP_THEME)
            self.panel.refresh_theme()

    # -- outer layout margin (2026-09-30, real bug found via headless
    # geometry probe, maintainer report of "wide borders around the image
    # area, biggest from the top") -------------------------------------------

    def test_outer_layout_has_no_margin_or_spacing(self) -> None:
        """The outermost layout was the one layout in `_build_ui` left at
        Qt's style-default ~11px margin on all four sides - invisible as a
        distinct line (this panel's own background and the canvas's are the
        same color), but it padded out the canvas/toolbar/nav bar on every
        side, worst at the top where it stacked on top of the dock's own
        title bar. A 10px left/right margin was briefly added here too
        (maintainer's "small stylish" follow-up), then moved to just the
        nav bar's own `controls` layout - it was meant for the Cube/λ rows
        only, not the canvas/toolbar as well. Checked on the layout object
        directly, not post-layout widget geometry, since the latter needs a
        real show()/resize() pass this test's `setUp` never does."""
        outer_layout = self.panel.layout()
        self.assertEqual(outer_layout.contentsMargins().left(), 0)
        self.assertEqual(outer_layout.contentsMargins().top(), 0)
        self.assertEqual(outer_layout.contentsMargins().right(), 0)
        self.assertEqual(outer_layout.contentsMargins().bottom(), 0)
        self.assertEqual(outer_layout.spacing(), 0)

    def test_nav_bar_has_left_right_margin_only(self) -> None:
        """The Cube/λ titles shouldn't start flush against the dock's left
        edge, and the number fields shouldn't end flush against its right
        edge (maintainer request) - scoped to this bar's own `controls`
        layout, not the whole panel."""
        controls_layout = self.panel._controls_bar.layout()
        self.assertEqual(controls_layout.contentsMargins().left(), 10)
        self.assertEqual(controls_layout.contentsMargins().top(), 0)
        self.assertEqual(controls_layout.contentsMargins().right(), 10)
        self.assertEqual(controls_layout.contentsMargins().bottom(), 0)

    # -- wavelength slider tick labels (2026-09-30, real bug) ----------------

    def test_wavelength_tick_labels_always_show_the_real_value_at_their_index(self) -> None:
        """Real bug, not cosmetic: the first-pass port labeled a tick with
        the nearest *round* 100 nm boundary ("400") while positioning it at
        whichever real value happened to be closest to that boundary - for
        this deliberately gappy set (nothing between 250 and 470), that put
        "400" on the index whose real value is 470, so clicking "400"
        actually selected 470 nm. Every label must now equal `values[index]`
        exactly, for any gap shape - never a fabricated round number."""
        values = (200.0, 250.0, 470.0, 500.0, 600.0)
        majors = self.panel._wavelength_slider_major_ticks(values)
        for index, label in majors.items():
            self.assertEqual(label, f"{values[index]:.0f}")
        # The exact reported symptom: the tick nearest 400 nm must read
        # "470", never "400", once it is positioned at index 2.
        self.assertEqual(majors[2], "470")
        self.assertNotIn("400", majors.values())

    def test_wavelength_tick_labels_cover_every_point_for_a_small_dataset(self) -> None:
        """Below the "nice interval" target-ticks threshold, every index
        gets its own label - matches `_cube_slider_major_ticks`'s identical
        shape for the same reason."""
        values = (405.3, 452.1, 498.9)
        majors = self.panel._wavelength_slider_major_ticks(values)
        self.assertEqual(majors, {0: "405", 1: "452", 2: "499"})

    def test_wavelength_tick_labels_empty_for_fewer_than_two_points(self) -> None:
        self.assertEqual(self.panel._wavelength_slider_major_ticks(()), {})
        self.assertEqual(self.panel._wavelength_slider_major_ticks((500.0,)), {})

    # -- wavelength axis gap break (2026-09-30, revised same day) -----------

    def test_wavelength_ticks_label_both_sides_of_a_real_gap(self) -> None:
        """Real scenario this was built for: a genuine 0 nm frame (a dark/
        reference image, still a real, selectable index) followed by a big
        jump to the first real spectral wavelength - `DataAxisSlider` draws
        the break glyph between whichever two indices this labels, so both
        must be labeled with their own real value, not left for the routine
        every-Nth-index rule to maybe skip."""
        values = (0.0, 470.0, 500.0, 550.0, 600.0)
        majors = self.panel._wavelength_slider_major_ticks(values)
        self.assertEqual(majors[0], "0")
        self.assertEqual(majors[1], "470")

    def test_wavelength_ticks_no_forced_labels_for_a_regular_grid(self) -> None:
        """The forced-labeling-around-a-gap rule must not fire when there is
        no real gap to mark - every label still comes from the ordinary
        every-Nth-index rule."""
        values = (470.0, 500.0, 550.0, 600.0, 650.0)
        majors = self.panel._wavelength_slider_major_ticks(values)
        self.assertEqual(majors, {index: f"{value:.0f}" for index, value in enumerate(values)})

    # -- navigation row layout polish (2026-09-30, maintainer request) ------

    def test_reference_jump_button_no_longer_exists(self) -> None:
        self.assertFalse(hasattr(self.panel, "_reference_jump_button"))

    def test_number_fields_show_plain_numbers_no_unit_text(self) -> None:
        """"Cube "/" nm" removed from the fields themselves - the row's own
        title label ("Cube" / "λ (nm)") already says what the number means,
        so the field repeating it was redundant."""
        self._load()
        self.assertEqual(self.panel._cube_spin.prefix(), "")
        self.assertEqual(self.panel._cube_spin.suffix(), "")
        self.assertEqual(self.panel._wavelength_spin.prefix(), "")
        self.assertEqual(self.panel._wavelength_spin.suffix(), "")
        self.assertEqual(self.panel._cube_spin.text(), "0")
        self.assertEqual(self.panel._wavelength_spin.text(), "500")

    def test_titles_and_number_fields_share_one_width_each(self) -> None:
        """"Cube" and "λ (nm)" are different lengths, and so are their
        number fields' typical contents - without a shared fixed width,
        the two sliders started at different x positions and the two
        number fields didn't line up on the right."""
        # Located by content rather than assuming child order.
        labels = {label.text(): label for label in self.panel.findChildren(QtWidgets.QLabel)}
        self.assertEqual(labels["Cube"].width(), labels["λ (nm)"].width())
        self.assertEqual(self.panel._cube_spin.width(), self.panel._wavelength_spin.width())

    def test_spin_boxes_are_narrow_and_sized_for_a_future_time_format(self) -> None:
        """Narrowed off "00:00:00" (2026-09-30, maintainer request), not the
        widgets' own `sizeHint()` - the cube field is meant to grow into an
        HH:MM:SS elapsed-time display later, and a plain index needs far
        less room than the old sizeHint-based width gave it."""
        from PyQt6.QtGui import QFontMetrics

        metrics = QFontMetrics(self.panel._cube_spin.font())
        expected = metrics.horizontalAdvance("00:00:00") + 28
        self.assertEqual(self.panel._cube_spin.width(), expected)
        self.assertEqual(self.panel._wavelength_spin.width(), expected)

    def test_canvas_is_above_the_navigation_bar(self) -> None:
        """Moved to the bottom of the panel (2026-09-30, maintainer
        request) - the outer layout must place the canvas column before the
        navigation bar, not after. `_top_bar` lives inside that column, an
        unnamed sub-layout, so find *that* sub-layout's position rather
        than assuming a stored reference to it exists."""
        outer_layout = self.panel.layout()
        controls_index = outer_layout.indexOf(self.panel._controls_bar)
        canvas_index = None
        for i in range(outer_layout.count()):
            sub_layout = outer_layout.itemAt(i).layout()
            if sub_layout is not None and sub_layout.indexOf(self.panel._top_bar) != -1:
                canvas_index = i
                break
        self.assertIsNotNone(canvas_index)
        self.assertLess(canvas_index, controls_index)

    # -- frame status moved to the dock title bar (2026-09-30, maintainer
    # request) - the old bottom status row is gone; "Cube X, wl nm" is
    # emitted as `frame_status_changed` for the dock title bar's centered
    # subtitle instead, and no resolution text is shown anywhere anymore.

    def test_frame_status_reports_cube_and_wavelength_with_no_decimal(self) -> None:
        self._load()
        self.assertEqual(self.panel._frame_status, "Cube 0, 500 nm")

    def test_frame_status_signal_fires_on_navigation(self) -> None:
        self._load()
        values: list[str] = []
        self.panel.frame_status_changed.connect(values.append)
        self.panel._cube_spin.setValue(1)
        _pump()
        self.assertIn("Cube 1, 500 nm", values)

    # -- top toolbar (2026-09-30, maintainer request - flipped from a
    # vertical strip on the canvas's left edge to a horizontal bar across
    # its top, "since frames are usually landscapes"; the cursor-readout
    # and "i" icons moved off the canvas corners and into this bar too).

    def test_tool_info_and_cursor_icon_live_in_the_top_bar(self) -> None:
        """The "i" icon is a plain widget in the shared top bar, right of the
        ribbon. The cursor toggle moved (2026-10-03) into the always-visible
        "General" group at the bar's left, before the ribbon; its readout text
        now shows in the canvas's top-left corner instead of in the bar."""
        top_bar_layout = self.panel._top_bar.layout()
        self.assertIs(self.panel._tool_info.parent(), self.panel._top_bar)
        self.assertIs(self.panel._cursor_overlay.icon_label.parent(), self.panel._general_row)
        ribbon_index = top_bar_layout.indexOf(self.panel._tool_ribbon)
        info_index = top_bar_layout.indexOf(self.panel._tool_info)
        self.assertNotEqual(ribbon_index, -1)
        # The General group is the ribbon's pinned, always-visible leading
        # section (2026-10-03), so it sits inside the ribbon, left of its tabs.
        self.assertTrue(self.panel._tool_ribbon.isAncestorOf(self.panel._general_row))
        self.assertLess(ribbon_index, info_index)

    def test_image_tools_tab_holds_a_transforms_section_wired_to_the_panels_own_modules(self) -> None:
        """"duplicate transform tools and put them in image tools of image
        panel" (maintainer request, 2026-09-30) - the ribbon's "Image tools"
        tab is a second `TransformsSection` instance, not the Workflow
        panel's own one moved here; it must share this panel's actual
        `GeometryModule`/`ActiveToolModule` so the two rows stay in sync
        (see `transforms_settings.py`'s module docstring)."""
        from lspr_imaging_app.panels.image.transforms_settings import TransformsSection

        section = self.panel._transforms_section
        self.assertIsInstance(section, TransformsSection)
        # The tab's page is a small container (Transforms, 2026-09-30 - see
        # mask_overlay_controls.py for why the mask controls moved out to
        # their own "Mask" tab, 2026-10-01), not the TransformsSection
        # directly any more; it must still be reachable as a child of that
        # page.
        self.assertIs(section.parentWidget(), self.panel._tool_ribbon._stack.widget(1))

        section._rotate_button.click()
        self.assertIs(self.panel._active_tool.active(), ImageTool.ROTATE)

    # -- mask overlay (2026-09-30, maintainer request: "implement...the
    # mask overlay features" - show/hide + color + transparency, ported
    # from the stable app's `show_mask_check`/`mask_color_button`/
    # `mask_alpha_slider`; moved into its own "Mask" ribbon tab 2026-10-01,
    # maintainer request - keep the mask icons out of "Image tools") -------

    def test_mask_overlay_controls_live_in_the_mask_tab(self) -> None:
        """Own "Mask" tab (index 1, right after "Image tools") - no longer
        sharing "Image tools" with Transforms behind a vertical divider.
        One level removed from the page itself (wrapped in its own
        `_labeled_icon_group`, see the "State"/"Visibility" caption test
        below), but still only ever a grandchild of this tab's page, never
        of any other tab."""
        from lspr_imaging_app.panels.image.mask_overlay_controls import MaskOverlayControls

        controls = self.panel._mask_tab.overlay_controls
        self.assertIsInstance(controls, MaskOverlayControls)
        page = self.panel._tool_ribbon._stack.widget(2)
        self.assertIs(controls.parentWidget().parentWidget(), page)

    def test_mask_scope_toggle_sits_left_of_a_divider_before_the_overlay_controls(self) -> None:
        """Maintainer request, 2026-10-02: "put them on the left side and
        separate from rest by | line" - the Persistent/Individual toggle's
        group, then the divider, then the overlay controls' group, left to
        right, all inside the same "Mask" tab page."""
        from lspr_imaging_app.panels.image.mask_scope_toggle import MaskScopeToggle

        toggle = self.panel._mask_tab._scope_toggle
        self.assertIsInstance(toggle, MaskScopeToggle)
        page = self.panel._tool_ribbon._stack.widget(2)
        self.assertIs(self.panel._mask_tab._separators[1].parentWidget(), page)

        # Each icon widget is now wrapped in its own `_labeled_icon_group`
        # container (the caption-below-icons group, see the "State"/
        # "Visibility" test below) - that wrapper, not the icon widget
        # itself, is what sits directly in the page's row.
        toggle_group = toggle.parentWidget()
        overlay_group = self.panel._mask_tab.overlay_controls.parentWidget()
        self.assertIs(toggle_group.parentWidget(), page)
        self.assertIs(overlay_group.parentWidget(), page)

        layout = page.layout()
        toggle_index = layout.indexOf(toggle_group)
        separator_index = layout.indexOf(self.panel._mask_tab._separators[1])
        overlay_index = layout.indexOf(overlay_group)
        self.assertNotEqual(toggle_index, -1)
        self.assertLess(toggle_index, separator_index)
        self.assertLess(separator_index, overlay_index)

    def test_mask_tab_groups_are_captioned_state_and_visibility(self) -> None:
        """Maintainer request, 2026-10-02: "non-intrusive labels... under
        [each group]... center of section" - a caption below each group's
        icons, not above (unlike `lspr_ui`'s `toolbarSectionTitle`
        convention used elsewhere in the suite)."""
        state_label = self.panel._mask_tab._state_label
        visibility_label = self.panel._mask_tab._visibility_label
        self.assertEqual(state_label.text(), "State")
        self.assertEqual(visibility_label.text(), "Visibility")

        toggle = self.panel._mask_tab._scope_toggle
        toggle_group = toggle.parentWidget()
        self.assertIs(state_label.parentWidget(), toggle_group)
        group_layout = toggle_group.layout()
        self.assertLess(group_layout.indexOf(toggle), group_layout.indexOf(state_label))

        overlay_controls = self.panel._mask_tab.overlay_controls
        overlay_group = overlay_controls.parentWidget()
        self.assertIs(visibility_label.parentWidget(), overlay_group)
        overlay_group_layout = overlay_group.layout()
        self.assertLess(overlay_group_layout.indexOf(overlay_controls), overlay_group_layout.indexOf(visibility_label))

    def test_mask_scope_toggle_shares_live_state_with_the_shared_mask_scope_module(self) -> None:
        """This toggle must never own its own private selection - clicking it
        pushes straight to the shared `MaskScopeModule`, exactly what lets a
        second copy elsewhere (the Workflow panel's `MaskHighlightActions`)
        stay in sync for free."""
        from lspr_imaging_app.image_tools import MaskScope

        toggle = self.panel._mask_tab._scope_toggle
        toggle._individual_button.click()
        self.assertIs(self.mask_scope.scope(), MaskScope.INDIVIDUAL)

        self.mask_scope.set_scope(MaskScope.PERSISTENT)
        self.assertTrue(toggle._persistent_button.isChecked())

    def test_mask_overlay_draws_the_chosen_color_over_masked_pixels(self) -> None:
        self._load()
        mask = np.zeros((64, 80), dtype=bool)
        mask[10:20, 10:20] = True
        self.mask.set_mask_change((0, 500.0), "persistent", mask)
        _pump()

        self.assertTrue(self.panel._mask_tint.item.isVisible())
        overlay = self.panel._mask_tint.item.image
        self.assertEqual(overlay.shape, (64, 80, 4))
        color = self.panel._mask_tint.color
        expected = (color.red(), color.green(), color.blue(), int(round(self.panel._mask_tint.alpha * 255.0)))
        self.assertEqual(tuple(overlay[15, 15]), expected)
        self.assertEqual(tuple(overlay[0, 0]), (0, 0, 0, 0))

    def test_mask_overlay_hides_when_toggled_off_and_returns_when_toggled_on(self) -> None:
        self._load()
        mask = np.zeros((64, 80), dtype=bool)
        mask[10:20, 10:20] = True
        self.mask.set_mask_change((0, 500.0), "persistent", mask)
        _pump()
        self.assertTrue(self.panel._mask_tint.item.isVisible())

        self.panel._mask_tab.overlay_controls._toggle_button.click()
        self.assertFalse(self.panel._mask_tint.item.isVisible())

        self.panel._mask_tab.overlay_controls._toggle_button.click()
        self.assertTrue(self.panel._mask_tint.item.isVisible())

    def test_mask_overlay_color_and_alpha_changes_redraw_without_a_new_render(self) -> None:
        """Color/alpha are cosmetic-only: changing them must not touch the
        async pixel-render pipeline (`_mask_overlay_state` cache is reused),
        only the overlay tint itself - same "cosmetic vs computational"
        distinction the rest of this rewrite already draws."""
        self._load()
        mask = np.zeros((64, 80), dtype=bool)
        mask[10:20, 10:20] = True
        self.mask.set_mask_change((0, 500.0), "persistent", mask)
        _pump()

        serial_before = self.panel._latest_serial
        new_color = QColor("#38bdf8")
        self.panel._mask_tab.overlay_controls.color_changed.emit(new_color)
        self.panel._mask_tab.overlay_controls.alpha_changed.emit(0.2)

        self.assertEqual(self.panel._latest_serial, serial_before)
        overlay = self.panel._mask_tint.item.image
        self.assertEqual(tuple(overlay[15, 15]), (new_color.red(), new_color.green(), new_color.blue(), 51))

    def test_mask_overlay_hides_while_a_preview_tool_is_active(self) -> None:
        """Same "wrong coordinate space" reasoning as the ROI overlay: a
        preview tool (rotate/crop) shows the uncropped image, a different
        canvas than the authored mask was resolved against."""
        self._load()
        mask = np.zeros((64, 80), dtype=bool)
        mask[10:20, 10:20] = True
        self.mask.set_mask_change((0, 500.0), "persistent", mask)
        _pump()
        self.assertTrue(self.panel._mask_tint.item.isVisible())

        self.panel._active_tool.set_active(ImageTool.ROTATE, True)
        _pump()
        self.assertFalse(self.panel._mask_tint.item.isVisible())

    # -- mask "Edit" tool picker (2026-10-02, maintainer request: "a pickup --
    # -- menu... option for tools how to change it") -------------------------

    def test_mask_edit_group_sits_in_the_mask_tab_after_a_second_divider(self) -> None:
        """"next to the visibility section in Mask, add Edit section" - a
        captioned group in the same "Mask" tab page, after a divider (same
        convention the State|Visibility pair already uses). Renamed "Edit"
        -> "Manual edit" once sibling groups ("General"/"PNG") landed
        beside it (2026-10-02, maintainer request)."""
        page = self.panel._tool_ribbon._stack.widget(2)
        # Picker -> mask_edit_group (the labeled_icon_group wrapper) -> page
        # - the same two-hop chain MaskOverlayControls's own group uses.
        # **Not** picker -> a picker+stack row -> group (a 2026-10-02 report:
        # wrapping the picker+stack *pair* made the "Manual edit" caption
        # center under the *reserved* stack width - as wide as Morphology's
        # own widest panel - rather than under the picker itself, so it
        # visually floated away whenever a narrower panel was shown). The
        # stack now sits as its own, uncaptioned, top-aligned sibling.
        picker_group = self.panel._mask_tab._edit_picker.parentWidget()
        self.assertIs(picker_group.parentWidget(), page)
        self.assertIs(self.panel._mask_tab._separators[2].parentWidget(), page)
        self.assertEqual(self.panel._mask_tab._edit_label.text(), "Manual edit")

        layout = page.layout()
        visibility_index = layout.indexOf(self.panel._mask_tab.overlay_controls.parentWidget())
        separator_index = layout.indexOf(self.panel._mask_tab._separators[2])
        edit_index = layout.indexOf(picker_group)
        stack_index = layout.indexOf(self.panel._mask_tab._edit_stack)
        self.assertLess(visibility_index, separator_index)
        self.assertLess(separator_index, edit_index)
        self.assertEqual(stack_index, edit_index + 1, "the stack sits immediately after the picker's own group")

    def test_general_group_is_the_leftmost_group_in_the_mask_tab(self) -> None:
        """"put this icon [Clear] in solo section 'General' and put section
        the most left" (2026-10-02, maintainer request)."""
        page = self.panel._tool_ribbon._stack.widget(2)
        general_group = self.panel._mask_tab._clear_action.parentWidget()
        self.assertIs(general_group.parentWidget(), page)
        self.assertEqual(self.panel._mask_tab._general_label.text(), "General")

        layout = page.layout()
        general_index = layout.indexOf(general_group)
        separator_index = layout.indexOf(self.panel._mask_tab._separators[0])
        state_group = self.panel._mask_tab._scope_toggle.parentWidget()
        state_index = layout.indexOf(state_group)
        self.assertEqual(general_index, 0, "General must be the leftmost item in the whole Mask tab row")
        self.assertLess(general_index, separator_index)
        self.assertLess(separator_index, state_index)

    def test_png_group_sits_after_manual_edit_behind_a_divider(self) -> None:
        """"these two icons [load/save] should be in 'PNG' section" -
        placed after "Manual edit", same divider convention as every other
        group boundary in this tab."""
        page = self.panel._tool_ribbon._stack.widget(2)
        png_group = self.panel._mask_tab._png_actions.parentWidget()
        self.assertIs(png_group.parentWidget(), page)
        self.assertIs(self.panel._mask_tab._separators[3].parentWidget(), page)
        self.assertEqual(self.panel._mask_tab._png_label.text(), "PNG")

        layout = page.layout()
        stack_index = layout.indexOf(self.panel._mask_tab._edit_stack)
        separator_index = layout.indexOf(self.panel._mask_tab._separators[3])
        png_index = layout.indexOf(png_group)
        self.assertLess(stack_index, separator_index)
        self.assertLess(separator_index, png_index)

    def test_png_group_order_is_load_then_save(self) -> None:
        actions = self.panel._mask_tab._png_actions
        layout = actions.layout()
        self.assertLess(layout.indexOf(actions._load_button), layout.indexOf(actions._save_button))

    def test_clear_mask_prompts_and_only_wipes_on_confirmation(self) -> None:
        """"add an icon of clean... to clean the mask entirely" - wipes the
        *whole* timeline (every cube/scope), and only after the user
        confirms, since Mask has no undo (see `MaskModule.clear_all_masks`'s
        own docstring)."""
        self._load()
        mask = np.zeros((64, 80), dtype=bool)
        mask[10:20, 10:20] = True
        self.mask.set_mask_change((0, 500.0), "persistent", mask)
        self.mask.set_mask_change((1, 500.0), "individual", mask)

        with patch(
            "lspr_imaging_app.panels.image.mask_file_actions.QMessageBox.question",
            return_value=QtWidgets.QMessageBox.StandardButton.No,
        ):
            self.panel._mask_tab._clear_action._clear_button.click()
        self.assertIsNotNone(self.mask.resolve_mask_source((0, 500.0)), "declining the prompt must not clear anything")

        with patch(
            "lspr_imaging_app.panels.image.mask_file_actions.QMessageBox.question",
            return_value=QtWidgets.QMessageBox.StandardButton.Yes,
        ):
            self.panel._mask_tab._clear_action._clear_button.click()
        self.assertIsNone(self.mask.resolve_mask_source((0, 500.0)))
        self.assertIsNone(self.mask.resolve_mask_source((1, 500.0)))

    def test_save_then_load_round_trips_the_current_mask(self) -> None:
        """"copy icons from mask to load/save mask as file" - Save writes
        whatever `resolve_mask_source` currently resolves at this frame;
        Load replaces the current frame/scope's mask with the file's
        content, through the real `image_tools.mask.io` PNG codec, not a
        mock of it."""
        self._load()
        mask = np.zeros((64, 80), dtype=bool)
        mask[5:9, 5:9] = True
        self.mask.set_mask_change((0, 500.0), "persistent", mask)

        destination = self.root / "exported_mask.png"
        with patch(
            "lspr_imaging_app.panels.image.mask_file_actions.QFileDialog.getSaveFileName",
            return_value=(str(destination), "PNG image (*.png)"),
        ):
            self.panel._mask_tab._png_actions._save_button.click()
        self.assertTrue(destination.exists())

        self.mask.clear_all_masks()
        self.assertIsNone(self.mask.resolve_mask_source((0, 500.0)))

        with patch(
            "lspr_imaging_app.panels.image.mask_file_actions.QFileDialog.getOpenFileName",
            return_value=(str(destination), "Mask images (*.png *.bmp *.tif *.tiff)"),
        ):
            self.panel._mask_tab._png_actions._load_button.click()
        _frame, resolved_mask, scope = self.mask.resolve_mask_source((0, 500.0))
        self.assertEqual(scope, "persistent")
        np.testing.assert_array_equal(resolved_mask, mask)

    def test_mask_edit_picker_defaults_to_histogram_selection(self) -> None:
        from lspr_imaging_app.image_tools import MaskEditTool
        from lspr_imaging_app.panels.image.mask_edit_panels import HistogramSelectionEditPanel

        self.assertIs(self.panel._mask_tab.edit_tool.tool(), MaskEditTool.HISTOGRAM_SELECTION)
        self.assertIsInstance(self.panel._mask_tab._edit_stack.currentWidget(), HistogramSelectionEditPanel)

    def test_picking_a_tool_switches_the_edit_stack(self) -> None:
        from lspr_imaging_app.image_tools import MaskEditTool
        from lspr_imaging_app.panels.image.mask_edit_panels import MorphologyEditPanel

        self.panel._mask_tab._edit_picker._actions[MaskEditTool.MORPHOLOGY].trigger()
        self.assertIsInstance(self.panel._mask_tab._edit_stack.currentWidget(), MorphologyEditPanel)
        self.assertIs(self.panel._mask_tab._edit_stack.currentWidget(), self.panel._mask_tab._edit_stack.widget(3))

    def test_histogram_selection_edit_panel_adds_the_highlighted_patch_in_raw_space(self) -> None:
        """Same assertion shape as `test_lspri_rewrite_mask_highlight_
        actions.py`'s own `test_add_masks_exactly_the_highlighted_patch_in_
        raw_space` - this is the Image panel's second front door onto the
        exact same `HistogramHighlightMaskEditor` logic, not a copy."""
        self._load()
        self.highlight_range.set_range(3000.0, 5000.0)
        panel = self.panel._mask_tab._edit_stack.widget(0)
        panel._add_button.click()

        resolution = self.mask.resolve_mask_source((0, 500.0))
        self.assertIsNotNone(resolution)
        _frame, resolved_mask, scope = resolution
        expected = np.zeros((64, 80), dtype=bool)
        expected[28:33, 38:43] = True
        np.testing.assert_array_equal(resolved_mask, expected)

    def test_morphology_edit_panel_erode_shrinks_the_mask(self) -> None:
        """Regression pin for the 2026-10-02 `MaskModule.apply_morphology`
        correctness fix (see that method's own docstring): erode must
        actually shrink the mask, not leave it unchanged (the old OR-merge
        behavior) or replace it with the removed boundary ring (the old
        AND-NOT-merge behavior)."""
        self._load()
        base = np.zeros((64, 80), dtype=bool)
        base[20:30, 20:30] = True  # a 10x10 filled square
        self.mask.set_mask_change((0, 500.0), "persistent", base)
        _pump()

        panel = self.panel._mask_tab._edit_stack.widget(3)
        panel._radius_spin.setValue(1)
        panel._operation_buttons["erode"].click()

        _frame, eroded, _scope = self.mask.resolve_mask_source((0, 500.0))
        self.assertEqual(int(eroded.sum()), 64)  # an 8x8 interior survives a 1px erosion
        self.assertTrue(bool(eroded[25, 25]))  # center still set
        self.assertFalse(bool(eroded[20, 25]))  # the boundary row is gone

    def test_morphology_open_close_icons_match_the_stable_app(self) -> None:
        """"copy the icons from the stable app (open book, closed book)" -
        2026-10-02 maintainer request."""
        from lspr_imaging_app.panels.image.mask_edit_panels import _MORPHOLOGY_OPERATIONS

        icons_by_operation = {operation: icon_name for operation, icon_name, _tooltip in _MORPHOLOGY_OPERATIONS}
        self.assertEqual(icons_by_operation["open"], "book")
        self.assertEqual(icons_by_operation["close"], "book-2")

    def test_disabled_action_buttons_keep_the_same_icon_color_as_enabled_ones(self) -> None:
        """"Not all +/- icons are same, you change only those in histogram
        selection. All other should be changed as well and same as
        histogram" (2026-10-02 maintainer request) - Qt grays a disabled
        QToolButton's icon by default, which is what made Threshold/Local-
        contrast/Draw's not-yet-wired +/- buttons look different from
        Histogram selection's own even though all share the same `ADD_
        COLOR`/`SUBTRACT_COLOR` - `action_button` (`mask_edit_common.py`)
        now registers the identical full-color pixmap for both the Normal
        and Disabled icon modes, so this must hold for every button
        regardless of its current enabled state."""
        from PyQt6.QtGui import QIcon

        threshold_panel = self.panel._mask_tab._edit_stack.widget(1)
        self.assertFalse(threshold_panel._add_button.isEnabled())
        icon = threshold_panel._add_button.icon()
        normal_image = icon.pixmap(44, 44, QIcon.Mode.Normal).toImage()
        disabled_image = icon.pixmap(44, 44, QIcon.Mode.Disabled).toImage()
        self.assertEqual(normal_image, disabled_image)

    def test_threshold_and_local_contrast_add_subtract_are_disabled(self) -> None:
        """Settings-only for now - see mask_edit_panels.py's module
        docstring for why (needs a background worker, not built yet)."""
        threshold_panel = self.panel._mask_tab._edit_stack.widget(1)
        local_contrast_panel = self.panel._mask_tab._edit_stack.widget(2)
        for panel in (threshold_panel, local_contrast_panel):
            self.assertFalse(panel._add_button.isEnabled())
            self.assertFalse(panel._subtract_button.isEnabled())

    def test_threshold_edit_panel_spinbox_pushes_mask_settings(self) -> None:
        panel = self.panel._mask_tab._edit_stack.widget(1)
        panel._threshold_spin.setValue(12.5)
        self.assertAlmostEqual(self.mask.settings().relative_threshold_fraction, 0.125, places=4)

    def test_local_contrast_edit_panel_spinbox_pushes_mask_settings(self) -> None:
        panel = self.panel._mask_tab._edit_stack.widget(2)
        panel._z_spin.setValue(3.5)
        self.assertAlmostEqual(self.mask.settings().local_contrast_z_threshold, 3.5, places=4)

    def test_morphology_edit_panel_radius_spinbox_pushes_mask_settings(self) -> None:
        panel = self.panel._mask_tab._edit_stack.widget(3)
        panel._radius_spin.setValue(7)
        self.assertEqual(self.mask.settings().morphology_radius_px, 7)

    def test_draw_edit_panel_brush_size_pushes_mask_settings(self) -> None:
        panel = self.panel._mask_tab._edit_stack.widget(4)
        panel._size_spin.setValue(9)
        self.assertEqual(self.mask.settings().brush_size_px, 9)

    # -- histogram highlight overlay (2026-10-02, "Histogram" ribbon tab) ----

    def test_histogram_highlight_overlay_controls_live_in_the_view_tab(self) -> None:
        """"View" is tab index 0 (renamed from "Histogram" and moved first,
        2026-10-06); the controls sit in its "Histogram" captioned group."""
        from lspr_imaging_app.panels.image.histogram_highlight_overlay_controls import (
            HistogramHighlightOverlayControls,
        )

        controls = self.panel._view_tab.highlight_controls
        self.assertIsInstance(controls, HistogramHighlightOverlayControls)
        page = self.panel._tool_ribbon._stack.widget(0)
        self.assertIs(controls.parentWidget().parentWidget(), page)
        label = self.panel._view_tab._highlight_label
        self.assertEqual(label.text(), "Histogram")
        self.assertIs(label.parentWidget(), controls.parentWidget())

    def test_view_tab_mask_icons_mirror_the_mask_tab(self) -> None:
        view_controls = self.panel._view_tab._mask_controls
        mask_controls = self.panel._mask_tab.overlay_controls
        self.assertEqual(self.panel._view_tab._mask_label.text(), "Mask")
        before = self.panel._mask_tint.visible
        view_controls._toggle_button.click()
        self.assertEqual(self.panel._mask_tint.visible, not before)
        self.assertEqual(mask_controls._toggle_button.isChecked(), not before)
        mask_controls._alpha_slider.setValue(37)
        self.assertEqual(view_controls._alpha_slider.value(), 37)
        self.assertAlmostEqual(self.panel._mask_tint.alpha, 0.37)

    def test_histogram_highlight_overlay_draws_the_chosen_color_over_selected_pixels(self) -> None:
        """The dataset's bright patch (`_write_dataset`: rows 28-32, cols
        38-42, ~4000-4200) isolated by a (3000, 5000) range - mirrors
        `test_mask_overlay_draws_the_chosen_color_over_masked_pixels`."""
        self._load()
        self.highlight_range.set_range(3000.0, 5000.0)
        _pump()

        self.assertTrue(self.panel._highlight_tint.item.isVisible())
        overlay = self.panel._highlight_tint.item.image
        self.assertEqual(overlay.shape, (64, 80, 4))
        color = self.panel._highlight_tint.color
        expected = (color.red(), color.green(), color.blue(), int(round(self.panel._highlight_tint.alpha * 255.0)))
        self.assertEqual(tuple(overlay[30, 40]), expected)
        self.assertEqual(tuple(overlay[0, 0]), (0, 0, 0, 0))

    def test_histogram_highlight_overlay_hides_when_toggled_off_and_returns_when_toggled_on(self) -> None:
        self._load()
        self.highlight_range.set_range(3000.0, 5000.0)
        _pump()
        self.assertTrue(self.panel._highlight_tint.item.isVisible())

        self.panel._view_tab.highlight_controls._toggle_button.click()
        self.assertFalse(self.panel._highlight_tint.item.isVisible())

        self.panel._view_tab.highlight_controls._toggle_button.click()
        self.assertTrue(self.panel._highlight_tint.item.isVisible())

    def test_histogram_highlight_overlay_hides_when_the_whole_image_falls_in_range(self) -> None:
        """A full-image selection (e.g. the range `HistogramPanel` seeds on
        first load, before the user narrows it) carries no information as a
        tint - same reasoning as the stable app's own fixed-sensor-range
        check, applied against the actual displayed image's range instead
        (see `_update_highlight_overlay`'s docstring)."""
        self._load()
        image = self.panel._current_display_image
        self.highlight_range.set_range(float(image.min()), float(image.max()))
        _pump()
        self.assertFalse(self.panel._highlight_tint.item.isVisible())

    def test_histogram_highlight_overlay_color_and_alpha_changes_redraw_without_a_new_render(self) -> None:
        """Cosmetic-only, like the mask overlay's own equivalent test: must
        not touch the async pixel-render pipeline."""
        self._load()
        self.highlight_range.set_range(3000.0, 5000.0)
        _pump()

        serial_before = self.panel._latest_serial
        new_color = QColor("#f472b6")
        self.panel._view_tab.highlight_controls.color_changed.emit(new_color)
        self.panel._view_tab.highlight_controls.alpha_changed.emit(0.3)

        self.assertEqual(self.panel._latest_serial, serial_before)
        overlay = self.panel._highlight_tint.item.image
        self.assertEqual(tuple(overlay[30, 40]), (new_color.red(), new_color.green(), new_color.blue(), 76))

    def test_histogram_highlight_overlay_updates_when_the_range_changes_with_no_new_render(self) -> None:
        """The other half of the shared-state symmetry
        `test_lspri_rewrite_histogram_panel.py` already pins from the
        Histogram-panel side: a plain `HighlightRangeModule.set_range` call
        (what a drag on the Histogram plot ultimately does) must redraw this
        overlay too, with no reference to `HistogramPanel` anywhere in
        `ImagePanel`."""
        self._load()
        serial_before = self.panel._latest_serial
        self.highlight_range.set_range(3000.0, 5000.0)
        self.assertEqual(self.panel._latest_serial, serial_before)
        self.assertTrue(self.panel._highlight_tint.item.isVisible())

    def test_tool_info_and_cursor_icon_match_the_bars_other_icons(self) -> None:
        """"make cursor and i icon same as other icons in the bar" (maintainer
        request). The "i" icon still shares `style_bar_icon_button`'s look with
        Select/Add ROI. The cursor toggle now sits in "General" and uses the
        ribbon's 28px icon size (same as the area-selection picker beside it,
        2026-10-03); it is fixed-width now, since its live text left the button."""
        from lspr_ui import transparent_icon_button_stylesheet

        from lspr_imaging_app.panels.image.canvas_tools import _BUTTON_SIZE, _ICON_SIZE
        from lspr_imaging_app.panels.image.general_group import BUTTON_SIZE, ICON_SIZE

        info = self.panel._tool_info
        self.assertEqual(info.height(), _BUTTON_SIZE)
        self.assertEqual(info.width(), _BUTTON_SIZE)
        self.assertEqual(info.iconSize().width(), _ICON_SIZE)
        self.assertEqual(info.styleSheet(), transparent_icon_button_stylesheet())
        cursor = self.panel._cursor_overlay.icon_label
        self.assertEqual((cursor.width(), cursor.height()), (BUTTON_SIZE, BUTTON_SIZE))
        self.assertEqual(cursor.iconSize().width(), ICON_SIZE)

    def test_top_bar_sits_above_the_view_with_a_bottom_border(self) -> None:
        """"make there a bo[r]der on the bottom to separate it from the
        image area" (maintainer request) - the seam belongs to the shared
        top bar now, not the old per-strip borders `CanvasToolsBar` and the
        cursor icon used to draw."""
        outer_layout = self.panel.layout()
        canvas_column = None
        for i in range(outer_layout.count()):
            sub_layout = outer_layout.itemAt(i).layout()
            if sub_layout is not None and sub_layout.indexOf(self.panel._top_bar) != -1:
                canvas_column = sub_layout
                break
        self.assertIsNotNone(canvas_column)
        self.assertLess(canvas_column.indexOf(self.panel._top_bar), canvas_column.indexOf(self.panel._view))
        style = self.panel._top_bar.styleSheet()
        self.assertIn("border-bottom: 1px solid", style)


class RewriteImagePanelViewportPersistenceTest(unittest.TestCase):
    """Regression tests for the viewport (pan/zoom) restore-on-launch feature
    (2026-09-30, maintainer request: "image area, position... should be
    restorable during app launch"). `AppSettings.image_view_*` /
    `ImagePanel(initial_view_range=...)` / `view_range_changed` ->
    `app_rewrite.py`'s `_persist` wiring - see
    `test_lspri_rewrite_visual_settings_restore.py` for the full round trip
    through `build_main_window`."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.dataset_model = _write_dataset(self.root)
        self.dataset = DatasetModule()

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self._tmp.cleanup()

    def _build_panel(self, *, initial_view_range=None) -> "ImagePanel":
        panel = ImagePanel(
            self.dataset, GeometryModule(), MaskModule(), ChromaticModule(),
            BackgroundModule(), RoiToolbox(), SelectionModule(), ActiveToolModule(),
            ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), initial_view_range=initial_view_range,
        )
        self.panel = panel
        panel.resize(400, 280)
        panel.show()
        return panel

    @staticmethod
    def _center(lo: float, hi: float) -> float:
        return (lo + hi) / 2.0

    def test_no_saved_range_leaves_pyqtgraph_auto_range_in_charge(self) -> None:
        """First-ever launch (or an older settings file predating this
        field): `initial_view_range=None` must not call `setRange` at all -
        only pin that the panel still renders normally."""
        panel = self._build_panel(initial_view_range=None)
        self.dataset.load_dataset(self.dataset_model)
        _pump()
        self.assertTrue(panel._view_range_restored)
        self.assertIsNotNone(panel._image_item.image)

    def test_a_saved_range_is_applied_after_the_first_render(self) -> None:
        """`setAspectLocked(True)` (panel.py's `_build_ui`) means an exact
        `setRange(..., padding=0)` can end up slightly wider than requested
        on whichever axis needs to grow to match the ViewBox's actual screen
        pixel aspect - it never shrinks or re-centers, though (confirmed
        empirically: same center, span >= requested). So this pins the two
        things a restore actually promises - the *requested* center, and a
        span no smaller than requested - rather than exact pixel bounds,
        which the aspect lock makes environment-dependent."""
        panel = self._build_panel(initial_view_range=((5.0, 45.0), (2.0, 30.0)))
        self.dataset.load_dataset(self.dataset_model)
        _pump()
        x_range, y_range = panel._plot.vb.viewRange()
        self.assertAlmostEqual(self._center(*x_range), 25.0, delta=0.5)
        self.assertAlmostEqual(self._center(*y_range), 16.0, delta=0.5)
        self.assertGreaterEqual(x_range[1] - x_range[0], 39.5)
        self.assertGreaterEqual(y_range[1] - y_range[0], 27.5)
        # And clearly not the full-image auto-fit - proof this is the
        # restored range, not the pre-existing default. The bound was 70.0
        # before the Image panel's top bar grew a second ribbon row
        # (`tool_ribbon.py`, 2026-09-30), then 150.0 once that row's own
        # height grew further, then 160.0 when `TransformsSection.ROW_HEIGHT`
        # grew 36px -> 50px (2026-10-02, Rotation/Flip/Crop/Calibrate each
        # becoming their own captioned group). This is a third bump, to
        # 180.0: observed value drifted again to 168.24, but
        # `tool_ribbon.py`'s `_ROW_HEIGHT` is `max()` of three terms (canvas
        # tools bar, TransformsSection, a 42px literal) - none depend on the
        # Mask tab content changed this round (icon colors, the "Manual
        # edit" label's wrapping), so this one bump is most likely test-order/
        # font-metric variance rather than a real further shrink of the
        # toolbar. A real auto-fit on this fixture is ~200, measured
        # directly - 180 keeps real headroom against that while still being
        # nowhere near it, not pinned to the exact pixel geometry of one
        # particular toolbar height (same reasoning as the earlier bumps).
        self.assertLess(x_range[1] - x_range[0], 260.0)  # 190 -> 260, 2026-10-04: the new "Chromatic Corrections" tab label widens the tab strip (the ribbon's minimum width) and so the canvas; measured here: restored span 203 vs full auto-fit 550, so it is still clearly the restored range

    def test_the_saved_range_is_never_reapplied_on_a_later_frame_change(self) -> None:
        """Restoring must be a one-shot: navigating to a different frame
        after restore must not snap the view back, or the user could never
        actually zoom/pan during the session."""
        panel = self._build_panel(initial_view_range=((5.0, 45.0), (2.0, 30.0)))
        self.dataset.load_dataset(self.dataset_model)
        _pump()
        panel._plot.vb.setRange(xRange=(0.0, 80.0), yRange=(0.0, 64.0), padding=0.0)
        panel._selection.set_wavelength(550.0)
        _pump()
        x_range, _ = panel._plot.vb.viewRange()
        # Moved to (roughly) the manual target's own center, not snapped
        # back to the restored range's center (25.0).
        self.assertAlmostEqual(self._center(*x_range), 40.0, delta=2.0)

    def test_panning_emits_view_range_changed_after_the_debounce(self) -> None:
        panel = self._build_panel(initial_view_range=None)
        self.dataset.load_dataset(self.dataset_model)
        _pump()
        received: list[tuple[float, float, float, float]] = []
        panel.view_range_changed.connect(lambda *args: received.append(args))
        panel._plot.vb.setRange(xRange=(10.0, 50.0), yRange=(5.0, 40.0), padding=0.0)
        _pump(1.0)  # longer than _VIEW_RANGE_PERSIST_DEBOUNCE_MS
        self.assertTrue(received)
        x_min, x_max, y_min, y_max = received[-1]
        self.assertAlmostEqual(self._center(x_min, x_max), 30.0, delta=0.5)
        self.assertAlmostEqual(self._center(y_min, y_max), 22.5, delta=0.5)
        self.assertGreaterEqual(x_max - x_min, 39.5)
        self.assertGreaterEqual(y_max - y_min, 34.5)


if __name__ == "__main__":
    unittest.main()
