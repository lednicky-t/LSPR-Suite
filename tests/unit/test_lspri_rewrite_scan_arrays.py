"""Pure-logic tests for `lspr_imaging_app.analysis.scan_arrays`: where a finished cube lands in the ``[cube, roi, wavelength, side]``
arrays of the result file. No Qt, no files."""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.scan import CubeResult, ScanSettings
    from lspr_imaging_app.analysis.scan_arrays import new_arrays, place
    from lspr_imaging_app.analysis.scan_jobs import ScanPlan
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.roi.model import AreaRoiDetectionSettings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402


def _plan(positions):
    settings = ScanSettings(GeometrySettings(), BackgroundSettings(), AreaRoiDetectionSettings(), ("mean",))
    return ScanPlan(settings, (), (10, 11), (500.0, 510.0, 520.0), tuple(positions), (1, 2))


def _result(cube_pos, n_frames, fill):
    shape = (2, n_frames, 2)
    return CubeResult(cube_pos, {"mean": np.full(shape, float(fill))}, np.full(shape, fill / 10.0), np.full(shape, 5, dtype=np.int32),
                      np.full(shape, 6, dtype=np.int32), np.full(shape, 2, dtype=np.int32))


class PlaceTests(unittest.TestCase):
    def test_a_cube_lands_on_its_own_columns_and_the_rest_stays_empty(self) -> None:
        plan = _plan([(0, 1, 2), (0, 2)])  # the second cube has no 510 nm frame
        arrays = new_arrays(plan)
        self.assertEqual(arrays.values["mean"].shape, (2, 2, 3, 2))
        place(arrays, plan, _result(1, 2, 7.0))
        self.assertTrue(np.all(arrays.values["mean"][1][:, [0, 2], :] == 7.0))
        self.assertTrue(np.all(np.isnan(arrays.values["mean"][1][:, 1, :])))  # the missing frame is NaN, not 0
        self.assertTrue(np.all(arrays.n_valid[1][:, 1, :] == 0))
        self.assertTrue(np.all(arrays.n_valid[1][:, [0, 2], :] == 5))
        self.assertTrue(np.all(arrays.n_nominal[1][:, [0, 2], :] == 6))
        self.assertTrue(np.all(arrays.n_masked[1][:, [0, 2], :] == 2))
        self.assertTrue(np.all(arrays.n_masked[1][:, 1, :] == 0))
        self.assertTrue(np.all(np.isnan(arrays.values["mean"][0])))  # cube 0 not placed yet
        self.assertEqual(list(arrays.done), [False, True])

    def test_completion_order_does_not_change_the_arrays(self) -> None:
        plan = _plan([(0, 1, 2), (0, 1, 2)])
        a, b = new_arrays(plan), new_arrays(plan)
        for pos in (0, 1):
            place(a, plan, _result(pos, 3, pos + 1.0))
        for pos in (1, 0):
            place(b, plan, _result(pos, 3, pos + 1.0))
        self.assertTrue(np.array_equal(a.values["mean"], b.values["mean"]))
        self.assertTrue(np.array_equal(a.std, b.std))
        self.assertEqual(a.n_done, 2)

    def test_placing_a_cube_twice_is_refused(self) -> None:
        plan = _plan([(0, 1, 2), (0, 1, 2)])
        arrays = new_arrays(plan)
        place(arrays, plan, _result(0, 3, 1.0))
        with self.assertRaises(ValueError):
            place(arrays, plan, _result(0, 3, 2.0))


if __name__ == "__main__":
    unittest.main()
