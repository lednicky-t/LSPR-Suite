"""The 3 x 3 label-place grid (ROIs tab Display menu, Groups label menu, and their View-tab copies) and the View
tab's "All" section (Show all / Hide all).

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch.**
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_ui import get_active_theme, set_active_theme

    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.gui.app_theme import LSPRI_BRIGHT_THEME
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.label_placement import PLACEMENTS
    from lspr_imaging_app.panels.image.label_placement_picker import LabelPlacementPicker
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class LabelPlacementPickerTest(unittest.TestCase):
    def test_nine_cells_and_the_given_one_is_checked(self) -> None:
        picker = LabelPlacementPicker("top-right")
        self.assertEqual([picker.button(p).isChecked() for p in PLACEMENTS].count(True), 1)
        self.assertEqual(picker.placement(), "top-right")
        self.assertEqual(len(PLACEMENTS), 9)

    def test_a_click_picks_the_cell_and_reports_it_once(self) -> None:
        picker = LabelPlacementPicker("right")
        seen: list[str] = []
        picker.placement_changed.connect(seen.append)
        picker.button("bottom-left").click()
        self.assertEqual((picker.placement(), seen), ("bottom-left", ["bottom-left"]))
        self.assertFalse(picker.button("right").isChecked(), "one cell at a time")
        picker.button("bottom-left").click()  # the cell already picked: nothing changes, nothing is reported
        self.assertEqual(seen, ["bottom-left"])
        self.assertTrue(picker.button("bottom-left").isChecked(), "a click never unchecks the pick")

    def test_set_placement_can_show_a_value_without_reporting_it(self) -> None:
        picker = LabelPlacementPicker("right")
        seen: list[str] = []
        picker.placement_changed.connect(seen.append)
        picker.set_placement("center", emit=False)
        self.assertEqual((picker.placement(), seen), ("center", []))
        picker.set_placement("top")
        self.assertEqual(seen, ["top"])
        with self.assertRaises(ValueError):
            picker.set_placement("diagonal")
        with self.assertRaises(ValueError):
            LabelPlacementPicker("diagonal")

    def test_a_copy_follows_the_original_silently(self) -> None:
        original, copy = LabelPlacementPicker("left"), LabelPlacementPicker("right")
        seen: list[str] = []
        copy.placement_changed.connect(seen.append)
        copy.sync_from(original)
        self.assertEqual((copy.placement(), seen), ("left", []))

    def test_the_pick_is_remembered_and_rubbish_in_the_saved_state_is_ignored(self) -> None:
        saved: dict[str, object] = {}
        store = UiStateStore({}, on_changed=saved.update)
        picker = LabelPlacementPicker("right")
        picker.bind_ui_state(store, "image/test_placement")
        picker.button("top-left").click()
        store.flush()
        self.assertEqual(saved["image/test_placement"], "top-left")
        restored = LabelPlacementPicker("right")
        restored.bind_ui_state(UiStateStore(dict(saved)), "image/test_placement")
        self.assertEqual(restored.placement(), "top-left")
        for rubbish in ("diagonal", 3, None, True):
            ignoring = LabelPlacementPicker("bottom")
            ignoring.bind_ui_state(UiStateStore({"image/test_placement": rubbish}), "image/test_placement")
            self.assertEqual(ignoring.placement(), "bottom", rubbish)

    def test_a_theme_switch_restyles_it(self) -> None:
        picker = LabelPlacementPicker("right")
        before = picker.button("right").styleSheet()
        picker.refresh_theme(LSPRI_BRIGHT_THEME)
        self.assertNotEqual(picker.button("right").styleSheet(), before)


class _PanelCase(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.geometry = GeometryModule()
        self.panel = ImagePanel(
            DatasetModule(), self.geometry, MaskModule(), ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(),
        )
        self.roi_tab = self.panel._roi_tab
        self.view_tab = self.panel._view_tab

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        undo_manager.clear()


class RoiLabelPlacementTest(_PanelCase):
    def test_the_grid_sits_next_to_the_labels_toggle_in_the_display_menu(self) -> None:
        popup = self.roi_tab._display_menu.menu().actions()[0].defaultWidget()
        picker = self.roi_tab.label_placement_picker
        self.assertTrue(popup.isAncestorOf(picker))
        self.assertIs(picker.parentWidget(), self.roi_tab.labels_button.parentWidget(), "the same row as the toggle")
        view_popup = self.view_tab._roi_display_menu.menu().actions()[0].defaultWidget()
        self.assertTrue(view_popup.isAncestorOf(self.view_tab._roi_placement_copy), "and on the View tab's copy of the menu")

    def test_the_default_place_is_right_where_the_labels_always_were(self) -> None:
        self.assertEqual(self.roi_tab.label_placement_picker.placement(), "right")
        self.assertEqual(self.panel._roi_overlay.label_item.placement(), "right")

    def test_picking_a_cell_moves_the_labels_and_the_view_tab_copy_follows(self) -> None:
        self.roi_tab.label_placement_picker.button("bottom").click()
        self.assertEqual(self.panel._roi_overlay.label_placement, "bottom")
        self.assertEqual(self.panel._roi_overlay.label_item.placement(), "bottom")
        self.assertEqual(self.view_tab._roi_placement_copy.placement(), "bottom")

    def test_picking_on_the_view_tab_copy_picks_it_on_the_rois_tab_too(self) -> None:
        self.view_tab._roi_placement_copy.button("top-left").click()
        self.assertEqual(self.roi_tab.label_placement_picker.placement(), "top-left")
        self.assertEqual(self.panel._roi_overlay.label_item.placement(), "top-left")

    def test_the_pick_is_saved_and_restored_on_every_copy(self) -> None:
        saved: dict[str, object] = {}
        store = UiStateStore({}, on_changed=saved.update)
        self.panel.restore_ui_state(store)
        self.view_tab._roi_placement_copy.button("center").click()
        store.flush()
        self.assertEqual(saved["image/roi_label_placement"], "center")

        self.panel._renderer.stop()
        fresh = ImagePanel(
            DatasetModule(), self.geometry, MaskModule(), ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(), mask_scope=MaskScopeModule(),
        )
        try:
            fresh.restore_ui_state(UiStateStore(dict(saved)))
            self.assertEqual(fresh._roi_tab.label_placement_picker.placement(), "center")
            self.assertEqual(fresh._view_tab._roi_placement_copy.placement(), "center")
            self.assertEqual(fresh._roi_overlay.label_item.placement(), "center", "applied, not just shown")
        finally:
            fresh._renderer.stop()
            self.panel = fresh  # tearDown stops it again; harmless


class GroupLabelPlacementTest(_PanelCase):
    def test_the_grid_replaces_the_side_drop_down_and_the_view_tab_has_a_copy_of_the_menu(self) -> None:
        menu = self.roi_tab.group_label_menu
        self.assertFalse(hasattr(menu, "side"), "the four-way drop-down is gone")
        self.assertEqual(menu.placement.placement(), "top", "the old default")
        copy = self.view_tab._group_label_menu
        self.assertIsNot(copy, menu)
        self.assertEqual(copy.placement.placement(), "top")

    def test_a_pick_reaches_the_overlay_and_the_view_tab_copy(self) -> None:
        self.roi_tab.group_label_menu.placement.button("bottom-right").click()
        self.assertEqual(self.panel._roi_overlay.group_labels.side, "bottom-right")
        self.assertEqual(self.view_tab._group_label_menu.placement.placement(), "bottom-right")

    def test_a_change_on_the_view_tab_copy_is_applied_and_saved_as_if_made_on_the_original(self) -> None:
        saved: dict[str, object] = {}
        store = UiStateStore({}, on_changed=saved.update)
        self.panel.restore_ui_state(store)
        copy = self.view_tab._group_label_menu
        copy.placement.button("left").click()
        copy.show_button.click()
        copy.box.setValue(55)
        copy.angle.rotate_button().click()  # 45 degrees
        store.flush()
        settings = self.panel._roi_overlay.group_labels
        self.assertEqual((settings.side, settings.visible, settings.box_percent, settings.angle), ("left", True, 55.0, 45))
        original = self.roi_tab.group_label_menu
        self.assertEqual(original.settings(), settings)
        self.assertEqual((saved["image/group_labels/side"], saved["image/group_labels/visible"]), ("left", True))

    def test_the_copy_follows_every_control_of_the_original(self) -> None:
        original, copy = self.roi_tab.group_label_menu, self.view_tab._group_label_menu
        original.position.button("last").click()
        original.min_size.setValue(9)
        original.show_button.click()
        self.assertEqual(copy.settings(), original.settings())
        self.assertEqual(copy.settings().position, "last")


class AllSectionTest(_PanelCase):
    def states(self) -> dict[str, bool]:
        panel, view = self.panel, self.view_tab
        return {
            "highlight": view.highlight_controls._toggle_button.isChecked(),
            "mask (View tab)": view._mask_controls._toggle_button.isChecked(),
            "mask (Mask tab)": panel._mask_tab.overlay_controls._toggle_button.isChecked(),
            "landmarks": panel._chromatic_tab.show_landmarks(),
            "sample": view._roi_sample_controls._toggle_button.isChecked(),
            "sample (ROIs tab)": self.roi_tab.sample_controls._toggle_button.isChecked(),
            "reference": view._roi_reference_controls._toggle_button.isChecked(),
            "reference (ROIs tab)": self.roi_tab.reference_controls._toggle_button.isChecked(),
            "labels": self.roi_tab.labels_button.isChecked(),
            "group labels": self.roi_tab.group_label_menu.show_button.isChecked(),
            "group labels (View tab)": view._group_label_menu.show_button.isChecked(),
            "scale bar": bool(self.geometry.settings().scale_bar_visible),
        }

    def applied(self) -> dict[str, bool]:
        """What the panel actually draws, not just what the buttons show."""
        panel = self.panel
        return {
            "highlight": panel._highlight_tint.visible,
            "mask": panel._mask_tint.visible,
            "sample": panel._roi_overlay.sample.visible,
            "reference": panel._roi_overlay.reference.visible,
            "labels": panel._roi_overlay.labels_visible,
            "group labels": panel._roi_overlay.group_labels.visible,
        }

    def test_the_section_sits_right_after_display_range_with_show_all_and_hide_all(self) -> None:
        layout = self.view_tab.layout()
        self.assertTrue(layout.itemAt(0).widget().isAncestorOf(self.view_tab.display_range_controls))
        all_group = layout.itemAt(2).widget()  # itemAt(1) is the separator
        controls = self.view_tab._all_controls
        self.assertTrue(all_group.isAncestorOf(controls))
        self.assertEqual(self.view_tab._all_label.text(), "All")
        self.assertFalse(controls.show_button.icon().isNull())
        self.assertFalse(controls.hide_button.icon().isNull())
        self.assertIn("Show all", controls.show_button.toolTip())
        self.assertIn("Hide all", controls.hide_button.toolTip())

    def test_hide_all_hides_every_overlay_and_show_all_shows_every_one(self) -> None:
        controls = self.view_tab._all_controls
        controls.hide_button.click()
        self.assertEqual([name for name, on in self.states().items() if on], [], "every toggle is off")
        self.assertEqual([name for name, on in self.applied().items() if on], [], "and nothing is drawn")
        controls.show_button.click()
        self.assertEqual([name for name, on in self.states().items() if not on], [], "every toggle is on")
        self.assertEqual([name for name, on in self.applied().items() if not on], [], "and everything is drawn")

    def test_it_also_works_from_a_mixed_state(self) -> None:
        self.roi_tab.labels_button.setChecked(True)
        self.view_tab.highlight_controls.set_visible(False)
        self.view_tab._group_label_menu.show_button.setChecked(True)
        self.view_tab._all_controls.show_button.click()
        self.assertTrue(all(self.states().values()))
        self.view_tab._all_controls.hide_button.click()
        self.assertFalse(any(self.states().values()))

    def test_the_individual_toggles_icons_follow(self) -> None:
        def icon_image(button):
            return button.icon().pixmap(44, 44).toImage()

        sample, labels = self.view_tab._roi_sample_controls._toggle_button, self.roi_tab.labels_button
        mask, group = self.view_tab._mask_controls._toggle_button, self.roi_tab.group_label_menu.show_button
        landmarks = self.panel._chromatic_tab.show_button()
        self.view_tab._all_controls.show_button.click()
        shown = {id(b): icon_image(b) for b in (sample, labels, mask, group, landmarks)}
        self.view_tab._all_controls.hide_button.click()
        for button in (sample, labels, mask, group, landmarks):
            self.assertNotEqual(icon_image(button), shown[id(button)], "its icon changed with it")
        # The View tab's mirror of the labels toggle follows the ROIs tab's button, icon and all.
        self.assertFalse(labels.isChecked())

    def test_the_background_view_and_the_landmark_scope_are_left_alone(self) -> None:
        background = self.panel._background_tab.view_button()
        scope = self.panel._chromatic_tab.scope_button()
        background.setChecked(True)
        scope.setChecked(True)
        self.view_tab._all_controls.hide_button.click()
        self.assertTrue(background.isChecked() and scope.isChecked())
        background.setChecked(False)
        scope.setChecked(False)
        self.view_tab._all_controls.show_button.click()
        self.assertFalse(background.isChecked() or scope.isChecked(), "show all does not swap the image for the background")

    def test_a_toggle_already_in_the_wanted_state_reports_nothing(self) -> None:
        self.view_tab._all_controls.show_button.click()
        seen: list[object] = []
        self.view_tab.mask_visibility_changed.connect(seen.append)
        self.view_tab.roi_overlay_changed.connect(lambda *args: seen.append(args))
        self.roi_tab.labels_toggled.connect(seen.append)
        self.view_tab._all_controls.show_button.click()
        self.assertEqual(seen, [])

    def test_each_toggle_is_saved_as_if_it_had_been_clicked(self) -> None:
        saved: dict[str, object] = {}
        store = UiStateStore({}, on_changed=saved.update)
        self.panel.restore_ui_state(store)
        self.view_tab._all_controls.show_button.click()
        store.flush()
        # Sample and reference start visible, so showing all changes (and saves) only what started hidden.
        for key in ("image/roi_labels", "image/group_labels/visible"):
            self.assertIs(saved.get(key), True, key)
        self.view_tab._all_controls.hide_button.click()
        store.flush()
        for key in ("image/roi_sample_visible", "image/roi_reference_visible", "image/roi_labels", "image/group_labels/visible"):
            self.assertIs(saved.get(key), False, key)

    def test_a_theme_switch_restyles_the_section_without_error(self) -> None:
        theme_before = get_active_theme()
        try:
            set_active_theme(LSPRI_BRIGHT_THEME)
            self.panel.refresh_theme()
            self.assertFalse(self.view_tab._all_controls.show_button.icon().isNull())
        finally:
            set_active_theme(theme_before)


if __name__ == "__main__":
    unittest.main()
