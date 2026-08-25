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

import h5py
import numpy as np

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiGroup, RoiArrayGroup, RoiMask
from lspr_imaging_app.storage.measurement_export import (
    ImagingMeasurementExportWriter,
    read_absorbance_spectra_trace,
    read_roi_definition_records,
    read_sensorgram_trace,
)


def _sample_rois() -> tuple[list[AreaRoi], list[AreaRoiGroup], list[RoiArrayGroup]]:
    circle_roi = AreaRoi(
        area_roi_id=1,
        center_x=10.0,
        center_y=20.0,
        sample_radius_px=5.0,
        sample_diameter_px=10.0,
        reference_inner_diameter_px=12.0,
        reference_outer_diameter_px=18.0,
        label="Spot A",
        notes="prepared fresh",
    )
    mask = RoiMask(x0=1, y0=2, mask=np.ones((3, 3), dtype=bool))
    rectangle_roi = AreaRoi(
        area_roi_id=2,
        center_x=50.0,
        center_y=60.0,
        sample_radius_px=0.0,
        sample_geometry_type="mask",
        sample_mask=mask,
        reference_geometry_type="mask",
        reference_mask=mask,
        label="Spot B (freeform)",
        created_by="auto",
    )
    group = AreaRoiGroup(group_id="group_1", name="Row A", area_roi_ids=[1, 2])
    array = RoiArrayGroup(
        array_id="array_1",
        label="Grid",
        rows=1,
        cols=2,
        spacing_x_px=40.0,
        spacing_y_px=0.0,
        anchor_x_px=10.0,
        anchor_y_px=20.0,
        member_area_roi_ids=[1, 2],
    )
    return [circle_roi, rectangle_roi], [group], [array]


