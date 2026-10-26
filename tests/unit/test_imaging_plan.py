"""Experiment steps from the measured notes, the join with a plan file, and cube classification
(`lspr_core.imaging_plan`). Small synthetic data; the numbers mirror the 2026-10 bulk-sensitivity example
(a mid-cube step change, idle stretches before and after the steps)."""

from __future__ import annotations

import unittest

from tests._paths import ensure_repo_paths

ensure_repo_paths()

from lspr_core import (  # noqa: E402
    ExperimentPlan,
    ExperimentPlanStep,
    ImagingAcquisitionMetadata,
    ImagingCommentEvent,
    ImagingCubeTiming,
    ImagingPlan,
    ImagingPlanStep,
    StepLookup,
    build_imaging_plan,
    classify_cube,
    classify_cubes,
    end_of_data_unix_ms,
    observed_steps,
)

IDLE = "Pump is not running"


def _events(*pairs: tuple[int, str]) -> list[ImagingCommentEvent]:
    return [ImagingCommentEvent(acquired_at_unix_ms=t, comment=c) for t, c in pairs]


def _planned(*steps: tuple[float, str]) -> ExperimentPlan:
    """(duration_s, note) per step, one after the other."""
    out: list[ExperimentPlanStep] = []
    cursor = 0.0
    for number, (duration_s, note) in enumerate(steps, start=1):
        out.append(
            ExperimentPlanStep(
                id=number,
                label=f"Step {number}",
                start_s=cursor,
                end_s=cursor + duration_s,
                comment=note,
                devices={"channels": [{"channel": 1, "flow_ul_min": 40.0, "direction": "CCW"}], "valve": "Close"},
            )
        )
        cursor += duration_s
    return ExperimentPlan(steps=out)


class ObservedStepsTests(unittest.TestCase):
    def test_idle_notes_are_not_steps_and_end_the_step_before_them(self) -> None:
        steps = observed_steps(_events((100, IDLE), (1000, "A"), (2300, "B"), (3300, IDLE)), data_end_unix_ms=3301)
        self.assertEqual([(s.label, s.start_unix_ms, s.end_unix_ms) for s in steps], [("A", 1000, 2300), ("B", 2300, 3300)])

    def test_last_step_runs_to_the_end_of_the_data(self) -> None:
        steps = observed_steps(_events((100, IDLE), (1000, "A")), data_end_unix_ms=5001)
        self.assertEqual((steps[0].start_unix_ms, steps[0].end_unix_ms), (1000, 5001))

    def test_last_step_without_data_end_has_zero_length(self) -> None:
        steps = observed_steps(_events((1000, "A")))
        self.assertEqual((steps[0].start_unix_ms, steps[0].end_unix_ms), (1000, 1000))

    def test_blank_and_case_differing_idle_notes_are_idle(self) -> None:
        steps = observed_steps(_events((0, ""), (10, "pump is not running  "), (20, "A")), data_end_unix_ms=30)
        self.assertEqual([s.label for s in steps], ["A"])

    def test_events_are_taken_in_time_order(self) -> None:
        steps = observed_steps(_events((2000, "B"), (1000, "A")), data_end_unix_ms=3000)
        self.assertEqual([s.label for s in steps], ["A", "B"])

    def test_end_of_data_is_just_after_the_last_image(self) -> None:
        timings = [ImagingCubeTiming(spectral_cube_index=0, wavelength_nm=470.0, acquired_at_unix_ms=t) for t in (5, 9, 7)]
        self.assertEqual(end_of_data_unix_ms(timings), 10)
        self.assertIsNone(end_of_data_unix_ms([]))


