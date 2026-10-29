"""The pure half of adding, removing and unifying the plan's steps and of the evaluation pairs (`lspri_imaging_app.plan`, 2026-10-29). No Qt, no files.

Synthetic steps, cubes of 2 s (so a grid point every 2000 ms): step 1 from 10 s to 30 s, step 2 from 30 s to 50 s (joined to step 1), an idle stretch, step 3 from 60 s to 80 s.
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
    import numpy as np

    from lspr_core import ImagingPlan, ImagingPlanStep
    from lspr_imaging_app.plan import edits
    from lspr_imaging_app.plan.state import AddedStep, EvalPair, PlanEditsState, added_ref, measured_ref
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

S1, S2, S3 = "H2O_--_ch1F_ch2P", "10perEG_-_ch1F", "20perEG_-_ch2F"
GRID = np.array([*range(0, 80_001, 2_000), 81_000], dtype=np.float64)  # a cube every 2 s, and the end of the data (81 s)
MIN_LENGTH = 2_000  # one cube


def _plan() -> ImagingPlan:
    return ImagingPlan(steps=[
        ImagingPlanStep(step=1, label=S1, start_unix_ms=10_000, end_unix_ms=30_000),
        ImagingPlanStep(step=2, label=S2, start_unix_ms=30_000, end_unix_ms=50_000),  # joined to step 1
        ImagingPlanStep(step=3, label=S3, start_unix_ms=60_000, end_unix_ms=80_000),  # an idle stretch lies before it
    ])


def _steps(state: PlanEditsState = PlanEditsState()) -> tuple[edits.EffectiveStep, ...]:
    return edits.apply_edits(_plan(), state).steps


M1, M2, M3 = (measured_ref(1, 10_000, S1), measured_ref(2, 30_000, S2), measured_ref(3, 60_000, S3))


class OrderAndReferenceTest(unittest.TestCase):
    def test_a_step_knows_its_place_in_time_a_stable_reference_and_no_role_yet(self) -> None:
        steps = _steps()
        self.assertEqual([s.order for s in steps], [1, 2, 3])
        self.assertEqual([s.ref for s in steps], [M1, M2, M3])
        self.assertEqual([s.roles for s in steps], [(), (), ()])
        self.assertFalse(any(s.is_added for s in steps))

    def test_the_reference_survives_edits_to_the_step(self) -> None:
        state = edits.set_label(PlanEditsState(), _steps()[0], "Water")
        self.assertEqual(_steps(state)[0].ref, M1)


class RemoveTest(unittest.TestCase):
    def test_a_measured_step_is_hidden_and_the_others_renumber(self) -> None:
        state = edits.remove_steps(_steps(), PlanEditsState(), [1])
        steps = _steps(state)
        self.assertEqual([(s.order, s.ref) for s in steps], [(1, M1), (2, M3)])
        self.assertEqual(state.removed, (M2,))

    def test_restoring_brings_it_back_with_its_edits(self) -> None:
        state = edits.set_color(PlanEditsState(), _steps()[1], "#ff0000")
        state = edits.remove_steps(_steps(state), state, [1])
        self.assertEqual(len(_steps(state)), 2)
        back = _steps(edits.restore_removed(state))
        self.assertEqual([s.ref for s in back], [M1, M2, M3])
        self.assertEqual(back[1].color, "#ff0000")

    def test_a_removed_step_with_an_edit_gives_no_warning(self) -> None:
        state = edits.set_label(PlanEditsState(), _steps()[1], "Mine")
        state = edits.remove_steps(_steps(state), state, [1])
        self.assertEqual(edits.apply_edits(_plan(), state).warnings, ())

    def test_a_step_the_user_added_is_deleted_not_hidden(self) -> None:
        state, ref = edits.add_step(_steps(), PlanEditsState(), 2, "after", GRID, min_length_ms=MIN_LENGTH)
        steps = _steps(state)
        added = next(s for s in steps if s.ref == ref)
        state = edits.remove_steps(steps, state, [added.order - 1])
        self.assertEqual((state.added, state.removed), ((), ()))
        self.assertEqual(state.next_uid, 2)  # the uid is spent

    def test_nothing_to_remove_returns_the_same_state(self) -> None:
        state = PlanEditsState()
        self.assertIs(edits.remove_steps(_steps(), state, []), state)
        self.assertIs(edits.remove_steps(_steps(), state, [7, -1]), state)

    def test_removing_twice_stores_one_record(self) -> None:
        once = edits.remove_steps(_steps(), PlanEditsState(), [0])
        again = edits.remove_steps(_steps(), once, [0])
        self.assertEqual(again.removed, (M1,))

    def test_removed_steps_that_the_dataset_no_longer_has_are_reported(self) -> None:
        state = PlanEditsState(removed=(measured_ref(9, 1, "gone"),))
        applied = edits.apply_edits(_plan(), state)
        self.assertEqual(len(applied.steps), 3)
        self.assertTrue(any("removed step 9" in w for w in applied.warnings))


class UnifyTest(unittest.TestCase):
    def test_neighbours_become_one_step_from_the_first_start_to_the_last_end(self) -> None:
        state = edits.unify(_steps(), PlanEditsState(), [0, 1])
        steps = _steps(state)
        self.assertEqual([(s.order, s.start_unix_ms, s.end_unix_ms, s.label) for s in steps], [(1, 10_000, 50_000, S1), (2, 60_000, 80_000, S3)])
        self.assertEqual(state.removed, (M2,))

    def test_steps_with_an_idle_stretch_between_them_may_be_unified_and_the_gap_is_absorbed(self) -> None:
        steps = _steps()
        self.assertTrue(edits.can_unify(steps, [1, 2]))
        merged = _steps(edits.unify(steps, PlanEditsState(), [1, 2]))
        self.assertEqual((merged[1].start_unix_ms, merged[1].end_unix_ms), (30_000, 80_000))

    def test_all_three_become_one_and_keep_the_firsts_note_and_colour(self) -> None:
        state = edits.set_color(PlanEditsState(), _steps()[0], "#00ff00")
        state = edits.unify(_steps(state), state, [2, 0, 1])  # the order of the indices does not matter
        (only,) = _steps(state)
        self.assertEqual((only.start_unix_ms, only.end_unix_ms, only.label, only.color), (10_000, 80_000, S1, "#00ff00"))

    def test_steps_that_are_not_neighbours_are_not_unified(self) -> None:
        steps = _steps()
        state = PlanEditsState()
        self.assertFalse(edits.can_unify(steps, [0, 2]))
        self.assertIs(edits.unify(steps, state, [0, 2]), state)
        self.assertIs(edits.unify(steps, state, [1]), state)  # one step is nothing to unify
        self.assertIs(edits.unify(steps, state, []), state)

    def test_unifying_a_step_the_user_added_works_whether_it_is_the_first_or_the_last(self) -> None:
        state, ref = edits.add_step(_steps(), PlanEditsState(), 1, "after", GRID, min_length_ms=MIN_LENGTH)  # 50 s - 60 s, between step 2 and step 3
        steps = _steps(state)
        self.assertEqual(steps[2].ref, ref)
        first = _steps(edits.unify(steps, state, [2, 3]))  # the added step is the first: it grows to the end of step 3, which is removed
        self.assertEqual((first[2].ref, first[2].start_unix_ms, first[2].end_unix_ms, len(first)), (ref, 50_000, 80_000, 3))
        last = edits.unify(steps, state, [1, 2])  # the added step is the last: step 2 grows to 60 s, the added step is deleted
        self.assertEqual((_steps(last)[1].ref, _steps(last)[1].end_unix_ms, last.added), (M2, 60_000, ()))

    def test_the_roles_of_steps_unified_away_are_cleared_and_the_firsts_stay(self) -> None:
        state = edits.set_role(PlanEditsState(), 1, "baseline", M1)
        state = edits.set_role(state, 1, "signal", M2)
        after = edits.unify(_steps(state), state, [0, 1])
        self.assertEqual(after.pair(1), EvalPair(1, baseline=M1, signal=None))
        self.assertEqual(edits.roles_lost(state, after), ["signal of pair 1"])


class AddTest(unittest.TestCase):
    def test_free_time_behind_a_step_is_filled(self) -> None:
        state, ref = edits.add_step(_steps(), PlanEditsState(), 1, "after", GRID, min_length_ms=MIN_LENGTH)  # 50 s - 60 s lies free
        new = next(s for s in _steps(state) if s.ref == ref)
        self.assertEqual((new.order, new.start_unix_ms, new.end_unix_ms, new.label, new.color, new.is_added, new.step), (3, 50_000, 60_000, "", None, True, 0))
        self.assertEqual([s.order for s in _steps(state)], [1, 2, 3, 4])

    def test_free_time_in_front_of_a_step_is_filled(self) -> None:
        state, ref = edits.add_step(_steps(), PlanEditsState(), 2, "before", GRID, min_length_ms=MIN_LENGTH)
        new = next(s for s in _steps(state) if s.ref == ref)
        self.assertEqual((new.order, new.start_unix_ms, new.end_unix_ms), (3, 50_000, 60_000))

    def test_free_time_before_the_first_step_runs_to_the_start_of_the_data(self) -> None:
        state, ref = edits.add_step(_steps(), PlanEditsState(), 0, "before", GRID, min_length_ms=MIN_LENGTH)
        new = next(s for s in _steps(state) if s.ref == ref)
        self.assertEqual((new.order, new.start_unix_ms, new.end_unix_ms), (1, 0, 10_000))

    def test_free_time_behind_the_last_step_runs_to_the_end_of_the_data(self) -> None:
        plan = ImagingPlan(steps=[ImagingPlanStep(step=1, label="a", start_unix_ms=10_000, end_unix_ms=60_000)])
        steps = edits.apply_edits(plan, PlanEditsState()).steps
        state, ref = edits.add_step(steps, PlanEditsState(), 0, "after", GRID, min_length_ms=MIN_LENGTH)  # 60 s - 81 s (end of the data) is free
        new = next(s for s in edits.apply_edits(plan, state).steps if s.ref == ref)
        self.assertEqual((new.start_unix_ms, new.end_unix_ms), (60_000, 81_000))

    def test_less_than_a_cube_of_free_time_is_not_a_step_so_the_step_gives_up_half(self) -> None:
        state, ref = edits.add_step(_steps(), PlanEditsState(), 2, "after", GRID, min_length_ms=MIN_LENGTH)  # only 80 s - 81 s is left behind step 3
        steps = _steps(state)
        new = next(s for s in steps if s.ref == ref)
        self.assertEqual((steps[2].start_unix_ms, steps[2].end_unix_ms, new.start_unix_ms, new.end_unix_ms), (60_000, 70_000, 70_000, 80_000))

    def test_joined_steps_leave_no_room_so_half_of_the_step_is_cut_off_on_a_cube_boundary(self) -> None:
        state, ref = edits.add_step(_steps(), PlanEditsState(), 0, "after", GRID, min_length_ms=MIN_LENGTH)  # step 1 (10 - 30 s) touches step 2
        steps = _steps(state)
        new = next(s for s in steps if s.ref == ref)
        self.assertEqual((steps[0].start_unix_ms, steps[0].end_unix_ms), (10_000, 20_000))  # the step keeps the first half
        self.assertEqual((new.start_unix_ms, new.end_unix_ms), (20_000, 30_000))  # the new one took the second half
        self.assertEqual([s.order for s in steps], [1, 2, 3, 4])
        self.assertEqual(steps[0].ref, M1)

    def test_in_front_of_a_joined_step_the_front_half_is_cut_off(self) -> None:
        state, ref = edits.add_step(_steps(), PlanEditsState(), 1, "before", GRID, min_length_ms=MIN_LENGTH)  # step 2 (30 - 50 s) touches step 1
        steps = _steps(state)
        new = next(s for s in steps if s.ref == ref)
        self.assertEqual((new.start_unix_ms, new.end_unix_ms), (30_000, 40_000))
        self.assertEqual((steps[2].start_unix_ms, steps[2].end_unix_ms), (40_000, 50_000))

    def test_a_step_too_short_to_share_gets_no_neighbour(self) -> None:
        plan = ImagingPlan(steps=[
            ImagingPlanStep(step=1, label="a", start_unix_ms=10_000, end_unix_ms=12_000),
            ImagingPlanStep(step=2, label="b", start_unix_ms=12_000, end_unix_ms=20_000),
        ])
        steps = edits.apply_edits(plan, PlanEditsState()).steps
        state = PlanEditsState()
        self.assertEqual(edits.add_step(steps, state, 0, "after", GRID, min_length_ms=MIN_LENGTH), (state, None))  # one cube cannot be split into two steps

    def test_uids_are_given_once_and_never_reused(self) -> None:
        state, first = edits.add_step(_steps(), PlanEditsState(), 1, "after", GRID, min_length_ms=MIN_LENGTH)
        state = edits.remove_steps(_steps(state), state, [2])
        state, second = edits.add_step(_steps(state), state, 1, "after", GRID, min_length_ms=MIN_LENGTH)
        self.assertEqual((first, second), (added_ref(1), added_ref(2)))

    def test_adding_next_to_a_step_the_user_added_shortens_that_step_not_a_copy(self) -> None:
        state, first = edits.add_step(_steps(), PlanEditsState(), 1, "after", GRID, min_length_ms=MIN_LENGTH)  # 50 - 60 s
        steps = _steps(state)
        state, second = edits.add_step(steps, state, 2, "after", GRID, min_length_ms=MIN_LENGTH)  # joined to step 3 (60 - 80 s): cut from the added one
        by_ref = {s.ref: s for s in _steps(state)}
        self.assertEqual((by_ref[first].start_unix_ms, by_ref[first].end_unix_ms), (50_000, 54_000))  # 55 s is halfway: the earlier of the two nearest grid points
        self.assertEqual((by_ref[second].start_unix_ms, by_ref[second].end_unix_ms), (54_000, 60_000))
        self.assertEqual(len(state.added), 2)


class EditingAnAddedStepTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state, self.ref = edits.add_step(_steps(), PlanEditsState(), 1, "after", GRID, min_length_ms=MIN_LENGTH)  # 50 - 60 s
        self.new = next(s for s in _steps(self.state) if s.ref == self.ref)

    def _again(self, state: PlanEditsState) -> edits.EffectiveStep:
        return next(s for s in _steps(state) if s.ref == self.ref)

    def test_note_colour_and_boundaries_change_the_record_itself(self) -> None:
        state = edits.set_label(self.state, self.new, "  Signal  ")
        state = edits.set_color(state, self._again(state), "#abc")
        state = edits.move_edge(_steps(state), state, self._again(state).order - 1, "end", 56_000, GRID, min_length_ms=MIN_LENGTH)
        found = self._again(state)
        self.assertEqual((found.label, found.color, found.start_unix_ms, found.end_unix_ms), ("Signal", "#aabbcc", 50_000, 56_000))
        self.assertEqual(state.edits, ())  # no overlay record: it has nothing measured to overlay

    def test_clearing_the_note_gives_an_empty_one(self) -> None:
        state = edits.set_label(self.state, self.new, "x")
        state = edits.set_label(state, self._again(state), "")
        self.assertEqual(self._again(state).label, "")

    def test_reset_leaves_an_added_step_alone_and_reset_all_removes_it(self) -> None:
        self.assertIs(edits.reset_step(self.state, self.new), self.state)
        self.assertEqual(edits.reset_all(self.state).added, ())
        self.assertEqual(edits.reset_all(self.state).next_uid, 2)  # the counter stays: an id is not given twice


class RolesTest(unittest.TestCase):
    def test_a_step_carries_the_label_of_each_role_it_plays(self) -> None:
        state = edits.set_role(PlanEditsState(), 1, "baseline", M1)
        state = edits.set_role(state, 1, "signal", M3)
        state = edits.set_role(state, 2, "baseline", M3)
        self.assertEqual([s.roles for s in _steps(state)], [("B1",), (), ("S1", "B2")])

    def test_a_step_is_not_both_roles_of_one_pair_but_may_play_one_in_another(self) -> None:
        state = edits.set_role(PlanEditsState(), 1, "baseline", M1)
        state = edits.set_role(state, 1, "signal", M1)  # the signal takes it: the baseline is cleared
        self.assertEqual(state.pair(1), EvalPair(1, baseline=None, signal=M1))
        state = edits.set_role(state, 2, "baseline", M1)
        self.assertEqual(state.pair(2), EvalPair(2, baseline=M1))

    def test_clearing_both_roles_drops_the_pair_and_no_change_returns_the_same_state(self) -> None:
        state = edits.set_role(PlanEditsState(), 1, "baseline", M1)
        self.assertIs(edits.set_role(state, 1, "baseline", M1), state)
        self.assertEqual(edits.set_role(state, 1, "baseline", None).pairs, ())
        empty = PlanEditsState()
        self.assertIs(edits.set_role(empty, 3, "signal", None), empty)  # clearing a role that was never set changes nothing

    def test_clearing_a_pair_empties_both_roles_and_leaves_the_other_pairs(self) -> None:
        state = edits.set_role(PlanEditsState(), 1, "baseline", M1)
        state = edits.set_role(state, 2, "baseline", M1)
        state = edits.set_role(state, 2, "signal", M2)
        after = edits.clear_pair(state, 2)
        self.assertEqual(after.pairs, (EvalPair(1, baseline=M1),))
        self.assertIs(edits.clear_pair(after, 2), after)  # a pair with nothing set: nothing changes

    def test_pairs_are_kept_in_order_and_start_at_one(self) -> None:
        state = edits.set_role(PlanEditsState(), 2, "signal", M2)
        state = edits.set_role(state, 1, "baseline", M1)
        self.assertEqual([p.number for p in state.pairs], [1, 2])
        with self.assertRaises(ValueError):
            edits.set_role(state, 0, "baseline", M1)

    def test_removing_a_step_clears_its_roles_and_says_which(self) -> None:
        state = edits.set_role(PlanEditsState(), 1, "baseline", M1)
        state = edits.set_role(state, 1, "signal", M2)
        after = edits.remove_steps(_steps(state), state, [1])
        self.assertEqual(after.pair(1), EvalPair(1, baseline=M1))
        self.assertEqual(edits.roles_lost(state, after), ["signal of pair 1"])
        gone = edits.remove_steps(_steps(after), after, [0])
        self.assertEqual(gone.pairs, ())

    def test_a_reference_to_a_step_the_dataset_does_not_have_marks_no_step(self) -> None:
        state = edits.set_role(PlanEditsState(), 1, "baseline", measured_ref(9, 1, "gone"))
        self.assertEqual([s.roles for s in _steps(state)], [(), (), ()])

    def test_reset_all_keeps_the_roles_of_measured_steps_and_drops_those_of_added_ones(self) -> None:
        state, ref = edits.add_step(_steps(), PlanEditsState(), 1, "after", GRID, min_length_ms=MIN_LENGTH)
        state = edits.set_role(state, 1, "baseline", M1)
        state = edits.set_role(state, 1, "signal", ref)
        self.assertEqual(edits.reset_all(state).pair(1), EvalPair(1, baseline=M1))


class PayloadTest(unittest.TestCase):
    def _state(self) -> PlanEditsState:
        state = edits.set_label(PlanEditsState(), _steps()[0], "Water")
        state, ref = edits.add_step(_steps(state), state, 1, "after", GRID, min_length_ms=MIN_LENGTH)
        state = edits.remove_steps(_steps(state), state, [2])  # the step added above is order 3
        state, ref2 = edits.add_step(_steps(state), state, 1, "after", GRID, min_length_ms=MIN_LENGTH)
        state = edits.remove_steps(_steps(state), state, [1])  # step 2 hidden (it stays in `removed`)
        state = edits.set_role(state, 1, "baseline", M1)
        return edits.set_role(state, 1, "signal", ref2)

    def test_the_payload_round_trips(self) -> None:
        state = self._state()
        self.assertEqual(PlanEditsState.from_payload(state.to_payload()), state)
        self.assertEqual(state.next_uid, 3)  # two steps were added; the first is deleted, its uid stays spent

    def test_a_state_with_only_edits_is_written_as_in_schema_1_5(self) -> None:
        state = edits.set_label(PlanEditsState(), _steps()[0], "Water")
        self.assertEqual(set(state.to_payload()), {"edits"})
        self.assertEqual(PlanEditsState().to_payload(), {"edits": []})

    def test_a_block_from_an_older_session_loads_with_no_structure(self) -> None:
        raw = {"edits": [{"step": 1, "measured_start_unix_ms": 10_000, "measured_label": S1, "label": "Water"}]}
        loaded = PlanEditsState.from_payload(raw)
        self.assertEqual((len(loaded.edits), loaded.removed, loaded.added, loaded.pairs, loaded.next_uid), (1, (), (), (), 1))

    def test_records_that_cannot_be_read_are_skipped_and_the_rest_kept(self) -> None:
        raw = self._state().to_payload()
        raw["removed"] = ["junk", {"added": 4}, *raw["removed"]]  # an added step cannot be "removed": it is deleted instead
        raw["added"] = ["junk", {"uid": 0, "start_unix_ms": 1, "end_unix_ms": 2}, {"uid": 7, "start_unix_ms": 5, "end_unix_ms": 5}, *raw["added"]]
        raw["pairs"] = ["junk", {"number": 0, "baseline": {"added": 1}}, {"number": 3}, *raw["pairs"]]
        self.assertEqual(PlanEditsState.from_payload(raw), self._state())

    def test_a_damaged_counter_cannot_give_out_a_taken_uid(self) -> None:
        raw = self._state().to_payload()
        for broken in (0, -3, "x", None, 1):
            raw["next_uid"] = broken
            self.assertEqual(PlanEditsState.from_payload(raw).next_uid, 3, msg=repr(broken))  # one past the highest uid present (2)

    def test_a_role_with_an_unreadable_step_loses_only_that_role(self) -> None:
        raw = {"edits": [], "pairs": [{"number": 1, "baseline": {"step": "x"}, "signal": {"added": 2}}]}
        self.assertEqual(PlanEditsState.from_payload(raw).pairs, (EvalPair(1, signal=added_ref(2)),))

    def test_the_added_step_record_keeps_a_tidy_note(self) -> None:
        step = AddedStep.from_payload({"uid": 1, "start_unix_ms": 1, "end_unix_ms": 2, "label": "  a \n b ", "color": "#ABC"})
        self.assertEqual((step.label, step.color), ("a b", "#aabbcc"))


if __name__ == "__main__":
    unittest.main()
