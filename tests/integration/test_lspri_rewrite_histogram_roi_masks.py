"""`RoiMaskProvider`: the Histogram panel's cached, background-built union of
every ROI's masks.

What it pins: a result is reused until the ROIs, image shape, affine or ring
defaults change (selecting a ROI re-renders the image and must not rebuild
masks); small ROI counts come back inline; large ones come back through
`ready(key)` with exactly the masks `union_roi_masks` gives; the latest request
wins; a change to the ROIs while a build is running drops that stale result.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch.**
"""

from __future__ import annotations

import sys
import time
import unittest
from unittest import mock

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np  # noqa: E402

try:
    from lspr_imaging_app.image_tools.chromatic.affine import identity_affine_matrix
    from lspr_imaging_app.panels.histogram import roi_masks
    from lspr_imaging_app.panels.histogram.roi_masks import RoiMaskProvider
    from lspr_imaging_app.roi.model import AreaRoi
    from lspr_imaging_app.roi.rasterize import union_roi_masks
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

_SHAPE = (200, 300)
_AFFINE = np.array([[1.0005, 0.0003, 1.2], [-0.0002, 0.9996, -0.7]])
_RING = (28.0, 36.0)


def _rois(count: int, seed: int = 3) -> list[AreaRoi]:
    rng = np.random.default_rng(seed)
    return [
        AreaRoi(
            area_roi_id=i + 1,
            center_x=float(rng.uniform(10, _SHAPE[1] - 10)),
            center_y=float(rng.uniform(10, _SHAPE[0] - 10)),
            sample_diameter_px=12.0,
        )
        for i in range(count)
    ]


def _wait_until(predicate, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


class RoiMaskProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = RoiMaskProvider()
        self.ready: list = []
        self.provider.ready.connect(self.ready.append)

    def tearDown(self) -> None:
        self.provider.stop()
        self.provider.deleteLater()

    def _request(self, rois, affine=None):
        return self.provider.request(rois, _SHAPE, identity_affine_matrix() if affine is None else affine, *_RING)

    def _expected(self, rois, affine=None):
        return union_roi_masks(
            rois, _SHAPE, identity_affine_matrix() if affine is None else affine,
            default_inner_diameter_px=_RING[0], default_outer_diameter_px=_RING[1],
        )

    # -- inline (few ROIs) --------------------------------------------------------

    def test_few_rois_come_back_inline_and_equal_the_direct_union(self) -> None:
        rois = _rois(5)
        _, masks = self._request(rois)
        self.assertIsNotNone(masks)
        for got, want in zip(masks, self._expected(rois), strict=True):
            np.testing.assert_array_equal(got, want)
        self.assertEqual(self.ready, [])  # inline: nothing to announce

    def test_the_same_request_is_served_from_the_cache(self) -> None:
        rois = _rois(5)
        with mock.patch.object(roi_masks, "union_roi_masks", wraps=union_roi_masks) as union:
            _, first = self._request(rois)
            _, second = self._request(rois)  # e.g. a ROI was only selected: the image re-rendered, nothing else changed
        self.assertEqual(union.call_count, 1)
        self.assertIs(first, second)

    def test_a_different_affine_shape_or_ring_is_a_different_answer(self) -> None:
        rois = _rois(5)
        with mock.patch.object(roi_masks, "union_roi_masks", wraps=union_roi_masks) as union:
            self._request(rois)
            self._request(rois, _AFFINE)  # another wavelength with a chromatic correction
            self.provider.request(rois, (100, 150), identity_affine_matrix(), *_RING)
            self.provider.request(rois, _SHAPE, identity_affine_matrix(), 30.0, 40.0)
        self.assertEqual(union.call_count, 4)

    def test_invalidate_forces_a_rebuild(self) -> None:
        rois = _rois(5)
        with mock.patch.object(roi_masks, "union_roi_masks", wraps=union_roi_masks) as union:
            self._request(rois)
            self.provider.invalidate()  # RoiToolbox.geometry_changed
            self._request(rois)
        self.assertEqual(union.call_count, 2)

    # -- background (many ROIs) ---------------------------------------------------

    def test_many_rois_arrive_through_ready_and_equal_the_direct_union(self) -> None:
        rois = _rois(60)
        with mock.patch.object(roi_masks, "SYNC_ROI_LIMIT", 10):
            key, masks = self._request(rois)
            self.assertIsNone(masks)
            self.assertTrue(_wait_until(lambda: bool(self.ready)))
        self.assertEqual(self.ready, [key])
        got = self.provider.cached(key)
        for g, w in zip(got, self._expected(rois), strict=True):
            np.testing.assert_array_equal(g, w)
        self.assertIs(self._request(rois)[1], got)  # and it is cached for the next redraw

    def test_the_background_job_works_on_a_snapshot_not_the_live_rois(self) -> None:
        rois = _rois(60)
        before = self._expected(rois)
        with mock.patch.object(roi_masks, "SYNC_ROI_LIMIT", 10):
            key, _ = self._request(rois)
            for roi in rois:  # the GUI thread edits ROIs in place, e.g. a drag
                roi.center_x += 50.0
            self.assertTrue(_wait_until(lambda: bool(self.ready)))
        got = self.provider.cached(key)
        np.testing.assert_array_equal(got[0], before[0])

    def test_latest_request_wins(self) -> None:
        rois_a, rois_b = _rois(150, seed=1), _rois(150, seed=2)
        with mock.patch.object(roi_masks, "SYNC_ROI_LIMIT", 10):
            key_a, _ = self._request(rois_a)
            key_b, _ = self._request(rois_b, _AFFINE)
            self.assertNotEqual(key_a, key_b)
            self.assertTrue(_wait_until(lambda: bool(self.ready)))
            _wait_until(lambda: False, 0.2)  # nothing else may arrive after it
        self.assertEqual(self.ready, [key_b])
        for g, w in zip(self.provider.cached(key_b), self._expected(rois_b, _AFFINE), strict=True):
            np.testing.assert_array_equal(g, w)

    def test_a_roi_change_during_a_build_drops_that_result(self) -> None:
        rois = _rois(200)
        with mock.patch.object(roi_masks, "SYNC_ROI_LIMIT", 10):
            key, _ = self._request(rois)
            self.provider.invalidate()
            _wait_until(lambda: False, 0.5)
            self.assertEqual(self.ready, [])
            self.assertIsNone(self.provider.cached(key))
            new_key, masks = self._request(rois)
            self.assertIsNone(masks)
            self.assertTrue(_wait_until(lambda: self.ready == [new_key]))

    def test_stop_is_safe_idle_and_while_running(self) -> None:
        self.provider.stop()
        with mock.patch.object(roi_masks, "SYNC_ROI_LIMIT", 10):
            self._request(_rois(200))
            self.provider.stop()
        self.assertFalse(self.provider._worker.is_running())


if __name__ == "__main__":
    unittest.main()
