"""The Venn picture of each selection mode says what the mode really does (2026-11-15).

`combine_mode_icons.MODE_AREAS` states, for the three areas of a Venn diagram (A only = the selection now, both, B only = the new region),
whether the mode leaves them selected, takes them away from the old selection, or leaves them unselected. The icons are drawn from that
table; this test applies the real combine rule of `AreaSelectionModule` to two overlapping discs and compares, so a picture can never say
something the mode does not do. No Qt event loop, no drawing, no files.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image.combine_mode_icons import MODE_AREAS, NOT_SELECTED, REMOVED, SELECTED
    from lspr_imaging_app.selection import AreaSelectionModule
    from lspr_imaging_app.selection.area_selection_module import COMBINE_MODES, REPLACE
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

SHAPE = (60, 90)


def _disc(row: int, col: int, radius: float) -> np.ndarray:
    yy, xx = np.mgrid[0 : SHAPE[0], 0 : SHAPE[1]]
    return np.hypot(yy - row, xx - col) <= radius


class CombineModeIconsTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.old = _disc(30, 35, 18)  # A: the selection now
        self.new = _disc(30, 55, 18)  # B: the new region
        self.areas = (self.old & ~self.new, self.old & self.new, self.new & ~self.old)  # A only, both, B only

    def tearDown(self) -> None:
        undo_manager.clear()

    def _result(self, how: str) -> np.ndarray:
        selection = AreaSelectionModule()
        selection.combine_region(self.old, REPLACE)
        selection.combine_region(self.new, how)
        result = selection.selected_mask(SHAPE)
        return np.zeros(SHAPE, dtype=bool) if result is None else result

    def test_the_picture_of_every_mode_is_what_the_combine_rule_does(self) -> None:
        for how in COMBINE_MODES:
            result = self._result(how)
            for area, state in zip(self.areas, MODE_AREAS[how]):
                self.assertTrue(area.any())
                if state == SELECTED:
                    self.assertTrue(result[area].all(), (how, "this area stays selected"))
                else:
                    self.assertFalse(result[area].any(), (how, "this area is not selected after the mode"))

    def test_taken_away_is_only_ever_drawn_for_pixels_that_were_selected(self) -> None:
        for how in COMBINE_MODES:
            for area, state, was_selected in zip(self.areas, MODE_AREAS[how], (True, True, False)):
                if state == REMOVED:
                    self.assertTrue(was_selected, (how, "orange means: was selected and is taken away"))
                    self.assertTrue(self.old[area].all())

    def test_every_mode_has_a_picture_and_no_two_modes_draw_the_same_thing(self) -> None:
        self.assertEqual(set(MODE_AREAS), set(COMBINE_MODES))
        self.assertEqual(len(set(MODE_AREAS.values())), len(COMBINE_MODES))
        for areas in MODE_AREAS.values():
            self.assertEqual(len(areas), 3)
            self.assertTrue(set(areas) <= {SELECTED, REMOVED, NOT_SELECTED})


if __name__ == "__main__":
    unittest.main()
