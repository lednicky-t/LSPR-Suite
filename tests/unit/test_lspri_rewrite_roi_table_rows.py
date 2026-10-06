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

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.roi_table.rows import (
        COLUMN_ID,
        COLUMN_NAME,
        COLUMN_SAMPLE,
        COLUMN_X,
        PIXELS,
        build_roi_rows,
        build_sections,
        edit_text,
        format_length,
        from_display,
        micrometers,
        movement_scope,
        parse_length,
        sort_rows,
        step_target_index,
        to_display,
    )
    from lspr_imaging_app.roi.model import AreaRoi, AreaRoiDetectionSettings, AreaRoiGroup
    from lspr_imaging_app.roi.palette import DEFAULT_ROI_COLOR_HEX
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


def _roi(roi_id: int, x: float = 0.0, **kwargs) -> AreaRoi:
    return AreaRoi(area_roi_id=roi_id, center_x=x, center_y=5.0, sample_diameter_px=kwargs.pop("sample_diameter_px", 6.0), **kwargs)


class LengthUnitTest(unittest.TestCase):
    def test_pixels_pass_through(self) -> None:
        self.assertEqual(format_length(12.34, PIXELS), "12.3")
        self.assertEqual(to_display(12.0, PIXELS), 12.0)
        self.assertEqual(parse_length("12.5", PIXELS), 12.5)

    def test_micrometers_convert_both_ways(self) -> None:
        unit = micrometers(0.5)  # 0.5 µm per px
        self.assertEqual(unit.label, "µm")
        self.assertEqual(to_display(20.0, unit), 10.0)
        self.assertEqual(format_length(20.0, unit), "10.0")
        self.assertEqual(parse_length("10", unit), 20.0)  # typed in µm, stored in px
        self.assertEqual(from_display(10.0, unit), 20.0)

    def test_an_edit_round_trips_without_losing_precision(self) -> None:
        unit = micrometers(0.37)
        px = 17.123
        self.assertAlmostEqual(parse_length(edit_text(px, unit), unit), px, delta=0.0015 / 0.37)

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


class RowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.defaults = AreaRoiDetectionSettings()  # ring 28 / 36

    def test_a_ring_with_no_override_is_inherited_and_shows_the_default(self) -> None:
        (row,) = build_roi_rows([_roi(1)], [], self.defaults)
        self.assertEqual((row.inner, row.outer), (28.0, 36.0))
        self.assertTrue(row.inner_inherited and row.outer_inherited)

    def test_an_override_is_not_inherited(self) -> None:
        roi = _roi(1, reference_inner_diameter_px=10.0)
        (row,) = build_roi_rows([roi], [], self.defaults)
        self.assertEqual((row.inner, row.outer), (10.0, 36.0))
        self.assertFalse(row.inner_inherited)
        self.assertTrue(row.outer_inherited)

    def test_colour_label_group_mask_and_nudges(self) -> None:
        plain = _roi(1)
        styled = _roi(2, sample_color_hex="#123456", label="spot A", sample_geometry_type="mask", per_wavelength={(0, 500.0): (1.0, 2.0)})
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
