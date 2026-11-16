"""The toolbar's three select-by-value tools (2026-11-15): Histogram range / Threshold / Local contrast, armed like the wand, a click on the image runs them.

A real `ImagePanel` on a one-frame dataset (noisy background, a bright disk and a dark disk). Pins: each tool selects exactly the pixels the
Mask tab's tool used to add to the mask; the result is cut to the lock and equals the unlocked result AND the lock; the mode picker and the keys
say how it combines; the numbers are the mask tools' `MaskSettings`; the old Mask-tab icons are on the buttons; the right-click opens each
tool's numbers; the right-drag on the image changes them (sideways the threshold, up the sigma).
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QContextMenuEvent, QIcon

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

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
    from lspr_imaging_app.image_tools.mask import raster_tools
    from lspr_imaging_app.image_tools.preprocess import histogram_highlight_region
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image import mask_edit_tool_icons as old_icons
    from lspr_imaging_app.panels.image.histogram_overlay_toggle import HistogramOverlayToggle
    from lspr_imaging_app.panels.image.selection_by_value import MODE_METHODS, ValueMethod
    from lspr_imaging_app.panels.mask_tool_settings import push_tool_settings
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import (
        AreaSelectionMode,
        AreaSelectionModule,
        HighlightRangeModule,
        ReferenceFrameModule,
        SelectionModule,
    )
    from lspr_imaging_app.selection.area_selection_module import ADD, REPLACE, SUBTRACT, how_for_keys
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

FRAME = (0, 500.0)
SHAPE = (120, 160)
BRIGHT = (40, 50, 8.0)  # row, col, radius
DARK = (80, 115, 10.0)


def _disk(row: int, col: int, radius: float) -> np.ndarray:
    yy, xx = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
    return np.hypot(yy - row, xx - col) <= radius


def _box(top: int, bottom: int, left: int, right: int) -> np.ndarray:
    pixels = np.zeros(SHAPE, dtype=bool)
    pixels[top:bottom, left:right] = True
    return pixels


def _pump_until(condition, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def _pump(seconds: float = 0.1) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


class _Click:
    """The slice of pyqtgraph's MouseClickEvent the panel reads."""

    def __init__(self, scene_pos: QPointF, button: Qt.MouseButton, modifiers: Qt.KeyboardModifier) -> None:
        self._pos, self._button, self._modifiers = scene_pos, button, modifiers

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._pos

    def button(self) -> Qt.MouseButton:
        return self._button

    def modifiers(self) -> Qt.KeyboardModifier:
        return self._modifiers


class _RightDrag:
    """The slice of pyqtgraph's MouseDragEvent the right-drag handlers read."""

    def __init__(self, down: QPointF, here: QPointF, *, start: bool, finish: bool) -> None:
        self._down, self._here, self._start, self._finish = down, here, start, finish

    def isStart(self) -> bool:  # noqa: N802
        return self._start

    def isFinish(self) -> bool:  # noqa: N802
        return self._finish

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._here

    def buttonDownScenePos(self) -> QPointF:  # noqa: N802
        return self._down


_MODE_OF = {method: mode for mode, method in MODE_METHODS.items()}


def _write_dataset(root: Path) -> ImageDataset:
    image = np.random.default_rng(4).normal(1000.0, 15.0, SHAPE)
    image[_disk(*BRIGHT)] += 600.0
    image[_disk(*DARK)] -= 500.0
    path = root / "c0_w500.tif"
    tifffile.imwrite(str(path), image.astype(np.float32))
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)
    return ImageDataset(folder=root, records=[record], source_format="image_stack")


class SelectByValueTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.mask = MaskModule()
        self.area = AreaSelectionModule()
        self.highlight = HighlightRangeModule()
        self.active_tool = ActiveToolModule()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), self.mask, ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), self.active_tool, ReferenceFrameModule(), self.highlight,
            mask_scope=MaskScopeModule(), area_selection=self.area,
        )
        self.panel.resize(1100, 800)
        self.panel.show()
        self.messages: list[str] = []
        self.panel.tool_status_changed.connect(self.messages.append)
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump_until(lambda: self.panel._selection_tools.value_selector._image is not None and self.panel._image_item.levels is not None)
        _pump()
        self.toolbar = self.panel._selection_tools.toolbar
        self.selector = self.panel._selection_tools.value_selector
        self.form = self.selector.form
        self._pick(ValueMethod.HISTOGRAM)  # the tool most tests use

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        undo_manager.clear()
        self._tmp.cleanup()

    # -- helpers --------------------------------------------------------------------------------------------------

    def _pick(self, method: ValueMethod) -> None:
        """Arm the tool of `method` (a click on its button; picking the armed one again would leave it, so only when it is not armed)."""
        mode = _MODE_OF[method]
        if self.active_tool.active() is not ImageTool.SELECT_AREA or self.area.mode() is not mode:
            self.toolbar.tool_button(mode).click()

    def _image_click(self, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier) -> None:
        """A left click on the image (anywhere: the value tools are image-wide), as the panel receives it."""
        scene = self.panel._plot.vb.mapViewToScene(QPointF(10.5, 10.5))
        self.panel._on_scene_clicked(_Click(scene, Qt.MouseButton.LeftButton, modifiers))

    def _click_button(self, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier) -> None:
        """Run the armed tool with a click on the image and wait for it (the name is from when it was a button)."""
        self._image_click(modifiers)
        self.assertTrue(_pump_until(lambda: not self.selector.is_busy()), "the method finished")
        _pump(0.05)

    def _right_drag(self, dx: float, dy_up: float, *, finish: bool = True) -> None:
        """Hold the right button on the image and move by (dx right, dy_up up) screen pixels; the drag is reported from a few pixels in."""
        interaction = self.panel._interaction
        down = QPointF(300.0, 300.0)
        interaction.on_right_drag(_RightDrag(down, QPointF(down.x() + 4.0, down.y()), start=True, finish=False))
        interaction.on_right_drag(_RightDrag(down, QPointF(down.x() + dx, down.y() - dy_up), start=False, finish=False))
        if finish:
            interaction.on_right_drag(_RightDrag(down, QPointF(down.x() + dx, down.y() - dy_up), start=False, finish=True))

    def _selected(self) -> np.ndarray:
        selected = self.area.selected_mask(SHAPE)
        self.assertIsNotNone(selected, self.messages[-1:])
        return selected

    def _plane(self) -> np.ndarray:
        return self.dataset.load_plane_uncached(*FRAME)

    def _lock_box(self, top: int = 20, bottom: int = 100, left: int = 20, right: int = 130) -> np.ndarray:
        box = _box(top, bottom, left, right)
        self.area.combine_region(box, REPLACE)
        self.toolbar.lock_button.click()
        return box

    # -- Histogram range -------------------------------------------------------------------------------------------

    def test_histogram_range_selects_the_pixels_in_the_highlighted_range(self) -> None:
        self.highlight.set_range(1400.0, 2000.0)
        self._click_button()
        expected = histogram_highlight_region(self.selector._image, 1400.0, 2000.0)
        self.assertTrue(expected.any())
        np.testing.assert_array_equal(self._selected(), expected)
        self.assertTrue(self._selected()[BRIGHT[0], BRIGHT[1]])
        self.assertEqual(undo_manager.undo_label, "Select by histogram range")
        self.assertIn("Histogram range: selected", self.messages[-1])

    def test_with_no_histogram_range_nothing_happens_and_the_status_says_why(self) -> None:
        self._click_button()
        self.assertFalse(self.area.has_selection())
        self.assertIn("no range on the Histogram", self.messages[-1])

    def test_a_range_that_finds_no_pixel_leaves_the_selection_alone(self) -> None:
        self.area.combine_region(_box(0, 5, 0, 5), REPLACE)
        self.highlight.set_range(50000.0, 60000.0)
        self._click_button()
        np.testing.assert_array_equal(self._selected(), _box(0, 5, 0, 5))
        self.assertIn("nothing found", self.messages[-1])

    def test_a_result_inside_the_lock_equals_the_unlocked_result_and_the_lock(self) -> None:
        self.highlight.set_range(300.0, 800.0)  # the dark disk
        self._click_button()
        free = self._selected().copy()
        self.area.clear()
        box = self._lock_box(60, 100, 115, 150)  # the dark disk is centred on column 115: the box keeps its right half
        self.assertTrue((free & box).any() and (free & ~box).any(), "the box cuts the dark disk")
        self._click_button()
        np.testing.assert_array_equal(self._selected(), free & box)

    def test_nothing_found_inside_the_lock_says_so(self) -> None:
        self._lock_box(100, 118, 2, 40)  # empty background corner
        self.highlight.set_range(1400.0, 2000.0)  # the bright disk, far away
        self._click_button()
        self.assertFalse(self.area.has_selection())
        self.assertIn("inside the lock", self.messages[-1])

    # -- Threshold and Local contrast ------------------------------------------------------------------------------

    def _set_threshold(self, percent: float = 20.0, sigma: int = 30) -> None:
        self.form.threshold_spin.setValue(percent)
        self.form.profile_sigma_spin.setValue(sigma)

    def test_threshold_selects_exactly_the_pixels_the_mask_function_finds_on_the_picked_side(self) -> None:
        """2026-11-16: the Threshold has two modes. Brighter (the default) selects what is above the local background, Darker what is below it."""
        self._pick(ValueMethod.THRESHOLD)
        self._set_threshold()
        self.assertEqual(self.form.threshold_side(), "brighter", "the default")
        self._click_button()
        brighter = raster_tools.create_relative_contrast_mask(self._plane(), sigma_px=30.0, threshold_fraction=0.20, side="brighter")
        self.assertTrue(brighter.any())
        np.testing.assert_array_equal(self._selected(), brighter)
        self.assertTrue(self._selected()[_disk(*BRIGHT)].any() and not self._selected()[_disk(*DARK)].any(), "the bright disk, not the dark one")
        self.assertEqual(undo_manager.undo_label, "Select by threshold (brighter)")
        self.form.set_threshold_side("darker")
        self._click_button()
        darker = raster_tools.create_relative_contrast_mask(self._plane(), sigma_px=30.0, threshold_fraction=0.20, side="darker")
        np.testing.assert_array_equal(self._selected(), darker)
        self.assertTrue(self._selected()[_disk(*DARK)].any() and not self._selected()[_disk(*BRIGHT)].any(), "the dark disk, not the bright one")
        self.assertEqual(undo_manager.undo_label, "Select by threshold (darker)")
        self.assertIn("(darker)", self.messages[-1])

    def test_threshold_inside_a_lock_equals_the_unlocked_result_and_the_lock(self) -> None:
        self._pick(ValueMethod.THRESHOLD)
        self._set_threshold()
        self.form.set_threshold_side("darker")  # the dark disk (row 80, col 115, radius 10), which the lock below cuts through
        self._click_button()
        free = self._selected().copy()
        self.area.clear()
        box = self._lock_box(60, 100, 100, 118)
        self.assertTrue((free & box).any() and (free & ~box).any())
        self._click_button()
        np.testing.assert_array_equal(self._selected(), free & box)

    def test_local_contrast_selects_exactly_the_pixels_the_mask_tool_found(self) -> None:
        self._pick(ValueMethod.LOCAL_CONTRAST)
        self.form.contrast_sigma_spin.setValue(12)
        self.form.z_spin.setValue(3.0)
        self._click_button()
        expected = raster_tools.create_local_contrast_mask(self._plane(), sigma_px=12.0, z_threshold=3.0)
        self.assertTrue(expected.any())
        np.testing.assert_array_equal(self._selected(), expected)
        self.assertEqual(undo_manager.undo_label, "Select by local contrast")

    def test_a_threshold_click_marks_the_tool_busy_until_the_result_arrives(self) -> None:
        self._pick(ValueMethod.THRESHOLD)
        self._set_threshold(percent=25.0, sigma=31)  # numbers not computed before: no cache hit
        button = self.toolbar.tool_button(AreaSelectionMode.THRESHOLD)
        self._image_click()
        self.assertTrue(self.selector.is_busy())
        self.assertIn("Working", button.toolTip())
        self._image_click()  # a second click while it works is ignored, not queued
        self.assertTrue(_pump_until(lambda: not self.selector.is_busy()))
        self.assertNotIn("Working", button.toolTip())
        self.assertEqual(undo_manager.undo_label, "Select by threshold (brighter)")
        undo_manager.undo()
        self.assertFalse(self.area.has_selection(), "the one click made exactly one step")

    # -- how it combines ------------------------------------------------------------------------------------------------

    def test_the_mode_picker_and_the_keys_say_how_the_result_combines(self) -> None:
        self.highlight.set_range(1400.0, 2000.0)  # the bright disk
        self._click_button()
        bright = self._selected().copy()
        self.highlight.set_range(300.0, 800.0)  # the dark disk
        self.area.set_combine_mode(ADD)
        self._click_button()
        np.testing.assert_array_equal(self._selected(), bright | histogram_highlight_region(self.selector._image, 300.0, 800.0))
        self.area.set_combine_mode(REPLACE)
        self.highlight.set_range(1400.0, 2000.0)
        self._click_button(Qt.KeyboardModifier.AltModifier)  # Alt at the click: take away
        np.testing.assert_array_equal(self._selected(), histogram_highlight_region(self.selector._image, 300.0, 800.0))

    def test_how_for_keys_is_photoshops_rule_with_the_sticky_mode_as_the_default(self) -> None:
        self.assertEqual(how_for_keys(False, False, SUBTRACT), SUBTRACT)
        self.assertEqual(how_for_keys(True, False, SUBTRACT), ADD)
        self.assertEqual(how_for_keys(False, True, ADD), SUBTRACT)
        self.assertEqual(how_for_keys(True, True, REPLACE), "intersect")

    # -- the button, the wheel, the pop-up, the numbers -------------------------------------------------------------

    def test_the_three_tools_wear_the_old_mask_tab_icons_with_a_frame_when_armed(self) -> None:
        """2026-11-15 (maintainer: "using the previous icons not these new ones"): the very pictures the Mask tab's picker showed."""
        wanted = {
            AreaSelectionMode.HISTOGRAM: old_icons.histogram_selection_icon,
            AreaSelectionMode.THRESHOLD: old_icons.threshold_icon,
            AreaSelectionMode.CONTRAST: old_icons.local_contrast_icon,
        }
        pictures = []
        for mode, make in wanted.items():
            icon = self.toolbar.tool_button(mode).icon()
            off = icon.pixmap(48, 48, QIcon.Mode.Normal, QIcon.State.Off).toImage()
            on = icon.pixmap(48, 48, QIcon.Mode.Normal, QIcon.State.On).toImage()
            self.assertEqual(off, make().pixmap(48, 48).toImage(), mode)
            self.assertNotEqual(off, on, "the armed tool wears a frame")
            pictures.append(off)
        self.assertEqual(len({image.cacheKey() for image in pictures}), 3)
        modify = self.toolbar.modify_button.icon().pixmap(48, 48).toImage()
        self.assertEqual(modify, old_icons.morphology_icon().pixmap(48, 48).toImage(), "Modify selection: the old Morphology icon")
        # Draw (2026-11-16): the Tabler pencil that turns blue while armed, like Rectangle / Lasso / Wand: no frame, no fixed grey
        brush = self.toolbar.tool_button(AreaSelectionMode.BRUSH)
        self.assertFalse(brush.isChecked())
        idle = brush.icon().pixmap(48, 48, QIcon.Mode.Normal, QIcon.State.Off).toImage()
        brush.click()
        armed = brush.icon().pixmap(48, 48, QIcon.Mode.Normal, QIcon.State.Off).toImage()
        self.assertNotEqual(idle, armed, "the armed Draw tool changes colour")
        self.assertEqual(
            brush.icon().pixmap(48, 48, QIcon.Mode.Normal, QIcon.State.On).toImage(), armed, "and has no separate framed picture"
        )
        brush.click()
        self.assertEqual(brush.icon().pixmap(48, 48, QIcon.Mode.Normal, QIcon.State.Off).toImage(), idle)

    def test_a_tool_arms_with_its_button_and_a_second_click_leaves_it(self) -> None:
        button = self.toolbar.tool_button(AreaSelectionMode.THRESHOLD)
        self.assertFalse(button.isChecked())
        button.click()
        self.assertTrue(button.isChecked())
        self.assertIs(self.area.mode(), AreaSelectionMode.THRESHOLD)
        self.assertFalse(self.toolbar.tool_button(AreaSelectionMode.HISTOGRAM).isChecked(), "one tool at a time")
        button.click()
        self.assertFalse(button.isChecked())
        self.assertIsNone(self.active_tool.active())
        self.assertFalse(self.area.has_selection(), "arming and leaving a tool selects nothing")

    def test_a_click_does_nothing_unless_a_value_tool_is_armed(self) -> None:
        self.highlight.set_range(1400.0, 2000.0)
        self.toolbar.tool_button(AreaSelectionMode.HISTOGRAM).click()  # leave the tool armed by setUp
        self.assertIsNone(self.active_tool.active())
        self._image_click()
        _pump(0.05)
        self.assertFalse(self.area.has_selection())

    def test_a_right_click_opens_the_numbers_of_that_tool_and_a_left_click_does_not(self) -> None:
        self.assertFalse(self.toolbar.value_menu.isVisible())
        for mode, box in (
            (AreaSelectionMode.HISTOGRAM, self.form._histogram_box),
            (AreaSelectionMode.THRESHOLD, self.form._threshold_box),
            (AreaSelectionMode.CONTRAST, self.form._contrast_box),
        ):
            button = self.toolbar.tool_button(mode)
            centre = button.rect().center()
            QtWidgets.QApplication.sendEvent(button, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, centre, button.mapToGlobal(centre)))
            self.assertTrue(self.toolbar.value_menu.isVisible(), mode)
            self.assertIs(self.form.picked_method(), MODE_METHODS[mode])
            self.assertTrue(box.isVisible() or not box.isHidden(), mode)
            self.toolbar.value_menu.close()

    def test_the_form_shows_only_the_picked_methods_numbers(self) -> None:
        for method in ValueMethod:
            self.form.set_method(method)
            self.assertEqual(not self.form._histogram_box.isHidden(), method is ValueMethod.HISTOGRAM)
            self.assertEqual(not self.form._threshold_box.isHidden(), method is ValueMethod.THRESHOLD)
            self.assertEqual(not self.form._contrast_box.isHidden(), method is ValueMethod.LOCAL_CONTRAST)
            self.assertIn(self.form.method_label(), self.form.title.text())

    def test_the_histogram_pop_up_has_a_copy_of_the_view_tabs_show_hide_switch(self) -> None:
        """2026-11-16: the Histogram range's pop-up holds a histogram-icon toggle for the highlight tint; it is the View tab -> Masks switch's
        copy: a click goes to the real control (and the panel's tint), and the real control's changes come back."""
        toggles = self.form.findChildren(HistogramOverlayToggle)
        self.assertEqual(len(toggles), 1)
        toggle = toggles[0]
        controls = self.panel._view_tab.highlight_controls
        tint = self.panel._highlight_tint
        self.assertTrue(self.form._histogram_box.isAncestorOf(toggle), "inside the Histogram range's part, so Threshold / Local contrast do not show it")
        self.assertEqual(toggle.isChecked(), controls.is_shown())

        def picture() -> object:
            return toggle.icon().pixmap(48, 48).toImage()

        shown_picture = picture()
        self.assertEqual(shown_picture, old_icons.histogram_selection_icon(True).pixmap(48, 48).toImage())
        toggle.click()  # hide it from the pop-up
        self.assertFalse(controls.is_shown())
        self.assertFalse(tint.visible, "the panel's tint follows, as for a click on the View tab")
        self.assertFalse(toggle.isChecked())
        self.assertEqual(picture(), old_icons.histogram_selection_icon(False).pixmap(48, 48).toImage())
        controls.set_visible(True)  # show it from the View tab (or Show all)
        self.assertTrue(toggle.isChecked())
        self.assertTrue(tint.visible)
        self.assertEqual(picture(), shown_picture)
        controls.set_visible(False)
        self.assertFalse(toggle.isChecked(), "a change made elsewhere is followed without a click")

    # -- right-drag on the image changes the armed tool's numbers -----------------------------------------------------

    def test_right_drag_with_threshold_armed_sets_the_threshold_sideways_and_sigma_upwards(self) -> None:
        self._pick(ValueMethod.THRESHOLD)
        self._set_threshold(percent=20.0, sigma=30)
        self._right_drag(80.0, 80.0, finish=False)  # 80 px right and 80 px up = one doubling each
        self.assertAlmostEqual(self.form.threshold_spin.value(), 10.0, places=1, msg="right = a bigger region = a lower threshold")
        self.assertEqual(self.form.profile_sigma_spin.value(), 60, "up = a bigger sigma")
        readout = self.panel._selection_tools.value_drag[AreaSelectionMode.THRESHOLD].readout
        self.assertTrue(readout.isVisible())
        self.assertIn("Threshold 10.0 %", readout.text())
        self.assertIn("Sigma 60 px", readout.text())
        self.panel._interaction.on_right_drag(
            _RightDrag(QPointF(300.0, 300.0), QPointF(380.0, 220.0), start=False, finish=True)
        )
        self.assertFalse(readout.isVisible())
        self.assertAlmostEqual(self.mask.settings().relative_threshold_fraction, 0.10, places=3, msg="the numbers are the mask tools' settings")
        self.assertEqual(self.mask.settings().relative_profile_sigma_px, 60.0)

    def test_dragging_the_other_way_halves_the_sigma_and_doubles_the_threshold_and_stays_in_range(self) -> None:
        self._pick(ValueMethod.THRESHOLD)
        self._set_threshold(percent=20.0, sigma=30)
        self._right_drag(-80.0, -80.0)
        self.assertAlmostEqual(self.form.threshold_spin.value(), 40.0, places=1)
        self.assertEqual(self.form.profile_sigma_spin.value(), 15)
        self._set_threshold(percent=400.0, sigma=1500)
        self._right_drag(-800.0, 800.0)  # far beyond the ends: kept inside the boxes' own ranges
        self.assertEqual(self.form.threshold_spin.value(), self.form.threshold_spin.maximum())
        self.assertEqual(self.form.profile_sigma_spin.value(), self.form.profile_sigma_spin.maximum())

    def test_right_drag_with_local_contrast_armed_sets_z_sideways_and_sigma_upwards(self) -> None:
        self._pick(ValueMethod.LOCAL_CONTRAST)
        self.form.z_spin.setValue(4.0)
        self.form.contrast_sigma_spin.setValue(12)
        self._right_drag(80.0, 80.0)
        self.assertAlmostEqual(self.form.z_spin.value(), 2.0, places=1, msg="right = a bigger region = a lower z")
        self.assertEqual(self.form.contrast_sigma_spin.value(), 24)

    def test_the_histogram_tool_has_no_numbers_to_drag_and_leaves_the_right_button_alone(self) -> None:
        self._pick(ValueMethod.HISTOGRAM)
        before = (self.form.threshold_spin.value(), self.form.z_spin.value())
        self.assertFalse(self.panel._interaction.on_right_drag(_RightDrag(QPointF(300, 300), QPointF(380, 300), start=True, finish=False)))
        self.assertEqual((self.form.threshold_spin.value(), self.form.z_spin.value()), before)

    def test_right_drag_does_nothing_when_no_tool_is_armed(self) -> None:
        self._pick(ValueMethod.THRESHOLD)
        self._set_threshold(percent=20.0, sigma=30)
        self.toolbar.tool_button(AreaSelectionMode.THRESHOLD).click()  # leave the tool
        self.assertFalse(self.panel._interaction.on_right_drag(_RightDrag(QPointF(300, 300), QPointF(380, 300), start=True, finish=False)))
        self.assertAlmostEqual(self.form.threshold_spin.value(), 20.0, places=1)

    def test_the_numbers_are_the_mask_tools_settings_both_ways(self) -> None:
        self.form.threshold_spin.setValue(37.0)
        self.assertAlmostEqual(self.mask.settings().relative_threshold_fraction, 0.37)
        self.form.z_spin.setValue(4.5)
        self.assertAlmostEqual(self.mask.settings().local_contrast_z_threshold, 4.5)
        push_tool_settings(self.mask, relative_profile_sigma_px=77.0, local_contrast_sigma_px=19.0)
        self.assertEqual(self.form.profile_sigma_spin.value(), 77)
        self.assertEqual(self.form.contrast_sigma_spin.value(), 19)

    def test_the_range_label_follows_the_histogram(self) -> None:
        self.assertIn("No range yet", self.form.range_label.text())
        self.highlight.set_range(1000.0, 2000.0)
        self.assertIn("1,000 to 2,000", self.form.range_label.text())


if __name__ == "__main__":
    unittest.main()
