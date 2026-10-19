"""Integration test for analysis step 1's runner (`lspr_imaging_app.analysis.scan`): small TIFF files on disk, read ->
prepare at the ROI pixels -> reduce, in this process and in worker processes. The result must equal the plain path
(`apply_preprocessing` on the whole frame + `reduce_frame`) exactly. Only runs when the `apps/LSPRi/eva` submodule is on
its `rewrite` branch. No Qt.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.frame_reduce import build_roi_set, reduce_frame
    from lspr_imaging_app.analysis.scan import CubeJob, FrameJob, ScanSettings, scan_cubes
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.image_tools.preprocess import apply_preprocessing
    from lspr_imaging_app.progress import Cancelled
    from lspr_imaging_app.roi.model import AreaRoi, AreaRoiDetectionSettings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

H, W = 80, 110
AFFINES = [np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]), np.array([[1.003, 0.001, -1.2], [-0.001, 0.998, 0.7]])]


def _rois():
    return [AreaRoi(i + 1, 18.0 + 25.0 * (i % 4), 18.0 + 24.0 * (i // 4), 10.0, reference_inner_diameter_px=14.0,
                    reference_outer_diameter_px=20.0) for i in range(12)]


class ScanRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        rng = np.random.default_rng(4)
        y, x = np.mgrid[0:H, 0:W]
        cls.paths = {}
        for cube in range(3):
            for k in range(2):
                frame = (3000.0 + 9.0 * x + 5.0 * y + 40.0 * cube + rng.normal(0, 25.0, (H, W))).astype(np.float32)
                path = Path(cls.tmp.name) / f"c{cube}_w{k}.tif"
                tifffile.imwrite(path, frame)
                cls.paths[(cube, k)] = str(path)
        cls.settings = ScanSettings(
            geometry=GeometrySettings(rotation_angle_deg=1.0),
            background=BackgroundSettings(flatten_background_enabled=True, flatten_background_sigma_px=10.0,
                                          flatten_background_binning=2, flatten_background_exclude_mask=True),
            detection=AreaRoiDetectionSettings(ignore_marked_pixels=True),
            methods=("mean", "median"),
            fast_tiff_decoder=False,
        )
        cls.rs = build_roi_set(_rois(), 14.0, 20.0)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _processed_shape(self):
        raw = tifffile.imread(self.paths[(0, 0)])
        return apply_preprocessing(raw, self.settings.geometry, BackgroundSettings()).shape[:2]

    def _jobs(self):
        shape = self._processed_shape()
        ignore = np.zeros(shape, dtype=bool)
        ignore[20:30, 30:45] = True
        return [
            CubeJob(cube, self.rs, tuple(FrameJob(self.paths[(cube, k)], AFFINES[k], ignore if (cube, k) == (1, 0) else None) for k in range(2)))
            for cube in range(3)
        ]

    def _expected(self, job: CubeJob, k: int):
        frame = job.frames[k]
        raw = tifffile.imread(frame.path)
        s = self.settings
        processed = apply_preprocessing(raw, s.geometry, s.background, rois=list(self.rs.rois), mask_settings=s.detection,
                                        external_mask=frame.ignore)
        return reduce_frame(self.rs, processed, frame.ignore, frame.affine, s.methods, exclusion_mode=s.exclusion_mode)

    def _check(self, results):
        jobs = self._jobs()
        self.assertEqual(sorted(r.cube_pos for r in results), [0, 1, 2])
        for r in results:
            for k in range(2):
                expected = self._expected(jobs[r.cube_pos], k)
                for m in self.settings.methods:
                    self.assertTrue(np.array_equal(r.values[m][:, k, :], expected.values[m], equal_nan=True), (r.cube_pos, k, m))
                self.assertTrue(np.array_equal(r.std[:, k, :], expected.std, equal_nan=True))
                self.assertTrue(np.array_equal(r.n_valid[:, k, :], expected.n_valid))
                self.assertTrue(np.array_equal(r.n_nominal[:, k, :], expected.n_nominal))

    def test_in_process_equals_the_plain_path(self) -> None:
        seen = []
        results = list(scan_cubes(self._jobs(), self.settings, processes=1, progress=lambda f, m: seen.append(f)))
        self._check(results)
        self.assertEqual(seen[-1], 1.0)

    def test_worker_processes_equal_the_plain_path(self) -> None:
        self._check(list(scan_cubes(self._jobs(), self.settings, processes=2)))

    def test_cancel_raises_and_keeps_finished_cubes(self) -> None:
        got = []
        with self.assertRaises(Cancelled):
            for result in scan_cubes(self._jobs(), self.settings, processes=1, cancelled=lambda: len(got) >= 1):
                got.append(result)
        self.assertEqual(len(got), 1)


if __name__ == "__main__":
    unittest.main()
