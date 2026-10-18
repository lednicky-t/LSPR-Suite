"""Tests for "Find reference" (best-contrast wavelength per cube) and the
app-wide cube display unit (cube number / start time) of the LSPRimaging
Evaluation rewrite.

Pure logic only: the scoring function, the time formatting and the
`ReferenceFrameModule` cache/resolve rules need no `QApplication` for the
scoring and formatting; the module test builds a bare `QObject` subclass, which
also needs none.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_core import ImagingAcquisitionMetadata, ImagingCommentEvent, ImagingCubeTiming
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.panels.image.slider_ticks import cube_slider_major_ticks
    from lspr_imaging_app.progress import Cancelled
    from lspr_imaging_app.selection.cube_display_module import MODE_TIME, CubeDisplayModule, format_elapsed_seconds
    from lspr_imaging_app.selection.reference_frame_module import ReferenceFrameModule
    from lspr_imaging_app.selection.reference_scoring import best_contrast_wavelength
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402


def _frame(spot_level: float) -> np.ndarray:
    """Bright background (1000) with a block of darker spots at `spot_level`."""
    image = np.full((100, 100), 1000.0, dtype=np.float32)
    image[20:40, 20:40] = spot_level
    image[60:80, 60:80] = spot_level
    return image


class BestContrastWavelengthTest(unittest.TestCase):
    def test_picks_the_wavelength_with_the_deepest_spots(self) -> None:
        planes = {500.0: _frame(900.0), 550.0: _frame(300.0), 600.0: _frame(700.0)}
        self.assertEqual(best_contrast_wavelength(list(planes), planes.__getitem__), 550.0)

    def test_dark_frame_is_never_a_candidate(self) -> None:
        planes = {0.0: _frame(0.0), 500.0: _frame(800.0)}
        self.assertEqual(best_contrast_wavelength(list(planes), planes.__getitem__), 500.0)

    def test_unreadable_plane_is_skipped_not_fatal(self) -> None:
        planes = {500.0: _frame(500.0)}

        def load(wavelength: float) -> np.ndarray:
            if wavelength == 600.0:
                raise OSError("unreadable")
            return planes[wavelength]

        self.assertEqual(best_contrast_wavelength([600.0, 500.0], load), 500.0)

    def test_nothing_readable_gives_none(self) -> None:
        def load(_wavelength: float) -> np.ndarray:
            raise OSError("unreadable")

        self.assertIsNone(best_contrast_wavelength([500.0, 600.0], load))
        self.assertIsNone(best_contrast_wavelength([], load))

    def test_cancel_stops_the_scan(self) -> None:
        calls: list[float] = []

        def load(wavelength: float) -> np.ndarray:
            calls.append(wavelength)
            return _frame(500.0)

        with self.assertRaises(Cancelled):
            best_contrast_wavelength([500.0, 550.0], load, cancelled=lambda: len(calls) >= 1)
        self.assertEqual(calls, [500.0])

    def test_progress_reports_per_plane_up_to_one(self) -> None:
        reports: list[float] = []
        best_contrast_wavelength([500.0, 550.0], lambda _w: _frame(500.0), progress=lambda f, _m: reports.append(f))
        self.assertEqual(reports, [0.0, 0.5, 1.0])


class ReferenceFrameResolveTest(unittest.TestCase):
    def test_auto_uses_the_viewed_cubes_cached_wavelength_and_does_not_follow_the_wavelength(self) -> None:
        module = ReferenceFrameModule()
        self.assertIsNone(module.resolve(0))  # not found yet
        module.set_auto_wavelength(0, 550.0)
        self.assertEqual(module.resolve(0), (0, 550.0))
        self.assertIsNone(module.resolve(1))  # another cube: its own search
        module.set_auto_wavelength(1, 600.0)
        self.assertEqual(module.resolve(1), (1, 600.0))

    def test_manual_ignores_the_viewed_cube(self) -> None:
        module = ReferenceFrameModule()
        module.set_auto_wavelength(0, 550.0)
        module.set_manual_frame(1, 500.0)
        self.assertEqual(module.resolve(0), (1, 500.0))
        self.assertEqual(module.resolve(None), (1, 500.0))

    def test_reset_forgets_the_cache(self) -> None:
        module = ReferenceFrameModule()
        module.set_auto_wavelength(0, 550.0)
        module.reset()
        self.assertIsNone(module.resolve(0))

    def test_setting_the_same_auto_wavelength_again_does_not_emit(self) -> None:
        module = ReferenceFrameModule()
        emitted: list[int] = []
        module.reference_frame_changed.connect(lambda: emitted.append(1))
        module.set_auto_wavelength(0, 550.0)
        module.set_auto_wavelength(0, 550.0)
        self.assertEqual(len(emitted), 1)


class CubeDisplayHelpersTest(unittest.TestCase):
    def test_elapsed_seconds_format(self) -> None:
        self.assertEqual(format_elapsed_seconds(0), "0:00")
        self.assertEqual(format_elapsed_seconds(75), "1:15")
        self.assertEqual(format_elapsed_seconds(3725), "1:02:05")
        self.assertEqual(format_elapsed_seconds(-5), "-0:05")

    def test_elapsed_seconds_format_with_tens_of_ms(self) -> None:
        self.assertEqual(format_elapsed_seconds(65.24, decimals=2), "1:05.24")
        self.assertEqual(format_elapsed_seconds(0, decimals=2), "0:00.00")
        self.assertEqual(format_elapsed_seconds(3725.07, decimals=2), "1:02:05.07")
        self.assertEqual(format_elapsed_seconds(59.999, decimals=2), "1:00.00")  # rounds as a whole: never 0:60.00
        self.assertEqual(format_elapsed_seconds(-9.5, decimals=2), "-0:09.50")

    def test_cube_ticks_use_the_label_function(self) -> None:
        values = tuple(range(20))
        plain = cube_slider_major_ticks(values)
        named = cube_slider_major_ticks(values, lambda cube: f"t{cube}")
        self.assertEqual(set(plain), set(named))
        self.assertTrue(all(label.startswith("t") for label in named.values()))


def _timed_dataset(with_metadata: bool) -> ImageDataset:
    records = [
        ImageRecord(key=ImageKey(wavelength_nm=wl, spectral_cube_index=cube), path=Path(f"c{cube}_{int(wl)}.tif"))
        for cube in (0, 1)
        for wl in (500.0, 600.0)
    ]
    metadata = None
    if with_metadata:
        base = 1_700_000_000_000
        metadata = ImagingAcquisitionMetadata(
            source_format="legacy_measuring_times_csv",
            image_timings=[
                ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=500.0, acquired_at_unix_ms=base + 1_000),
                ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=600.0, acquired_at_unix_ms=base + 3_000),
                ImagingCubeTiming(spectral_cube_index=1, wavelength_nm=500.0, acquired_at_unix_ms=base + 61_000),
                ImagingCubeTiming(spectral_cube_index=1, wavelength_nm=600.0, acquired_at_unix_ms=base + 63_000),
            ],
            comment_events=[ImagingCommentEvent(acquired_at_unix_ms=base + 60_000, comment="buffer exchange")],
        )
    return ImageDataset(folder=Path("."), records=records, source_format="image_stack", acquisition_metadata=metadata)


class DatasetTimeAndCommentTest(unittest.TestCase):
    def test_cube_start_is_the_first_frame_since_the_earliest_image(self) -> None:
        dataset = DatasetModule()
        dataset.load_dataset(_timed_dataset(True))
        self.assertEqual(dataset.cube_start_seconds(0), 0.0)  # no started_at_utc: anchored on the earliest frame
        self.assertEqual(dataset.cube_start_seconds(1), 60.0)  # first frame of cube 1, not its later ones
        self.assertIsNone(dataset.cube_start_seconds(7))

    def test_frame_start_is_each_frames_own_time_on_the_same_clock_as_the_cube_time(self) -> None:
        dataset = DatasetModule()
        dataset.load_dataset(_timed_dataset(True))
        self.assertEqual(dataset.frame_start_seconds(0, 500.0), 0.0)
        self.assertEqual(dataset.frame_start_seconds(0, 600.0), 2.0)  # a later frame of cube 0: not the cube's 0.0
        self.assertEqual(dataset.frame_start_seconds(1, 500.0), dataset.cube_start_seconds(1))  # a cube's time is its first frame's
        self.assertEqual(dataset.frame_start_seconds(1, 600.0), 62.0)
        self.assertIsNone(dataset.frame_start_seconds(5, 500.0), "no timing for this frame")
        self.assertIsNone(dataset.frame_start_seconds(0, 550.0), "no such wavelength")

    def test_frame_start_is_none_without_acquisition_metadata_or_dataset(self) -> None:
        plain = DatasetModule()
        self.assertIsNone(plain.frame_start_seconds(0, 500.0))  # nothing loaded
        plain.load_dataset(_timed_dataset(False))
        self.assertIsNone(plain.frame_start_seconds(0, 500.0))

    def test_the_recorded_start_is_time_zero_when_it_can_be_read(self) -> None:
        base = 1_700_000_000_000  # 2023-11-14T22:13:20Z
        timings = [ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=500.0, acquired_at_unix_ms=base + 1_000)]

        def seconds(started: str | None) -> float | None:
            dataset = DatasetModule()
            model = _timed_dataset(False)
            model.acquisition_metadata = ImagingAcquisitionMetadata(
                source_format="legacy_measuring_times_csv", image_timings=list(timings), started_at_utc=started
            )
            dataset.load_dataset(model)
            return dataset.frame_start_seconds(0, 500.0)

        self.assertEqual(seconds("2023-11-14T22:13:10Z"), 11.0)  # started 10 s before the base
        self.assertEqual(seconds("2023-11-14T22:13:30Z"), -9.0)  # a frame before the recorded start keeps its sign
        self.assertEqual(seconds("not a time"), 0.0, "unreadable start: the earliest frame is t = 0")
        self.assertEqual(seconds(None), 0.0)

    def test_time_zero_follows_a_newly_loaded_dataset(self) -> None:
        """The anchor is computed once, so it must be dropped when the dataset changes."""
        dataset = DatasetModule()
        dataset.load_dataset(_timed_dataset(True))
        self.assertEqual(dataset.frame_start_seconds(0, 600.0), 2.0)
        shifted = _timed_dataset(True)
        shifted.acquisition_metadata = ImagingAcquisitionMetadata(
            source_format="legacy_measuring_times_csv",
            image_timings=[
                ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=500.0, acquired_at_unix_ms=5_000),
                ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=600.0, acquired_at_unix_ms=9_000),
            ],
        )
        dataset.load_dataset(shifted)
        self.assertEqual(dataset.frame_start_seconds(0, 600.0), 4.0)
        dataset.clear_dataset()
        self.assertIsNone(dataset.frame_start_seconds(0, 600.0))

    def test_frame_comment_linked_unlinked_and_empty(self) -> None:
        dataset = DatasetModule()
        dataset.load_dataset(_timed_dataset(True))
        self.assertEqual(dataset.frame_comment(1, 600.0), (True, "buffer exchange"))
        self.assertEqual(dataset.frame_comment(0, 500.0), (True, None))  # before any comment was recorded
        self.assertEqual(dataset.frame_comment(5, 500.0), (False, None))  # no timing for this frame
        plain = DatasetModule()
        plain.load_dataset(_timed_dataset(False))
        self.assertEqual(plain.frame_comment(0, 500.0), (False, None))
        self.assertIsNone(plain.cube_start_seconds(0))

    def test_cube_display_falls_back_to_the_number_without_timing(self) -> None:
        dataset = DatasetModule()
        dataset.load_dataset(_timed_dataset(False))
        display = CubeDisplayModule(dataset)
        display.set_mode(MODE_TIME)
        self.assertFalse(display.time_available())
        self.assertEqual(display.label_for(1), "1")
        self.assertEqual(display.axis_title(), "Cube (#)")

    def test_cube_display_time_mode_labels_cubes_by_start_time(self) -> None:
        dataset = DatasetModule()
        dataset.load_dataset(_timed_dataset(True))
        display = CubeDisplayModule(dataset)
        self.assertEqual(display.label_for(1), "1")
        display.set_mode(MODE_TIME)
        self.assertEqual(display.label_for(1), "1:00")
        self.assertEqual(display.axis_title(), "Cube (t)")


if __name__ == "__main__":
    unittest.main()
