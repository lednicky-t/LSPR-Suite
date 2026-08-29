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

import json

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiGroup, RoiArrayGroup, RoiMask
from lspr_imaging_app.storage.measurement_export import (
    ImagingMeasurementExportWriter,
    read_formula_spectra_trace,
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
                writer.append_sensorgram_point(1, cube_index=0, timestamp_utc_ms=100, metric_value=0.5)
                writer.append_formula_spectrum(
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
                formula_spectrum_link = handle.get("rois/1/absorbance_spectra", getlink=True)
                linked_metric = handle["rois"]["1"]["sensorgram"]["metric_value"][...]

        self.assertEqual(definition_attrs["name"], "Spot A")
        self.assertIsInstance(sensorgram_link, h5py.SoftLink)
        self.assertIsInstance(formula_spectrum_link, h5py.SoftLink)
        np.testing.assert_array_equal(linked_metric, np.asarray([0.5]))

    def test_sensorgram_append_and_reload_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path) as writer:
                writer.set_sensorgram_metric(1, metric_name="centroid", formula_key="absorbance")
                writer.append_sensorgram_point(1, cube_index=0, timestamp_utc_ms=100, metric_value=0.10)
                writer.append_sensorgram_point(1, cube_index=1, timestamp_utc_ms=200, metric_value=0.15)
                writer.append_sensorgram_point(1, cube_index=2, timestamp_utc_ms=300, metric_value=0.22)

            trace = read_sensorgram_trace(path, 1)

        np.testing.assert_array_equal(trace["cube_index"], np.asarray([0, 1, 2], dtype=np.int64))
        np.testing.assert_array_equal(trace["timestamp_utc_ms"], np.asarray([100, 200, 300], dtype=np.int64))
        np.testing.assert_allclose(trace["metric_value"], np.asarray([0.10, 0.15, 0.22]))
        self.assertEqual(trace["metric_name"], "centroid")
        self.assertEqual(trace["formula_key"], "absorbance")

    def test_formula_spectrum_append_and_reload_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            wavelengths = np.asarray([600.0, 620.0, 640.0])
            with ImagingMeasurementExportWriter(path) as writer:
                writer.append_formula_spectrum(
                    1,
                    wavelengths_nm=wavelengths,
                    formula_values=np.asarray([0.1, 0.2, 0.3]),
                    sample_mean=np.asarray([1000.0, 1010.0, 1020.0]),
                    reference_mean=np.asarray([2000.0, 2010.0, 2020.0]),
                    cube_index=0,
                    timestamp_utc_ms=1000,
                )
                writer.append_formula_spectrum(
                    1,
                    wavelengths_nm=wavelengths,
                    formula_values=np.asarray([0.11, 0.21, 0.31]),
                    sample_mean=np.asarray([1001.0, 1011.0, 1021.0]),
                    reference_mean=np.asarray([2001.0, 2011.0, 2021.0]),
                    cube_index=1,
                    timestamp_utc_ms=2000,
                )

            trace = read_formula_spectra_trace(path, 1)

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
            writer.append_sensorgram_point(1, cube_index=0, timestamp_utc_ms=100, metric_value=0.1)
            writer.append_sensorgram_point(1, cube_index=1, timestamp_utc_ms=200, metric_value=0.2)
            writer.flush()
            writer.append_sensorgram_point(1, cube_index=2, timestamp_utc_ms=300, metric_value=0.3)
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
            writer.append_sensorgram_point(1, cube_index=0, timestamp_utc_ms=100, metric_value=0.5)
            writer.export_snapshot(snapshot_path)
            writer.append_sensorgram_point(1, cube_index=1, timestamp_utc_ms=200, metric_value=0.6)
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
                writer.append_sensorgram_point("combined_1_2", cube_index=0, timestamp_utc_ms=100, metric_value=0.3)

            trace = read_sensorgram_trace(path, "combined_1_2")

        self.assertEqual(trace["metric_name"], "centroid")
        self.assertEqual(trace["combined_roi_ids"], "1,2")

    def test_reopening_existing_backup_preserves_data_and_recovers_keys(self) -> None:
        """Regression test: reopening a backup file that already exists (the
        normal case when re-loading a dataset analyzed in a previous
        session) must append to it, not truncate it - and the reopened
        writer must recognize which (roi_id, cube_index) pairs are already
        on disk, so a caller doesn't append duplicate rows for cubes backed
        up before the app was closed."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            writer = ImagingMeasurementExportWriter(path)
            writer.append_sensorgram_point(1, cube_index=0, timestamp_utc_ms=100, metric_value=0.1)
            writer.append_formula_spectrum(
                1,
                wavelengths_nm=np.asarray([600.0, 650.0]),
                formula_values=np.asarray([0.1, 0.2]),
                sample_mean=np.asarray([1000.0, 1100.0]),
                reference_mean=np.asarray([2000.0, 2100.0]),
                cube_index=0,
                timestamp_utc_ms=100,
            )
            writer.close()

            reopened = ImagingMeasurementExportWriter(path)
            try:
                self.assertEqual(reopened.existing_sensorgram_keys(), {("1", 0, "")})
                self.assertEqual(reopened.existing_formula_spectrum_keys(), {(1, 0, "")})
                reopened.append_sensorgram_point(1, cube_index=1, timestamp_utc_ms=200, metric_value=0.2)
            finally:
                reopened.close()

            trace = read_sensorgram_trace(path, 1)

        np.testing.assert_array_equal(trace["cube_index"], np.asarray([0, 1], dtype=np.int64))
        np.testing.assert_array_equal(trace["timestamp_utc_ms"], np.asarray([100, 200], dtype=np.int64))

    def test_signature_hash_is_recorded_and_distinguishes_recomputed_rows(self) -> None:
        """A row backed up with one signature_hash and a later row for the
        same cube_index under a *different* hash (e.g. after an ROI moved)
        must both be recognized as distinct entries by existing_*_keys() -
        the point of storing the hash is that a changed value supersedes by
        appending, never by being mistaken for an already-current
        duplicate."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path) as writer:
                writer.append_sensorgram_point(1, cube_index=0, timestamp_utc_ms=100, metric_value=0.1, signature_hash="hash-a")
                writer.append_sensorgram_point(1, cube_index=0, timestamp_utc_ms=150, metric_value=0.2, signature_hash="hash-b")
                writer.append_formula_spectrum(
                    1,
                    wavelengths_nm=np.asarray([600.0, 650.0]),
                    formula_values=np.asarray([0.1, 0.2]),
                    sample_mean=np.asarray([1000.0, 1100.0]),
                    reference_mean=np.asarray([2000.0, 2100.0]),
                    cube_index=0,
                    timestamp_utc_ms=100,
                    signature_hash="hash-a",
                )

            reopened = ImagingMeasurementExportWriter(path)
            try:
                self.assertEqual(
                    reopened.existing_sensorgram_keys(), {("1", 0, "hash-a"), ("1", 0, "hash-b")}
                )
                self.assertEqual(reopened.existing_formula_spectrum_keys(), {(1, 0, "hash-a")})
            finally:
                reopened.close()

            trace = read_sensorgram_trace(path, 1)
        np.testing.assert_array_equal(trace["metric_value"], np.asarray([0.1, 0.2]))

    def test_sensorgram_metric_index_keeps_latest_row_per_cube(self) -> None:
        """sensorgram_metric_index() is the read-side counterpart of
        existing_sensorgram_keys() used to skip recomputation (see
        analysis_pipeline_redesign.md §4c item 3): it must return the
        actual metric_value, and when a cube_index has been recomputed under
        a new signature_hash (appended, never overwritten in place - §4d),
        the *later* row must win, not the first one."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path) as writer:
                writer.append_sensorgram_point(1, cube_index=0, timestamp_utc_ms=100, metric_value=0.1, signature_hash="hash-a")
                writer.append_sensorgram_point(1, cube_index=1, timestamp_utc_ms=200, metric_value=0.2, signature_hash="hash-a")
                # Cube 0 recomputed later under a different signature (e.g. an
                # ROI moved) - the writer appends rather than overwriting.
                writer.append_sensorgram_point(1, cube_index=0, timestamp_utc_ms=300, metric_value=0.15, signature_hash="hash-b")

                index = writer.sensorgram_metric_index("1")
                self.assertEqual(index, {0: ("hash-b", 0.15), 1: ("hash-a", 0.2)})
                # No rows for an ROI/combined-key that was never backed up.
                self.assertEqual(writer.sensorgram_metric_index("2"), {})

    def test_reopening_legacy_group_backfills_new_columns_row_aligned(self) -> None:
        """A sensorgram group written before cube_index/signature_hash
        existed (only timestamp_utc_ms/metric_value) must, once reopened and
        appended to, end up with every column the same length - not a new
        column starting at length 0 next to already-populated siblings."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with h5py.File(path, "w") as handle:
                processed = handle.create_group("processed")
                sensorgram_root = processed.create_group("sensorgram")
                legacy_group = sensorgram_root.create_group("1")
                legacy_group.create_dataset(
                    "timestamp_utc_ms", data=np.asarray([100, 200], dtype=np.int64), maxshape=(None,), chunks=True
                )
                legacy_group.create_dataset(
                    "metric_value", data=np.asarray([0.1, 0.2], dtype=np.float64), maxshape=(None,), chunks=True
                )

            writer = ImagingMeasurementExportWriter(path)
            try:
                writer.append_sensorgram_point(1, cube_index=2, timestamp_utc_ms=300, metric_value=0.3, signature_hash="hash-c")
            finally:
                writer.close()

            with h5py.File(path, "r") as handle:
                group = handle["processed"]["sensorgram"]["1"]
                lengths = {name: group[name].shape[0] for name in ("timestamp_utc_ms", "metric_value", "cube_index", "signature_hash")}
                cube_index_values = group["cube_index"][...]
                hash_values = [value.decode("utf-8") if isinstance(value, bytes) else value for value in group["signature_hash"][...]]

        self.assertEqual(lengths, {"timestamp_utc_ms": 3, "metric_value": 3, "cube_index": 3, "signature_hash": 3})
        np.testing.assert_array_equal(cube_index_values, np.asarray([-1, -1, 2], dtype=np.int64))
        self.assertEqual(hash_values, ["", "", "hash-c"])

    def test_missing_roi_returns_empty_traces_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path):
                pass

            sensorgram = read_sensorgram_trace(path, 999)
            formula_spectrum = read_formula_spectra_trace(path, 999)

        self.assertEqual(sensorgram["timestamp_utc_ms"].size, 0)
        self.assertEqual(formula_spectrum["wavelengths_nm"].size, 0)


