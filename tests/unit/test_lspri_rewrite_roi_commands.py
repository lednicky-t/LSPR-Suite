"""The ROI Toolbox's bulk commands: order, groups, colours, geometry.

The commands the ROI/Group table calls. Each is one undo step however many
ROIs it touches, validates before changing anything, and (for order) tells the
rest of the app which ROI is now which number. Pure palette/ordering helpers
are tested here too.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch**, like the
other `test_lspri_rewrite_*` files.
"""

from __future__ import annotations

import colorsys
import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.model import AreaRoiDetectionSettings
    from lspr_imaging_app.roi.ordering import move_block, renumbering
    from lspr_imaging_app.roi.palette import (
        GROUP_BASE_COLORS,
        first_free_tint_index,
        next_group_color,
        normalize_hex,
        tint_color,
    )
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


def _lightness(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)[1]


class PaletteTest(unittest.TestCase):
    def test_tint_zero_is_the_base_colour(self) -> None:
        self.assertEqual(tint_color("#FF7F0E", 0), "#ff7f0e")

    def test_the_first_seven_tints_are_distinct_and_span_a_range_of_lightness(self) -> None:
        for base in GROUP_BASE_COLORS:
            tints = [tint_color(base, i) for i in range(7)]
            self.assertEqual(len(set(tints)), 7, base)
            lightness = [_lightness(t) for t in tints]
            self.assertGreater(max(lightness) - min(lightness), 0.4, base)

    def test_a_large_group_keeps_getting_new_colours(self) -> None:
        tints = [tint_color("#1f77b4", i) for i in range(30)]
        self.assertEqual(len(set(tints)), 30)

    def test_tints_are_deterministic(self) -> None:
        self.assertEqual(tint_color("#2ca02c", 5), tint_color("#2ca02c", 5))

    def test_first_free_tint_fills_a_gap(self) -> None:
        base = "#d62728"
        used = [tint_color(base, 0), tint_color(base, 2), tint_color(base, 3)]
        self.assertEqual(first_free_tint_index(base, used), 1)
        self.assertEqual(first_free_tint_index(base, []), 0)
        self.assertEqual(first_free_tint_index(base, [None, ""]), 0)

    def test_next_group_color_skips_used_colours_and_cycles(self) -> None:
        self.assertEqual(next_group_color([]), GROUP_BASE_COLORS[0])
        self.assertEqual(next_group_color([GROUP_BASE_COLORS[0].upper()]), GROUP_BASE_COLORS[1])
        self.assertIn(next_group_color(list(GROUP_BASE_COLORS)), GROUP_BASE_COLORS)

    def test_normalize_hex_rejects_anything_but_rrggbb(self) -> None:
        self.assertEqual(normalize_hex("#ABCDEF"), "#abcdef")
        for bad in ("red", "#abc", "#abcdeg", "abcdef", "", None):
            with self.assertRaises(ValueError):
                normalize_hex(bad)  # type: ignore[arg-type]

    def test_gradient_runs_from_the_base_colour_to_a_lighter_shade_in_even_steps(self) -> None:
        import colorsys

        from lspr_imaging_app.roi.palette import gradient_tints

        colours = gradient_tints("#1f77b4", 5)
        self.assertEqual(colours[0], "#1f77b4")
        lightness = [colorsys.rgb_to_hls(*(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)))[1] for c in colours]
        steps = [b - a for a, b in zip(lightness, lightness[1:])]
        self.assertTrue(all(step > 0 for step in steps))
        self.assertLess(max(steps) - min(steps), 0.02)  # evenly spaced
        self.assertEqual(gradient_tints("#1f77b4", 1), ["#1f77b4"])
        self.assertEqual(gradient_tints("#1f77b4", 0), [])
        light = gradient_tints("#e6e6a0", 3)  # a light base darkens instead
        self.assertEqual(light[0], "#e6e6a0")
        self.assertLess(colorsys.rgb_to_hls(*(int(light[2][i:i + 2], 16) / 255 for i in (1, 3, 5)))[1], 0.5)

    def test_a_grey_base_colour_still_hands_out_new_tints_and_never_hangs(self) -> None:
        """Regression (2026-10-09): grey has no hue, so every 7th tint repeated and the search for a free tint
        never ended once an 8th group (grey) had 8 members: the app froze on "group by rows"."""
        grey = "#7f7f7f"
        used: list[str] = []
        for _ in range(300):
            used.append(tint_color(grey, first_free_tint_index(grey, used)))
        self.assertGreater(len(set(used[:20])), 14)  # the first members are told apart


