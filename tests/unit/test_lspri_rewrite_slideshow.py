"""Tests for the slider-ribbon slideshow's sequencing rules (`next_frame`,
`first_frame`) and the "1x" interval math (`mean_frame_interval_seconds`).

No dataset files, no widgets; Qt is only imported because the module also holds
the controller.
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset.model import mean_frame_interval_seconds
    from lspr_imaging_app.panels.image.playback_stats import PlaybackStats
    from lspr_imaging_app.panels.image.slideshow import (
        END_CUBE,
        END_DATASET,
        MODE_ALL,
        MODE_MASK_STATES,
        MODE_ONE_WAVELENGTH,
        MODE_ROI_STATES,
        STATE_MODES,
        SlideshowSettings,
        first_frame,
        next_frame,
        previous_frame,
    )
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

CUBES = (0, 1, 2)
WAVELENGTHS = {0: (500.0, 550.0, 600.0), 1: (500.0, 600.0), 2: (500.0, 550.0, 600.0)}  # cube 1 lacks 550


def _next(current, **settings):
    skipped = settings.pop("skipped", frozenset())
    return next_frame(CUBES, WAVELENGTHS, current, SlideshowSettings(**settings), lambda c, w: (c, w) in skipped)


class NextFrameTest(unittest.TestCase):
    def test_all_wavelengths_steps_through_a_cube_then_the_next_cube(self) -> None:
        self.assertEqual(_next((0, 500.0)), (0, 550.0))
        self.assertEqual(_next((0, 600.0)), (1, 500.0))
        self.assertEqual(_next((1, 500.0)), (1, 600.0))  # cube 1 has no 550

    def test_end_of_dataset_stops_without_repeat_and_wraps_with_it(self) -> None:
        self.assertIsNone(_next((2, 600.0)))
        self.assertEqual(_next((2, 600.0), repeat=True), (0, 500.0))

    def test_end_of_cube_stays_in_the_cube(self) -> None:
        self.assertEqual(_next((1, 500.0), end=END_CUBE), (1, 600.0))
        self.assertIsNone(_next((1, 600.0), end=END_CUBE))
        self.assertEqual(_next((1, 600.0), end=END_CUBE, repeat=True), (1, 500.0))

    def test_one_wavelength_steps_cubes_and_skips_cubes_without_it(self) -> None:
        self.assertEqual(_next((0, 550.0), mode=MODE_ONE_WAVELENGTH), (2, 550.0))  # cube 1 skipped
        self.assertIsNone(_next((2, 550.0), mode=MODE_ONE_WAVELENGTH))
        self.assertEqual(_next((2, 550.0), mode=MODE_ONE_WAVELENGTH, repeat=True), (0, 550.0))

    def test_one_wavelength_ignores_the_end_setting(self) -> None:
        self.assertEqual(_next((0, 500.0), mode=MODE_ONE_WAVELENGTH, end=END_CUBE), (1, 500.0))

    def test_ignored_frames_are_skipped(self) -> None:
        self.assertEqual(_next((0, 500.0), skipped={(0, 550.0)}), (0, 600.0))
        self.assertEqual(_next((0, 600.0), skipped={(1, 500.0)}), (1, 600.0))

    def test_a_current_frame_outside_the_range_continues_after_it(self) -> None:
        # The user dragged onto an ignored frame; the show goes on from there.
        self.assertEqual(_next((0, 550.0), skipped={(0, 550.0)}), (0, 600.0))

    def test_nothing_to_show(self) -> None:
        everything = {(c, w) for c, ws in WAVELENGTHS.items() for w in ws}
        self.assertIsNone(_next((0, 500.0), skipped=everything, repeat=True))

    def test_a_single_frame_range_never_loops_on_itself(self) -> None:
        self.assertIsNone(next_frame((0,), {0: (500.0,)}, (0, 500.0), SlideshowSettings(repeat=True)))

    def test_first_frame_is_the_start_of_the_range(self) -> None:
        settings = SlideshowSettings()
        self.assertEqual(first_frame(CUBES, WAVELENGTHS, (2, 600.0), settings), (0, 500.0))
        self.assertEqual(first_frame(CUBES, WAVELENGTHS, (2, 600.0), SlideshowSettings(end=END_CUBE)), (2, 500.0))
        self.assertEqual(first_frame(CUBES, WAVELENGTHS, (2, 550.0), SlideshowSettings(mode=MODE_ONE_WAVELENGTH)), (0, 550.0))

    def test_the_search_is_lazy_it_does_not_look_at_cubes_it_does_not_need(self) -> None:
        asked: list[int] = []

        class Spy(dict):
            def get(self, key, default=()):
                asked.append(key)
                return super().get(key, default)

        many = Spy({cube: (500.0, 550.0) for cube in range(300)})
        frame = next_frame(list(range(300)), many, (150, 500.0), SlideshowSettings())
        self.assertEqual(frame, (150, 550.0))
        self.assertEqual(asked, [150])  # one cube looked at, not 300

    def test_default_mode_is_all_and_end_is_dataset(self) -> None:
        self.assertEqual((SlideshowSettings().mode, SlideshowSettings().end), (MODE_ALL, END_DATASET))


def _previous(current, **settings):
    skipped = settings.pop("skipped", frozenset())
    return previous_frame(CUBES, WAVELENGTHS, current, SlideshowSettings(**settings), lambda c, w: (c, w) in skipped)


class PreviousFrameTest(unittest.TestCase):
    """The manual step back (2026-10-16): the mirror of `next_frame`, without wrapping and without the End setting."""

    def test_all_wavelengths_steps_back_through_a_cube_then_the_cube_before(self) -> None:
        self.assertEqual(_previous((0, 600.0)), (0, 550.0))
        self.assertEqual(_previous((1, 500.0)), (0, 600.0))
        self.assertEqual(_previous((2, 500.0)), (1, 600.0))
        self.assertEqual(_previous((1, 600.0)), (1, 500.0))  # cube 1 has no 550

    def test_one_wavelength_steps_back_through_the_cubes_and_skips_cubes_without_it(self) -> None:
        self.assertEqual(_previous((2, 550.0), mode=MODE_ONE_WAVELENGTH), (0, 550.0))  # cube 1 skipped
        self.assertIsNone(_previous((0, 550.0), mode=MODE_ONE_WAVELENGTH))

    def test_the_start_is_the_end_of_the_road_it_never_wraps(self) -> None:
        self.assertIsNone(_previous((0, 500.0)))
        self.assertIsNone(_previous((0, 500.0), repeat=True))

    def test_the_end_setting_does_not_stop_a_manual_step(self) -> None:
        self.assertEqual(_previous((1, 500.0), end=END_CUBE), (0, 600.0))

    def test_ignored_frames_are_skipped(self) -> None:
        self.assertEqual(_previous((1, 500.0), skipped={(0, 600.0)}), (0, 550.0))

    def test_a_current_cube_missing_from_the_list_steps_back_from_where_it_would_be(self) -> None:
        self.assertEqual(previous_frame((0, 2, 4), {0: (500.0,), 2: (500.0, 550.0), 4: (500.0,)}, (3, 500.0), SlideshowSettings()), (2, 550.0))

    def test_next_and_previous_undo_each_other(self) -> None:
        frame = (0, 500.0)
        for _ in range(7):
            following = _next(frame)
            self.assertEqual(_previous(following), frame)
            frame = following


STATE_WAVELENGTHS = {cube: (500.0, 550.0) for cube in range(8)}
STATE_WAVELENGTHS[3] = (500.0,)  # cube 3 lacks 550


def _state_next(states, current, **settings):
    skipped = settings.pop("skipped", frozenset())
    return next_frame(states, STATE_WAVELENGTHS, current, SlideshowSettings(mode=MODE_ROI_STATES, **settings), lambda c, w: (c, w) in skipped)


def _state_previous(states, current, **settings):
    skipped = settings.pop("skipped", frozenset())
    return previous_frame(states, STATE_WAVELENGTHS, current, SlideshowSettings(mode=MODE_ROI_STATES, **settings), lambda c, w: (c, w) in skipped)


class RoiStatesModeTest(unittest.TestCase):
    """ROI states (2026-10-16): the cubes are the ROI row's marks; the wavelength stays; the current cube need not be one."""

    STATES = (0, 3, 5)

    def test_next_goes_to_the_first_state_after_the_current_cube_even_between_states(self) -> None:
        self.assertEqual(_state_next(self.STATES, (1, 550.0)), (5, 550.0))  # cube 3 lacks 550: passed over
        self.assertEqual(_state_next(self.STATES, (1, 500.0)), (3, 500.0))
        self.assertEqual(_state_next(self.STATES, (0, 500.0)), (3, 500.0))  # on a state: the one after it

    def test_previous_goes_to_the_last_state_before_the_current_cube(self) -> None:
        self.assertEqual(_state_previous(self.STATES, (4, 500.0)), (3, 500.0))
        self.assertEqual(_state_previous(self.STATES, (5, 500.0)), (3, 500.0))
        self.assertEqual(_state_previous(self.STATES, (4, 550.0)), (0, 550.0))

    def test_the_ends_do_not_wrap_unless_the_show_repeats(self) -> None:
        self.assertIsNone(_state_next(self.STATES, (5, 500.0)))
        self.assertIsNone(_state_previous(self.STATES, (0, 500.0)))
        self.assertEqual(_state_next(self.STATES, (5, 500.0), repeat=True), (0, 500.0))
        self.assertIsNone(_state_next((5,), (5, 500.0), repeat=True))  # a single state never loops on itself

    def test_skipped_frames_are_passed_over(self) -> None:
        self.assertEqual(_state_next(self.STATES, (0, 500.0), skipped={(3, 500.0)}), (5, 500.0))

    def test_first_frame_is_the_first_state_with_the_wavelength(self) -> None:
        settings = SlideshowSettings(mode=MODE_ROI_STATES)
        self.assertEqual(first_frame(self.STATES, STATE_WAVELENGTHS, (4, 550.0), settings), (0, 550.0))
        self.assertEqual(first_frame((3, 5), STATE_WAVELENGTHS, (4, 550.0), settings), (5, 550.0))
        self.assertIsNone(first_frame((), STATE_WAVELENGTHS, (4, 550.0), settings))

    def test_no_states_nothing_to_visit(self) -> None:
        self.assertIsNone(_state_next((), (0, 500.0)))
        self.assertIsNone(_state_previous((), (0, 500.0)))

    def test_the_end_setting_means_nothing_here(self) -> None:
        self.assertEqual(_state_next(self.STATES, (0, 500.0), end=END_CUBE), (3, 500.0))

    def test_mask_states_steps_exactly_like_roi_states_over_its_own_cubes(self) -> None:
        mask_cubes = (1, 4, 6)
        for current in ((0, 500.0), (1, 500.0), (3, 500.0), (4, 550.0), (7, 500.0)):
            for step in (next_frame, previous_frame):
                roi = step(mask_cubes, STATE_WAVELENGTHS, current, SlideshowSettings(mode=MODE_ROI_STATES))
                mask = step(mask_cubes, STATE_WAVELENGTHS, current, SlideshowSettings(mode=MODE_MASK_STATES))
                self.assertEqual(roi, mask, (step.__name__, current))
        self.assertEqual(next_frame(mask_cubes, STATE_WAVELENGTHS, (2, 500.0), SlideshowSettings(mode=MODE_MASK_STATES)), (4, 500.0))
        self.assertEqual(first_frame(mask_cubes, STATE_WAVELENGTHS, (9, 550.0), SlideshowSettings(mode=MODE_MASK_STATES)), (1, 550.0))
        self.assertIn(MODE_MASK_STATES, STATE_MODES)


