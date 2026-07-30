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
    read_processing_settings_metadata,
    standard_measurement_metadata,
    validate_measurement_file,
    validate_measurement_metadata,
    write_measurement_manifest_metadata,
    write_measurement_root_metadata,
    write_processing_settings_metadata,
    write_processed_metrics_metadata,
)


def _write_processed_metrics_group(handle: h5py.File) -> None:
    processed = handle.create_group("processed")
    metrics = processed.create_group("metrics")
    write_processed_metrics_metadata(metrics)
    metrics.create_dataset(
        "acquired_at_unix_ms",
        shape=(0,),
        maxshape=(None,),
        dtype=np.int64,
        chunks=True,
    )
    write_processing_settings_metadata(
        metrics,
        {
            "wavelength_min_nm": 450.0,
            "wavelength_max_nm": 850.0,
            "baseline_method": "linear",
            "smoothing_method": "moving_average",
            "smoothing_window": 7,
            "temporal_smoothing": 2,
            "crop_method": "fixed_width",
            "crop_fraction": 0.75,
            "fit_method": "poly",
            "polynomial_order": 3,
            "fit_window_width_nm": 110.0,
            "analysis_resolution_nm": 0.001,
            "peak_tracking_mode": "poly_max",
            "trace_noise_window_s": 12.0,
            "trace_metrics": ["smoothed_max", "centroid"],
        },
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
        self.assertEqual(metadata["schema_major"], 6)
        self.assertEqual(metadata["schema_minor"], 2)
        self.assertEqual(metadata["app_name"], "Test App")
        self.assertEqual(metadata["app_version"], "9.8.7")
        self.assertEqual(metadata["started_at_utc"], "2026-01-02T03:04:05Z")
        self.assertEqual(metadata["experiment_name"], "demo")
        self.assertEqual(metadata["user"], "")

    def test_standard_measurement_metadata_carries_the_user_field(self) -> None:
        metadata = standard_measurement_metadata(
            created_by="tester",
            started_at_utc=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            app_name="Test App",
            app_version="9.8.7",
            user="Alex Chen",
        )
        self.assertEqual(metadata["user"], "Alex Chen")

    def test_root_metadata_user_field_round_trips_and_defaults_to_empty(self) -> None:
        started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "measurement.h5"
            with h5py.File(path, "w") as handle:
                write_measurement_root_metadata(
                    handle,
                    schema_name="lspr_measurement",
                    schema_version="6.1",
                    schema_major=6,
                    schema_minor=1,
                    format_name="experiment_run",
                    format_version=6,
                    app_name="LSPR Suite",
                    app_version="0.1.0",
                    created_by="tester",
                    started_at_utc=started_at,
                    user="Jamie Lee",
                )
            with h5py.File(path, "r") as handle:
                self.assertEqual(handle.attrs["user"], "Jamie Lee")

            path_no_user = Path(temp_dir) / "measurement_no_user.h5"
            with h5py.File(path_no_user, "w") as handle:
                write_measurement_root_metadata(
                    handle,
                    schema_name="lspr_measurement",
                    schema_version="6.1",
                    schema_major=6,
                    schema_minor=1,
                    format_name="experiment_run",
                    format_version=6,
                    app_name="LSPR Suite",
                    app_version="0.1.0",
                    created_by="tester",
                    started_at_utc=started_at,
                )
            with h5py.File(path_no_user, "r") as handle:
                self.assertEqual(handle.attrs["user"], "")

    def test_root_metadata_round_trip(self) -> None:
        started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "measurement.h5"
            with h5py.File(path, "w") as handle:
                write_measurement_root_metadata(
                    handle,
                    schema_name="lspr_measurement",
                    schema_version="6.0",
                    schema_major=6,
                    schema_minor=0,
                    format_name="experiment_run",
                    format_version=6,
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
        self.assertEqual(metadata["schema_version"], "6.0")
        self.assertEqual(metadata["schema_major"], 6)
        self.assertEqual(metadata["schema_minor"], 0)
        self.assertEqual(metadata["format_name"], "experiment_run")
        self.assertEqual(metadata["format_version"], 6)
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
                    schema_version="6.0",
                    schema_major=6,
                    schema_minor=0,
                    format_name="experiment_run",
                    format_version=6,
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
                    schema_version="6.0",
                    schema_major=6,
                    schema_minor=0,
                    format_name="experiment_run",
                    format_version=6,
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
                    schema_version="6.0",
                    schema_major=6,
                    schema_minor=0,
                    format_name="experiment_run",
                    format_version=6,
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
                data.create_dataset("time_series", shape=(0,), maxshape=(None,), dtype=np.float64, chunks=True)
                metadata = handle.create_group("metadata")
                metadata.create_dataset("experiment_plan", shape=(0, 0), maxshape=(None, 0), dtype=h5py.string_dtype(encoding="utf-8"), chunks=True)
                assignment_tables = metadata.create_group("assignment_tables")
                for name, columns in (
                    ("switch_solution_map", ["switch_port", "solution_label"]),
                    ("valve_state_map", ["state", "label", "color"]),
                    ("color_palette_entries", ["name", "color"]),
                ):
                    dataset = assignment_tables.create_dataset(
                        name,
                        shape=(0, len(columns)),
                        maxshape=(None, len(columns)),
                        dtype=h5py.string_dtype(encoding="utf-8"),
                        chunks=True,
                    )
                    dataset.attrs["columns"] = np.asarray(columns, dtype=h5py.string_dtype(encoding="utf-8"))
                data.create_dataset(
                    "wavelengths_nm",
                    data=np.asarray([610.0, 620.0], dtype=np.float64),
                )
                _write_processed_metrics_group(handle)
                spectra = data.create_group("spectra")
                for spectrum_name in ("sample", "dark", "reference"):
                    group = spectra.create_group(spectrum_name)
                    group.create_dataset(
                        "intensity",
                        shape=(0, 2),
                        maxshape=(None, 2),
                        dtype=np.float32,
                        chunks=True,
                    )
                    if spectrum_name == "sample":
                        group.create_dataset("dark_index", shape=(0,), maxshape=(None,), dtype=np.int64, chunks=True)
                        group.create_dataset(
                            "reference_index", shape=(0,), maxshape=(None,), dtype=np.int64, chunks=True
                        )
                runtime_columns = [
                    "timestamp_utc_ms",
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
                runtime_table = np.empty((0, len(runtime_columns)), dtype=h5py.string_dtype(encoding="utf-8"))
                data.create_dataset(
                    "experiment_control_runtime",
                    data=runtime_table,
                    shape=(0, len(runtime_columns)),
                    maxshape=(None, len(runtime_columns)),
                    chunks=True,
                )
                data["experiment_control_runtime"].attrs["columns"] = np.asarray(
                    runtime_columns, dtype=h5py.string_dtype(encoding="utf-8")
                )
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
                        f"ch{index + 1}_flow_ul_min" for index in range(6)
                    ]
                )
                plan_columns.extend(
                    [
                        f"ch{index + 1}_direction" for index in range(6)
                    ]
                )
                plan_columns.extend(
                    [
                        f"ch{index + 1}_tube_mm" for index in range(6)
                    ]
                )
                # FIXME: "experiment_plan" was created earlier (shape=(0, 0),
                # maxshape=(None, 0)) with a hard-capped 0-column maxshape - not
                # resizable to match plan_columns via h5py without recreating the
                # dataset. Net effect: this fixture's "experiment_plan" dataset shape
                # (0, 0) is inconsistent with its own "columns" attrs (26 entries)
                # below. Not fixed here since it touches HDF5 schema/fixture
                # mechanics - needs a maintainer decision, not a guess.
                metadata["experiment_plan"].attrs["columns"] = np.asarray(plan_columns, dtype=h5py.string_dtype(encoding="utf-8"))

            with h5py.File(path, "r") as handle:
                manifest = handle["manifest"]
                manifest_attrs = read_root_metadata(manifest)
                validation = validate_measurement_file(handle)

        self.assertEqual(manifest_attrs["schema_version"], "6.0")
        self.assertIn("export_user", manifest_attrs)
        self.assertTrue(bool(manifest_attrs["storage_compression_enabled"]))
        self.assertEqual(manifest_attrs["storage_compression_filter"], "gzip")
        self.assertTrue(validation.is_valid)
        self.assertEqual(validation.errors, [])
        self.assertFalse(validation.warnings)

    def test_processed_metrics_configuration_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "measurement.h5"
            with h5py.File(path, "w") as handle:
                processed = handle.create_group("processed")
                metrics = processed.create_group("metrics")
                write_processed_metrics_metadata(metrics)
                write_processing_settings_metadata(
                    metrics,
                    {
                        "wavelength_min_nm": 455.0,
                        "wavelength_max_nm": 825.0,
                        "baseline_method": "linear",
                        "smoothing_method": "savitzky_golay",
                        "smoothing_window": 9,
                        "temporal_smoothing": 3,
                        "crop_method": "threshold",
                        "crop_fraction": 0.8,
                        "fit_method": "gaussian",
                        "polynomial_order": 4,
                        "fit_window_width_nm": 100.0,
                        "analysis_resolution_nm": 0.0001,
                        "peak_tracking_mode": "gaussian_center",
                        "trace_noise_window_s": 15.0,
                        "trace_metrics": ["smoothed_max", "poly_max", "gaussian_center"],
                    },
                )

            with h5py.File(path, "r") as handle:
                metrics = handle["processed"]["metrics"]
                metrics_attrs = dict(metrics.attrs.items())
                config_attrs = dict(metrics["config"].attrs.items())
                settings_payload = read_processing_settings_metadata(metrics)

        self.assertIsNotNone(settings_payload)
        self.assertEqual(metrics_attrs["schema_name"], "lspr_processed_metrics")
        self.assertEqual(metrics_attrs["schema_version"], "1.0")
        self.assertEqual(config_attrs["schema_name"], "lspr_processing_settings")
        self.assertEqual(config_attrs["schema_version"], "1.0")
        self.assertEqual(settings_payload["smoothing_method"], "savitzky_golay")
        self.assertEqual(settings_payload["fit_method"], "gaussian")
        self.assertEqual(settings_payload["trace_metrics"], ["smoothed_max", "poly_max", "gaussian_center"])

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
                    schema_version="6.0",
                    schema_major=6,
                    schema_minor=0,
                    format_name="experiment_run",
                    format_version=6,
                    app_name="LSPR Suite",
                    app_version="0.1.0",
                    created_by="tester",
                    created_at_utc="2026-01-02T03:04:04Z",
                    started_at_utc="2026-01-02T03:04:05Z",
                    experiment_name="demo",
                )
                data = handle.create_group("data")
                data.create_dataset("time_series", shape=(0,), maxshape=(None,), dtype=np.float64, chunks=True)
                metadata = handle.create_group("metadata")
                metadata.create_dataset(
                    "experiment_plan",
                    shape=(0, 0),
                    maxshape=(None, 0),
                    dtype=h5py.string_dtype(encoding="utf-8"),
                    chunks=True,
                )
                assignment_tables = metadata.create_group("assignment_tables")
                for name, columns in (
                    ("switch_solution_map", ["switch_port", "solution_label"]),
                    ("valve_state_map", ["state", "label", "color"]),
                    ("color_palette_entries", ["name", "color"]),
                ):
                    dataset = assignment_tables.create_dataset(
                        name,
                        shape=(0, len(columns)),
                        maxshape=(None, len(columns)),
                        dtype=h5py.string_dtype(encoding="utf-8"),
                        chunks=True,
                    )
                    dataset.attrs["columns"] = np.asarray(columns, dtype=h5py.string_dtype(encoding="utf-8"))
                data.create_dataset("wavelengths_nm", data=np.asarray([610.0, 620.0], dtype=np.float64))
                _write_processed_metrics_group(handle)
                spectra = data.create_group("spectra")
                for spectrum_name in ("sample", "dark", "reference"):
                    group = spectra.create_group(spectrum_name)
                    group.create_dataset(
                        "intensity",
                        shape=(0, 2),
                        maxshape=(None, 2),
                        dtype=np.float32,
                        chunks=True,
                    )
                    if spectrum_name == "sample":
                        group.create_dataset("dark_index", shape=(0,), maxshape=(None,), dtype=np.int64, chunks=True)
                        group.create_dataset(
                            "reference_index", shape=(0,), maxshape=(None,), dtype=np.int64, chunks=True
                        )
                runtime_table = np.empty((0, 30), dtype=h5py.string_dtype(encoding="utf-8"))
                data.create_dataset(
                    "experiment_control_runtime",
                    data=runtime_table,
                    shape=(0, 30),
                    maxshape=(None, 30),
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
                    schema_version="6.0",
                    schema_major=6,
                    schema_minor=0,
                    format_name="experiment_run",
                    format_version=6,
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
                "schema_version": "7.0",
                "schema_major": 7,
                "schema_minor": 0,
                "format_name": "experiment_run",
                "started_at_utc": "2026-01-02T03:04:05Z",
            }
        )

        self.assertFalse(validation.is_valid)
        self.assertTrue(any("newer than supported" in error.lower() for error in validation.errors))
