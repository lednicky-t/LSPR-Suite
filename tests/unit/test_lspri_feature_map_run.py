"""`image_tools/mask/feature_map_run.py` (2026-11-04): the feature map's heavy loops on worker processes. No Qt, no dataset.

Tuning changes scheduling, never results: the map of the synthetic picture of `test_lspri_feature_map.py` is identical with the
plain loop, one process and two worker processes; progress stays within each stage's share and ends at 1; Cancel stops a run.
"""

from __future__ import annotations

import sys
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.mask import feature_map as fm
    from lspr_imaging_app.image_tools.mask.feature_map_run import STAGE_RANGES, ProcessMapper
    from lspr_imaging_app.progress import Cancelled
    from tests.unit import test_lspri_feature_map as synth
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)


class ProcessMapperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.logs = synth.picture()
        cls.reference = fm.feature_map(cls.logs)  # the plain loop

    def _assert_same(self, other: fm.FeatureMap) -> None:
        self.assertEqual([d.centre for d in other.disks], [d.centre for d in self.reference.disks])
        for kind in fm.KINDS:
            np.testing.assert_array_equal(other.kind_masks[kind], self.reference.kind_masks[kind], err_msg=kind)

    def test_identical_at_one_and_two_processes(self) -> None:
        for processes in (1, 2):
            with self.subTest(processes=processes), ProcessMapper(processes=processes) as mapper:
                self._assert_same(fm.feature_map(self.logs, mapper=mapper))

    def test_progress_stays_in_its_stage_and_ends_at_one(self) -> None:
        seen: list[float] = []
        with ProcessMapper(processes=2, progress=lambda f, _m: seen.append(f)) as mapper:
            fm.prepare(self.logs, mapper=mapper, progress=lambda f, _m: seen.append(f))
        self.assertTrue(all(0.0 <= f <= 1.0 for f in seen))
        self.assertEqual(seen[-1], 1.0)
        start, end = STAGE_RANGES["modelling the wavelengths"]
        self.assertIn(end, seen)  # the last wavelength reports the end of its share

    def test_cancel_stops_the_run(self) -> None:
        calls = {"n": 0}

        def cancelled() -> bool:
            calls["n"] += 1
            return calls["n"] > 3  # a few checks in: during the disk fits

        with self.assertRaises(Cancelled), ProcessMapper(processes=2, cancelled=cancelled) as mapper:
            fm.prepare(self.logs, mapper=mapper, cancelled=cancelled)


if __name__ == "__main__":
    unittest.main()
