"""Zoom on the Cube slider and the ROI row in the Image panel (step 4 of the ROI layout row work, 2026-10-16).

A real panel over a 40-cube dataset; wheel and mouse events are built by hand. The Cube slider and the ROI row share one
`AxisWindow`, so they always show the same cubes; the wavelength slider has its own. One process per file.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile
from PyQt6 import QtWidgets
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent, QWheelEvent

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
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
    from lspr_imaging_app.panels.image.data_axis_slider import axis_track, axis_x
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.model import SCOPE_PERSISTENT
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_image_panel import _pump
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_CUBES = 40
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


def _write_cubes(root: Path) -> ImageDataset:
    records = []
    rng = np.random.default_rng(7)
    for cube in range(_CUBES):
        path = root / f"c{cube}.tif"
        tifffile.imwrite(str(path), rng.uniform(100.0, 200.0, size=(16, 20)).astype(np.float32))
        records.append(ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


class AxisZoomTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.roi_toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), self.roi_toolbox,
            self.selection, ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), analysis_running=lambda: False,
        )
        self.panel.resize(900, 700)
        self.panel.show()
        self.dataset.load_dataset(_write_cubes(Path(self._tmp.name)))
        _pump()
        self.roi_toolbox.add_roi(10.0, 10.0, sample_diameter_px=4.0)
        self.roi_toolbox.move_roi(1, 12.0, 10.0, cube=30, scope=SCOPE_PERSISTENT)  # a mark at cube 30
        _pump(0.3)
        undo_manager.clear()
        self.selection.set_wavelength(500.0)  # the app does this on load; a bare panel has to be told (ROI-states steps keep it)
        self.slider = self.panel._cube_slider
        self.strip = self.panel._roi_strip
        self.window = self.slider.axis_window()

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()
        undo_manager.clear()

    # -- helpers -----------------------------------------------------------------------------------

    def send_wheel(self, widget, delta: int, modifiers=Qt.KeyboardModifier.NoModifier, x: float | None = None) -> None:
        x = float(widget.width()) / 2.0 if x is None else x
        event = QWheelEvent(
            QPointF(x, 5.0), QPointF(x, 5.0), QPoint(0, 0), QPoint(0, delta),
            Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False,
        )
        QtWidgets.QApplication.sendEvent(widget, event)

    def send_mouse(self, widget, kind: QEvent.Type, x: float, button=Qt.MouseButton.MiddleButton, buttons=None) -> None:
        if buttons is None:
            buttons = button if kind != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
        event = QMouseEvent(kind, QPointF(x, 5.0), QPointF(widget.mapToGlobal(QPoint(int(x), 5))), button, buttons, Qt.KeyboardModifier.NoModifier)
        QtWidgets.QApplication.sendEvent(widget, event)

    def visible_labels(self) -> dict[int, str]:
        return dict(self.slider._major_labels)

    # -- the shared window ---------------------------------------------------------------------------

    def test_the_cube_slider_and_the_roi_row_share_one_window_and_the_wavelength_slider_has_its_own(self) -> None:
        self.assertIs(self.strip._window, self.window)
        self.assertIsNot(self.panel._wavelength_slider.axis_window(), self.window)
        self.assertEqual(self.window.count(), _CUBES)

    def test_ctrl_wheel_on_the_slider_zooms_both_and_a_mark_stays_under_its_tick(self) -> None:
        self.send_wheel(self.slider, 120, CTRL, x=axis_track(self.slider.width())[0] + 0.75 * axis_track(self.slider.width())[1])
        self.send_wheel(self.slider, 120, CTRL, x=axis_track(self.slider.width())[0] + 0.75 * axis_track(self.slider.width())[1])
        self.assertTrue(self.window.is_zoomed())
        self.assertEqual(self.strip._view(), self.window.view())
        lo, hi = self.window.view()
        self.assertLess(hi - lo, _CUBES - 1.0)
        # cube 30 has a mark: it is on screen and the row finds it exactly where the slider draws that tick
        self.assertTrue(lo <= 30 <= hi)
        x = axis_x(30, _CUBES, self.slider.width(), self.window.view())
        self.assertEqual(self.strip.mark_near(x).index, 30)

    def test_ctrl_wheel_on_the_roi_row_zooms_the_same_window(self) -> None:
        self.send_wheel(self.strip, 120, CTRL)
        self.assertTrue(self.window.is_zoomed())

    def test_a_plain_wheel_still_steps_the_slider_and_hops_the_row(self) -> None:
        self.selection.set_cube(5)
        _pump(0.1)
        self.send_wheel(self.slider, -120)  # a QSlider's own wheel: down = lower
        self.assertFalse(self.window.is_zoomed())
        self.assertLess(int(self.selection.current_cube()), 5)
        self.selection.set_cube(5)
        _pump(0.1)
        self.send_wheel(self.strip, 120)  # the row: up = the next mark
        self.assertEqual(int(self.selection.current_cube()), 30)

    def test_shift_wheel_and_a_side_wheel_pan_on_both(self) -> None:
        self.send_wheel(self.slider, 360, CTRL, x=axis_track(self.slider.width())[0])
        self.assertEqual(self.window.view()[0], 0.0)
        before = self.window.view()
        self.send_wheel(self.strip, -120, SHIFT)  # wheel down: later cubes
        after = self.window.view()
        self.assertGreater(after[0], before[0])
        self.assertAlmostEqual(after[1] - after[0], before[1] - before[0])

    def test_middle_drag_on_the_slider_pans_and_a_double_click_shows_everything(self) -> None:
        self.send_wheel(self.slider, 360, CTRL, x=axis_track(self.slider.width())[0])  # zoomed onto the start
        lo_before = self.window.view()[0]
        self.send_mouse(self.slider, QEvent.Type.MouseButtonPress, 300.0)
        self.send_mouse(self.slider, QEvent.Type.MouseMove, 200.0)  # dragged left: later cubes come into view
        self.send_mouse(self.slider, QEvent.Type.MouseButtonRelease, 200.0)
        self.assertGreater(self.window.view()[0], lo_before)
        self.assertFalse(self.window.is_panning())
        self.send_mouse(self.slider, QEvent.Type.MouseButtonDblClick, 200.0)
        self.assertFalse(self.window.is_zoomed())

    def test_middle_drag_and_double_click_work_on_the_roi_row_too(self) -> None:
        self.send_wheel(self.strip, 360, CTRL, x=axis_track(self.strip.width())[0])
        lo_before = self.window.view()[0]
        self.send_mouse(self.strip, QEvent.Type.MouseButtonPress, 300.0)
        self.send_mouse(self.strip, QEvent.Type.MouseMove, 150.0)
        self.send_mouse(self.strip, QEvent.Type.MouseButtonRelease, 150.0)
        self.assertGreater(self.window.view()[0], lo_before)
        self.send_mouse(self.strip, QEvent.Type.MouseButtonDblClick, 150.0)
        self.assertFalse(self.window.is_zoomed())

    # -- labels, clicks, following the cube ---------------------------------------------------------------

    def test_zooming_in_labels_the_ticks_that_are_on_screen_more_densely(self) -> None:
        full = self.visible_labels()
        self.assertEqual(sorted(full), list(range(0, _CUBES, 5)))  # 40 cubes: every fifth
        self.window.zoom(5.0, 0.0)  # about 8 cubes on screen
        zoomed = self.visible_labels()
        lo, hi = self.window.view()
        self.assertTrue(zoomed and all(lo - 1e-9 <= index <= hi + 1e-9 for index in zoomed))
        self.assertGreaterEqual(len(zoomed), 7)  # almost every visible tick has its number
        self.assertEqual(zoomed[3], "3")
        self.window.reset()
        self.assertEqual(self.visible_labels(), full)

    def test_a_click_on_the_zoomed_slider_picks_the_cube_under_the_pointer(self) -> None:
        self.window.zoom(4.0, 1.0)  # the last quarter: roughly cubes 30 to 39
        lo, hi = self.window.view()
        x = axis_x(35, _CUBES, self.slider.width(), self.window.view())
        self.send_mouse(self.slider, QEvent.Type.MouseButtonPress, x, Qt.MouseButton.LeftButton)
        self.assertEqual(self.slider.value(), 35)
        _pump(0.1)
        self.assertEqual(int(self.selection.current_cube()), 35)

    def test_a_click_on_the_zoomed_roi_row_picks_the_cube_too(self) -> None:
        self.window.zoom(4.0, 1.0)
        x = axis_x(33, _CUBES, self.strip.width(), self.window.view())
        self.send_mouse(self.strip, QEvent.Type.MouseButtonPress, x, Qt.MouseButton.LeftButton)
        _pump(0.1)
        self.assertEqual(int(self.selection.current_cube()), 33)

    def test_the_window_follows_the_cube_when_it_leaves_the_screen(self) -> None:
        self.window.zoom(4.0, 0.0)  # [0, ~10]
        self.assertTrue(self.window.view()[1] < 20)
        self.selection.set_cube(25)  # as Play does, or the spin box: a programmatic change
        _pump(0.1)
        lo, hi = self.window.view()
        self.assertTrue(lo <= 25 <= hi, (lo, hi))
        self.selection.set_cube(1)
        _pump(0.1)
        lo, hi = self.window.view()
        self.assertTrue(lo <= 1 <= hi, (lo, hi))

    # -- the cube stays in the middle, the rails move (2026-10-17, maintainer request) -------------------------

    def handle_x(self, cube: int, widget=None) -> float:
        widget = widget or self.slider
        return axis_x(cube, _CUBES, widget.width(), self.window.view())

    def test_the_cube_stays_in_the_middle_and_the_row_with_its_marks_slides_past(self) -> None:
        self.window.zoom(4.0, 0.0)
        self.selection.set_cube(27)  # about cubes 22 to 32 are on screen: the row's mark at cube 30 among them
        _pump(0.1)
        self.assertAlmostEqual(self.handle_x(27), self.slider.width() / 2.0)
        mark_x = self.handle_x(30, self.strip)
        self.assertEqual(self.strip.mark_near(mark_x).index, 30)
        self.selection.set_cube(28)
        _pump(0.1)
        self.assertAlmostEqual(self.handle_x(28), self.slider.width() / 2.0)  # the handle did not move ...
        moved_mark_x = self.handle_x(30, self.strip)
        self.assertLess(moved_mark_x, mark_x)  # ... the rails did
        self.assertEqual(self.strip.mark_near(moved_mark_x).index, 30)  # and the row's mark moved with its tick
        self.assertEqual(self.strip._view(), self.window.view())

    def test_play_steps_keep_the_cube_in_the_middle(self) -> None:
        from lspr_imaging_app.panels.image.slideshow import MODE_ALL

        self.window.zoom(4.0, 0.0)
        self.selection.set_cube(15)
        _pump(0.1)
        for expected in range(16, 23):
            self.assertTrue(self.panel._slideshow.step(1, MODE_ALL))
            _pump(0.05)
            self.assertEqual(int(self.selection.current_cube()), expected)
            self.assertAlmostEqual(self.handle_x(expected), self.slider.width() / 2.0)

    def test_near_the_ends_of_the_dataset_the_handle_runs_to_the_end_of_the_rail(self) -> None:
        self.window.zoom(4.0, 0.5)
        self.selection.set_cube(_CUBES - 1)
        _pump(0.1)
        self.assertEqual(self.window.view()[1], _CUBES - 1.0)
        self.assertGreater(self.handle_x(_CUBES - 1), self.slider.width() / 2.0)

    def test_scrubbing_along_the_row_does_not_run_away_and_centres_when_the_button_is_up(self) -> None:
        self.window.zoom(4.0, 0.5)
        view = self.window.view()
        press_x, move_x = axis_x(17, _CUBES, self.strip.width(), view), axis_x(21, _CUBES, self.strip.width(), view)
        self.send_mouse(self.strip, QEvent.Type.MouseButtonPress, press_x, Qt.MouseButton.LeftButton)
        self.assertEqual(self.slider.value(), 17)
        self.send_mouse(self.strip, QEvent.Type.MouseMove, move_x, Qt.MouseButton.LeftButton)
        self.assertEqual(self.slider.value(), 21)  # the tick under the pointer, not a runaway
        self.assertEqual(self.window.view(), view)  # the rails stood still under the pointer
        self.send_mouse(self.strip, QEvent.Type.MouseButtonRelease, move_x, Qt.MouseButton.LeftButton)
        self.assertAlmostEqual(sum(self.window.view()) / 2.0, 21.0)
        _pump(0.1)
        self.assertEqual(int(self.selection.current_cube()), 21)

    def test_a_click_on_the_slider_centres_the_picked_cube_when_the_button_is_released(self) -> None:
        self.window.zoom(4.0, 1.0)  # the last ~10 cubes
        view = self.window.view()
        x = axis_x(32, _CUBES, self.slider.width(), view)  # far enough from the end for the window to be able to centre on it
        self.send_mouse(self.slider, QEvent.Type.MouseButtonPress, x, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.view(), view)
        self.send_mouse(self.slider, QEvent.Type.MouseButtonRelease, x, Qt.MouseButton.LeftButton)
        self.assertAlmostEqual(sum(self.window.view()) / 2.0, 32.0)

    def test_the_wavelength_slider_follows_with_its_own_window(self) -> None:
        wavelength_window = self.panel._wavelength_slider.axis_window()
        self.assertIsNot(wavelength_window, self.window)
        self.window.zoom(4.0, 0.0)
        self.selection.set_cube(20)
        _pump(0.1)
        self.assertAlmostEqual(sum(self.window.view()) / 2.0, 20.0)  # the Cube axis moved ...
        self.assertFalse(wavelength_window.is_zoomed())  # ... the wavelength axis is its own business

    def test_play_in_the_roi_states_mode_keeps_the_cube_on_screen(self) -> None:
        from dataclasses import replace

        from lspr_imaging_app.panels.image.slideshow import MODE_ROI_STATES

        ribbon = self.panel._slider_ribbon
        ribbon.set_settings(replace(ribbon.settings(), mode=MODE_ROI_STATES), emit=True)
        self.window.zoom(5.0, 0.0)
        self.selection.set_cube(0)
        self.panel._slideshow.step(1)  # the next mark is cube 30, far off screen
        _pump(0.1)
        lo, hi = self.window.view()
        self.assertEqual(int(self.selection.current_cube()), 30)
        self.assertTrue(lo <= 30 <= hi, (lo, hi))

    def test_a_new_dataset_shows_the_whole_axis_again(self) -> None:
        self.window.zoom(5.0, 0.5)
        self.assertTrue(self.window.is_zoomed())
        self.panel._refresh_navigation_ranges()  # what loading or clearing a dataset does
        self.assertFalse(self.window.is_zoomed())

    def test_a_zoomed_slider_shows_where_more_axis_lies_with_a_triangle_at_the_rail_ends(self) -> None:
        left, width = axis_track(self.slider.width())
        rail_y = 7  # the rail is drawn at y = 6 .. 9
        left_pixel, right_pixel = round(left - 6), round(left + width + 6)

        def pixels() -> tuple[int, int]:
            image = self.slider.grab().toImage()
            ratio = image.devicePixelRatio()
            return (
                image.pixel(round(left_pixel * ratio), round(rail_y * ratio)),
                image.pixel(round(right_pixel * ratio), round(rail_y * ratio)),
            )

        whole = pixels()
        self.window.zoom(4.0, 0.5)  # more to the left and to the right
        both = pixels()
        self.assertNotEqual(both[0], whole[0])
        self.assertNotEqual(both[1], whole[1])
        self.window.pan(-1000.0)  # at the start: nothing more to the left any more
        at_start = pixels()
        self.assertEqual(at_start[0], whole[0])
        self.assertNotEqual(at_start[1], whole[1])
        self.window.reset()
        self.assertEqual(pixels(), whole)

    def test_the_hairline_and_the_mark_hit_test_use_the_zoomed_axis(self) -> None:
        self.window.zoom(4.0, 1.0)
        self.selection.set_cube(32)
        _pump(0.1)
        self.assertEqual(self.strip._cursor, 32)
        self.assertIsNone(self.strip.mark_near(axis_x(0, _CUBES, self.strip.width(), self.window.view())))  # cube 0: no mark, off screen
        self.assertEqual(self.strip.mark_near(axis_x(30, _CUBES, self.strip.width(), self.window.view())).index, 30)


if __name__ == "__main__":
    unittest.main()
