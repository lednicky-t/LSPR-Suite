"""The pure half of the Sensorgram's "Experimental plan" bar (`lspri_imaging_app.plan`): which part of a note is shown, the time <-> x map, the saved edits
and the edit operations. No Qt, no files. Synthetic steps: two steps that follow each other (one joined boundary), an idle stretch, a third step; cubes of 2 s."""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import numpy as np

    from lspr_core import ImagingPlan, ImagingPlanStep
    from lspr_imaging_app.plan import edits
    from lspr_imaging_app.plan.axis_map import AxisMap
    from lspr_imaging_app.plan.label_rule import (
        DEFAULT_SEPARATORS, MODE_SOLUTION, MODE_WHOLE, format_separators, parse_separators, shown_label,
    )
    from lspr_imaging_app.plan.state import PlanEdit, PlanEditsState, normalise_color
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

S1, S2, S3 = "H2O_--_ch1F_ch2P", "10perEG_-_ch1F", "H2O_--_ch1F_ch2P"


def _plan(color_of_first: str | None = None) -> ImagingPlan:
    return ImagingPlan(steps=[
        ImagingPlanStep(step=1, label=S1, start_unix_ms=10_000, end_unix_ms=30_000, color=color_of_first),
        ImagingPlanStep(step=2, label=S2, start_unix_ms=30_000, end_unix_ms=50_000),  # joined to step 1
        ImagingPlanStep(step=3, label=S3, start_unix_ms=60_000, end_unix_ms=80_000),  # an idle stretch lies before it
    ])


GRID = np.array([*range(0, 80_001, 2_000), 81_000], dtype=np.float64)  # a cube every 2 s, and the end of the data


def _steps(state: PlanEditsState = PlanEditsState(), plan: ImagingPlan | None = None) -> tuple[edits.EffectiveStep, ...]:
    return edits.apply_edits(plan or _plan(), state).steps


class LabelRuleTest(unittest.TestCase):
    def test_whole_note_is_shown_without_surrounding_spaces(self) -> None:
        self.assertEqual(shown_label("  30perEG_-_ch2  ", MODE_WHOLE), "30perEG_-_ch2")

    def test_solution_is_the_text_before_the_separator_for_both_notations(self) -> None:
        self.assertEqual(shown_label("30perEG_-_ch2_to_50perEG_-_ch1P_ch4F", MODE_SOLUTION), "30perEG")
        self.assertEqual(shown_label("H2O_--_ch1F_ch2P", MODE_SOLUTION), "H2O")

    def test_the_earliest_separator_wins(self) -> None:
        self.assertEqual(shown_label("A_--_B_-_C", MODE_SOLUTION), "A")
        self.assertEqual(shown_label("A_-_B_--_C", MODE_SOLUTION), "A")

    def test_the_longest_separator_wins_when_two_start_at_one_place(self) -> None:
        self.assertEqual(shown_label("A_--_B", MODE_SOLUTION, ("_-", "_--_")), "A")

    def test_a_note_without_a_separator_is_shown_whole(self) -> None:
        self.assertEqual(shown_label("Water", MODE_SOLUTION), "Water")

    def test_a_label_is_never_blank_because_of_the_rule(self) -> None:
        self.assertEqual(shown_label("_-_ch1", MODE_SOLUTION), "_-_ch1")
        self.assertEqual(shown_label("  _-_ch1", MODE_SOLUTION), "_-_ch1")

    def test_empty_separators_are_ignored(self) -> None:
        self.assertEqual(shown_label("A_-_B", MODE_SOLUTION, ("", "_-_")), "A")
        self.assertEqual(shown_label("A_-_B", MODE_SOLUTION, ()), "A_-_B")

    def test_the_separator_field_parses_and_formats(self) -> None:
        self.assertEqual(parse_separators("_-_  _--_,_-_ ; "), ("_-_", "_--_"))
        self.assertEqual(format_separators(DEFAULT_SEPARATORS), "_-_ _--_")
        self.assertEqual(parse_separators(format_separators(DEFAULT_SEPARATORS)), DEFAULT_SEPARATORS)


