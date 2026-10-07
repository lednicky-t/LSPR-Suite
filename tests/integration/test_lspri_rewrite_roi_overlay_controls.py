"""The Image ribbon's "ROIs" tab display controls: show/hide, colour and
transparency for the sample circles and for the reference rings, and a toggle
for the ROI labels - and what each does to what is drawn on the image.

The values are display-only state owned by `ImagePanel` (no module owns them);
they are remembered across restarts through the UI-state store. The sample
"colour" is the colour of ROIs that have none of their own: a ROI in a group
(or coloured by hand) keeps its own.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch.**
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QToolButton

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_ui import get_active_theme, set_active_theme

    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.gui.app_theme import LSPRI_BRIGHT_THEME, LSPRI_DARK_THEME, apply_app_theme
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
    from lspr_imaging_app.panels.image.background_tab import _ROI_COLOR, _roi_icon
    from lspr_imaging_app.panels.image.roi_icons import ring_icon, spot_icon
    from lspr_imaging_app.panels.image.roi_label_overlay import label_text
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_image_panel import _pump, _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

_DIALOG = "lspr_imaging_app.panels.image.roi_overlay_controls.QColorDialog.getColor"


class RoiOverlayControlsTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset_model = _write_dataset(Path(self._tmp.name))
        self.dataset = DatasetModule()
        self.geometry = GeometryModule()
        self.roi_toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.active_tool = ActiveToolModule()
        self.panel = ImagePanel(
            self.dataset, self.geometry, MaskModule(), ChromaticModule(), BackgroundModule(), self.roi_toolbox,
            self.selection, self.active_tool, ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(),
        )
        self.dataset.load_dataset(self.dataset_model)
        _pump()
        self.sample = self.panel._roi_sample_controls
        self.reference = self.panel._roi_reference_controls

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self._tmp.cleanup()
        if hasattr(self, "_theme_before"):
            set_active_theme(self._theme_before)
        undo_manager.clear()

    def two_rois(self) -> None:
        """ROI 1 in a group (so it has a colour of its own), ROI 2 with none."""
        self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        self.roi_toolbox.add_roi(60.0, 45.0, sample_diameter_px=10.0)
        self.roi_toolbox.group_rois((1,), "A")
        _pump(0.3)

    def points(self, curve) -> int:
        xs = curve.getData()[0]
        return 0 if xs is None else int(sum(1 for x in xs if x == x))  # NaN separators do not count

    def pen_color(self, curve) -> QColor:
        return curve.opts["pen"].color()

    # -- the tab -------------------------------------------------------------------------

    def test_the_rois_tab_holds_the_tools_and_the_three_new_groups(self) -> None:
        self.panel._tool_ribbon.set_category("ROIs")
        for widget in (self.sample, self.reference, self.panel._roi_labels_button):
            self.assertTrue(widget.isVisibleTo(self.panel), widget)
        self.panel._tool_ribbon.set_category("Mask")
        for widget in (self.sample, self.reference, self.panel._roi_labels_button):
            self.assertFalse(widget.isVisibleTo(self.panel), "only on the ROIs tab")

    def test_the_sample_toggle_uses_the_background_tabs_spot_icon(self) -> None:
        """The same drawing, not a lookalike."""
        dim = "#8b95a3"
        same = _roi_icon(True, dim).pixmap(44, 44).toImage() == spot_icon(True, dim, _ROI_COLOR).pixmap(44, 44).toImage()
        self.assertTrue(same)
        self.assertTrue(_roi_icon(False, dim).pixmap(44, 44).toImage() == spot_icon(False, dim, _ROI_COLOR).pixmap(44, 44).toImage())

    def test_icons_change_with_state_and_spot_and_ring_differ(self) -> None:
        def image(icon):
            return icon.pixmap(44, 44).toImage()

        dim, on = "#8b95a3", "#f59e0b"
        self.assertNotEqual(image(spot_icon(True, dim, on)), image(spot_icon(False, dim, on)))
        self.assertNotEqual(image(ring_icon(True, dim, on)), image(ring_icon(False, dim, on)))
        self.assertNotEqual(image(spot_icon(True, dim, on)), image(ring_icon(True, dim, on)))

    # -- sample circles ----------------------------------------------------------------------

    def test_the_sample_toggle_hides_the_circles_and_the_selection_highlight_but_not_the_rings(self) -> None:
        self.two_rois()
        self.selection.set_roi_selection({2})
        _pump(0.2)
        self.assertTrue(all(curve.isVisible() for curve in self.panel._roi_overlay.sample_curves.values()))
        self.assertTrue(self.panel._roi_overlay.selection_curve.isVisible())

        self.sample._toggle_button.click()
        self.assertFalse(any(curve.isVisible() for curve in self.panel._roi_overlay.sample_curves.values()))
        self.assertFalse(self.panel._roi_overlay.selection_curve.isVisible())
        self.assertTrue(self.panel._roi_overlay.reference_curve.isVisible())

        self.sample._toggle_button.click()
        self.assertTrue(all(curve.isVisible() for curve in self.panel._roi_overlay.sample_curves.values()))
        self.assertTrue(self.panel._roi_overlay.selection_curve.isVisible())

    def test_a_curve_made_while_the_circles_are_hidden_stays_hidden(self) -> None:
        self.two_rois()
        self.sample._toggle_button.click()
        self.roi_toolbox.set_roi_colors((1,), "#112233")  # a colour no curve exists for yet
        _pump(0.3)
        self.assertIn("#112233", self.panel._roi_overlay.sample_curves)
        self.assertFalse(self.panel._roi_overlay.sample_curves["#112233"].isVisible())

    def test_the_sample_colour_is_the_colour_of_rois_with_none_of_their_own(self) -> None:
        self.two_rois()
        own = self.roi_toolbox.roi_by_id(1).sample_color_hex
        with mock.patch(_DIALOG, return_value=QColor("#00ff00")):
            self.sample._color_button.click()
        self.assertEqual(self.points(self.panel._roi_overlay.sample_curves["#00ff00"]), 48, "ROI 2 moved to the new default colour")
        self.assertEqual(self.points(self.panel._roi_overlay.sample_curves[own]), 48, "ROI 1 keeps its group tint")
        self.assertEqual(self.points(self.panel._roi_overlay.sample_curve), 0, "nothing is left on the old default")

    def test_a_cancelled_colour_dialog_changes_nothing(self) -> None:
        self.two_rois()
        with mock.patch(_DIALOG, return_value=QColor()):
            self.sample._color_button.click()
        self.assertEqual(self.panel._roi_overlay.sample.color, "#f59e0b")

    def test_sample_transparency_applies_to_every_sample_curve_including_new_ones(self) -> None:
        self.two_rois()
        self.sample._alpha_slider.setValue(50)
        self.assertAlmostEqual(self.panel._roi_overlay.sample.alpha, 0.5)
        for color, curve in self.panel._roi_overlay.sample_curves.items():
            self.assertAlmostEqual(self.pen_color(curve).alphaF(), 0.5, delta=0.01, msg=color)
        self.roi_toolbox.set_roi_colors((2,), "#445566")
        _pump(0.3)
        self.assertAlmostEqual(self.pen_color(self.panel._roi_overlay.sample_curves["#445566"]).alphaF(), 0.5, delta=0.01)
        self.assertEqual(self.pen_color(self.panel._roi_overlay.sample_curves["#445566"]).name(), "#445566", "alpha does not touch the colour")

    def test_sample_circles_are_filled_with_their_colour_and_the_fill_follows_transparency(self) -> None:
        self.two_rois()
        overlay = self.panel._roi_overlay
        own = self.roi_toolbox.roi_by_id(1).sample_color_hex
        for color in (own, overlay.sample.color):
            fill = overlay.sample_fills[color]
            self.assertFalse(fill.path().isEmpty(), f"{color}: the circle is filled, not just outlined")
            self.assertEqual(fill.brush().color().name(), color)
        self.sample._alpha_slider.setValue(50)
        self.assertAlmostEqual(overlay.sample_fills[own].brush().color().alphaF(), 0.5 * 0.3, delta=0.01)

    def test_reference_rings_are_filled_as_rings_and_follow_their_own_transparency(self) -> None:
        self.two_rois()
        fill = self.panel._roi_overlay.reference_fill
        self.assertFalse(fill.path().isEmpty())
        from PyQt6.QtCore import QPointF
        self.assertFalse(fill.path().contains(QPointF(60.0, 45.0)), "the ring's inner opening is empty")
        self.reference._alpha_slider.setValue(40)
        self.assertAlmostEqual(fill.brush().color().alphaF(), 0.4 * 0.3, delta=0.01)
        self.assertEqual(fill.brush().color().name(), self.panel._roi_overlay.reference.color)

    def test_the_selection_highlight_is_not_made_transparent(self) -> None:
        self.two_rois()
        self.sample._alpha_slider.setValue(10)
        self.assertEqual(self.pen_color(self.panel._roi_overlay.selection_curve).alphaF(), 1.0)

    # -- reference rings ---------------------------------------------------------------------

    def test_the_reference_toggle_hides_only_the_rings(self) -> None:
        self.two_rois()
        self.reference._toggle_button.click()
        self.assertFalse(self.panel._roi_overlay.reference_curve.isVisible())
        self.assertTrue(self.panel._roi_overlay.sample_curve.isVisible())
        self.reference._toggle_button.click()
        self.assertTrue(self.panel._roi_overlay.reference_curve.isVisible())

    def test_reference_colour_and_transparency(self) -> None:
        self.two_rois()
        with mock.patch(_DIALOG, return_value=QColor("#ff00ff")):
            self.reference._color_button.click()
        self.reference._alpha_slider.setValue(30)
        pen = self.pen_color(self.panel._roi_overlay.reference_curve)
        self.assertEqual(pen.name(), "#ff00ff")
        self.assertAlmostEqual(pen.alphaF(), 0.3, delta=0.01)
        self.assertEqual(self.pen_color(self.panel._roi_overlay.sample_curve).alphaF(), 1.0, "sample circles untouched")

    # -- labels ----------------------------------------------------------------------------------

    def test_labels_are_off_until_toggled_then_show_number_and_name(self) -> None:
        self.two_rois()
        self.assertEqual(self.panel._roi_overlay.label_item.labels(), [])
        self.roi_toolbox.set_roi_label(2, "spot B")
        _pump(0.3)
        self.panel._roi_labels_button.click()
        texts = {text for _x, _y, _r, text in self.panel._roi_overlay.label_item.labels()}
        self.assertEqual(texts, {"1", "2 spot B"})
        x, y, radius, _ = next(entry for entry in self.panel._roi_overlay.label_item.labels() if entry[3] == "1")
        self.assertEqual((x, y, radius), (40.0, 30.0, 5.0), "anchored at the right edge of the circle")
        self.panel._roi_labels_button.click()
        self.assertEqual(self.panel._roi_overlay.label_item.labels(), [])

    def test_labels_follow_the_rois(self) -> None:
        self.two_rois()
        self.panel._roi_labels_button.click()
        self.roi_toolbox.add_roi(20.0, 15.0, sample_diameter_px=10.0)
        _pump(0.3)
        self.assertEqual(len(self.panel._roi_overlay.label_item.labels()), 3)
        self.roi_toolbox.delete_rois((3,))
        _pump(0.3)
        self.assertEqual(len(self.panel._roi_overlay.label_item.labels()), 2)

    def test_labels_are_hidden_with_the_circles_while_a_preview_tool_is_active(self) -> None:
        self.two_rois()
        self.panel._roi_labels_button.click()
        self.assertEqual(len(self.panel._roi_overlay.label_item.labels()), 2)
        self.active_tool.set_active(ImageTool.CROP, True)
        _pump(0.3)
        self.assertEqual(self.panel._roi_overlay.label_item.labels(), [])

    def test_the_label_text(self) -> None:
        self.assertEqual(label_text(7, None), "7")
        self.assertEqual(label_text(7, ""), "7")
        self.assertEqual(label_text(7, "spot A"), "7 spot A")

    def test_painting_the_labels_does_not_raise(self) -> None:
        self.two_rois()
        self.panel._roi_labels_button.click()
        self.panel.resize(900, 700)
        self.panel.show()
        _pump(0.3)
        image = QImage(900, 700, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        self.panel._roi_overlay.label_item.paint(painter)
        painter.end()
        self.panel.hide()

    # -- remembered state ----------------------------------------------------------------------------

    def test_changes_are_saved_and_restored(self) -> None:
        store = UiStateStore({})
        self.panel.restore_ui_state(store)
        self.sample._toggle_button.click()
        self.reference._alpha_slider.setValue(40)
        with mock.patch(_DIALOG, return_value=QColor("#abcdef")):
            self.sample._color_button.click()
        self.panel._roi_labels_button.click()
        self.assertIs(store.get("image/roi_sample_visible"), False)
        self.assertEqual(store.get("image/roi_sample_color"), "#abcdef")
        self.assertAlmostEqual(store.get("image/roi_reference_alpha"), 0.4)
        self.assertIs(store.get("image/roi_labels"), True)

        self.panel._renderer.stop()
        fresh = ImagePanel(
            self.dataset, self.geometry, MaskModule(), ChromaticModule(), BackgroundModule(), self.roi_toolbox,
            self.selection, self.active_tool, ReferenceFrameModule(), HighlightRangeModule(), mask_scope=MaskScopeModule(),
        )
        try:
            fresh.restore_ui_state(store)
            self.assertFalse(fresh._roi_sample_controls._toggle_button.isChecked())
            self.assertEqual(fresh._roi_sample_controls._color.name(), "#abcdef")
            self.assertEqual(fresh._roi_reference_controls._alpha_slider.value(), 40)
            self.assertTrue(fresh._roi_labels_button.isChecked())
            self.assertFalse(fresh._roi_overlay.sample_curve.isVisible(), "the restored state is applied, not just shown")
            self.assertAlmostEqual(fresh._roi_overlay.reference_curve.opts["pen"].color().alphaF(), 0.4, delta=0.01)
        finally:
            fresh._renderer.stop()

    def test_restoring_does_not_save_what_it_just_read(self) -> None:
        saved: list[dict] = []
        store = UiStateStore({"image/roi_sample_visible": False}, on_changed=saved.append)
        self.panel.restore_ui_state(store)
        store.flush()
        self.assertEqual(saved, [], "nothing was changed by the user")

    def test_rubbish_in_the_saved_state_is_ignored(self) -> None:
        store = UiStateStore({
            "image/roi_sample_visible": "yes", "image/roi_sample_color": "not a colour", "image/roi_sample_alpha": 7,
            "image/roi_reference_alpha": True, "image/roi_labels": 1,
        })
        self.panel.restore_ui_state(store)
        self.assertTrue(self.panel._roi_overlay.sample.visible)
        self.assertEqual(self.panel._roi_overlay.sample.color, "#f59e0b")
        self.assertEqual(self.panel._roi_overlay.sample.alpha, 1.0)
        self.assertEqual(self.panel._roi_overlay.reference.alpha, 1.0)
        self.assertFalse(self.panel._roi_overlay.labels_visible)

    def test_clearing_the_dataset_empties_the_labels(self) -> None:
        self.two_rois()
        self.panel._roi_labels_button.click()
        self.dataset.clear_dataset()
        _pump(0.3)
        self.assertEqual(self.panel._roi_overlay.label_item.labels(), [])

    @staticmethod
    def _painted_width(button: QToolButton) -> int:
        """Width in pixels of what the button actually paints (pixels that
        differ from its own background)."""
        image = button.grab().toImage()
        background = QColor(image.pixel(1, 1)).getRgb()[:3]
        columns = [
            x
            for x in range(image.width())
            for y in range(image.height())
            if sum(abs(a - b) for a, b in zip(QColor(image.pixel(x, y)).getRgb()[:3], background)) > 120
        ]
        return (max(columns) - min(columns) + 1) if columns else 0

    def test_the_new_icons_are_painted_at_the_ribbons_size_under_the_app_theme(self) -> None:
        """The table's toolbar had icons shrunk to a few pixels by a private
        button style under the app-wide style; these use the ribbon's own, and
        this pins it, measured on what is painted."""
        saved = (_APP.styleSheet(), _APP.palette(), _APP.style().objectName())
        self._theme_before = get_active_theme()
        apply_app_theme(_APP, LSPRI_DARK_THEME)
        set_active_theme(LSPRI_DARK_THEME)
        try:
            self.panel.refresh_theme()
            self.panel.resize(900, 700)
            self.panel.show()
            self.panel._tool_ribbon.set_category("ROIs")
            _pump(0.3)
            for name, button in (
                ("sample", self.sample._toggle_button), ("reference", self.reference._toggle_button),
                ("labels", self.panel._roi_labels_button),
            ):
                self.assertGreaterEqual(self._painted_width(button), 14, f"{name} icon is too small")
        finally:
            self.panel.hide()
            _APP.setStyleSheet(saved[0])
            _APP.setPalette(saved[1])
            _APP.setStyle(saved[2])

    def test_a_theme_switch_restyles_the_new_controls_without_error(self) -> None:
        self._theme_before = get_active_theme()
        set_active_theme(LSPRI_BRIGHT_THEME)
        self.panel.refresh_theme()
        self.assertFalse(self.panel._roi_labels_button.icon().isNull())
        self.assertFalse(self.sample._toggle_button.icon().isNull())


if __name__ == "__main__":
    unittest.main()
