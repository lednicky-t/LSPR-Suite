"""The toolbar's Threshold tool, live and with two sides (2026-11-16, maintainer: "make selection by threshold live, so it is visible when changing
parameters how it influences the selection ... inverse selection, not over threshold but under, changed by scrolling over the icon, which changes the
icon ... applying by clicking").

A real `ImagePanel` on a one-frame dataset (noisy background, a bright disk and a dark disk). Pins: while the Threshold tool is armed the pixels a
click would select are tinted on the image (`MaskEditPreview` + `EditPreviewLayer`) and follow the threshold, the sigma and the side; changing the
threshold or the side costs no new blur (the slow half is kept), a new sigma does; the tint is cut to the lock but **not** to the selection; no other
tool tints; Play and another frame hide it; a click selects exactly what was tinted; the wheel over the button flips Brighter / Darker, the icon
pictures it (coloured part over or under the line) and it is remembered; the form has the same switch.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QIcon, QWheelEvent

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
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image import mask_edit_tool_icons as icons
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import (
        AreaSelectionMode,
        AreaSelectionModule,
        HighlightRangeModule,
        ReferenceFrameModule,
        SelectionModule,
    )
    from lspr_imaging_app.selection.area_selection_module import REPLACE
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
PERCENT, SIGMA = 20.0, 30
_REAL_DEVIATION = raster_tools.relative_deviation  # kept before any test patches the module's name with a counting spy


def _disk(row: int, col: int, radius: float) -> np.ndarray:
    yy, xx = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
    return np.hypot(yy - row, xx - col) <= radius


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


def _write_dataset(root: Path) -> ImageDataset:
    image = np.random.default_rng(4).normal(1000.0, 15.0, SHAPE)
    image[_disk(*BRIGHT)] += 600.0
    image[_disk(*DARK)] -= 500.0
    path = root / "c0_w500.tif"
    tifffile.imwrite(str(path), image.astype(np.float32))
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)
    return ImageDataset(folder=root, records=[record], source_format="image_stack")


def _close(picked: QColor, wanted: str, tolerance: int = 40) -> bool:
    other = QColor(wanted)
    return all(abs(a - b) <= tolerance for a, b in ((picked.red(), other.red()), (picked.green(), other.green()), (picked.blue(), other.blue())))


class ThresholdLivePreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.mask = MaskModule()
        self.area = AreaSelectionModule()
        self.active_tool = ActiveToolModule()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), self.mask, ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), self.active_tool, ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), area_selection=self.area,
        )
        self.panel.resize(1100, 800)
        self.panel.show()
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump_until(lambda: self.panel._selection_tools.value_selector._image is not None and self.panel._image_item.levels is not None)
        _pump()
        self.toolbar = self.panel._selection_tools.toolbar
        self.selector = self.panel._selection_tools.value_selector
        self.form = self.selector.form
        self.preview = self.panel._mask_tab.edit_preview
        self.layer = self.panel._edit_preview_layer
        self.button = self.toolbar.tool_button(AreaSelectionMode.THRESHOLD)
        self.form.threshold_spin.setValue(PERCENT)
        self.form.profile_sigma_spin.setValue(SIGMA)

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        undo_manager.clear()
        self._tmp.cleanup()

    # -- helpers -----------------------------------------------------------------------------------------------------

    def _arm(self) -> None:
        if self.active_tool.active() is not ImageTool.SELECT_AREA or self.area.mode() is not AreaSelectionMode.THRESHOLD:
            self.button.click()

    def _plane(self) -> np.ndarray:
        return self.dataset.load_plane_uncached(*FRAME)

    def _expected(self, side: str, percent: float | None = None, sigma: int | None = None) -> np.ndarray:
        percent = self.form.threshold_spin.value() if percent is None else percent
        sigma = self.form.profile_sigma_spin.value() if sigma is None else sigma
        # Through the saved original, not `create_relative_contrast_mask`: the spy of a test would count these calls as the tool's own.
        deviation = _REAL_DEVIATION(self._plane(), sigma_px=float(sigma))
        return raster_tools.relative_deviation_mask(deviation, threshold_fraction=percent / 100.0, side=side)

    def _tinted(self) -> np.ndarray | None:
        """The pixels the tint layer draws now, or ``None`` when it is hidden."""
        if not self.layer.item.isVisible() or self.layer.item.image is None:
            return None
        return np.asarray(self.layer.item.image)[..., 3] > 0

    def _shows(self, expected: np.ndarray) -> bool:
        return _pump_until(lambda: (tinted := self._tinted()) is not None and np.array_equal(tinted, expected))

    def _wheel(self, delta: int = 120) -> None:
        event = QWheelEvent(
            QPointF(5.0, 5.0), QPointF(5.0, 5.0), QPoint(0, 0), QPoint(0, delta), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        QtWidgets.QApplication.sendEvent(self.button, event)

    def _box(self, top: int, bottom: int, left: int, right: int) -> np.ndarray:
        box = np.zeros(SHAPE, dtype=bool)
        box[top:bottom, left:right] = True
        return box

    # -- the live tint -----------------------------------------------------------------------------------------------

    def test_nothing_is_tinted_until_the_threshold_tool_is_armed(self) -> None:
        _pump(0.4)
        self.assertIsNone(self._tinted())
        self._arm()
        self.assertTrue(self._shows(self._expected("brighter")), "the pixels a click would select, Brighter being the default")
        self.assertTrue(self._tinted()[_disk(*BRIGHT)].any() and not self._tinted()[_disk(*DARK)].any())

    def test_the_tint_follows_the_threshold_without_blurring_again_and_a_new_sigma_blurs_once(self) -> None:
        calls: list[int] = []
        real = raster_tools.relative_deviation

        def spy(image, *, sigma_px):
            calls.append(1)
            return real(image, sigma_px=sigma_px)

        with patch.object(raster_tools, "relative_deviation", spy):
            self._arm()
            self.assertTrue(self._shows(self._expected("brighter")))
            self.assertEqual(len(calls), 1, "one blur for the first picture")
            for percent in (35.0, 12.0, 60.0):
                self.form.threshold_spin.setValue(percent)
                self.assertTrue(self._shows(self._expected("brighter")), f"{percent} %")
            self.assertEqual(len(calls), 1, "dragging the threshold costs a compare, not a blur")
            self.form.profile_sigma_spin.setValue(SIGMA + 12)
            self.assertTrue(self._shows(self._expected("brighter")))
            self.assertEqual(len(calls), 2, "a new sigma is a new blur")

    def test_the_old_tint_stays_while_a_new_sigma_computes(self) -> None:
        self._arm()
        before = self._expected("brighter")
        self.assertTrue(self._shows(before))
        self.form.profile_sigma_spin.setValue(SIGMA + 17)  # a blur not made yet
        self.assertTrue(np.array_equal(self._tinted(), before), "no blank flash: the previous picture stays until the new one arrives")
        self.assertTrue(self._shows(self._expected("brighter")))

    def test_switching_the_side_flips_the_tint_and_costs_no_new_blur(self) -> None:
        calls: list[int] = []
        real = raster_tools.relative_deviation

        def spy(image, *, sigma_px):
            calls.append(1)
            return real(image, sigma_px=sigma_px)

        with patch.object(raster_tools, "relative_deviation", spy):
            self._arm()
            self.assertTrue(self._shows(self._expected("brighter")))
            self.form.set_threshold_side("darker")
            self.assertTrue(self._shows(self._expected("darker")))
            self.assertTrue(self._tinted()[_disk(*DARK)].any() and not self._tinted()[_disk(*BRIGHT)].any(), "the dark disk now")
            self.assertEqual(len(calls), 1, "the blur is shared by both sides")

    def test_leaving_the_tool_removes_the_tint_and_arming_it_again_brings_it_back(self) -> None:
        self._arm()
        self.assertTrue(self._shows(self._expected("brighter")))
        self.button.click()  # a second click leaves the tool
        self.assertTrue(_pump_until(lambda: self._tinted() is None), "no tint without the tool")
        self._arm()
        self.assertTrue(self._shows(self._expected("brighter")), "re-armed with the very same numbers: shown again, not skipped as 'already asked'")

    def test_no_other_tool_tints(self) -> None:
        self._arm()
        self.assertTrue(self._shows(self._expected("brighter")))
        for mode in (AreaSelectionMode.CONTRAST, AreaSelectionMode.HISTOGRAM, AreaSelectionMode.WAND):
            self.toolbar.tool_button(mode).click()
            self.assertTrue(_pump_until(lambda: self._tinted() is None), mode)
        self.assertFalse(self.area.has_selection(), "arming tools selects nothing")

    def test_the_tint_is_cut_to_the_lock_but_not_to_the_selection(self) -> None:
        self._arm()
        free = self._expected("brighter")
        self.assertTrue(self._shows(free))
        # A selection without a lock does not fence the tool: the tint is still the whole candidate.
        pick = self._box(30, 50, 40, 50)  # the left part of the bright disk (columns 42 to 58): the lock below cuts it in two
        self.area.combine_region(pick, REPLACE)
        _pump(0.3)
        self.assertTrue(np.array_equal(self._tinted(), free), "a selection is what the tool is about to change, not its fence")
        # A lock is the fence.
        self.toolbar.lock_button.click()
        self.assertTrue(self._shows(free & pick))
        self.assertTrue(free[~pick].any(), "the lock really cut something")

    def test_a_click_selects_exactly_what_was_tinted_on_either_side(self) -> None:
        self._arm()
        for side in ("brighter", "darker"):
            with self.subTest(side=side):
                self.area.clear()
                self.form.set_threshold_side(side)
                self.assertTrue(self._shows(self._expected(side)))
                tinted = self._tinted().copy()
                scene = self.panel._plot.vb.mapViewToScene(QPointF(10.5, 10.5))

                class _Click:
                    def scenePos(self) -> QPointF:  # noqa: N802
                        return scene

                    def button(self) -> Qt.MouseButton:
                        return Qt.MouseButton.LeftButton

                    def modifiers(self) -> Qt.KeyboardModifier:
                        return Qt.KeyboardModifier.NoModifier

                self.panel._on_scene_clicked(_Click())
                self.assertTrue(_pump_until(lambda: not self.selector.is_busy()))
                _pump(0.05)
                np.testing.assert_array_equal(self.area.selected_mask(SHAPE), tinted)

    def test_play_and_another_frame_hide_the_tint_and_they_come_back(self) -> None:
        self._arm()
        expected = self._expected("brighter")
        self.assertTrue(self._shows(expected))
        self.preview.set_playing(True)
        self.assertTrue(_pump_until(lambda: self._tinted() is None), "no tint for frames that fly past")
        self.preview.set_playing(False)
        self.assertTrue(self._shows(expected))
        self.preview.set_frame((0, 999.0))  # another frame than the one the pixels describe
        self.assertTrue(_pump_until(lambda: self._tinted() is None), "the tint describes the frame it was made for")
        self.preview.set_frame(FRAME)
        self.assertTrue(self._shows(expected))

    # -- the wheel, the icon, the form, the memory ---------------------------------------------------------------------

    def test_the_wheel_over_the_button_switches_brighter_and_darker(self) -> None:
        self.assertEqual(self.form.threshold_side(), "brighter")
        self._wheel(120)
        self.assertEqual(self.form.threshold_side(), "darker")
        self.assertIn("Darker", self.button.toolTip().split("\n")[0], "the tool tip names the side")
        self._wheel(-120)
        self.assertEqual(self.form.threshold_side(), "brighter")
        self.assertIn("Brighter", self.button.toolTip().split("\n")[0])

    def test_the_wheel_flips_a_live_tint_too(self) -> None:
        self._arm()
        self.assertTrue(self._shows(self._expected("brighter")))
        self._wheel()
        self.assertTrue(self._shows(self._expected("darker")))

    def test_the_icon_colours_the_part_over_the_line_for_brighter_and_under_it_for_darker(self) -> None:
        green, muted = "#22c55e", "#8b95a3"
        for darker, over, under in ((False, green, muted), (True, muted, green)):
            with self.subTest(darker=darker):
                image = icons.threshold_icon(darker).pixmap(48, 48).toImage()
                # The middle bar spans design x 11..14, y 5..20; the line is at y = 11. Points well off the line and the bar's edges.
                self.assertTrue(_close(image.pixelColor(25, 14), over), f"over the line: {image.pixelColor(25, 14).name()}")
                self.assertTrue(_close(image.pixelColor(25, 32), under), f"under the line: {image.pixelColor(25, 32).name()}")
        self.assertNotEqual(icons.threshold_icon(False).pixmap(48, 48).toImage(), icons.threshold_icon(True).pixmap(48, 48).toImage())

    def test_the_button_wears_the_icon_of_the_picked_side_and_keeps_its_frame_when_armed(self) -> None:
        def picture(state: QIcon.State) -> object:
            return self.button.icon().pixmap(48, 48, QIcon.Mode.Normal, state).toImage()

        brighter_off = picture(QIcon.State.Off)
        self.assertEqual(brighter_off, icons.threshold_icon(False).pixmap(48, 48).toImage(), "unchanged for the default side")
        self._wheel()
        darker_off = picture(QIcon.State.Off)
        self.assertEqual(darker_off, icons.threshold_icon(True).pixmap(48, 48).toImage())
        self.assertNotEqual(darker_off, brighter_off)
        self.assertNotEqual(picture(QIcon.State.On), darker_off, "the armed tool still wears its frame")

    def test_the_form_has_the_same_switch_as_two_icon_buttons_and_the_two_stay_in_step(self) -> None:
        buttons = self.form.side_buttons
        self.assertEqual(list(buttons), ["brighter", "darker"])
        self.assertEqual([buttons[side].isChecked() for side in buttons], [True, False])
        for side, button in buttons.items():  # the pop-up's pictures are the toolbar button's own
            self.assertEqual(button.icon().pixmap(48, 48).toImage(), icons.threshold_icon(side == "darker").pixmap(48, 48).toImage())
        buttons["darker"].click()
        self.assertEqual(self.form.threshold_side(), "darker")
        self.assertEqual([buttons[side].isChecked() for side in buttons], [False, True])
        self.assertEqual(self.button.icon().pixmap(48, 48, QIcon.Mode.Normal, QIcon.State.Off).toImage(), icons.threshold_icon(True).pixmap(48, 48).toImage())
        buttons["darker"].click()  # a click on the checked button leaves it checked
        self.assertTrue(buttons["darker"].isChecked())
        self.assertEqual(self.form.threshold_side(), "darker")
        self._wheel()
        self.assertEqual([buttons[side].isChecked() for side in buttons], [True, False], "the wheel moves the form's switch")

    def test_the_side_is_remembered(self) -> None:
        store = UiStateStore({"selection/threshold_side": "darker"})
        self.toolbar.bind_ui_state(store)
        self.assertEqual(self.form.threshold_side(), "darker", "restored")
        self.assertEqual(self.button.icon().pixmap(48, 48, QIcon.Mode.Normal, QIcon.State.Off).toImage(), icons.threshold_icon(True).pixmap(48, 48).toImage())
        self._wheel()
        self.assertEqual(store.get("selection/threshold_side"), "brighter", "and saved when it changes")

    def test_the_undo_step_and_the_status_say_which_side_was_selected(self) -> None:
        self._arm()
        self.form.set_threshold_side("darker")
        self.assertTrue(self._shows(self._expected("darker")))
        scene = self.panel._plot.vb.mapViewToScene(QPointF(10.5, 10.5))

        class _Click:
            def scenePos(self) -> QPointF:  # noqa: N802
                return scene

            def button(self) -> Qt.MouseButton:
                return Qt.MouseButton.LeftButton

            def modifiers(self) -> Qt.KeyboardModifier:
                return Qt.KeyboardModifier.NoModifier

        self.panel._on_scene_clicked(_Click())
        self.assertTrue(_pump_until(lambda: not self.selector.is_busy()))
        self.assertEqual(undo_manager.undo_label, "Select by threshold (darker)")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
