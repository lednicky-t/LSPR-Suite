"""Cross-app compatibility: proves LSPRimaging Evaluation's own OME-Zarr
reader can load a dataset written by LSPRimaging Acquisition's
OmeZarrCubeWriter (apps/LSPRi/acq/src/lspri_acq_app/storage/image_writer.py).

Belongs at the umbrella level, not inside either app's own test suite -
this is specifically about the integration point between two otherwise
decoupled apps, not either app's internal logic. See the storage-format
benchmark and lspri_acq_build_log.md for why round-trip compatibility with
eva (not just "the bytes look like valid zarr") is the actual bar - eva's
reader is what a scientist will actually use to analyze what this app
writes.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

ACQ_SRC = REPO_ROOT / "apps" / "LSPRi" / "acq" / "src"
if str(ACQ_SRC) not in sys.path:
    sys.path.insert(0, str(ACQ_SRC))

from lspri_acq_app.domain.models import Frame, SpectralCube  # noqa: E402
from lspri_acq_app.storage.image_writer import OmeZarrCubeWriter  # noqa: E402

from lspr_imaging_app.io.dataset import load_image_array, load_ome_zarr_dataset  # noqa: E402


def _make_cube(cube_index: int, wavelengths_nm: list[float], height: int, width: int) -> SpectralCube:
    now = datetime.now(timezone.utc)
    frames = [
        Frame(
            image=np.random.default_rng(cube_index * 100 + i).integers(0, 1024, size=(height, width), dtype=np.uint16),
            wavelength_nm=wavelength,
            acquired_at=now,
        )
        for i, wavelength in enumerate(wavelengths_nm)
    ]
    return SpectralCube(frames=frames, cube_index=cube_index, started_at=now, completed_at=now)


class EvaCanReadAcqZarrOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.destination = Path(self._tmp.name) / "live_dataset"

    def _write_dataset(self, *, compression: str, shard_mode: str) -> tuple[OmeZarrCubeWriter, list[SpectralCube]]:
        wavelengths = [450.0, 500.0, 550.0]
        writer = OmeZarrCubeWriter(
            self.destination,
            wavelengths_nm=wavelengths,
            image_shape=(48, 64),
            compression=compression,
            shard_mode=shard_mode,
        )
        cubes = [_make_cube(0, wavelengths, height=48, width=64), _make_cube(1, wavelengths, height=48, width=64)]
        for cube in cubes:
            writer.write_cube(cube)
        return writer, cubes

    def _assert_eva_reads_back_correctly(self, writer: OmeZarrCubeWriter, cubes: list[SpectralCube]) -> None:
        dataset = load_ome_zarr_dataset(writer._destination)
        self.assertEqual(len(dataset.records), len(cubes) * len(cubes[0].frames))
        self.assertEqual(sorted(dataset.spectral_cube_indices), [c.cube_index for c in cubes])
        self.assertEqual(sorted(dataset.wavelengths_nm), sorted(f.wavelength_nm for f in cubes[0].frames))

        by_key = {(record.key.spectral_cube_index, record.key.wavelength_nm): record for record in dataset.records}
        for cube in cubes:
            for frame in cube.frames:
                record = by_key[(cube.cube_index, frame.wavelength_nm)]
                read_back = load_image_array(str(record.path))
                np.testing.assert_array_equal(read_back.astype(np.uint16), frame.image)

    def test_uncompressed_per_spectral_cube_shards(self) -> None:
        writer, cubes = self._write_dataset(compression="none", shard_mode="per_spectral_cube")
        self._assert_eva_reads_back_correctly(writer, cubes)

    def test_lz4_compressed_per_spectral_cube_shards(self) -> None:
        writer, cubes = self._write_dataset(compression="lz4", shard_mode="per_spectral_cube")
        self._assert_eva_reads_back_correctly(writer, cubes)

    def test_lz4_compressed_per_image_shards(self) -> None:
        writer, cubes = self._write_dataset(compression="lz4", shard_mode="per_image")
        self._assert_eva_reads_back_correctly(writer, cubes)


if __name__ == "__main__":
    unittest.main()
