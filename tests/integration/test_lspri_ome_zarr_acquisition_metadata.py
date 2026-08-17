"""Regression test for mirroring `ImagingAcquisitionMetadata` into an OME-Zarr
export's `.zattrs` (export_ome_zarr_dataset / load_ome_zarr_dataset,
apps/LSPRi/eva/src/lspr_imaging_app/io/dataset.py).

Before this, an exported dataset carried only the `lspr` attrs group
(chunking/compression/dtype/image-tools) - the camera/illumination settings,
per-image timing, and comment events loaded via `Dataset > Metadata` were
silently dropped on export. This proves the round trip: export with metadata
loaded, reload, and get back an equivalent `ImagingAcquisitionMetadata`.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile

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
    WavelengthIlluminationSettings,
)

import lspr_imaging_app.io.dataset as dataset_module  # noqa: E402
from lspr_imaging_app.domain.models import ImageDataset, ImageKey, ImageRecord  # noqa: E402
from lspr_imaging_app.io.dataset import export_ome_zarr_dataset, load_ome_zarr_dataset  # noqa: E402

IMAGE_SIZE = 16
SPECTRAL_CUBE_COUNT = 2
WAVELENGTHS_NM = [450.0, 500.0]
_STARTED_AT_UTC = "2026-08-17T09:00:00Z"
_START_UNIX_MS = 1_755_421_200_000


def _make_metadata() -> ImagingAcquisitionMetadata:
    return ImagingAcquisitionMetadata(
        source_format="lspri_acquisition_v6_4",
        started_at_utc=_STARTED_AT_UTC,
        operator="tester",
        notes="round-trip test",
        wavelengths_nm=WAVELENGTHS_NM,
        camera_settings_by_wavelength={
            450.0: WavelengthCameraSettings(exposure_us=1000.0, gain=2.0, binning=1),
            500.0: WavelengthCameraSettings(exposure_us=1200.0, gain=2.0, binning=1),
        },
        illumination_settings_by_wavelength={
            450.0: WavelengthIlluminationSettings(settle_time_ms=50.0, current=100.0),
            500.0: WavelengthIlluminationSettings(settle_time_ms=50.0, current=110.0),
        },
        image_timings=[
            ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=450.0, acquired_at_unix_ms=_START_UNIX_MS),
            ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=500.0, acquired_at_unix_ms=_START_UNIX_MS + 100),
            ImagingCubeTiming(spectral_cube_index=1, wavelength_nm=450.0, acquired_at_unix_ms=_START_UNIX_MS + 9000),
            ImagingCubeTiming(spectral_cube_index=1, wavelength_nm=500.0, acquired_at_unix_ms=_START_UNIX_MS + 9100),
        ],
        comment_events=[ImagingCommentEvent(acquired_at_unix_ms=_START_UNIX_MS, comment="step 1")],
    )


def _make_dataset(folder: Path, *, with_metadata: bool) -> ImageDataset:
    records: list[ImageRecord] = []
    for cube_index in range(SPECTRAL_CUBE_COUNT):
        for wavelength in WAVELENGTHS_NM:
            image = np.zeros((IMAGE_SIZE, IMAGE_SIZE), dtype=np.uint16)
            path = folder / f"cube{cube_index}_wl{wavelength:.0f}.tif"
            tifffile.imwrite(str(path), image)
            records.append(ImageRecord(ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube_index), path))
    metadata = _make_metadata() if with_metadata else None
    return ImageDataset(folder=folder, records=records, acquisition_metadata=metadata)


class OmeZarrAcquisitionMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source_dir = self.root / "source"
        self.source_dir.mkdir()

    def test_acquisition_metadata_round_trips_through_export(self) -> None:
        dataset = _make_dataset(self.source_dir, with_metadata=True)
        destination = export_ome_zarr_dataset(dataset, self.root / "export", chunk_size_px=8, adaptive_workers_enabled=False)

        reloaded = load_ome_zarr_dataset(destination)

        metadata = reloaded.acquisition_metadata
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata.source_format, "lspri_acquisition_v6_4")
        self.assertEqual(metadata.started_at_utc, _STARTED_AT_UTC)
        self.assertEqual(metadata.operator, "tester")
        self.assertEqual(metadata.notes, "round-trip test")
        self.assertEqual(sorted(metadata.camera_settings_by_wavelength.keys()), WAVELENGTHS_NM)
        self.assertEqual(metadata.camera_settings_by_wavelength[450.0].exposure_us, 1000.0)
        self.assertEqual(metadata.illumination_settings_by_wavelength[500.0].current, 110.0)
        self.assertEqual(len(metadata.comment_events), 1)
        self.assertEqual(metadata.comment_events[0].comment, "step 1")

        # Per-image timings survive in full (not summarized per cube), so both
        # cube-level (sensorgram) and per-image (live label) consumers keep working.
        self.assertEqual(len(metadata.image_timings), 4)
        timing = metadata.timing_for(1, 500.0)
        self.assertIsNotNone(timing)
        self.assertEqual(timing.acquired_at_unix_ms, _START_UNIX_MS + 9100)
        earliest = metadata.earliest_timing_for_cube(1)
        self.assertIsNotNone(earliest)
        self.assertEqual(earliest.acquired_at_unix_ms, _START_UNIX_MS + 9000)

    def test_export_without_metadata_reloads_with_none(self) -> None:
        dataset = _make_dataset(self.source_dir, with_metadata=False)
        destination = export_ome_zarr_dataset(dataset, self.root / "export_no_meta", chunk_size_px=8, adaptive_workers_enabled=False)

        reloaded = load_ome_zarr_dataset(destination)

        self.assertIsNone(reloaded.acquisition_metadata)
        zarr_group = dataset_module._ome_zarr_group_cached(str(destination))
        attrs = dict(zarr_group.attrs.asdict() if hasattr(zarr_group.attrs, "asdict") else dict(zarr_group.attrs))
        self.assertNotIn(dataset_module.OME_ZARR_ACQUISITION_METADATA_KEY, attrs)

    def test_malformed_acquisition_metadata_attrs_does_not_crash_load(self) -> None:
        dataset = _make_dataset(self.source_dir, with_metadata=False)
        destination = export_ome_zarr_dataset(dataset, self.root / "export_bad_meta", chunk_size_px=8, adaptive_workers_enabled=False)

        # The cached group handle is opened read-only, so corrupt the on-disk
        # zarr v3 group metadata (zarr.json's "attributes") directly instead.
        zarr_json_path = destination / "zarr.json"
        payload = json.loads(zarr_json_path.read_text(encoding="utf-8"))
        payload["attributes"][dataset_module.OME_ZARR_ACQUISITION_METADATA_KEY] = {"not": "a valid payload"}
        zarr_json_path.write_text(json.dumps(payload), encoding="utf-8")
        dataset_module._ome_zarr_group_cached.cache_clear()
        self.addCleanup(dataset_module._ome_zarr_group_cached.cache_clear)

        reloaded = load_ome_zarr_dataset(destination)
        self.assertIsNone(reloaded.acquisition_metadata)


if __name__ == "__main__":
    unittest.main()
