from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.models import ProcessingSettings
from lspr_app.gui.experiment_control_import import build_experiment_plan_steps_from_hdf5_rows
from lspr_app.storage.app_config import load_processing_settings_from_hdf5
from lspr_app.storage.hdf5_export import HDF5MeasurementWriter, repack_measurement_hdf5_file
from lspr_io import read_processing_settings_metadata


class Hdf5AcquisitionWriterTests(unittest.TestCase):
    def test_hdf5_plan_rows_can_be_parsed_into_steps(self) -> None:
        columns = [
            "step",
            "duration_s",
            "start_s",
            "end_s",
            "color",
            "valve",
            "switch_position",
            "description",
            "ch1_flow_ul_min",
            "ch1_direction",
            "ch1_tube_mm",
            "ch2_flow_ul_min",
            "ch2_direction",
            "ch2_tube_mm",
        ]
        rows = [
            [
                "1",
                "12.5",
                "0.0",
                "12.5",
                "#4E79A7",
                "Open",
                "3",
                "First step",
                "50",
                "CW",
                "0.25",
                "20",
                "CCW",
                "0.25",
            ]
        ]

        steps = build_experiment_plan_steps_from_hdf5_rows(columns, rows)

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].step, 1)
        self.assertEqual(steps[0].duration_s, 12.5)
        self.assertEqual(steps[0].switch_position, 3)
        self.assertEqual(steps[0].description, "First step")
        self.assertEqual(steps[0].channels[0].flow_ul_min, 50)
        self.assertEqual(steps[0].channels[1].direction, "CCW")

    def test_processing_settings_can_be_loaded_from_hdf5(self) -> None:
        processing = ProcessingSettings(
            wavelength_min_nm=455.0,
            wavelength_max_nm=825.0,
            baseline_method="linear",
            smoothing_method="savitzky_golay",
            smoothing_window=9,
            temporal_smoothing=3,
            crop_method="threshold",
            crop_fraction=0.8,
            fit_method="gaussian",
            polynomial_order=4,
            fit_window_width_nm=100.0,
            analysis_resolution_nm=0.0001,
            spectrum_tracking_mode="gaussian_center",
            trace_noise_window_s=15.0,
            trace_metrics=["smoothed_max", "poly_max", "gaussian_center"],
        )
        wavelengths = np.asarray([610.0, 620.0, 630.0], dtype=np.float64)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "measurement.h5"
            writer = HDF5MeasurementWriter(
                path,
                "sample",
                wavelengths,
                processing,
                experiment_name="demo",
                started_at_utc=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            )
            writer.close()

            loaded = load_processing_settings_from_hdf5(path)

        self.assertEqual(loaded.wavelength_min_nm, 455.0)
        self.assertEqual(loaded.wavelength_max_nm, 825.0)
        self.assertEqual(loaded.smoothing_method, "savitzky_golay")
        self.assertEqual(loaded.fit_method, "gaussian")
        self.assertEqual(loaded.trace_metrics, ["smoothed_max", "poly_max", "gaussian_center"])

    def test_switch_solution_metadata_is_resizable(self) -> None:
        processing = ProcessingSettings()
        wavelengths = np.asarray([610.0, 620.0, 630.0], dtype=np.float64)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "measurement.h5"
            writer = HDF5MeasurementWriter(
                path,
                "sample",
                wavelengths,
                processing,
                experiment_name="demo",
                started_at_utc=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            )
            plan_row = [
                "1",
                "60",
                "0",
                "60",
                "#4E79A7",
                "load",
                "1",
                "Prime / load",
            ]
            plan_row.extend(["50", "CW", "0.25"] * 6)
            writer.update_acquisition_state(
                {
                    "source_mode": "spectrometer",
                    "plot_mode": "sample",
                    "live_rate_hz": 4.0,
                    "show_residual": False,
                    "freeze_plots": False,
                    "experiment_control": {
                        "switch_solution_mode": True,
                        "switch_solution_rows": [["A", "buffer"], ["B", "sample"]],
                        "valve_state_labels": {"Open": "Open", "Close": "Close"},
                        "valve_state_colors": {"Open": "#4E79A7", "Close": "#B44A4A"},
                        "color_palette_entries": [
                            {"name": "Blue", "color": "#4E79A7"},
                            {"name": "Red", "color": "#B44A4A"},
                        ],
                        "plan_rows": [plan_row, [value for value in plan_row]],
                    },
                }
            )
            writer.append_flow_state(
                [
                    {
                        "timestamp_utc_ms": 123,
                        "event": "step_started",
                        "step_index": 1,
                        "status": "running",
                        "pump_running": True,
                        "switch_position": 1,
                        "valve_position": "load",
                    }
                ]
            )
            writer.append_device_state(
                {
                    "timestamp_utc_ms": 456,
                    "event": "step_updated",
                    "step_index": 1,
                    "status": "running",
                    "pump_running": False,
                    "switch_position": 2,
                    "valve_position": "sample",
                }
            )
            writer.append_metrics(
                [
                    {
                        "acquired_at_unix_ms": 789,
                        "t_ms": 123,
                        "sample_index": 7,
                        "centroid_nm": 1.1,
                        "smoothed_max_nm": 1.2,
                        "poly_max_nm": 1.3,
                        "gaussian_center_nm": 1.4,
                        "fwhm_nm": 1.5,
                        "mse": 1.6,
                        "snr": 1.7,
                    }
                ]
            )
            writer.close()

            with h5py.File(path, "r") as handle:
                assignment_tables = handle["metadata"]["assignment_tables"]
                switch_map = assignment_tables["switch_solution_map"]
                plan_shape = handle["metadata"]["experiment_plan"].shape
                runtime_shape = handle["data"]["experiment_control_runtime"].shape
                runtime_columns = [
                    column.decode("utf-8") if isinstance(column, bytes) else str(column)
                    for column in handle["data"]["experiment_control_runtime"].attrs["columns"]
                ]
                wavelengths_shape = handle["data"]["wavelengths_nm"].shape
                sample_shape = handle["data"]["spectra"]["sample"]["intensity"].shape
                dark_shape = handle["data"]["spectra"]["dark"]["intensity"].shape
                reference_shape = handle["data"]["spectra"]["reference"]["intensity"].shape
                has_peak_dataset = "peak_position_nm" in handle["data"]
                has_plans_group = "plans" in handle
                has_runs_group = "runs" in handle
                has_axes_group = "axes" in handle
                has_spectra_group = "spectra" in handle
                has_root_switch_map = "switch_solution_map" in handle["metadata"]
                processed_metrics = handle["processed"]["metrics"]
                processed_metrics_attrs = dict(processed_metrics.attrs.items())
                processing_config = read_processing_settings_metadata(processed_metrics)
                processed_metrics_timestamp = int(processed_metrics["acquired_at_unix_ms"][0])
                shape = switch_map.shape
                columns = [
                    column.decode("utf-8") if isinstance(column, bytes) else str(column)
                    for column in switch_map.attrs["columns"]
                ]
                rows = [
                    [cell.decode("utf-8") if isinstance(cell, bytes) else str(cell) for cell in row]
                    for row in switch_map[...]
                ]
                valve_label_rows = [
                    [cell.decode("utf-8") if isinstance(cell, bytes) else str(cell) for cell in row]
                    for row in assignment_tables["valve_state_map"][...]
                ]
                palette_rows = [
                    [cell.decode("utf-8") if isinstance(cell, bytes) else str(cell) for cell in row]
                    for row in assignment_tables["color_palette_entries"][...]
                ]

        self.assertEqual(shape, (2, 2))
        self.assertEqual(columns, ["switch_port", "solution_label"])
        self.assertEqual(rows, [["A", "buffer"], ["B", "sample"]])
        self.assertEqual(valve_label_rows, [["Open", "Open", "#4E79A7"], ["Close", "Close", "#B44A4A"]])
        self.assertEqual(palette_rows, [["Blue", "#4E79A7"], ["Red", "#B44A4A"]])
        self.assertEqual(runtime_columns[0], "timestamp_utc_ms")
        self.assertEqual(plan_shape, (2, len(plan_row)))
        self.assertEqual(runtime_shape[0], 2)
        self.assertEqual(len(wavelengths_shape), 1)
        self.assertEqual(sample_shape, (0, len(wavelengths)))
        self.assertEqual(dark_shape[1], len(wavelengths))
        self.assertEqual(reference_shape[1], len(wavelengths))
        self.assertFalse(has_peak_dataset)
        self.assertFalse(has_plans_group)
        self.assertFalse(has_runs_group)
        self.assertFalse(has_axes_group)
        self.assertFalse(has_spectra_group)
        self.assertFalse(has_root_switch_map)
        self.assertEqual(processed_metrics_attrs["schema_name"], "lspr_processed_metrics")
        self.assertEqual(processed_metrics_attrs["schema_version"], "1.0")
        self.assertEqual(processed_metrics_timestamp, 789)
        self.assertIsNotNone(processing_config)
        self.assertEqual(processing_config["baseline_method"], "none")
        self.assertEqual(processing_config["trace_metrics"], ["smoothed_max", "centroid"])

    def test_compression_metadata_and_filters_are_written(self) -> None:
        processing = ProcessingSettings()
        wavelengths = np.asarray([610.0, 620.0, 630.0], dtype=np.float64)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "measurement.h5"
            writer = HDF5MeasurementWriter(
                path,
                "sample",
                wavelengths,
                processing,
                experiment_name="demo",
                started_at_utc=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
                compression_enabled=True,
            )
            writer.close()

            with h5py.File(path, "r") as handle:
                root_attrs = dict(handle.attrs.items())
                manifest_attrs = dict(handle["manifest"].attrs.items())
                sample_intensity = handle["data"]["spectra"]["sample"]["intensity"]
                sample_compression = sample_intensity.compression
                sample_shuffle = bool(sample_intensity.shuffle)
                sample_compression_opts = sample_intensity.compression_opts
                wavelengths_compression = handle["data"]["wavelengths_nm"].compression
                has_legacy_raw = "raw_spectra_extinction" in handle["data"]
                has_legacy_wavelengths = "wavelengths" in handle["data"]

            self.assertFalse(bool(root_attrs["storage_compression_enabled"]))
            self.assertEqual(root_attrs["storage_compression_filter"], "none")
            self.assertEqual(int(root_attrs["storage_compression_level"]), 0)
            self.assertEqual(manifest_attrs["manifest_kind"], "measurement")
            self.assertEqual(manifest_attrs["storage_compression_filter"], "none")
            self.assertIn("export_host", manifest_attrs)
            self.assertFalse(sample_compression)
            self.assertFalse(sample_shuffle)
            self.assertIsNone(sample_compression_opts)
            self.assertFalse(wavelengths_compression)
            self.assertFalse(has_legacy_raw)
            self.assertFalse(has_legacy_wavelengths)

            repack_measurement_hdf5_file(path)

            with h5py.File(path, "r") as handle:
                root_attrs = dict(handle.attrs.items())
                manifest_attrs = dict(handle["manifest"].attrs.items())
                sample_intensity = handle["data"]["spectra"]["sample"]["intensity"]
                sample_compression = sample_intensity.compression
                sample_shuffle = bool(sample_intensity.shuffle)
                sample_compression_opts = sample_intensity.compression_opts
                wavelengths_compression = handle["data"]["wavelengths_nm"].compression

            self.assertTrue(bool(root_attrs["storage_compression_enabled"]))
            self.assertEqual(root_attrs["storage_compression_filter"], "gzip")
            self.assertEqual(int(root_attrs["storage_compression_level"]), 4)
            self.assertEqual(manifest_attrs["storage_compression_filter"], "gzip")
            self.assertEqual(sample_compression, "gzip")
            self.assertTrue(sample_shuffle)
            self.assertEqual(int(sample_compression_opts), 4)
            self.assertEqual(wavelengths_compression, "gzip")


if __name__ == "__main__":
    unittest.main()