class ReducedValuesByMethodTests(unittest.TestCase):
    """Schema 6.7: every Reduction method actually computed alongside a row
    (see processing/roi_math.py's reduce_sample_and_reference_all_methods)
    is written into its own reduced_values/<method>/ subgroup, so any of
    them can be recovered later without re-reading pixels - see
    processing/analysis.py's project_reduction_result. The existing flat
    sample_mean/reference_mean/absorbance columns are unaffected."""

    def _write_row(self, writer: ImagingMeasurementExportWriter, *, cube_index: int, reduced_values_by_method=None) -> None:
        writer.append_formula_spectrum(
            1,
            wavelengths_nm=np.asarray([600.0, 650.0]),
            formula_values=np.asarray([0.1, 0.2]),
            sample_mean=np.asarray([1000.0, 1100.0]),
            reference_mean=np.asarray([2000.0, 2100.0]),
            cube_index=cube_index,
            timestamp_utc_ms=100 * (cube_index + 1),
            formula_key="absorbance",
            reduction_method="mean",
            signature_hash=f"hash-{cube_index}",
            reduced_values_by_method=reduced_values_by_method,
        )

    def test_all_methods_round_trip_through_formula_spectrum_index(self) -> None:
        reduced_values_by_method = {
            "mean": (np.asarray([1000.0, 1100.0]), np.asarray([2000.0, 2100.0])),
            "median": (np.asarray([999.0, 1099.0]), np.asarray([1999.0, 2099.0])),
            "trimmed_mean": (np.asarray([1000.5, 1100.5]), np.asarray([2000.5, 2100.5])),
            "plane_fit": (np.asarray([1000.0, 1100.0]), np.asarray([1998.0, 2098.0])),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path) as writer:
                self._write_row(writer, cube_index=0, reduced_values_by_method=reduced_values_by_method)
                trace = writer.formula_spectrum_index(1)

        self.assertIsNotNone(trace)
        stored_hash, by_method = trace.by_cube[0]
        self.assertEqual(stored_hash, "hash-0")
        self.assertEqual(set(by_method.keys()), {"mean", "median", "trimmed_mean", "plane_fit"})
        np.testing.assert_allclose(by_method["median"][0], [999.0, 1099.0], atol=1e-3)
        np.testing.assert_allclose(by_method["median"][1], [1999.0, 2099.0], atol=1e-3)
        np.testing.assert_allclose(by_method["plane_fit"][1], [1998.0, 2098.0], atol=1e-3)
        # The active method's pair matches the flat legacy columns exactly.
        np.testing.assert_allclose(by_method["mean"][0], [1000.0, 1100.0], atol=1e-3)

    def test_none_reduced_values_writes_only_legacy_columns(self) -> None:
        """Optional/backward-compatible: a caller that never passes
        reduced_values_by_method (e.g. a future caller that only computes
        one method) must not create a reduced_values/ group at all - the
        row still round-trips via the flat columns exactly as pre-6.7."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path) as writer:
                self._write_row(writer, cube_index=0, reduced_values_by_method=None)
                trace = writer.formula_spectrum_index(1)

        stored_hash, by_method = trace.by_cube[0]
        self.assertEqual(stored_hash, "hash-0")
        self.assertEqual(set(by_method.keys()), {"mean"})

    def test_reduced_values_backfilled_for_rows_that_predate_the_feature(self) -> None:
        """Two rows written the old way (no reduced_values_by_method), then
        a third written the new way. The new method's subgroup must be
        row-aligned with cube_index (backfilled for rows 0-1), and
        reduced_values_start_row must mark row 2 as the first trustworthy
        one - formula_spectrum_index must NOT report "median" as available
        for rows 0-1 even though the (NaN) array technically has entries
        there."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path) as writer:
                self._write_row(writer, cube_index=0, reduced_values_by_method=None)
                self._write_row(writer, cube_index=1, reduced_values_by_method=None)
                self._write_row(
                    writer,
                    cube_index=2,
                    reduced_values_by_method={
                        "mean": (np.asarray([1000.0, 1100.0]), np.asarray([2000.0, 2100.0])),
                        "median": (np.asarray([777.0, 888.0]), np.asarray([1777.0, 1888.0])),
                    },
                )

                with h5py.File(path, "r") as handle:
                    group = handle["processed"]["absorbance_spectra"]["1"]
                    self.assertEqual(int(group.attrs["reduced_values_start_row"]), 2)
                    self.assertEqual(group["reduced_values"]["median"]["sample_mean"].shape[0], 3)
                    backfilled_rows = group["reduced_values"]["median"]["sample_mean"][0:2]
                    self.assertTrue(np.all(np.isnan(backfilled_rows)))

                trace = writer.formula_spectrum_index(1)

        for cube_index in (0, 1):
            _, by_method = trace.by_cube[cube_index]
            self.assertEqual(set(by_method.keys()), {"mean"}, msg=f"cube {cube_index} should not see the backfilled median entry")
        _, by_method_row2 = trace.by_cube[2]
        self.assertEqual(set(by_method_row2.keys()), {"mean", "median"})
        np.testing.assert_allclose(by_method_row2["median"][0], [777.0, 888.0], atol=1e-3)

    def test_method_and_formula_definitions_are_stamped_and_stable(self) -> None:
        """Reproducibility catalog (see _stamp_method_definitions): written
        once as JSON attrs on the parent group, and never overwritten by a
        later append (so a newer app version's catalog text never silently
        replaces what was actually true when the file's data was written)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.h5"
            with ImagingMeasurementExportWriter(path) as writer:
                self._write_row(writer, cube_index=0, reduced_values_by_method=None)
                with h5py.File(path, "r") as handle:
                    parent = handle["processed"]["absorbance_spectra"]
                    reduction_definitions = json.loads(parent.attrs["reduction_method_definitions"])
                    formula_definitions = json.loads(parent.attrs["formula_key_definitions"])
                self.assertEqual(set(reduction_definitions.keys()), {"mean", "median", "trimmed_mean", "plane_fit"})
                self.assertEqual(set(formula_definitions.keys()), {"absorbance", "ratio", "relative_change", "mod_absorbance"})
                self.assertIn("plane", reduction_definitions["plane_fit"].lower())

                # Tamper with the stamped value, then append again - a real
                # append must not restamp/overwrite it.
                with h5py.File(path, "a") as handle:
                    handle["processed"]["absorbance_spectra"].attrs["reduction_method_definitions"] = json.dumps({"custom": "value"})
                self._write_row(writer, cube_index=1, reduced_values_by_method=None)
                with h5py.File(path, "r") as handle:
                    self.assertEqual(
                        json.loads(handle["processed"]["absorbance_spectra"].attrs["reduction_method_definitions"]),
                        {"custom": "value"},
                    )


if __name__ == "__main__":
    unittest.main()