class BuildImagingPlanTests(unittest.TestCase):
    EVENTS = ((100, IDLE), (1000, "A_--_ch1F_ch2P"), (2300, "B_--_ch2F_ch3P"), (3300, IDLE))

    def test_notes_only_plan_has_steps_without_settings(self) -> None:
        plan = build_imaging_plan(_events(*self.EVENTS), data_end_unix_ms=3301)
        assert plan is not None
        self.assertEqual([s.step for s in plan.steps], [1, 2])
        self.assertEqual(plan.steps[0].label, "A_--_ch1F_ch2P")
        self.assertEqual(plan.steps[0].devices, {})
        self.assertIsNone(plan.steps[0].planned_duration_s)
        self.assertIsNone(plan.source_file)
        self.assertEqual(plan.warnings, [])

    def test_no_steps_and_no_plan_file_gives_none(self) -> None:
        self.assertIsNone(build_imaging_plan(_events((0, IDLE)), data_end_unix_ms=10))
        self.assertIsNone(build_imaging_plan([], data_end_unix_ms=10))

    def test_matching_plan_attaches_settings_and_keeps_measured_times(self) -> None:
        planned = _planned((1.0, "A_--_ch1F_ch2P"), (1.0, "B_--_ch2F_ch3P"))
        plan = build_imaging_plan(_events(*self.EVENTS), data_end_unix_ms=3301, planned=planned, source_file="run.csv")
        assert plan is not None
        self.assertEqual(plan.source_file, "run.csv")
        self.assertEqual(plan.warnings, [])
        first = plan.steps[0]
        self.assertEqual((first.start_unix_ms, first.end_unix_ms), (1000, 2300))  # measured, not planned
        self.assertEqual(first.planned_duration_s, 1.0)
        self.assertEqual(first.devices["valve"], "Close")

    def test_step_count_mismatch_refuses_the_join_and_says_so(self) -> None:
        planned = _planned((1.0, "A_--_ch1F_ch2P"), (1.0, "B_--_ch2F_ch3P"), (1.0, "C"))
        plan = build_imaging_plan(_events(*self.EVENTS), data_end_unix_ms=3301, planned=planned, source_file="run.csv")
        assert plan is not None
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[0].devices, {})
        self.assertIsNone(plan.source_file)
        self.assertEqual(len(plan.warnings), 1)
        self.assertIn("3 step(s)", plan.warnings[0])
        self.assertIn("show 2", plan.warnings[0])

    def test_duration_far_from_the_plan_is_reported_small_differences_are_not(self) -> None:
        planned = _planned((1.0, "A_--_ch1F_ch2P"), (10.0, "B_--_ch2F_ch3P"))  # step 2 measured 1.0 s, planned 10 s
        plan = build_imaging_plan(_events(*self.EVENTS), data_end_unix_ms=3301, planned=planned, source_file="run.csv")
        assert plan is not None
        self.assertEqual(len(plan.warnings), 1)
        self.assertIn("Step 2", plan.warnings[0])

    def test_label_that_differs_from_the_plan_is_reported_and_the_recorded_note_kept(self) -> None:
        planned = _planned((1.0, "A_--_ch1F_ch2P"), (1.0, "B_--_ch2F_ch3p"))
        plan = build_imaging_plan(_events(*self.EVENTS), data_end_unix_ms=3301, planned=planned, source_file="run.csv")
        assert plan is not None
        self.assertEqual(plan.steps[1].label, "B_--_ch2F_ch3P")
        self.assertEqual(len(plan.warnings), 1)
        self.assertIn("Step 2", plan.warnings[0])

    def test_plan_file_without_step_changes_in_the_notes_gives_an_empty_plan_with_a_warning(self) -> None:
        plan = build_imaging_plan(_events((0, IDLE)), data_end_unix_ms=10, planned=_planned((1.0, "A")), source_file="run.csv")
        assert plan is not None
        self.assertEqual(plan.steps, [])
        self.assertIn("could not be placed in time", plan.warnings[0])

    def test_reader_warnings_are_carried_along(self) -> None:
        plan = build_imaging_plan(
            _events(*self.EVENTS), data_end_unix_ms=3301, planned=_planned((1.0, "A_--_ch1F_ch2P"), (1.0, "B_--_ch2F_ch3P")),
            source_file="run.csv", read_warnings=["Step 3 (line 4): something"],
        )
        assert plan is not None
        self.assertEqual(plan.warnings, ["Step 3 (line 4): something"])


class StepLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = ImagingPlan(
            steps=[
                ImagingPlanStep(step=1, label="A", start_unix_ms=1000, end_unix_ms=2000),
                ImagingPlanStep(step=2, label="B", start_unix_ms=2000, end_unix_ms=3000),
                ImagingPlanStep(step=3, label="C", start_unix_ms=4000, end_unix_ms=5000),
            ]
        )

    def test_start_is_inclusive_and_end_exclusive(self) -> None:
        lookup = StepLookup(self.plan)
        self.assertIsNone(lookup.step_at(999))
        self.assertEqual(lookup.step_at(1000).step, 1)
        self.assertEqual(lookup.step_at(1999).step, 1)
        self.assertEqual(lookup.step_at(2000).step, 2)
        self.assertIsNone(lookup.step_at(3000))  # a gap
        self.assertIsNone(lookup.step_at(3999))
        self.assertEqual(lookup.step_at(4000).step, 3)
        self.assertIsNone(lookup.step_at(5000))

    def test_plan_step_at_agrees_with_the_lookup(self) -> None:
        lookup = StepLookup(self.plan)
        for time_ms in (0, 999, 1000, 1500, 2000, 2999, 3000, 3500, 4000, 4999, 5000, 9000):
            expected = lookup.step_at(time_ms)
            self.assertEqual(None if expected is None else expected.step, None if self.plan.step_at(time_ms) is None else self.plan.step_at(time_ms).step)

    def test_no_plan_finds_nothing(self) -> None:
        self.assertIsNone(StepLookup(None).step_at(1000))


class ClassifyCubeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lookup = StepLookup(
            ImagingPlan(
                steps=[
                    ImagingPlanStep(step=1, label="A", start_unix_ms=1000, end_unix_ms=2300),
                    ImagingPlanStep(step=2, label="B", start_unix_ms=2300, end_unix_ms=3300),
                ]
            )
        )

    def test_cube_inside_one_step(self) -> None:
        result = classify_cube(self.lookup, [1000, 1300])
        self.assertEqual((result.step, result.mixed, result.counts), (1, False, ((1, 2),)))

    def test_cube_across_a_step_change_is_mixed_and_counts_each_side(self) -> None:
        result = classify_cube(self.lookup, [2300, 2000, 2200])  # unsorted on purpose
        self.assertTrue(result.mixed)
        self.assertIsNone(result.step)
        self.assertEqual(result.counts, ((1, 2), (2, 1)))

    def test_cube_across_idle_and_a_step_is_mixed(self) -> None:
        result = classify_cube(self.lookup, [100, 400, 1000])
        self.assertTrue(result.mixed)
        self.assertEqual(result.counts, ((None, 2), (1, 1)))

    def test_cube_entirely_outside_the_steps_is_not_mixed_and_has_no_step(self) -> None:
        result = classify_cube(self.lookup, [100, 400])
        self.assertEqual((result.step, result.mixed), (None, False))

    def test_cube_without_images(self) -> None:
        result = classify_cube(self.lookup, [])
        self.assertEqual((result.step, result.mixed, result.counts), (None, False, ()))

    def test_classify_cubes_maps_each_cube(self) -> None:
        plan = ImagingPlan(steps=[ImagingPlanStep(step=1, label="A", start_unix_ms=1000, end_unix_ms=2000)])
        result = classify_cubes(plan, {0: [100, 200], 1: [1000, 1500], 2: [1900, 2100]})
        self.assertEqual([result[c].mixed for c in (0, 1, 2)], [False, False, True])
        self.assertEqual(result[1].step, 1)


class MetadataModelTests(unittest.TestCase):
    def test_plan_survives_a_json_round_trip(self) -> None:
        plan = build_imaging_plan(
            _events((100, IDLE), (1000, "A"), (2000, IDLE)), data_end_unix_ms=2001,
            planned=_planned((1.0, "A")), source_file="run.csv",
        )
        metadata = ImagingAcquisitionMetadata(source_format="legacy_measuring_times_csv", plan=plan)
        again = ImagingAcquisitionMetadata.model_validate(metadata.model_dump(mode="json"))
        self.assertEqual(again.plan, plan)

    def test_metadata_saved_before_plans_existed_loads_with_no_plan(self) -> None:
        old = {"source_format": "legacy_measuring_times_csv", "comment_events": [{"acquired_at_unix_ms": 5, "comment": "A"}]}
        metadata = ImagingAcquisitionMetadata.model_validate(old)
        self.assertIsNone(metadata.plan)
        self.assertEqual(len(metadata.comment_events), 1)


if __name__ == "__main__":
    unittest.main()