class MeanIntervalTest(unittest.TestCase):
    def test_average_gap_inside_cubes_only(self) -> None:
        # Cube 0: frames 1 s apart. Cube 1 starts 100 s later - that pause must not count.
        timings = {(0, 500.0): 0, (0, 550.0): 1000, (0, 600.0): 2000, (1, 500.0): 102000, (1, 550.0): 103000}
        self.assertAlmostEqual(mean_frame_interval_seconds(timings), 1.0)

    def test_none_without_two_timed_images_in_a_cube(self) -> None:
        self.assertIsNone(mean_frame_interval_seconds({}))
        self.assertIsNone(mean_frame_interval_seconds({(0, 500.0): 0, (1, 500.0): 5000}))


class DatasetIntervalCacheTest(unittest.TestCase):
    """`DatasetModule.mean_frame_interval_seconds` is asked on every slideshow step (to re-arm the timer): it must scan the
    frame timings once per dataset (testing10: 4 % of the GUI thread while playing), and notice another dataset."""

    def test_computed_once_per_dataset_and_noticed_when_it_changes(self) -> None:
        from lspr_imaging_app.dataset import DatasetModule
        from lspr_imaging_app.dataset import module as dataset_module

        module = DatasetModule()
        stub = SimpleNamespace(acquisition_metadata=object(), records=[1, 2, 3])
        module._dataset = stub
        timings = SimpleNamespace(per_frame_ms={(0, 500.0): 0, (0, 550.0): 300, (0, 600.0): 700})
        calls: list[int] = []

        def compact(_dataset):
            calls.append(1)
            return timings

        with mock.patch.object(dataset_module, "compact_dataset_image_timings", side_effect=compact):
            first = module.mean_frame_interval_seconds()
            for _ in range(5):
                self.assertEqual(module.mean_frame_interval_seconds(), first)
            self.assertEqual(len(calls), 1)
            stub.records.append(4)  # a grown record list is noticed
            module.mean_frame_interval_seconds()
            self.assertEqual(len(calls), 2)
            module._dataset = SimpleNamespace(acquisition_metadata=object(), records=[1, 2, 3, 4])  # another dataset
            module.mean_frame_interval_seconds()
            self.assertEqual(len(calls), 3)
            module.clear_dataset()
            self.assertIsNone(module.mean_frame_interval_seconds())
        self.assertAlmostEqual(first, 0.35)  # gaps of 300 and 400 ms inside the cube: 350 ms on average