class AxisMapTest(unittest.TestCase):
    def test_time_axis_is_exact_and_round_trips(self) -> None:
        cubes_ms = list(range(10_000, 50_001, 2_000))
        x = [(ms - 10_000) / 1000.0 for ms in cubes_ms]  # seconds from the start
        axis = AxisMap.build(cubes_ms, x)
        self.assertAlmostEqual(axis.x_at(25_000), 15.0, places=9)
        self.assertAlmostEqual(axis.unix_ms_at(15.0), 25_000.0, places=6)
        for ms in (10_000, 17_321, 49_999):
            self.assertAlmostEqual(axis.unix_ms_at(axis.x_at(ms)), ms, places=6)

    def test_a_boundary_inside_a_cube_sits_inside_that_cube_on_the_cube_axis(self) -> None:
        axis = AxisMap.build(list(range(0, 20_001, 2_000)), list(range(11)))  # cube n starts at 2000 n ms
        self.assertAlmostEqual(axis.x_at(5_000), 2.5, places=9)
        self.assertAlmostEqual(axis.unix_ms_at(2.5), 5_000.0, places=6)

    def test_outside_the_first_and_last_point_it_extrapolates_with_the_mean_slope(self) -> None:
        axis = AxisMap.build([2_000, 4_000, 6_000], [1.0, 2.0, 3.0])
        self.assertAlmostEqual(axis.x_at(0), 0.0, places=9)
        self.assertAlmostEqual(axis.x_at(10_000), 5.0, places=9)
        self.assertAlmostEqual(axis.unix_ms_at(0.0), 0.0, places=6)
        self.assertAlmostEqual(axis.unix_ms_at(5.0), 10_000.0, places=6)

    def test_pairs_with_a_missing_value_are_dropped(self) -> None:
        axis = AxisMap.build([0, None, 4_000, 6_000], [0.0, 1.0, float("nan"), 3.0])
        self.assertEqual(axis.unix_ms.tolist(), [0.0, 6_000.0])

    def test_no_map_without_two_usable_points_or_when_x_does_not_grow_with_time(self) -> None:
        self.assertIsNone(AxisMap.build([0], [0.0]))
        self.assertIsNone(AxisMap.build([0, None], [0.0, 1.0]))
        self.assertIsNone(AxisMap.build([0, 2_000, 4_000], [0.0, 5.0, 3.0]))


class StateTest(unittest.TestCase):
    def test_payload_round_trip_and_stable_order(self) -> None:
        a = PlanEdit(step=2, measured_start_unix_ms=30_000, measured_label=S2, end_unix_ms=51_000, color="#112233")
        b = PlanEdit(step=1, measured_start_unix_ms=10_000, measured_label=S1, label="Water")
        state = PlanEditsState.from_payload(PlanEditsState(edits=(a, b)).to_payload())
        self.assertEqual([e.step for e in state.edits], [1, 2])
        self.assertEqual(state.edit_for(a.key), a)
        self.assertEqual(PlanEditsState.from_payload(state.to_payload()), state)
        self.assertEqual(PlanEditsState.from_payload(PlanEditsState(edits=(b, a)).to_payload()), state)  # the order given does not matter

    def test_only_changed_fields_are_written(self) -> None:
        payload = PlanEdit(step=1, measured_start_unix_ms=1, measured_label="x", label="y").to_payload()
        self.assertEqual(set(payload), {"step", "measured_start_unix_ms", "measured_label", "label"})

    def test_a_missing_or_wrong_block_gives_no_edits(self) -> None:
        for raw in (None, 5, [], {}, {"edits": "no"}):
            self.assertEqual(PlanEditsState.from_payload(raw), PlanEditsState())

    def test_an_unreadable_record_is_skipped_and_the_others_kept(self) -> None:
        good = PlanEdit(step=1, measured_start_unix_ms=10_000, measured_label=S1, label="Water").to_payload()
        state = PlanEditsState.from_payload({"edits": [{"step": "x"}, "junk", good, {"step": 2}]})
        self.assertEqual([e.label for e in state.edits], ["Water"])

    def test_empty_edits_are_dropped_and_the_last_duplicate_wins(self) -> None:
        empty = PlanEdit(step=3, measured_start_unix_ms=60_000, measured_label=S3).to_payload()
        one = PlanEdit(step=1, measured_start_unix_ms=10_000, measured_label=S1, label="A").to_payload()
        two = PlanEdit(step=1, measured_start_unix_ms=10_000, measured_label=S1, label="B").to_payload()
        state = PlanEditsState.from_payload({"edits": [empty, one, two]})
        self.assertEqual([e.label for e in state.edits], ["B"])

    def test_colours_are_normalised(self) -> None:
        self.assertEqual(normalise_color("#ABC"), "#aabbcc")
        self.assertEqual(normalise_color(" #3399FF "), "#3399ff")
        for bad in ("red", "#12", "#gggggg", "", None, 5):
            self.assertIsNone(normalise_color(bad))


