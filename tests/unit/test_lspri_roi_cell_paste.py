"""Copying and pasting cells of the ROI table, without a window (apps/LSPRi/eva/docs/roi_shapes_and_linking_plan_2026-11-18.md, "Copy and paste of cells").

Three parts: the pure paste rules (`panels/roi_table/cell_clipboard.py`: matching columns, one value for all rows, a pattern that repeats, a block that runs on down from one row),
the marks' edges (`cell_marks.py`), and the toolbox command that gives each ROI its own numbers in one undo step (`RoiToolbox.set_cell_values`).

Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch.
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
    from lspr_imaging_app.domain.roi_model import RoiMask
    from lspr_imaging_app.domain.roi_shapes import NO_BORDER, Circle, MaskShape, Rectangle, ring_diameters
    from lspr_imaging_app.panels.roi_table import cell_clipboard as cc
    from lspr_imaging_app.panels.roi_table.cell_marks import CellMarks, edges
    from lspr_imaging_app.panels.roi_table.rows import (
        COLUMN_ANGLE,
        COLUMN_HEIGHT,
        COLUMN_RIM_OFFSET,
        COLUMN_RIM_THICKNESS,
        COLUMN_SAMPLE,
        COLUMN_WIDTH,
        COLUMN_X,
        COLUMN_Y,
        PIXELS,
        micrometers,
    )
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

EVERYTHING = lambda roi_id, column: True  # noqa: E731


def block(columns, *rows) -> cc.CellBlock:
    return cc.CellBlock(tuple(columns), tuple(tuple(row) for row in rows))


class BlockFormatTest(unittest.TestCase):
    def test_a_payload_round_trips(self) -> None:
        original = block((COLUMN_SAMPLE, COLUMN_ANGLE), (6.0, None), (8.5, 30.0))
        again = cc.block_from_payload(cc.block_to_payload(original))
        self.assertEqual((again.columns, again.rows, again.token), (original.columns, original.rows, original.token))

    def test_junk_is_not_a_block(self) -> None:
        for raw in (b"", b"nonsense", b"{}", b'{"columns": [], "rows": []}', b'{"columns": [99], "rows": [[1]]}', b'{"columns": [4], "rows": [[1, 2]]}', b'{"columns": [4], "rows": "x"}'):
            self.assertIsNone(cc.block_from_payload(raw), raw)

    def test_the_text_for_other_programs_follows_the_unit_and_leaves_blanks(self) -> None:
        text = cc.block_to_text(block((COLUMN_SAMPLE, COLUMN_ANGLE), (6.0, None), (8.5, 30.0)), PIXELS)
        self.assertEqual(text, "6.0\t\n8.5\t30.0")
        in_um = cc.block_to_text(block((COLUMN_SAMPLE, COLUMN_ANGLE), (20.0, 33.0)), micrometers(0.5))
        self.assertEqual(in_um, "10\t33.0", "a length in µm, the angle in degrees whatever the unit")

    def test_each_copy_has_its_own_token(self) -> None:
        self.assertNotEqual(block((COLUMN_X,), (1.0,)).token, block((COLUMN_X,), (1.0,)).token)


class PlanPasteTest(unittest.TestCase):
    def plan(self, source, targets, order=None, target_columns=None, has_cell=EVERYTHING):
        order = list(order if order is not None else range(1, 11))
        return cc.plan_paste(source, targets, order, target_columns, has_cell)

    def test_one_source_row_fills_every_target_row(self) -> None:
        plan = self.plan(block((COLUMN_SAMPLE,), (9.0,)), [2, 3, 5])
        self.assertEqual(plan.values, {2: {"sample_diameter_px": 9.0}, 3: {"sample_diameter_px": 9.0}, 5: {"sample_diameter_px": 9.0}})
        self.assertEqual(plan.rows, (2, 3, 5))
        self.assertEqual(plan.columns, (COLUMN_SAMPLE,))
        self.assertFalse(plan.refusal)

    def test_several_source_rows_repeat_in_order_until_the_target_runs_out(self) -> None:
        source = block((COLUMN_SAMPLE,), (10.0,), (20.0,), (30.0,))
        plan = self.plan(source, [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual([plan.values[i]["sample_diameter_px"] for i in range(1, 8)], [10.0, 20.0, 30.0, 10.0, 20.0, 30.0, 10.0])
        short = self.plan(source, [4, 5])
        self.assertEqual([short.values[i]["sample_diameter_px"] for i in (4, 5)], [10.0, 20.0], "the pattern is cut where the target ends")

    def test_one_target_row_and_several_source_rows_run_on_down_like_excel(self) -> None:
        plan = self.plan(block((COLUMN_SAMPLE,), (10.0,), (20.0,), (30.0,)), [4])
        self.assertEqual(plan.rows, (4, 5, 6))
        self.assertEqual([plan.values[i]["sample_diameter_px"] for i in (4, 5, 6)], [10.0, 20.0, 30.0])

    def test_the_table_ending_cuts_the_block_and_says_so(self) -> None:
        plan = self.plan(block((COLUMN_SAMPLE,), (10.0,), (20.0,), (30.0,)), [9], order=range(1, 11))
        self.assertEqual(plan.rows, (9, 10))
        self.assertIn("table ended after 2 of 3", plan.note)

    def test_only_matching_columns_are_filled_and_types_are_never_mixed(self) -> None:
        source = block((COLUMN_SAMPLE, COLUMN_RIM_OFFSET, COLUMN_RIM_THICKNESS), (6.0, 10.0, 20.0))
        every_column = self.plan(source, [1])
        self.assertEqual(set(every_column.values[1]), {"sample_diameter_px", "rim_offset_px", "rim_thickness_px"}, "rows chosen, no cells: all columns match")
        only_offset = self.plan(source, [1, 2], target_columns={COLUMN_RIM_OFFSET})
        self.assertEqual(only_offset.values, {1: {"rim_offset_px": 10.0}, 2: {"rim_offset_px": 10.0}})
        self.assertEqual(only_offset.columns, (COLUMN_RIM_OFFSET,))

    def test_a_target_without_the_copied_column_is_refused_with_the_names(self) -> None:
        plan = self.plan(block((COLUMN_RIM_OFFSET,), (10.0,)), [1, 2], target_columns={COLUMN_SAMPLE})
        self.assertEqual(plan.values, {})
        self.assertIn("No matching column", plan.refusal)
        self.assertIn("copied d,", plan.refusal)
        self.assertIn("D_s", plan.refusal)
        self.assertEqual(self.plan(block((COLUMN_X, COLUMN_Y), (1.0, 2.0)), [1], target_columns={COLUMN_WIDTH}).values, {}, "a position does not go into a width")

    def test_a_row_with_no_such_cell_is_skipped_and_counted(self) -> None:
        circles = {1, 2}
        plan = self.plan(block((COLUMN_WIDTH,), (30.0,)), [1, 2, 3, 4], has_cell=lambda roi_id, column: roi_id not in circles)
        self.assertEqual(set(plan.values), {3, 4})
        self.assertIn("2 rows have no W", plan.note)
        self.assertEqual(plan.rows, (3, 4))

    def test_nothing_to_paste_says_why(self) -> None:
        plan = self.plan(block((COLUMN_WIDTH,), (30.0,)), [1, 2], has_cell=lambda roi_id, column: False)
        self.assertIn("Nothing to paste", plan.refusal)
        self.assertIn("2 rows have no W", plan.refusal)
        blank = self.plan(block((COLUMN_WIDTH,), (None,)), [1])
        self.assertIn("no value", blank.refusal)
        self.assertIn("select a ROI", self.plan(block((COLUMN_WIDTH,), (1.0,)), []).refusal)

    def test_a_blank_source_cell_is_skipped_not_zero(self) -> None:
        plan = self.plan(block((COLUMN_SAMPLE, COLUMN_ANGLE), (6.0, None), (8.0, 15.0)), [1, 2])
        self.assertEqual(plan.values, {1: {"sample_diameter_px": 6.0}, 2: {"sample_diameter_px": 8.0, "angle_deg": 15.0}})

    def test_a_block_of_several_columns_goes_column_by_column(self) -> None:
        source = block((COLUMN_WIDTH, COLUMN_HEIGHT, COLUMN_ANGLE), (30.0, 14.0, 33.0), (20.0, 10.0, 0.0))
        plan = self.plan(source, [5, 6])
        self.assertEqual(plan.values[5], {"width_px": 30.0, "height_px": 14.0, "angle_deg": 33.0})
        self.assertEqual(plan.values[6], {"width_px": 20.0, "height_px": 10.0, "angle_deg": 0.0})


class MarksTest(unittest.TestCase):
    def test_the_border_is_only_where_the_block_ends(self) -> None:
        rows, columns = {2, 3, 4}, {5, 6}
        self.assertEqual(edges(3, 5, rows, columns, above=2, below=4, left=4, right=6), (False, False, False, True), "left column of the block: its left side")
        self.assertEqual(edges(3, 6, rows, columns, above=2, below=4, left=5, right=7), (False, True, False, False))
        self.assertEqual(edges(2, 5, rows, columns, above=1, below=3, left=4, right=6), (True, False, False, True))
        self.assertEqual(edges(4, 6, rows, columns, above=3, below=5, left=5, right=7), (False, True, True, False))
        self.assertIsNone(edges(9, 5, rows, columns, above=None, below=None, left=None, right=None), "not in the block")
        self.assertIsNone(edges(3, 9, rows, columns, above=2, below=4, left=None, right=None))

    def test_a_gap_in_the_selected_rows_gives_each_part_its_own_border(self) -> None:
        rows, columns = {1, 2, 5}, {3}
        self.assertEqual(edges(2, 3, rows, columns, above=1, below=3, left=None, right=None), (False, True, True, True), "row 3 is not in the block")
        self.assertEqual(edges(5, 3, rows, columns, above=4, below=None, left=None, right=None), (True, True, True, True))

    def test_restricting_the_range_keeps_the_selected_rows_and_drops_an_empty_one(self) -> None:
        marks = CellMarks()
        marks.set_range([1, 2, 3], [4, 5])
        self.assertTrue(marks.restrict_range({2, 3, 9}))
        self.assertEqual(marks.range_rows, (2, 3))
        self.assertFalse(marks.restrict_range({2, 3}), "nothing changed")
        marks.restrict_range({7})
        self.assertFalse(marks.has_range())
        self.assertEqual(marks.range_columns, frozenset())

    def test_a_column_that_is_not_shown_leaves_the_range(self) -> None:
        marks = CellMarks()
        marks.set_range([1], [4, 5, 6])
        marks.restrict_range({1}, shown_columns=[4, 6])
        self.assertEqual(marks.range_columns, frozenset({4, 6}))

    def test_the_ants_advance_and_wrap(self) -> None:
        marks = CellMarks()
        marks.set_copied([1], [4], "t")
        phases = []
        for _ in range(8):
            phases.append(marks.ants_phase)
            marks.step_ants()
        self.assertEqual(phases, [0, 1, 2, 3, 4, 5, 0, 1])
        marks.clear_copied()
        self.assertFalse(marks.has_copied())


class SetCellValuesTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        for x in (10.0, 20.0, 30.0):
            self.toolbox.add_roi(x, 5.0, sample_diameter_px=6.0)  # ROIs 1-3
        self.rect = self.toolbox.add_shaped_roi(60.0, 5.0, Rectangle(30.0, 14.0, 33.0))  # ROI 4
        undo_manager.clear()  # (adding the ROIs above left undo entries: these tests look at what the paste adds)
        self.signals: list[object] = []
        self.toolbox.geometry_changed.connect(self.signals.append)

    def tearDown(self) -> None:
        undo_manager.clear()

    def diameters(self, *ids: int) -> list[float]:
        return [self.toolbox.roi_by_id(i).shape.diameter_px for i in ids]

    def test_each_roi_gets_its_own_value_in_one_undo_step_and_one_signal(self) -> None:
        self.toolbox.set_cell_values({1: {"sample_diameter_px": 8.0, "x": 11.0}, 2: {"sample_diameter_px": 9.0, "x": 22.0}, 3: {"sample_diameter_px": 10.0}})
        self.assertEqual(self.diameters(1, 2, 3), [8.0, 9.0, 10.0])
        self.assertEqual([self.toolbox.roi_by_id(i).center_x for i in (1, 2, 3)], [11.0, 22.0, 30.0])
        self.assertEqual(len(self.signals), 1, "one signal however many ROIs and fields")
        undo_manager.undo()
        self.assertEqual(self.diameters(1, 2, 3), [6.0, 6.0, 6.0])
        self.assertEqual([self.toolbox.roi_by_id(i).center_x for i in (1, 2, 3)], [10.0, 20.0, 30.0], "positions and diameters came back together")
        self.assertFalse(undo_manager.can_undo, "the whole paste was one step")

    def test_ring_diameters_and_rectangle_numbers_use_the_rules_of_typing_them(self) -> None:
        self.toolbox.set_cell_values({1: {"reference_inner_diameter_px": 10.0, "reference_outer_diameter_px": 20.0}, self.rect: {"width_px": 40.0, "angle_deg": -10.0}})
        roi = self.toolbox.roi_by_id(1)
        self.assertEqual(ring_diameters(roi.shape, roi.border), (10.0, 20.0))
        self.assertEqual(self.toolbox.roi_by_id(self.rect).shape, Rectangle(40.0, 14.0, -10.0))

    def test_all_or_nothing_and_the_roi_is_named(self) -> None:
        for values, text in (
            ({1: {"sample_diameter_px": 8.0}, 2: {"sample_diameter_px": 1.0}}, "ROI 2: sample diameter must be at least 2"),
            ({1: {"sample_diameter_px": 8.0}, 2: {"reference_inner_diameter_px": 50.0}}, "ROI 2: the rim needs"),
            ({1: {"sample_diameter_px": 8.0}, self.rect: {"sample_diameter_px": 8.0}}, f"ROI {self.rect} is not a circle"),
            ({1: {"sample_diameter_px": 8.0}, 3: {"width_px": 20.0}}, "ROI 3 is not a rectangle"),
            ({1: {"sample_diameter_px": 8.0}, self.rect: {"height_px": 1.0}}, f"ROI {self.rect}: height must be at least 2"),
            ({1: {"x": float("nan")}}, "ROI 1: position x must be a finite number"),
        ):
            with self.assertRaisesRegex(ValueError, text):
                self.toolbox.set_cell_values(values)
            self.assertEqual(self.diameters(1, 2, 3), [6.0, 6.0, 6.0], "nothing changed")
            self.assertFalse(undo_manager.can_undo)

    def test_a_pixel_mask_is_not_moved_and_a_stale_id_or_field_is_refused(self) -> None:
        self.toolbox.roi_by_id(2).shape = MaskShape(RoiMask(0, 0, np.ones((2, 2), dtype=bool)))
        with self.assertRaisesRegex(ValueError, "pixel mask"):
            self.toolbox.set_cell_values({1: {"x": 1.0}, 2: {"x": 2.0}})
        self.assertEqual(self.toolbox.roi_by_id(1).center_x, 10.0)
        with self.assertRaises(KeyError):
            self.toolbox.set_cell_values({99: {"x": 1.0}})
        with self.assertRaisesRegex(ValueError, "unknown field"):
            self.toolbox.set_cell_values({1: {"colour": 1.0}})

    def test_numbers_that_do_not_differ_leave_no_undo_entry_and_no_signal(self) -> None:
        self.toolbox.set_cell_values({1: {"sample_diameter_px": 6.0, "x": 10.0}, self.rect: {"width_px": 30.0}})
        self.assertFalse(undo_manager.can_undo)
        self.assertEqual(self.signals, [])

    def test_a_ring_keeps_its_width_when_only_the_sample_changes(self) -> None:
        self.toolbox.roi_by_id(3).border = NO_BORDER
        self.toolbox.set_cell_values({3: {"sample_diameter_px": 12.0}})
        self.assertEqual(self.toolbox.roi_by_id(3).border, NO_BORDER)
        self.assertEqual(self.toolbox.roi_by_id(3).shape, Circle(12.0))
        self.toolbox.set_cell_values({1: {"sample_diameter_px": 12.0}})
        self.assertIsNone(self.toolbox.roi_by_id(1).border, "a ROI on the shared default border keeps following it (the ring goes with the sample, as resize_rois does)")

    def test_it_agrees_with_resize_rois_for_the_same_numbers(self) -> None:
        other = RoiToolbox()
        other.add_roi(10.0, 5.0, sample_diameter_px=6.0)
        other.resize_rois([1], sample_diameter_px=9.0, reference_inner_diameter_px=12.0, reference_outer_diameter_px=24.0)
        self.toolbox.set_cell_values({1: {"sample_diameter_px": 9.0, "reference_inner_diameter_px": 12.0, "reference_outer_diameter_px": 24.0}})
        self.assertEqual(self.toolbox.roi_by_id(1).shape, other.roi_by_id(1).shape)
        self.assertEqual(self.toolbox.roi_by_id(1).border, other.roi_by_id(1).border)


if __name__ == "__main__":
    unittest.main()
