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
        """Both used to float as manually-`.move()`d overlays on the canvas
        itself; now they're ordinary widgets in the shared top bar, after
        the Select/Add ROI tool buttons (`_canvas_tools`) with a stretch
        between - tools on the left, utility icons on the right."""
        top_bar_layout = self.panel._top_bar.layout()
        self.assertIs(self.panel._tool_info.parent(), self.panel._top_bar)
        self.assertIs(self.panel._cursor_overlay.icon_label.parent(), self.panel._top_bar)
        canvas_tools_index = top_bar_layout.indexOf(self.panel._canvas_tools)
        cursor_index = top_bar_layout.indexOf(self.panel._cursor_overlay.icon_label)
        info_index = top_bar_layout.indexOf(self.panel._tool_info)
        self.assertNotEqual(canvas_tools_index, -1)
        self.assertLess(canvas_tools_index, cursor_index)
        self.assertLess(cursor_index, info_index)

    def test_tool_info_and_cursor_icon_match_the_bars_other_icons(self) -> None:
        """"make cursor and i icon same as other icons in the bar... this
        apply for all icons later applied, they should have same style"
        (maintainer request) - both now go through the same
        `style_bar_icon_button` helper Select/Add ROI use, so they share
        height, icon size, and hover chrome with the rest of this bar. The
        cursor icon's *width* stays free (`fixed_width=False`) since it
        must grow to show live text while enabled."""
        from lspr_imaging_app.panels.image.canvas_tools import _BUTTON_SIZE, _ICON_SIZE

        for widget in (self.panel._tool_info, self.panel._cursor_overlay.icon_label):
            self.assertEqual(widget.height(), _BUTTON_SIZE)
            self.assertEqual(widget.iconSize().width(), _ICON_SIZE)
            self.assertEqual(widget.styleSheet(), self.panel._canvas_tools._select_button.styleSheet())
        self.assertEqual(self.panel._tool_info.width(), _BUTTON_SIZE)
        # The cursor icon's width is deliberately not clamped, unlike the
        # "i" icon's - it must still grow to show live text.
        self.assertGreater(self.panel._cursor_overlay.icon_label.maximumWidth(), _BUTTON_SIZE)

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

    def test_canvas_tools_bar_is_horizontal_with_no_border_of_its_own(self) -> None:
        """Flipped from a vertical strip (2026-09-30, maintainer request) -
        Select and Add ROI now sit side by side; the border moved to the
        wrapping top bar, so this widget draws none itself."""
        self.assertIsInstance(self.panel._canvas_tools.layout(), QtWidgets.QHBoxLayout)
        self.assertIn("border: none", self.panel._canvas_tools.styleSheet())
        self.assertNotIn("border-right", self.panel._canvas_tools.styleSheet())


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
            ReferenceFrameModule(), initial_view_range=initial_view_range,
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
        # And clearly not the full-image auto-fit (the test image is 80x64) -
        # proof this is the restored range, not the pre-existing default.
        self.assertLess(x_range[1] - x_range[0], 70.0)

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
