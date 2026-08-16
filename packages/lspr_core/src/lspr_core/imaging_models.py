from __future__ import annotations

from pydantic import BaseModel, ConfigDict

SOURCE_FORMAT_LSPRI_ACQUISITION_V6_4 = "lspri_acquisition_v6_4"
SOURCE_FORMAT_LEGACY_MEASURING_TIMES_CSV = "legacy_measuring_times_csv"


class WavelengthCameraSettings(BaseModel):
    """Per-wavelength camera configuration for one imaging acquisition.

    Shape matches `lspr_measurement` schema v6.4's `metadata/camera_settings`
    table (see `packages/lspr_io/src/lspr_io/schema.py`,
    `LSPR_MEASUREMENT_CAMERA_SETTINGS_COLUMNS`) and LSPRimaging Acquisition's
    own `WavelengthCameraSettings` dataclass - kept here as the shared shape
    a reader (LSPRimaging Evaluation) can target without depending on the
    acquisition app's package.
    """

    model_config = ConfigDict(extra="ignore")

    exposure_us: float
    gain: float | None = None
    binning: int = 1
    resolution_width_px: int | None = None
    resolution_height_px: int | None = None
    crop_x_px: int | None = None
    crop_y_px: int | None = None
    crop_width_px: int | None = None
    crop_height_px: int | None = None
    saving_mode: str = "all_frames"


class WavelengthIlluminationSettings(BaseModel):
    """Per-wavelength illumination configuration for one imaging acquisition.

    Shape matches `lspr_measurement` schema v6.4's
    `metadata/illumination_settings` table. `current` (LED current) is
    nullable because it depends on which LED controller ends up paired with
    the illumination source - not every source has one (e.g. a VariSpec LCTF
    on a fixed broadband lamp has no controllable current at all).
    """

    model_config = ConfigDict(extra="ignore")

    settle_time_ms: float | None = None
    current: float | None = None
    spectrum_source: str = "default_file"


class ImagingCubeTiming(BaseModel):
    """When one (spectral_cube_index, wavelength_nm) image was acquired.

    Per-image granularity, not per-cube: this is a strict superset of the
    native v6.4 format's `image_cube_manifest` table (one row per completed
    cube) - reading a native file assigns that cube's single timestamp to
    every wavelength in it, while a legacy `measureing_times.csv` import
    keeps its real, distinct per-image timestamps (which include capture
    jitter the native cube-level timestamp can't represent). `wavelength_nm`/
    `spectral_cube_index` match `lspr_imaging_app.domain.models.ImageKey`'s
    field names so a timing entry joins directly against an `ImageRecord`.
    """

    model_config = ConfigDict(extra="ignore")

    spectral_cube_index: int
    wavelength_nm: float
    acquired_at_unix_ms: int


class ImagingCommentEvent(BaseModel):
    """One entry in a sparse comment/annotation transition log.

    Only present when the active comment changed - not one row per image.
    Mirrors singleLSPR acquisition's own `experiment_control_runtime` event
    log (absolute timestamp + event, joined to dense per-image timestamps at
    read time by "latest transition at or before this image's time") rather
    than a legacy CSV's per-row repetition of the same string across every
    image captured while a pump-plan step was active.
    """

    model_config = ConfigDict(extra="ignore")

    acquired_at_unix_ms: int
    comment: str


class ImagingAcquisitionMetadata(BaseModel):
    """Camera/illumination setup and per-image timing for one imaging
    dataset, in one shape regardless of whether it came from a native
    `lspr_measurement` v6.4 HDF5 file (written by LSPRimaging Acquisition) or
    an older manual-workflow export (`measureing_times.csv` +
    `metaData.txt`). `source_format` records which; everything else has the
    same meaning either way, so LSPRimaging Evaluation's Dataset loader only
    ever deals with one representation.
    """

    model_config = ConfigDict(extra="ignore")

    source_format: str
    started_at_utc: str | None = None
    wavelengths_nm: list[float] = []
    camera_settings_by_wavelength: dict[float, WavelengthCameraSettings] = {}
    illumination_settings_by_wavelength: dict[float, WavelengthIlluminationSettings] = {}
    image_timings: list[ImagingCubeTiming] = []
    comment_events: list[ImagingCommentEvent] = []

    def comment_at(self, acquired_at_unix_ms: int) -> str | None:
        """The comment active at *acquired_at_unix_ms* - the latest
        `comment_events` entry at or before that time, or None if it's
        before every recorded transition (or there are none)."""
        active: str | None = None
        for event in sorted(self.comment_events, key=lambda e: e.acquired_at_unix_ms):
            if event.acquired_at_unix_ms > acquired_at_unix_ms:
                break
            active = event.comment
        return active
