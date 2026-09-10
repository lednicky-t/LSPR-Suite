"""Schema major 7 (fixed-size, pre-allocated, /rois/<roi_id>/... layout) -
see apps/LSPRi/eva/src/lspr_imaging_app/storage/measurement_export_schema7.py
and docs/measurement_backup_performance_and_crash_recovery.md's "recommended
real fix" section this implements. Covers: fresh schema-7 creation and
round-trip, in-place overwrite (no growth), non-dense cube indices, and that
reopening an existing schema-6 file is completely unaffected (regression
guard - real historical backup files are all schema 6 today)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.storage.measurement_export import ImagingMeasurementExportWriter


class Schema7CreationTests(unittest.TestCase):
    def test_new_writer_without_cube_indices_stays_schema_six(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path) as writer:
                self.assertEqual(writer._schema_major, 6)
                self.assertEqual(int(writer._handle.attrs["schema_major"]), 6)

    def test_new_writer_with_cube_indices_becomes_schema_seven(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(
                path, spectral_cube_indices=[0, 1, 2], wavelengths_nm=np.asarray([500.0, 600.0])
            ) as writer:
                self.assertEqual(writer._schema_major, 7)
                self.assertEqual(str(writer._handle.attrs["schema_version"]), "7.0")

    def test_reopening_existing_schema_six_file_ignores_cube_indices(self) -> None:
        """Passing spectral_cube_indices/wavelengths_nm when REOPENING an
        existing schema-6 file must not upgrade it - old files stay on the
        old format, no forced migration."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path):
                pass
            with ImagingMeasurementExportWriter(
                path, spectral_cube_indices=[0, 1, 2], wavelengths_nm=np.asarray([500.0, 600.0])
            ) as writer:
                self.assertEqual(writer._schema_major, 6)


class Schema7FormulaSpectrumRoundTripTests(unittest.TestCase):
    def _make_writer(self, path: Path, spectral_cube_indices: list[int]) -> ImagingMeasurementExportWriter:
        return ImagingMeasurementExportWriter(
            path, spectral_cube_indices=spectral_cube_indices, wavelengths_nm=np.asarray([500.0, 550.0, 600.0])
        )

    def test_round_trips_through_row_and_index_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with self._make_writer(path, [0, 1, 2]) as writer:
                writer.append_formula_spectrum(
                    1,
                    wavelengths_nm=np.asarray([500.0, 550.0, 600.0]),
                    formula_values=np.asarray([0.1, 0.2, 0.3]),
                    sample_mean=np.asarray([1000.0, 1100.0, 1200.0]),
                    reference_mean=np.asarray([2000.0, 2100.0, 2200.0]),
                    cube_index=1,
                    timestamp_utc_ms=500,
                    formula_key="absorbance",
                    reduction_method="mean",
                    signature_hash="hash-cube1",
                )
                row_trace = writer.formula_spectrum_row(1, 1)
                index_trace = writer.formula_spectrum_index(1)

        self.assertIsNotNone(row_trace)
        self.assertEqual(set(row_trace.by_cube.keys()), {1})
        stored_hash, methods = row_trace.by_cube[1]
        self.assertEqual(stored_hash, "hash-cube1")
        np.testing.assert_allclose(methods["mean"][0], [1000.0, 1100.0, 1200.0])
        np.testing.assert_allclose(methods["mean"][1], [2000.0, 2100.0, 2200.0])

        self.assertEqual(set(index_trace.by_cube.keys()), {1})
        self.assertEqual(index_trace.by_cube[1][0], "hash-cube1")

    def test_unwritten_cube_reads_as_none_not_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with self._make_writer(path, [0, 1, 2]) as writer:
                writer.append_formula_spectrum(
                    1,
                    wavelengths_nm=np.asarray([500.0, 550.0, 600.0]),
                    formula_values=np.asarray([0.1, 0.2, 0.3]),
                    sample_mean=np.asarray([1000.0, 1100.0, 1200.0]),
                    reference_mean=np.asarray([2000.0, 2100.0, 2200.0]),
                    cube_index=1,
                    timestamp_utc_ms=500,
                    signature_hash="hash-cube1",
                )
                self.assertIsNone(writer.formula_spectrum_row(1, 0))
                self.assertIsNone(writer.formula_spectrum_row(1, 2))
                self.assertIsNone(writer.formula_spectrum_row(1, 999))  # not in this file's cube list at all

    def test_overwrite_in_place_does_not_grow_the_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with self._make_writer(path, [0, 1, 2]) as writer:
                for signature_hash, sample_value in (("hash-v1", 100.0), ("hash-v2-recomputed", 999.0)):
                    writer.append_formula_spectrum(
                        1,
                        wavelengths_nm=np.asarray([500.0, 550.0, 600.0]),
                        formula_values=np.asarray([0.1, 0.2, 0.3]),
                        sample_mean=np.asarray([sample_value] * 3),
                        reference_mean=np.asarray([2000.0, 2100.0, 2200.0]),
                        cube_index=1,
                        timestamp_utc_ms=500,
                        signature_hash=signature_hash,
                    )
                spectra_group = writer._handle["rois"]["1"]["spectra"]
                self.assertEqual(spectra_group["signature_hash"].shape, (3,))
                row_trace = writer.formula_spectrum_row(1, 1)

        self.assertEqual(row_trace.by_cube[1][0], "hash-v2-recomputed")
        np.testing.assert_allclose(row_trace.by_cube[1][1]["mean"][0], [999.0, 999.0, 999.0])

    def test_non_dense_cube_indices_map_correctly(self) -> None:
        """Cube indices are not guaranteed dense/zero-based - writing to
        cube_index=100 (the last of 3 known cubes: 10, 45, 100) must not be
        confused with array position 100."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with self._make_writer(path, [10, 45, 100]) as writer:
                writer.append_formula_spectrum(
                    1,
                    wavelengths_nm=np.asarray([500.0, 550.0, 600.0]),
                    formula_values=np.asarray([0.1, 0.2, 0.3]),
                    sample_mean=np.asarray([7.0, 8.0, 9.0]),
                    reference_mean=np.asarray([70.0, 80.0, 90.0]),
                    cube_index=100,
                    timestamp_utc_ms=1,
                    signature_hash="hash-100",
                )
                spectra_group = writer._handle["rois"]["1"]["spectra"]
                self.assertEqual(spectra_group["signature_hash"].shape, (3,), "must not grow to accommodate index 100")
                self.assertIsNone(writer.formula_spectrum_row(1, 10))
                self.assertIsNone(writer.formula_spectrum_row(1, 45))
                row_trace = writer.formula_spectrum_row(1, 100)

        self.assertEqual(row_trace.by_cube[100][0], "hash-100")

    def test_reduced_values_by_method_all_present_without_flat_fallback(self) -> None:
        """A row with multiple reduction methods computed (the real
        "interactive single-cube preview" shape) round-trips every method,
        with no separate flat column needed."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with self._make_writer(path, [0]) as writer:
                writer.append_formula_spectrum(
                    1,
                    wavelengths_nm=np.asarray([500.0, 550.0, 600.0]),
                    formula_values=np.asarray([0.1, 0.2, 0.3]),
                    sample_mean=np.asarray([1.0, 2.0, 3.0]),
                    reference_mean=np.asarray([4.0, 5.0, 6.0]),
                    cube_index=0,
                    timestamp_utc_ms=1,
                    reduction_method="mean",
                    signature_hash="hash-0",
                    reduced_values_by_method={
                        "mean": (np.asarray([1.0, 2.0, 3.0]), np.asarray([4.0, 5.0, 6.0])),
                        "median": (np.asarray([1.5, 2.5, 3.5]), np.asarray([4.5, 5.5, 6.5])),
                    },
                )
                row_trace = writer.formula_spectrum_row(1, 0)

        self.assertEqual(set(row_trace.by_cube[0][1].keys()), {"mean", "median"})
        np.testing.assert_allclose(row_trace.by_cube[0][1]["median"][0], [1.5, 2.5, 3.5])


