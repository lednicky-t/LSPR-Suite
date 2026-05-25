from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from lspr_io import (
    read_root_metadata,
    standard_measurement_metadata,
    validate_measurement_file,
    validate_measurement_metadata,
    write_measurement_manifest_metadata,
    write_measurement_root_metadata,
)


class IoMetadataTests(unittest.TestCase):
    def test_standard_measurement_metadata_contains_required_fields(self) -> None:
        started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        metadata = standard_measurement_metadata(
            created_by="tester",
            started_at_utc=started_at,
            app_name="Test App",
            app_version="9.8.7",
            experiment_name="demo",
        )

        self.assertEqual(metadata["schema_name"], "lspr_measurement")
        self.assertEqual(metadata["schema_major"], 4)
        self.assertEqual(metadata["schema_minor"], 0)
        self.assertEqual(metadata["app_name"], "Test App")
        self.assertEqual(metadata["app_version"], "9.8.7")
        self.assertEqual(metadata["started_at_utc"], "2026-01-02T03:04:05Z")
        self.assertEqual(metadata["experiment_name"], "demo")

    def test_root_metadata_round_trip(self) -> None:
        started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "measurement.h5"
            with h5py.File(path, "w") as handle:
                write_measurement_root_metadata(
                    handle,
                    schema_name="lspr_measurement",
                    schema_version="4.0",
                    schema_major=4,
                    schema_minor=0,
                    format_name="experiment_run",
                    format_version=4,
                    app_name="LSPR Suite",
                    app_version="0.1.0",
                    created_by="tester",
                    created_at_utc="2026-01-02T03:04:04Z",
                    started_at_utc=started_at,
                    experiment_name="demo",
                )

            with h5py.File(path, "r") as handle:
                metadata = read_root_metadata(handle)

        self.assertEqual(metadata["schema_name"], "lspr_measurement")
        self.assertEqual(metadata["schema_version"], "4.0")
        self.assertEqual(metadata["schema_major"], 4)
        self.assertEqual(metadata["schema_minor"], 0)
        self.assertEqual(metadata["format_name"], "experiment_run")
        self.assertEqual(metadata["format_version"], 4)
        self.assertEqual(metadata["app_name"], "LSPR Suite")
        self.assertEqual(metadata["app_version"], "0.1.0")
        self.assertEqual(metadata["created_by"], "tester")
        self.assertEqual(metadata["created_at_utc"], "2026-01-02T03:04:04Z")
        self.assertEqual(metadata["started_at_utc"], "2026-01-02T03:04:05Z")

    def test_root_metadata_accepts_iso_string_started_at(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "measurement.h5"
            with h5py.File(path, "w") as handle:
                write_measurement_root_metadata(
                    handle,
                    schema_name="lspr_measurement",
                    schema_version="4.0",
                    schema_major=4,
                    schema_minor=0,
                    format_name="experiment_run",
                    format_version=4,
                    app_name="LSPR Suite",
                    app_version="0.1.0",
                    created_by="tester",
                    created_at_utc="2026-01-02T03:04:04Z",
                    started_at_utc="2026-01-02T03:04:05Z",
                    experiment_name="demo",
                )

            with h5py.File(path, "r") as handle:
                metadata = read_root_metadata(handle)

        self.assertEqual(metadata["started_at_utc"], "2026-01-02T03:04:05Z")

    def test_manifest_metadata_round_trip_and_validation(self) -> None:
        started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "measurement.h5"
            with h5py.File(path, "w") as handle:
                write_measurement_root_metadata(
                    handle,
                    schema_name="lspr_measurement",
                    schema_version="4.0",
                    schema_major=4,
                    schema_minor=0,
                    format_name="experiment_run",
                    format_version=4,
                    app_name="LSPR Suite",
                    app_version="0.1.0",
                    created_by="tester",
                    created_at_utc="2026-01-02T03:04:04Z",
                    started_at_utc=started_at,
                    experiment_name="demo",
                )
                manifest = handle.create_group("manifest")
                write_measurement_manifest_metadata(
                    manifest,
                    schema_name="lspr_measurement",
                    schema_version="4.0",
                    schema_major=4,
                    schema_minor=0,
                    format_name="experiment_run",
                    format_version=4,
                    app_name="LSPR Suite",
                    app_version="0.1.0",
                    created_by="tester",
                    created_at_utc="2026-01-02T03:04:04Z",
                    started_at_utc=started_at,
                    experiment_name="demo",
                    storage_compression_enabled=True,
                    storage_compression_filter="gzip",
                    storage_compression_level=4,
                )
                data = handle.create_group("data")
                data.create_dataset("wavelengths", data=np.asarray([610.0, 620.0], dtype=np.float64))
                data.create_dataset(
                    "raw_spectra_extinction",
                    shape=(0, 2),
                    maxshape=(None, 2),
                    dtype=np.float32,
                    chunks=True,
                )
                data.create_dataset("time_series", shape=(0,), maxshape=(None,), dtype=np.float64, chunks=True)
                data.create_dataset("peak_position_nm", shape=(0,), maxshape=(None,), dtype=np.float64, chunks=True)
                axes = handle.create_group("axes")
                axes.create_dataset("wavelengths_nm", data=np.asarray([610.0, 620.0], dtype=np.float64))
                metadata = handle.create_group("metadata")
                metadata.create_dataset(
                    "switch_solution_map",
                    shape=(0, 2),
                    maxshape=(None, 2),
                    dtype=h5py.string_dtype(encoding="utf-8"),
                    chunks=True,
                )
                plans = handle.create_group("plans")
                plan_columns = [
                    "step",
                    "duration_s",
                    "start_s",
                    "end_s",
                    "color",
                    "valve",
                    "switch_position",
                    "description",
                ]
                plan_columns.extend(
                    [
                        f"ch{index + 1}_flow_ul_min"
                        for index in range(6)
                    ]
                )
                plan_columns.extend(
                    [
                        f"ch{index + 1}_direction"
                        for index in range(6)
                    ]
                )
                plan_columns.extend(
                    [
                        f"ch{index + 1}_tube_mm"
                        for index in range(6)
                    ]
                )
                empty_plan = np.empty((0, len(plan_columns)), dtype=h5py.string_dtype(encoding="utf-8"))
                plans.create_dataset("experiment_plan", data=empty_plan, chunks=True)
                plans["experiment_plan"].attrs["columns"] = np.asarray(plan_columns, dtype=h5py.string_dtype(encoding="utf-8"))
                plans.create_dataset("experiment_plan_tmp", data=empty_plan, chunks=True)
                plans["experiment_plan_tmp"].attrs["columns"] = np.asarray(plan_columns, dtype=h5py.string_dtype(encoding="utf-8"))
                runs = handle.create_group("runs")
                flow_columns = [
                    "t_ms",
                    "event",
                    "step_index",
                    "elapsed_in_step_ms",
                    "pump_running",
                    "valve_position",
                    "switch_position",
                    "pump_connected",
                    "valve_connected",
                    "switch_connected",
                    "status",
                    "ch1_flow_ul_min",
                    "ch1_direction",
                    "ch1_tube_mm",
                    "ch2_flow_ul_min",
                    "ch2_direction",
                    "ch2_tube_mm",
                    "ch3_flow_ul_min",
                    "ch3_direction",
                    "ch3_tube_mm",
                    "ch4_flow_ul_min",
                    "ch4_direction",
                    "ch4_tube_mm",
                    "ch5_flow_ul_min",
                    "ch5_direction",
                    "ch5_tube_mm",
                    "ch6_flow_ul_min",
                    "ch6_direction",
                    "ch6_tube_mm",
                ]
                flow_table = np.empty((0, len(flow_columns)), dtype=h5py.string_dtype(encoding="utf-8"))
                runs.create_dataset("flow_events", data=flow_table, chunks=True)
                runs["flow_events"].attrs["columns"] = np.asarray(flow_columns, dtype=h5py.string_dtype(encoding="utf-8"))
                runs["flow_events"].attrs["schema_version_added"] = "3.0"

            with h5py.File(path, "r") as handle:
                manifest = handle["manifest"]
                manifest_attrs = read_root_metadata(manifest)
                validation = validate_measurement_file(handle)

        self.assertEqual(manifest_attrs["schema_version"], "4.0")
        self.assertIn("export_user", manifest_attrs)
        self.assertTrue(bool(manifest_attrs["storage_compression_enabled"]))
        self.assertEqual(manifest_attrs["storage_compression_filter"], "gzip")
        self.assertTrue(validation.is_valid)
        self.assertEqual(validation.errors, [])
        self.assertFalse(validation.warnings)

    def test_validation_warns_on_missing_manifest_and_old_schema(self) -> None:
        validation = validate_measurement_metadata(
            {
                "schema_name": "lspr_measurement",
                "schema_version": "3.0",
                "schema_major": 3,
                "schema_minor": 0,
                "format_name": "experiment_run",
                "format_version": 3,
                "started_at_utc": "2026-01-02T03:04:05Z",
            }
        )

        self.assertTrue(validation.is_valid)
        self.assertTrue(any("older" in warning for warning in validation.warnings))

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "measurement.h5"
            with h5py.File(path, "w") as handle:
                write_measurement_root_metadata(
                    handle,
                    schema_name="lspr_measurement",
                    schema_version="4.0",
                    schema_major=4,
                    schema_minor=0,
                    format_name="experiment_run",
                    format_version=4,
                    app_name="LSPR Suite",
                    app_version="0.1.0",
                    created_by="tester",
                    created_at_utc="2026-01-02T03:04:04Z",
                    started_at_utc="2026-01-02T03:04:05Z",
                    experiment_name="demo",
                )
                data = handle.create_group("data")
                data.create_dataset("wavelengths", data=np.asarray([610.0, 620.0], dtype=np.float64))
                data.create_dataset(
                    "raw_spectra_extinction",
                    shape=(0, 2),
                    maxshape=(None, 2),
                    dtype=np.float32,
                    chunks=True,
                )
                data.create_dataset("time_series", shape=(0,), maxshape=(None,), dtype=np.float64, chunks=True)
                data.create_dataset("peak_position_nm", shape=(0,), maxshape=(None,), dtype=np.float64, chunks=True)
                axes = handle.create_group("axes")
                axes.create_dataset("wavelengths_nm", data=np.asarray([610.0, 620.0], dtype=np.float64))
                metadata = handle.create_group("metadata")
                metadata.create_dataset(
                    "experiment_plan",
                    shape=(0, 0),
                    maxshape=(None, 0),
                    dtype=h5py.string_dtype(encoding="utf-8"),
                    chunks=True,
                )
            with h5py.File(path, "r") as handle:
                file_validation = validate_measurement_file(handle)

        self.assertTrue(file_validation.is_valid)
        self.assertTrue(any("manifest" in warning.lower() for warning in file_validation.warnings))

    def test_validation_rejects_root_only_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "measurement.h5"
            with h5py.File(path, "w") as handle:
                write_measurement_root_metadata(
                    handle,
                    schema_name="lspr_measurement",
                    schema_version="4.0",
                    schema_major=4,
                    schema_minor=0,
                    format_name="experiment_run",
                    format_version=4,
                    app_name="LSPR Suite",
                    app_version="0.1.0",
                    created_by="tester",
                    created_at_utc="2026-01-02T03:04:04Z",
                    started_at_utc="2026-01-02T03:04:05Z",
                    experiment_name="demo",
                )
            with h5py.File(path, "r") as handle:
                validation = validate_measurement_file(handle)

        self.assertFalse(validation.is_valid)
        self.assertTrue(any("data" in error.lower() for error in validation.errors))

    def test_validation_rejects_newer_major_schema(self) -> None:
        validation = validate_measurement_metadata(
            {
                "schema_name": "lspr_measurement",
                "schema_version": "5.0",
                "schema_major": 5,
                "schema_minor": 0,
                "format_name": "experiment_run",
                "started_at_utc": "2026-01-02T03:04:05Z",
            }
        )

        self.assertFalse(validation.is_valid)
        self.assertTrue(any("newer than supported" in error.lower() for error in validation.errors))
