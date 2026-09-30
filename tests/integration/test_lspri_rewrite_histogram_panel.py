"""Tests for the LSPRimaging Evaluation rewrite's Histogram panel.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring for why.

Same pattern as `test_lspri_rewrite_image_panel.py`: a real `QApplication`,
real widgets, a real TIFF-backed dataset, driven entirely by direct
method/signal calls rather than screen coordinates.

The property most worth pinning here is the shared-state design for the
Highlight range (`HighlightRangeModule`): the panel driving it (a drag or a
spin-box edit) and the panel reacting to it (as Mask/ROI Toolbox setting it
directly would) must look identical from the panel's side - that symmetry is
the whole point of not making this Histogram-specific pub/sub.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics

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
    from lspr_imaging_app.panels.histogram import HistogramPanel
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

_IMAGE_SHAPE = (64, 80)


def _pump(seconds: float = 0.5) -> None:
    """Let the coalescing redraw timer fire and the render thread's result
    reach the GUI thread (same reasoning as the Image panel test's `_pump`:
    Histogram only redraws in reaction to `ImagePanel.image_rendered`, which
    itself only arrives after an off-thread render completes)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _write_dataset(root: Path) -> ImageDataset:
    rng = np.random.default_rng(7)
    frame = rng.uniform(100.0, 200.0, size=_IMAGE_SHAPE).astype(np.float32)
    frame[28:33, 38:43] += 4000.0
    path = root / "c0_w500.tif"
    tifffile.imwrite(str(path), frame)
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)
    return ImageDataset(folder=root, records=[record], source_format="image_stack")


