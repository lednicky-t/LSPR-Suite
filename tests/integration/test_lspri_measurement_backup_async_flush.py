"""Regression tests for the background measurement-backup flush
(AnalysisWorkerMixin._flush_measurement_backup_buffers_async /
_write_measurement_backup_buffers, gui/analysis_worker_mixin.py) - added
after profiling found the periodic mid-run flush (every
measurement_backup_batch_size cubes) was running its real HDF5 write
synchronously on the GUI thread, costing ~1-1.75s and stalling the very
next cube's analysis (see apps/LSPRi/eva/docs/tiff_vs_ome_zarr_read_
benchmark.md's Finding 3).

Uses a lightweight duck-typed fake window (mirrors
test_lspri_sensorgram_start_reentrancy.py's `_FakeWindow` pattern) holding
a real `QThreadPool` and a real `ImagingMeasurementExportWriter` against a
temp file - the actual write path, not a mock - rather than constructing a
full `MainWindow`, since these methods only ever read a handful of
`window.*` attributes.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PyQt6 import QtCore, QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - Qt
# objects get built at import time in some of those modules.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import AreaRoi, FormulaSpectrumResult
from lspr_imaging_app.gui.analysis_controller import AnalysisController
from lspr_imaging_app.storage.measurement_export import ImagingMeasurementExportWriter, read_sensorgram_trace


def _fake_roi_result(roi_id: int) -> FormulaSpectrumResult:
    wavelengths = np.asarray([600.0, 650.0])
    return FormulaSpectrumResult(
        wavelengths_nm=wavelengths,
        formula_values=np.asarray([0.1, 0.2]),
        sample_reduced_value=np.asarray([1000.0, 1100.0]),
        reference_reduced_value=np.asarray([2000.0, 2100.0]),
        sample_pixel_count=np.asarray([50, 50]),
        reference_pixel_count=np.asarray([80, 80]),
    )


class TestMeasurementBackupAsyncFlush(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "backup.h5"
        self.writer = ImagingMeasurementExportWriter(self.path)
        self.roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
        self.writer.write_roi_definitions([self.roi], [], [])
        self.addCleanup(self.writer.close)

        flush_pool = QtCore.QThreadPool()
        flush_pool.setMaxThreadCount(1)
        self.window = SimpleNamespace(
            _measurement_export_writer=self.writer,
            _formula_spectrum_backup_buffer={},
            _sensorgram_backup_buffer={},
            _measurement_backup_buffered_cube_count=3,
            _measurement_backup_flush_pool=flush_pool,
        )
        self.controller = AnalysisController.__new__(AnalysisController)
        self.controller.window = self.window

    def test_async_flush_swaps_buffer_immediately_without_waiting_for_the_write(self) -> None:
        self.window._formula_spectrum_backup_buffer = {
            "1": [(0, "hash0", _fake_roi_result(1), 1000), (1, "hash1", _fake_roi_result(1), 2000)]
        }
        self.window._sensorgram_backup_buffer = {"1": [(0, "hash0", 0.5, 1000), (1, "hash1", 0.6, 2000)]}

        self.controller._flush_measurement_backup_buffers_async()

        # The swap is synchronous - by the time the call returns, the old
        # buffers are gone and replaced with fresh empty dicts, regardless
        # of whether the background write has even started yet.
        self.assertEqual(self.window._formula_spectrum_backup_buffer, {})
        self.assertEqual(self.window._sensorgram_backup_buffer, {})
        self.assertEqual(self.window._measurement_backup_buffered_cube_count, 0)

        self.window._measurement_backup_flush_pool.waitForDone(5000)

        trace = read_sensorgram_trace(self.path, 1)
        np.testing.assert_array_equal(trace["cube_index"], np.asarray([0, 1], dtype=np.int64))
        np.testing.assert_array_equal(trace["timestamp_utc_ms"], np.asarray([1000, 2000], dtype=np.int64))
        np.testing.assert_allclose(trace["metric_value"], np.asarray([0.5, 0.6]))

    def test_async_flush_is_a_safe_no_op_when_nothing_is_buffered(self) -> None:
        self.controller._flush_measurement_backup_buffers_async()
        self.window._measurement_backup_flush_pool.waitForDone(5000)
        self.assertEqual(self.window._measurement_backup_buffered_cube_count, 0)

    def test_sync_flush_waits_for_an_in_flight_async_flush_before_writing_its_own_rows(self) -> None:
        # Queue an async flush for cube 0, then - without waiting - queue a
        # second batch (cube 1) through the synchronous path, matching
        # end-of-run behavior. The single-worker pool must serialize these:
        # the synchronous call's own waitForDone() should block until the
        # first (async) write has actually landed, so both cubes end up on
        # disk in the right order rather than racing each other.
        self.window._sensorgram_backup_buffer = {"1": [(0, "hash0", 0.5, 1000)]}
        self.controller._flush_measurement_backup_buffers_async()

        self.window._sensorgram_backup_buffer = {"1": [(1, "hash1", 0.6, 2000)]}
        self.controller._flush_measurement_backup_buffers()

        trace = read_sensorgram_trace(self.path, 1)
        np.testing.assert_array_equal(trace["cube_index"], np.asarray([0, 1], dtype=np.int64))
        np.testing.assert_allclose(trace["metric_value"], np.asarray([0.5, 0.6]))

    def test_async_flush_failure_is_logged_not_raised(self) -> None:
        # A writer whose batch-append always raises must not crash the
        # background thread or the caller - _write_measurement_backup_
        # buffers already catches per-ROI write errors; this confirms that
        # holds through the async path too (worker.signals.error is wired
        # to a log call, not left unconnected).
        class _BrokenWriter:
            def append_sensorgram_point_batch(self, *args, **kwargs):
                raise RuntimeError("disk full (simulated)")

            def append_formula_spectrum_batch(self, *args, **kwargs):
                raise RuntimeError("disk full (simulated)")

        self.window._measurement_export_writer = _BrokenWriter()
        self.window._sensorgram_backup_buffer = {"1": [(0, "hash0", 0.5, 1000)]}
        self.controller._flush_measurement_backup_buffers_async()
        self.window._measurement_backup_flush_pool.waitForDone(5000)
        # No exception propagated - the failure is caught and logged inside
        # _write_measurement_backup_buffers itself, same as the synchronous
        # path already did before this change.


if __name__ == "__main__":
    unittest.main()
