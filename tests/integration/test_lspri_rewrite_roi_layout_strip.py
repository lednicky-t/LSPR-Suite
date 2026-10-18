"""The ROI row under the Cube slider (``TimelineStrip``) and its ribbon toggles in the Image panel.

A real panel over a six-cube dataset. The row marks where the ROI *layout* changes: ``>`` persistent (and always at the
first cube), a plain line individual. Design: apps/LSPRi/eva/docs/roi_layout_row_2026-10-16.md. One process per file.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from dataclasses import replace
from unittest import mock

import numpy as np
import tifffile
from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QContextMenuEvent, QWheelEvent
from PyQt6.QtWidgets import QMenu

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
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
    from lspr_imaging_app.panels.image import ImagePanel, roi_layout_menu
    from lspr_imaging_app.panels.image.data_axis_slider import axis_index, axis_x
    from lspr_imaging_app.panels.image.timeline_strip import KIND_MIXED, StripMark, governing_mark
    from lspr_imaging_app.panels.image.slideshow import MODE_ALL, MODE_ONE_WAVELENGTH, MODE_ROI_STATES
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.model import SCOPE_INDIVIDUAL, SCOPE_PERSISTENT
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_image_panel import _pump
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

_CUBES = 6


def _write_cubes(root: Path) -> ImageDataset:
    records = []
    rng = np.random.default_rng(5)
    for cube in range(_CUBES):
        for wavelength in (500.0, 550.0):
            path = root / f"c{cube}_w{int(wavelength)}.tif"
            tifffile.imwrite(str(path), rng.uniform(100.0, 200.0, size=(64, 80)).astype(np.float32))
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


def _marks(*specs: tuple[int, str]) -> list[StripMark]:
    return [StripMark(index, kind, f"mark {index}") for index, kind in specs]


class GoverningMarkTest(unittest.TestCase):
    def test_an_individual_mark_on_the_cube_governs_it(self) -> None:
        marks = _marks((0, SCOPE_PERSISTENT), (3, SCOPE_INDIVIDUAL), (5, SCOPE_PERSISTENT))
        self.assertEqual([governing_mark(marks, c) for c in (0, 2, 3, 4, 5, 9)], [0, 0, 1, 0, 2, 2])

    def test_no_cursor_or_no_mark_before_it_governs_nothing(self) -> None:
        self.assertIsNone(governing_mark(_marks((0, SCOPE_PERSISTENT)), None))
        self.assertIsNone(governing_mark(_marks((2, SCOPE_PERSISTENT)), 1))
        self.assertIsNone(governing_mark(_marks((2, SCOPE_INDIVIDUAL)), 3))  # an individual mark is its own cube only


class MixedMarkTest(unittest.TestCase):
    """The Mask row under the Cube slider: a cube with a persistent mask AND individual masks is a filled ">"."""

    def test_a_mixed_mark_holds_from_its_cube_on_like_a_persistent_one(self) -> None:
        marks = _marks((0, SCOPE_PERSISTENT), (3, KIND_MIXED), (5, SCOPE_INDIVIDUAL))
        self.assertEqual([governing_mark(marks, c) for c in (2, 3, 4, 5, 6)], [0, 1, 1, 2, 1])

    def test_a_mixed_mark_is_a_filled_chevron_a_persistent_one_is_only_an_outline(self) -> None:
        from lspr_imaging_app.panels.image.timeline_strip import TimelineStrip

        strip = TimelineStrip()
        strip.resize(400, 14)
        strip.set_axis_count(11)
        strip.set_color("#ff0000")
        strip.set_marks([StripMark(3, SCOPE_PERSISTENT, ""), StripMark(8, KIND_MIXED, "")])
        image = strip.grab().toImage()
        ratio = image.devicePixelRatio()

        def pixel(x: float, y: int):
            return image.pixelColor(int(x * ratio), int(y * ratio))

        def distance(a, b) -> int:
            return abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())

        background = pixel(2, 8)
        # a pixel well inside the chevron (about 1 px right of the mark, below the rail line at y = 7)
        inside_outline = pixel(int(axis_x(3, 11, 400)) + 1, 8)
        inside_filled = pixel(int(axis_x(8, 11, 400)) + 1, 8)
        self.assertGreater(distance(inside_filled, background), 150)  # painted in the mark's colour
        self.assertLess(distance(inside_outline, background), 0.3 * distance(inside_filled, background))  # only an outline


class AxisMappingTest(unittest.TestCase):
    def test_axis_index_is_the_inverse_of_axis_x(self) -> None:
        for count in (2, 7, 314):
            for index in range(count):
                self.assertEqual(axis_index(axis_x(index, count, 900), count, 900), index)

    def test_clicks_outside_the_axis_clamp_to_the_ends(self) -> None:
        self.assertEqual((axis_index(-50.0, 10, 400), axis_index(5000.0, 10, 400)), (0, 9))


class RoiLayoutRowTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset_model = _write_cubes(Path(self._tmp.name))
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
        self.dataset.load_dataset(self.dataset_model)
        _pump()
        for x, y in ((20.0, 20.0), (40.0, 30.0), (60.0, 45.0)):
            self.roi_toolbox.add_roi(x, y, sample_diameter_px=10.0)
        _pump(0.3)
        undo_manager.clear()
        self.strip = self.panel._roi_strip
        self.ribbon = self.panel._slider_ribbon

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()
        undo_manager.clear()

    def marks(self) -> dict[int, StripMark]:
        return {mark.index: mark for mark in self.strip.marks()}

    # -- the start layout -------------------------------------------------------------------------

    def test_the_first_cube_always_has_a_persistent_mark_for_the_start_layout(self) -> None:
        marks = self.marks()
        self.assertEqual(list(marks), [0])
        self.assertEqual(marks[0].kind, SCOPE_PERSISTENT)
        self.assertIn("start layout", marks[0].tip)
        self.assertIn("3 ROIs", marks[0].tip)

    def test_with_no_rois_there_is_nothing_to_lay_out(self) -> None:
        self.roi_toolbox.delete_rois((1, 2, 3))
        _pump(0.3)
        self.assertEqual(self.marks(), {})

    # -- marks follow the ROI edits ------------------------------------------------------------------

    def test_a_persistent_edit_and_an_individual_edit_each_get_their_own_kind_of_mark(self) -> None:
        self.roi_toolbox.move_roi(1, 25.0, 20.0, cube=2, scope=SCOPE_PERSISTENT)
        self.roi_toolbox.translate_rois([2, 3], 1.0, 0.0, cube=4, scope=SCOPE_INDIVIDUAL)
        _pump(0.3)
        marks = self.marks()
        self.assertEqual({i: m.kind for i, m in marks.items()}, {0: SCOPE_PERSISTENT, 2: SCOPE_PERSISTENT, 4: SCOPE_INDIVIDUAL})
        self.assertIn("1 ROI changed", marks[2].tip)
        self.assertIn("holds from here on", marks[2].tip)
        self.assertIn("2 ROIs changed", marks[4].tip)
        self.assertIn("this cube only", marks[4].tip)

    def test_undo_takes_the_mark_away(self) -> None:
        self.roi_toolbox.move_roi(1, 25.0, 20.0, cube=2)
        _pump(0.3)
        self.assertIn(2, self.marks())
        undo_manager.undo()
        _pump(0.3)
        self.assertEqual(list(self.marks()), [0])

    def test_a_burst_of_edits_makes_one_update(self) -> None:
        updates: list[int] = []
        original = self.strip.set_marks
        self.strip.set_marks = lambda marks: (updates.append(len(marks)), original(marks))[1]  # type: ignore[method-assign]
        for step in range(1, 6):  # like the steps of one drag
            self.roi_toolbox.move_roi(1, 20.0 + step, 20.0, cube=3)
        _pump(0.4)
        self.assertEqual(updates, [2])

    # -- only the selected ROIs ----------------------------------------------------------------------

    def test_only_selected_rois_counts_just_their_changes(self) -> None:
        self.roi_toolbox.move_roi(1, 25.0, 20.0, cube=2, scope=SCOPE_PERSISTENT)
        self.roi_toolbox.move_roi(2, 45.0, 30.0, cube=4, scope=SCOPE_PERSISTENT)
        self.selection.set_roi_selection({2})
        _pump(0.3)
        self.assertEqual(list(self.marks()), [0, 2, 4])  # everything, until the filter is on
        self.ribbon.roi_selected_button.click()
        marks = self.marks()
        self.assertEqual(list(marks), [0, 4])
        self.assertIn("1 selected ROI changed", marks[4].tip)
        self.assertIn("1 selected ROI", marks[0].tip)
        self.selection.set_roi_selection({1, 2})  # the filter follows the selection
        _pump(0.3)
        self.assertEqual(list(self.marks()), [0, 2, 4])
        self.selection.set_roi_selection(set())
        _pump(0.3)
        self.assertEqual(list(self.marks()), [0])  # the start layout stays: it is the base of every ROI

    # -- the cube cursor, clicks, tooltips --------------------------------------------------------------

    def test_the_hairline_follows_the_cube_shown(self) -> None:
        self.selection.set_cube(3)
        _pump(0.2)
        self.assertEqual(self.strip._cursor, 3)

    def test_a_click_on_the_row_moves_the_cube_slider_and_the_cube(self) -> None:
        self.strip.index_requested.emit(4)
        _pump(0.2)
        self.assertEqual(self.panel._cube_slider.value(), 4)
        self.assertEqual(int(self.selection.current_cube()), 4)

    def test_the_tooltip_belongs_to_the_mark_under_the_pointer(self) -> None:
        self.roi_toolbox.move_roi(1, 25.0, 20.0, cube=2)
        _pump(0.3)
        x = axis_x(2, _CUBES, self.strip.width())
        self.assertEqual(self.strip.mark_near(x + 2.0).index, 2)
        self.assertIsNone(self.strip.mark_near(axis_x(3, _CUBES, self.strip.width())))

    def test_the_row_lines_up_with_the_cube_slider(self) -> None:
        slider, strip = self.panel._cube_slider, self.strip
        self.assertEqual(slider.width(), strip.width())
        self.assertEqual(slider.mapTo(self.panel, QPoint(0, 0)).x(), strip.mapTo(self.panel, QPoint(0, 0)).x())
        self.assertEqual(self.panel._roi_row_spacer.width(), self.panel._cube_spin.width())

    # -- ribbon: show / hide, colour, remembering -------------------------------------------------------------

    def test_the_eye_button_shows_and_hides_the_row(self) -> None:
        self.assertFalse(self.panel._roi_row.isHidden())
        self.ribbon.roi_row_button.click()
        self.assertTrue(self.panel._roi_row.isHidden())
        self.ribbon.roi_row_button.click()
        self.assertFalse(self.panel._roi_row.isHidden())
        self.assertEqual(list(self.marks()), [0])

    def test_the_marks_are_the_themes_blue_and_there_is_no_colour_control_for_them(self) -> None:
        from lspr_ui import get_active_theme

        self.assertEqual(self.strip._color, get_active_theme().accent_blue)
        self.panel._on_slider_color_changed("ignored", "#ff0000")  # another slider colour changing leaves the row alone
        self.assertEqual(self.strip._color, get_active_theme().accent_blue)
        for role in ("roi", "mask"):
            with self.assertRaises(ValueError):
                self.panel._slider_colors.set(role, "#ff0000")  # these roles are gone: the rows under the sliders have fixed colours

    def test_the_toggles_are_remembered_without_saving_what_was_just_read(self) -> None:
        saved: list[dict] = []
        store = UiStateStore({"image/roi_row_visible": False, "image/roi_row_selected_only": True}, on_changed=saved.append)
        self.panel.restore_ui_state(store)
        self.assertTrue(self.panel._roi_row.isHidden())
        self.assertTrue(self.ribbon.roi_selected_button.isChecked())
        self.assertFalse(self.ribbon.roi_row_button.isChecked())
        store.flush()
        self.assertEqual(saved, [], "restoring must not save what it just read")
        self.ribbon.roi_row_button.click()
        store.flush()
        self.assertTrue(saved[-1]["image/roi_row_visible"])

    # -- stepping: Previous / Next, the wheel, Play -----------------------------------------------------------

    def make_states(self) -> None:
        """Marks at cube 2 (persistent, ROI 1) and cube 4 (individual, ROIs 2 and 3); the start layout at cube 0."""
        self.roi_toolbox.move_roi(1, 25.0, 20.0, cube=2, scope=SCOPE_PERSISTENT)
        self.roi_toolbox.translate_rois([2, 3], 1.0, 0.0, cube=4, scope=SCOPE_INDIVIDUAL)
        _pump(0.3)

    def set_mode(self, mode: str) -> None:
        self.ribbon.set_settings(replace(self.ribbon.settings(), mode=mode), emit=True)

    def frame(self) -> tuple[int, float]:
        return int(self.selection.current_cube()), float(self.selection.current_wavelength())

    def go_to(self, cube: int, wavelength: float = 550.0) -> None:
        self.selection.set_cube(cube)
        self.selection.set_wavelength(wavelength)

    def wheel(self, delta: int, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        event = QWheelEvent(
            QPointF(40.0, 5.0), QPointF(40.0, 5.0), QPoint(0, 0), QPoint(0, delta),
            Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False,
        )
        QtWidgets.QApplication.sendEvent(self.strip, event)

    def test_manual_steps_follow_what_play_steps_through(self) -> None:
        step = self.panel._slideshow.step
        self.go_to(0, 500.0)
        step(1)  # every wavelength of every cube: the next one is the next wavelength
        self.assertEqual(self.frame(), (0, 550.0))
        step(-1)
        self.assertEqual(self.frame(), (0, 500.0))
        self.set_mode(MODE_ONE_WAVELENGTH)
        step(1)  # one wavelength: the next cube
        self.assertEqual(self.frame(), (1, 500.0))
        step(-1)
        self.assertEqual(self.frame(), (0, 500.0))
        step(-1)  # the first cube: it does not wrap
        self.assertEqual(self.frame(), (0, 500.0))

    def test_in_roi_states_mode_manual_steps_hop_between_the_marks_on_one_wavelength(self) -> None:
        self.make_states()
        self.set_mode(MODE_ROI_STATES)
        self.go_to(0)
        steps = []
        for direction in (1, 1, 1, -1, -1, -1):
            self.panel._slideshow.step(direction)
            steps.append(self.frame())
        self.assertEqual(steps, [(2, 550.0), (4, 550.0), (4, 550.0), (2, 550.0), (0, 550.0), (0, 550.0)])  # no wrapping at the ends

    def test_a_cube_between_two_marks_steps_to_the_nearest_one_on_each_side(self) -> None:
        self.make_states()
        self.set_mode(MODE_ROI_STATES)
        self.go_to(3)
        self.panel._slideshow.step(1)
        self.assertEqual(self.frame()[0], 4)
        self.go_to(3)
        self.panel._slideshow.step(-1)
        self.assertEqual(self.frame()[0], 2)

    def test_the_wheel_over_the_row_hops_between_marks_whatever_play_steps_through(self) -> None:
        self.make_states()
        self.assertEqual(self.ribbon.settings().mode, MODE_ALL)
        self.go_to(0)
        self.wheel(120)  # wheel up = the next mark, like the sliders
        self.assertEqual(self.frame(), (2, 550.0))
        self.wheel(120)
        self.assertEqual(self.frame(), (4, 550.0))
        self.wheel(-120)
        self.assertEqual(self.frame(), (2, 550.0))

    def test_a_wheel_notch_is_120_units_a_touchpad_adds_up_and_modifiers_are_left_for_zoom(self) -> None:
        self.make_states()
        self.go_to(0)
        self.wheel(60)
        self.assertEqual(self.frame()[0], 0)  # half a notch: not yet
        self.wheel(60)
        self.assertEqual(self.frame()[0], 2)
        self.wheel(120, Qt.KeyboardModifier.ControlModifier)
        self.wheel(120, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.frame()[0], 2)  # Ctrl / Shift + wheel are for zooming / panning later

    def test_the_wheel_does_nothing_without_any_roi(self) -> None:
        self.roi_toolbox.delete_rois((1, 2, 3))
        _pump(0.3)
        self.go_to(1)
        self.wheel(120)
        self.assertEqual(self.frame()[0], 1)

    def test_only_selected_rois_also_decides_where_the_steps_go(self) -> None:
        self.make_states()
        self.set_mode(MODE_ROI_STATES)
        self.ribbon.roi_selected_button.click()
        self.selection.set_roi_selection({1})  # ROI 1 changed at cube 2 only
        self.go_to(0)
        self.panel._slideshow.step(1)
        self.assertEqual(self.frame()[0], 2)
        self.panel._slideshow.step(1)
        self.assertEqual(self.frame()[0], 2)  # no more marks for ROI 1
        self.selection.set_roi_selection({2})  # ROI 2: cube 4
        self.go_to(0)
        self.panel._slideshow.step(1)
        self.assertEqual(self.frame()[0], 4)

    def test_play_in_roi_states_mode_visits_only_the_marks(self) -> None:
        self.make_states()
        self.set_mode(MODE_ROI_STATES)
        self.go_to(0)
        self.panel._slideshow.play()
        try:
            self.assertEqual(self.panel._slideshow.upcoming_frames(3), [(2, 550.0), (4, 550.0)])
        finally:
            self.panel._slideshow.stop()

    def test_roi_states_is_a_choice_of_the_mode_menu_and_the_button_says_so(self) -> None:
        button = self.ribbon._mode_button
        button._actions[MODE_ROI_STATES].trigger()  # the menu entry
        self.assertEqual(self.ribbon.settings().mode, MODE_ROI_STATES)
        self.assertTrue(button._actions[MODE_ROI_STATES].isChecked())
        self.assertFalse(button._actions[MODE_ALL].isChecked())
        self.assertFalse(self.ribbon._settings_panel._end_cube.isEnabled())  # the End setting only means something with every wavelength shown
        self.assertIn("ROI layout change", button.toolTip())
        button._actions[MODE_ONE_WAVELENGTH].trigger()
        self.assertIn("Cubes", button.toolTip())
        self.assertNotIn("ROI layout", button.toolTip())

    def test_the_stepping_choice_is_remembered_including_roi_states(self) -> None:
        saved: list[dict] = []
        store = UiStateStore({"image/slideshow_mode": "roi_states"}, on_changed=saved.append)
        self.panel.restore_ui_state(store)
        self.assertEqual(self.ribbon.settings().mode, MODE_ROI_STATES)
        self.assertEqual(self.panel._slideshow.settings().mode, MODE_ROI_STATES)

    # -- right click on a mark ------------------------------------------------------------------------------

    def right_click_mark(self, index: int, choice: str | None):
        """A real right click on the row at the mark `index`; the menu itself is replaced by `choice`."""
        x = round(axis_x(index, _CUBES, self.strip.width()))
        with mock.patch.object(roi_layout_menu, "show_layout_point_menu", return_value=choice) as menu:
            event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(x, 5), self.strip.mapToGlobal(QPoint(x, 5)))
            QtWidgets.QApplication.sendEvent(self.strip, event)
        return menu

    def status_messages(self) -> list[str]:
        messages: list[str] = []
        self.panel.tool_status_changed.connect(messages.append)
        return messages

    def test_a_right_click_off_any_mark_opens_no_menu(self) -> None:
        self.make_states()
        with mock.patch.object(roi_layout_menu, "show_layout_point_menu") as menu:
            x = round(axis_x(3, _CUBES, self.strip.width()))  # cube 3 has no mark
            QtWidgets.QApplication.sendEvent(
                self.strip, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(x, 5), self.strip.mapToGlobal(QPoint(x, 5)))
            )
        menu.assert_not_called()

    def test_the_menu_is_told_what_the_mark_is(self) -> None:
        self.make_states()
        kwargs = self.right_click_mark(2, None).call_args.kwargs
        self.assertEqual((kwargs["kind"], kwargs["is_start"], kwargs["roi_count"], kwargs["hidden_count"]), (SCOPE_PERSISTENT, False, 1, 0))
        self.assertEqual(kwargs["cube_text"], "Cube 2")
        kwargs = self.right_click_mark(4, None).call_args.kwargs
        self.assertEqual((kwargs["kind"], kwargs["roi_count"]), (SCOPE_INDIVIDUAL, 2))
        kwargs = self.right_click_mark(0, None).call_args.kwargs  # the start layout: every ROI
        self.assertEqual((kwargs["kind"], kwargs["is_start"], kwargs["roi_count"]), (SCOPE_PERSISTENT, True, 3))

    def test_the_menu_counts_the_rois_the_filter_hides(self) -> None:
        self.make_states()
        self.ribbon.roi_selected_button.click()
        self.selection.set_roi_selection({2})  # the individual point at cube 4 has ROIs 2 and 3
        _pump(0.3)
        kwargs = self.right_click_mark(4, None).call_args.kwargs
        self.assertEqual((kwargs["roi_count"], kwargs["hidden_count"]), (2, 1))  # the action still takes the whole point

    def test_delete_removes_the_whole_point_says_so_and_undo_brings_it_back(self) -> None:
        self.make_states()
        messages = self.status_messages()
        self.right_click_mark(4, roi_layout_menu.DELETE)
        _pump(0.3)
        self.assertEqual(list(self.marks()), [0, 2])
        self.assertTrue(messages and "deleted" in messages[-1] and "2 ROIs" in messages[-1], messages)
        undo_manager.undo()
        _pump(0.3)
        self.assertEqual(list(self.marks()), [0, 2, 4])

    def test_make_individual_and_make_persistent_flip_the_kind_of_the_point(self) -> None:
        self.make_states()
        messages = self.status_messages()
        self.right_click_mark(2, roi_layout_menu.MAKE_INDIVIDUAL)
        _pump(0.3)
        self.assertEqual(self.marks()[2].kind, SCOPE_INDIVIDUAL)
        self.assertIn("individual", messages[-1])
        self.right_click_mark(4, roi_layout_menu.MAKE_PERSISTENT)
        _pump(0.3)
        self.assertEqual(self.marks()[4].kind, SCOPE_PERSISTENT)
        self.assertIn("persistent", messages[-1])

    def test_the_start_layout_cannot_be_changed_even_if_the_menu_said_so(self) -> None:
        self.make_states()
        for choice in (roi_layout_menu.DELETE, roi_layout_menu.MAKE_INDIVIDUAL, roi_layout_menu.MAKE_PERSISTENT):
            self.right_click_mark(0, choice)
        _pump(0.3)
        self.assertEqual(list(self.marks()), [0, 2, 4])
        self.assertFalse(self.panel._roi_toolbox.layout_point_kind(0))  # no timeline entry was written at the first cube

    def test_go_to_this_cube_moves_the_cube_slider(self) -> None:
        self.make_states()
        self.right_click_mark(4, roi_layout_menu.GO_TO)
        _pump(0.2)
        self.assertEqual(int(self.selection.current_cube()), 4)

    def test_the_menu_text_names_the_rois_and_greys_the_start_layout(self) -> None:
        seen: list[list[tuple[str, bool]]] = []

        class SpyMenu(QMenu):
            def exec(self, *_args):  # record what would be shown instead of opening a modal menu
                seen.append([(action.text(), action.isEnabled()) for action in self.actions() if action.text()])
                return None

        with mock.patch.object(roi_layout_menu, "QMenu", SpyMenu):
            roi_layout_menu.show_layout_point_menu(self.panel, cube_text="Cube 2", kind=SCOPE_PERSISTENT, is_start=False, roi_count=3, hidden_count=1)
            roi_layout_menu.show_layout_point_menu(self.panel, cube_text="Cube 4", kind=SCOPE_INDIVIDUAL, is_start=False, roi_count=1)
            roi_layout_menu.show_layout_point_menu(self.panel, cube_text="Cube 0", kind=SCOPE_PERSISTENT, is_start=True, roi_count=3)
        persistent, individual, start = seen
        self.assertEqual(persistent[0], ("Cube 2 - persistent", False))  # the header is not clickable
        self.assertIn(("Make individual (3 ROIs, 1 not selected)", True), persistent)
        self.assertIn(("Delete this change (3 ROIs, 1 not selected)", True), persistent)
        self.assertIn(("Make persistent (1 ROI)", True), individual)
        self.assertEqual(start[0], ("Cube 0 - start layout", False))
        self.assertIn(("Go to this cube", True), start)  # the menu never opens with nothing to click
        self.assertIn(("Make individual (3 ROIs)", False), start)
        self.assertIn(("Delete this change (3 ROIs)", False), start)

    def test_the_rois_and_mask_sections_have_just_their_toggles_and_no_colour_swatches(self) -> None:
        from lspr_imaging_app.panels.image.slider_colors import ColorSwatch

        roi_section = self.ribbon.roi_row_button.parentWidget()
        self.assertIs(self.ribbon.roi_selected_button.parentWidget(), roi_section)
        mask_section = self.ribbon.mask_rows_button.parentWidget()
        self.assertIsNot(mask_section, roi_section)
        for section in (roi_section, mask_section):
            self.assertEqual(section.findChildren(ColorSwatch), [])
        for role in ("roi", "mask"):
            with self.assertRaises(KeyError):
                self.ribbon.slider_colors.swatch(role)


if __name__ == "__main__":
    unittest.main()