class OrderingHelpersTest(unittest.TestCase):
    def test_move_block_up_down_front_and_back(self) -> None:
        order = [1, 2, 3, 4, 5]
        self.assertEqual(move_block(order, [3], 1), [1, 3, 2, 4, 5])      # up one
        self.assertEqual(move_block(order, [3], 3), [1, 2, 4, 3, 5])      # down one
        self.assertEqual(move_block(order, [4, 5], 0), [4, 5, 1, 2, 3])   # block to the front
        self.assertEqual(move_block(order, [1, 2], 99), [3, 4, 5, 1, 2])  # clamped to the end
        self.assertEqual(move_block(order, [3], -5), [3, 1, 2, 4, 5])     # clamped to the start

    def test_a_block_keeps_its_internal_order_and_unknown_ids_are_ignored(self) -> None:
        self.assertEqual(move_block([1, 2, 3, 4], [4, 2, 99], 0), [2, 4, 1, 3])

    def test_renumbering_puts_each_roi_in_its_slot(self) -> None:
        self.assertEqual(renumbering([3, 1, 5], [1, 3, 5]), {3: 1, 1: 3, 5: 5})
        with self.assertRaises(ValueError):
            renumbering([1, 2], [1])


class _ToolboxCase(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.renumbered: list[dict[int, int]] = []
        self.cosmetic: list[object] = []
        self.geometry: list[object] = []
        self.toolbox.roi_ids_renumbered.connect(self.renumbered.append)
        self.toolbox.cosmetic_changed.connect(self.cosmetic.append)
        for x in (10.0, 20.0, 30.0, 40.0, 50.0):
            self.toolbox.add_roi(x, 5.0, sample_diameter_px=6.0)
        self.toolbox.geometry_changed.connect(self.geometry.append)  # after setup: only what the test does

    def tearDown(self) -> None:
        undo_manager.clear()

    def xs(self) -> list[float]:
        """x of the ROIs in id order - tells *which* ROI holds each id."""
        return [roi.center_x for roi in sorted(self.toolbox.rois(), key=lambda r: r.area_roi_id)]

    def colors(self) -> dict[int, str | None]:
        return {roi.area_roi_id: roi.sample_color_hex for roi in self.toolbox.rois()}

    def depth(self) -> int:
        """How many undo steps are on the stack (leaves the stack as it was)."""
        n = 0
        while undo_manager.can_undo:
            undo_manager.undo()
            n += 1
        for _ in range(n):
            undo_manager.redo()
        return n


class ReorderTest(_ToolboxCase):
    def test_reorder_gives_each_roi_its_new_number_and_keeps_the_objects(self) -> None:
        third = self.toolbox.roi_by_id(3)
        self.toolbox.reorder_rois((3, 1, 2, 4, 5))
        self.assertEqual(self.xs(), [30.0, 10.0, 20.0, 40.0, 50.0])
        self.assertIs(self.toolbox.roi_by_id(1), third)
        self.assertEqual(self.renumbered[-1], {3: 1, 1: 2, 2: 3, 4: 4, 5: 5})
        self.assertEqual([r.area_roi_id for r in self.toolbox.rois()], [1, 2, 3, 4, 5])

    def test_undo_and_redo_restore_the_numbering_and_announce_it(self) -> None:
        self.toolbox.reorder_rois((5, 4, 3, 2, 1))
        undo_manager.undo()
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0])
        self.assertEqual(self.renumbered[-1], {1: 5, 2: 4, 3: 3, 4: 2, 5: 1})
        undo_manager.redo()
        self.assertEqual(self.xs(), [50.0, 40.0, 30.0, 20.0, 10.0])

    def test_move_one_place_up_and_down(self) -> None:
        self.toolbox.move_in_order((3,), 1)  # id 3 (position 2) up one
        self.assertEqual(self.xs(), [10.0, 30.0, 20.0, 40.0, 50.0])
        self.toolbox.move_in_order((2,), 2)  # that ROI (now id 2, position 1) down one
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0])

    def test_move_a_selection_to_the_front(self) -> None:
        self.toolbox.move_in_order((4, 5), 0)
        self.assertEqual(self.xs(), [40.0, 50.0, 10.0, 20.0, 30.0])

    def test_moving_within_a_group_leaves_other_groups_numbers_alone(self) -> None:
        self.toolbox.group_rois((1, 3, 5), "odd")
        self.toolbox.move_in_order((5,), 0, scope_ids=(1, 3, 5))
        # ids 1, 3, 5 are reshuffled among themselves; 2 and 4 keep their ROIs
        self.assertEqual(self.xs(), [50.0, 20.0, 10.0, 40.0, 30.0])
        self.assertEqual(self.toolbox.groups()[0].area_roi_ids, [1, 3, 5])
        self.assertEqual(self.renumbered[-1], {1: 3, 2: 2, 3: 5, 4: 4, 5: 1})

    def test_group_and_array_membership_follow_their_rois(self) -> None:
        self.toolbox.group_rois((1, 2), "first two")
        first = self.toolbox.roi_by_id(1)
        self.toolbox.reorder_rois((5, 4, 3, 2, 1))
        group = self.toolbox.group_for_roi(first.area_roi_id)
        self.assertIsNotNone(group)
        self.assertEqual(first.area_roi_id, 5)
        self.assertEqual(group.area_roi_ids, [4, 5])

    def test_a_reorder_that_changes_nothing_records_nothing(self) -> None:
        before, depth = len(self.renumbered), self.depth()
        self.toolbox.reorder_rois((1, 2, 3, 4, 5))
        self.toolbox.move_in_order((1,), 0)
        self.assertEqual(len(self.renumbered), before)
        self.assertEqual(self.depth(), depth)

    def test_invalid_orders_raise_and_change_nothing(self) -> None:
        with self.assertRaises(ValueError):
            self.toolbox.reorder_rois((1, 2, 3))          # missing ids
        with self.assertRaises(ValueError):
            self.toolbox.reorder_rois((1, 1, 2, 3, 4))    # duplicate
        with self.assertRaises(ValueError):
            self.toolbox.move_in_order((4,), 0, scope_ids=(1, 2, 3))  # moved ROI not in scope
        with self.assertRaises(KeyError):
            self.toolbox.move_in_order((1,), 0, scope_ids=(1, 99))
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0])


