"""Regression tests for `CompactImageTimings` / `compact_dataset_image_timings`
/ `rehydrated_acquisition_metadata` (domain/models.py) - the mitigation for a
PyQt6-sip native crash-on-close that correlates with a large legacy CSV
metadata import leaving 8,500+ live `lspr_core.ImagingCubeTiming` pydantic
instances referenced by `dataset.acquisition_metadata.image_timings` for the
whole session. See qthreadpool_zarr_crash_investigation.md's sibling
memory note (lspri_pyqt6_sip_crash_on_close) for the full incident history.

Pure `domain/models.py` logic, no Qt needed - unlike most of this app's
gui/ tests, this file does not need a QApplication bootstrapped first.
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

from lspr_core import ImagingAcquisitionMetadata, ImagingCubeTiming

from lspr_imaging_app.domain.models import (
    CompactImageTimings,
    ImageDataset,
    compact_dataset_image_timings,
    rehydrated_acquisition_metadata,
)


def _make_timings(cube_count: int = 3, wavelengths_per_cube: int = 4) -> list[ImagingCubeTiming]:
    return [
        ImagingCubeTiming(
            spectral_cube_index=cube,
            wavelength_nm=float(500 + wl * 10),
            acquired_at_unix_ms=1_000 + cube * 100 + wl,
        )
        for cube in range(cube_count)
        for wl in range(wavelengths_per_cube)
    ]


def _make_dataset(timings: list[ImagingCubeTiming] | None = None) -> ImageDataset:
    metadata = ImagingAcquisitionMetadata(
        source_format="legacy_measuring_times_csv",
        image_timings=timings if timings is not None else _make_timings(),
    )
    return ImageDataset(folder=Path("."), records=[], acquisition_metadata=metadata)


class CompactImageTimingsRoundTripTests(unittest.TestCase):
    def test_from_timings_to_timings_round_trips_losslessly(self) -> None:
        timings = _make_timings()
        compact = CompactImageTimings.from_timings(timings)
        rebuilt = compact.to_timings()

        original_set = {(t.spectral_cube_index, t.wavelength_nm, t.acquired_at_unix_ms) for t in timings}
        rebuilt_set = {(t.spectral_cube_index, t.wavelength_nm, t.acquired_at_unix_ms) for t in rebuilt}
        self.assertEqual(original_set, rebuilt_set)

    def test_len_matches_original_list_length(self) -> None:
        timings = _make_timings(cube_count=5, wavelengths_per_cube=7)
        compact = CompactImageTimings.from_timings(timings)
        self.assertEqual(len(compact), 35)
        self.assertEqual(len(compact), len(timings))

    def test_earliest_and_latest_by_cube_match_hand_computed_expectation(self) -> None:
        timings = [
            ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=500.0, acquired_at_unix_ms=300),
            ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=510.0, acquired_at_unix_ms=100),
            ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=520.0, acquired_at_unix_ms=200),
            ImagingCubeTiming(spectral_cube_index=1, wavelength_nm=500.0, acquired_at_unix_ms=999),
        ]
        compact = CompactImageTimings.from_timings(timings)
        self.assertEqual(compact.earliest_ms_by_cube, {0: 100, 1: 999})
        self.assertEqual(compact.latest_ms_by_cube, {0: 300, 1: 999})

    def test_per_frame_ms_keyed_by_cube_and_wavelength(self) -> None:
        timings = _make_timings(cube_count=2, wavelengths_per_cube=2)
        compact = CompactImageTimings.from_timings(timings)
        self.assertEqual(compact.per_frame_ms[(0, 500.0)], 1000)
        self.assertEqual(compact.per_frame_ms[(1, 510.0)], 1101)

    def test_empty_input_gives_empty_everything(self) -> None:
        compact = CompactImageTimings.from_timings([])
        self.assertEqual(len(compact), 0)
        self.assertEqual(compact.earliest_ms_by_cube, {})
        self.assertEqual(compact.latest_ms_by_cube, {})


class CompactDatasetImageTimingsTests(unittest.TestCase):
    def test_builds_compact_form_and_empties_the_original_list(self) -> None:
        dataset = _make_dataset()
        compact = compact_dataset_image_timings(dataset)

        self.assertEqual(len(compact), 12)
        self.assertEqual(dataset.acquisition_metadata.image_timings, [])
        self.assertIs(dataset.compact_image_timings, compact)

    def test_second_call_is_a_cache_read_not_a_rebuild(self) -> None:
        dataset = _make_dataset()
        first = compact_dataset_image_timings(dataset)
        second = compact_dataset_image_timings(dataset)
        self.assertIs(first, second)

    def test_no_dataset_metadata_returns_empty_compact_form(self) -> None:
        dataset = ImageDataset(folder=Path("."), records=[], acquisition_metadata=None)
        compact = compact_dataset_image_timings(dataset)
        self.assertEqual(len(compact), 0)

    def test_metadata_with_no_timings_returns_empty_compact_form(self) -> None:
        metadata = ImagingAcquisitionMetadata(source_format="lspri_acquisition_v6_4", image_timings=[])
        dataset = ImageDataset(folder=Path("."), records=[], acquisition_metadata=metadata)
        compact = compact_dataset_image_timings(dataset)
        self.assertEqual(len(compact), 0)

    def test_reassigning_acquisition_metadata_with_fresh_timings_recompacts(self) -> None:
        """A dataset whose metadata is later reassigned (e.g. a re-import)
        must be recompacted on the next call, not stuck serving the first
        import's stale compact form - the lazy design's whole point is that
        it re-derives from whatever the *current* acquisition_metadata is,
        not a one-time flag."""
        dataset = _make_dataset(_make_timings(cube_count=1, wavelengths_per_cube=2))
        first = compact_dataset_image_timings(dataset)
        self.assertEqual(len(first), 2)

        dataset.acquisition_metadata = ImagingAcquisitionMetadata(
            source_format="legacy_measuring_times_csv",
            image_timings=_make_timings(cube_count=3, wavelengths_per_cube=5),
        )
        second = compact_dataset_image_timings(dataset)
        self.assertEqual(len(second), 15)

    def test_lazy_build_works_even_when_acquisition_metadata_was_set_directly(self) -> None:
        """The exact scenario that broke the original eager-call-site
        design: an ImageDataset constructed with acquisition_metadata
        already populated (a test fixture, a dataset loader that doesn't
        know about this mechanism at all) must still compact correctly on
        first access - no assignment-site cooperation required."""
        dataset = ImageDataset(
            folder=Path("."),
            records=[],
            acquisition_metadata=ImagingAcquisitionMetadata(
                source_format="lspri_acquisition_v6_4", image_timings=_make_timings()
            ),
        )
        self.assertIsNone(dataset.compact_image_timings)  # never touched yet
        compact = compact_dataset_image_timings(dataset)
        self.assertEqual(len(compact), 12)


class RehydratedAcquisitionMetadataTests(unittest.TestCase):
    def test_none_dataset_metadata_returns_none(self) -> None:
        dataset = ImageDataset(folder=Path("."), records=[], acquisition_metadata=None)
        self.assertIsNone(rehydrated_acquisition_metadata(dataset))

    def test_uncompacted_metadata_is_returned_unchanged(self) -> None:
        dataset = _make_dataset()
        rehydrated = rehydrated_acquisition_metadata(dataset)
        self.assertIs(rehydrated, dataset.acquisition_metadata)
        self.assertEqual(len(rehydrated.image_timings), 12)

    def test_compacted_metadata_is_rebuilt_losslessly_without_mutating_the_dataset(self) -> None:
        dataset = _make_dataset()
        original_timings = list(dataset.acquisition_metadata.image_timings)
        compact_dataset_image_timings(dataset)
        self.assertEqual(dataset.acquisition_metadata.image_timings, [])  # confirms it's actually compacted

        rehydrated = rehydrated_acquisition_metadata(dataset)
        self.assertEqual(len(rehydrated.image_timings), 12)
        rehydrated_set = {(t.spectral_cube_index, t.wavelength_nm, t.acquired_at_unix_ms) for t in rehydrated.image_timings}
        original_set = {(t.spectral_cube_index, t.wavelength_nm, t.acquired_at_unix_ms) for t in original_timings}
        self.assertEqual(rehydrated_set, original_set)

        # The live dataset's own acquisition_metadata must stay compacted -
        # rehydration is transient, never stored back.
        self.assertEqual(dataset.acquisition_metadata.image_timings, [])

    def test_other_metadata_fields_survive_rehydration_unchanged(self) -> None:
        dataset = _make_dataset()
        dataset.acquisition_metadata.operator = "tester"
        dataset.acquisition_metadata.notes = "some notes"
        compact_dataset_image_timings(dataset)

        rehydrated = rehydrated_acquisition_metadata(dataset)
        self.assertEqual(rehydrated.operator, "tester")
        self.assertEqual(rehydrated.notes, "some notes")


if __name__ == "__main__":
    unittest.main()
