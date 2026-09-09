"""Coverage for AnalysisWorkerMixin._backup_per_roi_sensorgram_points - the
sibling of _backup_sensorgram_point that backs up each selected ROI's OWN
sensorgram metric under its real roi_id, in addition to (never instead of)
the existing combined-selection row. Isolates the method's own logic
(iteration, dedup, RAM-buffer-while-running vs. immediate-write, writer
calls) from _sensorgram_point_signature_hash_cube_context/_for_roi's own
real (heavily window-state-dependent) content, which is exercised elsewhere
(test_lspri_sensorgram_signature_hash_for_roi.py, which also proves that
pairing produces byte-identical hashes to the original, expensive per-ROI
_sensorgram_point_signature_hash call it replaced) - here both are stubbed
to a deterministic stand-in so this test only needs to check that they're
*called* correctly and the result is used, not re-derive real signature
logic.
"""

from __future__ import annotations

import math
import sys
import unittest
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import AreaRoi  # noqa: E402
from lspr_imaging_app.gui.analysis_worker_mixin import AnalysisWorkerMixin  # noqa: E402


class _FakeWriter:
    def __init__(self) -> None:
        self.metric_calls: list[tuple] = []
        self.point_calls: list[tuple] = []

    def set_sensorgram_metric(self, roi_id, *, metric_name, formula_key, combined_roi_ids):
        self.metric_calls.append((roi_id, metric_name, formula_key, combined_roi_ids))

    def append_sensorgram_point(self, roi_id, *, cube_index, signature_hash, timestamp_utc_ms, metric_value):
        self.point_calls.append((roi_id, cube_index, signature_hash, timestamp_utc_ms, metric_value))


def _make_mixin(*, running: bool, writer: _FakeWriter | None = None):
    mixin = AnalysisWorkerMixin.__new__(AnalysisWorkerMixin)
    if writer is None:
        writer = _FakeWriter()
    rois = [
        AreaRoi(area_roi_id=1, center_x=0.0, center_y=0.0, sample_radius_px=5.0),
        AreaRoi(area_roi_id=2, center_x=10.0, center_y=10.0, sample_radius_px=5.0),
    ]
    window = SimpleNamespace(
        _measurement_export_writer=writer,
        _state=SimpleNamespace(area_rois=rois),
        _measurement_export_backed_up_sensorgram=set(),
        _sensorgram_running=running,
        _sensorgram_backup_buffer={},
        _analysis_metric_key=lambda: "centroid",
    )
    mixin.window = window
    mixin._active_formula_key = lambda: "absorbance"
    mixin._acquisition_timestamp_ms_for_cube = lambda cube_index: 1000 + int(cube_index)
    # cube_context is just a tuple carrying cube_index through, matching the
    # real method's shape closely enough for _sensorgram_point_signature_
    # hash_for_roi's stub below to build the same "sig-<roi_id>-<cube>"
    # strings the tests assert on.
    mixin._sensorgram_point_signature_hash_cube_context = lambda cube_index: ("ctx", cube_index)
    mixin._sensorgram_point_signature_hash_for_roi = lambda cube_context, roi: f"sig-{roi.area_roi_id}-{cube_context[1]}"
    return mixin, writer, window


class TestBackupPerRoiSensorgramPoints(unittest.TestCase):
    def test_writes_one_row_per_roi_under_its_real_roi_id(self) -> None:
        mixin, writer, _window = _make_mixin(running=False)
        mixin._backup_per_roi_sensorgram_points({1: (1.5, 0.2), 2: (2.5, 0.3)}, cube_index=7)
        self.assertEqual(len(writer.point_calls), 2)
        by_roi = {call[0]: call for call in writer.point_calls}
        self.assertEqual(set(by_roi.keys()), {"1", "2"})
        self.assertEqual(by_roi["1"][1:], (7, "sig-1-7", 1007, 1.5))
        self.assertEqual(by_roi["2"][1:], (7, "sig-2-7", 1007, 2.5))
        # combined_roi_ids must be empty for a single real ROI - not the
        # synthetic "combined_..." grouping used for a multi-ROI selection.
        self.assertTrue(all(call[3] == "" for call in writer.metric_calls))

    def test_buffers_in_ram_while_running_instead_of_writing(self) -> None:
        mixin, writer, window = _make_mixin(running=True)
        mixin._backup_per_roi_sensorgram_points({1: (1.5, 0.2)}, cube_index=3)
        self.assertEqual(writer.point_calls, [])
        self.assertIn("1", window._sensorgram_backup_buffer)
        self.assertEqual(window._sensorgram_backup_buffer["1"], [(3, "sig-1-3", 1.5, 1003)])

    def test_dedup_skips_an_already_backed_up_roi_but_not_others(self) -> None:
        mixin, writer, window = _make_mixin(running=False)
        window._measurement_export_backed_up_sensorgram.add(("1", 7, "sig-1-7"))
        mixin._backup_per_roi_sensorgram_points({1: (1.5, 0.2), 2: (2.5, 0.3)}, cube_index=7)
        written_roi_ids = {call[0] for call in writer.point_calls}
        self.assertEqual(written_roi_ids, {"2"})

    def test_nan_metric_value_is_skipped(self) -> None:
        mixin, writer, _window = _make_mixin(running=False)
        mixin._backup_per_roi_sensorgram_points({1: (math.nan, math.nan)}, cube_index=7)
        self.assertEqual(writer.point_calls, [])
        self.assertEqual(writer.metric_calls, [])

    def test_roi_id_not_in_area_rois_is_skipped_without_error(self) -> None:
        mixin, writer, _window = _make_mixin(running=False)
        mixin._backup_per_roi_sensorgram_points({99: (1.0, 0.1)}, cube_index=7)
        self.assertEqual(writer.point_calls, [])

    def test_no_writer_is_a_no_op(self) -> None:
        mixin, writer, window = _make_mixin(running=False)
        window._measurement_export_writer = None
        mixin._backup_per_roi_sensorgram_points({1: (1.5, 0.2)}, cube_index=7)
        self.assertEqual(writer.point_calls, [])

    def test_empty_input_is_a_no_op(self) -> None:
        mixin, writer, _window = _make_mixin(running=False)
        mixin._backup_per_roi_sensorgram_points({}, cube_index=7)
        mixin._backup_per_roi_sensorgram_points(None, cube_index=7)
        self.assertEqual(writer.point_calls, [])

    def test_no_cube_context_is_a_no_op(self) -> None:
        # Mirrors _sensorgram_point_signature_hash_cube_context returning
        # None (e.g. no dataset loaded) - must not crash trying to unpack it.
        mixin, writer, _window = _make_mixin(running=False)
        mixin._sensorgram_point_signature_hash_cube_context = lambda cube_index: None
        mixin._backup_per_roi_sensorgram_points({1: (1.5, 0.2)}, cube_index=7)
        self.assertEqual(writer.point_calls, [])

    def test_cube_context_computed_once_not_per_roi(self) -> None:
        # The whole point of this pairing (see _sensorgram_point_signature_
        # hash_cube_context's docstring): the expensive cube-level context
        # must be built once per cube, not once per ROI.
        mixin, writer, _window = _make_mixin(running=False)
        calls = []
        real_cube_context = mixin._sensorgram_point_signature_hash_cube_context
        mixin._sensorgram_point_signature_hash_cube_context = lambda cube_index: (calls.append(cube_index), real_cube_context(cube_index))[1]
        mixin._backup_per_roi_sensorgram_points({1: (1.5, 0.2), 2: (2.5, 0.3)}, cube_index=7)
        self.assertEqual(calls, [7])


if __name__ == "__main__":
    unittest.main()