class RewriteHistogramPanelTest(unittest.TestCase):
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
        self.image_panel = ImagePanel(
            self.dataset, self.geometry, self.mask, self.chromatic,
            self.background, self.roi_toolbox, self.selection, ActiveToolModule(), ReferenceFrameModule(),
        )
        self.panel = HistogramPanel(
            self.image_panel, self.geometry, self.mask, self.chromatic, self.roi_toolbox, self.highlight_range,
        )
        # Real auto-ranging only happens once pyqtgraph actually paints the
        # widget - an earlier manual check missed this (resize() alone was
        # not enough) and mistook that gap for a real auto-range bug that
        # turned out to be something else entirely (see the auto-range-
        # button tests below). Showing it here, once, for every test keeps
        # that gap from recurring silently.
        self.panel.resize(500, 300)
        self.panel.show()

    def tearDown(self) -> None:
        self.image_panel._renderer.stop()
        self._tmp.cleanup()

    def _load(self) -> None:
        self.dataset.load_dataset(self.dataset_model)
        _pump()

    # -- lifecycle ------------------------------------------------------

    def test_empty_before_a_dataset_is_loaded(self) -> None:
        x, _ = self.panel._plot._all_pixels_curve.getData()
        self.assertTrue(x is None or len(x) == 0)

    def test_dataset_cleared_resets_the_panel(self) -> None:
        self._load()
        self.dataset.clear_dataset()
        _pump()
        x, _ = self.panel._plot._all_pixels_curve.getData()
        self.assertTrue(x is None or len(x) == 0)

    # -- curves -----------------------------------------------------------------

    def test_all_pixels_curve_sums_to_100_percent_by_default(self) -> None:
        self._load()
        _, y = self.panel._plot._all_pixels_curve.getData()
        self.assertAlmostEqual(float(np.sum(y)), 100.0, places=3)

    def test_x_axis_spans_the_full_16bit_range_regardless_of_data(self) -> None:
        """Maintainer's spec (2026-09-29): the x-axis is fixed [0, 65535],
        not the observed data's own (much narrower) span."""
        self._load()
        x, _ = self.panel._plot._all_pixels_curve.getData()
        self.assertAlmostEqual(float(x[0]), 0.0)
        self.assertGreaterEqual(float(x[-1]), 65535.0)

    def test_counts_mode_sums_to_the_real_pixel_count(self) -> None:
        self._load()
        self.panel._show_settings_dialog()
        self.panel._settings_dialog.axis_mode_combo.setCurrentIndex(1)  # a real combo change, not a coordinate
        _pump()
        _, y = self.panel._plot._all_pixels_curve.getData()
        self.assertAlmostEqual(float(np.sum(y)), float(_IMAGE_SHAPE[0] * _IMAGE_SHAPE[1]), places=3)

    def test_roi_adds_to_the_sample_curve(self) -> None:
        self._load()
        _, before = self.panel._plot._sample_curve.getData()
        self.assertEqual(float(np.sum(before)), 0.0)
        self.roi_toolbox.add_roi(40.0, 30.0, sample_radius_px=5.0)
        _pump()
        _, after = self.panel._plot._sample_curve.getData()
        self.assertGreater(float(np.sum(after)), 0.0)

    def test_an_authored_mask_adds_to_the_ignore_curve(self) -> None:
        self._load()
        raw_mask = np.zeros(_IMAGE_SHAPE, dtype=bool)
        raw_mask[10:20, 10:20] = True
        self.mask.set_mask_change((0, 500.0), "persistent", raw_mask)
        _pump()
        _, y = self.panel._plot._ignore_mask_curve.getData()
        self.assertGreater(float(np.sum(y)), 0.0)

    def test_bin_width_changes_the_number_of_bins(self) -> None:
        self._load()
        x_before, _ = self.panel._plot._all_pixels_curve.getData()
        self.panel._show_settings_dialog()
        self.panel._settings_dialog.bin_spin.setValue(32)
        _pump()
        x_after, _ = self.panel._plot._all_pixels_curve.getData()
        self.assertGreater(len(x_after), len(x_before))

    def test_line_width_setting_applies_to_the_curves(self) -> None:
        self._load()
        self.panel._show_settings_dialog()
        self.panel._settings_dialog.line_width_spin.setValue(4.0)
        self.assertEqual(self.panel._plot._all_pixels_curve.opts["pen"].widthF(), 4.0)

    # -- highlight range: shared-state symmetry ------------------------------

    def test_highlight_range_is_seeded_and_visible_after_first_load(self) -> None:
        """Regression pin (maintainer's report, 2026-09-29 - "the histogram
        highlighting range selector is not visible"): the region starts
        hidden until `HighlightRangeModule` holds a range, and the *only*
        interactive way to give it one is dragging the region - impossible
        while it is invisible. Without a seed, the selector could never be
        made visible at all."""
        self.assertIsNone(self.highlight_range.current_range())
        self._load()
        seeded = self.highlight_range.current_range()
        self.assertIsNotNone(seeded)
        lo, hi = seeded
        self.assertAlmostEqual(lo, float(self.panel._image.min()), places=1)
        self.assertAlmostEqual(hi, float(self.panel._image.max()), places=1)
        self.assertEqual(self.panel._plot._region.getRegion(), (lo, hi))

    def test_highlight_range_seed_does_not_overwrite_an_existing_value(self) -> None:
        self._load()
        self.highlight_range.set_range(500.0, 600.0)
        self.panel._redraw()  # a second redraw must not re-seed over a real value
        self.assertEqual(self.highlight_range.current_range(), (500.0, 600.0))

    def test_readout_fields_are_centered_in_a_fixed_five_digit_width(self) -> None:
        """Regression pin (maintainer's report, 2026-09-30, final design
        after four rounds of `QDoubleSpinBox`-based attempts - see CLAUDE.md's
        "Common Pitfalls" entry for the full account): the maintainer's own
        spec is a *fixed* width sized once for the five-digit worst case,
        not a per-value refit - "smaller numbers can be centered... why it
        is so complicated?" Pinned two ways: the field width does not change
        between a short value and a long one (proving it is fixed, not
        refitted), and it comfortably fits the five-digit worst case with no
        clipping (measured against the actual rendered glyph width, not
        `.text()` - `.text()` always returns the full logical string
        regardless of what is actually visible, which is why it looked
        "fixed" in an earlier round when it was not)."""
        readout = self.panel._plot._range_readout
        readout.set_range(5.0, 9.0)
        narrow_width = readout._min_edit.width()
        readout.set_range(17428.0, 32127.0)
        wide_width = readout._min_edit.width()
        self.assertEqual(narrow_width, wide_width)  # fixed, not refitted

        worst_case = QFontMetrics(readout._min_edit.font()).horizontalAdvance("99999")
        self.assertGreaterEqual(readout._min_edit.width(), worst_case)

    def test_readout_fields_are_center_aligned(self) -> None:
        readout = self.panel._plot._range_readout
        self.assertEqual(
            readout._min_edit.alignment() & Qt.AlignmentFlag.AlignCenter, Qt.AlignmentFlag.AlignCenter
        )
        self.assertEqual(
            readout._max_edit.alignment() & Qt.AlignmentFlag.AlignCenter, Qt.AlignmentFlag.AlignCenter
        )

    def test_readout_pieces_have_zero_spacing_between_them(self) -> None:
        """"put those boxes next to each other, put between them comma
        without any spacing" (maintainer's spec, 2026-09-30)."""
        readout = self.panel._plot._range_readout
        self.assertEqual(readout.layout().spacing(), 0)

    def test_dragging_the_region_sets_the_shared_module(self) -> None:
        self._load()
        received: list[object] = []
        self.highlight_range.range_changed.connect(received.append)
        self.panel._on_highlight_dragged(100.0, 200.0)
        self.assertEqual(self.highlight_range.current_range(), (100.0, 200.0))
        self.assertEqual(received[-1], (100.0, 200.0))

    def test_a_module_side_change_updates_the_plot_and_readout(self) -> None:
        """Mask/ROI Toolbox set this module directly, with no reference to
        this panel at all - this pins the other half of that flow: the
        panel (and the plot's floating readout) reacts exactly as if the
        region had been dragged itself."""
        self._load()
        self.highlight_range.set_range(150.0, 250.0)
        self.assertEqual(self.panel._plot._region.getRegion(), (150.0, 250.0))
        readout = self.panel._plot._range_readout
        self.assertEqual(readout._min_edit.text(), "150")
        self.assertEqual(readout._max_edit.text(), "250")

    def test_readout_stays_live_while_dragging_without_committing_early(self) -> None:
        """Maintainer's regression report (2026-09-29): "when highlight
        range is moving make these fields live showing change, now they are
        stale." Moving one edge of the region (`InfiniteLine.setValue`,
        the same mechanism an in-progress mouse drag uses - fires
        `sigRegionChanged` only, not `sigRegionChangeFinished`) must update
        the readout immediately, but must NOT yet commit to the shared
        module - that only happens once the drag finishes, so a future
        Mask/ROI-detection subscriber never sees a flood of in-progress
        values."""
        self._load()
        self.highlight_range.set_range(100.0, 200.0)
        self.panel._plot._region.lines[0].setValue(150.0)
        self.assertEqual(self.panel._plot._range_readout._min_edit.text(), "150")
        self.assertEqual(self.highlight_range.current_range(), (100.0, 200.0))

    def test_editing_one_readout_field_does_not_silently_move_the_other(self) -> None:
        """Regression pin: an earlier version routed both fields through
        `HighlightRangeModule.set_range`'s swap-on-crossed-pair safety,
        which - for two independently-edited fields - snapped whichever
        field the user did NOT touch to an unintended value the moment the
        one they did touch crossed it."""
        self._load()
        self.panel._on_highlight_min_edited(300.0)
        self.panel._on_highlight_max_edited(400.0)
        self.assertEqual(self.highlight_range.current_range(), (300.0, 400.0))
        self.assertEqual(self.panel._plot._range_readout._min_edit.text(), "300")

    def test_min_crossing_max_pushes_max_forward_instead_of_swapping(self) -> None:
        self._load()
        self.panel._on_highlight_min_edited(100.0)
        self.panel._on_highlight_max_edited(200.0)
        self.panel._on_highlight_min_edited(250.0)  # crosses the current max
        self.assertEqual(self.highlight_range.current_range(), (250.0, 250.0))
        self.assertEqual(self.panel._plot._range_readout._max_edit.text(), "250")

    # -- auto-range, settings icon, cursor overlay (2026-09-29) --------------

    def test_y_axis_auto_ranges_to_fit_new_data(self) -> None:
        """Maintainer's report: "Autoranging of the histogram will not jump
        to proposed range." The real Y-axis auto-range-on-new-data mechanism
        was never actually broken - what was broken is pinned by the two
        tests below instead."""
        vb = self.panel._plot._plot_item.getViewBox()
        self.assertEqual(list(vb.viewRange()[1]), [0, 1])  # nothing plotted yet
        self._load()
        _, y_range = vb.viewRange()
        self.assertNotEqual(list(y_range), [0, 1])
        self.assertGreater(y_range[1], 50.0)  # the real peak is ~100% in one bin

    def test_auto_range_button_is_not_permanently_hidden(self) -> None:
        """Reversed 2026-09-30 (maintainer request: real X zoom/pan, so
        "jump back to seeing everything" needs to be reachable again).
        pyqtgraph's own `updateButtons()` decides moment-to-moment
        show/hide (hover + not-already-at-auto-range) - this only pins that
        nothing here forces it permanently off via `buttonsHidden`."""
        self.assertFalse(self.panel._plot._plot_item.buttonsHidden)

    def test_auto_range_resets_x_to_the_full_16bit_range_not_data_bounds(self) -> None:
        """Reversed 2026-09-30: X used to be physically incapable of being
        anything but [0, 65535] (`setLimits(minXRange=maxXRange=full_span)`),
        so any `autoRange()` call trivially landed there. Now that X can
        really zoom/pan, this pins the *replacement* mechanism - the
        instance's wrapped `ViewBox.autoRange()` (`plot.py`'s `__init__`) -
        actually resets X back to the full sensor range instead of fitting
        to whatever data happens to be visible, exactly like the old
        (now-removed) hard lock used to guarantee for free. Covers both
        doors that reach it: the corner "A" button and the right-click
        menu's "View All"/"Auto" (`vb.autoRange()` here is exactly what
        that menu action calls, not a proxy for it)."""
        self._load()
        vb = self.panel._plot._plot_item.getViewBox()
        vb.setXRange(10_000.0, 20_000.0, padding=0.0)  # zoom in first, so autoRange has to move X back
        vb.autoRange()
        x_range, y_range = vb.viewRange()
        self.assertEqual(list(x_range), [0.0, 65535.0])
        self.assertGreater(y_range[1], 50.0)  # Y still auto-ranges normally, unaffected

    def test_auto_button_click_resets_x_to_the_full_range(self) -> None:
        """The corner "A" button's own click handler (`_on_auto_button_
        clicked`) - a separate code path from `vb.autoRange()` above
        (`PlotItem.autoBtnClicked`'s native behavior is `enableAutoRange()`,
        not `autoRange()`), so both need their own pin."""
        self._load()
        vb = self.panel._plot._plot_item.getViewBox()
        vb.setXRange(10_000.0, 20_000.0, padding=0.0)
        self.panel._plot._on_auto_button_clicked()
        x_range, _ = vb.viewRange()
        self.assertEqual(list(x_range), [0.0, 65535.0])

    def test_x_axis_mouse_interaction_is_enabled(self) -> None:
        """Reversed 2026-09-30 (maintainer request): a drag/wheel-zoom on X
        must actually move the view now, bounded only by `setLimits`
        (`plot.py`'s `__init__`), not blocked outright."""
        vb = self.panel._plot._plot_item.getViewBox()
        mouse_enabled_x, mouse_enabled_y = vb.state["mouseEnabled"]
        self.assertTrue(mouse_enabled_x)
        self.assertTrue(mouse_enabled_y)

    def test_x_axis_can_actually_zoom_in(self) -> None:
        """The real-world behavior all of the above exists to enable -
        `setXRange` is what a mouse-wheel zoom or a drag ultimately calls
        under the hood, so this is the most direct proof X is no longer
        physically incapable of moving."""
        vb = self.panel._plot._plot_item.getViewBox()
        vb.setXRange(1_000.0, 5_000.0, padding=0.0)
        x_range, _ = vb.viewRange()
        self.assertAlmostEqual(x_range[0], 1_000.0)
        self.assertAlmostEqual(x_range[1], 5_000.0)

    def test_x_axis_cannot_be_panned_outside_the_sensor_range(self) -> None:
        """`setLimits(xMin=0, xMax=65535)` still bounds X - zoom/pan is now
        real, not unlimited."""
        vb = self.panel._plot._plot_item.getViewBox()
        vb.setXRange(-5_000.0, 2_000.0, padding=0.0)
        x_range, _ = vb.viewRange()
        self.assertGreaterEqual(x_range[0], 0.0)

    def test_settings_icon_is_not_blank(self) -> None:
        button = self.panel._plot._settings_button
        self.assertFalse(button.icon().isNull())
        self.assertIn("border: none", button.styleSheet())

    def test_cursor_overlay_toggle_and_value_readout(self) -> None:
        self._load()
        overlay = self.panel._plot._cursor_overlay
        self.assertFalse(overlay._enabled)
        overlay.toggle()
        self.assertTrue(overlay._enabled)
        self.assertTrue(self.panel._plot._region is not None)  # sanity: plot still intact
        result = overlay._value_at(2200.0, 10.0)
        self.assertIsNotNone(result)
        x, y, text = result
        self.assertAlmostEqual(x, 2304.0)  # snapped to the containing bin's center (512 DN bins)
        self.assertIn("DN", text)
        overlay.toggle()
        self.assertFalse(overlay._enabled)


if __name__ == "__main__":
    unittest.main()
