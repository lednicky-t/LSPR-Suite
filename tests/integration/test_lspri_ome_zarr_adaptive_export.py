"""Regression test for adaptive worker tuning in the OME-Zarr export coordinator
(export_ome_zarr_dataset, apps/LSPRi/eva/src/lspr_imaging_app/io/dataset.py).

Proves the mid-export ProcessPoolExecutor pool-swap (triggered when the measured
I/O-wait fraction crosses a threshold) changes only execution parallelism, never
the exported pixel data -- correctness must hold regardless of whether, or how
many times, a flip happens during export.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import lspr_imaging_app.io.dataset as dataset_module  # noqa: E402
from lspr_imaging_app.domain.models import ImageDataset, ImageKey, ImageRecord  # noqa: E402
from lspr_imaging_app.io.dataset import export_ome_zarr_dataset, load_image_array, load_ome_zarr_dataset  # noqa: E402

IMAGE_SIZE = 256  # px, uint16 -> ~131KB raw per plane
SPECTRAL_CUBE_COUNT = 3
WAVELENGTHS_NM = [450.0, 500.0, 550.0, 600.0, 650.0, 700.0]  # 3 x 6 = 18 planes, ~2.4MB raw total


def _make_dataset(folder: Path) -> tuple[ImageDataset, dict[tuple[int, float], np.ndarray]]:
    records: list[ImageRecord] = []
    known: dict[tuple[int, float], np.ndarray] = {}
    for cube_index in range(SPECTRAL_CUBE_COUNT):
        for wavelength in WAVELENGTHS_NM:
            rng = np.random.default_rng(int(cube_index * 1000 + wavelength))
            image = rng.integers(0, 4096, size=(IMAGE_SIZE, IMAGE_SIZE), dtype=np.uint16)
            path = folder / f"cube{cube_index}_wl{wavelength:.0f}.tif"
            tifffile.imwrite(str(path), image)
            key = ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube_index)
            records.append(ImageRecord(key=key, path=path))
            known[(cube_index, wavelength)] = image
    dataset = ImageDataset(folder=folder, records=records)
    return dataset, known


def _assert_round_trips(destination: Path, known: dict[tuple[int, float], np.ndarray]) -> None:
    reloaded = load_ome_zarr_dataset(destination)
    if len(reloaded.records) != len(known):
        raise AssertionError(f"expected {len(known)} records, got {len(reloaded.records)}")
    for record in reloaded.records:
        expected = known[(int(record.key.spectral_cube_index), float(record.key.wavelength_nm))]
        read_back = load_image_array(str(record.path))
        np.testing.assert_array_equal(read_back.astype(np.uint16), expected)


class AdaptiveOmeZarrExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source_dir = self.root / "source"
        self.source_dir.mkdir()
        self.dataset, self.known = _make_dataset(self.source_dir)

    def test_adaptive_disabled_is_a_correct_baseline(self) -> None:
        destination = export_ome_zarr_dataset(
            self.dataset,
            self.root / "export_off",
            chunk_size_px=32,
            compression_enabled=True,
            shard_mode="per_image",
            adaptive_workers_enabled=False,
        )
        _assert_round_trips(destination, self.known)

    def test_forced_flip_mid_export_does_not_corrupt_output(self) -> None:
        # Force a flip on the very first window crossing regardless of real disk
        # speed on the test machine (avoids flakiness): any nonzero I/O time
        # exceeds a 0.0 threshold. Also pin cpu_count so there's always headroom
        # to flip up (a real machine with cpu_count >= n_tasks would otherwise
        # start at worker_count == n_tasks with nowhere higher to go).
        original_flip_up = dataset_module.ADAPTIVE_IO_FLIP_UP
        dataset_module.ADAPTIVE_IO_FLIP_UP = 0.0
        self.addCleanup(setattr, dataset_module, "ADAPTIVE_IO_FLIP_UP", original_flip_up)

        flip_events: list[str] = []

        def _progress_callback(percent: int, text: str) -> None:
            if text.startswith("ADAPTIVE_FLIP: "):
                flip_events.append(text)

        with mock.patch("os.cpu_count", return_value=4):
            destination = export_ome_zarr_dataset(
                self.dataset,
                self.root / "export_on",
                chunk_size_px=32,
                compression_enabled=False,  # keeps bytes_written == raw size, so the 1MB window threshold is predictable
                shard_mode="per_image",
                adaptive_workers_enabled=True,
                adaptive_batch_mb=1,  # smallest allowed value (floored at 1MB, see batch_bytes_threshold)
                progress_callback=_progress_callback,
            )
        self.assertTrue(flip_events, "expected at least one adaptive worker-count flip during this export")
        _assert_round_trips(destination, self.known)


if __name__ == "__main__":
    unittest.main()
