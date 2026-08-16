"""Read-side helper for LSPRimaging Evaluation: pull the setup metadata a
native `lspr_measurement` v6.4 file carries (illumination/camera settings,
image-cube timing) into the shared `lspr_core.ImagingAcquisitionMetadata`
shape, without also restoring the ROI/valve-label/session state that
`lspri_acq_app.storage.hdf5_export.read_imaging_session` covers - eva has no
use for those, and depending on the acquisition app's own package just to
read a handful of tables would be the wrong direction of dependency.
"""

from __future__ import annotations

from pathlib import Path

import h5py

from lspr_core import (
    SOURCE_FORMAT_LSPRI_ACQUISITION_V6_4,
    ImagingAcquisitionMetadata,
    ImagingCubeTiming,
    WavelengthCameraSettings,
    WavelengthIlluminationSettings,
)

from .hdf5 import read_root_metadata, read_string_table_dataset
from .schema import (
    LSPR_MEASUREMENT_CAMERA_SETTINGS_DATASET_NAME,
    LSPR_MEASUREMENT_ILLUMINATION_SETTINGS_DATASET_NAME,
    LSPR_MEASUREMENT_IMAGE_CUBE_MANIFEST_DATASET_NAME,
    LSPR_MEASUREMENT_SCHEMA_NAME,
)


def is_imaging_measurement_file(path: Path) -> bool:
    """Whether *path* looks like a native imaging measurement/session file:
    a valid `lspr_measurement` HDF5 file with the v6.4 imaging groups. Safe
    to call on any path - returns False (never raises) for anything that
    isn't a readable HDF5 file, isn't this schema, or predates v6.4.
    """
    if not path.is_file():
        return False
    try:
        with h5py.File(path, "r") as handle:
            if str(handle.attrs.get("schema_name", "")) != LSPR_MEASUREMENT_SCHEMA_NAME:
                return False
            metadata = handle.get("metadata")
            if metadata is None:
                return False
            return (
                LSPR_MEASUREMENT_CAMERA_SETTINGS_DATASET_NAME in metadata
                or LSPR_MEASUREMENT_ILLUMINATION_SETTINGS_DATASET_NAME in metadata
                or LSPR_MEASUREMENT_IMAGE_CUBE_MANIFEST_DATASET_NAME in metadata
            )
    except OSError:
        return False


def read_imaging_acquisition_metadata(path: Path) -> ImagingAcquisitionMetadata | None:
    """Read *path* (a native v6.4 `lspr_measurement` HDF5 file) into the
    shared `ImagingAcquisitionMetadata` shape. Returns None if *path* isn't
    a recognized imaging measurement file rather than raising - callers use
    this for auto-detection (try native, fall back to a legacy importer).

    The native format only records one timestamp per completed cube
    (`metadata/image_cube_manifest`), not per wavelength - that single
    timestamp is assigned to every wavelength in the cube, which is coarser
    than what a `measureing_times.csv` legacy import can provide (real
    per-image timestamps) but is all the native format captures.
    """
    if not is_imaging_measurement_file(path):
        return None

    with h5py.File(path, "r") as handle:
        root_metadata = read_root_metadata(handle)
        metadata = handle["metadata"]

        wavelengths_nm: list[float] = []
        camera_settings_by_wavelength: dict[float, WavelengthCameraSettings] = {}
        _columns, camera_rows = read_string_table_dataset(metadata.get(LSPR_MEASUREMENT_CAMERA_SETTINGS_DATASET_NAME))
        for row in camera_rows:
            wavelength_nm = float(row[0])
            wavelengths_nm.append(wavelength_nm)
            camera_settings_by_wavelength[wavelength_nm] = WavelengthCameraSettings(
                exposure_us=float(row[1]),
                gain=float(row[2]) if row[2] else None,
                binning=int(row[3]) if row[3] else 1,
                resolution_width_px=int(row[4]) if row[4] else None,
                resolution_height_px=int(row[5]) if row[5] else None,
                crop_x_px=int(row[6]) if row[6] else None,
                crop_y_px=int(row[7]) if row[7] else None,
                crop_width_px=int(row[8]) if row[8] else None,
                crop_height_px=int(row[9]) if row[9] else None,
                saving_mode=row[10] or "all_frames",
            )

        illumination_settings_by_wavelength: dict[float, WavelengthIlluminationSettings] = {}
        _columns, illumination_rows = read_string_table_dataset(
            metadata.get(LSPR_MEASUREMENT_ILLUMINATION_SETTINGS_DATASET_NAME)
        )
        for row in illumination_rows:
            wavelength_nm = float(row[0])
            if wavelength_nm not in wavelengths_nm:
                wavelengths_nm.append(wavelength_nm)
            illumination_settings_by_wavelength[wavelength_nm] = WavelengthIlluminationSettings(
                settle_time_ms=float(row[1]) if row[1] else None,
                current=float(row[2]) if row[2] else None,
                spectrum_source=row[3] or "default_file",
            )

        image_timings: list[ImagingCubeTiming] = []
        _columns, manifest_rows = read_string_table_dataset(
            metadata.get(LSPR_MEASUREMENT_IMAGE_CUBE_MANIFEST_DATASET_NAME)
        )
        for row in manifest_rows:
            cube_index = int(row[0])
            timestamp_utc_ms = int(float(row[1]))
            for wavelength_nm in wavelengths_nm:
                image_timings.append(
                    ImagingCubeTiming(
                        spectral_cube_index=cube_index,
                        wavelength_nm=wavelength_nm,
                        acquired_at_unix_ms=timestamp_utc_ms,
                    )
                )

        return ImagingAcquisitionMetadata(
            source_format=SOURCE_FORMAT_LSPRI_ACQUISITION_V6_4,
            started_at_utc=root_metadata.get("started_at_utc"),
            wavelengths_nm=sorted(wavelengths_nm),
            camera_settings_by_wavelength=camera_settings_by_wavelength,
            illumination_settings_by_wavelength=illumination_settings_by_wavelength,
            image_timings=image_timings,
            comment_events=[],
        )
