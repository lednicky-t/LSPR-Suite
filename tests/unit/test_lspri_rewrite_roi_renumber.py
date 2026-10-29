"""ROI renumbering: the `roi_ids_renumbered` signal contract and the numbering-independent exclusion digests.

When a delete closes the gap, or a fresh detection reuses 1..N, the id of a ROI changes or is reused; whatever holds a ROI id
(the selection) follows the signal. The digests must not change when only the numbering does. (The tests of the former analysis
engine's stored results, which also lived here, went with the engine on 2026-10-29.)

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch**, like the
other `test_lspri_rewrite_*` files.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.provenance import (
        background_exclusion_digest,
        sample_exclusion_digest,
    )
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.model import AreaRoi, AreaRoiDetectionSettings
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _roi(roi_id: int, x: float, y: float, diameter: float = 6.0) -> AreaRoi:
    return AreaRoi(area_roi_id=roi_id, center_x=x, center_y=y, sample_diameter_px=diameter)


class ExclusionDigestIgnoresNumberingTest(unittest.TestCase):
    """A reorder must not look like a change to the set of excluded circles,
    or it would mark every stored cell stale in those modes."""

    def setUp(self) -> None:
        self.a = _roi(1, 40.0, 30.0)
        self.b = _roi(2, 47.0, 34.0, diameter=8.0)
        self.c = _roi(3, 20.0, 10.0)

    def _swapped(self) -> list[AreaRoi]:
        return [replace(self.b, area_roi_id=1), replace(self.a, area_roi_id=2), self.c]

    def test_the_sample_digest_is_the_same_after_a_reorder(self) -> None:
        before = sample_exclusion_digest([self.a, self.b, self.c])
        self.assertEqual(sample_exclusion_digest(self._swapped()), before)
        self.assertEqual(sample_exclusion_digest(list(reversed([self.a, self.b, self.c]))), before)

    def test_the_sample_digest_still_changes_when_a_circle_moves(self) -> None:
        before = sample_exclusion_digest([self.a, self.b, self.c])
        moved = [replace(self.a, center_x=41.0), self.b, self.c]
        self.assertNotEqual(sample_exclusion_digest(moved), before)

    def test_the_background_digest_is_the_same_after_a_reorder(self) -> None:
        settings = BackgroundSettings(flatten_background_enabled=True, flatten_background_exclude_area_rois=True)
        detection = AreaRoiDetectionSettings()
        before = background_exclusion_digest([self.a, self.b, self.c], settings, detection)
        self.assertIsNotNone(before)
        self.assertEqual(background_exclusion_digest(self._swapped(), settings, detection), before)

    def test_the_background_digest_still_changes_when_a_circle_resizes(self) -> None:
        settings = BackgroundSettings(flatten_background_enabled=True, flatten_background_exclude_area_rois=True)
        detection = AreaRoiDetectionSettings()
        before = background_exclusion_digest([self.a, self.b, self.c], settings, detection)
        resized = [replace(self.a, sample_diameter_px=9.0), self.b, self.c]
        self.assertNotEqual(background_exclusion_digest(resized, settings, detection), before)


class RenumberSignalContractTest(unittest.TestCase):
    """`roi_ids_renumbered` carries `{old: new}` for every ROI that survives;
    an id absent from it is gone. Subscribers (the selection) rely on that, including for the all-deleted and re-detected
    cases, where an empty map is the whole message."""

    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.maps: list[dict[int, int]] = []
        self.toolbox.roi_ids_renumbered.connect(self.maps.append)
        for x in (10.0, 20.0, 30.0):
            self.toolbox.add_roi(x, 5.0)

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_deleting_one_roi_maps_every_survivor_and_undo_maps_back(self) -> None:
        self.toolbox.delete_rois((1,))
        self.assertEqual(self.maps, [{2: 1, 3: 2}])
        undo_manager.undo()
        self.assertEqual(self.maps[-1], {1: 2, 2: 3})

    def test_deleting_every_roi_still_announces_it(self) -> None:
        self.toolbox.delete_rois((1, 2, 3))
        self.assertEqual(self.maps, [{}])

    def test_a_fresh_detection_announces_an_empty_map_both_ways(self) -> None:
        self.toolbox.detect_rois([_roi(1, 50.0, 50.0), _roi(2, 60.0, 60.0)])
        self.assertEqual(self.maps, [{}])
        undo_manager.undo()
        self.assertEqual(self.maps, [{}, {}])
        self.assertEqual(sorted(roi.area_roi_id for roi in self.toolbox.rois()), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
