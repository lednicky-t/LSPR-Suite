"""Regression tests for `cube_interval_stats`/`CubeIntervalStats`
(`dataset/model.py`, `dataset/module.py`) - the "typical time between
cubes" summary shown in the rewrite's "Experimental plan" Workflow section
(2026-09-30, revised same day: "cube interval" not "sampling interval",
mean+std not median).

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring for why.

Pure `dataset/model.py`/`dataset/module.py` logic, no Qt needed.
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

try:
    from lspr_core import ImagingAcquisitionMetadata, ImagingCubeTiming

    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.model import (
        CompactImageTimings,
        ImageDataset,
        ImageKey,
        ImageRecord,
        cube_interval_stats,
    )
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


def _cube_start_timings(start_ms_by_cube: dict[int, int]) -> list[ImagingCubeTiming]:
    """One timing per cube, at the given start time - `cube_interval_stats`
    only ever reads `earliest_ms_by_cube`, so a single timing per cube is
    enough to exercise it without a second, unrelated "which frame in the
    cube" concern."""
    return [
        ImagingCubeTiming(spectral_cube_index=cube, wavelength_nm=500.0, acquired_at_unix_ms=start_ms)
        for cube, start_ms in start_ms_by_cube.items()
    ]


class CubeIntervalStatsPureFunctionTests(unittest.TestCase):
    def test_regular_cadence_gives_the_exact_mean_with_zero_spread(self) -> None:
        # Cubes 0/1/2/3 starting exactly 8s apart.
        compact = CompactImageTimings.from_timings(_cube_start_timings({0: 0, 1: 8_000, 2: 16_000, 3: 24_000}))
        stats = cube_interval_stats(compact)
        self.assertEqual(stats.mean_s, 8.0)
        self.assertEqual(stats.min_s, 8.0)
        self.assertEqual(stats.max_s, 8.0)
        self.assertEqual(stats.std_s, 0.0)
        self.assertEqual(stats.n_gaps, 3)

    def test_one_atypically_long_gap_pulls_the_mean_but_is_visible_in_max_and_std(self) -> None:
        """Mean, not median, was the maintainer's explicit call (2026-09-30)
        specifically so an irregular gap shows up rather than being
        silently absorbed - this pins that it actually does."""
        compact = CompactImageTimings.from_timings(
            _cube_start_timings({0: 0, 1: 8_000, 2: 16_000, 3: 76_000, 4: 84_000})
        )
        stats = cube_interval_stats(compact)
        # Gaps: 8, 8, 60, 8 -> mean pulled up from the "typical" 8s.
        self.assertAlmostEqual(stats.mean_s, 21.0)
        self.assertEqual(stats.min_s, 8.0)
        self.assertEqual(stats.max_s, 60.0)
        self.assertGreater(stats.std_s, 0.0)

    def test_fewer_than_two_cubes_returns_none(self) -> None:
        self.assertIsNone(cube_interval_stats(CompactImageTimings.from_timings(_cube_start_timings({0: 0}))))
        self.assertIsNone(cube_interval_stats(CompactImageTimings.from_timings([])))

    def test_exactly_one_gap_has_no_std(self) -> None:
        """A spread needs at least two data points - one gap is a mean with
        nothing to spread."""
        compact = CompactImageTimings.from_timings(_cube_start_timings({0: 0, 1: 8_000}))
        stats = cube_interval_stats(compact)
        self.assertEqual(stats.mean_s, 8.0)
        self.assertEqual(stats.n_gaps, 1)
        self.assertIsNone(stats.std_s)

    def test_cube_index_order_is_used_not_insertion_order(self) -> None:
        """Cubes must be sorted by index before differencing - an
        out-of-order `image_timings` list (not guaranteed sorted by the
        importer) must not produce a bogus negative-then-huge gap."""
        compact = CompactImageTimings.from_timings(_cube_start_timings({2: 16_000, 0: 0, 1: 8_000}))
        stats = cube_interval_stats(compact)
        self.assertEqual(stats.mean_s, 8.0)
        self.assertEqual(stats.max_s, 8.0)


class DatasetModuleCubeIntervalStatsTests(unittest.TestCase):
    """`DatasetModule.cube_interval_stats()` - the query surface
    `panels/workflow/dataset_experimental_plan.py` actually calls."""

    def _dataset_with_timings(self, start_ms_by_cube: dict[int, int]) -> ImageDataset:
        metadata = ImagingAcquisitionMetadata(
            source_format="legacy_measuring_times_csv",
            image_timings=_cube_start_timings(start_ms_by_cube),
        )
        records = [
            ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=cube), path=Path(f"c{cube}.tif"))
            for cube in start_ms_by_cube
        ]
        return ImageDataset(folder=Path("."), records=records, acquisition_metadata=metadata)

    def test_none_with_no_dataset_loaded(self) -> None:
        self.assertIsNone(DatasetModule().cube_interval_stats())

    def test_none_with_no_acquisition_metadata(self) -> None:
        module = DatasetModule()
        module.load_dataset(ImageDataset(folder=Path("."), records=[]))
        self.assertIsNone(module.cube_interval_stats())

    def test_real_value_after_loading_timed_metadata(self) -> None:
        module = DatasetModule()
        module.load_dataset(self._dataset_with_timings({0: 0, 1: 8_000, 2: 16_000}))
        stats = module.cube_interval_stats()
        self.assertEqual(stats.mean_s, 8.0)
        self.assertEqual(stats.n_gaps, 2)

    def test_survives_compaction_a_second_call_still_works(self) -> None:
        """`compact_dataset_image_timings` empties `metadata.image_timings`
        as a side effect the first time it runs (see its own docstring) -
        a second query must still return the same answer from the
        already-compacted cache, not silently go back to `None`."""
        module = DatasetModule()
        module.load_dataset(self._dataset_with_timings({0: 0, 1: 8_000, 2: 16_000}))
        first = module.cube_interval_stats()
        second = module.cube_interval_stats()
        self.assertEqual(first, second)
        self.assertEqual(first.mean_s, 8.0)


if __name__ == "__main__":
    unittest.main()
