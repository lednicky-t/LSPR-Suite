"""The selection toolbar's View pop-up (2026-11-16): the colour of the selection's three outlines, an infill for each (5 opacity steps, step 0 = none),
and the Venn mode icons that wear the same circle colours.

`SelectionStyle` is the one state; the outlines on the image, the Venn icons of the mode button and the pop-up follow it. Pins: the defaults are the
colours the borders had before (and no fill, so nothing looks different until the user picks something); a step above 0 tints the pixels inside an
outline with its colour at the step's opacity; the left circle of an icon is the *past* colour and the right circle the *present* one; the choice is
remembered (a bad saved value falls back); the View button sits at the bottom of the toolbar, outside the scrolling column.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6 import QtWidgets
from PyQt6.QtGui import QColor

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools import ActiveToolModule, BackgroundModule, ChromaticModule, GeometryModule, MaskModule, MaskScopeModule
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.combine_mode_icons import combine_mode_icon
    from lspr_imaging_app.panels.image.selection_palette import COLORS, FILL_OPACITY_STEPS, PAST, PRESENT, RESULT
    from lspr_imaging_app.panels.image.selection_style import MAX_FILL_STEP, SelectionStyle
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import AreaSelectionMode, AreaSelectionModule, HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.selection.area_selection_module import ADD, INTERSECT, REPLACE, SUBTRACT
    from lspr_imaging_app.storage import ui_state_keys as keys
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

_SHAPE = (64, 80)


def _pump(seconds: float = 0.1) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _pump_until(condition, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


class SelectionStyleStateTest(unittest.TestCase):
    """The state alone: no panel."""

    def test_the_defaults_are_the_borders_colours_and_no_fill(self) -> None:
        style = SelectionStyle()
        for role in (PAST, PRESENT, RESULT):
            self.assertEqual(style.color(role), QColor(COLORS[role]).name())
            self.assertEqual(style.step(role), 0)
            self.assertEqual(style.fill_alpha(role), 0.0)
            self.assertEqual(style.fill_color(role).alpha(), 0)

    def test_five_steps_and_the_first_is_no_fill(self) -> None:
        self.assertEqual(len(FILL_OPACITY_STEPS), 5)
        self.assertEqual(FILL_OPACITY_STEPS[0], 0.0)
        self.assertEqual(MAX_FILL_STEP, 4)
        style = SelectionStyle()
        style.set_step(PAST, 2)
        self.assertAlmostEqual(style.fill_color(PAST).alphaF(), 0.5, places=2)
        self.assertEqual(style.fill_color(PAST).name(), style.color(PAST), "the infill has the border's colour")
        style.set_step(PAST, 99)
        self.assertEqual(style.step(PAST), 4, "held to the last step")
        style.set_step(PAST, -3)
        self.assertEqual(style.step(PAST), 0, "and to the first")

    def test_a_change_is_reported_once_and_an_invalid_colour_is_refused(self) -> None:
        style = SelectionStyle()
        seen: list[int] = []
        style.changed.connect(lambda: seen.append(1))
        self.assertTrue(style.set_color(PAST, "#102030"))
        self.assertEqual(style.color(PAST), "#102030")
        style.set_color(PAST, "#102030")  # the same again: nothing happens
        style.set_step(PRESENT, 3)
        style.set_step(PRESENT, 3)
        self.assertEqual(len(seen), 2)
        self.assertFalse(style.set_color(PAST, "not a colour"))
        self.assertEqual(style.color(PAST), "#102030")

    def test_every_border_is_shown_by_default_and_a_switch_is_reported_once(self) -> None:
        style = SelectionStyle()
        self.assertTrue(all(style.border_shown(role) for role in (PAST, PRESENT, RESULT)))
        seen: list[int] = []
        style.changed.connect(lambda: seen.append(1))
        style.set_border_shown(PAST, False)
        style.set_border_shown(PAST, False)
        self.assertFalse(style.border_shown(PAST))
        self.assertTrue(style.border_shown(PRESENT), "one outline at a time")
        self.assertEqual(len(seen), 1)

    def test_the_border_switch_is_remembered_and_a_bad_value_falls_back(self) -> None:
        saved = {
            keys.SELECTION_STYLE_KEYS[PAST]["border"].key: False,
            keys.SELECTION_STYLE_KEYS[PRESENT]["border"].key: "no",  # the wrong type: the default (shown) stays
        }
        store = UiStateStore(saved)
        style = SelectionStyle()
        style.bind_ui_state(store)
        self.assertFalse(style.border_shown(PAST))
        self.assertTrue(style.border_shown(PRESENT))
        style.set_border_shown(RESULT, False)
        self.assertIs(store.get(keys.SELECTION_STYLE_KEYS[RESULT]["border"].key), False)

    def test_what_is_saved_comes_back_and_a_bad_value_falls_back(self) -> None:
        saved = {
            keys.SELECTION_STYLE_KEYS[PAST]["color"].key: "#aabbcc",
            keys.SELECTION_STYLE_KEYS[PAST]["fill"].key: 3,
            keys.SELECTION_STYLE_KEYS[PRESENT]["color"].key: "not a colour",  # edited by hand: the default stays
            keys.SELECTION_STYLE_KEYS[PRESENT]["fill"].key: "two",  # the wrong type: the default stays
            keys.SELECTION_STYLE_KEYS[RESULT]["fill"].key: 40,  # out of range: held to the last step
        }
        writes: list[dict[str, object]] = []
        store = UiStateStore(saved, on_changed=writes.append)
        style = SelectionStyle()
        style.bind_ui_state(store)
        store.flush()
        self.assertEqual(writes, [], "restoring does not save what it just read")
        self.assertEqual((style.color(PAST), style.step(PAST)), ("#aabbcc", 3))
        self.assertEqual((style.color(PRESENT), style.step(PRESENT)), (QColor(COLORS[PRESENT]).name(), 0))
        self.assertEqual(style.step(RESULT), 4)

        style.set_color(PRESENT, "#010203")
        style.set_step(PRESENT, 1)
        store.flush()
        self.assertEqual(store.get(keys.SELECTION_STYLE_KEYS[PRESENT]["color"].key), "#010203")
        self.assertEqual(store.get(keys.SELECTION_STYLE_KEYS[PRESENT]["fill"].key), 1)

    def test_the_stored_roles_are_the_palettes(self) -> None:
        """`storage` may not import the palette, so it spells the roles itself: they must be the same."""
        self.assertEqual(set(keys.SELECTION_STYLE_KEYS), {PAST, PRESENT, RESULT})
        for role_keys in keys.SELECTION_STYLE_KEYS.values():
            self.assertTrue(all(key in keys.HAND_WIRED_KEYS for key in role_keys.values()))

    def test_the_left_circle_wears_the_past_colour_and_the_right_circle_the_present_one(self) -> None:
        for how in (REPLACE, ADD, SUBTRACT, INTERSECT):
            image = combine_mode_icon(how, "#ff0000", "#0000ff").pixmap(48, 48).toImage()
            self.assertEqual(image.pixelColor(5, 24).name(), "#ff0000", how)  # on the left circle's border, at its leftmost point
            self.assertEqual(image.pixelColor(42, 24).name(), "#0000ff", how)  # on the right circle's border, at its rightmost point
        default = combine_mode_icon(ADD).pixmap(48, 48).toImage()
        self.assertEqual(default.pixelColor(5, 24).name(), QColor(COLORS[PAST]).name())
        self.assertEqual(default.pixelColor(42, 24).name(), QColor(COLORS[PRESENT]).name())
        self.assertEqual(combine_mode_icon(ADD).cacheKey(), combine_mode_icon(ADD, COLORS[PAST], COLORS[PRESENT]).cacheKey(), "the default is cached once")


class SelectionViewPopupTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self._tmp.name)
        path = root / "c0_w500.tif"
        tifffile.imwrite(str(path), np.full(_SHAPE, 150.0, dtype=np.float32))
        dataset = ImageDataset(folder=root, records=[ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)], source_format="image_stack")
        self.dataset = DatasetModule()
        self.area = AreaSelectionModule()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), area_selection=self.area,
        )
        self.panel.resize(900, 600)
        self.panel.show()
        self.dataset.load_dataset(dataset)
        _pump_until(lambda: self.panel._area_tool._image_shape is not None)
        _pump()
        self.toolbar = self.panel._selection_tools.toolbar
        self.style = self.toolbar.style
        self.outlines = self.panel._area_tool._outlines

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()

    # -- one style for everyone -----------------------------------------------------------------------------

    def test_the_outlines_the_icons_and_the_pop_up_share_one_style(self) -> None:
        self.assertIs(self.style, self.panel._area_tool.style)
        self.assertIs(self.style, self.outlines.style)
        self.assertIs(self.style, self.toolbar.view_form._style)

    # -- the image ---------------------------------------------------------------------------------------------

    def test_nothing_is_filled_until_a_step_is_picked_and_the_dashes_keep_their_colours(self) -> None:
        self.area.set_rectangle(10, 10, 40, 40)
        _pump(0.05)
        for role in (PAST, PRESENT, RESULT):
            self.assertFalse(self.outlines.fill_item(role).isVisible(), role)
        self.assertEqual(self.outlines.past_items()[1].pen().color().name(), QColor(COLORS[PAST]).name())

    def test_a_step_above_zero_tints_the_selection_with_its_colour_at_the_steps_opacity(self) -> None:
        self.area.set_rectangle(10, 10, 40, 40)
        fill = self.outlines.fill_item(PAST)
        self.style.set_step(PAST, 2)
        self.assertTrue(fill.isVisible())
        self.assertEqual(fill.brush().color().name(), self.style.color(PAST))
        self.assertAlmostEqual(fill.brush().color().alphaF(), 0.5, places=2)
        self.assertEqual(fill.path().boundingRect(), self.outlines.past_items()[0].path().boundingRect(), "the infill covers the outline's shape")
        self.assertLess(fill.zValue(), self.outlines.past_items()[0].zValue(), "under the line, never over it")
        self.style.set_step(PAST, 0)
        self.assertFalse(fill.isVisible(), "step 0 is no fill")

    def test_a_picked_colour_recolours_the_dashes_and_the_infill_at_once(self) -> None:
        self.area.set_rectangle(10, 10, 40, 40)
        self.style.set_step(PAST, 1)
        self.style.set_color(PAST, "#123456")
        self.assertEqual(self.outlines.past_items()[1].pen().color().name(), "#123456")
        self.assertEqual(self.outlines.fill_item(PAST).brush().color().name(), "#123456")
        self.assertAlmostEqual(self.outlines.fill_item(PAST).brush().color().alphaF(), 0.25, places=2)
        self.assertEqual(self.outlines.past_items()[0].pen().color().name(), "#ffffff", "the white line underneath stays white")

    def test_the_infill_follows_its_outline(self) -> None:
        self.style.set_step(PAST, 3)
        fill = self.outlines.fill_item(PAST)
        self.assertFalse(fill.isVisible(), "no selection, no infill")
        self.area.set_rectangle(10, 10, 40, 40)
        self.assertTrue(fill.isVisible())
        first = fill.path().boundingRect()
        self.area.clear()
        self.assertFalse(fill.isVisible(), "a deselected selection leaves no tint")
        self.area.set_rectangle(20, 20, 60, 50)
        self.assertTrue(fill.isVisible())
        self.assertNotEqual(fill.path().boundingRect(), first, "and a new selection gets its own shape")

    def test_a_shape_that_changed_while_the_step_was_zero_is_filled_correctly_once_a_step_is_picked(self) -> None:
        self.area.set_rectangle(10, 10, 40, 40)
        self.area.set_rectangle(20, 20, 60, 50)  # the infill never took the first path: it was off
        self.style.set_step(PAST, 1)
        self.assertEqual(self.outlines.fill_item(PAST).path().boundingRect(), self.outlines.past_items()[0].path().boundingRect())

    def test_with_the_border_off_only_the_infill_is_drawn_and_nothing_animates(self) -> None:
        self.area.set_rectangle(10, 10, 40, 40)
        self.style.set_step(PAST, 2)
        base, ants = self.outlines.past_items()
        self.assertTrue(base.isVisible() and ants.isVisible())
        self.assertTrue(self.outlines.is_animating(), "the dashes crawl while a border is drawn")
        self.style.set_border_shown(PAST, False)
        self.assertFalse(base.isVisible() or ants.isVisible(), "no line")
        self.assertTrue(self.outlines.fill_item(PAST).isVisible(), "the infill stays")
        self.assertFalse(self.outlines.is_animating(), "a static infill: no timer, no repaint 6 times a second")
        self.style.set_border_shown(PAST, True)
        self.assertTrue(base.isVisible() and ants.isVisible())
        self.assertTrue(self.outlines.is_animating())

    def test_the_border_can_be_turned_off_before_the_selection_is_made_and_a_new_shape_still_draws(self) -> None:
        self.style.set_border_shown(PAST, False)
        self.style.set_step(PAST, 1)
        self.area.set_rectangle(10, 10, 40, 40)
        self.assertFalse(self.outlines.past_items()[0].isVisible())
        self.assertTrue(self.outlines.fill_item(PAST).isVisible())
        self.assertFalse(self.outlines.is_animating())
        self.assertEqual(self.outlines.fill_item(PAST).path().boundingRect(), self.outlines.past_items()[0].path().boundingRect(), "the lines hold the path, hidden")
        self.area.clear()
        self.assertFalse(self.outlines.fill_item(PAST).isVisible())

    def test_the_dashes_keep_crawling_while_another_outline_still_has_a_border(self) -> None:
        self.toolbar.tool_button(AreaSelectionMode.RECTANGLE).click()
        self.area.set_rectangle(10, 10, 40, 40)
        self.style.set_border_shown(PAST, False)
        self.assertFalse(self.outlines.is_animating())
        tool = self.panel._area_tool
        self.assertTrue(tool.begin_gesture(20.0, 20.0))
        tool.update_gesture(50.0, 45.0)
        self.assertTrue(self.outlines.present_items()[1].isVisible())
        self.assertTrue(self.outlines.is_animating(), "the dragged shape's border is on")
        tool.end_gesture()

    def test_the_dragged_shape_has_its_own_infill(self) -> None:
        self.toolbar.tool_button(AreaSelectionMode.RECTANGLE).click()
        self.style.set_step(PRESENT, 4)
        self.style.set_color(PRESENT, "#00ff00")
        tool = self.panel._area_tool
        self.assertTrue(tool.begin_gesture(10.0, 10.0))
        tool.update_gesture(40.0, 30.0)
        fill = self.outlines.fill_item(PRESENT)
        self.assertTrue(fill.isVisible())
        self.assertEqual(fill.brush().color().name(), "#00ff00")
        self.assertAlmostEqual(fill.brush().color().alphaF(), 1.0, places=2)
        tool.end_gesture()

    # -- the Venn icons ----------------------------------------------------------------------------------------

    def test_the_mode_button_and_its_menu_wear_the_picked_circle_colours(self) -> None:
        how = self.area.combine_mode()
        self.assertEqual(self.toolbar.mode_button.icon().cacheKey(), combine_mode_icon(how).cacheKey(), "unchanged by default")
        self.style.set_color(PAST, "#ff0000")
        self.style.set_color(PRESENT, "#0000ff")
        self.assertEqual(self.toolbar.mode_button.icon().cacheKey(), combine_mode_icon(how, "#ff0000", "#0000ff").cacheKey())
        for mode, action in self.toolbar._mode_actions.items():
            self.assertEqual(action.icon().cacheKey(), combine_mode_icon(mode, "#ff0000", "#0000ff").cacheKey(), mode)
        picture = self.toolbar.mode_button.icon().pixmap(48, 48).toImage()
        self.assertEqual(picture.pixelColor(42, 24).name(), "#0000ff")
        # A step changes only the infill, never the icons (their fill is a fixed grey).
        before = self.toolbar.mode_button.icon().cacheKey()
        self.style.set_step(PAST, 3)
        self.assertEqual(self.toolbar.mode_button.icon().cacheKey(), before)

    def test_a_saved_colour_reaches_the_icons_when_the_toolbar_restores_its_state(self) -> None:
        store = UiStateStore({keys.SELECTION_STYLE_KEYS[PAST]["color"].key: "#abcdef"})
        self.toolbar.bind_ui_state(store)
        how = self.area.combine_mode()
        self.assertEqual(self.style.color(PAST), "#abcdef")
        self.assertEqual(self.toolbar.mode_button.icon().cacheKey(), combine_mode_icon(how, "#abcdef", COLORS[PRESENT]).cacheKey())

    # -- the pop-up ----------------------------------------------------------------------------------------------

    def test_the_view_button_is_at_the_bottom_of_the_toolbar_outside_the_scrolling_column(self) -> None:
        _pump(0.05)
        button = self.toolbar.view_button
        self.assertFalse(self.toolbar.column.content.isAncestorOf(button), "pinned: it does not scroll away with the tools")
        self.assertGreater(button.geometry().top(), self.toolbar.column.geometry().bottom() - 1)
        self.assertLessEqual(self.toolbar.height() - button.geometry().bottom(), 8, "level with the bottom of the ribbon")
        self.assertFalse(button.icon().isNull())
        self.assertTrue(button.toolTip())

    def test_a_click_opens_the_pop_up(self) -> None:
        self.assertFalse(self.toolbar.view_menu.isVisible())
        self.toolbar.view_button.click()
        self.assertTrue(self.toolbar.view_menu.isVisible())
        self.toolbar.view_menu.close()

    def test_the_pop_up_has_a_swatch_and_a_five_step_wedge_per_outline_and_changes_the_style(self) -> None:
        form = self.toolbar.view_form
        self.assertEqual(set(form.swatches), {PAST, PRESENT, RESULT})
        for role in (PAST, PRESENT, RESULT):
            wedge = form.wedges[role]
            self.assertEqual(wedge.value(), 0)
            wedge.setValue(3)
            self.assertEqual(self.style.step(role), 3, role)
            wedge.setValue(99)  # the wedge holds the value to its last position
            self.assertEqual(self.style.step(role), MAX_FILL_STEP, role)
        with patch.object(QtWidgets.QColorDialog, "getColor", return_value=QColor("#336699")):
            form.swatches[PRESENT].click()
        self.assertEqual(self.style.color(PRESENT), "#336699")
        self.assertIn("#336699", form.swatches[PRESENT].styleSheet())
        with patch.object(QtWidgets.QColorDialog, "getColor", return_value=QColor()):  # the dialog was cancelled
            form.swatches[PRESENT].click()
        self.assertEqual(self.style.color(PRESENT), "#336699")

    def test_each_row_has_a_border_switch_that_changes_the_style_and_follows_it(self) -> None:
        form = self.toolbar.view_form
        self.assertEqual(set(form.border_buttons), {PAST, PRESENT, RESULT})
        for role in (PAST, PRESENT, RESULT):
            button = form.border_buttons[role]
            self.assertTrue(button.isCheckable() and button.isChecked(), "shown by default")
            on_picture = button.icon().pixmap(28, 28).toImage()
            button.click()
            self.assertFalse(self.style.border_shown(role), role)
            self.assertNotEqual(button.icon().pixmap(28, 28).toImage(), on_picture, "crossed out while hidden")
            self.style.set_border_shown(role, True)  # changed elsewhere
            self.assertTrue(button.isChecked(), role)
            self.assertEqual(button.icon().pixmap(28, 28).toImage(), on_picture)

    def test_the_pop_up_follows_a_change_made_elsewhere(self) -> None:
        form = self.toolbar.view_form
        self.style.set_step(RESULT, 2)
        self.assertEqual(form.wedges[RESULT].value(), 2)
        self.style.set_color(PAST, "#445566")
        self.assertIn("#445566", form.swatches[PAST].styleSheet())
        self.assertIn("50 %", form.wedges[RESULT].toolTip())
        self.style.set_step(RESULT, 0)
        self.assertIn("none", form.wedges[RESULT].toolTip())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
