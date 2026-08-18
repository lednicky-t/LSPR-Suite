"""Regression test: the fast shard-file reader (_fast_read_zarr_plane,
io/dataset.py) must verify the CRC32C checksum it reads from the shard's
trailer against the index bytes it parses, not just read the checksum and
ignore it.

Before this fix, a corrupted/torn shard index (bit rot, an interrupted write)
would still be parsed as if it were valid, silently returning wrong pixel
data through the "fast path" instead of falling back to the standard
(correct) zarr array read - exactly the kind of silent-corruption failure
mode the function's own docstring says it exists to avoid ("falls back to
the standard zarr array read rather than risk returning a subtly wrong
image").
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

import lspr_imaging_app.io.dataset as dataset_module  # noqa: E402
from lspr_imaging_app.domain.models import ImageDataset, ImageKey, ImageRecord  # noqa: E402
from lspr_imaging_app.io.dataset import OME_ZARR_ARRAY_DIRNAME, export_ome_zarr_dataset  # noqa: E402

IMAGE_SIZE = 16  # matches chunk_size_px below -> exactly one inner chunk per plane
WAVELENGTHS_NM = [450.0, 500.0]


class TestFastShardReaderVerifiesCrc(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source_dir = self.root / "source"
        self.source_dir.mkdir()

        records: list[ImageRecord] = []
        self.known: dict[tuple[int, float], np.ndarray] = {}
        for wavelength in WAVELENGTHS_NM:
            rng = np.random.default_rng(int(wavelength))
            image = rng.integers(0, 4096, size=(IMAGE_SIZE, IMAGE_SIZE), dtype=np.uint16)
            path = self.source_dir / f"cube0_wl{wavelength:.0f}.tif"
            tifffile.imwrite(str(path), image)
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=0), path=path))
            self.known[(0, wavelength)] = image
        dataset = ImageDataset(folder=self.source_dir, records=records)

        # compression_enabled=False -> raw uncompressed tiles, and
        # chunk_size_px == IMAGE_SIZE -> exactly one inner chunk per plane,
        # so the shard's on-disk layout is as simple as possible to corrupt
        # by hand below.
        self.destination = export_ome_zarr_dataset(
            dataset, self.root / "export.ome.zarr", chunk_size_px=IMAGE_SIZE, compression_enabled=False
        )
        self.meta = dataset_module._ome_zarr_fast_read_metadata(str(self.destination))
        self.assertIsNotNone(self.meta)

        self.shard_path = self.destination / OME_ZARR_ARRAY_DIRNAME / "c" / "0" / "0" / "0" / "0"
        self.assertTrue(self.shard_path.exists())

    def test_fast_path_reads_correct_data_before_corruption(self) -> None:
        plane = dataset_module._fast_read_zarr_plane(self.meta, 0, 0)
        self.assertIsNotNone(plane)
        np.testing.assert_array_equal(plane.astype(np.uint16), self.known[(0, WAVELENGTHS_NM[0])])

    def test_fast_path_refuses_a_shard_with_a_corrupted_index(self) -> None:
        # One inner chunk -> index is 1 * 2 * 8 = 16 bytes, followed by a
        # 4-byte CRC32C trailer. Flip one byte inside the index (not the
        # CRC) so the stored checksum no longer matches.
        index_len = 1 * 2 * 8
        trailer_len = index_len + 4
        data = bytearray(self.shard_path.read_bytes())
        corrupt_at = len(data) - trailer_len  # first byte of the index
        data[corrupt_at] ^= 0xFF
        self.shard_path.write_bytes(bytes(data))

        plane = dataset_module._fast_read_zarr_plane(self.meta, 0, 0)
        self.assertIsNone(plane, "a shard with a corrupted index must be rejected, not silently misread")


if __name__ == "__main__":
    unittest.main()