class ApplyEditsTest(unittest.TestCase):
    def test_without_edits_the_steps_are_the_measured_ones(self) -> None:
        applied = edits.apply_edits(_plan(), PlanEditsState())
        self.assertEqual([(s.step, s.label, s.start_unix_ms, s.end_unix_ms, s.edited) for s in applied.steps],
                         [(1, S1, 10_000, 30_000, False), (2, S2, 30_000, 50_000, False), (3, S3, 60_000, 80_000, False)])
        self.assertEqual(applied.warnings, ())

    def test_edits_replace_only_what_they_set(self) -> None:
        state = PlanEditsState(edits=(PlanEdit(step=2, measured_start_unix_ms=30_000, measured_label=S2, start_unix_ms=32_000, label="X", color="#aabbcc"),))
        step = edits.apply_edits(_plan(), state).steps[1]
        self.assertEqual((step.start_unix_ms, step.end_unix_ms, step.label, step.color, step.edited), (32_000, 50_000, "X", "#aabbcc", True))
        self.assertEqual((step.measured_start_unix_ms, step.measured_label), (30_000, S2))

    def test_colour_falls_back_to_the_plan_files_then_none(self) -> None:
        steps = edits.apply_edits(_plan(color_of_first="#FF0000"), PlanEditsState()).steps
        self.assertEqual((steps[0].color, steps[1].color), ("#ff0000", None))

    def test_an_edit_that_no_longer_matches_is_reported_and_not_applied(self) -> None:
        stale = PlanEdit(step=2, measured_start_unix_ms=30_000, measured_label="another note", label="X")
        applied = edits.apply_edits(_plan(), PlanEditsState(edits=(stale,)))
        self.assertEqual(applied.steps[1].label, S2)
        self.assertEqual(len(applied.warnings), 1)
        self.assertIn("step 2", applied.warnings[0])

    def test_a_shifted_measured_start_also_does_not_match(self) -> None:
        stale = PlanEdit(step=1, measured_start_unix_ms=10_001, measured_label=S1, label="X")
        applied = edits.apply_edits(_plan(), PlanEditsState(edits=(stale,)))
        self.assertEqual(applied.steps[0].label, S1)
        self.assertEqual(len(applied.warnings), 1)

    def test_no_plan_at_all_reports_every_edit(self) -> None:
        applied = edits.apply_edits(None, PlanEditsState(edits=(PlanEdit(step=1, measured_start_unix_ms=10_000, measured_label=S1, label="X"),)))
        self.assertEqual((applied.steps, len(applied.warnings)), ((), 1))

    def test_times_out_of_order_fall_back_to_the_measured_times(self) -> None:
        bad = PlanEdit(step=1, measured_start_unix_ms=10_000, measured_label=S1, start_unix_ms=40_000)  # starts after its own end
        applied = edits.apply_edits(_plan(), PlanEditsState(edits=(bad,)))
        self.assertEqual((applied.steps[0].start_unix_ms, applied.steps[0].end_unix_ms), (10_000, 30_000))
        self.assertEqual(len(applied.warnings), 1)

    def test_the_measured_plan_is_not_changed(self) -> None:
        plan = _plan()
        edits.apply_edits(plan, PlanEditsState(edits=(PlanEdit(step=1, measured_start_unix_ms=10_000, measured_label=S1, label="X", end_unix_ms=20_000),)))
        self.assertEqual((plan.steps[0].label, plan.steps[0].end_unix_ms), (S1, 30_000))


