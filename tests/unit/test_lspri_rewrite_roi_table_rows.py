"""The ROI/Group table's decisions, without a window: units, rows, sorting,
sections, and which ROIs a move may reorder.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch**, like the
other `test_lspri_rewrite_*` files. The widget itself is in
`tests/integration/test_lspri_rewrite_roi_table_panel.py`.
"""

from __future__ import annotations

import math
import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.roi_table.rows import (
        COLUMN_ANGLE,
        COLUMN_HEIGHT,
        COLUMN_ID,
        COLUMN_NAME,
        COLUMN_RING_IN,
        COLUMN_RING_OUT,
        COLUMN_SAMPLE,
        COLUMN_SHAPE,
        COLUMN_WIDTH,
        COLUMN_X,
        COLUMN_Y,
        PIXELS,
        RECTANGLE_COLUMNS,
        SHAPE_DISK,
        SHAPE_FIELDS,
        SHAPE_MASK,
        SHAPE_RECTANGLE,
        SHAPE_RING,
        VIEW_GEOMETRY,
        VIEW_MODE_COLUMNS,
        VIEW_SIMPLE,
        angle_edit_text,
        angle_step_text,
        build_roi_rows,
        build_sections,
        edit_text,
        format_angle,
        format_length,
        from_display,
        micrometers,
        movement_scope,
        parse_angle,
        parse_length,
        shape_of,
        sort_rows,
        step_target_index,
        to_display,
    )
    from lspr_imaging_app.domain.roi_model import AreaRoi, AreaRoiDetectionSettings, AreaRoiGroup, RoiMask
    from lspr_imaging_app.domain.roi_shapes import Border, Circle, MaskShape, NO_BORDER, Rectangle
    from lspr_imaging_app.roi.palette import DEFAULT_ROI_COLOR_HEX
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402


def _roi(roi_id: int, x: float = 0.0, **kwargs) -> AreaRoi:
    shape = kwargs.pop("shape", None) or Circle(kwargs.pop("sample_diameter_px", 6.0))
    return AreaRoi(area_roi_id=roi_id, center_x=x, center_y=5.0, shape=shape, **kwargs)


class LengthUnitTest(unittest.TestCase):
    def test_pixels_pass_through(self) -> None:
        self.assertEqual(format_length(12.34, PIXELS), "12.3")
        self.assertEqual(to_display(12.0, PIXELS), 12.0)
        self.assertEqual(parse_length("12.5", PIXELS), 12.5)

    def test_micrometers_convert_both_ways(self) -> None:
        unit = micrometers(0.5)  # 0.5 µm per px
        self.assertEqual(unit.label, "µm")
        self.assertEqual(to_display(20.0, unit), 10.0)
        self.assertEqual(format_length(20.0, unit), "10")
        self.assertEqual(parse_length("10", unit), 20.0)  # typed in µm, stored in px
        self.assertEqual(from_display(10.0, unit), 20.0)

    def test_edit_text_has_the_displayed_precision(self) -> None:
        unit = micrometers(0.37)
        self.assertEqual(edit_text(17.123, unit), "6")  # 6.33 um, no decimals
        self.assertEqual(edit_text(17.123, PIXELS), "17.1")

    def test_edit_text_drops_trailing_zeros_and_negative_zero(self) -> None:
        self.assertEqual(edit_text(12.0, PIXELS), "12")
        self.assertEqual(edit_text(12.5, PIXELS), "12.5")
        self.assertEqual(edit_text(-0.0001, PIXELS), "0")

    def test_a_decimal_comma_is_accepted(self) -> None:
        self.assertEqual(parse_length(" 12,5 ", PIXELS), 12.5)

    def test_unreadable_input_raises_a_message_fit_to_show(self) -> None:
        for bad in ("abc", "", "1.2.3", "inf", "nan"):
            with self.assertRaises(ValueError):
                parse_length(bad, PIXELS)
        with self.assertRaisesRegex(ValueError, "'abc' is not a number"):
            parse_length("abc", PIXELS)

    def test_a_micrometer_scale_must_be_positive_and_finite(self) -> None:
        for bad in (0.0, -1.0, math.inf, math.nan):
            with self.assertRaises(ValueError):
                micrometers(bad)