class ImagingMeasurementExportWriterTests(unittest.TestCase):
    def test_roi_definitions_round_trip_including_mask_geometry(self) -> None:
        rois, groups, arrays = _sample_rois()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path, experiment_name="demo") as writer:
                writer.write_roi_definitions(rois, groups, arrays)

            records = read_roi_definition_records(path)

        self.assertEqual(len(records), 2)
        by_id = {record.area_roi_id: record for record in records}
        self.assertEqual(by_id[1].label, "Spot A")
        self.assertEqual(by_id[1].sample_geometry_type, "circle")
        self.assertEqual(by_id[1].group_id, "group_1")
        self.assertEqual(by_id[1].array_group_id, "array_1")
        self.assertEqual(by_id[1].array_row, 0)
        self.assertEqual(by_id[1].array_col, 0)
        self.assertEqual(by_id[2].sample_geometry_type, "mask")
        self.assertEqual(by_id[2].created_by, "auto")
        self.assertEqual(by_id[2].array_col, 1)

    def test_rois_index_soft_links_are_created_for_each_roi(self) -> None:
        rois, groups, arrays = _sample_rois()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path) as writer:
                writer.write_roi_definitions(rois, groups, arrays)
                writer.append_sensorgram_point(1, timestamp_utc_ms=100, metric_value=0.5)
                writer.append_absorbance_spectrum(
                    1,
                    wavelengths_nm=np.asarray([600.0, 650.0]),
                    formula_values=np.asarray([0.1, 0.2]),
                    sample_mean=np.asarray([1000.0, 1100.0]),
                    reference_mean=np.asarray([2000.0, 2100.0]),
                    cube_index=0,
                    timestamp_utc_ms=100,
                )

            with h5py.File(path, "r") as handle:
                definition_attrs = dict(handle["rois"]["1"]["definition"].attrs.items())
                sensorgram_link = handle.get("rois/1/sensorgram", getlink=True)
                absorbance_link = handle.get("rois/1/absorbance_spectra", getlink=True)
                linked_metric = handle["rois"]["1"]["sensorgram"]["metric_value"][...]

        self.assertEqual(definition_attrs["name"], "Spot A")
        self.assertIsInstance(sensorgram_link, h5py.SoftLink)
        self.assertIsInstance(absorbance_link, h5py.SoftLink)
        np.testing.assert_array_equal(linked_metric, np.asarray([0.5]))

    def test_sensorgram_append_and_reload_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path) as writer:
                writer.set_sensorgram_metric(1, metric_name="centroid", formula_key="absorbance")
                writer.append_sensorgram_point(1, timestamp_utc_ms=100, metric_value=0.10)
                writer.append_sensorgram_point(1, timestamp_utc_ms=200, metric_value=0.15)
                writer.append_sensorgram_point(1, timestamp_utc_ms=300, metric_value=0.22)

            trace = read_sensorgram_trace(path, 1)

        np.testing.assert_array_equal(trace["timestamp_utc_ms"], np.asarray([100, 200, 300], dtype=np.int64))
        np.testing.assert_allclose(trace["metric_value"], np.asarray([0.10, 0.15, 0.22]))
        self.assertEqual(trace["metric_name"], "centroid")
        self.assertEqual(trace["formula_key"], "absorbance")

    def test_absorbance_spectrum_append_and_reload_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            wavelengths = np.asarray([600.0, 620.0, 640.0])
            with ImagingMeasurementExportWriter(path) as writer:
                writer.append_absorbance_spectrum(
                    1,
                    wavelengths_nm=wavelengths,
                    formula_values=np.asarray([0.1, 0.2, 0.3]),
                    sample_mean=np.asarray([1000.0, 1010.0, 1020.0]),
                    reference_mean=np.asarray([2000.0, 2010.0, 2020.0]),
                    cube_index=0,
                    timestamp_utc_ms=1000,
                )
                writer.append_absorbance_spectrum(
                    1,
                    wavelengths_nm=wavelengths,
                    formula_values=np.asarray([0.11, 0.21, 0.31]),
                    sample_mean=np.asarray([1001.0, 1011.0, 1021.0]),
                    reference_mean=np.asarray([2001.0, 2011.0, 2021.0]),
                    cube_index=1,
                    timestamp_utc_ms=2000,
                )

            trace = read_absorbance_spectra_trace(path, 1)

        np.testing.assert_array_equal(trace["wavelengths_nm"], wavelengths)
        np.testing.assert_array_equal(trace["cube_index"], np.asarray([0, 1], dtype=np.int64))
        np.testing.assert_array_equal(trace["timestamp_utc_ms"], np.asarray([1000, 2000], dtype=np.int64))
        np.testing.assert_allclose(trace["absorbance"], np.asarray([[0.1, 0.2, 0.3], [0.11, 0.21, 0.31]]), atol=1e-6)
        np.testing.assert_allclose(
            trace["sample_mean"], np.asarray([[1000.0, 1010.0, 1020.0], [1001.0, 1011.0, 1021.0]]), atol=1e-3
        )
        self.assertEqual(trace["formula_key"], "absorbance")

    def test_simulated_crash_leaves_completed_rows_intact(self) -> None:
        """Close the file abruptly (no clean 'finalize' step, just close()
        after some appends) and confirm the last completed rows survive -
        the crash-safety property this format is meant to provide."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            writer = ImagingMeasurementExportWriter(path)
            writer.append_sensorgram_point(1, timestamp_utc_ms=100, metric_value=0.1)
            writer.append_sensorgram_point(1, timestamp_utc_ms=200, metric_value=0.2)
            writer.flush()
            writer.append_sensorgram_point(1, timestamp_utc_ms=300, metric_value=0.3)
            writer.close()

            trace = read_sensorgram_trace(path, 1)

        np.testing.assert_array_equal(trace["timestamp_utc_ms"], np.asarray([100, 200, 300], dtype=np.int64))

    def test_export_snapshot_creates_independent_readable_copy(self) -> None:
        """Snapshot exported while the writer is still open must be
        readable on its own and must not disturb further appends to the
        live file - this is what the "Export Results..." button relies on."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            snapshot_path = Path(temp_dir) / "snapshot.h5"
            writer = ImagingMeasurementExportWriter(path)
            writer.append_sensorgram_point(1, timestamp_utc_ms=100, metric_value=0.5)
            writer.export_snapshot(snapshot_path)
            writer.append_sensorgram_point(1, timestamp_utc_ms=200, metric_value=0.6)
            writer.close()

            snapshot_trace = read_sensorgram_trace(snapshot_path, 1)
            live_trace = read_sensorgram_trace(path, 1)

        np.testing.assert_array_equal(snapshot_trace["metric_value"], np.asarray([0.5]))
        np.testing.assert_array_equal(live_trace["metric_value"], np.asarray([0.5, 0.6]))

    def test_set_sensorgram_metric_records_combined_roi_ids(self) -> None:
        """A multi-ROI combined-selection sensorgram trace is written under
        a synthetic roi_id (see gui/analysis_controller.py's
        _backup_sensorgram_point) with the real member ROI ids recorded as
        an attr, so it stays self-describing without a dedicated ROI
        definition row."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path) as writer:
                writer.set_sensorgram_metric(
                    "combined_1_2", metric_name="centroid", formula_key="absorbance", combined_roi_ids="1,2"
                )
                writer.append_sensorgram_point("combined_1_2", timestamp_utc_ms=100, metric_value=0.3)

            trace = read_sensorgram_trace(path, "combined_1_2")

        self.assertEqual(trace["metric_name"], "centroid")
        self.assertEqual(trace["combined_roi_ids"], "1,2")

    def test_missing_roi_returns_empty_traces_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path):
                pass

            sensorgram = read_sensorgram_trace(path, 999)
            absorbance = read_absorbance_spectra_trace(path, 999)

        self.assertEqual(sensorgram["timestamp_utc_ms"].size, 0)
        self.assertEqual(absorbance["wavelengths_nm"].size, 0)


if __name__ == "__main__":
    unittest.main()
