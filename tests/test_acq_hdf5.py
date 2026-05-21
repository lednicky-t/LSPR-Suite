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
                    },
                }
            )
            writer.close()

            with h5py.File(path, "r") as handle:
                switch_map = handle["metadata"]["switch_solution_map"]
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


if __name__ == "__main__":
    unittest.main()