class AngleTest(unittest.TestCase):
    """A rectangle's angle: degrees clockwise, typed, shown and stepped like a length but never converted to µm."""

    def test_parse_accepts_a_comma_and_any_finite_number(self) -> None:
        self.assertEqual(parse_angle(" 12,5 "), 12.5)
        self.assertEqual(parse_angle("-30"), -30.0)
        self.assertEqual(parse_angle("370"), 370.0, "stored as typed: 370 is a turn of 10, but it is the user's number")

    def test_unreadable_input_raises_a_message_fit_to_show(self) -> None:
        for bad in ("abc", "", "1.2.3", "inf", "nan"):
            with self.assertRaises(ValueError):
                parse_angle(bad)
        with self.assertRaisesRegex(ValueError, "'abc' is not a number"):
            parse_angle("abc")

    def test_shown_with_one_decimal_and_edited_without_trailing_zeros(self) -> None:
        self.assertEqual(format_angle(33.04), "33.0")
        self.assertEqual(angle_edit_text(33.04), "33")
        self.assertEqual(angle_edit_text(33.5), "33.5")
        self.assertEqual(angle_edit_text(-0.0001), "0")
        self.assertEqual(angle_edit_text(-12.5), "-12.5")

    def test_a_step_is_one_degree_and_shift_five(self) -> None:
        self.assertEqual(angle_step_text("30", 1, False), "31")
        self.assertEqual(angle_step_text("30", -1, False), "29")
        self.assertEqual(angle_step_text("30", 1, True), "35")
        self.assertEqual(angle_step_text("0", -1, False), "-1", "an angle has no lower limit")
        self.assertEqual(angle_step_text("0,5", 1, False), "1.5")
        self.assertIsNone(angle_step_text("abc", 1, False))


class RowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.defaults = AreaRoiDetectionSettings()  # ring 28 / 36

    def test_a_ring_with_no_override_is_inherited_and_shows_the_default(self) -> None:
        (row,) = build_roi_rows([_roi(1)], [], self.defaults)
        self.assertEqual((row.inner, row.outer), (14.0, 22.0))  # the default border (4, 4) around a 6 px sample
        self.assertTrue(row.inner_inherited and row.outer_inherited)

    def test_a_roi_with_no_colour_takes_the_given_default_and_an_own_colour_wins(self) -> None:
        plain, own = _roi(1), _roi(2, sample_color_hex="#112233")
        a, b = build_roi_rows([plain, own], [], self.defaults, "#c957e8")
        self.assertEqual((a.color_hex, a.has_own_color), ("#c957e8", False))
        self.assertEqual((b.color_hex, b.has_own_color), ("#112233", True))

    def test_an_override_is_not_inherited(self) -> None:
        roi = _roi(1, border=Border.from_ring_diameters(6.0, 10.0, 36.0))
        (row,) = build_roi_rows([roi], [], self.defaults)
        self.assertEqual((row.inner, row.outer), (10.0, 36.0))
        self.assertFalse(row.inner_inherited)
        self.assertFalse(row.outer_inherited)
        (follower,) = build_roi_rows([_roi(2)], [], self.defaults)  # no border of its own: the default (4, 4) around the 6 px sample
        self.assertEqual((follower.inner, follower.outer), (14.0, 22.0))
        self.assertTrue(follower.inner_inherited and follower.outer_inherited)

    def test_colour_label_group_mask_and_nudges(self) -> None:
        plain = _roi(1)
        styled = _roi(2, sample_color_hex="#123456", label="spot A", shape=MaskShape(RoiMask(0, 0, np.ones((2, 2), dtype=bool))), per_wavelength={(0, 500.0): (1.0, 2.0)})
        group = AreaRoiGroup(group_id="g1", name="G", area_roi_ids=[2])
        a, b = build_roi_rows([plain, styled], [group], self.defaults)
        self.assertEqual((a.color_hex, a.has_own_color, a.label, a.group_id, a.is_mask, a.nudge_count), (DEFAULT_ROI_COLOR_HEX, False, "", None, False, 0))
        self.assertEqual((b.color_hex, b.has_own_color, b.label, b.group_id, b.is_mask, b.nudge_count), ("#123456", True, "spot A", "g1", True, 1))


class SortAndSectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.defaults = AreaRoiDetectionSettings()
        self.rois = [
            _roi(1, x=30.0, label="beta"),
            _roi(2, x=10.0),
            _roi(3, x=20.0, label="Alpha"),
            _roi(4, x=20.0, sample_diameter_px=9.0),
        ]
        self.rows = build_roi_rows(self.rois, [], self.defaults)

    def ids(self, rows) -> list[int]:
        return [row.roi_id for row in rows]

    def test_sort_by_number_both_ways(self) -> None:
        self.assertEqual(self.ids(sort_rows(self.rows, COLUMN_ID, False)), [1, 2, 3, 4])
        self.assertEqual(self.ids(sort_rows(self.rows, COLUMN_ID, True)), [4, 3, 2, 1])

    def test_sort_by_a_figure_breaks_ties_by_number(self) -> None:
        self.assertEqual(self.ids(sort_rows(self.rows, COLUMN_X, False)), [2, 3, 4, 1])
        self.assertEqual(self.ids(sort_rows(self.rows, COLUMN_X, True)), [1, 4, 3, 2])
        self.assertEqual(self.ids(sort_rows(self.rows, COLUMN_SAMPLE, True)), [4, 3, 2, 1])

    def test_sort_by_name_ignores_case_and_puts_unnamed_last(self) -> None:
        self.assertEqual(self.ids(sort_rows(self.rows, COLUMN_NAME, False)), [3, 1, 2, 4])

    def test_sections_keep_the_users_group_order_and_ungrouped_comes_last(self) -> None:
        groups = [AreaRoiGroup("g2", "Zed", area_roi_ids=[3]), AreaRoiGroup("g1", "Alpha", area_roi_ids=[1, 2])]
        rows = build_roi_rows(self.rois, groups, self.defaults)
        sections = build_sections(rows, groups, {"g1": "#111111", "g2": "#222222"}, sort_column=COLUMN_ID, descending=False)
        self.assertEqual([s.group.name for s in sections], ["Zed", "Alpha", "Ungrouped"], "never sorted by a column")
        self.assertEqual([self.ids(s.rows) for s in sections], [[3], [1, 2], [4]])
        self.assertIsNone(sections[-1].group.group_id)
        self.assertEqual(sections[0].group.color_hex, "#222222")

    def test_rows_are_sorted_inside_each_section(self) -> None:
        groups = [AreaRoiGroup("g1", "A", area_roi_ids=[1, 2, 3, 4])]
        rows = build_roi_rows(self.rois, groups, self.defaults)
        sections = build_sections(rows, groups, {"g1": "#111111"}, sort_column=COLUMN_X, descending=True)
        self.assertEqual(self.ids(sections[0].rows), [1, 4, 3, 2])

    def test_there_is_no_ungrouped_section_when_everyone_is_grouped(self) -> None:
        groups = [AreaRoiGroup("g1", "A", area_roi_ids=[1, 2, 3, 4])]
        rows = build_roi_rows(self.rois, groups, self.defaults)
        sections = build_sections(rows, groups, {"g1": "#111111"}, sort_column=COLUMN_ID, descending=False)
        self.assertEqual(len(sections), 1)

    def test_an_empty_group_still_has_a_section(self) -> None:
        groups = [AreaRoiGroup("g1", "Empty", area_roi_ids=[])]
        rows = build_roi_rows(self.rois, groups, self.defaults)
        sections = build_sections(rows, groups, {"g1": "#111111"}, sort_column=COLUMN_ID, descending=False)
        self.assertEqual([(s.group.name, len(s.rows)) for s in sections], [("Empty", 0), ("Ungrouped", 4)])


class MovementTest(unittest.TestCase):
    def setUp(self) -> None:
        defaults = AreaRoiDetectionSettings()
        groups = [AreaRoiGroup("g1", "A", area_roi_ids=[1, 3, 5]), AreaRoiGroup("g2", "B", area_roi_ids=[2])]
        self.rows = build_roi_rows([_roi(i, x=float(i)) for i in range(1, 7)], groups, defaults)

    def test_flat_view_reorders_within_everyone(self) -> None:
        self.assertEqual(movement_scope({2, 5}, self.rows, grouped=False), (1, 2, 3, 4, 5, 6))

    def test_grouped_view_reorders_within_the_one_group(self) -> None:
        self.assertEqual(movement_scope({3, 5}, self.rows, grouped=True), (1, 3, 5))
        self.assertEqual(movement_scope({4}, self.rows, grouped=True), (4, 6), "the Ungrouped list")

    def test_rois_from_different_groups_have_no_common_list(self) -> None:
        self.assertIsNone(movement_scope({1, 2}, self.rows, grouped=True))
        self.assertIsNone(movement_scope({1, 4}, self.rows, grouped=True), "a group and Ungrouped")

    def test_nothing_selected_has_no_scope(self) -> None:
        self.assertIsNone(movement_scope(set(), self.rows, grouped=False))

    def test_stepping_one_place(self) -> None:
        scope = (1, 2, 3, 4, 5)
        self.assertEqual(step_target_index(scope, {3}, -1), 1)
        self.assertEqual(step_target_index(scope, {3}, 1), 3)
        self.assertEqual(step_target_index(scope, {3, 4}, -1), 1, "a block moves up together")
        self.assertEqual(step_target_index(scope, {3, 4}, 1), 3)
        self.assertEqual(step_target_index(scope, {1}, -1), -1, "the toolbox clamps this to the start")


