"""Regression test: an OME-Zarr export folder that never finished (crashed or
was cancelled partway through) must not be silently loadable as if it were a
complete dataset.

export_ome_zarr_dataset (io/dataset.py) creates the zarr array - with its
full, final shape - before writing any pixel data, and only writes the app's
own "lspr" attrs key at the very end, once every shard has been written
successfully. Before this fix, load_ome_zarr_dataset treated a missing "lspr"
key as "just use defaults" (empty dict) and happily returned a full-shaped
ImageDataset anyway, so an interrupted export looked like a valid, complete
dataset - the missing planes just read back as zero/NaN fill with no error
anywhere in the chain.
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

from lspr_imaging_app.domain.models import ImageDataset, ImageKey, ImageRecord  # noqa: E402
from lspr_imaging_app.io.dataset import (  # noqa: E402
    OME_ZARR_LSPR_KEY,
    export_ome_zarr_dataset,
    load_ome_zarr_dataset,
)

IMAGE_SIZE = 32
WAVELENGTHS_NM = [450.0, 500.0]


def _make_dataset(folder: Path) -> ImageDataset:
    records: list[ImageRecord] = []
    for cube_index in range(2):
        for wavelength in WAVELENGTHS_NM:
            rng = np.random.default_rng(int(cube_index * 1000 + wavelength))
            image = rng.integers(0, 4096, size=(IMAGE_SIZE, IMAGE_SIZE), dtype=np.uint16)
            path = folder / f"cube{cube_index}_wl{wavelength:.0f}.tif"
            tifffile.imwrite(str(path), image)
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube_index), path=path))
    return ImageDataset(folder=folder, records=records)


class TestIncompleteOmeZarrExportIsRejected(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source_dir = self.root / "source"
        self.source_dir.mkdir()
        self.dataset = _make_dataset(self.source_dir)

    def test_a_finished_export_still_loads_normally(self) -> None:
        destination = export_ome_zarr_dataset(
            self.dataset, self.root / "export.ome.zarr", chunk_size_px=16, compression_enabled=False
        )
        reloaded = load_ome_zarr_dataset(destination)
        self.assertEqual(len(reloaded.records), 4)

    def test_export_missing_completion_metadata_is_rejected(self) -> None:
        destination = export_ome_zarr_dataset(
            self.dataset, self.root / "export.ome.zarr", chunk_size_px=16, compression_enabled=False
        )

        # Simulate a crash/cancel that happened just before the exporter
        # writes its final "lspr" completion marker - the array and its full
        # shape already exist on disk at this point, same as a real
        # interrupted export.
        import zarr
        from zarr.storage import LocalStore

        group = zarr.open_group(store=LocalStore(str(destination)), mode="r+")
        del group.attrs[OME_ZARR_LSPR_KEY]

        with self.assertRaises(FileNotFoundError):
            load_ome_zarr_dataset(destination)


if __name__ == "__main__":
    unittest.main()
