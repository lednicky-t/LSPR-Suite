"""Integration test for the acquisition-metadata GUI wiring in LSPRimaging
Evaluation's MainWindow: the Metadata section's status label after a dataset
loads, the live per-image acquired-time/comment label, and the preview/edit
dialog's round-trip fidelity for fields it doesn't expose editing for.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
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


def _make_dataset(folder: Path, *, with_metadata: bool) -> ImageDataset:
    records = [
        ImageRecord(key=ImageKey(wavelength_nm=470.0, spectral_cube_index=0), path=folder / "wl470_cube0.tif"),
        ImageRecord(key=ImageKey(wavelength_nm=480.0, spectral_cube_index=0), path=folder / "wl480_cube0.tif"),
    ]
    return ImageDataset(folder=folder, records=records, acquisition_metadata=_make_metadata() if with_metadata else None)


def _load_into_window(window: MainWindow, dataset: ImageDataset, folder: Path) -> None:
    # Mirrors what DatasetController._on_dataset_loaded sets before handing
    # off to _finish_load_dataset_from_folder - bypassed here to avoid that
    # method's own async processing-state-restore chain, which isn't what
    # this test is about.
    window._state.dataset = dataset
    window._record_map = dataset_record_map(dataset)
    window._spectral_cube_values = dataset.spectral_cube_indices
    window._wavelength_values = dataset.wavelengths_nm
    window._dataset_controller._finish_load_dataset_from_folder(folder, None)


class MetadataStatusLabelTests(unittest.TestCase):
    def test_dataset_with_metadata_updates_status_label_and_enables_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            window = MainWindow(folder, fast_startup=True)
            _load_into_window(window, _make_dataset(folder, with_metadata=True), folder)

        text = window.metadata_status_label.text()
        self.assertIn("legacy import", text)
        self.assertIn("2 timed images", text)
        self.assertIn("1 comment events", text)
        self.assertTrue(window.metadata_preview_button.isEnabled())

    def test_dataset_without_metadata_shows_placeholder_and_disables_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            window = MainWindow(folder, fast_startup=True)
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
            window = MainWindow(folder, fast_startup=True)
            _load_into_window(window, _make_dataset(folder, with_metadata=True), folder)

        text = window.metadata_current_cube_label.text()
        self.assertIn("2025-09-12", text)
        self.assertIn("Pump is not running", text)


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


if __name__ == "__main__":
    unittest.main()