class DatasetIndexTest(unittest.TestCase):
    """`ImageDataset` answers cube / wavelength queries from a cached index (they scanned every record before)."""

    def _dataset(self, layout):
        from pathlib import Path

        from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord

        records = [
            ImageRecord(key=ImageKey(wavelength_nm=wl, spectral_cube_index=cube), path=Path(f"c{cube}_{wl}.tif"))
            for cube, wavelengths in layout.items() for wl in wavelengths
        ]
        return ImageDataset(folder=Path("."), records=records)

    def test_answers_match_a_brute_force_scan_including_a_cube_short_a_wavelength(self) -> None:
        layout = {2: (500.0, 0.0, 600.0), 0: (500.0, 550.0, 600.0), 1: (500.0, 600.0)}
        ds = self._dataset(layout)
        self.assertEqual(ds.spectral_cube_indices, [0, 1, 2])
        self.assertEqual(ds.wavelengths_nm, [0.0, 500.0, 550.0, 600.0])
        for cube, wavelengths in layout.items():
            self.assertEqual(ds.wavelengths_for_cube(cube), sorted(wavelengths))
        self.assertEqual(ds.wavelengths_for_cube(99), [])

    def test_callers_get_copies_and_a_replaced_or_grown_record_list_is_noticed(self) -> None:
        ds = self._dataset({0: (500.0, 600.0)})
        ds.wavelengths_for_cube(0).append(123.0)  # a caller editing its result must not corrupt the index
        self.assertEqual(ds.wavelengths_for_cube(0), [500.0, 600.0])
        other = self._dataset({0: (500.0, 600.0), 1: (700.0,)})
        ds.records = other.records  # replaced
        self.assertEqual(ds.spectral_cube_indices, [0, 1])
        ds.records.append(self._dataset({5: (800.0,)}).records[0])  # grown in place
        self.assertEqual(ds.spectral_cube_indices, [0, 1, 5])
        self.assertEqual(ds.wavelengths_for_cube(5), [800.0])


