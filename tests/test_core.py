from __future__ import annotations

import unittest

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from lspr_core import ExperimentPlan, ExperimentPlanStep, SuiteIdentity, retime_steps, shift_steps, summarize_experiment_plan


class CoreModelTests(unittest.TestCase):
    def test_experiment_plan_ordering_and_summary(self) -> None:
        plan = ExperimentPlan(
            identity=SuiteIdentity(
                app_name="Suite",
                app_version="1.2.3",
                format_name="plan",
                format_version=1,
            ),
            steps=[
                ExperimentPlanStep(id=2, start_s=10.0, end_s=15.0, comment="second"),
                ExperimentPlanStep(id=1, start_s=0.0, end_s=4.0, comment="first"),
            ],
        )

        ordered_ids = [step.id for step in plan.ordered()]
        self.assertEqual(ordered_ids, [1, 2])

        summary = summarize_experiment_plan(plan)
        self.assertEqual(summary.step_count, 2)
        self.assertEqual(summary.start_s, 0.0)
        self.assertEqual(summary.end_s, 15.0)
        self.assertEqual(summary.total_duration_s, 15.0)

    def test_retime_and_shift_steps_preserve_step_duration(self) -> None:
        original = [
            ExperimentPlanStep(id=1, start_s=2.0, end_s=5.0),
            ExperimentPlanStep(id=2, start_s=5.0, end_s=9.5),
        ]

        retimed = retime_steps(original, start_s=7.0)
        self.assertEqual([(step.start_s, step.end_s) for step in retimed], [(7.0, 10.0), (10.0, 14.5)])

        shifted = shift_steps(original, delta_s=3.0)
        self.assertEqual([(step.start_s, step.end_s) for step in shifted], [(5.0, 8.0), (8.0, 12.5)])

        self.assertEqual([step.duration_s for step in retimed], [3.0, 4.5])
        self.assertEqual([step.duration_s for step in shifted], [3.0, 4.5])
