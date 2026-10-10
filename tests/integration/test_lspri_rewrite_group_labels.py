"""Group labels: which ROI carries the label, where it sits, the box that keeps it short, and the menu."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from PyQt6.QtCore import QPointF
    from PyQt6.QtWidgets import QApplication

    from lspr_imaging_app.panels.image.group_label_menu import GroupLabelMenu
    from lspr_imaging_app.panels.image.group_label_overlay import GroupLabelItem, group_label_entries, label_rect, wrap_text
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.undo import undo_manager
except ImportError as error:  # pragma: no cover
    raise unittest.SkipTest(f"LSPRi rewrite not importable: {error}") from error

_APP = QApplication.instance() or QApplication([])


class LabelEntriesTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        for x in (10.0, 50.0, 90.0, 130.0):  # one row, pitch 40
            self.toolbox.add_roi(x, 20.0, sample_diameter_px=10.0)
        self.toolbox.group_rois((1, 2, 3, 4), "Row A")
        self.rois = self.toolbox.rois()
        self.centers = [(r.center_x, r.center_y) for r in self.rois]

    def tearDown(self) -> None:
        undo_manager.clear()

    def entries(self, position: str):
        return group_label_entries(self.toolbox.groups(), self.rois, self.centers, position=position, default_color="#ffffff")

    def test_first_or_last_roi_carries_the_name_and_spacing_is_the_pitch(self) -> None:
        first, last = self.entries("first")[0], self.entries("last")[0]
        self.assertEqual((first.text, first.x), ("Row A", 10.0))
        self.assertEqual(last.x, 130.0)
        self.assertAlmostEqual(first.spacing, 40.0)
        self.assertAlmostEqual(first.radius, 5.0)

    def test_colour_is_the_middle_members_own(self) -> None:
        middle = self.rois[2].sample_color_hex  # 4 members: index 2 is "the middle"
        self.assertEqual(self.entries("first")[0].color, middle)

    def test_a_single_roi_group_uses_twice_its_diameter_and_ungrouped_rois_have_no_label(self) -> None:
        self.toolbox.add_roi(200.0, 20.0, sample_diameter_px=10.0)
        self.toolbox.group_rois((5,), "Solo")
        rois = self.toolbox.rois()
        entries = group_label_entries(
            self.toolbox.groups(), rois, [(r.center_x, r.center_y) for r in rois], position="first", default_color="#fff"
        )
        solo = next(e for e in entries if e.text == "Solo")
        self.assertAlmostEqual(solo.spacing, 20.0)
        self.assertEqual(len(entries), 2)


class WrapTextTest(unittest.TestCase):
    width = staticmethod(lambda text: 10.0 * len(text))  # 10 px per character

    def test_breaks_at_spaces_and_keeps_short_text_whole(self) -> None:
        self.assertEqual(wrap_text("Row 1", self.width, 100.0), ["Row 1"])
        self.assertEqual(wrap_text("Left array row", self.width, 80.0), ["Left", "array", "row"])
        self.assertEqual(wrap_text("aa bb cc dd", self.width, 60.0), ["aa bb", "cc dd"])

    def test_breaks_inside_a_word_wider_than_the_box(self) -> None:
        self.assertEqual(wrap_text("abcdefghij", self.width, 40.0), ["abcd", "efgh", "ij"])

    def test_past_the_line_limit_the_end_is_cut_with_an_ellipsis(self) -> None:
        lines = wrap_text("a b c d e f", self.width, 10.0, max_lines=3)
        self.assertEqual(lines, ["a", "b", "c…"])


class FitTest(unittest.TestCase):
    def test_a_long_name_shrinks_to_the_minimum_size_before_it_is_broken(self) -> None:
        text = "A rather long group name"
        font, lines = GroupLabelItem._fit(text, 10_000.0, 6.0)
        self.assertEqual((font.pointSizeF(), lines), (9.0, [text]))  # plenty of room: normal size, one line
        font, lines = GroupLabelItem._fit(text, 120.0, 6.0)
        self.assertLess(font.pointSizeF(), 9.0)
        self.assertGreaterEqual(font.pointSizeF(), 6.0)
        font, lines = GroupLabelItem._fit(text, 40.0, 6.0)
        self.assertEqual(font.pointSizeF(), 6.0)  # shrunk as far as allowed, then broken
        self.assertGreater(len(lines), 1)
        font, _ = GroupLabelItem._fit(text, 40.0, 8.0)
        self.assertEqual(font.pointSizeF(), 8.0)  # a larger minimum stops the shrinking sooner


class LabelRectTest(unittest.TestCase):
    def test_each_side_puts_the_label_just_outside_the_circle(self) -> None:
        anchor = QPointF(100.0, 100.0)
        top = label_rect(anchor, 10.0, "top", 40.0, 12.0, gap=3.0)
        self.assertEqual((top.center().x(), top.bottom()), (100.0, 87.0))
        bottom = label_rect(anchor, 10.0, "bottom", 40.0, 12.0, gap=3.0)
        self.assertEqual((bottom.center().x(), bottom.top()), (100.0, 113.0))
        left = label_rect(anchor, 10.0, "left", 40.0, 12.0, gap=3.0)
        self.assertEqual((left.right(), left.center().y()), (87.0, 100.0))
        right = label_rect(anchor, 10.0, "right", 40.0, 12.0, gap=3.0)
        self.assertEqual((right.left(), right.center().y()), (113.0, 100.0))
        with self.assertRaises(ValueError):
            label_rect(anchor, 10.0, "diagonal", 1.0, 1.0)


class LabelMenuTest(unittest.TestCase):
    def test_settings_follow_the_controls_and_changes_are_reported(self) -> None:
        menu = GroupLabelMenu()
        seen: list[int] = []
        menu.changed.connect(lambda: seen.append(1))
        self.assertFalse(menu.settings().visible)
        menu.show_button.click()
        menu.direction.setCurrentIndex(menu.direction.findData("vertical"))
        menu.position.setCurrentIndex(menu.position.findData("last"))
        menu.side.setCurrentIndex(menu.side.findData("left"))
        menu.box.setValue(60)
        menu.min_size.setValue(8)
        settings = menu.settings()
        self.assertEqual(
            (settings.visible, settings.vertical, settings.position, settings.side, settings.box_percent),
            (True, True, "last", "left", 60.0),
        )
        self.assertEqual(menu.settings().min_point_size, 8.0)
        self.assertEqual(len(seen), 6)

    def test_every_control_is_remembered(self) -> None:
        saved: dict[str, object] = {}
        store = UiStateStore({}, on_changed=lambda values: saved.update(values))
        first = GroupLabelMenu()
        first.bind_ui_state(store)
        first.show_button.click()
        first.side.setCurrentIndex(first.side.findData("bottom"))
        first.box.setValue(120)
        store.flush()
        second = GroupLabelMenu()
        second.bind_ui_state(UiStateStore(dict(saved)))
        settings = second.settings()
        self.assertEqual((settings.visible, settings.side, settings.box_percent), (True, "bottom", 120.0))


if __name__ == "__main__":
    unittest.main()
