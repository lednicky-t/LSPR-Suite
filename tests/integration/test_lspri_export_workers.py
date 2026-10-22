"""The OME-Zarr export's worker count (`dataset/io.py`, `lspr_core.perf`): it may change the speed, never the files.

* The same dataset exported with one worker, three workers and the automatic count gives byte-identical shards, index files
  and metadata (the rule in the root CLAUDE.md: tuning changes scheduling, never results).
* The user's choice (Preferences: a fixed number) is honoured and reported in the export statistics.
* Free memory limits the pool: a machine with almost none runs one worker, and still writes the right files.

Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch. A tiny dataset keeps the three exports fast; the tuner itself is
covered on simulated machines in `tests/unit/test_lspr_core_perf_tuner.py`.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_core.perf import PerfPreferences, PerfRuntime, ProfileStore, SystemProfile, set_runtime
    from lspr_imaging_app.dataset.export_stats import ExportStats
    from lspr_imaging_app.dataset.io import export_ome_zarr_dataset
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

CUBES, WAVELENGTHS_NM = 3, [450.0, 500.0, 550.0, 600.0]
GIB = 1024**3
TUNE = PerfPreferences(mode="auto")  # "Tune automatically" (the default mode is Standard)


def tree_digest(root: Path) -> dict[str, str]:
    """Every file below `root` -> its SHA-256 (relative POSIX path as key)."""
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(root.rglob("*")) if path.is_file()}


class ExportWorkerCountTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        records = []
        for cube in range(CUBES):
            for wavelength in WAVELENGTHS_NM:
                rng = np.random.default_rng(int(cube * 1000 + wavelength))
                path = self.root / "src" / f"c{cube}_w{wavelength:.0f}.tif"
                tifffile.imwrite(str(path), rng.integers(1, 4096, size=(96, 128), dtype=np.uint16))
                records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
        self.dataset = ImageDataset(folder=self.root / "src", records=records)
        previous = set_runtime(None)
        self.addCleanup(set_runtime, previous)

    def _export(
        self, name: str, preferences: PerfPreferences, *, free_gib: float = 8.0, tune: bool = True, store: ProfileStore | None = None
    ) -> tuple[Path, ExportStats]:
        machine = SystemProfile(8, 4, 16 * GIB, int(free_gib * GIB), None, "test")
        set_runtime(PerfRuntime(preferences, store=store, probe=lambda: machine))
        stats = ExportStats()
        destination = export_ome_zarr_dataset(
            self.dataset, self.root / name, chunk_size_px=32, compression_enabled=True, shard_mode="per_image",
            adaptive_workers_enabled=tune, export_stats=stats,
        )
        return destination, stats

    def test_one_three_and_automatic_workers_write_identical_files(self) -> None:
        one, stats_one = self._export("one", PerfPreferences(mode="manual", manual_workers=1))
        three, stats_three = self._export("three", PerfPreferences(mode="manual", manual_workers=3))
        auto, stats_auto = self._export("auto", TUNE)
        standard, stats_standard = self._export("standard", PerfPreferences())
        self.assertEqual((stats_one.workers, stats_three.workers, stats_standard.workers), (1, 3, 7))
        self.assertEqual(stats_standard.worker_mode, "standard")
        self.assertEqual((stats_one.worker_mode, stats_three.worker_mode), ("manual", "manual"))
        self.assertGreaterEqual(stats_auto.workers, 1)
        reference = tree_digest(one)
        self.assertTrue(reference)
        self.assertEqual(tree_digest(three), reference)
        self.assertEqual(tree_digest(auto), reference)
        self.assertEqual(tree_digest(standard), reference)

    def test_a_job_this_small_is_not_tuned_even_with_tuning_on_and_says_so(self) -> None:
        _, stats = self._export("small", TUNE)
        self.assertEqual(stats.worker_mode, "fixed", "12 planes are far too few to pay back a search")
        self.assertEqual(stats.planes, CUBES * len(WAVELENGTHS_NM))
        self.assertIn("Used", stats.describe())

    def test_almost_no_free_memory_runs_one_worker_and_the_files_are_still_right(self) -> None:
        tight, stats = self._export("tight", PerfPreferences(), free_gib=0.2)
        self.assertEqual(stats.workers, 1)
        reference, _ = self._export("reference", PerfPreferences(mode="manual", manual_workers=2))
        self.assertEqual(tree_digest(tight), tree_digest(reference))

    def test_standard_mode_uses_a_count_that_tuning_saved(self) -> None:
        store = ProfileStore(self.root / "perf.json")
        machine = SystemProfile(8, 4, 16 * GIB, 8 * GIB, None, "test")
        from lspr_core.perf.system_profile import probe_destination

        store.record(machine.fingerprint(), "export.ome_zarr", probe_destination(self.root).key(), workers=2, rate=30.0)
        saved, stats = self._export("saved", PerfPreferences(), store=store)
        self.assertEqual((stats.workers, stats.worker_mode), (2, "saved"))
        reference, _ = self._export("reference", PerfPreferences(mode="manual", manual_workers=1))
        self.assertEqual(tree_digest(saved), tree_digest(reference))

    def test_adaptive_workers_disabled_keeps_the_starting_count(self) -> None:
        _, stats = self._export("pinned", TUNE, tune=False)
        self.assertEqual(stats.worker_mode, "fixed")
        self.assertEqual(stats.workers, 7, "every core but the one kept free")

    def test_low_priority_workers_still_write_the_same_files(self) -> None:
        low, _ = self._export("low", PerfPreferences(mode="manual", manual_workers=2, low_priority=True))
        normal, _ = self._export("normal", PerfPreferences(mode="manual", manual_workers=2, low_priority=False))
        self.assertEqual(tree_digest(low), tree_digest(normal))


if __name__ == "__main__":
    unittest.main()