class GroupTest(_ToolboxCase):
    def test_new_groups_get_different_base_colours(self) -> None:
        a = self.toolbox.create_group("A")
        b = self.toolbox.create_group("B")
        groups = {g.group_id: g for g in self.toolbox.groups()}
        self.assertNotEqual(groups[a].sample_color_hex, groups[b].sample_color_hex)

    def test_grouping_a_selection_tints_each_member_differently(self) -> None:
        group_id = self.toolbox.group_rois((1, 2, 3), "A")
        (group,) = self.toolbox.groups()
        self.assertEqual(group.group_id, group_id)
        self.assertEqual(group.area_roi_ids, [1, 2, 3])
        colors = self.colors()
        self.assertEqual(colors[1], group.sample_color_hex, "the first member shows the group's own colour")
        self.assertEqual(len({colors[1], colors[2], colors[3]}), 3)
        self.assertIsNone(colors[4])

    def test_grouping_is_one_undo_step_and_undo_restores_the_old_groups(self) -> None:
        self.toolbox.group_rois((1, 2), "A")
        before_colors = self.colors()
        self.toolbox.group_rois((2, 3), "B")  # takes 2 out of A
        self.assertEqual([g.area_roi_ids for g in self.toolbox.groups()], [[1], [2, 3]])
        undo_manager.undo()
        self.assertEqual([(g.name, g.area_roi_ids) for g in self.toolbox.groups()], [("A", [1, 2])])
        self.assertEqual(self.colors(), before_colors)
        undo_manager.redo()
        self.assertEqual([g.area_roi_ids for g in self.toolbox.groups()], [[1], [2, 3]])

    def test_a_group_emptied_by_a_move_is_removed_but_an_empty_one_made_on_purpose_stays(self) -> None:
        self.toolbox.create_group("empty on purpose")
        first = self.toolbox.group_rois((1,), "solo")
        self.toolbox.group_rois((1,), "new home")
        names = [g.name for g in self.toolbox.groups()]
        self.assertEqual(names, ["empty on purpose", "new home"])
        self.assertNotIn(first, [g.group_id for g in self.toolbox.groups()])

    def test_a_new_group_is_a_gradient_in_id_order_and_a_newcomer_gets_a_colour_no_member_has(self) -> None:
        import colorsys

        group_id = self.toolbox.group_rois((3, 1, 2), "A")
        base = self.toolbox.groups()[0].sample_color_hex
        colors = self.colors()
        self.assertEqual(colors[1], base, "the first member shows the group's colour")
        lightness = [colorsys.rgb_to_hls(*(int(colors[i][k:k + 2], 16) / 255 for k in (1, 3, 5)))[1] for i in (1, 2, 3)]
        self.assertLess(lightness[0], lightness[1])
        self.assertLess(lightness[1], lightness[2])
        before = dict(colors)
        self.toolbox.add_rois_to_group((4,), group_id)
        after = self.colors()
        self.assertEqual({i: after[i] for i in (1, 2, 3)}, {i: before[i] for i in (1, 2, 3)}, "members keep their colour")
        self.assertNotIn(after[4], {before[i] for i in (1, 2, 3)})

    def test_ungrouping_clears_the_tint_and_can_target_one_group(self) -> None:
        a = self.toolbox.group_rois((1, 2), "A")
        self.toolbox.group_rois((3,), "B")
        self.toolbox.remove_rois_from_groups((1, 3), group_id=a)  # 3 is not in A: untouched
        self.assertIsNone(self.colors()[1])
        self.assertIsNotNone(self.colors()[3])
        self.assertEqual({g.name: g.area_roi_ids for g in self.toolbox.groups()}, {"A": [2], "B": [3]})
        self.toolbox.remove_rois_from_groups((2, 3))
        self.assertEqual(self.toolbox.groups(), ())

    def test_deleting_a_group_keeps_its_rois_and_undo_brings_it_back(self) -> None:
        group_id = self.toolbox.group_rois((1, 2), "A")
        colors = self.colors()
        self.toolbox.delete_group(group_id)
        self.assertEqual(self.toolbox.groups(), ())
        self.assertEqual(len(self.toolbox.rois()), 5)
        self.assertIsNone(self.colors()[1])
        undo_manager.undo()
        self.assertEqual([g.area_roi_ids for g in self.toolbox.groups()], [[1, 2]])
        self.assertEqual(self.colors(), colors)

    def test_recolouring_repaints_the_members_unless_told_not_to(self) -> None:
        group_id = self.toolbox.group_rois((1, 2, 3), "A")
        self.toolbox.set_roi_colors((2,), "#123456")  # a hand-picked colour
        self.toolbox.recolor_group(group_id, "#ff0000")
        self.assertEqual([self.colors()[i] for i in (1, 2, 3)], [tint_color("#ff0000", i) for i in range(3)])
        self.assertEqual(self.toolbox.groups()[0].sample_color_hex, "#ff0000")
        undo_manager.undo()
        self.assertEqual(self.colors()[2], "#123456")
        self.toolbox.recolor_group(group_id, "#00ff00", repaint_members=False)
        self.assertEqual(self.colors()[2], "#123456")
        self.assertEqual(self.toolbox.groups()[0].sample_color_hex, "#00ff00")

    def test_setting_colours_by_hand_and_clearing_them(self) -> None:
        self.toolbox.set_roi_colors((1, 2, 3), "#ABCDEF")
        self.assertEqual([self.colors()[i] for i in (1, 2, 3)], ["#abcdef"] * 3)
        undo_manager.undo()
        self.assertEqual(self.colors()[1], None, "one undo step for all three")
        undo_manager.redo()
        self.toolbox.set_roi_colors((1,), None)
        self.assertIsNone(self.colors()[1])
        with self.assertRaises(ValueError):
            self.toolbox.set_roi_colors((1,), "blue")

    def test_names_are_required_and_a_no_op_records_nothing(self) -> None:
        with self.assertRaises(ValueError):
            self.toolbox.create_group("   ")
        with self.assertRaises(ValueError):
            self.toolbox.group_rois((1,), "")
        self.assertEqual(self.toolbox.groups(), ())
        group_id = self.toolbox.create_group("A")
        with self.assertRaises(ValueError):
            self.toolbox.rename_group(group_id, " ")
        depth = self.depth()
        self.toolbox.rename_group(group_id, "A")
        self.toolbox.set_roi_colors((1,), None)
        self.assertEqual(self.depth(), depth)

    def test_unknown_ids_raise_instead_of_being_skipped(self) -> None:
        with self.assertRaises(KeyError):
            self.toolbox.group_rois((1, 99), "A")
        with self.assertRaises(KeyError):
            self.toolbox.add_rois_to_group((1,), "group_404")
        self.assertEqual(self.toolbox.groups(), ())

    def test_older_undo_steps_still_reach_the_live_group_after_later_ones_are_undone(self) -> None:
        """Undo restores group fields onto the same objects. Were a later undo
        to swap in copies, an earlier step holding the original object would
        edit one that is no longer in the toolbox."""
        group_id = self.toolbox.create_group("G")
        self.toolbox.add_to_group(1, group_id)
        self.toolbox.delete_group(group_id)
        undo_manager.undo()   # the delete
        undo_manager.undo()   # the add
        (group,) = self.toolbox.groups()
        self.assertEqual(group.area_roi_ids, [])
        self.assertIsNone(self.colors()[1])

    def test_undoing_a_delete_of_rois_restores_the_same_group_objects(self) -> None:
        group_id = self.toolbox.create_group("G")
        self.toolbox.add_rois_to_group((1, 2), group_id)
        (group,) = self.toolbox.groups()
        self.toolbox.delete_rois((1, 2))
        self.assertEqual(self.toolbox.groups(), ())
        undo_manager.undo()   # the delete: the group is back
        (restored,) = self.toolbox.groups()
        self.assertIs(restored, group)
        undo_manager.undo()   # the add: must edit the live group
        self.assertEqual(restored.area_roi_ids, [])