class Schema7SensorgramRoundTripTests(unittest.TestCase):
    def test_round_trips_through_metric_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(
                path, spectral_cube_indices=[0, 1, 2], wavelengths_nm=np.asarray([500.0])
            ) as writer:
                writer.set_sensorgram_metric(1, metric_name="peak_wavelength", formula_key="absorbance")
                writer.append_sensorgram_point(1, cube_index=1, timestamp_utc_ms=500, metric_value=555.5, signature_hash="sg-hash")
                index = writer.sensorgram_metric_index(1)

        self.assertEqual(index, {1: ("sg-hash", 555.5)})

    def test_existing_keys_are_always_empty_for_schema_seven(self) -> None:
        """Schema 7 has no file-wide dedup key-set to build - the old
        skip-a-duplicate-append optimization doesn't apply once writes are
        fixed-offset overwrites regardless of whether the value changed."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(
                path, spectral_cube_indices=[0], wavelengths_nm=np.asarray([500.0])
            ) as writer:
                writer.append_formula_spectrum(
                    1,
                    wavelengths_nm=np.asarray([500.0]),
                    formula_values=np.asarray([0.1]),
                    sample_mean=np.asarray([1.0]),
                    reference_mean=np.asarray([2.0]),
                    cube_index=0,
                    timestamp_utc_ms=1,
                    signature_hash="h",
                )
                writer.append_sensorgram_point(1, cube_index=0, timestamp_utc_ms=1, metric_value=1.0, signature_hash="h")
                self.assertEqual(writer.existing_formula_spectrum_keys(), set())
                self.assertEqual(writer.existing_sensorgram_keys(), set())


class Schema6RegressionGuardTests(unittest.TestCase):
    """A real historical measurement_backup.h5 is schema 6 - confirms this
    entire feature is purely additive, with zero behavior change for the
    existing, already-shipped format."""

    def test_schema_six_writer_unaffected_by_new_optional_params_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path) as writer:
                writer.append_formula_spectrum(
                    1,
                    wavelengths_nm=np.asarray([500.0, 600.0]),
                    formula_values=np.asarray([0.1, 0.2]),
                    sample_mean=np.asarray([1.0, 2.0]),
                    reference_mean=np.asarray([3.0, 4.0]),
                    cube_index=0,
                    timestamp_utc_ms=1,
                    signature_hash="h0",
                )
                writer.append_formula_spectrum(
                    1,
                    wavelengths_nm=np.asarray([500.0, 600.0]),
                    formula_values=np.asarray([0.3, 0.4]),
                    sample_mean=np.asarray([5.0, 6.0]),
                    reference_mean=np.asarray([7.0, 8.0]),
                    cube_index=1,
                    timestamp_utc_ms=2,
                    signature_hash="h1",
                )
                keys = writer.existing_formula_spectrum_keys()
                trace = writer.formula_spectrum_index(1)

        self.assertEqual(keys, {(1, 0, "h0"), (1, 1, "h1")})
        self.assertEqual(set(trace.by_cube.keys()), {0, 1})


if __name__ == "__main__":
    unittest.main()
