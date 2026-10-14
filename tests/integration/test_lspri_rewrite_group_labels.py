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
    from PyQt6.QtWidgets import QApplication, QComboBox, QFrame, QLabel

    from lspr_imaging_app.gui.app_theme import LSPRI_BRIGHT_THEME
    from lspr_imaging_app.panels.image.group_label_menu import GroupLabelMenu
    from lspr_imaging_app.panels.image.group_label_overlay import (
        GroupLabelItem,
        group_label_entries,
        label_rect,
        rotated_extent,
        wrap_text,
    )
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


class RotatedExtentTest(unittest.TestCase):
    def test_the_box_round_a_turned_block(self) -> None:
        self.assertEqual(rotated_extent(40.0, 12.0, 0), (40.0, 12.0))
        width, height = rotated_extent(40.0, 12.0, 90)
        self.assertAlmostEqual(width, 12.0)  # turned on its end: the two swap
        self.assertAlmostEqual(height, 40.0)
        side = (40.0 + 12.0) / 2.0**0.5
        for angle in (45, 135, 225, 315):
            width, height = rotated_extent(40.0, 12.0, angle)
            self.assertAlmostEqual(width, side)
            self.assertAlmostEqual(height, side)
        width, height = rotated_extent(40.0, 12.0, 180)  # upside down: the same box as upright
        self.assertAlmostEqual(width, 40.0)
        self.assertAlmostEqual(height, 12.0)

    def test_every_angle_paints_without_error(self) -> None:
        import pyqtgraph as pg
        from PyQt6.QtGui import QImage, QPainter

        from lspr_imaging_app.panels.image.group_label_overlay import GroupLabelEntry, GroupLabelSettings

        widget = pg.PlotWidget()
        widget.resize(300, 200)
        widget.getPlotItem().vb.setRange(xRange=(0, 300), yRange=(0, 200), padding=0.0)
        widget.show()
        _APP.processEvents()
        item = GroupLabelItem(widget.getPlotItem().vb)
        item.set_entries([GroupLabelEntry("Row A", 150.0, 100.0, 10.0, 60.0, "#ff8800")])
        try:
            for angle in range(0, 360, 45):
                item.set_settings(GroupLabelSettings(visible=True, angle=angle, side="right"))
                image = QImage(300, 200, QImage.Format.Format_ARGB32)
                image.fill(0)
                painter = QPainter(image)
                item.paint(painter)
                painter.end()
                lit = sum(1 for x in range(300) for y in range(200) if image.pixelColor(x, y).alpha() > 0)
                self.assertGreater(lit, 20, f"{angle} degrees: the text is drawn")
        finally:
            widget.close()


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

    def test_the_middle_cell_centres_the_label_and_a_corner_cell_clears_the_circle(self) -> None:
        anchor = QPointF(100.0, 100.0)
        centre = label_rect(anchor, 10.0, "center", 40.0, 12.0, gap=3.0)
        self.assertEqual((centre.center().x(), centre.center().y()), (100.0, 100.0))
        corner = label_rect(anchor, 10.0, "top-left", 40.0, 12.0, gap=3.0)
        # The label's nearest corner (bottom right) sits on the 45-degree point, `gap` beyond the circle.
        reach = (10.0 + 3.0) / 2.0**0.5
        self.assertAlmostEqual(corner.right(), 100.0 - reach)
        self.assertAlmostEqual(corner.bottom(), 100.0 - reach)
        self.assertGreaterEqual(((corner.right() - 100.0) ** 2 + (corner.bottom() - 100.0) ** 2) ** 0.5, 13.0 - 1e-9)


