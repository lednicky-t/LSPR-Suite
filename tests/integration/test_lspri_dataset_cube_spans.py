"""`DatasetModule.cube_spans_unix_ms` (2026-10-26): the first and last frame time of each cube, which the step statistics use to tell whether a cube lies wholly inside a step."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    import numpy as np

    from lspr_core import ImagingAcquisitionMetadata, ImagingCubeTiming
    from lspr_imaging_app.dataset.model import ImageDataset
    from lspr_imaging_app.dataset.module import DatasetModule
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

T0 = 1_700_000_000_000


def _dataset(root: Path) -> ImageDataset:
    timings = [
        ImagingCubeTiming(spectral_cube_index=cube, wavelength_nm=wl, acquired_at_unix_ms=T0 + offset)
        for cube, wl, offset in ((0, 600.0, 0), (0, 610.0, 100), (0, 620.0, 200), (1, 600.0, 1000), (1, 610.0, 1100), (3, 600.0, 3000))  # cube 2 has no timing
    ]
    return ImageDataset(folder=root, records=[], source_format="test", acquisition_metadata=ImagingAcquisitionMetadata(source_format="test", image_timings=timings))


class CubeSpansTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.module = DatasetModule()

    def tearDown(self) -> None:
        self.module.deleteLater()
        self._tmp.cleanup()

    def test_first_and_last_frame_of_each_cube_in_the_order_asked(self) -> None:
        self.module.load_dataset(_dataset(Path(self._tmp.name)))
        first, last = self.module.cube_spans_unix_ms([3, 0, 1])
        np.testing.assert_array_equal(first, [T0 + 3000, T0, T0 + 1000])
        np.testing.assert_array_equal(last, [T0 + 3000, T0 + 200, T0 + 1100])  # a cube with one frame: first == last
        self.assertEqual(first.dtype, np.float64)

    def test_a_cube_without_timing_is_nan(self) -> None:
        self.module.load_dataset(_dataset(Path(self._tmp.name)))
        first, last = self.module.cube_spans_unix_ms([0, 2, 99])
        self.assertEqual(first[0], T0)
        self.assertTrue(np.isnan(first[1:]).all() and np.isnan(last[1:]).all())

    def test_agrees_with_cube_start_unix_ms(self) -> None:
        self.module.load_dataset(_dataset(Path(self._tmp.name)))
        first, _ = self.module.cube_spans_unix_ms([0, 1, 3])
        self.assertEqual(first.tolist(), [self.module.cube_start_unix_ms(c) for c in (0, 1, 3)])

    def test_no_dataset_gives_nan(self) -> None:
        first, last = self.module.cube_spans_unix_ms([0, 1])
        self.assertTrue(np.isnan(first).all() and np.isnan(last).all())
        self.assertEqual(first.shape, (2,))


if __name__ == "__main__":
    unittest.main()
