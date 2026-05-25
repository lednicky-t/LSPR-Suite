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
from lspr_app.storage.hdf5_export import HDF5MeasurementWriter


class Hdf5AcquisitionWriterTests(unittest.TestCase):
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
                        "plan_rows": [plan_row, [value for value in plan_row]],
                    },
                }
            )
            writer.append_flow_state(
                [
                    {
                        "event": "step_started",
                        "step_index": 1,
                        "status": "running",
                        "pump_running": True,
                        "switch_position": 1,
                        "valve_position": "load",
                    }
                ]
            )
            writer.close()

            with h5py.File(path, "r") as handle:
                switch_map = handle["metadata"]["switch_solution_map"]
                plan_shape = handle["plans"]["experiment_plan"].shape
                plan_tmp_shape = handle["plans"]["experiment_plan_tmp"].shape
                legacy_plan_shape = handle["metadata"]["experiment_plan"].shape
                flow_events_shape = handle["runs"]["flow_events"].shape
                shape = switch_map.shape
                columns = [
                    column.decode("utf-8") if isinstance(column, bytes) else str(column)
                    for column in switch_map.attrs["columns"]
                ]
                rows = [
                    [cell.decode("utf-8") if isinstance(cell, bytes) else str(cell) for cell in row]
                    for row in switch_map[...]
                ]

        self.assertEqual(shape, (2, 2))
        self.assertEqual(columns, ["switch_port", "solution_label"])
        self.assertEqual(rows, [["A", "buffer"], ["B", "sample"]])
        self.assertEqual(plan_shape, (2, len(plan_row)))
        self.assertEqual(plan_tmp_shape, (2, len(plan_row)))
        self.assertEqual(legacy_plan_shape, (2, len(plan_row)))
        self.assertEqual(flow_events_shape[0], 1)

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
                raw_compression = handle["data"]["raw_spectra_extinction"].compression
                raw_shuffle = bool(handle["data"]["raw_spectra_extinction"].shuffle)
                raw_compression_opts = handle["data"]["raw_spectra_extinction"].compression_opts
                wavelengths_compression = handle["axes"]["wavelengths_nm"].compression

        self.assertTrue(bool(root_attrs["storage_compression_enabled"]))
        self.assertEqual(root_attrs["storage_compression_filter"], "gzip")
        self.assertEqual(int(root_attrs["storage_compression_level"]), 4)
        self.assertEqual(manifest_attrs["manifest_kind"], "measurement")
        self.assertEqual(manifest_attrs["storage_compression_filter"], "gzip")
        self.assertIn("export_host", manifest_attrs)
        self.assertEqual(raw_compression, "gzip")
        self.assertTrue(raw_shuffle)
        self.assertEqual(int(raw_compression_opts), 4)
        self.assertEqual(wavelengths_compression, "gzip")


if __name__ == "__main__":
    unittest.main()