class NoDataOverlayCacheTest(unittest.TestCase):
    def test_same_mask_returns_the_same_array_and_a_changed_mask_a_new_one(self) -> None:
        import numpy as np

        from lspr_imaging_app.panels.image import no_data

        clean = np.ones((40, 50), dtype=np.float32)
        self.assertIsNone(no_data.no_data_overlay_rgba(clean))
        first = clean.copy()
        first[:5, :5] = np.nan
        a = no_data.no_data_overlay_rgba(first)
        again = first * 2.0  # another wavelength, same NaN corner
        self.assertIs(no_data.no_data_overlay_rgba(again), a)
        self.assertEqual(int(a[0, 0, 3]), 255)
        self.assertEqual(int(a[20, 20, 3]), 0)
        moved = clean.copy()
        moved[-5:, -5:] = np.nan
        b = no_data.no_data_overlay_rgba(moved)
        self.assertIsNot(b, a)
        self.assertEqual((int(b[0, 0, 3]), int(b[-1, -1, 3])), (0, 255))
        self.assertTrue(np.array_equal(b[..., :3], a[..., :3]))  # the checker pattern itself is shared per shape


class PlaybackStatsTest(unittest.TestCase):
    def test_one_frames_trip_is_split_into_its_stages(self) -> None:
        stats = PlaybackStats()
        stats.start(0.9, target_interval_s=0.1)
        stats.note_step(1.000, handlers_s=0.002)  # the step began at 0.998
        stats.note_redraw(7, start=1.100, end=1.101)  # waited ~102 ms for the redraw timer
        line = stats.note_rendered(7, arrived=1.200, done=1.206, load_s=0.060, prep_s=0.030, listeners_s=0.004)
        self.assertIsNone(line)  # the one-second window is not over
        fields = stats.total_fields()  # the stage breakdown lives in the log fields, not in the status bar
        expected = {"wait_ms": 102, "redraw_ms": 1, "queue_ms": 9, "load_ms": 60, "prep_ms": 30,
                    "gui_ms": 6, "listeners_ms": 4, "lag_ms": 208}
        for name, value in expected.items():
            self.assertAlmostEqual(fields[name], value, delta=0.2, msg=name)

    def test_detail_stages_are_reported_per_shown_frame_biggest_first(self) -> None:
        stats = PlaybackStats()
        stats.note_detail("ignored_while_inactive", 1.0)
        stats.start(0.9, target_interval_s=0.1)
        for frame in range(2):
            stats.note_step(1.0 + frame, 0.0)
            stats.note_redraw(frame + 1, 1.0 + frame, 1.0 + frame)
            stats.note_detail("overlays", 0.040)
            stats.note_detail("paint_view", 0.060)  # a stage may run several times per frame
            stats.note_detail("paint_view", 0.060)
            stats.note_rendered(frame + 1, 1.1 + frame, 1.11 + frame, load_s=0.0, prep_s=0.0, listeners_s=0.0)
        fields = stats.total_fields()
        self.assertEqual((fields["d_overlays_ms"], fields["d_paint_view_ms"]), (40.0, 120.0))
        self.assertNotIn("d_ignored_while_inactive_ms", fields)

    def test_the_whole_cycle_is_reported_so_unmeasured_time_shows_up_as_other(self) -> None:
        stats = PlaybackStats()
        stats.start(0.9, target_interval_s=0.1)
        for frame in range(3):  # a step every 150 ms, each frame's lag only 100 ms
            start = 1.0 + 0.15 * frame
            stats.note_step(start, 0.0)
            stats.note_redraw(frame + 1, start, start)
            stats.note_rendered(frame + 1, start + 0.09, start + 0.10, load_s=0.0, prep_s=0.0, listeners_s=0.0)
        fields = stats.total_fields()
        self.assertEqual((fields["cycle_ms"], fields["lag_ms"]), (150.0, 100.0))  # the other 50 ms = cycle - lag

    def test_queue_time_is_split_into_thread_wait_and_gui_delivery(self) -> None:
        stats = PlaybackStats()
        stats.start(0.9, target_interval_s=0.1)
        stats.note_step(1.000, handlers_s=0.0)
        stats.note_redraw(1, start=1.0, end=1.001)
        stats.note_rendered(
            1, arrived=1.200, done=1.205, load_s=0.0, prep_s=0.0, listeners_s=0.0,
            submitted_at=1.001, started_at=1.101, finished_at=1.150, blocked_by_prefetch=True,
        )
        fields = stats.total_fields()
        self.assertEqual((fields["thread_ms"], fields["deliver_ms"], fields["blocked"]), (100.0, 50.0, 1))

    def test_gui_thread_cpu_share_says_busy_or_waiting(self) -> None:
        stats = PlaybackStats()
        stats.start(0.0, target_interval_s=0.05)
        stats.note_gui_clock(wall=1.00, cpu=10.00)  # the first call only sets the reference point
        for index in range(1, 5):  # 50 ms of wall per frame, 30 ms of it the GUI thread's own CPU time
            stats.note_gui_clock(wall=1.00 + 0.05 * index, cpu=10.00 + 0.03 * index)
        fields = stats.total_fields()
        self.assertAlmostEqual(fields["gui_cpu_ms"], 30.0, places=1)
        self.assertEqual(fields["gui_busy_pct"], 60.0)

    def test_gui_kernel_time_is_reported_apart_from_the_total(self) -> None:
        stats = PlaybackStats()
        stats.start(0.0, target_interval_s=0.05)
        stats.note_gui_clock(wall=1.00, cpu=10.00, kernel=2.00)
        for index in range(1, 5):  # 30 ms CPU per frame, 12 ms of it kernel time
            stats.note_gui_clock(wall=1.00 + 0.05 * index, cpu=10.00 + 0.03 * index, kernel=2.00 + 0.012 * index)
        fields = stats.total_fields()
        self.assertAlmostEqual(fields["gui_kernel_ms"], 12.0, places=1)
        self.assertAlmostEqual(fields["gui_cpu_ms"], 30.0, places=1)

    def test_gui_clock_is_silent_when_inactive_and_starts_afresh_with_each_run(self) -> None:
        stats = PlaybackStats()
        stats.note_gui_clock(1.0, 1.0)  # not started: ignored
        stats.start(0.0, target_interval_s=0.05)
        stats.note_gui_clock(5.0, 5.0)  # a first call after start() is only a reference point
        self.assertEqual(stats.total_fields()["gui_busy_pct"], 0.0)
        stats.stop()
        stats.start(10.0, target_interval_s=0.05)
        stats.note_gui_clock(20.0, 2.0)  # must not be measured against the previous run's last call
        self.assertEqual(stats.total_fields()["gui_busy_pct"], 0.0)

    def test_steps_before_a_redraw_are_merged(self) -> None:
        stats = PlaybackStats()
        stats.start(0.0, target_interval_s=0.05)
        for k in range(5):
            stats.note_step(1.0 + 0.05 * k, handlers_s=0.001)
        stats.note_redraw(1, start=1.3, end=1.301)
        stats.note_rendered(1, arrived=1.31, done=1.32, load_s=0.0, prep_s=0.0, listeners_s=0.0)
        fields = stats.total_fields()
        self.assertEqual((fields["steps"], fields["shown"]), (5, 1))  # 4 steps were merged into the one frame shown

    def test_a_superseded_result_is_not_counted_and_inactive_is_silent(self) -> None:
        stats = PlaybackStats()
        self.assertIsNone(stats.note_rendered(1, 0.0, 0.1, load_s=0.0, prep_s=0.0, listeners_s=0.0))  # inactive
        stats.start(0.0, target_interval_s=0.1)
        stats.note_step(1.0, 0.0)
        stats.note_redraw(1, 1.0, 1.0)
        stats.note_step(1.1, 0.0)
        stats.note_redraw(2, 1.1, 1.1)
        stats.note_rendered(2, 1.2, 1.21, load_s=0.0, prep_s=0.0, listeners_s=0.0)  # 1 never arrives
        self.assertEqual(stats._total.stale, 1)
        self.assertEqual(stats._total.shown, 1)

    def test_the_status_line_is_only_the_frame_rate_next_to_the_asked_one(self) -> None:
        stats = PlaybackStats()
        stats.start(0.0, target_interval_s=0.5)  # speed setting: 2 frames per second
        lines = []
        for serial, done in ((1, 0.5), (2, 1.0)):
            stats.note_step(done - 0.05, 0.0)
            stats.note_redraw(serial, done - 0.04, done - 0.04)
            lines.append(stats.note_rendered(serial, done - 0.01, done, load_s=0.0, prep_s=0.0, listeners_s=0.0))
        self.assertIsNone(lines[0])  # the one-second window is not over yet
        self.assertEqual(lines[1], "Slideshow: 2.0 of 2.0 fps")  # a line every second, no stage timings
        self.assertEqual(stats.summary(now=2.0), "Slideshow finished: 1.0 of 2.0 fps on average")

    def test_a_run_without_a_shown_frame_says_so(self) -> None:
        stats = PlaybackStats()
        stats.start(0.0, target_interval_s=0.5)
        self.assertEqual(stats.summary(now=1.0), "Slideshow: no frame was shown")


if __name__ == "__main__":
    unittest.main()