class GeometryCommandsTest(_ToolboxCase):
    def test_translating_several_rois_is_one_change_and_one_undo_step(self) -> None:
        self.toolbox.translate_rois((1, 2, 3), 2.0, -1.0)
        self.assertEqual(self.xs()[:4], [12.0, 22.0, 32.0, 40.0])
        self.assertEqual([r.center_y for r in self.toolbox.rois()][:4], [4.0, 4.0, 4.0, 5.0])
        self.assertEqual(len(self.geometry), 1)
        self.assertEqual(self.geometry[0].roi_ids, (1, 2, 3))
        undo_manager.undo()
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0])

    def test_aligning_sets_one_coordinate_only(self) -> None:
        self.toolbox.place_rois((1, 2, 3), y=99.0)
        self.assertEqual([r.center_y for r in self.toolbox.rois()], [99.0, 99.0, 99.0, 5.0, 5.0])
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0])
        self.toolbox.place_rois((1, 2), x=0.0)
        self.assertEqual(self.xs()[:3], [0.0, 0.0, 30.0])

    def test_non_finite_positions_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.toolbox.translate_rois((1,), float("nan"), 0.0)
        self.assertEqual(self.xs(), [10.0, 20.0, 30.0, 40.0, 50.0])

    def test_resizing_many_rois(self) -> None:
        self.toolbox.resize_rois((1, 2), sample_diameter_px=12.0, reference_inner_diameter_px=16.0, reference_outer_diameter_px=24.0)
        for roi_id in (1, 2):
            roi = self.toolbox.roi_by_id(roi_id)
            self.assertEqual((roi.sample_diameter_px, roi.reference_inner_diameter_px, roi.reference_outer_diameter_px), (12.0, 16.0, 24.0))
        self.assertEqual(self.toolbox.roi_by_id(3).sample_diameter_px, 6.0)
        self.assertEqual(len(self.geometry), 1)
        undo_manager.undo()
        self.assertIsNone(self.toolbox.roi_by_id(1).reference_inner_diameter_px)
        self.assertEqual(self.toolbox.roi_by_id(1).sample_diameter_px, 6.0)

    def test_invalid_sizes_raise_before_anything_changes(self) -> None:
        defaults = AreaRoiDetectionSettings()  # inner 28, outer 36
        self.assertLess(defaults.reference_inner_diameter_px, defaults.reference_outer_diameter_px)
        with self.assertRaises(ValueError):
            self.toolbox.resize_rois((1, 2), sample_diameter_px=1.0)                       # below the minimum
        with self.assertRaises(ValueError):
            self.toolbox.resize_rois((1, 2), sample_diameter_px=float("inf"))
        with self.assertRaises(ValueError):
            self.toolbox.resize_rois((1,), reference_inner_diameter_px=30.0, reference_outer_diameter_px=30.0)
        with self.assertRaises(ValueError):                                                 # outer inherited from the default
            self.toolbox.resize_rois((1,), reference_inner_diameter_px=defaults.reference_outer_diameter_px + 1)
        with self.assertRaises(ValueError):
            self.toolbox.resize_rois((1,), reference_inner_diameter_px=-1.0)
        for roi in self.toolbox.rois():
            self.assertEqual((roi.sample_diameter_px, roi.reference_inner_diameter_px, roi.reference_outer_diameter_px), (6.0, None, None))
        self.assertEqual(self.geometry, [])
        self.toolbox.resize_rois((1,), reference_inner_diameter_px=10.0)  # fine: 10 < the inherited outer
        self.assertEqual(self.toolbox.roi_by_id(1).reference_inner_diameter_px, 10.0)

    def test_the_one_roi_form_still_works_and_undoes(self) -> None:
        self.toolbox.resize_roi(2, sample_diameter_px=9.0)
        self.assertEqual(self.toolbox.roi_by_id(2).sample_diameter_px, 9.0)
        undo_manager.undo()
        self.assertEqual(self.toolbox.roi_by_id(2).sample_diameter_px, 6.0)

    def test_resetting_diameters_returns_to_the_shared_defaults(self) -> None:
        defaults = AreaRoiDetectionSettings()
        self.toolbox.resize_rois((1, 2), sample_diameter_px=12.0, reference_inner_diameter_px=16.0, reference_outer_diameter_px=24.0)
        self.toolbox.reset_roi_diameters((1,))
        reset = self.toolbox.roi_by_id(1)
        self.assertEqual(reset.sample_diameter_px, defaults.sample_diameter_px)
        self.assertEqual((reset.reference_inner_diameter_px, reset.reference_outer_diameter_px), (None, None))
        self.assertEqual(self.toolbox.roi_by_id(2).sample_diameter_px, 12.0)
        self.toolbox.reset_roi_diameters((2,), sample=False)
        self.assertEqual(self.toolbox.roi_by_id(2).sample_diameter_px, 12.0)
        self.assertIsNone(self.toolbox.roi_by_id(2).reference_inner_diameter_px)
        undo_manager.undo()
        self.assertEqual(self.toolbox.roi_by_id(2).reference_inner_diameter_px, 16.0)

    def test_labels(self) -> None:
        self.toolbox.set_roi_label(1, "  spot A ")
        self.assertEqual(self.toolbox.roi_by_id(1).label, "spot A")
        undo_manager.undo()
        self.assertIsNone(self.toolbox.roi_by_id(1).label)
        self.toolbox.set_roi_label(1, "   ")
        self.assertIsNone(self.toolbox.roi_by_id(1).label)


if __name__ == "__main__":
    unittest.main()
