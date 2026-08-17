"""Regression tests for the dataset loader's one-level-deep folder search
(io/dataset.py's discover_dataset_candidates/load_dataset).

Real datasets often keep the acquisition metadata files (measureing_times.csv
/metaData.txt) directly in a parent folder, with the actual TIFF stack and/or
OME-Zarr export living one level below it in a differently-named subfolder.
Before this, load_dataset required pointing directly at that subfolder. Now
it also accepts the parent: if the folder doesn't directly hold a dataset, it
searches immediate subfolders, auto-loads a single candidate, and returns a
DatasetLoadChoice (for the GUI to prompt) when more than one is found -
metadata is always resolved from the folder the caller actually passed in,
regardless of which subfolder the images end up in.
"""

from __future__ import annotations

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

from lspr_core import SOURCE_FORMAT_LEGACY_MEASURING_TIMES_CSV  # noqa: E402

from lspr_imaging_app.domain.models import ImageDataset, ImageKey, ImageRecord  # noqa: E402
from lspr_imaging_app.io.dataset import (  # noqa: E402
    DatasetLoadChoice,
    discover_dataset_candidates,
    export_ome_zarr_dataset,
    load_dataset,
    summarize_dataset_candidate,
)

# Small synthetic subset in the real legacy format - not real measurement data.
_META_DATA_TXT = """User: testuser
Date and time: 12.09.2025 12:59:11
Camera
BinningH=4
BinningV=4
MultiplExpTime=1.0
Averaging=1
ImageWidth=1300
ImageHeight=900
XoffsetImage=0
YoffsetImage=0
Measurement
FilterSetTime=100
Wavelength and exposure time camera
defined wl:real wl:exposure time
470:470:210
480:480:220
eof
"""

_MEASURING_TIMES_CSV = (
    "Image;Measuring time [ms];Measuring time [s];Note pump plan\n"
    "imLCTFatWL470Frame0;144;0;Pump is not running\n"
    "imLCTFatWL480Frame0;472;0;Pump is not running\n"
)


def _write_legacy_metadata_files(folder: Path) -> None:
    (folder / "measureing_times.csv").write_text(_MEASURING_TIMES_CSV, encoding="utf-8")
    (folder / "metaData.txt").write_text(_META_DATA_TXT, encoding="utf-8")


def _write_tiff_stack(folder: Path, *, cube_count: int = 1, wavelengths=(470.0, 480.0)) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for cube_index in range(cube_count):
        for wavelength in wavelengths:
            tifffile.imwrite(
                str(folder / f"imLCTFatWL{wavelength:.0f}Frame{cube_index}.tif"),
                np.zeros((8, 8), dtype=np.uint16),
            )


def _write_ome_zarr_export(folder: Path, destination_name: str = "export") -> Path:
    # Source TIFFs live in their own throwaway temp dir, not under `folder`
    # itself - `folder` is what the test points the loader at, and a stray
    # TIFF-stack subfolder there would register as its own discovered
    # candidate. export_ome_zarr_dataset copies pixel data into `folder /
    # destination_name`, so the source can be discarded right after.
    with tempfile.TemporaryDirectory() as source_dir_str:
        source = Path(source_dir_str)
        _write_tiff_stack(source)
        records = [
            ImageRecord(ImageKey(wavelength_nm=wl, spectral_cube_index=0), source / f"imLCTFatWL{wl:.0f}Frame0.tif")
            for wl in (470.0, 480.0)
        ]
        dataset = ImageDataset(folder=source, records=records)
        destination = export_ome_zarr_dataset(dataset, folder / destination_name, chunk_size_px=8, adaptive_workers_enabled=False)
    return destination


class DatasetFolderDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_pointing_directly_at_tiff_folder_is_unchanged(self) -> None:
        folder = self.root / "images"
        _write_tiff_stack(folder)
        result = load_dataset(folder)
        self.assertIsInstance(result, ImageDataset)
        self.assertEqual(result.folder, folder)
        self.assertEqual(result.home, folder)
        self.assertFalse(result.is_ome_zarr)

    def test_pointing_directly_at_ome_zarr_folder_is_unchanged(self) -> None:
        destination = _write_ome_zarr_export(self.root)
        result = load_dataset(destination)
        self.assertIsInstance(result, ImageDataset)
        self.assertEqual(result.folder, destination)
        self.assertEqual(result.home, destination)
        self.assertTrue(result.is_ome_zarr)

    def test_single_tiff_candidate_one_level_down_is_auto_loaded_with_parent_metadata(self) -> None:
        parent = self.root / "dataset"
        parent.mkdir()
        _write_legacy_metadata_files(parent)
        images_dir = parent / "images"
        _write_tiff_stack(images_dir)

        result = load_dataset(parent)

        self.assertIsInstance(result, ImageDataset)
        self.assertEqual(result.folder, images_dir)
        # The dataset's "home" (where analysis/, sessions, ROI/mask state, and
        # the metadata sidecar get saved) stays the parent folder the caller
        # pointed at, not the discovered images/ subfolder - so this app's own
        # output never lands mixed in with the raw TIFFs.
        self.assertEqual(result.home, parent)
        self.assertNotEqual(result.home, result.folder)
        self.assertIsNotNone(result.acquisition_metadata)
        self.assertEqual(result.acquisition_metadata.source_format, SOURCE_FORMAT_LEGACY_MEASURING_TIMES_CSV)

    def test_single_ome_zarr_candidate_one_level_down_is_auto_loaded(self) -> None:
        parent = self.root / "dataset"
        parent.mkdir()
        destination = _write_ome_zarr_export(parent, "myexport.ome.zarr")

        result = load_dataset(parent)

        self.assertIsInstance(result, ImageDataset)
        self.assertEqual(result.folder, destination)
        self.assertEqual(result.home, parent)
        self.assertNotEqual(result.home, result.folder)
        self.assertTrue(result.is_ome_zarr)

    def test_both_formats_present_returns_a_choice(self) -> None:
        parent = self.root / "dataset"
        parent.mkdir()
        _write_legacy_metadata_files(parent)
        images_dir = parent / "images"
        _write_tiff_stack(images_dir)
        zarr_destination = _write_ome_zarr_export(parent, "myexport.ome.zarr")

        result = load_dataset(parent)

        self.assertIsInstance(result, DatasetLoadChoice)
        self.assertEqual(result.parent_folder, parent)
        self.assertEqual({candidate.folder for candidate in result.candidates}, {images_dir, zarr_destination})
        self.assertIsNotNone(result.acquisition_metadata)
        self.assertEqual(result.acquisition_metadata.source_format, SOURCE_FORMAT_LEGACY_MEASURING_TIMES_CSV)

    def test_neither_format_found_raises_with_helpful_message(self) -> None:
        parent = self.root / "empty_dataset"
        parent.mkdir()
        (parent / "notes.txt").write_text("not a dataset", encoding="utf-8")

        with self.assertRaises(FileNotFoundError) as ctx:
            load_dataset(parent)
        self.assertIn("immediate subfolders", str(ctx.exception))

    def test_summarize_dataset_candidate_reports_counts_and_size(self) -> None:
        folder = self.root / "images"
        _write_tiff_stack(folder, cube_count=2, wavelengths=(470.0, 480.0))
        candidates = discover_dataset_candidates(folder)
        self.assertEqual(len(candidates), 1)

        summary = summarize_dataset_candidate(candidates[0])

        self.assertEqual(summary.source_format, "image_stack")
        self.assertEqual(summary.image_count, 4)
        self.assertEqual(summary.spectral_cube_count, 2)
        self.assertEqual(summary.wavelength_count, 2)
        self.assertGreater(summary.total_bytes, 0)


if __name__ == "__main__":
    unittest.main()
