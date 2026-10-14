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

import gc
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QEvent, Qt
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
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.histogram import HistogramPanel, compute
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
            self.highlight_range,
            mask_scope=MaskScopeModule(),
        )
        self.panel = HistogramPanel(
            self.image_panel, self.geometry, self.mask, self.chromatic, self.roi_toolbox, self.highlight_range,
            selection=self.selection,
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
        self.panel._stop_worker()  # the Histogram's own worker thread, like the render thread just below
        self.image_panel._renderer.stop()
        # Destroy the panels here, in a known order, on the GUI thread and outside any event handler. Left to the
        # cyclic garbage collector they were destroyed in whatever test happened to be running when it fired, and
        # with a dozen or more of them in one pass that crashed the process (native access violation, 2026-10-14).
        for widget in (self.panel, self.image_panel):
            widget.hide()
            widget.deleteLater()
        _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        _APP.processEvents()
        gc.collect()
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

    def test_normalized_mode_peaks_at_one(self) -> None:
        """Third Y-axis mode (maintainer's spec, 2026-09-30: "normalized
        display (normalization would be towards highest value)"), revised
        2026-10-03: every curve is divided by the All-pixels peak, so All
        pixels reads exactly 1.0 and no sub-population exceeds it."""
        self._load()
        self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        self.panel._show_settings_dialog()
        self.panel._settings_dialog.axis_mode_combo.setCurrentIndex(2)  # Normalized
        _pump()
        _, y = self.panel._plot._all_pixels_curve.getData()
        self.assertAlmostEqual(float(np.max(y)), 1.0, places=6)
        _, sample = self.panel._plot._sample_curve.getData()
        self.assertGreater(float(np.max(sample)), 0.0)
        self.assertLessEqual(float(np.max(sample)), 1.0 + 1e-9)
        # Shared divisor: sample / all-pixels counts ratio is preserved.
        self.assertTrue(np.all(sample <= y + 1e-9))

    def test_roi_adds_to_the_sample_curve(self) -> None:
        self._load()
        _, before = self.panel._plot._sample_curve.getData()
        self.assertEqual(float(np.sum(before)), 0.0)
        self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        _pump()
        _, after = self.panel._plot._sample_curve.getData()
        self.assertGreater(float(np.sum(after)), 0.0)

    def test_legend_right_click_opens_style_menu_and_applies_style(self) -> None:
        from unittest import mock

        from PyQt6.QtCore import QPointF, Qt

        from lspr_imaging_app.panels.histogram import plot as plot_module
        from lspr_imaging_app.panels.histogram.curve_style_menu import CurveStyleEditor

        plot = self.panel._plot
        seen = []
        plot.curve_styles_changed.connect(seen.append)
        event = mock.Mock()
        event.button.return_value = Qt.MouseButton.RightButton
        event.screenPos.return_value = QPointF(5, 5)
        sample, _label = plot._legend.items[1]  # "Sample ROI"
        with mock.patch.object(plot_module, "show_curve_style_menu") as menu:
            sample.mouseClickEvent(event)
        menu.assert_called_once()
        on_changed = menu.call_args.args[-1]
        on_changed({"color": "#112233", "width": 3.5, "line": "dot", "fill": True, "alpha": 50})
        pen = plot._sample_curve.opts["pen"]
        self.assertEqual(pen.color().name(), "#112233")
        self.assertEqual(pen.widthF(), 3.5)
        self.assertEqual(pen.style(), Qt.PenStyle.DotLine)
        self.assertEqual(plot._sample_curve.opts["fillLevel"], 0.0)
        self.assertEqual(seen[-1]["sample"]["line"], "dot")
        self.assertNotIn("reference", seen[-1])
        on_changed({})  # Reset
        self.assertIsNone(plot._sample_curve.opts["fillLevel"])
        # Saved styles come back; unknown keys are ignored.
        plot.set_curve_styles({"reference": {"width": 2.0}, "bogus": {"width": 9}})
        self.assertEqual(plot.curve_styles(), {"reference": {"width": 2.0}})

        editor = CurveStyleEditor({}, "#ffffff", 1.5, "solid")
        got = []
        editor.changed.connect(got.append)
        editor._width_buttons[2.0].click()
        editor._line_buttons["dash"].click()
        editor._fill.click()
        self.assertEqual(got[-1], {"width": 2.0, "line": "dash", "fill": True, "alpha": 30})

    def test_selected_roi_curves_are_scaled_to_the_all_pixels_peak(self) -> None:
        self._load()
        roi_id = self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        self.roi_toolbox.add_roi(20.0, 20.0, sample_diameter_px=10.0)
        _pump()
        plot = self.panel._plot
        self.assertEqual(float(np.sum(plot._selected_sample_curve.getData()[1])), 0.0)
        self.selection.set_roi_selection({roi_id})
        _pump()
        all_peak = float(np.max(plot._all_pixels_curve.getData()[1]))
        sample = plot._selected_sample_curve.getData()[1]
        self.assertAlmostEqual(float(np.max(sample)), all_peak, places=6)
        self.selection.set_roi_selection(set())
        _pump()
        self.assertEqual(float(np.sum(plot._selected_sample_curve.getData()[1])), 0.0)

    def test_selecting_a_roi_does_not_rebuild_the_roi_masks(self) -> None:
        """Selecting re-renders the image (so the Histogram redraws) but changes
        no ROI: the masks must come from the cache (0.9 s per rebuild at 1000
        ROIs before this was cached)."""
        from unittest import mock

        from lspr_imaging_app.panels.histogram import roi_masks

        self._load()
        roi_id = self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        self.roi_toolbox.add_roi(20.0, 20.0, sample_diameter_px=10.0)
        _pump()
        with mock.patch.object(roi_masks, "union_roi_masks", wraps=roi_masks.union_roi_masks) as union:
            self.selection.set_roi_selection({roi_id})
            _pump()
            self.selection.set_roi_selection(set())
            _pump()
            # Only the one-ROI selection subset is built; the full 2-ROI union stays cached.
            self.assertTrue(all(len(call.args[0]) == 1 for call in union.call_args_list))
            self.roi_toolbox.move_roi(roi_id, 41.0, 30.0)  # a real change does rebuild
            _pump()
            self.assertGreaterEqual(union.call_count, 1)

    def test_many_rois_draw_their_curves_when_the_background_build_finishes(self) -> None:
        from unittest import mock

        from lspr_imaging_app.panels.histogram import roi_masks

        self._load()
        with mock.patch.object(roi_masks, "SYNC_ROI_LIMIT", 1):
            for x in (10.0, 25.0, 40.0, 55.0, 70.0):
                self.roi_toolbox.add_roi(x, 30.0, sample_diameter_px=8.0)
            _pump(1.5)
        _, sample = self.panel._plot._sample_curve.getData()
        self.assertGreater(float(np.sum(sample)), 0.0)
        _, reference = self.panel._plot._reference_curve.getData()
        self.assertGreater(float(np.sum(reference)), 0.0)

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

    def test_bin_spin_step_size_follows_the_non_uniform_table(self) -> None:
        """Maintainer's spec, 2026-09-30: "can BIN jump by some reasonable
        increments... 1..10 by one, then by 5 until 50, than by 10 until
        100, and by 25 until 250, 50 until 500". `stepBy` is what a spin
        arrow click or mouse-wheel notch actually calls - pinning it
        directly rather than via a real click event, same as the line-width
        spin box's own `valueChanged` tests elsewhere in this file."""
        self.panel._show_settings_dialog()
        spin = self.panel._settings_dialog.bin_spin
        cases = [(1, 2), (9, 10), (10, 15), (49, 54), (50, 60), (99, 109), (100, 125), (249, 274), (250, 300)]
        for start, expected_after_one_step_up in cases:
            spin.setValue(start)
            spin.stepBy(1)
            self.assertEqual(spin.value(), expected_after_one_step_up, f"stepping up from {start}")

    def test_changing_bin_width_refits_the_y_axis_even_after_a_manual_zoom(self) -> None:
        """Regression pin (maintainer's report, 2026-09-30): "when I change
        BIN size, the plot change but when I do autoscale it will [not] fit
        the plot to area, it is stale." Reproduced by simulating the one
        thing that actually disables pyqtgraph's own continuous Y auto-range
        - a manual auto-range/zoom interaction - before changing the bin
        size; without the fix, Y stays frozen at the pre-change fit."""
        self._load()
        vb = self.panel._plot._plot_item.getViewBox()
        self.panel._plot._on_auto_button_clicked()  # disables continuous Y auto-range, same as a real zoom/pan would
        self.assertFalse(vb.state["autoRange"][1])
        self.panel._show_settings_dialog()
        self.panel._settings_dialog.bin_spin.setValue(8192)  # one giant bin -> ~100% in it, very different from before
        _pump()
        _, y = self.panel._plot._all_pixels_curve.getData()
        self.assertAlmostEqual(vb.viewRange()[1][1], float(np.max(y)), delta=float(np.max(y)) * 0.15)

    def test_changing_axis_mode_refits_the_y_axis_even_after_a_manual_zoom(self) -> None:
        """Same regression as above ("Same for changing axis to other
        display"), for the Percent-of-total <-> Counts <-> Normalized combo
        instead of bin size."""
        self._load()
        vb = self.panel._plot._plot_item.getViewBox()
        self.panel._plot._on_auto_button_clicked()
        self.panel._show_settings_dialog()
        self.panel._settings_dialog.axis_mode_combo.setCurrentIndex(1)  # Counts - a much larger scale than Percent
        _pump()
        _, y = self.panel._plot._all_pixels_curve.getData()
        self.assertAlmostEqual(vb.viewRange()[1][1], float(np.max(y)), delta=float(np.max(y)) * 0.15)

    def test_double_clicking_the_y_axis_cycles_the_display_mode(self) -> None:
        """Maintainer's spec, 2026-09-30: "can y axis be switchable also by
        double clicking on it." Cycles Percent -> Counts -> Normalized ->
        back to Percent, the same order as the settings dialog's combo."""
        self._load()
        self.assertEqual(self.panel._y_mode, "percent")
        self.panel._plot.y_axis_double_clicked.emit()
        self.assertEqual(self.panel._y_mode, "counts")
        self.panel._plot.y_axis_double_clicked.emit()
        self.assertEqual(self.panel._y_mode, "normalized")
        self.panel._plot.y_axis_double_clicked.emit()
        self.assertEqual(self.panel._y_mode, "percent")

    def test_double_click_cycling_keeps_an_open_settings_dialog_in_sync(self) -> None:
        self._load()
        self.panel._show_settings_dialog()
        self.panel._plot.y_axis_double_clicked.emit()
        self.assertEqual(self.panel._settings_dialog.axis_mode_combo.currentIndex(), 1)

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

    def test_readout_and_shared_module_both_stay_live_while_dragging(self) -> None:
        """Maintainer's regression report (2026-09-29): "when highlight
        range is moving make these fields live showing change, now they are
        stale." Moving one edge of the region (`InfiniteLine.setValue`, the
        same mechanism an in-progress mouse drag uses - fires
        `sigRegionChanged` only, not `sigRegionChangeFinished`) updates the
        readout immediately.

        **Revised 2026-10-02** (maintainer request: the Image panel's
        histogram-highlight overlay should also track a drag live, not just
        snap into place on release) - this used to also assert the shared
        module was *not* yet updated at this point, deliberately deferring
        the commit to drag-finish. That half is now the opposite on purpose:
        see `HistogramPlot._on_region_changed_live`'s docstring for why the
        original "avoid a flood of in-progress values" reasoning no longer
        applies now that a real (cheap) reactive consumer exists."""
        self._load()
        self.highlight_range.set_range(100.0, 200.0)
        self.panel._plot._region.lines[0].setValue(150.0)
        self.assertEqual(self.panel._plot._range_readout._min_edit.text(), "150")
        self.assertEqual(self.highlight_range.current_range(), (150.0, 200.0))

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

    def test_settings_and_cursor_icons_match_the_image_panel_size(self) -> None:
        """Regression pin (maintainer's report, 2026-09-30: "change cursor
        and setting icon size to match size of icons in image panel") -
        both go through `image/canvas_tools.style_bar_icon_button`, the one
        shared place the Image panel's own overlay icons (crop apply,
        canvas tools bar, its own cursor/"i" icons) get their size from, so
        this pins the numbers against that function rather than duplicating
        them here."""
        from lspr_imaging_app.panels.image.canvas_tools import _BUTTON_SIZE, _ICON_SIZE

        settings_button = self.panel._plot._settings_button
        self.assertEqual(settings_button.iconSize().width(), _ICON_SIZE)
        self.assertEqual(settings_button.height(), _BUTTON_SIZE)
        self.assertEqual(settings_button.width(), _BUTTON_SIZE)

        cursor_icon = self.panel._plot._cursor_overlay.icon_label
        self.assertEqual(cursor_icon.iconSize().width(), _ICON_SIZE)
        self.assertEqual(cursor_icon.height(), _BUTTON_SIZE)

    # -- no work while the dock is hidden (2026-10-14) ---------------------------------------

    def test_a_hidden_histogram_does_no_work_and_catches_up_once_when_shown(self) -> None:
        self._load()
        redraws = []
        original = self.panel._redraw_now
        self.panel._redraw_now = lambda: (redraws.append(1), original())[1]
        self.panel.set_panel_visible(False)  # what the dock's visibilityChanged does
        self.panel._redraw_timer.stop()
        for _ in range(3):  # frames keep arriving while hidden
            self.panel._on_image_rendered(self.panel._image, 0, 500.0)
        _pump(0.4)
        self.assertEqual(redraws, [], "nothing is computed or drawn while hidden")
        self.assertFalse(self.panel._redraw_timer.isActive())
        self.panel.set_panel_visible(True)
        _pump(0.4)
        self.assertEqual(len(redraws), 1, "one catch-up when shown")
        _, y = self.panel._plot._all_pixels_curve.getData()
        self.assertAlmostEqual(float(np.sum(y)), 100.0, places=3)

    def test_a_redraw_already_scheduled_when_the_dock_is_hidden_does_not_run(self) -> None:
        self._load()
        redraws = []
        original = self.panel._redraw_now
        self.panel._redraw_now = lambda: (redraws.append(1), original())[1]
        self.panel._schedule_redraw()  # a timer is running ...
        self.panel.set_panel_visible(False)  # ... and then the dock is hidden
        _pump(0.4)
        self.assertEqual(redraws, [])
        self.panel.set_panel_visible(True)
        _pump(0.4)
        self.assertEqual(len(redraws), 1)

    # -- calm updates while the slideshow plays (2026-10-14) -----------------------------

    def test_calm_mode_throttles_redraws_instead_of_restarting_the_timer(self) -> None:
        timer = self.panel._redraw_timer
        self.panel.set_calm(True)
        self.assertEqual(timer.interval(), 1000)
        timer.stop()
        self.panel._schedule_redraw()
        self.assertTrue(timer.isActive())
        remaining = timer.remainingTime()
        time.sleep(0.05)
        self.panel._schedule_redraw()  # a new frame arrives: the running timer must be left alone
        self.assertLess(timer.remainingTime(), remaining)
        self.panel.set_calm(False)  # Play stopped: back to normal and one catch-up redraw
        self.assertEqual(timer.interval(), 100)
        self.assertTrue(timer.isActive())
        # normal mode is a debounce again: every request restarts the timer
        timer.stop()
        self.panel._schedule_redraw()
        first = timer.remainingTime()
        time.sleep(0.03)
        self.panel._schedule_redraw()
        self.assertGreaterEqual(timer.remainingTime(), first - 2)

    def test_the_image_panels_calm_signal_reaches_the_histogram(self) -> None:
        self.image_panel.calm_histogram_changed.emit(True)
        self.assertEqual(self.panel._redraw_timer.interval(), 1000)
        self.image_panel.calm_histogram_changed.emit(False)
        self.assertEqual(self.panel._redraw_timer.interval(), 100)

    # -- display-settings persistence (2026-09-30) ---------------------------

    def test_initial_display_settings_are_applied_at_construction(self) -> None:
        """`AppSettings.histogram_*` / `HistogramPanel(initial_*=...)` -
        maintainer request: histogram settings-dialog controls must survive
        an app restart. See `test_lspri_rewrite_visual_settings_restore.py`
        for the full round trip through `build_main_window`."""
        panel = HistogramPanel(
            self.image_panel, self.geometry, self.mask, self.chromatic, self.roi_toolbox, self.highlight_range,
            initial_y_mode="counts", initial_log_y=True, initial_bin_width=64, initial_line_width=3.0,
        )
        self.assertEqual(panel._y_mode, "counts")
        self.assertTrue(panel._log_y)
        self.assertEqual(panel._bin_width, 64.0)
        self.assertEqual(panel._line_width, 3.0)
        self.assertEqual(panel._plot._all_pixels_curve.opts["pen"].widthF(), 3.0)

    def test_changing_a_dialog_control_emits_display_settings_changed(self) -> None:
        self._load()
        received: list[tuple[str, bool, int, float]] = []
        self.panel.display_settings_changed.connect(lambda *args: received.append(args))
        self.panel._show_settings_dialog()
        self.panel._settings_dialog.axis_mode_combo.setCurrentIndex(1)  # Counts, not Percent
        self.panel._settings_dialog.scale_combo.setCurrentIndex(1)  # Log
        self.panel._settings_dialog.bin_spin.setValue(32)
        self.panel._settings_dialog.line_width_spin.setValue(4.0)
        self.assertEqual(len(received), 4)
        y_mode, log_y, bin_width, line_width = received[-1]
        self.assertEqual(y_mode, "counts")
        self.assertTrue(log_y)
        self.assertEqual(bin_width, 32)
        self.assertEqual(line_width, 4.0)

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


    # -- the numbers are made on a worker thread (2026-10-14) -----------------------------

    def _blocked_compute(self):
        """A stand-in for `compute.redraw_counts` that waits for `release` before doing the real work and records
        every call (thread, bin count): lets a test hold a redraw "in flight" and see what the panel does meanwhile."""
        release = threading.Event()
        calls = []
        original = compute.redraw_counts

        def blocked(image, region, ignore_mask, edges):
            calls.append((threading.current_thread(), edges.size))
            release.wait(10.0)
            return original(image, region, ignore_mask, edges)

        return release, calls, blocked

    def _plot_x_size(self) -> int:
        x, _ = self.panel._plot._all_pixels_curve.getData()
        return 0 if x is None else len(x)

    def test_the_numbers_are_made_off_the_gui_thread_and_drawn_on_it(self) -> None:
        release, calls, blocked = self._blocked_compute()
        release.set()
        with mock.patch.object(compute, "redraw_counts", blocked):
            self._load()
        self.assertTrue(calls, "the redraw computed nothing")
        self.assertTrue(all(thread is not threading.main_thread() for thread, _ in calls))
        _, y = self.panel._plot._all_pixels_curve.getData()
        self.assertAlmostEqual(float(np.sum(y)), 100.0, places=3)  # and it was drawn

    def test_starting_a_redraw_does_not_wait_for_the_numbers(self) -> None:
        self._load()
        release, calls, blocked = self._blocked_compute()
        try:
            with mock.patch.object(compute, "redraw_counts", blocked):
                started = time.perf_counter()
                self.panel._redraw_now()  # the compute stand-in is stuck until `release`
                waited = time.perf_counter() - started
                self.assertLess(waited, 0.5, "the GUI thread must not wait for the worker")
                _pump(0.2)
                self.assertTrue(self.panel._worker.is_running())
        finally:
            release.set()
        _pump(0.4)
        self.assertFalse(self.panel._worker.is_running())

    def test_while_one_redraw_runs_only_the_newest_request_is_kept(self) -> None:
        self._load()
        release, calls, blocked = self._blocked_compute()
        try:
            with mock.patch.object(compute, "redraw_counts", blocked):
                self.panel._redraw_now()  # job 1: running (stuck)
                _pump(0.2)
                for width in (128.0, 64.0):  # jobs 2 and 3 arrive while job 1 runs: 3 replaces 2
                    self.panel._bin_width = width
                    self.panel._redraw_now()
                release.set()
                _pump(0.6)
        finally:
            release.set()
        self.assertEqual(len(calls), 2, "job 2 was replaced by job 3 and never ran")
        self.assertEqual(calls[1][1], compute.histogram_edges(64.0).size)
        self.assertIn(self._plot_x_size(), (compute.histogram_edges(64.0).size, compute.histogram_edges(64.0).size - 1))

    def test_a_result_for_an_image_that_was_cleared_meanwhile_is_not_drawn(self) -> None:
        self._load()
        release, _calls, blocked = self._blocked_compute()
        try:
            with mock.patch.object(compute, "redraw_counts", blocked):
                self.panel._redraw_now()
                _pump(0.2)
                self.dataset.clear_dataset()
                _pump(0.2)
                release.set()
                _pump(0.5)
        finally:
            release.set()
        self.assertEqual(self._plot_x_size(), 0, "the cleared plot must stay empty")

    def test_a_result_that_arrives_while_hidden_is_not_drawn_until_shown(self) -> None:
        self._load()
        before = self._plot_x_size()
        release, _calls, blocked = self._blocked_compute()
        try:
            with mock.patch.object(compute, "redraw_counts", blocked):
                self.panel._bin_width = 64.0  # a visible change: many more bins than the 512 default
                self.panel._redraw_now()
                _pump(0.2)
                self.panel.set_panel_visible(False)
                release.set()
                _pump(0.5)
                self.assertEqual(self._plot_x_size(), before, "nothing is drawn while hidden")
                self.panel.set_panel_visible(True)  # one catch-up redraw
                _pump(0.6)
        finally:
            release.set()
        self.assertGreater(self._plot_x_size(), before)

    def test_closing_waits_for_a_running_worker(self) -> None:
        self._load()
        release, _calls, blocked = self._blocked_compute()
        with mock.patch.object(compute, "redraw_counts", blocked):
            self.panel._redraw_now()
            _pump(0.2)
            self.assertTrue(self.panel._worker.is_running())
            threading.Timer(0.3, release.set).start()
            self.panel.close()  # must wait for the worker instead of leaving it to emit into a dead widget
            self.assertFalse(self.panel._worker.is_running())


if __name__ == "__main__":
    unittest.main()