class MoveEdgeTest(unittest.TestCase):
    def test_a_joined_boundary_moves_both_steps_and_snaps_to_a_cube_start(self) -> None:
        steps = _steps()
        state = edits.move_edge(steps, PlanEditsState(), 0, "end", 41_300, GRID, together=True)  # between the cubes at 40 s and 42 s: the nearer one is 42 s
        moved = _steps(state)
        self.assertEqual((moved[0].end_unix_ms, moved[1].start_unix_ms), (42_000, 42_000))
        self.assertEqual((moved[1].end_unix_ms, moved[0].start_unix_ms), (50_000, 10_000))
        self.assertTrue(moved[0].edited and moved[1].edited and not moved[2].edited)

    def test_dragging_from_the_other_side_of_the_boundary_does_the_same(self) -> None:
        state = edits.move_edge(_steps(), PlanEditsState(), 1, "start", 21_000, GRID, together=True)  # 20 s or 22 s are equally near; the lower one wins
        moved = _steps(state)
        self.assertEqual((moved[0].end_unix_ms, moved[1].start_unix_ms), (20_000, 20_000))

    def test_dragging_back_to_the_measured_boundary_leaves_no_edit(self) -> None:
        state = edits.move_edge(_steps(), PlanEditsState(), 0, "end", 42_000, GRID, together=True)
        back = edits.move_edge(_steps(state), state, 0, "end", 30_100, GRID, together=True)
        self.assertEqual(back, PlanEditsState())

    def test_a_boundary_stops_at_the_far_edges_of_its_neighbours(self) -> None:
        low = _steps(edits.move_edge(_steps(), PlanEditsState(), 0, "end", 0, GRID, together=True, min_length_ms=4_000))
        self.assertEqual(low[0].end_unix_ms, 14_000)  # step 1 keeps four seconds (its start is at 10 s)
        high = _steps(edits.move_edge(_steps(), PlanEditsState(), 0, "end", 79_000, GRID, together=True, min_length_ms=4_000))
        self.assertEqual(high[0].end_unix_ms, 46_000)  # step 2 keeps four seconds (its end is at 50 s)

    def test_a_free_edge_moves_alone_and_may_touch_the_next_step(self) -> None:
        state = edits.move_edge(_steps(), PlanEditsState(), 1, "end", 70_000, GRID)  # an idle stretch lies between steps 2 and 3
        moved = _steps(state)
        self.assertEqual((moved[1].end_unix_ms, moved[2].start_unix_ms), (60_000, 60_000))  # stopped where step 3 begins
        self.assertEqual(moved[2].end_unix_ms, 80_000)

    def test_the_first_start_and_the_last_end_are_free_towards_the_outside(self) -> None:
        first = _steps(edits.move_edge(_steps(), PlanEditsState(), 0, "start", -5_000, GRID))
        self.assertEqual(first[0].start_unix_ms, 0)  # the earliest grid point
        last = _steps(edits.move_edge(_steps(), PlanEditsState(), 2, "end", 500_000, GRID))
        self.assertEqual(last[2].end_unix_ms, 81_000)  # the end of the data

    def test_nothing_changes_when_no_grid_point_fits_or_the_edge_is_already_there(self) -> None:
        state = PlanEditsState()
        self.assertIs(edits.move_edge(_steps(), state, 0, "end", 41_000, np.empty(0)), state)
        self.assertIs(edits.move_edge(_steps(), state, 0, "end", 30_400, GRID), state)  # the nearest grid point is where it already is
        self.assertIs(edits.move_edge(_steps(), state, 0, "end", 41_000, np.array([100_000.0])), state)

    def test_moving_one_edge_keeps_the_other_edits_of_the_step(self) -> None:
        state = edits.set_label(PlanEditsState(), _steps()[0], "Water")
        state = edits.move_edge(_steps(state), state, 0, "end", 42_000, GRID, together=True)
        step = _steps(state)[0]
        self.assertEqual((step.label, step.end_unix_ms), ("Water", 42_000))


