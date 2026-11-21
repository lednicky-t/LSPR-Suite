"""The Image panel's View controls in a side ribbon right of the image (2026-11-21): the Preferences choice between the ribbon's View tab and
the column, that the same widgets move (no copies, no lost state), that an overlay's icon is its show / hide switch (a left click) and a
right-click opens the menu with the rest of its controls (beside the button in the column, below it in the tab), and the Histogram's "View
controls are in front of the person" rule.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch.**
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QBoxLayout

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.tool_ribbon import VIEW_TAB
    from lspr_imaging_app.panels.image.view_placement import PLACEMENTS, RIGHT, TAB, valid_placement
    from lspr_imaging_app.panels.preferences_dialog import DEFAULT_PREFERENCES, Preferences, PreferencesDialog
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.storage.ui_state_keys import IMAGE_VIEW_PLACEMENT
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _press(button: QtWidgets.QWidget, which: Qt.MouseButton) -> None:
    """A mouse press on `button` (what the side ribbon's event filter listens for)."""
    point = QPointF(3.0, 3.0)
    event = QMouseEvent(QEvent.Type.MouseButtonPress, point, point, which, which, Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(button, event)


class _PanelCase(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.panel = ImagePanel(
            DatasetModule(), GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(),
        )
        self.view_tab = self.panel._view_tab
        self.placement = self.panel._view_placement
        self.ribbon = self.placement.ribbon
        self.tool_ribbon = self.panel._tool_ribbon

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        undo_manager.clear()


class PlacementTests(_PanelCase):
    def test_it_starts_in_the_ribbon_tab_as_the_app_always_did(self) -> None:
        self.assertEqual(self.panel.view_placement, TAB)
        self.assertTrue(self.ribbon.isHidden())
        self.assertFalse(self.view_tab.is_vertical())
        self.assertFalse(self.tool_ribbon.tab_button(VIEW_TAB).isHidden())
        self.assertTrue(self.placement.host.isAncestorOf(self.view_tab))

    def test_the_same_widgets_move_to_the_column_and_back(self) -> None:
        masks_button = self.view_tab.mask_toggle
        scale_toggle = self.view_tab.scale_bar_controls._toggle
        self.panel.set_view_placement(RIGHT)
        self.assertEqual(self.panel.view_placement, RIGHT)
        self.assertFalse(self.ribbon.isHidden())
        self.assertTrue(self.ribbon.isAncestorOf(self.view_tab), "the View tab object itself moved: nothing is copied")
        self.assertIs(self.panel._view_tab, self.view_tab)
        self.assertTrue(self.ribbon.isAncestorOf(masks_button) and self.ribbon.isAncestorOf(scale_toggle))
        self.assertTrue(self.tool_ribbon.tab_button(VIEW_TAB).isHidden(), "the View tab is gone while the column shows")
        self.panel.set_view_placement(TAB)
        self.assertTrue(self.ribbon.isHidden())
        self.assertFalse(self.view_tab.is_vertical())
        self.assertTrue(self.placement.host.isAncestorOf(self.view_tab))
        self.assertTrue(self.placement.host.isAncestorOf(masks_button))
        self.assertFalse(self.tool_ribbon.tab_button(VIEW_TAB).isHidden())

    def test_the_column_is_a_column_without_captions_and_the_tab_a_row_with_them(self) -> None:
        self.panel.set_view_placement(RIGHT)
        self.assertTrue(self.view_tab.is_vertical())
        self.assertTrue(all(caption.isHidden() for caption in self.view_tab._captions))
        self.assertTrue(all(separator._horizontal for separator in self.view_tab._separators), "the dividers are horizontal lines in the column")
        self.assertEqual(self.view_tab._layout.direction(), QBoxLayout.Direction.TopToBottom)
        self.panel.set_view_placement(TAB)
        self.assertTrue(all(not caption.isHidden() for caption in self.view_tab._captions))
        self.assertFalse(any(separator._horizontal for separator in self.view_tab._separators), "and vertical lines in the tab")
        self.assertEqual(self.view_tab._layout.direction(), QBoxLayout.Direction.LeftToRight)

    def test_the_chunk_group_and_its_divider_follow_the_dataset_in_both_looks(self) -> None:
        """No OME-Zarr in the test: the last group is hidden, and so is the divider before it, whichever look is on."""
        last = len(self.view_tab._separators) - 1
        for where in (RIGHT, TAB):
            self.panel.set_view_placement(where)
            self.assertTrue(self.view_tab._separators[last].isHidden(), where)

    def test_a_control_keeps_its_state_when_it_moves(self) -> None:
        controls = self.view_tab.display_range_controls
        controls.toggle_button.setChecked(True)  # fixed display range, 0 to 65535
        self.view_tab.scale_bar_controls.set_visible(True)
        before = (controls.state(), self.view_tab.scale_bar_controls._toggle.isChecked())
        for where in (RIGHT, TAB, RIGHT):
            self.panel.set_view_placement(where)
            self.assertEqual((controls.state(), self.view_tab.scale_bar_controls._toggle.isChecked()), before, where)

    def test_the_change_is_announced_once_and_the_same_placement_is_not_a_change(self) -> None:
        seen: list[str] = []
        self.panel.view_placement_changed.connect(seen.append)
        self.panel.set_view_placement(RIGHT)
        self.panel.set_view_placement(RIGHT)
        self.panel.set_view_placement(TAB)
        self.assertEqual(seen, [RIGHT, TAB])

    def test_hiding_the_open_view_tab_opens_another_one(self) -> None:
        self.tool_ribbon.set_category(VIEW_TAB)
        self.assertEqual(self.panel.active_ribbon_category(), VIEW_TAB)
        self.panel.set_view_placement(RIGHT)
        self.assertNotEqual(self.panel.active_ribbon_category(), VIEW_TAB)
        self.assertFalse(self.tool_ribbon.set_category(VIEW_TAB), "a hidden tab cannot be opened")
        self.panel.set_view_placement(TAB)
        self.assertTrue(self.tool_ribbon.set_category(VIEW_TAB))

    def test_an_unknown_placement_falls_back_to_the_tab(self) -> None:
        for rubbish in ("left", "", None, 3):
            self.assertEqual(valid_placement(rubbish), TAB)
        self.assertEqual(PLACEMENTS, (TAB, RIGHT))
        self.panel.set_view_placement("left")
        self.assertEqual(self.panel.view_placement, TAB)


class HistogramRuleTests(_PanelCase):
    def test_the_view_controls_are_open_on_the_view_tab_or_always_in_the_column(self) -> None:
        self.tool_ribbon.set_category("Mask")
        self.assertFalse(self.panel.view_controls_open())
        self.tool_ribbon.set_category(VIEW_TAB)
        self.assertTrue(self.panel.view_controls_open())
        self.tool_ribbon.set_category("Mask")
        self.panel.set_view_placement(RIGHT)
        self.assertTrue(self.panel.view_controls_open(), "in the column they are always in view, whichever tab is open")


class RightClickTests(_PanelCase):
    def setUp(self) -> None:
        super().setUp()
        self.panel.set_view_placement(RIGHT)
        self.panel.resize(760, 560)
        self.panel.show()  # the buttons need real places on the screen: a menu opened at x = 0 is pushed right by Qt
        _APP.processEvents()

    def tearDown(self) -> None:
        self.panel.hide()
        super().tearDown()

    def _opener(self, button: QtWidgets.QWidget):
        return next(opener for opener in self.view_tab.openers if opener.button is button)

    def _popup_of(self, button: QtWidgets.QWidget) -> QtWidgets.QWidget:
        return self.view_tab.menu_for(button).actions()[0].defaultWidget()

    def _overlay_buttons(self) -> dict[str, QtWidgets.QToolButton]:
        tab = self.view_tab
        return {
            "histogram": tab.histogram_toggle,
            "mask": tab.mask_toggle,
            "sample": tab._roi_sample_controls.toggle_button,
            "reference": tab._roi_reference_controls.toggle_button,
            "roi labels": tab.roi_labels_button,
            "landmarks": tab._mirrors[0],
            "group labels": tab.group_labels_button,
            "scale bar": tab.scale_bar_toggle,
        }

    def test_every_button_with_a_menu_has_an_opener(self) -> None:
        buttons = [opener.button for opener in self.view_tab.openers]
        self.assertEqual(len(buttons), 10)  # 8 overlays, the AR / MR switch and its "..."
        for name, button in self._overlay_buttons().items():
            self.assertIn(button, buttons, name)
        controls = self.view_tab.display_range_controls
        self.assertIn(controls.toggle_button, buttons)
        self.assertIn(controls.more_button, buttons)

    def test_a_right_click_opens_the_menu_beside_the_button_to_its_left(self) -> None:
        for opener in self.view_tab.openers:
            button, menu = opener.button, opener.menu
            menu.hide()
            opener._hidden_at = float("-inf")
            _press(button, Qt.MouseButton.RightButton)
            self.assertTrue(menu.isVisible(), f"{button.toolTip()[:30]!r}")
            self.assertLessEqual(menu.geometry().right(), button.mapToGlobal(button.rect().topLeft()).x(), "the menu opens to the left of the button")
            menu.hide()

    def test_in_the_tab_the_menu_opens_below_the_button(self) -> None:
        self.panel.set_view_placement(TAB)
        for opener in self.view_tab.openers:
            self.assertFalse(opener._beside)
        opener = self._opener(self.view_tab.histogram_toggle)
        opener._hidden_at = float("-inf")
        _press(opener.button, Qt.MouseButton.RightButton)
        self.assertTrue(opener.menu.isVisible(), "a right-click works in the tab too")
        opener.menu.hide()
        self.panel.set_view_placement(RIGHT)
        self.assertTrue(all(opener._beside for opener in self.view_tab.openers))

    def test_the_overlay_icon_is_the_switch_and_a_right_click_does_not_switch_it(self) -> None:
        def is_on(button: QtWidgets.QToolButton) -> bool:
            return getattr(button, "show_button", button).isChecked()  # the group labels' icon keeps its state in the switch it holds

        for name, button in self._overlay_buttons().items():
            before = is_on(button)
            self._opener(button)._hidden_at = float("-inf")
            _press(button, Qt.MouseButton.RightButton)
            self.assertEqual(is_on(button), before, f"{name}: a right-click only opens its menu")
            self.view_tab.menu_for(button).hide()
            self.assertFalse(self.view_tab.menu_for(button).isVisible())
            button.click()
            self.assertEqual(is_on(button), not before, f"{name}: a left click shows or hides it")
            button.click()

    def test_a_left_click_switches_the_overlay_and_does_not_open_its_menu(self) -> None:
        for name, button in self._overlay_buttons().items():
            self._opener(button)._hidden_at = float("-inf")
            _press(button, Qt.MouseButton.LeftButton)
            self.assertFalse(self.view_tab.menu_for(button).isVisible(), name)

    def test_the_switch_reaches_the_panel_as_it_did_from_the_old_menu(self) -> None:
        before = self.panel._mask_tint.visible
        self.view_tab.mask_toggle.click()
        self.assertEqual(self.panel._mask_tint.visible, not before)
        before = self.panel._highlight_tint.visible
        self.view_tab.histogram_toggle.click()
        self.assertEqual(self.panel._highlight_tint.visible, not before)

    def test_the_masks_and_the_rois_are_one_icon_each_and_the_menus_hold_the_rest(self) -> None:
        tab = self.view_tab
        sample, reference = tab._roi_sample_controls, tab._roi_reference_controls
        for controls in (tab.highlight_controls, tab._mask_controls, sample, reference):
            toggle = controls.toggle_button
            popup = self._popup_of(toggle)
            self.assertTrue(popup.isAncestorOf(controls), "the colour and transparency are in the menu")
            self.assertTrue(popup.isAncestorOf(controls._color_button) and popup.isAncestorOf(controls._alpha_slider))
            self.assertFalse(popup.isAncestorOf(toggle), "the switch is the icon in the ribbon, not a row of the menu")
            self.assertTrue(self.ribbon.isAncestorOf(toggle))
        # Masks: the two icons side by side; ROIs: three.
        self.assertIs(tab.histogram_toggle.parentWidget(), tab.mask_toggle.parentWidget())
        self.assertIs(sample.toggle_button.parentWidget(), tab.roi_labels_button.parentWidget())
        self.assertIs(reference.toggle_button.parentWidget(), tab.roi_labels_button.parentWidget())

    def test_the_label_places_and_size_are_in_the_labels_menu(self) -> None:
        popup = self._popup_of(self.view_tab.roi_labels_button)
        self.assertTrue(popup.isAncestorOf(self.view_tab._roi_placement_copy))
        self.assertTrue(popup.isAncestorOf(self.view_tab._roi_size_copy))

    def test_the_landmarks_icon_is_the_switch_and_every_wavelength_is_in_its_menu(self) -> None:
        tab = self.view_tab
        landmarks, scope = tab._mirrors[0], tab._mirrors[1]
        self.assertTrue(self._popup_of(landmarks).isAncestorOf(scope), "the every-wavelength switch moved under the landmarks icon")
        shown = tab._chromatic_tab.show_button().isChecked()
        landmarks.click()
        self.assertEqual(tab._chromatic_tab.show_button().isChecked(), not shown, "a left click shows or hides the landmarks")
        tab._chromatic_tab.show_button().setChecked(True)  # the scope switch is greyed (as on the Chromatic tab) while they are hidden
        self.assertTrue(scope.isEnabled())
        every = tab._chromatic_tab.scope_button().isChecked()
        scope.click()  # the mirror in the menu still drives the chromatic tab's scope button
        self.assertEqual(tab._chromatic_tab.scope_button().isChecked(), not every)

    def test_the_group_labels_icon_is_the_switch_and_the_strip_has_no_switch_of_its_own(self) -> None:
        menu_button = self.view_tab.group_labels_button
        original = self.panel._roi_tab.group_label_menu
        shown = menu_button.show_button.isChecked()
        menu_button.click()
        self.assertEqual(menu_button.show_button.isChecked(), not shown)
        self.assertEqual(original.show_button.isChecked(), not shown, "the ROIs tab's own menu follows")
        self.assertTrue(menu_button.show_button.isHidden(), "the strip does not repeat the switch")
        self.assertTrue(menu_button._separators[0].isHidden())
        self.assertIsNone(menu_button.menu(), "no menu of its own to open on a left click")
        self.assertIsNotNone(original.menu(), "the ROIs tab's menu opens on a click as before")

    def test_the_scale_bar_icon_is_the_switch_and_the_units_and_the_colour_are_in_its_menu(self) -> None:
        controls = self.view_tab.scale_bar_controls
        toggle = self.view_tab.scale_bar_toggle
        popup = self._popup_of(toggle)
        self.assertTrue(popup.isAncestorOf(controls.units_button), "pixels / micrometers moved under the ruler icon")
        self.assertTrue(popup.isAncestorOf(controls._swatch), "so did the colour")
        self.assertFalse(popup.isAncestorOf(toggle), "the switch is the icon in the ribbon, not a row of the menu")
        self.assertTrue(self.ribbon.isAncestorOf(toggle) and not self.ribbon.isAncestorOf(controls.units_button))
        geometry = self.panel._geometry
        shown = bool(geometry.settings().scale_bar_visible)
        toggle.click()
        self.assertEqual(bool(geometry.settings().scale_bar_visible), not shown, "a left click shows or hides the scale bar")
        self.assertEqual(toggle.isChecked(), not shown)
        toggle.click()

    def test_the_display_range_toggle_opens_its_numbers_on_a_right_click_and_the_dots_on_either(self) -> None:
        controls = self.view_tab.display_range_controls
        _press(controls.toggle_button, Qt.MouseButton.LeftButton)
        self.assertFalse(controls.range_menu.isVisible(), "a left press on the AR / MR switch is left to the button")
        self._opener(controls.toggle_button)._hidden_at = float("-inf")
        _press(controls.toggle_button, Qt.MouseButton.RightButton)
        self.assertTrue(controls.range_menu.isVisible(), "its right-click opens the numbers")
        controls.range_menu.hide()
        self._opener(controls.more_button)._hidden_at = float("-inf")
        _press(controls.more_button, Qt.MouseButton.LeftButton)
        self.assertTrue(controls.range_menu.isVisible(), "the dots open them on a left click")
        controls.range_menu.hide()

    def test_a_press_just_after_the_menu_closed_is_the_click_that_closed_it(self) -> None:
        button = self.view_tab.display_range_controls.more_button
        menu = self.view_tab.menu_for(button)
        self._opener(button)._hidden_at = float("-inf")
        _press(button, Qt.MouseButton.LeftButton)
        menu.hide()  # Qt closes the menu on the press and hands the same press to the button
        _press(button, Qt.MouseButton.LeftButton)
        self.assertFalse(menu.isVisible(), "it must not open again at once")

    def test_every_overlay_icon_says_what_the_right_click_gives(self) -> None:
        for name, button in self._overlay_buttons().items():
            self.assertIn("Right-click", button.toolTip(), name)


class RememberedTests(_PanelCase):
    def test_a_saved_right_placement_is_restored_and_a_change_is_saved(self) -> None:
        saved: dict[str, object] = {}
        store = UiStateStore({IMAGE_VIEW_PLACEMENT.key: RIGHT}, on_changed=saved.update)
        self.panel.restore_ui_state(store)
        self.assertEqual(self.panel.view_placement, RIGHT)
        self.assertNotIn(IMAGE_VIEW_PLACEMENT.key, saved, "restoring saves nothing")
        self.panel.set_view_placement(TAB)
        store.flush()
        self.assertEqual(saved[IMAGE_VIEW_PLACEMENT.key], TAB)

    def test_rubbish_in_the_saved_state_leaves_the_tab(self) -> None:
        for rubbish in ("sideways", 7, True):
            self.panel.restore_ui_state(UiStateStore({IMAGE_VIEW_PLACEMENT.key: rubbish}))
            self.assertEqual(self.panel.view_placement, TAB, rubbish)


class PreferencesTests(unittest.TestCase):
    def test_the_default_is_the_tab_and_the_dialog_round_trips_both_choices(self) -> None:
        self.assertEqual(DEFAULT_PREFERENCES.view_placement, TAB)
        for where in (TAB, RIGHT):
            current = Preferences(view_placement=where)
            dialog = PreferencesDialog(current)
            self.assertEqual(dialog.view_placement_combo.currentData(), where)
            self.assertEqual(dialog.values(), current)

    def test_restore_defaults_puts_the_tab_back_on_screen(self) -> None:
        dialog = PreferencesDialog(Preferences(view_placement=RIGHT))
        dialog._restore_defaults()
        self.assertEqual(dialog.values().view_placement, TAB)


if __name__ == "__main__":
    unittest.main()
