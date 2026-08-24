from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import h5py
import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.models import ProcessingSettings, Spectrum
from lspr_acq_shell.experiment_control_import import build_experiment_plan_steps_from_hdf5_rows
from lspr_app.gui.main_window_import_dialog import probe_measurement_hdf5
from lspr_app.storage.app_config import load_processing_settings_from_hdf5
from lspr_app.storage.hdf5_export import (
    AsyncHDF5MeasurementWriter,
    HDF5MeasurementWriter,
    repack_measurement_hdf5_file,
)
from lspr_core import ExperimentPlan, ExperimentPlanStep
from lspr_io import build_legacy_experiment_plan_row_table, read_processing_settings_metadata


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

    def test_switch_position_written_as_empty_when_disabled(self) -> None:
        plan = ExperimentPlan(
            steps=[
                ExperimentPlanStep(
                    id=1,
                    start_s=0.0,
                    end_s=12.5,
                    color="#4E79A7",
                    comment="First step",
                    devices={"valve": "Open", "switch_position": 3, "channels": []},
                ),
            ],
        )

        enabled_table = build_legacy_experiment_plan_row_table(plan, switch_position_enabled=True)
        disabled_table = build_legacy_experiment_plan_row_table(plan, switch_position_enabled=False)

        switch_index = enabled_table.columns.index("switch_position")
        valve_index = enabled_table.columns.index("valve")
        self.assertEqual(enabled_table.rows[0][switch_index], "3")
        self.assertEqual(disabled_table.rows[0][switch_index], "")
        # Only the switch_position column is affected - everything else
        # (including the other device column, valve) stays the same.
        self.assertEqual(enabled_table.rows[0][valve_index], disabled_table.rows[0][valve_index])
        row_without_switch = list(enabled_table.rows[0])
        row_without_switch[switch_index] = ""
        self.assertEqual(disabled_table.rows[0], row_without_switch)

        # The reader's existing fallback (used for any unparseable/blank
        # switch_position text) must still safely round-trip the disabled
        # row back to the default position, with no reader change needed.
        steps = build_experiment_plan_steps_from_hdf5_rows(disabled_table.columns, disabled_table.rows)
        self.assertEqual(steps[0].switch_position, 1)

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

    def test_switch_solution_details_and_device_inventory_are_written(self) -> None:
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
            writer.write_device_inventory(
                [
                    ["spectrometer_1", "spectrometer", "primary", "OceanSpectrometer", "USB0", "Ocean HR4000", "HR4000", "SN123", "true"],
                    ["pump_1", "pump", "", "RegloICCClient", "COM3", "", "", "", "false"],
                ]
            )
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
                        "switch_solution_detail_rows": [
                            ["A", "10 mM", "mM", "prepared fresh"],
                            ["B", "", "", ""],
                        ],
                    },
                }
            )
            writer.close()

            with h5py.File(path, "r") as handle:
                root_schema_version = handle.attrs["schema_version"]
                assignment_tables = handle["metadata"]["assignment_tables"]
                details_table = assignment_tables["switch_solution_details"]
                details_columns = [
                    column.decode("utf-8") if isinstance(column, bytes) else str(column)
                    for column in details_table.attrs["columns"]
                ]
                details_rows = [
                    [cell.decode("utf-8") if isinstance(cell, bytes) else str(cell) for cell in row]
                    for row in details_table[...]
                ]
                inventory_group = handle["devices"]["inventory"]
                inventory_group_attrs = dict(inventory_group.attrs.items())
                inventory_table = inventory_group["devices"]
                inventory_columns = [
                    column.decode("utf-8") if isinstance(column, bytes) else str(column)
                    for column in inventory_table.attrs["columns"]
                ]
                inventory_rows = [
                    [cell.decode("utf-8") if isinstance(cell, bytes) else str(cell) for cell in row]
                    for row in inventory_table[...]
                ]

        self.assertEqual(root_schema_version, "6.5")
        self.assertEqual(details_columns, ["switch_port", "concentration", "concentration_unit", "notes"])
        self.assertEqual(details_rows, [["A", "10 mM", "mM", "prepared fresh"], ["B", "", "", ""]])
        self.assertEqual(inventory_group_attrs["schema_name"], "lspr_device_inventory")
        self.assertEqual(inventory_group_attrs["schema_version"], "1.0")
        self.assertEqual(
            inventory_columns,
            ["label", "type", "role", "driver", "endpoint", "display_name", "model", "serial_number", "connected"],
        )
        self.assertEqual(
            inventory_rows,
            [
                ["spectrometer_1", "spectrometer", "primary", "OceanSpectrometer", "USB0", "Ocean HR4000", "HR4000", "SN123", "true"],
                ["pump_1", "pump", "", "RegloICCClient", "COM3", "", "", "", "false"],
            ],
        )

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

    def test_rois_probe_index_mirrors_spectra_and_metrics_via_soft_link(self) -> None:
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
            spectrum = Spectrum(
                wavelengths_nm=wavelengths,
                values=np.asarray([1.0, 2.0, 3.0], dtype=np.float32),
                y_label="counts",
                acquired_at=datetime(2026, 1, 2, 3, 4, 6, tzinfo=timezone.utc),
            )
            writer.append_batch([spectrum], [0.0], [615.0])
            writer.close()

            with h5py.File(path, "r") as handle:
                real_intensity = handle["data"]["spectra"]["sample"]["intensity"][...]
                linked_intensity = handle["rois"]["probe"]["spectra"]["sample"]["intensity"][...]
                definition_attrs = dict(handle["rois"]["probe"]["definition"].attrs.items())
                spectra_link = handle.get("rois/probe/spectra", getlink=True)
                metrics_link = handle.get("rois/probe/metrics", getlink=True)

        np.testing.assert_array_equal(real_intensity, linked_intensity)
        self.assertEqual(definition_attrs["name"], "Fiber probe")
        self.assertEqual(definition_attrs["geometry_type"], "single_channel")
        self.assertIsInstance(spectra_link, h5py.SoftLink)
        self.assertEqual(spectra_link.path, "/data/spectra")
        self.assertIsInstance(metrics_link, h5py.SoftLink)
        self.assertEqual(metrics_link.path, "/processed/metrics")

    def test_repack_preserves_rois_index_as_a_link_not_a_duplicate(self) -> None:
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

            repack_measurement_hdf5_file(path)

            with h5py.File(path, "r") as handle:
                spectra_link = handle.get("rois/probe/spectra", getlink=True)
                real_intensity = handle["data"]["spectra"]["sample"]["intensity"][...]
                linked_intensity = handle["rois"]["probe"]["spectra"]["sample"]["intensity"][...]

        self.assertIsInstance(spectra_link, h5py.SoftLink)
        np.testing.assert_array_equal(real_intensity, linked_intensity)

    def test_async_writer_reports_failure_via_on_error_callback(self) -> None:
        processing = ProcessingSettings()
        wavelengths = np.asarray([610.0, 620.0, 630.0], dtype=np.float64)
        errors: list[str] = []
        error_event = threading.Event()

        def on_error(message: str) -> None:
            errors.append(message)
            error_event.set()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "measurement.h5"
            writer = AsyncHDF5MeasurementWriter(
                path,
                "sample",
                wavelengths,
                processing,
                experiment_name="demo",
                started_at_utc=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
                flush_interval_s=0.25,
                on_error=on_error,
            )
            try:
                spectrum = Spectrum(
                    wavelengths_nm=wavelengths,
                    values=np.zeros(3, dtype=np.float64),
                    y_label="Intensity",
                    acquired_at=datetime.now(timezone.utc),
                )
                with mock.patch.object(
                    HDF5MeasurementWriter,
                    "append_batch",
                    side_effect=RuntimeError("disk full"),
                ):
                    writer.append_batch([spectrum], [0.0], [615.0])
                    writer.flush()
                    triggered = error_event.wait(timeout=5.0)

                self.assertTrue(triggered, "on_error callback was not called within timeout")
                self.assertEqual(len(errors), 1)
                self.assertIn("disk full", errors[0])
                # The writer marks itself closed on failure so callers stop
                # queueing into a thread that has already exited.
                self.assertTrue(writer._closed)
            finally:
                # Should be a safe no-op: the writer thread already exited on
                # its own after the simulated failure.
                writer.close()

    def test_save_copy_flushes_then_produces_a_valid_readable_copy(self) -> None:
        processing = ProcessingSettings()
        wavelengths = np.asarray([610.0, 620.0, 630.0], dtype=np.float64)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.h5"
            writer = AsyncHDF5MeasurementWriter(
                path,
                "sample",
                wavelengths,
                processing,
                experiment_name="demo",
                started_at_utc=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
                # Long interval so it's save_copy's own flush that matters here,
                # not the periodic timeout flush racing it.
                flush_interval_s=5.0,
            )
            try:
                spectrum = Spectrum(
                    wavelengths_nm=wavelengths,
                    values=np.asarray([1.0, 2.0, 3.0], dtype=np.float64),
                    y_label="Intensity",
                    acquired_at=datetime.now(timezone.utc),
                )
                writer.append_batch([spectrum], [0.0], [615.0])

                dest_path = Path(temp_dir) / "copy" / "session_copy.h5"
                results: list[tuple[bool, str]] = []
                done = threading.Event()

                def on_done(success: bool, message: str) -> None:
                    results.append((success, message))
                    done.set()

                writer.save_copy(dest_path, on_done=on_done)
                triggered = done.wait(timeout=5.0)

                self.assertTrue(triggered, "save_copy's on_done callback was not called within timeout")
                self.assertEqual(results, [(True, "")])
                self.assertTrue(dest_path.exists())

                with h5py.File(dest_path, "r") as handle:
                    sample_intensity = handle["data"]["spectra"]["sample"]["intensity"]
                    shape = sample_intensity.shape
                    values = sample_intensity[0, :]

                self.assertEqual(shape, (1, 3))
                np.testing.assert_allclose(values, [1.0, 2.0, 3.0])

                # save_copy must not have closed or otherwise disturbed the live
                # writer - it should still accept and persist further data.
                spectrum2 = Spectrum(
                    wavelengths_nm=wavelengths,
                    values=np.asarray([4.0, 5.0, 6.0], dtype=np.float64),
                    y_label="Intensity",
                    acquired_at=datetime.now(timezone.utc),
                )
                writer.append_batch([spectrum2], [1.0], [616.0])
                writer.flush()
            finally:
                writer.close()

            with h5py.File(path, "r") as handle:
                shape = handle["data"]["spectra"]["sample"]["intensity"].shape
            self.assertEqual(shape, (2, 3))

    def test_save_copy_on_already_closed_writer_reports_failure(self) -> None:
        processing = ProcessingSettings()
        wavelengths = np.asarray([610.0, 620.0, 630.0], dtype=np.float64)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.h5"
            writer = AsyncHDF5MeasurementWriter(
                path,
                "sample",
                wavelengths,
                processing,
                experiment_name="demo",
                started_at_utc=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            )
            writer.close()

            results: list[tuple[bool, str]] = []
            writer.save_copy(Path(temp_dir) / "copy.h5", on_done=lambda ok, msg: results.append((ok, msg)))

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0][0])
        self.assertIn("closed", results[0][1].lower())

    def test_environment_reading_written_and_readable(self) -> None:
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
            writer.append_environment_reading(1700000000000, 23.4, 41.2)
            # A partial reading (only one sensor responded) must still record
            # a row - the other value becomes NaN, the row isn't dropped.
            writer.append_environment_reading(1700000060000, None, 40.5)
            writer.close()

            with h5py.File(path, "r") as handle:
                group = handle["devices"]["environment"]
                timestamps = group["timestamp_utc_ms"][...]
                temperatures = group["temperature_c"][...]
                humidities = group["humidity_percent"][...]

        np.testing.assert_array_equal(timestamps, [1700000000000, 1700000060000])
        np.testing.assert_allclose(temperatures[0], 23.4)
        self.assertTrue(np.isnan(temperatures[1]))
        np.testing.assert_allclose(humidities, [41.2, 40.5])

    def test_async_writer_environment_reading_reaches_disk(self) -> None:
        # Does not peek at the file with a second h5py.File handle while the
        # writer's own handle is still open - see save_copy's docstring in
        # hdf5_export.py: a second concurrent open collides with the
        # writer's file lock on Windows. close() drains the queue (the
        # "environment" message is processed before the "close" message
        # behind it, since the queue is FIFO) and blocks until the
        # background thread has fully exited, so it's safe to read after.
        processing = ProcessingSettings()
        wavelengths = np.asarray([610.0, 620.0, 630.0], dtype=np.float64)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "measurement.h5"
            writer = AsyncHDF5MeasurementWriter(
                path,
                "sample",
                wavelengths,
                processing,
                experiment_name="demo",
                started_at_utc=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
                flush_interval_s=0.1,
            )
            writer.append_environment_reading(1700000000000, 22.1, 55.0)
            writer.close()

            with h5py.File(path, "r") as handle:
                group = handle["devices"]["environment"]
                self.assertEqual(group["timestamp_utc_ms"][0], 1700000000000)
                self.assertAlmostEqual(float(group["temperature_c"][0]), 22.1)
                self.assertAlmostEqual(float(group["humidity_percent"][0]), 55.0)

    def test_probe_rejects_incompatible_measurement_schema_major_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "future_format.h5"
            with h5py.File(path, "w") as handle:
                handle.attrs["schema_name"] = "lspr_measurement"
                handle.attrs["schema_version"] = "99.0"
                handle.attrs["started_at_utc"] = "2026-01-02T03:04:05+00:00"
                handle.attrs["created_at_utc"] = "2026-01-02T03:04:05+00:00"

            probe = probe_measurement_hdf5(path)

        self.assertTrue(probe.error)
        self.assertIn("99", probe.error)
        self.assertFalse(probe.processing.available)
        self.assertFalse(probe.plan.available)

    def test_probe_accepts_current_measurement_schema_version(self) -> None:
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
            writer.close()

            probe = probe_measurement_hdf5(path)

        self.assertEqual(probe.error, "")


if __name__ == "__main__":
    unittest.main()
