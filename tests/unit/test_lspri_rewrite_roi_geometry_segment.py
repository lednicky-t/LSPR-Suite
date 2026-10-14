"""`RoiToolbox.geometry_segment`: which cubes show the same ROI geometry (the overlay cache keys on it).

Property pinned: two cubes with the same label have the same geometry for EVERY ROI; and a timeline with one edit does not
make every cube look different (testing12: 3 ROIs with one persistent change at cube 79 made all 314 cubes distinct).
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.model import SCOPE_PERSISTENT
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

CUBES = range(0, 31)


def _geometry_signature(toolbox: "RoiToolbox", cube: int) -> tuple:
    return tuple(
        (r.area_roi_id, r.center_x, r.center_y, r.sample_diameter_px, r.reference_inner_diameter_px, r.reference_outer_diameter_px)
        for r in toolbox.rois_at(cube)
    )


class GeometrySegmentTests(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.a = self.toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        self.b = self.toolbox.add_roi(90.0, 30.0, sample_diameter_px=10.0)
        self.c = self.toolbox.add_roi(140.0, 30.0, sample_diameter_px=10.0)

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_without_a_timeline_every_cube_has_the_same_label(self) -> None:
        self.assertEqual({self.toolbox.geometry_segment(c) for c in CUBES}, {()})
        self.assertEqual(self.toolbox.geometry_segment(None), ())

    def test_one_persistent_change_makes_two_segments_not_one_per_cube(self) -> None:
        self.toolbox.move_roi(self.a, 45.0, 33.0, cube=12, scope=SCOPE_PERSISTENT)
        labels = {c: self.toolbox.geometry_segment(c) for c in CUBES}
        self.assertEqual(len(set(labels.values())), 2)
        self.assertEqual({labels[c] for c in range(0, 12)}, {labels[0]})
        self.assertEqual({labels[c] for c in range(12, 31)}, {labels[12]})
        self.assertNotEqual(labels[11], labels[12])

    def test_same_label_means_same_geometry_for_every_roi(self) -> None:
        self.toolbox.move_roi(self.a, 45.0, 33.0, cube=5, scope=SCOPE_PERSISTENT)
        self.toolbox.move_roi(self.b, 95.0, 36.0, cube=20, scope=SCOPE_PERSISTENT)
        self.toolbox.move_roi(self.c, 150.0, 28.0, cube=12, scope="individual")
        self.toolbox.move_roi(self.a, 41.0, 31.0, cube=5, scope="individual")
        by_label: dict[tuple, set] = {}
        for cube in CUBES:
            by_label.setdefault(self.toolbox.geometry_segment(cube), set()).add(_geometry_signature(self.toolbox, cube))
        for label, signatures in by_label.items():
            self.assertEqual(len(signatures), 1, f"{label}: cubes with one label show different geometry")
        self.assertGreaterEqual(len(by_label), 4)  # [0,4], 5 alone, [6,19] and 12 alone, [20,..]

    def test_an_individual_change_only_marks_its_own_cube(self) -> None:
        self.toolbox.move_roi(self.a, 45.0, 33.0, cube=8, scope="individual")
        self.assertEqual(self.toolbox.geometry_segment(8), ("single", 8))
        self.assertEqual(self.toolbox.geometry_segment(7), self.toolbox.geometry_segment(9))
        self.assertNotEqual(self.toolbox.geometry_segment(8), self.toolbox.geometry_segment(9))


if __name__ == "__main__":
    unittest.main()
