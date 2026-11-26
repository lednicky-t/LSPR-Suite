"""The batch edits of the plan steps (`lspri_imaging_app.plan.batch_edit`, 2026-11-26): shrink many steps, or move many steps in time. Pure, no Qt, no files.

Synthetic steps as in `test_lspri_plan.py`: 10 - 30 s and 30 - 50 s (joined), 60 - 80 s; a cube every 2 s (0 ... 80 s) and the end of the data at 81 s. The rules under test
(maintainer, 2026-11-26): "both" splits the amount; a percentage is of the step's own length and splits the same way; a step that cannot shrink that much is left as it is (never cut
down to what fits); an offset may overlap neighbours but never leaves the data.
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
    from lspr_imaging_app.plan import batch_edit as be
    from lspr_imaging_app.plan import edits
    from lspr_imaging_app.plan.state import PlanEditsState
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

GRID = np.array([*range(0, 80_001, 2_000), 81_000], dtype=np.float64)
CUBE = 2_000


def _plan(*spans: tuple[int, int]) -> ImagingPlan:
    return ImagingPlan(steps=[ImagingPlanStep(step=i + 1, label=f"S{i + 1}", start_unix_ms=a, end_unix_ms=b) for i, (a, b) in enumerate(spans)])


def _steps(state: PlanEditsState = PlanEditsState(), *spans: tuple[int, int]):
    return edits.apply_edits(_plan(*(spans or ((10_000, 30_000), (30_000, 50_000), (60_000, 80_000)))), state).steps


def _run(spec: be.BatchSpec, indices=(0, 1, 2), spans=()):
    steps = _steps(PlanEditsState(), *spans)
    return steps, be.plan_batch(steps, indices, spec, GRID, CUBE)


def _bounds(plan: be.BatchPlan) -> dict[int, tuple[int, int]]:
    return {c.index: (c.start_unix_ms, c.end_unix_ms) for c in plan.changes}


def _shrink(unit: str, amount: float, side: str = be.SIDE_BOTH) -> be.BatchSpec:
    return be.BatchSpec(be.KIND_SHRINK, unit, amount, side)


def _offset(unit: str, amount: float) -> be.BatchSpec:
    return be.BatchSpec(be.KIND_OFFSET, unit, amount)


class ShrinkTest(unittest.TestCase):
    def test_cubes_from_the_start_drop_whole_cubes(self) -> None:
        _steps_, plan = _run(_shrink(be.UNIT_CUBES, 2, be.SIDE_START))
        self.assertEqual(_bounds(plan), {0: (14_000, 30_000), 1: (34_000, 50_000), 2: (64_000, 80_000)})
        self.assertEqual((plan.refused, plan.unmoved), (0, 0))

    def test_cubes_from_the_end_drop_whole_cubes(self) -> None:
        _steps_, plan = _run(_shrink(be.UNIT_CUBES, 1, be.SIDE_END))
        self.assertEqual(_bounds(plan), {0: (10_000, 28_000), 1: (30_000, 48_000), 2: (60_000, 78_000)})

    def test_both_splits_the_amount(self) -> None:
        _steps_, plan = _run(_shrink(be.UNIT_CUBES, 4))
        self.assertEqual(_bounds(plan), {0: (14_000, 26_000), 1: (34_000, 46_000), 2: (64_000, 76_000)})  # two cubes from each side

    def test_both_with_an_odd_number_of_cubes_takes_the_extra_cube_from_the_start(self) -> None:
        _steps_, plan = _run(_shrink(be.UNIT_CUBES, 3))
        self.assertEqual(_bounds(plan)[0], (14_000, 28_000))

    def test_seconds_split_the_same_way(self) -> None:
        _steps_, by_time = _run(_shrink(be.UNIT_TIME, 8))
        _steps_, by_cubes = _run(_shrink(be.UNIT_CUBES, 4))
        self.assertEqual(_bounds(by_time), _bounds(by_cubes))

    def test_percent_is_of_the_steps_own_length_and_splits(self) -> None:
        # a 20 s step loses 20 % = 4 s: 2 s from each side
        _steps_, plan = _run(_shrink(be.UNIT_PERCENT, 20))
        self.assertEqual(_bounds(plan), {0: (12_000, 28_000), 1: (32_000, 48_000), 2: (62_000, 78_000)})

    def test_percent_follows_each_steps_own_length(self) -> None:
        _steps_, plan = _run(_shrink(be.UNIT_PERCENT, 20, be.SIDE_START), spans=((10_000, 30_000), (40_000, 80_000)), indices=(0, 1))
        self.assertEqual(_bounds(plan), {0: (14_000, 30_000), 1: (48_000, 80_000)})  # 4 s of the first step, 8 s of the 40 s step

    def test_an_amount_under_half_a_cube_moves_nothing_and_is_reported(self) -> None:
        _steps_, plan = _run(_shrink(be.UNIT_TIME, 1))
        self.assertEqual((_bounds(plan), plan.unmoved, plan.refused), ({}, 3, 0))

    def test_a_step_too_short_for_the_amount_is_left_unchanged_while_the_others_shrink(self) -> None:
        # the third step is two cubes long: losing 2 cubes (one per side) would leave nothing
        _steps_, plan = _run(_shrink(be.UNIT_CUBES, 2), spans=((10_000, 30_000), (30_000, 50_000), (60_000, 64_000)))
        self.assertEqual(sorted(_bounds(plan)), [0, 1])
        self.assertEqual(plan.refused, 1)

    def test_a_step_always_keeps_at_least_one_cube(self) -> None:
        _steps_, kept = _run(_shrink(be.UNIT_CUBES, 1, be.SIDE_START), spans=((10_000, 14_000),), indices=(0,))
        self.assertEqual(_bounds(kept), {0: (12_000, 14_000)})
        _steps_, gone = _run(_shrink(be.UNIT_CUBES, 2, be.SIDE_START), spans=((10_000, 14_000),), indices=(0,))
        self.assertEqual((_bounds(gone), gone.refused), ({}, 1))

    def test_a_huge_amount_is_refused_not_clamped(self) -> None:
        for spec in (_shrink(be.UNIT_CUBES, 500), _shrink(be.UNIT_TIME, 500), _shrink(be.UNIT_PERCENT, 100)):
            _steps_, plan = _run(spec)
            self.assertEqual((_bounds(plan), plan.refused), ({}, 3), spec)

    def test_an_edge_that_is_between_cubes_lands_on_the_cube_inside_the_step(self) -> None:
        # measured edges need not be on a cube start: the first whole cube starts at the first cube start inside the step
        _steps_, plan = _run(_shrink(be.UNIT_CUBES, 1, be.SIDE_START), spans=((10_500, 30_500),), indices=(0,))
        self.assertEqual(_bounds(plan), {0: (14_000, 30_500)})  # first whole cube starts at 12 s; one cube less starts at 14 s

    def test_only_the_chosen_steps_change(self) -> None:
        _steps_, plan = _run(_shrink(be.UNIT_CUBES, 1, be.SIDE_START), indices=(1,))
        self.assertEqual(sorted(_bounds(plan)), [1])
        self.assertEqual(plan.asked, 1)

    def test_steps_that_do_not_exist_are_ignored(self) -> None:
        _steps_, plan = _run(_shrink(be.UNIT_CUBES, 1, be.SIDE_START), indices=(1, 7, -1, 1))
        self.assertEqual((sorted(_bounds(plan)), plan.asked), ([1], 1))

    def test_without_a_grid_nothing_can_change(self) -> None:
        steps = _steps()
        plan = be.plan_batch(steps, (0, 1, 2), _shrink(be.UNIT_CUBES, 1), np.array([]), CUBE)
        self.assertEqual((plan.changes, plan.refused), ((), 3))


class OffsetTest(unittest.TestCase):
    def test_cubes_move_every_edge_by_the_same_number_of_cubes(self) -> None:
        _steps_, plan = _run(_offset(be.UNIT_CUBES, 2), indices=(0, 1))
        self.assertEqual(_bounds(plan), {0: (14_000, 34_000), 1: (34_000, 54_000)})

    def test_a_negative_offset_moves_earlier(self) -> None:
        _steps_, plan = _run(_offset(be.UNIT_TIME, -4))
        self.assertEqual(_bounds(plan), {0: (6_000, 26_000), 1: (26_000, 46_000), 2: (56_000, 76_000)})

    def test_length_is_kept(self) -> None:
        steps, plan = _run(_offset(be.UNIT_CUBES, 3))
        for change in plan.changes:
            self.assertEqual(change.end_unix_ms - change.start_unix_ms, steps[change.index].end_unix_ms - steps[change.index].start_unix_ms)

    def test_steps_may_overlap_their_neighbours(self) -> None:
        steps, plan = _run(_offset(be.UNIT_TIME, 10), indices=(0,))
        self.assertEqual(_bounds(plan), {0: (20_000, 40_000)})  # step 2 starts at 30 s: they overlap now
        applied = edits.apply_edits(_plan((10_000, 30_000), (30_000, 50_000), (60_000, 80_000)), be.apply_batch(steps, PlanEditsState(), plan))
        self.assertEqual(applied.warnings, ())
        self.assertEqual([(s.start_unix_ms, s.end_unix_ms) for s in applied.steps][:2], [(20_000, 40_000), (30_000, 50_000)])

    def test_a_step_that_would_leave_the_data_stays_while_the_others_move(self) -> None:
        # the last step ends at 80 s; two cubes later is past the end of the data (81 s), one cube later is the end of the data itself
        _steps_, two = _run(_offset(be.UNIT_CUBES, 2))
        self.assertEqual((sorted(_bounds(two)), two.refused), ([0, 1], 1))
        _steps_, one = _run(_offset(be.UNIT_CUBES, 1))
        self.assertEqual(_bounds(one)[2], (62_000, 81_000))

    def test_before_the_first_cube_is_leaving_the_data(self) -> None:
        _steps_, plan = _run(_offset(be.UNIT_TIME, -12))
        self.assertEqual((sorted(_bounds(plan)), plan.refused), ([1, 2], 1))  # the first step would start at -2 s

    def test_an_offset_of_zero_moves_nothing(self) -> None:
        _steps_, plan = _run(_offset(be.UNIT_CUBES, 0))
        self.assertEqual((plan.changes, plan.unmoved), ((), 3))

    def test_a_time_offset_under_half_a_cube_moves_nothing(self) -> None:
        _steps_, plan = _run(_offset(be.UNIT_TIME, 0.5))
        self.assertEqual((plan.changes, plan.unmoved), ((), 3))


class ApplyTest(unittest.TestCase):
    def test_apply_writes_the_edges_into_the_plan_edits_and_leaves_the_rest_alone(self) -> None:
        steps, plan = _run(_shrink(be.UNIT_CUBES, 2))
        state = be.apply_batch(steps, PlanEditsState(), plan)
        after = edits.apply_edits(_plan((10_000, 30_000), (30_000, 50_000), (60_000, 80_000)), state).steps
        self.assertEqual([(s.start_unix_ms, s.end_unix_ms) for s in after], [(12_000, 28_000), (32_000, 48_000), (62_000, 78_000)])
        self.assertEqual([s.label for s in after], ["S1", "S2", "S3"])
        self.assertEqual([(s.measured_start_unix_ms, s.measured_end_unix_ms) for s in after], [(10_000, 30_000), (30_000, 50_000), (60_000, 80_000)])  # measured steps untouched
        self.assertTrue(all(s.edited for s in after))

    def test_moving_a_step_back_to_where_it_was_stores_no_edit(self) -> None:
        steps, plan = _run(_offset(be.UNIT_CUBES, 1), indices=(0, 1))
        moved = be.apply_batch(steps, PlanEditsState(), plan)
        steps_again = edits.apply_edits(_plan((10_000, 30_000), (30_000, 50_000), (60_000, 80_000)), moved).steps
        back = be.plan_batch(steps_again, (0, 1), _offset(be.UNIT_CUBES, -1), GRID, CUBE)
        self.assertEqual(be.apply_batch(steps_again, moved, back), PlanEditsState())

    def test_a_step_the_user_added_can_be_shrunk_too(self) -> None:
        state, ref = edits.add_step_at(_steps(), PlanEditsState(), 54_000, GRID, min_length_ms=CUBE)
        self.assertIsNotNone(ref)
        steps = edits.apply_edits(_plan((10_000, 30_000), (30_000, 50_000), (60_000, 80_000)), state).steps
        added = next(i for i, s in enumerate(steps) if s.is_added)
        plan = be.plan_batch(steps, (added,), _shrink(be.UNIT_CUBES, 1, be.SIDE_START), GRID, CUBE)
        self.assertEqual(len(plan.changes), 1)
        after = edits.apply_edits(_plan((10_000, 30_000), (30_000, 50_000), (60_000, 80_000)), be.apply_batch(steps, state, plan)).steps
        self.assertEqual(after[added].start_unix_ms, steps[added].start_unix_ms + CUBE)


class StatusTextTest(unittest.TestCase):
    def test_before_and_after_apply(self) -> None:
        spec = _shrink(be.UNIT_CUBES, 2)
        plan = be.BatchPlan((be.EdgeChange(0, 1, 2), be.EdgeChange(1, 1, 2)), asked=4, refused=2)
        self.assertEqual(be.status_text(plan, spec), "2 steps will shrink; 2 steps too short to lose that much, left unchanged.")
        self.assertEqual(be.status_text(plan, spec, done=True), "2 steps shrunk; 2 steps too short to lose that much, left unchanged.")

    def test_an_offset_names_the_data_edge(self) -> None:
        plan = be.BatchPlan((be.EdgeChange(0, 1, 2),), asked=2, refused=1)
        self.assertEqual(be.status_text(plan, _offset(be.UNIT_CUBES, 1)), "1 step will move; 1 step would leave the data, left unchanged.")

    def test_nothing_to_do(self) -> None:
        self.assertEqual(be.status_text(be.BatchPlan((), asked=3, unmoved=3), _offset(be.UNIT_TIME, 0.5)),
                         "No step would move; 3 steps would not move (under half a cube), left unchanged.")
        self.assertEqual(be.status_text(be.BatchPlan((), asked=0), _offset(be.UNIT_TIME, 1)), "There are no steps to edit.")


if __name__ == "__main__":
    unittest.main()
