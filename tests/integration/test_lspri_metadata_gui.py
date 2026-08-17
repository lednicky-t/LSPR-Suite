"""Integration test for the acquisition-metadata GUI wiring in LSPRimaging
Evaluation's MainWindow: the Metadata section's status label after a dataset
loads, the live per-image acquired-time/comment label, and the preview/edit
dialog's round-trip fidelity for fields it doesn't expose editing for.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - Qt
# objects get built at import time in some of those modules.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_core import (  # noqa: E402
    ImagingAcquisitionMetadata,
    ImagingCommentEvent,
    ImagingCubeTiming,
    WavelengthCameraSettings,
)

from lspr_imaging_app.domain.models import ImageDataset, ImageKey, ImageRecord  # noqa: E402
from lspr_imaging_app.gui.main_window import MainWindow  # noqa: E402
from lspr_imaging_app.gui.metadata_edit_dialog import MetadataEditDialog  # noqa: E402
from lspr_imaging_app.io.dataset import dataset_record_map  # noqa: E402


def _make_metadata() -> ImagingAcquisitionMetadata:
    return ImagingAcquisitionMetadata(
        source_format="legacy_measuring_times_csv",
        started_at_utc="2025-09-12T12:59:11Z",
        operator="testuser",
        wavelengths_nm=[470.0, 480.0],
        camera_settings_by_wavelength={
            # Realistic: a real import always populates every declared
            # wavelength uniformly (see legacy_metadata.py's
            # _build_camera_settings), never a partial subset.
            470.0: WavelengthCameraSettings(exposure_us=210_000.0, binning=4, crop_x_px=12, crop_y_px=34),
            480.0: WavelengthCameraSettings(exposure_us=220_000.0, binning=4, crop_x_px=12, crop_y_px=34),
        },
        image_timings=[
            ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=470.0, acquired_at_unix_ms=1_757_681_951_144),
            ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=480.0, acquired_at_unix_ms=1_757_681_951_472),
        ],
        comment_events=[ImagingCommentEvent(acquired_at_unix_ms=1_757_681_951_000, comment="Pump is not running")],
    )


def _make_relative_only_metadata() -> ImagingAcquisitionMetadata:
    """A CSV-only legacy import (no metaData.txt, so no absolute
    started_at_utc) - acquired_at_unix_ms values here are elapsed-since-start
    offsets, not real Unix timestamps (see
    legacy_metadata.import_legacy_imaging_metadata)."""
    return ImagingAcquisitionMetadata(
        source_format="legacy_measuring_times_csv",
        wavelengths_nm=[470.0, 480.0],
        image_timings=[
            ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=470.0, acquired_at_unix_ms=144),
            ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=480.0, acquired_at_unix_ms=472),
        ],
        comment_events=[ImagingCommentEvent(acquired_at_unix_ms=0, comment="Pump is not running")],
    )


def _make_dataset(folder: Path, *, with_metadata: bool) -> ImageDataset:
    records = [
        ImageRecord(key=ImageKey(wavelength_nm=470.0, spectral_cube_index=0), path=folder / "wl470_cube0.tif"),
        ImageRecord(key=ImageKey(wavelength_nm=480.0, spectral_cube_index=0), path=folder / "wl480_cube0.tif"),
    ]
    return ImageDataset(folder=folder, records=records, acquisition_metadata=_make_metadata() if with_metadata else None)


def _load_into_window(window: MainWindow, dataset: ImageDataset, folder: Path) -> None:
    # Sets exactly the state the metadata-status wiring under test actually
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
    window._update_metadata_status_labels(dataset)


@contextmanager
def _open_window(folder: Path):
    """MainWindow construction/teardown, matching test_main_window_state.py's
    close()+deleteLater() convention - a window owns a pyqtgraph PlotWidget
    (native paint resources), so leaving many unclosed across a full
    test-suite run accumulates real OS-level resources rather than just
    Python objects, and can crash Qt itself later in an unrelated test."""
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


class MetadataStatusLabelTests(unittest.TestCase):
    def test_dataset_with_metadata_updates_status_label_and_enables_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with _open_window(folder) as window:
                _load_into_window(window, _make_dataset(folder, with_metadata=True), folder)

                text = window.metadata_status_label.text()
                self.assertIn("legacy import", text)
                self.assertIn("2 timed images", text)
                self.assertIn("1 comment events", text)
                self.assertTrue(window.metadata_preview_button.isEnabled())

    def test_dataset_without_metadata_shows_placeholder_and_disables_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with _open_window(folder) as window:
                _load_into_window(window, _make_dataset(folder, with_metadata=False), folder)

                self.assertEqual(window.metadata_status_label.text(), "No metadata loaded")
                self.assertFalse(window.metadata_preview_button.isEnabled())

    def test_current_cube_label_shows_acquired_time_and_active_comment(self) -> None:
        # Sliders default to index 0 on a freshly loaded dataset, which maps
        # to (spectral_cube_index=0, wavelength_nm=470.0) here - the first
        # image_timings entry, whose comment_at() resolves to the one
        # comment event (it's before both image timestamps).
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with _open_window(folder) as window:
                _load_into_window(window, _make_dataset(folder, with_metadata=True), folder)

                text = window.metadata_current_cube_label.text()
                self.assertIn("2025-09-12", text)
                self.assertIn("Pump is not running", text)

    def test_current_cube_label_shows_elapsed_time_not_a_fake_date_when_relative_only(self) -> None:
        # No started_at_utc (a CSV-only legacy import) - acquired_at_unix_ms
        # is an elapsed-ms offset, not a real Unix timestamp, so this must
        # not be rendered as if it were a calendar date (e.g. a misleading
        # "1970-01-01").
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with _open_window(folder) as window:
                dataset = ImageDataset(
                    folder=folder,
                    records=[
                        ImageRecord(key=ImageKey(wavelength_nm=470.0, spectral_cube_index=0), path=folder / "wl470_cube0.tif"),
                        ImageRecord(key=ImageKey(wavelength_nm=480.0, spectral_cube_index=0), path=folder / "wl480_cube0.tif"),
                    ],
                    acquisition_metadata=_make_relative_only_metadata(),
                )
                _load_into_window(window, dataset, folder)

                text = window.metadata_current_cube_label.text()
                self.assertIn("since start", text)
                self.assertIn("Pump is not running", text)
                self.assertNotIn("1970", text)


class MetadataEditDialogRoundTripTests(unittest.TestCase):
    def test_unedited_fields_survive_the_round_trip(self) -> None:
        metadata = _make_metadata()
        dialog = MetadataEditDialog(metadata, parent=None)
        updated = dialog.result_metadata()

        # crop_x_px/crop_y_px aren't shown in the edit table at all - must
        # still come through unchanged, not silently reset to None.
        self.assertEqual(updated.camera_settings_by_wavelength, metadata.camera_settings_by_wavelength)
        self.assertEqual(len(updated.image_timings), len(metadata.image_timings))
        self.assertEqual(updated.comment_events, metadata.comment_events)

    def test_editing_operator_field_is_reflected(self) -> None:
        metadata = _make_metadata()
        dialog = MetadataEditDialog(metadata, parent=None)
        dialog._operator_edit.setText("new-operator")
        updated = dialog.result_metadata()
        self.assertEqual(updated.operator, "new-operator")

    def test_relative_only_comment_events_survive_the_round_trip(self) -> None:
        # Regression guard: the dialog's comment table is editable, so its
        # displayed text must round-trip through _parse_time exactly for
        # relative-only metadata too - not just be displayed differently.
        # Before this, formatting a relative offset as if it were a calendar
        # date/time and then re-parsing it with the calendar-date format
        # would silently drop the row (result_metadata() skips unparseable
        # rows), losing the comment on the next save.
        metadata = _make_relative_only_metadata()
        dialog = MetadataEditDialog(metadata, parent=None)
        updated = dialog.result_metadata()
        self.assertEqual(updated.comment_events, metadata.comment_events)


if __name__ == "__main__":
    unittest.main()
