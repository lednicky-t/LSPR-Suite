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
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.render import RenderRequest, RenderResult
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import ReferenceFrameModule, SelectionModule
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
        self.panel = ImagePanel(
            self.dataset, self.geometry, self.mask, self.chromatic,
            self.background, self.roi_toolbox, self.selection, ActiveToolModule(),
            self.reference_frame,
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
        self.assertIn(f"{expected:.1f}", text)

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
        self.assertEqual(texts, {"500.0", "600.0"})

    # -- navigation bar border (2026-09-30, maintainer request) --------------

    def test_navigation_bar_has_a_subtle_border_against_the_canvas(self) -> None:
        """The reported bug: this bar (Cube/λ rows + status row) and the
        canvas above it (moved below the canvas 2026-09-30) share the same
        background, so the seam between them was invisible. Only the top
        edge (the one that actually touches the canvas) should be
        bordered."""
        style = self.panel._controls_bar.styleSheet()
        self.assertIn("border-top: 1px solid", style)
        self.assertNotIn("border-left", style)
        self.assertNotIn("border-right", style)
        self.assertNotIn("border-bottom", style)

    def test_navigation_bar_border_updates_on_a_live_theme_switch(self) -> None:
        from lspr_ui import APP_THEME, BRIGHT_THEME, set_active_theme

        set_active_theme(BRIGHT_THEME)
        try:
            self.panel.refresh_theme()
            self.assertIn(BRIGHT_THEME.toolbar_border, self.panel._controls_bar.styleSheet())
        finally:
            set_active_theme(APP_THEME)
            self.panel.refresh_theme()

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
        self.assertEqual(self.panel._wavelength_spin.text(), "500.0")

    def test_titles_and_number_fields_share_one_width_each(self) -> None:
        """"Cube" and "λ (nm)" are different lengths, and so are their
        number fields' typical contents - without a shared fixed width,
        the two sliders started at different x positions and the two
        number fields didn't line up on the right."""
        # Located by content rather than assuming child order.
        labels = {label.text(): label for label in self.panel.findChildren(QtWidgets.QLabel)}
        self.assertEqual(labels["Cube"].width(), labels["λ (nm)"].width())
        self.assertEqual(self.panel._cube_spin.width(), self.panel._wavelength_spin.width())

    def test_canvas_is_above_the_navigation_bar(self) -> None:
        """Moved to the bottom of the panel (2026-09-30, maintainer
        request) - the outer layout must place the canvas row before the
        navigation bar, not after. `_canvas_tools` lives inside `canvas_row`,
        an unnamed sub-layout, so find *that* sub-layout's position rather
        than assuming a stored reference to it exists."""
        outer_layout = self.panel.layout()
        controls_index = outer_layout.indexOf(self.panel._controls_bar)
        canvas_index = None
        for i in range(outer_layout.count()):
            sub_layout = outer_layout.itemAt(i).layout()
            if sub_layout is not None and sub_layout.indexOf(self.panel._canvas_tools) != -1:
                canvas_index = i
                break
        self.assertIsNotNone(canvas_index)
        self.assertLess(canvas_index, controls_index)


if __name__ == "__main__":
    unittest.main()
