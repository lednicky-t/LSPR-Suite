"""Regression test: the OME-Zarr export must really compress when compression is on.

The export worker (`io/_zarr_export_worker.write_shard`) used to hand Blosc the tile's raw bytes. Blosc then took the
element size as 1 byte, bit-shuffle did nothing useful on 16-bit pixels, and real data came out at 100 % of raw (found
2026-10-20; `apps/LSPRi/eva/docs/zarr_chunk_planner_2026-10-19.md`). Encoding the array gives Blosc the true element
size. The stored format is unchanged, so both readers (zarr itself and the fast shard reader) must still return the
exact pixels.
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

import lspr_imaging_app.dataset.io as dataset_module  # noqa: E402
from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord  # noqa: E402
from lspr_imaging_app.dataset.io import OME_ZARR_ARRAY_DIRNAME, export_ome_zarr_dataset  # noqa: E402

SIZE = 64
CHUNK = 32  # 1024 pixels per chunk: a multiple of 8, as bit-shuffle needs
WAVELENGTHS_NM = [450.0, 500.0]


class TestExportCompresses(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        source = root / "source"
        source.mkdir()
        records = []
        self.known = {}
        for wavelength in WAVELENGTHS_NM:
            # Camera-like: a smooth level plus noise, 12-bit values in 16-bit pixels (high bits mostly constant).
            rng = np.random.default_rng(int(wavelength))
            yy, xx = np.mgrid[0:SIZE, 0:SIZE]
            image = (1500 + 3 * xx + 2 * yy + rng.normal(0, 25, (SIZE, SIZE))).astype(np.uint16)
            path = source / f"cube0_wl{wavelength:.0f}.tif"
            tifffile.imwrite(str(path), image)
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=0), path=path))
            self.known[wavelength] = image
        self.destination = export_ome_zarr_dataset(
            ImageDataset(folder=source, records=records), root / "export.ome.zarr", chunk_size_px=CHUNK, compression_enabled=True
        )
        self.raw_bytes = sum(img.nbytes for img in self.known.values())

    def test_shards_are_smaller_than_raw(self) -> None:
        shards = [p for p in (self.destination / OME_ZARR_ARRAY_DIRNAME / "c").rglob("*") if p.is_file()]
        self.assertTrue(shards)
        stored = sum(p.stat().st_size for p in shards)
        self.assertLess(stored, 0.85 * self.raw_bytes, f"stored {stored} B vs raw {self.raw_bytes} B: not compressed")

    def test_both_readers_return_the_exact_pixels(self) -> None:
        import zarr

        array = zarr.open_array(self.destination / OME_ZARR_ARRAY_DIRNAME, mode="r")
        meta = dataset_module._ome_zarr_fast_read_metadata(str(self.destination))
        self.assertIsNotNone(meta)
        for wi, wavelength in enumerate(WAVELENGTHS_NM):
            np.testing.assert_array_equal(np.asarray(array[0, wi]), self.known[wavelength])
            fast = dataset_module._fast_read_zarr_plane(meta, 0, wi)
            self.assertIsNotNone(fast)
            np.testing.assert_array_equal(fast.astype(np.uint16), self.known[wavelength])


if __name__ == "__main__":
    unittest.main()
