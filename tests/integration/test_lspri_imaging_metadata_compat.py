"""Cross-app compatibility: proves `lspr_io.read_imaging_acquisition_metadata`
(the reader LSPRimaging Evaluation's Dataset loader uses) can read back a
real `lspr_measurement` v6.4 file written by LSPRimaging Acquisition's own
`ImagingMeasurementWriter` (apps/LSPRi/acq/src/lspri_acq_app/storage/
hdf5_export.py) - not just a file this repo's own reader happens to agree
with itself about.

Belongs at the umbrella level, not inside either app's own test suite - see
test_lspri_acq_zarr_compat.py's docstring for the same reasoning applied to
image pixel data instead of setup metadata.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

ACQ_SRC = REPO_ROOT / "apps" / "LSPRi" / "acq" / "src"
if str(ACQ_SRC) not in sys.path:
    sys.path.insert(0, str(ACQ_SRC))

from lspri_acq_app.domain.models import (  # noqa: E402
    ImagingAcquisitionSettings,
    WavelengthCameraSettings as AcqWavelengthCameraSettings,
    WavelengthIlluminationSettings as AcqWavelengthIlluminationSettings,
)
from lspri_acq_app.storage.hdf5_export import ImagingMeasurementWriter  # noqa: E402

from lspr_io import is_imaging_measurement_file, read_imaging_acquisition_metadata  # noqa: E402


class ReadImagingAcquisitionMetadataCompatTests(unittest.TestCase):
    def test_reads_back_a_real_v6_4_file_written_by_lspri_acq(self) -> None:
        started_at = datetime(2026, 8, 16, 9, 0, 0, tzinfo=timezone.utc)
        settings = ImagingAcquisitionSettings(
            wavelengths_nm=[470.0, 480.0],
            exposure_us=210_000.0,
            gain=None,
            camera_settings_by_wavelength={
                470.0: AcqWavelengthCameraSettings(exposure_us=210_000.0, binning=4, resolution_width_px=1300, resolution_height_px=900),
                480.0: AcqWavelengthCameraSettings(exposure_us=220_000.0, binning=4, resolution_width_px=1300, resolution_height_px=900),
            },
            illumination_settings_by_wavelength={
                470.0: AcqWavelengthIlluminationSettings(settle_time_ms=40.0, spectrum_source="measured"),
                480.0: AcqWavelengthIlluminationSettings(settle_time_ms=45.0, spectrum_source="measured"),
            },
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "measurement.h5"
            writer = ImagingMeasurementWriter(path, experiment_name="compat-test", started_at_utc=started_at)
            writer.write_illumination_settings(settings)
            writer.write_camera_settings(settings)
            writer.append_image_cube_manifest_row(cube_index=0, timestamp_utc_ms=1_755_331_200_000, file_path="images/cube_0")
            writer.append_image_cube_manifest_row(cube_index=1, timestamp_utc_ms=1_755_331_205_000, file_path="images/cube_1")
            writer.close()

            self.assertTrue(is_imaging_measurement_file(path))
            metadata = read_imaging_acquisition_metadata(path)

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata.wavelengths_nm, [470.0, 480.0])

        camera_470 = metadata.camera_settings_by_wavelength[470.0]
        self.assertEqual(camera_470.exposure_us, 210_000.0)
        self.assertEqual(camera_470.binning, 4)
        self.assertEqual(camera_470.resolution_width_px, 1300)

        illumination_480 = metadata.illumination_settings_by_wavelength[480.0]
        self.assertEqual(illumination_480.settle_time_ms, 45.0)
        self.assertEqual(illumination_480.spectrum_source, "measured")

        # One cube-level timestamp expanded across every wavelength in that cube.
        timings_cube_0 = [t for t in metadata.image_timings if t.spectral_cube_index == 0]
        self.assertEqual({t.wavelength_nm for t in timings_cube_0}, {470.0, 480.0})
        self.assertTrue(all(t.acquired_at_unix_ms == 1_755_331_200_000 for t in timings_cube_0))

    def test_non_imaging_measurement_file_reads_as_none(self) -> None:
        # A file that just doesn't exist - the common case for "no metadata
        # available", which callers must treat as normal, not an error.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.h5"
            self.assertFalse(is_imaging_measurement_file(path))
            self.assertIsNone(read_imaging_acquisition_metadata(path))


if __name__ == "__main__":
    unittest.main()
