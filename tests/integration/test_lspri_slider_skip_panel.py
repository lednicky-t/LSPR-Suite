"""Scrolling the Cube and wavelength sliders by hand obeys the slideshow's Skip settings (2026-11-21, maintainer request).

A real Image panel over a 6-cube x 5-wavelength dataset (0 nm = the dark frame, 500 ... 650 nm). Wheel, key and mouse events are built by
hand and sent to the real sliders; frames are ignored through the real `FrameRolesModule`. What is checked: a step jumps over skipped
frames, a press or drag lands on the nearest frame that is not skipped, the wheel past the last allowed frame goes on into the next cube,
a click on a mark of the ROI row still lands exactly, and with both Skip settings off every frame is visited. One process per file.

Rewrite-only; skipped when the `apps/LSPRi/eva` submodule is not on its `rewrite` branch.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent, QWheelEvent
from PyQt6.QtTest import QTest

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
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.data_axis_slider import axis_x
    from lspr_imaging_app.panels.image.slideshow import SlideshowSettings
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.selection.frame_roles_module import FrameRolesModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

_WAVELENGTHS = (0.0, 500.0, 550.0, 600.0, 650.0)  # slider indexes 0 ... 4; 0 nm is the dark frame
_CUBES = 6


def _pump(seconds: float = 0.1) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    rng = np.random.default_rng(5)
    for cube in range(_CUBES):
        for wavelength in _WAVELENGTHS:
            path = root / f"c{cube}_w{int(wavelength)}.tif"
            tifffile.imwrite(str(path), rng.uniform(100.0, 200.0, size=(16, 20)).astype(np.float32))
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


class SliderSkipPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.selection = SelectionModule()
        self.reference = ReferenceFrameModule()
        self.roles = FrameRolesModule(self.dataset, self.reference)
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(),
            RoiToolbox(), self.selection, ActiveToolModule(), self.reference, HighlightRangeModule(),
            mask_scope=MaskScopeModule(), frame_roles=self.roles,
        )
        self.panel.resize(900, 700)
        self.panel.show()
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump(0.4)
        self.cube_slider = self.panel._cube_slider
        self.wavelength_slider = self.panel._wavelength_slider

    def tearDown(self) -> None:
        self.panel._slideshow.stop()
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()
        undo_manager.clear()

    # -- helpers -------------------------------------------------------------------------------------

    def show(self, cube: int, wavelength: float) -> None:
        self.selection.set_cube(cube)
        self.selection.set_wavelength(wavelength)
        _pump()

    def frame(self) -> tuple[int, float]:
        return int(self.selection.current_cube()), float(self.selection.current_wavelength())

    def wheel(self, slider, delta: int) -> None:
        x = float(slider.width()) / 2.0
        event = QWheelEvent(
            QPointF(x, 5.0), QPointF(x, 5.0), QPoint(0, 0), QPoint(0, delta),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
        )
        QtWidgets.QApplication.sendEvent(slider, event)
        _pump()

    def key(self, slider, key: Qt.Key) -> None:
        QTest.keyClick(slider, key)
        _pump()

    def mouse(self, slider, kind: QEvent.Type, index: int) -> None:
        count = slider.maximum() - slider.minimum() + 1
        x = axis_x(index, count, slider.width(), slider.axis_window().view_for(count))
        buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
        event = QMouseEvent(
            kind, QPointF(x, 5.0), QPointF(slider.mapToGlobal(QPoint(int(x), 5))),
            Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier,
        )
        QtWidgets.QApplication.sendEvent(slider, event)
        _pump(0.03)

    def press(self, slider, index: int) -> None:
        self.mouse(slider, QEvent.Type.MouseButtonPress, index)
        self.mouse(slider, QEvent.Type.MouseButtonRelease, index)

    def ignore_wavelengths(self, *wavelengths: float) -> None:
        for wavelength in wavelengths:
            self.roles.set_wavelength_ignored(wavelength, True)

    def ignore_cubes(self, *cubes: int) -> None:
        for cube in cubes:
            self.roles.set_cube_ignored(cube, True)

    # -- the wavelength slider: steps ---------------------------------------------------------------------

    def test_the_wheel_steps_over_ignored_wavelengths_in_both_directions(self) -> None:
        self.ignore_wavelengths(550.0, 600.0)
        self.show(0, 500.0)
        self.wheel(self.wavelength_slider, 120)
        self.assertEqual(self.frame(), (0, 650.0))  # 550 and 600 are left out
        self.assertEqual(self.wavelength_slider.value(), 4)  # and the slider shows where it went
        self.wheel(self.wavelength_slider, -120)
        self.assertEqual(self.frame(), (0, 500.0))

    def test_the_arrow_keys_home_and_end_obey_it_too(self) -> None:
        self.ignore_wavelengths(550.0, 600.0)
        self.show(0, 500.0)
        self.key(self.wavelength_slider, Qt.Key.Key_Right)
        self.assertEqual(self.frame(), (0, 650.0))
        self.key(self.wavelength_slider, Qt.Key.Key_Left)
        self.assertEqual(self.frame(), (0, 500.0))
        self.key(self.wavelength_slider, Qt.Key.Key_End)
        self.assertEqual(self.frame(), (0, 650.0))
        self.key(self.wavelength_slider, Qt.Key.Key_Home)  # 0 nm is the dark frame: not skipped by default
        self.assertEqual(self.frame(), (0, 0.0))

    def test_the_wheel_on_the_last_allowed_wavelength_goes_on_into_the_next_cube(self) -> None:
        self.ignore_wavelengths(650.0)  # the last tick is skipped, so 600 nm is the end
        self.show(0, 600.0)
        self.wheel(self.wavelength_slider, 120)
        self.assertEqual(self.frame(), (1, 0.0))  # the first frame of the next cube (the dark frame is not skipped by default)

    def make_zero_the_dark_frame(self) -> None:
        """What the session restore does in the app (`FrameRolesModule` does not pick the dark frame itself on a load)."""
        self.roles.set_dark(0.0)
        self.assertEqual(self.roles.role_of(1, 0.0), "dark")

    def test_with_skip_dark_on_the_wheel_down_from_the_first_wavelength_crosses_into_the_previous_cube(self) -> None:
        self.make_zero_the_dark_frame()
        self.panel._slideshow.set_settings(SlideshowSettings(skip_ignored=True, skip_dark=True))
        self.show(1, 500.0)
        self.wheel(self.wavelength_slider, -120)  # 0 nm lies below, skipped: that is the end of this cube
        self.assertEqual(self.frame(), (0, 650.0))

    # -- the wavelength slider: the pointer ---------------------------------------------------------------

    def test_a_press_on_an_ignored_wavelength_lands_on_the_nearest_allowed_one(self) -> None:
        self.ignore_wavelengths(550.0, 600.0)
        self.show(0, 650.0)
        self.press(self.wavelength_slider, 2)  # 550: 500 is one tick away, 650 two
        self.assertEqual(self.frame(), (0, 500.0))
        self.press(self.wavelength_slider, 3)  # 600: 650 is one tick away, 500 two
        self.assertEqual(self.frame(), (0, 650.0))
        self.assertEqual(self.wavelength_slider.value(), 4)

    def test_a_drag_across_ignored_wavelengths_never_shows_one(self) -> None:
        self.ignore_wavelengths(550.0, 600.0)
        self.show(0, 0.0)
        shown = []
        self.mouse(self.wavelength_slider, QEvent.Type.MouseButtonPress, 0)
        for index in (1, 2, 3, 4):
            self.mouse(self.wavelength_slider, QEvent.Type.MouseMove, index)
            shown.append(self.frame()[1])
        self.mouse(self.wavelength_slider, QEvent.Type.MouseButtonRelease, 4)
        self.assertEqual(shown, [500.0, 500.0, 650.0, 650.0])
        self.assertTrue(all(wavelength not in (550.0, 600.0) for wavelength in shown))

    # -- the cube slider -----------------------------------------------------------------------------------

    def test_the_wheel_on_the_cube_slider_steps_over_ignored_cubes(self) -> None:
        self.ignore_cubes(2, 3)
        self.show(1, 500.0)
        self.wheel(self.cube_slider, 120)
        self.assertEqual(self.frame(), (4, 500.0))
        self.wheel(self.cube_slider, -120)
        self.assertEqual(self.frame(), (1, 500.0))

    def test_a_press_on_an_ignored_cube_lands_on_the_nearest_allowed_one(self) -> None:
        self.ignore_cubes(2, 3)
        self.show(0, 500.0)
        self.press(self.cube_slider, 2)  # cube 1 is one tick away, cube 4 two
        self.assertEqual(self.frame()[0], 1)
        self.press(self.cube_slider, 3)  # cube 4 is one tick away, cube 1 two
        self.assertEqual(self.frame()[0], 4)

    def test_the_cube_slider_still_moves_while_the_dark_frame_is_shown_with_skip_dark_on(self) -> None:
        self.make_zero_the_dark_frame()
        self.panel._slideshow.set_settings(SlideshowSettings(skip_ignored=True, skip_dark=True))
        self.show(2, 0.0)  # every cube's frame at 0 nm is skipped: the user is on it on purpose
        self.wheel(self.cube_slider, 120)
        self.assertEqual(self.frame(), (3, 0.0))

    # -- what is not filtered ------------------------------------------------------------------------------

    def test_a_click_on_a_mark_of_the_roi_row_lands_exactly_even_on_an_ignored_cube(self) -> None:
        self.ignore_cubes(2)
        self.show(0, 500.0)
        self.panel._roi_strip.index_requested.emit(2)  # what a click on a mark sends: wired straight to the slider's setValue
        _pump()
        self.assertEqual(self.frame()[0], 2)

    def test_setting_the_value_from_code_is_not_filtered(self) -> None:
        self.ignore_wavelengths(550.0)
        self.show(0, 500.0)
        self.wavelength_slider.setValue(2)  # "Go to reference" and the like
        _pump()
        self.assertEqual(self.frame(), (0, 550.0))

    def test_with_both_skip_settings_off_every_frame_is_visited(self) -> None:
        self.ignore_wavelengths(550.0)
        self.ignore_cubes(2)
        self.panel._slideshow.set_settings(SlideshowSettings(skip_ignored=False, skip_dark=False))
        self.show(0, 500.0)
        self.wheel(self.wavelength_slider, 120)
        self.assertEqual(self.frame(), (0, 550.0))
        self.show(1, 500.0)
        self.wheel(self.cube_slider, 120)
        self.assertEqual(self.frame()[0], 2)
        self.press(self.cube_slider, 2)
        self.assertEqual(self.frame()[0], 2)


if __name__ == "__main__":
    unittest.main()