class LabelMenuTest(unittest.TestCase):
    def test_settings_follow_the_controls_and_changes_are_reported(self) -> None:
        menu = GroupLabelMenu()
        seen: list[int] = []
        menu.changed.connect(lambda: seen.append(1))
        self.assertFalse(menu.settings().visible)
        menu.show_button.click()
        menu.angle.rotate_button().click()
        menu.angle.rotate_button().click()  # two presses of 45 degrees: the text now reads upwards
        menu.position.button("last").click()
        menu.placement.set_placement("left")
        menu.box.setValue(60)
        menu.min_size.setValue(8)
        settings = menu.settings()
        self.assertEqual(
            (settings.visible, settings.angle, settings.position, settings.side, settings.box_percent),
            (True, 90, "last", "left", 60.0),
        )
        self.assertEqual(menu.settings().min_point_size, 8.0)
        self.assertEqual(len(seen), 7)

    def test_every_control_is_remembered(self) -> None:
        saved: dict[str, object] = {}
        store = UiStateStore({}, on_changed=lambda values: saved.update(values))
        first = GroupLabelMenu()
        first.bind_ui_state(store)
        first.show_button.click()
        first.placement.button("bottom-right").click()
        first.box.setValue(120)
        store.flush()
        second = GroupLabelMenu()
        second.bind_ui_state(UiStateStore(dict(saved)))
        settings = second.settings()
        self.assertEqual((settings.visible, settings.side, settings.box_percent), (True, "bottom-right", 120.0))

    def test_the_menu_is_one_captionless_strip_of_pickers_in_the_requested_order(self) -> None:
        menu = GroupLabelMenu()
        panel = menu.menu().actions()[0].defaultWidget()
        self.assertEqual(panel.findChildren(QComboBox), [], "no drop-downs")
        self.assertEqual([label for label in panel.findChildren(QLabel) if label.text()], [], "no captions")  # the angle's picture has no text
        layout = panel.layout()
        parts = [layout.itemAt(i).widget() for i in range(layout.count())]
        parts = [part for part in parts if not isinstance(part, QFrame)]  # drop the thin separator lines
        self.assertEqual(
            parts, [menu.show_button, menu.position, menu.placement, menu.angle, menu.box, menu.min_size]
        )
        self.assertEqual((menu.box.suffix(), menu.min_size.suffix()), (" %", " pt"))
        for part in (menu.show_button, menu.placement, menu.box, menu.min_size):  # the pickers' tips are on their buttons
            self.assertTrue(part.toolTip(), "no caption, so every part explains itself in its tooltip")
        for choice in ("first", "last"):
            self.assertTrue(menu.position.button(choice).toolTip())
        self.assertTrue(menu.angle.rotate_button().toolTip())
        self.assertTrue(menu.angle.picture().toolTip())

    def test_the_pictures_are_painted_and_follow_the_pick(self) -> None:
        menu = GroupLabelMenu()

        def image(button):
            return button.icon().pixmap(60, 60).toImage()

        first, last = menu.position.button("first"), menu.position.button("last")
        self.assertFalse(image(first).isNull() or image(last).isNull())
        self.assertNotEqual(image(first), image(last), "1st and nth look different")
        before = image(first)
        last.click()
        self.assertNotEqual(image(first), before, "the picked one is lit, the other dims")
        self.assertFalse(menu.angle.rotate_button().icon().isNull(), "the rotate arrow is drawn")
        pictures = []
        for _ in range(4):  # 0, 45, 90, 135 degrees: the picture of the word turns with every press
            pictures.append(menu.angle.picture().pixmap().toImage())
            menu.angle.rotate_button().click()
        for earlier in range(len(pictures)):
            for later in range(earlier + 1, len(pictures)):
                self.assertNotEqual(pictures[earlier], pictures[later], f"{earlier * 45} and {later * 45} degrees look different")

    def test_each_picker_has_one_pick_and_reports_it_once(self) -> None:
        menu = GroupLabelMenu()
        seen: list[int] = []
        menu.changed.connect(lambda: seen.append(1))
        menu.angle.rotate_button().click()
        self.assertEqual((menu.angle.angle(), len(seen)), (45, 1))
        menu.position.button("last").click()
        self.assertEqual((menu.position.value(), len(seen)), ("last", 2))
        menu.position.button("last").click()  # already picked: nothing happens
        self.assertEqual(len(seen), 2)
        with self.assertRaises(ValueError):
            menu.position.set_value("middle")
        with self.assertRaises(ValueError):
            menu.angle.set_angle(50)  # not a multiple of 45

    def test_the_rotate_arrow_turns_the_text_45_degrees_a_press_and_comes_round_after_eight(self) -> None:
        menu = GroupLabelMenu()
        reported: list[int] = []
        menu.angle.angle_changed.connect(reported.append)
        for _ in range(8):
            menu.angle.rotate_button().click()
        self.assertEqual(reported, [45, 90, 135, 180, 225, 270, 315, 0])
        self.assertEqual(menu.settings().angle, 0)
        menu.angle.set_angle(360)  # a full turn is the same as none: nothing to report
        self.assertEqual(len(reported), 8)
        menu.angle.set_angle(-45)
        self.assertEqual(menu.angle.angle(), 315)

    def test_choices_saved_while_they_were_drop_downs_are_still_read(self) -> None:
        """The text direction was a word (horizontal / vertical / diagonal) and is now an angle; a file with the
        old word still gives the same look, the new key wins when both are there, and rubbish is ignored."""
        for word, angle in (("horizontal", 0), ("diagonal", 45), ("vertical", 90)):
            menu = GroupLabelMenu()
            menu.bind_ui_state(UiStateStore({"image/group_labels/direction": word, "image/group_labels/position": "last"}))
            self.assertEqual((menu.settings().angle, menu.settings().position), (angle, "last"), word)
        both = GroupLabelMenu()
        both.bind_ui_state(UiStateStore({"image/group_labels/direction": "vertical", "image/group_labels/angle": 135}))
        self.assertEqual(both.settings().angle, 135)
        for rubbish in ("sideways", 50, True, None, 2.5):
            other = GroupLabelMenu()
            other.bind_ui_state(UiStateStore({"image/group_labels/direction": rubbish, "image/group_labels/angle": rubbish,
                                              "image/group_labels/position": 3}))
            self.assertEqual((other.settings().angle, other.settings().position), (0, "first"), rubbish)

    def test_the_angle_is_saved_and_comes_back(self) -> None:
        saved: dict[str, object] = {}
        store = UiStateStore({}, on_changed=lambda values: saved.update(values))
        first = GroupLabelMenu()
        first.bind_ui_state(store)
        for _ in range(3):
            first.angle.rotate_button().click()
        store.flush()
        self.assertEqual(saved["image/group_labels/angle"], 135)
        second = GroupLabelMenu()
        second.bind_ui_state(UiStateStore(dict(saved)))
        self.assertEqual(second.settings().angle, 135)

    def test_a_copy_follows_the_pickers_silently_and_a_theme_switch_restyles_them(self) -> None:
        original, copy = GroupLabelMenu(), GroupLabelMenu()
        seen: list[int] = []
        copy.changed.connect(lambda: seen.append(1))
        original.angle.rotate_button().click()
        original.angle.rotate_button().click()
        original.position.button("last").click()
        copy.sync_from(original)
        self.assertEqual((copy.angle.angle(), copy.position.value(), seen), (90, "last", []))
        before = copy.position.button("last").styleSheet()
        copy.position.refresh_theme(LSPRI_BRIGHT_THEME)
        self.assertNotEqual(copy.position.button("last").styleSheet(), before)
        copy.angle.refresh_theme(LSPRI_BRIGHT_THEME)  # restyles without error
        self.assertFalse(copy.angle.rotate_button().icon().isNull())

    def test_a_side_saved_before_the_grid_existed_is_still_read(self) -> None:
        """The key was a four-way drop-down (top / left / bottom / right) and kept its name."""
        menu = GroupLabelMenu()
        menu.bind_ui_state(UiStateStore({"image/group_labels/side": "left"}))
        self.assertEqual(menu.settings().side, "left")
        other = GroupLabelMenu()
        other.bind_ui_state(UiStateStore({"image/group_labels/side": "diagonal"}))  # rubbish: keeps the default
        self.assertEqual(other.settings().side, "top")


if __name__ == "__main__":
    unittest.main()
