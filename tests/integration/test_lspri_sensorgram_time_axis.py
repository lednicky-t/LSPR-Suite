"""Integration test for the sensorgram's time-aware x-axis: when acquisition
metadata with per-image timing is loaded, the sensorgram plots against real
elapsed seconds since the dataset's start instead of raw spectral_cube_index
- the actual point of linking cubes to time (see gui/analysis_controller.py's
_sensorgram_x_values/_sensorgram_time_mode_metadata). Datasets without that
metadata (still the common case) must keep plotting by raw index, unchanged.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_core import ImagingAcquisitionMetadata, ImagingCubeTiming  # noqa: E402

from lspr_imaging_app.domain.models import ImageDataset, ImageKey, ImageRecord  # noqa: E402
from lspr_imaging_app.gui.main_window import MainWindow  # noqa: E402
from lspr_imaging_app.io.dataset import dataset_record_map  # noqa: E402

# Three cubes, two wavelengths each, at real-world-shaped elapsed times
# (matches the actual legacy-import cadence seen in production data: ~330ms
# within a cube, ~9s between cubes) so this isn't just testing round numbers.
_STARTED_AT_UTC = "2025-09-12T12:59:11Z"
_START_UNIX_MS = 1_757_681_951_000
_TIMINGS = [
    ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=470.0, acquired_at_unix_ms=_START_UNIX_MS + 144),
    ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=480.0, acquired_at_unix_ms=_START_UNIX_MS + 472),
    ImagingCubeTiming(spectral_cube_index=1, wavelength_nm=470.0, acquired_at_unix_ms=_START_UNIX_MS + 9535),
    ImagingCubeTiming(spectral_cube_index=1, wavelength_nm=480.0, acquired_at_unix_ms=_START_UNIX_MS + 9875),
    ImagingCubeTiming(spectral_cube_index=2, wavelength_nm=470.0, acquired_at_unix_ms=_START_UNIX_MS + 18937),
    ImagingCubeTiming(spectral_cube_index=2, wavelength_nm=480.0, acquired_at_unix_ms=_START_UNIX_MS + 19267),
]


def _make_dataset(folder: Path, *, with_metadata: bool) -> ImageDataset:
    records = [
        ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=folder / f"c{cube}_wl{wavelength:.0f}.tif")
        for cube in range(3)
        for wavelength in (470.0, 480.0)
    ]
    metadata = None
    if with_metadata:
        metadata = ImagingAcquisitionMetadata(
            source_format="legacy_measuring_times_csv", started_at_utc=_STARTED_AT_UTC, image_timings=list(_TIMINGS)
        )
    return ImageDataset(folder=folder, records=records, acquisition_metadata=metadata)


def _load_into_window(window: MainWindow, dataset: ImageDataset, folder: Path) -> None:
    # Sets exactly the state the sensorgram x-axis code under test actually
    # reads, without going through DatasetController._finish_load_dataset_
    # from_folder's full chain (image refresh, processing-state restore) -
    # that chain dispatches background QThreadPool work against these
    # records' (synthetic, non-existent) paths, which was racing this test's
    # tempfile.TemporaryDirectory() cleanup on Windows (WinError 145).
    window._state.dataset = dataset
    window._record_map = dataset_record_map(dataset)
    window._spectral_cube_values = dataset.spectral_cube_indices
    window._wavelength_values = dataset.wavelengths_nm
    window._configure_slider(window.spectral_cube_slider, len(window._spectral_cube_values))
    window._configure_slider(window.wavelength_slider, len(window._wavelength_values))


@contextmanager
def _open_window(folder: Path):
    """MainWindow construction/teardown, matching test_main_window_state.py's
    close()+deleteLater() convention - each window owns a pyqtgraph
    PlotWidget (native paint resources), so leaving many unclosed across a
    full test-suite run accumulates real OS-level resources rather than
    just Python objects, and can crash Qt itself later in an unrelated test."""
    window = MainWindow(folder, fast_startup=True)
    try:
        yield window
    finally:
        # closeEvent() saves processing/session state for window._state.dataset
        # if one is set - real, wanted behavior for a real user, but these
        # tests point `dataset.folder` at a temp directory about to be
        # deleted, and the save races that deletion (WinError 145 on
        # Windows). Clearing it first makes close() a no-op save, matching
        # what these tests actually want (just release the window's Qt
        # resources, not persist anything).
        window._state.dataset = None
        window.close()
        window.deleteLater()


class SensorgramTimeAxisTests(unittest.TestCase):
    def test_with_metadata_plots_elapsed_seconds_since_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with _open_window(folder) as window:
                _load_into_window(window, _make_dataset(folder, with_metadata=True), folder)

                self.assertEqual(window._spectral_cube_axis_label(), "Elapsed time (s)")
                window._analysis_controller.set_sensorgram_series([0, 1, 2], [1.0, 1.1, 1.2], summary_text="test")
                x_values, y_values = window.sensorgram_curve.getData()

                # Anchored to started_at_utc: cube 0's earliest image is 144ms
                # after start -> 0.144s, cube 1's is 9535ms, cube 2's is 18937ms.
                self.assertAlmostEqual(x_values[0], 0.144, places=3)
                self.assertAlmostEqual(x_values[1], 9.535, places=3)
                self.assertAlmostEqual(x_values[2], 18.937, places=3)
                self.assertEqual(list(y_values), [1.0, 1.1, 1.2])

    def test_without_metadata_still_plots_raw_cube_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with _open_window(folder) as window:
                _load_into_window(window, _make_dataset(folder, with_metadata=False), folder)

                self.assertEqual(window._spectral_cube_axis_label(), "Cube")
                window._analysis_controller.set_sensorgram_series([0, 1, 2], [1.0, 1.1, 1.2], summary_text="test")
                x_values, _y_values = window.sensorgram_curve.getData()

                self.assertEqual(list(x_values), [0.0, 1.0, 2.0])

    def test_cursor_round_trips_through_elapsed_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with _open_window(folder) as window:
                _load_into_window(window, _make_dataset(folder, with_metadata=True), folder)

                # Nearest to cube 1's own elapsed time (9.535s), not a
                # coincidence with the raw index 1 - proves the cursor is
                # genuinely reading back through the time mapping, not
                # accidentally still index-based.
                window.sensorgram_cursor_line.setValue(9.4)
                window._analysis_controller._on_sensorgram_cursor_moved()

                self.assertEqual(window._current_spectral_cube(), 1)

    def test_current_point_marker_uses_elapsed_time_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with _open_window(folder) as window:
                _load_into_window(window, _make_dataset(folder, with_metadata=True), folder)

                window._analysis_controller.set_sensorgram_series([0, 1, 2], [1.0, 1.1, 1.2], summary_text="test")
                window.spectral_cube_slider.setValue(1)  # -> spectral_cube_index 1
                window._analysis_controller.update_current_point()
                x_values, y_values = window.sensorgram_current_point.getData()

                self.assertAlmostEqual(x_values[0], 9.535, places=3)
                self.assertAlmostEqual(y_values[0], 1.1, places=6)


if __name__ == "__main__":
    unittest.main()
