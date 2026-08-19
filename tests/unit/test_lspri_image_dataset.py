"""Coverage for ImageDataset's computed properties (domain/models.py).

Most of domain/models.py is plain dataclass field storage, not worth testing
on its own. ImageDataset is the exception: its properties do real work - the
home/folder fallback that keeps app state out of a raw data folder it doesn't
own, dedup+sort of wavelengths/cube indices across records, and matching
several source_format spellings to decide OME-Zarr vs. image-stack behavior.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import ImageDataset, ImageKey, ImageRecord


def _record(wavelength_nm: float, spectral_cube_index: int) -> ImageRecord:
    key = ImageKey(wavelength_nm=wavelength_nm, spectral_cube_index=spectral_cube_index)
    return ImageRecord(key=key, path=Path(f"cube{spectral_cube_index}_wl{wavelength_nm}.tif"))


class TestImageDatasetHome(unittest.TestCase):
    def test_home_falls_back_to_folder_when_home_folder_is_unset(self) -> None:
        dataset = ImageDataset(folder=Path("/data/experiment"), records=[])
        self.assertEqual(dataset.home, Path("/data/experiment"))

    def test_home_uses_home_folder_when_set(self) -> None:
        # This is the case where load_dataset discovered the actual data one
        # level below the folder the user pointed at - app state must be
        # written to home_folder, not into the raw data folder.
        dataset = ImageDataset(folder=Path("/data/experiment/raw"), records=[], home_folder=Path("/data/experiment"))
        self.assertEqual(dataset.home, Path("/data/experiment"))


class TestImageDatasetDerivedLists(unittest.TestCase):
    def test_wavelengths_nm_is_sorted_and_deduplicated(self) -> None:
        records = [_record(650.0, 0), _record(450.0, 0), _record(450.0, 1), _record(550.0, 0)]
        dataset = ImageDataset(folder=Path("/data"), records=records)
        self.assertEqual(dataset.wavelengths_nm, [450.0, 550.0, 650.0])

    def test_spectral_cube_indices_is_sorted_and_deduplicated(self) -> None:
        records = [_record(450.0, 2), _record(450.0, 0), _record(550.0, 2), _record(450.0, 1)]
        dataset = ImageDataset(folder=Path("/data"), records=records)
        self.assertEqual(dataset.spectral_cube_indices, [0, 1, 2])

    def test_empty_dataset_has_empty_derived_lists(self) -> None:
        dataset = ImageDataset(folder=Path("/data"), records=[])
        self.assertEqual(dataset.wavelengths_nm, [])
        self.assertEqual(dataset.spectral_cube_indices, [])


class TestImageDatasetFormat(unittest.TestCase):
    def test_default_source_format_is_an_image_stack(self) -> None:
        dataset = ImageDataset(folder=Path("/data"), records=[])
        self.assertFalse(dataset.is_ome_zarr)
        self.assertTrue(dataset.is_image_stack)
        self.assertEqual(dataset.format_label, "ImageStack")

    def test_recognized_ome_zarr_spellings_are_all_treated_as_ome_zarr(self) -> None:
        for spelling in ("ome_zarr", "ome-zarr", "zarr", "OME_ZARR", "Zarr"):
            with self.subTest(spelling=spelling):
                dataset = ImageDataset(folder=Path("/data"), records=[], source_format=spelling)
                self.assertTrue(dataset.is_ome_zarr, f"{spelling!r} should be recognized as OME-Zarr")
                self.assertFalse(dataset.is_image_stack)
                self.assertEqual(dataset.format_label, "OME-Zarr")

    def test_unrecognized_source_format_is_treated_as_an_image_stack(self) -> None:
        dataset = ImageDataset(folder=Path("/data"), records=[], source_format="some_future_format")
        self.assertFalse(dataset.is_ome_zarr)
        self.assertTrue(dataset.is_image_stack)


if __name__ == "__main__":
    unittest.main()
