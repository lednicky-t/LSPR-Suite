"""`AxisWindow` (the zoom of the Cube / wavelength sliders and the ROI row), the window-aware axis maths, and the labels
chosen for the visible stretch. No widgets painted; Qt is only imported for the QObject and the wheel events.

Design: apps/LSPRi/eva/docs/roi_layout_row_2026-10-16.md, step 4.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image.data_axis_slider import AxisWindow, axis_index, axis_track, axis_x
    from lspr_imaging_app.panels.image.slider_ticks import cube_slider_major_ticks, major_indices, wavelength_slider_major_ticks
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

WIDTH = 400  # widget width in these tests; the axis runs along 372 px of it


def _window(count: int = 101) -> AxisWindow:
    window = AxisWindow()
    window.set_count(count)
    return window


def _wheel(delta_y: int = 0, delta_x: int = 0, modifiers=Qt.KeyboardModifier.NoModifier, x: float = 200.0) -> QWheelEvent:
    return QWheelEvent(
        QPointF(x, 5.0), QPointF(x, 5.0), QPoint(0, 0), QPoint(delta_x, delta_y),
        Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False,
    )


class WindowStateTest(unittest.TestCase):
    def test_a_new_axis_shows_everything(self) -> None:
        window = _window()
        self.assertEqual(window.view(), (0.0, 100.0))
        self.assertFalse(window.is_zoomed())

    def test_the_window_of_another_tick_count_is_not_used(self) -> None:
        window = _window(101)
        window.zoom(4.0)
        self.assertEqual(window.view_for(50), (0.0, 49.0))
        self.assertEqual(window.view_for(101), window.view())

    def test_a_different_tick_count_resets_to_the_full_axis_and_the_same_one_keeps_the_zoom(self) -> None:
        window = _window(101)
        window.zoom(4.0)
        changes: list[int] = []
        window.changed.connect(lambda: changes.append(1))
        window.set_count(101)
        self.assertTrue(window.is_zoomed())
        self.assertEqual(changes, [])
        window.set_count(60)
        self.assertEqual((window.view(), window.is_zoomed(), len(changes)), ((0.0, 59.0), False, 1))

    def test_zoom_keeps_the_tick_under_the_pointer_where_it_is(self) -> None:
        window = _window(101)
        window.zoom(2.0, 0.25)  # the tick a quarter of the way along is 25
        lo, hi = window.view()
        self.assertAlmostEqual(lo + 0.25 * (hi - lo), 25.0)
        self.assertAlmostEqual(hi - lo, 50.0)
        centre_before = sum(window.view()) / 2.0  # [12.5, 62.5] -> 37.5
        window.zoom(2.0, 0.5)
        lo, hi = window.view()
        self.assertAlmostEqual((lo + hi) / 2.0, centre_before)  # zooming on the middle keeps the middle
        self.assertAlmostEqual(hi - lo, 25.0)

    def test_the_deepest_zoom_shows_about_five_ticks(self) -> None:
        window = _window(101)
        window.zoom(1000.0, 0.5)
        lo, hi = window.view()
        self.assertAlmostEqual(hi - lo, AxisWindow.MIN_VISIBLE_TICKS - 1.0)

    def test_a_short_axis_can_not_be_zoomed_beyond_itself(self) -> None:
        window = _window(3)
        window.zoom(10.0)
        self.assertEqual(window.view(), (0.0, 2.0))

    def test_zooming_out_stops_at_the_whole_axis(self) -> None:
        window = _window(101)
        window.zoom(4.0, 0.3)
        window.zoom(0.1, 0.3)
        self.assertEqual(window.view(), (0.0, 100.0))
        self.assertFalse(window.is_zoomed())

    def test_zooming_at_an_edge_stays_inside_the_axis(self) -> None:
        window = _window(101)
        window.zoom(4.0, 1.0)
        self.assertEqual(window.view(), (75.0, 100.0))
        window.zoom(4.0, 0.0)
        self.assertEqual(window.view()[0], 75.0)

    def test_pan_moves_the_window_and_stops_at_both_ends(self) -> None:
        window = _window(101)
        window.zoom(4.0, 0.0)  # [0, 25]
        window.pan(10.0)
        self.assertEqual(window.view(), (10.0, 35.0))
        window.pan(1000.0)
        self.assertEqual(window.view(), (75.0, 100.0))
        window.pan(-1000.0)
        self.assertEqual(window.view(), (0.0, 25.0))

    def test_reset_shows_everything(self) -> None:
        window = _window(101)
        window.zoom(8.0, 0.7)
        window.reset()
        self.assertEqual(window.view(), (0.0, 100.0))

    def test_ensure_visible_does_nothing_on_the_whole_axis_or_for_a_visible_tick(self) -> None:
        window = _window(101)
        window.ensure_visible(60)
        self.assertEqual(window.view(), (0.0, 100.0))
        window.zoom(4.0, 0.0)  # [0, 25]
        window.ensure_visible(12)
        self.assertEqual(window.view(), (0.0, 25.0))

    def test_ensure_visible_pages_to_a_tick_that_left_the_window(self) -> None:
        window = _window(101)
        window.zoom(4.0, 0.0)  # [0, 25]
        window.ensure_visible(60)  # came back a tenth of the span from the left edge
        self.assertEqual(window.view(), (57.5, 82.5))
        window.ensure_visible(30)  # went back: a tenth of the span from the right edge
        self.assertEqual(window.view(), (7.5, 32.5))
        window.ensure_visible(2)
        self.assertEqual(window.view(), (0.0, 25.0))  # clamped at the start


class FollowTest(unittest.TestCase):
    """The value stays in the middle and the axis slides past it (2026-10-17, maintainer request: like rails seen from a train)."""

    def test_center_on_puts_the_tick_in_the_middle_and_keeps_the_zoom(self) -> None:
        window = _window(101)
        window.zoom(4.0, 0.0)  # [0, 25]
        window.center_on(60)
        self.assertEqual(window.view(), (47.5, 72.5))

    def test_center_on_does_nothing_on_the_whole_axis(self) -> None:
        window = _window(101)
        window.center_on(60)
        self.assertEqual(window.view(), (0.0, 100.0))

    def test_near_the_ends_the_window_stops_and_the_tick_leaves_the_middle(self) -> None:
        window = _window(101)
        window.zoom(4.0, 0.0)
        window.center_on(3)  # nothing lies before tick 0: the window cannot slide further
        self.assertEqual(window.view(), (0.0, 25.0))
        window.center_on(99)
        self.assertEqual(window.view(), (75.0, 100.0))

    def test_each_step_slides_the_window_by_one_tick_and_keeps_its_width(self) -> None:
        window = _window(101)
        window.zoom(4.0, 0.0)
        window.follow(50)
        before = window.view()
        window.follow(51)
        after = window.view()
        self.assertAlmostEqual(after[0] - before[0], 1.0)
        self.assertAlmostEqual(after[1] - after[0], before[1] - before[0])
        self.assertAlmostEqual(sum(after) / 2.0, 51.0)

    def test_following_a_tick_that_is_already_in_the_middle_announces_nothing(self) -> None:
        window = _window(101)
        window.zoom(4.0, 0.0)
        changes: list[int] = []
        window.changed.connect(lambda: changes.append(1))
        window.follow(50)
        window.follow(50)
        self.assertEqual(len(changes), 1)

    def test_a_held_pointer_stops_the_window_and_the_release_centres_on_the_last_value(self) -> None:
        window = _window(101)
        window.zoom(4.0, 0.0)  # [0, 25]
        slider, row = object(), object()
        window.hold(slider)
        window.hold(row)
        window.follow(12)
        window.follow(20)
        self.assertEqual(window.view(), (0.0, 25.0))  # stood still
        window.release(row)
        self.assertEqual(window.view(), (0.0, 25.0))  # the slider's button is still down
        window.release(slider)
        self.assertEqual(window.view(), (7.5, 32.5))  # centred on 20

    def test_a_pan_in_progress_stops_following(self) -> None:
        window = _window(101)
        window.zoom(4.0, 0.0)
        window.start_pan(100.0)
        window.follow(80)
        self.assertEqual(window.view(), (0.0, 25.0))
        window.end_pan()
        window.follow(80)
        self.assertAlmostEqual(sum(window.view()) / 2.0, 80.0)

    def test_releasing_without_having_followed_anything_changes_nothing(self) -> None:
        window = _window(101)
        window.zoom(4.0, 0.0)
        owner = object()
        window.hold(owner)
        window.release(owner)
        self.assertEqual(window.view(), (0.0, 25.0))

    def test_ensure_visible_is_still_the_gentle_paging_rule(self) -> None:
        window = _window(101)
        window.zoom(4.0, 0.0)
        window.ensure_visible(12)  # on screen: nothing moves
        self.assertEqual(window.view(), (0.0, 25.0))


class WheelGestureTest(unittest.TestCase):
    def test_ctrl_wheel_zooms_in_around_the_pointer_and_wheel_down_zooms_out(self) -> None:
        window = _window(101)
        x_quarter = axis_track(WIDTH)[0] + 0.25 * axis_track(WIDTH)[1]
        self.assertTrue(window.handle_wheel(_wheel(120, modifiers=Qt.KeyboardModifier.ControlModifier, x=x_quarter), WIDTH))
        lo, hi = window.view()
        self.assertAlmostEqual(hi - lo, 100.0 / AxisWindow.ZOOM_PER_NOTCH)
        self.assertAlmostEqual(lo + 0.25 * (hi - lo), 25.0)  # the tick under the pointer stayed
        window.handle_wheel(_wheel(-120, modifiers=Qt.KeyboardModifier.ControlModifier, x=x_quarter), WIDTH)
        self.assertEqual(window.view(), (0.0, 100.0))

    def test_a_touchpad_sends_small_deltas_and_zooms_smoothly(self) -> None:
        window = _window(101)
        window.handle_wheel(_wheel(12, modifiers=Qt.KeyboardModifier.ControlModifier), WIDTH)  # a tenth of a notch
        lo, hi = window.view()
        self.assertLess(hi - lo, 100.0)
        self.assertGreater(hi - lo, 95.0)

    def test_shift_wheel_pans_wheel_up_towards_the_first_cube(self) -> None:
        window = _window(101)
        window.zoom(4.0, 0.5)
        before = window.view()
        window.handle_wheel(_wheel(120, modifiers=Qt.KeyboardModifier.ShiftModifier), WIDTH)
        after_up = window.view()
        self.assertLess(after_up[0], before[0])
        window.handle_wheel(_wheel(-240, modifiers=Qt.KeyboardModifier.ShiftModifier), WIDTH)
        self.assertGreater(window.view()[0], before[0])

    def test_a_side_wheel_pans_right_for_positive_horizontal_scroll(self) -> None:
        window = _window(101)
        window.zoom(4.0, 0.5)
        before = window.view()
        self.assertTrue(window.handle_wheel(_wheel(delta_x=120), WIDTH))
        self.assertGreater(window.view()[0], before[0])
        self.assertAlmostEqual(window.view()[1] - window.view()[0], before[1] - before[0])

    def test_a_plain_vertical_wheel_is_not_a_gesture_of_the_window(self) -> None:
        window = _window(101)
        self.assertFalse(window.handle_wheel(_wheel(120), WIDTH))
        self.assertFalse(window.handle_wheel(_wheel(120, modifiers=Qt.KeyboardModifier.AltModifier), WIDTH))
        self.assertEqual(window.view(), (0.0, 100.0))

    def test_an_axis_with_fewer_than_two_ticks_ignores_everything(self) -> None:
        window = _window(1)
        self.assertFalse(window.handle_wheel(_wheel(120, modifiers=Qt.KeyboardModifier.ControlModifier), WIDTH))


class PanDragTest(unittest.TestCase):
    def test_the_axis_follows_the_pointer_like_a_sheet_of_paper(self) -> None:
        window = _window(101)
        window.zoom(5.0, 0.5)  # a span of 20 ticks
        lo, hi = window.view()
        track_width = axis_track(WIDTH)[1]
        window.start_pan(100.0)
        self.assertTrue(window.is_panning())
        self.assertTrue(window.drag_pan(100.0 + 0.1 * track_width, WIDTH))  # dragged right by a tenth of the axis
        self.assertAlmostEqual(window.view()[0], lo - 2.0)  # so the window moved left by a tenth of its span
        window.drag_pan(100.0, WIDTH)  # back where it began: the same view, however many moves it took
        self.assertAlmostEqual(window.view()[0], lo)
        window.end_pan()
        self.assertFalse(window.is_panning())
        self.assertFalse(window.drag_pan(300.0, WIDTH))


class AxisMathTest(unittest.TestCase):
    def test_the_default_view_is_the_whole_axis(self) -> None:
        left, width = axis_track(WIDTH)
        self.assertEqual(axis_x(0, 11, WIDTH), left)
        self.assertEqual(axis_x(10, 11, WIDTH), left + width)
        self.assertEqual(axis_x(5, 11, WIDTH), axis_x(5, 11, WIDTH, (0.0, 10.0)))

    def test_a_zoomed_view_stretches_its_ticks_over_the_whole_track(self) -> None:
        left, width = axis_track(WIDTH)
        view = (20.0, 40.0)
        self.assertAlmostEqual(axis_x(20, 101, WIDTH, view), left)
        self.assertAlmostEqual(axis_x(40, 101, WIDTH, view), left + width)
        self.assertAlmostEqual(axis_x(30, 101, WIDTH, view), left + width / 2)
        self.assertLess(axis_x(10, 101, WIDTH, view), left)  # off to the left: outside the track
        self.assertGreater(axis_x(60, 101, WIDTH, view), left + width)

    def test_axis_index_is_the_inverse_of_axis_x_in_every_view(self) -> None:
        for view in (None, (0.0, 100.0), (20.0, 40.0), (57.5, 82.5), (96.0, 100.0)):
            for index in range(101):
                lo, hi = view if view is not None else (0.0, 100.0)
                if lo <= index <= hi:
                    self.assertEqual(axis_index(axis_x(index, 101, WIDTH, view), 101, WIDTH, view), index)

    def test_axis_index_clamps_to_the_ticks_that_exist(self) -> None:
        self.assertEqual(axis_index(-1000.0, 101, WIDTH, (20.0, 40.0)), 20)
        self.assertEqual(axis_index(5000.0, 101, WIDTH, (20.0, 40.0)), 40)
        self.assertEqual(axis_index(5000.0, 101, WIDTH), 100)


class VisibleLabelsTest(unittest.TestCase):
    def test_the_whole_axis_is_labelled_as_before(self) -> None:
        self.assertEqual(list(major_indices(101)), list(range(0, 101, 20)))
        self.assertEqual(list(major_indices(101, (0.0, 100.0))), list(range(0, 101, 20)))
        self.assertEqual(list(major_indices(1)), [])

    def test_a_zoomed_axis_is_labelled_for_what_is_on_screen(self) -> None:
        self.assertEqual(list(major_indices(101, (20.0, 40.0))), [20, 25, 30, 35, 40])
        self.assertEqual(list(major_indices(101, (50.0, 54.0))), [50, 51, 52, 53, 54])  # a few ticks: every one

    def test_labels_do_not_move_while_the_window_is_panned(self) -> None:
        a = set(major_indices(101, (20.0, 40.0)))
        b = set(major_indices(101, (22.5, 42.5)))
        self.assertEqual(a & b, {25, 30, 35, 40})  # the same ticks keep their labels; only the edges change

    def test_cube_labels_use_the_visible_stretch_and_the_names(self) -> None:
        values = tuple(range(100, 201))
        labels = cube_slider_major_ticks(values, lambda cube: f"c{cube}", (20.0, 40.0))
        self.assertEqual(labels, {20: "c120", 25: "c125", 30: "c130", 35: "c135", 40: "c140"})

    def test_wavelength_labels_follow_the_view_too(self) -> None:
        values = tuple(400.0 + 5.0 * i for i in range(101))
        labels = wavelength_slider_major_ticks(values, (20.0, 40.0))
        self.assertEqual(list(labels), [20, 25, 30, 35, 40])
        self.assertEqual(labels[20], "500")


if __name__ == "__main__":
    unittest.main()
