"""Tests for the Image panel's slider ribbon (frame icon controls, Play,
slideshow settings) and the right-click menu that shares its icon widget.

A real `QApplication` built in-process, never `.exec()`; real widgets and TIFF
files; driven by button `.click()` and direct method calls. The slideshow is
stepped by calling the controller's `_step` (deterministic) except for one test
that lets the real timer run.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

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
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.frame_tools import FrameToolsWidget
    from lspr_imaging_app.panels.image.slideshow import END_CUBE, MODE_ONE_WAVELENGTH, SlideshowSettings
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.selection.frame_roles_module import FrameRolesModule
    from lspr_imaging_app.selection.reference_frame_module import MODE_MANUAL
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402
import tifffile  # noqa: E402


def _pump(seconds: float = 0.4) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _write_dataset(root: Path) -> ImageDataset:
    records = []
    rng = np.random.default_rng(5)
    for cube in (0, 1):
        for wavelength in (500.0, 550.0, 600.0):
            path = root / f"c{cube}_w{int(wavelength)}.tif"
            tifffile.imwrite(str(path), rng.uniform(100.0, 200.0, size=(32, 40)).astype(np.float32))
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
    return ImageDataset(folder=root, records=records, source_format="image_stack")


class SliderRibbonTest(unittest.TestCase):
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
        self.tools = self.panel._slider_ribbon.frame_tools
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump()
        self.selection.set_cube(0)
        self.selection.set_wavelength(550.0)

    def tearDown(self) -> None:
        self.panel._slideshow.stop()
        self.panel._renderer.stop()
        self._tmp.cleanup()

    # -- icon controls ----------------------------------------------------------

    def test_ignore_wavelength_icon_ignores_it_in_all_cubes_and_shows_it(self) -> None:
        self.tools._ignore_wl.click()
        self.assertEqual(self.roles.ignored_wavelengths(), (550.0,))
        self.assertTrue(self.tools._ignore_wl.isChecked())
        self.assertTrue(self.tools._ignore_clear.isEnabled())
        self.assertIn("550 nm", self.tools._ignore_wl.toolTip())
        self.tools._ignore_wl.click()  # toggles back
        self.assertEqual(self.roles.ignored_wavelengths(), ())

    def test_ignore_cube_and_eraser_clears_both(self) -> None:
        self.tools._ignore_cube.click()
        self.tools._ignore_wl.click()
        self.assertEqual((self.roles.ignored_cubes(), self.roles.ignored_wavelengths()), ((0,), (550.0,)))
        self.tools._ignore_clear.click()
        self.assertEqual((self.roles.ignored_cubes(), self.roles.ignored_wavelengths()), ((), ()))
        self.assertFalse(self.tools._ignore_clear.isEnabled())

    def test_clear_all_ignores_clears_other_cubes_and_wavelengths_in_one_undo(self) -> None:
        self.roles.set_cube_ignored(1, True)
        self.roles.set_wavelength_ignored(600.0, True)
        self.assertFalse(self.tools._ignore_clear.isEnabled())  # nothing ignored at the frame shown
        self.assertTrue(self.tools._ignore_clear_all.isEnabled())
        self.tools._ignore_clear_all.click()
        self.assertEqual((self.roles.ignored_cubes(), self.roles.ignored_wavelengths()), ((), ()))
        self.assertFalse(self.tools._ignore_clear_all.isEnabled())
        undo_manager.undo()
        self.assertEqual((self.roles.ignored_cubes(), self.roles.ignored_wavelengths()), ((1,), (600.0,)))

    def test_dark_pick_and_eraser(self) -> None:
        self.assertFalse(self.tools._dark_clear.isEnabled())  # no 0 nm in this dataset: no dark
        self.tools._dark_pick.click()
        self.assertEqual(self.roles.dark_wavelength(), 550.0)
        self.assertTrue(self.tools._dark_pick.isChecked())
        self.tools._dark_clear.click()
        self.assertIsNone(self.roles.dark_wavelength())

    def test_reference_icons(self) -> None:
        self.selection.set_cube(1)
        self.selection.set_wavelength(600.0)
        self.tools._set_ref.click()
        self.assertEqual(self.reference.mode(), MODE_MANUAL)
        self.assertEqual(self.reference.manual_frame(), (1, 600.0))

    def test_go_to_reference_moves_both_sliders_to_the_reference_frame(self) -> None:
        from PyQt6.QtWidgets import QToolButton

        group = self.tools._go_ref.parentWidget()
        self.assertEqual(  # the first icon of the Reference group, from the left
            group.findChildren(QToolButton)[:3], [self.tools._go_ref, self.tools._set_ref, self.tools._find_ref]
        )
        self.assertFalse(self.tools._go_ref.isEnabled())  # no reference frame yet
        self.assertIn("no reference frame", self.tools._go_ref.toolTip())
        self.selection.set_cube(1)
        self.selection.set_wavelength(600.0)
        self.tools._set_ref.click()
        self.assertTrue(self.tools._go_ref.isEnabled())
        self.assertNotIn("no reference frame", self.tools._go_ref.toolTip())
        self.selection.set_cube(0)
        self.selection.set_wavelength(500.0)
        _pump(0.1)
        self.tools._go_ref.click()
        _pump(0.1)
        self.assertEqual((self.selection.current_cube(), self.selection.current_wavelength()), (1, 600.0))
        self.assertEqual((self.panel._cube_spin.value(), self.panel._wavelength_spin.value()), (1, 600.0))
        # The right-click menu has the same icon, and it closes the menu like every other one.
        self.selection.set_cube(0)
        self.selection.set_wavelength(500.0)
        menu_tools = FrameToolsWidget(stacked=True)
        self.panel._connect_frame_tools(menu_tools)
        menu_tools.refresh(self.panel._frame_tools_state())
        closed: list[bool] = []
        menu_tools.used.connect(lambda: closed.append(True))
        menu_tools._go_ref.click()
        self.assertEqual((self.selection.current_cube(), self.selection.current_wavelength()), (1, 600.0))
        self.assertEqual(closed, [True])

    def test_dock_role_field_names_a_reference_dark_or_ignored_frame_in_its_slider_colour(self) -> None:
        colors = self.panel._slider_colors
        self.reference.set_manual_frame(1, 600.0)
        self.panel._show_frame_status(1, 600.0)
        role = self.panel.frame_columns()[0]
        self.assertEqual((role.text, role.color), ("Reference", colors.color("reference")))
        self.panel._show_frame_status(0, 500.0)
        self.assertEqual(self.panel.frame_columns()[0].text, "")  # a plain frame: the field is empty, not gone
        self.assertIsNone(self.panel.frame_columns()[0].color)
        self.roles.set_wavelength_ignored(500.0, True)
        self.panel._show_frame_status(0, 500.0)
        role = self.panel.frame_columns()[0]
        self.assertEqual((role.text, role.color), ("Ignored", colors.color("ignored")))
        self.roles.set_dark(550.0)
        self.panel._show_frame_status(0, 550.0)
        role = self.panel.frame_columns()[0]
        self.assertEqual((role.text, role.color), ("Dark background", colors.color("dark")))

    def test_unit_button_names_the_unit_a_click_switches_to(self) -> None:
        # No acquisition timing in this dataset: switching to time is not offered.
        self.assertEqual(self.tools._unit.text(), "(t)")
        self.assertFalse(self.tools._unit.isEnabled())
        self.assertIn("Needs acquisition metadata", self.tools._unit.toolTip())

    def test_every_button_has_a_tooltip(self) -> None:
        for button in self.tools._buttons():
            self.assertTrue(button.toolTip(), button)

    def test_buttons_disabled_with_no_dataset(self) -> None:
        self.dataset.clear_dataset()
        _pump(0.1)
        self.assertFalse(self.tools._ignore_wl.isEnabled())
        self.assertFalse(self.panel._slider_ribbon._play.isEnabled())

    def test_context_menu_widget_runs_the_same_commands_and_closes(self) -> None:
        menu_tools = FrameToolsWidget(stacked=True)
        self.panel._connect_frame_tools(menu_tools)
        menu_tools.refresh(self.panel._frame_tools_state())
        closed: list[bool] = []
        menu_tools.used.connect(lambda: closed.append(True))
        menu_tools._ignore_cube.click()
        self.assertEqual(self.roles.ignored_cubes(), (0,))
        self.assertEqual(closed, [True])

    # -- Find reference runs only on a click ---------------------------------------

    def test_find_reference_runs_only_when_clicked_and_stores_a_fixed_frame(self) -> None:
        from lspr_imaging_app.selection.reference_auto_task import ReferenceAutoFind

        finder = ReferenceAutoFind(self.dataset, self.selection, self.reference, self.roles)
        self.selection.set_cube(1)  # previewing cubes never starts a scan
        self.selection.set_cube(0)
        self.assertFalse(finder.is_running())
        self.assertIsNone(self.reference.manual_frame())
        self.tools._find_ref.click()
        self.assertTrue(finder.is_running())
        deadline = time.monotonic() + 10
        while finder.is_running() and time.monotonic() < deadline:
            _pump(0.05)
        frame = self.reference.manual_frame()
        self.assertIsNotNone(frame)
        self.assertEqual(frame[0], 0)
        self.assertEqual(self.reference.mode(), MODE_MANUAL)
        self.selection.set_cube(1)  # the result stays fixed while other cubes are viewed
        _pump(0.2)
        self.assertFalse(finder.is_running())
        self.assertEqual(self.reference.manual_frame(), frame)

    # -- roll up / down ---------------------------------------------------------

    def test_handle_rolls_the_ribbon_up_and_down_and_remembers(self) -> None:
        from lspr_imaging_app.panels.ui_state import UiStateStore

        drawer = self.panel._ribbon_drawer
        store = UiStateStore()
        self.panel.restore_ui_state(store)
        self.panel.show()
        _pump(0.1)
        self.assertTrue(drawer.is_expanded())
        self.assertTrue(drawer._handle.isHidden())  # no handle while the ribbon is shown
        QTest.mouseDClick(drawer.ribbon, Qt.MouseButton.LeftButton, pos=QPoint(drawer.ribbon.width() - 5, 3))
        _pump(0.4)  # the roll-up is animated
        self.assertFalse(drawer.is_expanded())
        self.assertTrue(drawer.ribbon.isHidden())
        self.assertFalse(drawer._handle.isHidden())
        self.assertLessEqual(drawer._handle.mapTo(drawer, drawer._handle.rect().topLeft()).x(), 2)  # at the left edge, not centred
        self.assertIs(store.get("image/slider_ribbon_open"), False)
        drawer._handle.click()
        _pump(0.4)
        self.assertTrue(drawer.is_expanded())
        self.assertFalse(drawer.ribbon.isHidden())
        self.assertGreater(drawer.ribbon.height(), 10)
        self.assertIs(store.get("image/slider_ribbon_open"), True)
        # A saved "rolled up" state comes back without animating.
        store.set("image/slider_ribbon_open", False)
        self.panel.restore_ui_state(store)
        self.assertFalse(drawer.is_expanded())
        self.panel.hide()

    # -- slideshow --------------------------------------------------------------

    def test_default_interval_is_half_a_second_without_timing_and_scales_with_speed(self) -> None:
        show = self.panel._slideshow
        self.assertAlmostEqual(show.interval_seconds(), 0.5)
        show.set_settings(SlideshowSettings(speed=10))
        self.assertAlmostEqual(show.interval_seconds(), 0.05)

    def test_play_steps_through_frames_and_stops_at_the_end(self) -> None:
        show = self.panel._slideshow
        self.selection.set_cube(0)
        self.selection.set_wavelength(500.0)
        show.play()
        self.assertTrue(show.is_playing())
        visited = [(self.selection.current_cube(), self.selection.current_wavelength())]
        for _ in range(10):
            show._advance()
            visited.append((self.selection.current_cube(), self.selection.current_wavelength()))
            if not show.is_playing():
                break
        self.assertEqual(visited[:6], [(0, 500.0), (0, 550.0), (0, 600.0), (1, 500.0), (1, 550.0), (1, 600.0)])
        self.assertFalse(show.is_playing())

    def test_play_at_the_end_restarts_from_the_beginning(self) -> None:
        show = self.panel._slideshow
        self.selection.set_cube(1)
        self.selection.set_wavelength(600.0)
        show.play()
        self.assertEqual((self.selection.current_cube(), self.selection.current_wavelength()), (0, 500.0))
        show.stop()

    def test_the_real_timer_advances_frames(self) -> None:
        show = self.panel._slideshow
        show.set_settings(SlideshowSettings(speed=10))
        self.selection.set_cube(0)
        self.selection.set_wavelength(500.0)
        show.play()
        _pump(0.3)
        show.stop()
        self.assertNotEqual((self.selection.current_cube(), self.selection.current_wavelength()), (0, 500.0))

    def test_fast_play_still_redraws_while_steps_keep_arriving(self) -> None:
        """The redraw timer must be a throttle while playing: a restarting debounce
        never fired at 5x+ (measured: 4 of 52 steps drawn)."""
        show = self.panel._slideshow
        shown: list[float] = []
        self.panel.image_rendered.connect(lambda _img, _cube, wl: shown.append(wl))
        show.set_settings(SlideshowSettings(speed=10, repeat=True))  # 50 ms steps
        self.selection.set_cube(0)
        self.selection.set_wavelength(500.0)
        _pump(0.3)
        shown.clear()
        show.play()
        _pump(1.0)
        show.stop()
        self.assertGreaterEqual(len(shown), 5)  # about 10-20 steps ran; with the debounce there was 1 or 0

    def test_slow_renders_are_still_shown_while_playing(self) -> None:
        """A render slower than the redraw interval is superseded on arrival every time;
        Play must show the newest arrived frame anyway (it once showed none)."""
        renderer = self.panel._renderer
        original = renderer._load_plane

        def slow(cube: int, wavelength: float):
            time.sleep(0.08)
            return original(cube, wavelength)

        renderer._load_plane = slow
        shown: list[float] = []
        self.panel.image_rendered.connect(lambda _img, _cube, wl: shown.append(wl))
        show = self.panel._slideshow
        show.set_settings(SlideshowSettings(speed=10, repeat=True))
        self.selection.set_cube(0)
        self.selection.set_wavelength(500.0)
        _pump(0.4)
        shown.clear()
        show.play()
        _pump(1.5)
        show.stop()
        _pump(0.3)
        self.assertGreaterEqual(len(shown), 4)
        self.assertGreaterEqual(len(set(shown)), 2)  # different frames, not one repeated

    def test_while_playing_the_read_ahead_follows_the_play_order_without_a_quiet_period(self) -> None:
        calls = []
        renderer = self.panel._renderer
        original = renderer.prefetch
        renderer.prefetch = lambda keys, **kw: (calls.append((list(keys), kw)), original(keys, **kw))[1]
        show = self.panel._slideshow
        self.selection.set_cube(0)
        self.selection.set_wavelength(500.0)
        self.assertEqual(show.upcoming_frames(3), [])  # not playing: nothing
        show.play()
        self.assertEqual(show.upcoming_frames(3), [(0, 550.0), (0, 600.0), (1, 500.0)])
        calls.clear()
        self.panel._request_prefetch(0, 500.0)
        keys, kwargs = calls[-1]
        self.assertEqual(keys, [(0, 550.0), (0, 600.0), (1, 500.0)])
        self.assertEqual(kwargs, {"quiet_seconds": 0.0})
        show.set_settings(SlideshowSettings(mode="one_wavelength"))
        self.assertEqual(show.upcoming_frames(3), [(1, 500.0)])  # skip cubes at 500 nm: only two cubes
        show.stop()
        calls.clear()
        self.panel._request_prefetch(0, 500.0)  # not playing: the usual neighbour order, usual quiet period
        self.assertEqual(calls[-1][1], {})
        self.assertIn((0, 550.0), calls[-1][0])

    def test_fast_roi_outlines_are_thin_only_while_playing_and_only_when_switched_on(self) -> None:
        from lspr_imaging_app.panels.image.roi_overlay import SAMPLE_WIDTH, SAMPLE_WIDTH_FAST

        circles = self.panel._roi_overlay.sample_circles
        settings_panel = self.panel._slider_ribbon._settings_panel
        show = self.panel._slideshow
        self.selection.set_cube(0)
        self.selection.set_wavelength(500.0)
        self.assertFalse(show.settings().thin_roi_outline)  # off by default: the look does not change unasked
        show.play()
        self.assertEqual(circles.width, SAMPLE_WIDTH)  # playing, option off
        settings_panel._thin_outline.click()  # switched on while playing: applies at once
        self.assertTrue(show.settings().thin_roi_outline)
        self.assertEqual(circles.width, SAMPLE_WIDTH_FAST)
        show.stop()
        self.assertEqual(circles.width, SAMPLE_WIDTH)  # back to normal when Play stops
        show.play()
        self.assertEqual(circles.width, SAMPLE_WIDTH_FAST)
        settings_panel._thin_outline.click()  # switched off while playing
        self.assertEqual(circles.width, SAMPLE_WIDTH)
        show.stop()

    def test_the_play_only_options_announce_themselves_to_the_other_panels(self) -> None:
        playing_events, calm_events = [], []
        self.panel.playback_changed.connect(playing_events.append)
        self.panel.calm_histogram_changed.connect(calm_events.append)
        settings_panel = self.panel._slider_ribbon._settings_panel
        show = self.panel._slideshow
        self.selection.set_cube(0)
        self.selection.set_wavelength(500.0)
        show.play()
        show.stop()
        self.assertEqual((playing_events, calm_events), ([True, False], []))  # option off: the histogram is left alone
        settings_panel._calm_histogram.click()
        self.assertEqual(calm_events, [])  # on, but not playing yet
        show.play()
        self.assertEqual(calm_events, [True])
        settings_panel._calm_histogram.click()  # switched off while playing
        self.assertEqual(calm_events, [True, False])
        settings_panel._calm_histogram.click()
        self.assertEqual(calm_events, [True, False, True])
        show.stop()
        self.assertEqual(calm_events, [True, False, True, False])

    def test_play_waits_for_the_previous_frame_to_be_shown(self) -> None:
        show = self.panel._slideshow
        self.selection.set_cube(0)
        self.selection.set_wavelength(500.0)
        show.play()
        show._advance()  # a step is made; its image is not on screen yet
        after_first = self.selection.current_wavelength()
        show._step()  # the timer slot: must wait
        self.assertEqual(self.selection.current_wavelength(), after_first)
        show.frame_displayed()
        show._step()
        self.assertNotEqual(self.selection.current_wavelength(), after_first)
        show.stop()

    def test_steps_keep_a_fixed_rate_even_when_a_step_itself_takes_time(self) -> None:
        """The next step is due one interval after the previous one BEGAN: a 40 ms step with a 100 ms
        interval re-arms the timer for ~60 ms, not 100 ms (which made every cycle step cost + interval)."""
        show = self.panel._slideshow
        show.interval_seconds = lambda: 0.100
        slow = self.selection.set_wavelength

        def slow_set_wavelength(value):
            time.sleep(0.04)
            slow(value)

        self.selection.set_wavelength = slow_set_wavelength
        self.selection.set_cube(0)
        slow(500.0)
        show.play()
        show._advance()
        interval = show._timer.interval()
        show.stop()
        _pump(0.2)
        self.assertTrue(40 <= interval <= 75, f"timer re-armed for {interval} ms; a step that took 40 ms of a 100 ms interval leaves ~60")
        self.assertEqual(show._timer.timerType(), Qt.TimerType.PreciseTimer)

    def test_pipelined_play_steps_inside_the_display_call_and_submits_the_render_request_at_once(self) -> None:
        show = self.panel._slideshow
        renderer = self.panel._renderer
        submitted = []
        original = renderer.submit
        renderer.submit = lambda request: (submitted.append(request.wavelength_nm), original(request))[1]
        self.selection.set_cube(0)
        self.selection.set_wavelength(500.0)
        _pump(0.3)
        show.play()
        show._advance()  # a step is made, its image not shown yet (no event loop runs before the asserts below)
        first = self.selection.current_wavelength()
        show._step()  # the timer fires while the frame is on its way: no polling, it only notes that a step is due
        self.assertEqual(self.selection.current_wavelength(), first)
        self.assertTrue(show._step_due)
        submitted.clear()
        show.frame_displayed()  # now the step runs, inside this very call ...
        self.assertNotEqual(self.selection.current_wavelength(), first)
        self.assertEqual(submitted, [self.selection.current_wavelength()])  # ... and its render request is already sent
        self.assertFalse(show._step_due)
        show.stop()
        _pump(0.2)

    def test_an_older_result_does_not_release_the_brake_while_a_newer_request_is_in_flight(self) -> None:
        from dataclasses import replace

        from lspr_imaging_app.panels.image.render import RenderResult

        show = self.panel._slideshow
        renderer = self.panel._renderer
        requests = []
        original = renderer.submit
        renderer.submit = lambda request: (requests.append(request), original(request))[1]
        acks = []
        show.frame_displayed = lambda *args: acks.append(1)
        self.selection.set_cube(0)
        self.selection.set_wavelength(500.0)
        _pump(0.3)
        show.play()
        requests.clear()
        self.panel._redraw()
        self.panel._redraw()  # two requests in flight: the second is the newest
        older, newest = requests[-2], requests[-1]
        image = np.ones((8, 8), dtype=np.float32)
        acks.clear()
        self.panel._on_rendered(RenderResult(request=replace(older), image=image))
        self.assertEqual(acks, [], "an older frame is shown, but a newer one is still coming: no release")
        self.panel._on_rendered(RenderResult(request=replace(newest), image=image))
        self.assertEqual(acks, [1], "the newest frame releases the brake")
        show.stop()

    def test_a_slow_gui_gets_fewer_steps_instead_of_dropped_ones(self) -> None:
        """Each plane read takes 150 ms (a slow disk) while Play asks for a step every 50 ms. Without
        back-pressure about two of three steps were thrown away unseen (and in the real app the screen
        fell up to 16 s behind); with it Play steps only as fast as frames can be produced."""
        renderer = self.panel._renderer
        original = renderer._load_plane

        def slow(cube: int, wavelength: float):
            time.sleep(0.15)
            return original(cube, wavelength)

        renderer._load_plane = slow
        show = self.panel._slideshow
        show.set_settings(SlideshowSettings(speed=10, repeat=True))
        self.selection.set_cube(0)
        self.selection.set_wavelength(500.0)
        _pump(0.4)
        show.play()
        _pump(2.5)
        show.stop()
        fields = self.panel._playback_stats.total_fields()
        self.assertGreaterEqual(fields["shown"], 5)
        self.assertLessEqual(fields["steps"] - fields["shown"], 2, fields)  # without back-pressure: about half the steps

    # -- overlay redraws skipped while nothing they draw has changed ----------------------

    def _count_roi_draws(self):
        calls = []
        original = self.panel._roi_overlay.draw
        self.panel._roi_overlay.draw = lambda *a, **k: (calls.append(1), original(*a, **k))[1]
        return calls

    def test_roi_overlay_is_not_redrawn_on_a_plain_wavelength_step_but_is_when_anything_changes(self) -> None:
        roi_toolbox = self.panel._roi_toolbox
        roi_id = roi_toolbox.add_roi(20.0, 15.0, sample_diameter_px=6.0)
        self.panel._redraw()
        draws = self._count_roi_draws()

        self.selection.set_wavelength(550.0)
        self.panel._redraw()
        self.selection.set_wavelength(600.0)
        self.panel._redraw()
        self.assertEqual(len(draws), 0, "a wavelength step alone must not redraw the ROI overlay")

        roi_toolbox.move_roi(roi_id, 25.0, 15.0)  # an edit
        self.panel._redraw()
        self.assertEqual(len(draws), 1)
        self.selection.set_roi_selection({roi_id})  # a selection change
        self.panel._redraw()
        self.assertEqual(len(draws), 2)
        self.selection.set_cube(1)  # another cube, but no ROI has a per-cube timeline: the circles are the same
        self.panel._redraw()
        self.assertEqual(len(draws), 2)
        roi_toolbox.move_roi(roi_id, 26.0, 15.0, cube=1, scope="persistent")  # now a ROI differs from cube 1 on
        self.panel._redraw()
        self.assertEqual(len(draws), 3)
        self.selection.set_cube(0)  # with a timeline the cube matters
        self.panel._redraw()
        self.assertEqual(len(draws), 4)
        self.panel._draw_roi_overlay()  # a forced redraw (style / label change) always draws
        self.assertEqual(len(draws), 5)
        self.panel._redraw()  # and then nothing is pending
        self.assertEqual(len(draws), 5)

    def test_roi_overlay_follows_a_chromatic_shift_that_differs_between_wavelengths(self) -> None:
        import numpy as np

        self.panel._roi_toolbox.add_roi(20.0, 15.0, sample_diameter_px=6.0)
        self.panel._redraw()
        draws = self._count_roi_draws()
        shifted = np.array([[1.0, 0.0, 3.0], [0.0, 1.0, 2.0]])
        original = self.panel._chromatic.affine_for
        self.panel._chromatic.affine_for = lambda frame: shifted if frame[1] == 600.0 else original(frame)
        self.selection.set_wavelength(600.0)
        self.panel._redraw()
        self.assertEqual(len(draws), 1)  # this frame's affine differs: redrawn at the shifted place
        self.selection.set_wavelength(500.0)
        self.panel._redraw()
        self.assertEqual(len(draws), 2)  # and back

    def test_the_real_call_sites_emit_structured_log_events(self) -> None:
        """Dataset load, plane reads, render frames, @instrumented commands and the playback
        summary all reach the log as events with fields (the Log panel / JSONL file read these)."""
        import logging

        from lspr_imaging_app.applog.handlers import RingBufferHandler

        ring = RingBufferHandler()
        root = logging.getLogger()
        old_level = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(ring)
        try:
            from lspr_imaging_app.dataset import io as dataset_io

            dataset_io._load_image_array_cached.cache_clear()  # so the next frames are real reads
            self.dataset.load_dataset(self.dataset._dataset)  # a reload: planes are read from disk again
            self.selection.set_cube(0)
            self.selection.set_wavelength(500.0)
            _pump(0.5)
            show = self.panel._slideshow
            show.set_settings(SlideshowSettings(speed=10, repeat=True))
            show.play()
            _pump(1.2)
            show.stop()
            _pump(0.2)
        finally:
            root.removeHandler(ring)
            root.setLevel(old_level)
        by_event: dict[str, list] = {}
        for entry in ring.snapshot():
            if entry.event:
                by_event.setdefault(entry.event, []).append(entry)
        self.assertIn("dataset.loaded", by_event)
        self.assertEqual(by_event["dataset.loaded"][0].fields["planes"], 6)
        self.assertIn("dataset.plane_read", by_event)
        self.assertEqual(by_event["dataset.plane_read"][0].fields["source"], "disk")
        frame = by_event["render.frame"][0]
        self.assertTrue({"cube", "wl", "load_ms", "prep_ms"} <= set(frame.fields))
        self.assertIsNotNone(frame.dur_ms)
        self.assertTrue(any(name.startswith("DatasetModule.") for name in by_event))  # @instrumented
        summary = by_event["playback.summary"][0].fields
        self.assertGreater(summary["shown"], 0)
        self.assertIn("lag_ms", summary)

    def test_playing_reports_stats_to_the_status_bar_and_a_summary_at_the_end(self) -> None:
        messages: list[str] = []
        self.panel.tool_status_changed.connect(messages.append)
        show = self.panel._slideshow
        show.set_settings(SlideshowSettings(speed=10, repeat=True))
        show.play()
        _pump(1.4)
        show.stop()
        self.assertTrue(any(m.startswith("Slideshow:") and m.endswith(" fps") for m in messages), messages)
        self.assertTrue(messages[-1].startswith("Slideshow finished:"), messages[-1])

    def test_ignored_frames_are_skipped_by_the_show(self) -> None:
        self.roles.set_wavelength_ignored(550.0, True)
        self.selection.set_cube(0)
        self.selection.set_wavelength(500.0)
        self.panel._slideshow.play()
        self.panel._slideshow._advance()
        self.assertEqual(self.selection.current_wavelength(), 600.0)

    def test_skip_icons_decide_whether_ignored_and_dark_frames_are_shown(self) -> None:
        show, panel = self.panel._slideshow, self.panel._slider_ribbon._settings_panel
        self.roles.set_wavelength_ignored(550.0, True)
        self.roles.set_dark(600.0)

        def frame_after_500_of_cube_0() -> tuple[int, float]:
            self.selection.set_cube(0)
            self.selection.set_wavelength(500.0)
            show.play()
            show._advance()
            show.stop()
            return (self.selection.current_cube(), self.selection.current_wavelength())

        settings = show.settings()
        self.assertEqual((settings.skip_ignored, settings.skip_dark), (True, False))  # the defaults
        self.assertEqual(frame_after_500_of_cube_0(), (0, 600.0))  # ignored 550 skipped, dark 600 shown
        panel._skip_ignored.click()  # now show ignored frames too
        self.assertEqual(frame_after_500_of_cube_0(), (0, 550.0))
        panel._skip_ignored.click()
        panel._skip_dark.click()  # skip both: 550 is ignored and 600 is dark, so on to cube 1
        self.assertEqual((show.settings().skip_ignored, show.settings().skip_dark), (True, True))
        self.assertEqual(frame_after_500_of_cube_0(), (1, 500.0))

    def test_play_button_toggles_and_shows_pause(self) -> None:
        ribbon = self.panel._slider_ribbon
        self.selection.set_cube(0)
        self.selection.set_wavelength(500.0)
        ribbon._play.click()
        self.assertTrue(self.panel._slideshow.is_playing())
        self.assertIn("Pause", ribbon._play.toolTip())
        ribbon._play.click()
        self.assertFalse(self.panel._slideshow.is_playing())
        self.assertIn("Play", ribbon._play.toolTip())

    def test_settings_panel_changes_reach_the_controller_and_the_store(self) -> None:
        ribbon = self.panel._slider_ribbon
        panel = ribbon._settings_panel
        saved: list[SlideshowSettings] = []
        ribbon.settings_changed.connect(saved.append)
        panel._speed.setValue(3)  # the fourth tick: 10x
        panel._mode_one.click()
        panel._repeat.click()
        settings = self.panel._slideshow.settings()
        self.assertEqual((settings.speed, settings.mode, settings.repeat), (10, MODE_ONE_WAVELENGTH, True))
        self.assertEqual(saved[-1], settings)
        self.assertEqual(panel._speed_label.text(), "10x")
        # In one-wavelength mode the End icons are greyed out; back in all-wavelengths mode they work.
        self.assertFalse(panel._end_cube.isEnabled() or panel._end_dataset.isEnabled())
        panel._mode_all.click()
        panel._end_cube.click()
        self.assertEqual(self.panel._slideshow.settings().end, END_CUBE)
        self.assertTrue(panel._end_cube.isEnabled())

    def test_settings_survive_through_the_ui_state_store(self) -> None:
        from lspr_imaging_app.panels.ui_state import UiStateStore

        store = UiStateStore()
        store.set("image/slideshow_speed", 5)
        store.set("image/slideshow_mode", "one_wavelength")
        store.set("image/slideshow_repeat", True)
        store.set("image/slideshow_end", END_CUBE)
        store.set("image/slideshow_thin_roi_outline", True)
        self.panel._restore_slideshow_settings(store)
        self.assertEqual(
            self.panel._slideshow.settings(),
            SlideshowSettings(speed=5, mode="one_wavelength", repeat=True, end=END_CUBE, thin_roi_outline=True),
        )
        self.assertEqual(self.panel._slider_ribbon.settings(), self.panel._slideshow.settings())
        store.set("image/slideshow_speed", 7)  # not a speed: a hand-edited file falls back to the defaults
        self.panel._restore_slideshow_settings(store)
        self.assertEqual(self.panel._slideshow.settings(), SlideshowSettings())

    # -- slider colours and marks (2026-10-15) -----------------------------------

    def test_reference_tick_is_one_colour_on_both_sliders(self) -> None:
        from lspr_ui import get_active_theme

        self.reference.set_manual_frame(1, 600.0)
        cube_slider, wavelength_slider = self.panel._cube_slider, self.panel._wavelength_slider
        self.assertEqual(cube_slider._reference_tick_color, wavelength_slider._reference_tick_color)
        self.assertEqual(cube_slider._reference_tick_color, get_active_theme().accent_green)

    def test_dark_tick_marks_the_dark_wavelength_on_the_wavelength_slider_only(self) -> None:
        from lspr_ui import get_active_theme

        wavelength_slider = self.panel._wavelength_slider
        self.assertIsNone(wavelength_slider._dark_tick_index)  # no dark chosen
        self.tools._dark_pick.click()  # 550 nm, the second wavelength of cube 0
        self.assertEqual(self.roles.dark_wavelength(), 550.0)
        self.assertEqual(wavelength_slider._dark_tick_index, 1)
        self.assertEqual(wavelength_slider._dark_tick_color, get_active_theme().accent_purple)
        self.assertIsNone(self.panel._cube_slider._dark_tick_index)
        self.selection.set_wavelength(600.0)  # the tick is the dark's own, not the viewed frame's
        self.assertEqual(wavelength_slider._dark_tick_index, 1)
        self.tools._dark_clear.click()
        self.assertIsNone(wavelength_slider._dark_tick_index)

    def test_a_picked_colour_recolours_the_ticks_and_reset_returns_the_default(self) -> None:
        from lspr_ui import get_active_theme

        from lspr_imaging_app.panels.hue_slider import color_for_hue

        self.reference.set_manual_frame(1, 600.0)
        self.tools._dark_pick.click()
        colors = self.panel._slider_ribbon.slider_colors
        colors.popup("dark").hue_slider.setValue(120)  # the user drags the hue slider
        self.assertEqual(self.panel._wavelength_slider._dark_tick_color, color_for_hue(120))
        colors.popup("reference").hue_slider.setValue(30)
        self.assertEqual(self.panel._cube_slider._reference_tick_color, color_for_hue(30))
        self.assertEqual(self.panel._wavelength_slider._reference_tick_color, color_for_hue(30))
        self.assertIn("Dark", colors.button("dark").toolTip())  # hovering names what the swatch colours
        colors.popup("dark").reset_button.click()
        self.assertEqual(self.panel._wavelength_slider._dark_tick_color, get_active_theme().accent_purple)
        self.assertEqual(self.panel._cube_slider._reference_tick_color, color_for_hue(30))  # the others stay

    def test_an_ignored_cube_marks_its_tick_on_the_cube_slider_only(self) -> None:
        from lspr_ui import get_active_theme

        cube_slider, wavelength_slider = self.panel._cube_slider, self.panel._wavelength_slider
        self.assertEqual(cube_slider._ignored_tick_indices, frozenset())
        self.tools._ignore_cube.click()  # cube 0 is viewed
        self.assertEqual(cube_slider._ignored_tick_indices, frozenset({0}))
        self.assertEqual(cube_slider._ignored_tick_color, get_active_theme().accent_red)
        self.assertEqual(wavelength_slider._ignored_tick_indices, frozenset())
        self.roles.set_cube_ignored(1, True)
        self.assertEqual(cube_slider._ignored_tick_indices, frozenset({0, 1}))
        self.tools._ignore_clear_all.click()
        self.assertEqual(cube_slider._ignored_tick_indices, frozenset())

    def test_an_ignored_wavelength_marks_its_tick_on_the_wavelength_slider_only(self) -> None:
        cube_slider, wavelength_slider = self.panel._cube_slider, self.panel._wavelength_slider
        self.tools._ignore_wl.click()  # 550 nm: the second wavelength of every cube here
        self.assertEqual(wavelength_slider._ignored_tick_indices, frozenset({1}))
        self.assertEqual(cube_slider._ignored_tick_indices, frozenset())
        self.selection.set_cube(1)  # ignored in every cube: the tick follows to the next cube
        self.assertEqual(wavelength_slider._ignored_tick_indices, frozenset({1}))
        self.selection.set_wavelength(600.0)  # the tick is the ignored wavelength's own, not the viewed frame's
        self.assertEqual(wavelength_slider._ignored_tick_indices, frozenset({1}))
        self.roles.set_wavelength_ignored(550.0, False)
        self.assertEqual(wavelength_slider._ignored_tick_indices, frozenset())

    def test_marks_on_one_tick_are_stacked_not_hidden(self) -> None:
        from lspr_imaging_app.panels.image.data_axis_slider import DataAxisSlider

        slider = DataAxisSlider()
        slider.set_ticks([500.0, 550.0, 600.0], {})
        slider.set_ignored_ticks(frozenset({1, 2}), "#ef4444")
        slider.set_dark_tick(0, "#a855f7")
        slider.set_reference_tick(1, "#22c55e")
        # Top to bottom: ignored, dark, reference; a tick with one mark has one colour.
        self.assertEqual(
            slider._mark_colors(3),
            {1: ["#ef4444", "#22c55e"], 2: ["#ef4444"], 0: ["#a855f7"]},
        )
        slider.set_ignored_ticks(frozenset(), None)
        self.assertEqual(slider._mark_colors(3), {0: ["#a855f7"], 1: ["#22c55e"]})

    def test_the_ignored_colour_is_one_of_the_slider_colours(self) -> None:
        self.tools._ignore_cube.click()
        self.tools._ignore_wl.click()
        self.panel._slider_ribbon.slider_colors.color_changed.emit("ignored", "#112233")
        self.assertEqual(self.panel._cube_slider._ignored_tick_color, "#112233")
        self.assertEqual(self.panel._wavelength_slider._ignored_tick_color, "#112233")
        self.panel._slider_ribbon.slider_colors.color_changed.emit("ignored", None)
        self.assertNotEqual(self.panel._cube_slider._ignored_tick_color, "#112233")

    def test_slider_colours_are_remembered(self) -> None:
        from lspr_imaging_app.panels.ui_state import UiStateStore

        store = UiStateStore()
        self.panel.restore_ui_state(store)
        self.panel._slider_ribbon.slider_colors.color_changed.emit("mask", "#112233")
        self.assertEqual(store.get("image/slider_color_mask"), "#112233")
        self.panel._slider_ribbon.slider_colors.color_changed.emit("mask", None)  # reset
        self.assertIsNone(store.get("image/slider_color_mask"))

        store.set("image/slider_color_mask", "#445566")
        store.set("image/slider_color_roi", "#778899")  # a leftover from before the ROI row lost its colour control: ignored
        store.set("image/slider_color_dark", 7)  # not a colour: a hand-edited file falls back to the default
        store.set("image/slider_color_reference", "not a colour")
        self.panel._restore_slider_colors(store)
        colors = self.panel._slider_colors
        self.assertEqual(colors.color("mask"), "#445566")
        self.assertIsNone(colors.override("dark"))
        self.assertIsNone(colors.override("reference"))


if __name__ == "__main__":
    unittest.main()