class IndependentEdgeTest(unittest.TestCase):
    """The default: only the dragged step changes and it stops at its neighbour's edge (Shift in the UI = `together`)."""

    def test_shrinking_a_step_leaves_a_gap_and_the_neighbour_alone(self) -> None:
        state = edits.move_edge(_steps(), PlanEditsState(), 0, "end", 21_000, GRID)  # 20 s or 22 s are equally near; the lower one wins
        moved = _steps(state)
        self.assertEqual((moved[0].start_unix_ms, moved[0].end_unix_ms), (10_000, 20_000))
        self.assertEqual((moved[1].start_unix_ms, moved[1].end_unix_ms), (30_000, 50_000))  # untouched
        self.assertTrue(moved[0].edited and not moved[1].edited)

    def test_growing_into_the_neighbour_is_stopped_at_its_edge(self) -> None:
        state = PlanEditsState()
        self.assertIs(edits.move_edge(_steps(), state, 0, "end", 41_000, GRID), state)  # step 2 starts where step 1 ends: nothing to grow into
        self.assertIs(edits.move_edge(_steps(), state, 1, "start", 21_000, GRID), state)

    def test_the_start_of_a_step_moves_alone_too(self) -> None:
        moved = _steps(edits.move_edge(_steps(), PlanEditsState(), 1, "start", 36_100, GRID))
        self.assertEqual((moved[0].end_unix_ms, moved[1].start_unix_ms), (30_000, 36_000))

    def test_closing_the_gap_again_makes_the_boundary_shared_once_more(self) -> None:
        state = edits.move_edge(_steps(), PlanEditsState(), 1, "start", 36_000, GRID)  # step 2 starts later: a gap opens
        state = edits.move_edge(_steps(state), state, 0, "end", 39_000, GRID)  # step 1 grows into the gap, up to where step 2 starts
        moved = _steps(state)
        self.assertEqual((moved[0].end_unix_ms, moved[1].start_unix_ms), (36_000, 36_000))
        # ... and a joined boundary can be moved together again
        together = _steps(edits.move_edge(moved, state, 0, "end", 44_000, GRID, together=True))
        self.assertEqual((together[0].end_unix_ms, together[1].start_unix_ms), (44_000, 44_000))

    def test_together_only_matters_for_a_shared_boundary(self) -> None:
        free = _steps(edits.move_edge(_steps(), PlanEditsState(), 1, "end", 56_000, GRID, together=True))  # an idle stretch lies after step 2
        self.assertEqual((free[1].end_unix_ms, free[2].start_unix_ms), (56_000, 60_000))


class OtherOperationsTest(unittest.TestCase):
    def test_label_edit_strips_and_goes_back_to_measured_for_empty_or_equal_text(self) -> None:
        step = _steps()[0]
        state = edits.set_label(PlanEditsState(), step, "  Water  ")
        self.assertEqual(_steps(state)[0].label, "Water")
        self.assertEqual(edits.set_label(state, step, ""), PlanEditsState())
        self.assertEqual(edits.set_label(state, step, S1), PlanEditsState())
        self.assertEqual(edits.set_label(PlanEditsState(), step, S1), PlanEditsState())

    def test_colour_edit_normalises_clears_and_rejects_junk(self) -> None:
        step = _steps()[1]
        state = edits.set_color(PlanEditsState(), step, "#ABC")
        self.assertEqual(_steps(state)[1].color, "#aabbcc")
        self.assertEqual(edits.set_color(state, step, None), PlanEditsState())
        with self.assertRaises(ValueError):
            edits.set_color(PlanEditsState(), step, "red")

    def test_reset_step_and_reset_all(self) -> None:
        steps = _steps()
        state = edits.set_label(PlanEditsState(), steps[0], "A")
        state = edits.set_color(state, steps[1], "#112233")
        self.assertEqual([e.step for e in edits.reset_step(state, steps[0]).edits], [2])
        self.assertEqual(edits.reset_all(), PlanEditsState())

    def test_two_steps_with_the_same_note_are_edited_separately(self) -> None:
        steps = _steps()
        self.assertEqual(steps[0].label, steps[2].label)
        state = edits.set_label(PlanEditsState(), steps[2], "Water at the end")
        self.assertEqual([s.label for s in _steps(state)], [S1, S2, "Water at the end"])


if __name__ == "__main__":
    unittest.main()