if __name__ == "__main__":
    unittest.main()


class ShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.defaults = AreaRoiDetectionSettings()  # ring 28 / 36

    def row(self, **kwargs):
        (row,) = build_roi_rows([_roi(1, 12.5, **kwargs)], [], self.defaults)
        return row

    def test_the_two_stored_geometry_types_make_one_shape(self) -> None:
        mask = MaskShape(RoiMask(0, 0, np.ones((2, 2), dtype=bool)))
        self.assertEqual(shape_of(Circle(6.0), None), SHAPE_RING)  # following the default border
        self.assertEqual(shape_of(Circle(6.0), Border(1.0, 2.0)), SHAPE_RING)
        self.assertEqual(shape_of(Circle(6.0), NO_BORDER), SHAPE_DISK)
        self.assertEqual(shape_of(mask, Border(1.0, 2.0)), SHAPE_MASK)
        self.assertEqual(shape_of(mask, NO_BORDER), SHAPE_MASK)
        self.assertEqual(shape_of(Rectangle(8.0, 4.0), None), SHAPE_RECTANGLE)  # since phase 3 a rectangle has its own label and pictogram

    def test_a_row_carries_its_shape(self) -> None:
        self.assertEqual(self.row().shape, SHAPE_RING)
        self.assertEqual(self.row(border=NO_BORDER).shape, SHAPE_DISK)

    def test_each_view_mode_shows_the_id_column_and_the_modes_differ(self) -> None:
        self.assertEqual(VIEW_MODE_COLUMNS[VIEW_SIMPLE], (COLUMN_ID, COLUMN_NAME))
        self.assertEqual(
            VIEW_MODE_COLUMNS[VIEW_GEOMETRY],
            (COLUMN_ID, COLUMN_X, COLUMN_Y, COLUMN_SHAPE, COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT, COLUMN_WIDTH, COLUMN_HEIGHT, COLUMN_ANGLE),
        )

    def test_a_rectangle_has_a_width_a_height_and_an_angle_and_nothing_else_has(self) -> None:
        self.assertEqual(SHAPE_FIELDS[SHAPE_RECTANGLE], RECTANGLE_COLUMNS)
        for shape in (SHAPE_RING, SHAPE_DISK, SHAPE_MASK):
            self.assertFalse(set(SHAPE_FIELDS[shape]) & set(RECTANGLE_COLUMNS), shape)
        self.assertFalse(set(SHAPE_FIELDS[SHAPE_RECTANGLE]) & {COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT})

    def test_a_rectangle_row_carries_its_size_and_angle(self) -> None:
        rectangle = self.row(shape=Rectangle(30.0, 14.5, 33.0))
        self.assertEqual((rectangle.width, rectangle.height, rectangle.angle), (30.0, 14.5, 33.0))
        circle = self.row()
        self.assertEqual((circle.width, circle.height, circle.angle), (0.0, 0.0, 0.0))

    def test_the_new_columns_sort(self) -> None:
        rows = build_roi_rows(
            [_roi(1, shape=Rectangle(30.0, 10.0, 5.0)), _roi(2, shape=Rectangle(20.0, 40.0, -10.0)), _roi(3)], [], self.defaults
        )
        self.assertEqual([r.roi_id for r in sort_rows(rows, COLUMN_WIDTH, False)], [3, 2, 1], "a circle has no width: first")
        self.assertEqual([r.roi_id for r in sort_rows(rows, COLUMN_HEIGHT, True)], [2, 1, 3])
        self.assertEqual([r.roi_id for r in sort_rows(rows, COLUMN_ANGLE, False)], [2, 3, 1])

    def test_the_shape_column_sorts(self) -> None:
        rows = build_roi_rows([_roi(1, 30.0), _roi(2, 10.0, border=NO_BORDER), _roi(3, 20.0)], [], self.defaults)
        self.assertEqual([r.roi_id for r in sort_rows(rows, COLUMN_SHAPE, False)], [2, 1, 3], "disk before ring")
