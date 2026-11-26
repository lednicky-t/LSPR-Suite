"""The shared budget of cores (`lspr_core.perf.budget`) and what it does to the runs that hold shares of it.

Machines are simulated (no hardware, no real work): an 8-thread computer with one core kept free has a budget of 7 workers.
"""

from __future__ import annotations

import gc
import logging
import random
import tempfile
import threading
import unittest
from pathlib import Path

from tests._paths import ensure_repo_paths

ensure_repo_paths()

from lspr_core.perf import (  # noqa: E402
    PerfPreferences,
    PerfRuntime,
    ProfileStore,
    SystemProfile,
    ThroughputTuner,
    WorkerBudget,
    WorkerLimits,
    fair_shares,
)
from lspr_core.perf.preferences import MODE_AUTO, MODE_MANUAL  # noqa: E402
from lspr_core.perf.tuner import PHASE_BASELINE, PHASE_FIXED  # noqa: E402

GIB = 1024**3
TUNE = PerfPreferences(mode=MODE_AUTO)


def machine(logical: int = 8) -> SystemProfile:
    return SystemProfile(
        logical_cores=logical, physical_cores=logical // 2, total_ram_bytes=16 * GIB, available_ram_bytes=12 * GIB, on_battery=False,
        platform="test-x86_64",
    )


class FairSharesTests(unittest.TestCase):
    def test_a_single_job_gets_what_it_wants_up_to_the_capacity(self) -> None:
        self.assertEqual(fair_shares(7, [7]), [7])
        self.assertEqual(fair_shares(7, [3]), [3])
        self.assertEqual(fair_shares(7, [20]), [7])

    def test_equal_jobs_split_the_cores_and_the_older_one_keeps_more(self) -> None:
        self.assertEqual(fair_shares(7, [7, 7]), [4, 3])
        self.assertEqual(fair_shares(7, [7, 7, 7]), [3, 2, 2])
        self.assertEqual(fair_shares(8, [8, 8]), [4, 4])

    def test_what_a_small_job_does_not_need_goes_to_the_others(self) -> None:
        self.assertEqual(fair_shares(7, [2, 7]), [2, 5])
        self.assertEqual(fair_shares(7, [7, 2]), [5, 2])
        self.assertEqual(fair_shares(7, [1, 7, 7]), [1, 3, 3])

    def test_nobody_gets_less_than_one_worker_even_when_there_are_more_jobs_than_cores(self) -> None:
        self.assertEqual(fair_shares(2, [8] * 5), [1, 1, 1, 1, 1])
        self.assertEqual(fair_shares(1, [4, 4]), [1, 1])

    def test_a_hard_lease_gets_exactly_what_it_wants_and_the_others_share_the_rest(self) -> None:
        self.assertEqual(fair_shares(8, [3, 8], [True, False]), [3, 5])
        self.assertEqual(fair_shares(8, [8, 3], [False, True]), [5, 3])
        self.assertEqual(fair_shares(4, [6, 4], [True, False]), [6, 1], "a hard want above the capacity is still honoured")

    def test_nobody_gets_more_than_it_wants_and_the_total_fits_when_it_can(self) -> None:
        rng = random.Random(7)
        for _ in range(500):
            capacity = rng.randint(1, 32)
            wants = [rng.randint(1, 40) for _ in range(rng.randint(1, 6))]
            shares = fair_shares(capacity, wants)
            self.assertTrue(all(1 <= share <= want for share, want in zip(shares, wants)), (capacity, wants, shares))
            if len(wants) <= capacity:
                self.assertLessEqual(sum(shares), capacity, (capacity, wants, shares))
                if sum(wants) >= capacity:
                    self.assertEqual(sum(shares), capacity, "no core is left idle while a job wants more")

    def test_it_is_deterministic(self) -> None:
        self.assertEqual(fair_shares(7, [7, 5, 7]), fair_shares(7, [7, 5, 7]))


class WorkerBudgetTests(unittest.TestCase):
    def test_a_second_job_shrinks_the_first_and_its_end_gives_the_cores_back(self) -> None:
        budget, told = WorkerBudget(7), []
        first = budget.acquire("export", want=7, on_change=told.append)
        self.assertEqual(first.allowance, 7)
        second = budget.acquire("fits", want=7)
        self.assertEqual((first.allowance, second.allowance), (4, 3))
        self.assertEqual(told, [4], "the older job is told; the new one reads its share from the lease")
        second.release()
        self.assertEqual(first.allowance, 7)
        self.assertEqual(told, [4, 7])

    def test_releasing_twice_is_harmless(self) -> None:
        budget = WorkerBudget(7)
        lease = budget.acquire("export", want=7)
        other = budget.acquire("fits", want=7)
        lease.release()
        lease.release()
        self.assertEqual((budget.active(), other.allowance), (1, 7))
        self.assertTrue(lease.released)

    def test_a_lease_is_a_context_manager(self) -> None:
        budget = WorkerBudget(7)
        with budget.acquire("export", want=7):
            self.assertEqual(budget.active(), 1)
        self.assertEqual(budget.active(), 0)

    def test_the_capacity_given_by_the_newest_job_wins(self) -> None:
        budget = WorkerBudget(2)
        budget.acquire("export", want=8, capacity=6)
        self.assertEqual(budget.capacity, 6)

    def test_a_callback_that_fails_does_not_stop_the_others_or_the_new_job(self) -> None:
        budget, told = WorkerBudget(8), []

        def broken(_: int) -> None:
            raise RuntimeError("boom")

        with self.assertLogs("lspr_core.perf.budget", level=logging.WARNING):
            a = budget.acquire("a", want=8, on_change=broken)
            b = budget.acquire("b", want=8, on_change=told.append)  # tells a (which fails)
            c = budget.acquire("c", want=8)  # tells a (fails again) and b
        self.assertEqual(told[-1], b.allowance)
        self.assertEqual(sorted((a.allowance, b.allowance, c.allowance)), [2, 3, 3])

    def test_a_released_lease_is_not_told_any_more(self) -> None:
        budget, told = WorkerBudget(7), []
        first = budget.acquire("export", want=7, on_change=told.append)
        first.release()
        budget.acquire("fits", want=7)
        budget.acquire("more", want=7)
        self.assertEqual(told, [])

    def test_snapshot_says_who_holds_what(self) -> None:
        budget = WorkerBudget(7)
        budget.acquire("export", want=7)
        budget.acquire("fits", want=2, hard=True)
        self.assertEqual([(i.task_key, i.want, i.allowance, i.hard) for i in budget.snapshot()], [("export", 7, 5, False), ("fits", 2, 2, True)])

    def test_many_threads_starting_and_ending_jobs_leave_it_empty_and_consistent(self) -> None:
        budget, errors = WorkerBudget(7), []

        def churn(seed: int) -> None:
            rng = random.Random(seed)
            try:
                for _ in range(150):
                    lease = budget.acquire(f"job{seed}", want=rng.randint(1, 9), on_change=lambda share: None)
                    self.assertGreaterEqual(lease.allowance, 1)
                    lease.release()
            except Exception as exc:  # noqa: BLE001 - reported to the test below
                errors.append(exc)

        threads = [threading.Thread(target=churn, args=(seed,)) for seed in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(budget.active(), 0)


class TunerRangeChangeTests(unittest.TestCase):
    """`ThroughputTuner.set_limits`: the range moves while the job runs."""

    @staticmethod
    def limits(minimum: int, start: int, maximum: int, fixed: bool = False) -> WorkerLimits:
        return WorkerLimits(minimum=minimum, start=start, maximum=maximum, fixed=fixed, reason="test")

    @staticmethod
    def machine_rate(n: int) -> float:
        return 10.0 * min(n, 5)  # five useful workers

    def drive(self, tuner: ThroughputTuner, windows: int) -> None:
        for _ in range(windows):
            tuner.observe_rate(self.machine_rate(tuner.workers))

    def test_a_smaller_range_takes_effect_at_once_and_the_search_restarts_inside_it(self) -> None:
        tuner = ThroughputTuner(self.limits(1, 6, 7))
        self.drive(tuner, 40)
        tuner.set_limits(self.limits(1, 3, 3))
        self.assertLessEqual(tuner.workers, 3)
        self.assertEqual(tuner.phase, PHASE_BASELINE, "a new search inside the smaller range")
        tuner.set_limits(self.limits(3, 3, 3))
        self.assertEqual((tuner.workers, tuner.phase), (3, PHASE_FIXED), "a range of one value is not searched")
        tuner.set_limits(self.limits(1, 3, 4))
        self.assertEqual(tuner.phase, PHASE_BASELINE)
        self.drive(tuner, 40)
        self.assertLessEqual(tuner.workers, 4)

    def test_when_the_range_grows_again_the_search_climbs_back_by_itself(self) -> None:
        tuner = ThroughputTuner(self.limits(1, 6, 7))
        self.drive(tuner, 40)
        tuner.set_limits(self.limits(1, 2, 2))
        tuner.set_limits(self.limits(1, 6, 7))
        self.drive(tuner, 60)
        self.assertGreaterEqual(self.machine_rate(tuner.workers), 0.93 * 50.0, f"back on {tuner.workers} workers")

    def test_measurements_from_before_the_change_are_not_compared_with_those_after(self) -> None:
        tuner = ThroughputTuner(self.limits(1, 6, 7))
        self.drive(tuner, 10)
        windows_before = len(tuner.history)
        tuner.set_limits(self.limits(1, 3, 4))
        tuner.observe_rate(1000.0)  # the window that spans the change: thrown away
        self.assertEqual(len(tuner.history), windows_before)

    def test_a_run_that_was_never_searched_takes_the_count_its_new_range_starts_at(self) -> None:
        tuner = ThroughputTuner(self.limits(7, 7, 7, fixed=True))
        tuner.set_limits(self.limits(3, 3, 3, fixed=True))
        self.assertEqual((tuner.workers, tuner.phase), (3, PHASE_FIXED))
        tuner.set_limits(self.limits(7, 7, 7, fixed=True))
        self.assertEqual(tuner.workers, 7)

    def test_a_job_too_short_to_search_is_not_searched_after_a_change_either(self) -> None:
        tuner = ThroughputTuner(self.limits(1, 6, 7), total_units=10)
        self.assertEqual(tuner.phase, PHASE_FIXED)
        tuner.set_limits(self.limits(1, 3, 4))
        self.assertEqual(tuner.phase, PHASE_FIXED)

    def test_the_change_is_reported_to_the_owner(self) -> None:
        seen: list[tuple[int, int, str]] = []
        tuner = ThroughputTuner(self.limits(1, 6, 7), on_change=lambda old, new, why: seen.append((old, new, why)))
        tuner.set_limits(self.limits(1, 3, 3))
        self.assertEqual(seen, [(6, 3, "range changed")])


class SharedRunTests(unittest.TestCase):
    """Jobs that run at the same time share the cores; each one gives its share back however it ends."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = ProfileStore(Path(self._tmp.name) / "perf.json")

    def runtime(self, preferences: PerfPreferences = PerfPreferences(), logical: int = 8) -> PerfRuntime:
        return PerfRuntime(preferences, store=self.store, probe=lambda: machine(logical))

    def test_a_lone_job_is_exactly_what_it_was_before_there_was_a_budget(self) -> None:
        run = self.runtime().begin("export", total_units=100000)
        self.assertEqual((run.workers, run.maximum, run.share, run.shared), (7, 7, 7, False))
        self.assertEqual(run.gate.limit, 7)
        self.assertFalse(run.finish().shared)

    def test_two_jobs_at_once_do_not_each_take_every_core(self) -> None:
        runtime = self.runtime()
        export = runtime.begin("export", total_units=100000)
        fits = runtime.begin("fits", total_units=100000)
        self.assertEqual((export.workers, fits.workers), (4, 3))
        self.assertEqual((export.gate.limit, fits.gate.limit), (4, 3), "the gates that hold back the threads follow")
        self.assertEqual((export.maximum, fits.maximum), (7, 7), "the pool is still made at the largest size: only the tasks in flight shrink")
        fits.finish()
        self.assertEqual((export.workers, export.gate.limit), (7, 7), "the cores come back when the other job ends")
        report = export.finish()
        self.assertTrue(report.shared)
        self.assertEqual(runtime.budget.active(), 0)

    def test_a_job_that_wants_little_leaves_the_rest_to_the_other(self) -> None:
        runtime = self.runtime()
        big = runtime.begin("export", total_units=100000)
        small = runtime.begin("one_task", total_units=100, max_useful=2)
        self.assertEqual((big.workers, small.workers), (5, 2))

    def test_a_fixed_number_of_workers_is_exact_and_the_others_share_the_rest(self) -> None:
        runtime = self.runtime(PerfPreferences(mode=MODE_MANUAL, manual_workers=5))
        manual = runtime.begin("export", total_units=100000)
        runtime.set_preferences(PerfPreferences())
        other = runtime.begin("fits", total_units=100000)
        self.assertEqual((manual.workers, other.workers), (5, 2))
        self.assertFalse(manual.finish().shared)

    def test_a_job_that_fails_gives_its_share_back_with_close(self) -> None:
        runtime = self.runtime()
        first = runtime.begin("export", total_units=100000)
        second = runtime.begin("fits", total_units=100000)
        second.close()
        self.assertEqual(first.workers, 7)
        second.close()  # twice is fine
        self.assertEqual(runtime.budget.active(), 1)
        first.close()
        self.assertEqual(runtime.budget.active(), 0)

    def test_finish_after_close_still_reports(self) -> None:
        run = self.runtime().begin("export", total_units=100000)
        run.close()
        self.assertEqual(run.finish().workers, 7)

    def test_report_keeps_the_share_and_finish_gives_it_back(self) -> None:
        runtime = self.runtime()
        run = runtime.begin("scan", total_units=100000)
        run.report()
        self.assertEqual(runtime.budget.active(), 1, "a job whose tuned stage ended but that still runs keeps its share")
        run.finish()
        self.assertEqual(runtime.budget.active(), 0)

    def test_a_run_is_a_context_manager(self) -> None:
        runtime = self.runtime()
        with self.assertRaises(RuntimeError), runtime.begin("export", total_units=100000):
            self.assertEqual(runtime.budget.active(), 1)
            raise RuntimeError("the job failed")
        self.assertEqual(runtime.budget.active(), 0)

    def test_a_run_that_is_simply_forgotten_gives_its_share_back_when_it_is_collected(self) -> None:
        runtime = self.runtime()
        keeper = runtime.begin("export", total_units=100000)

        def forgetful() -> None:
            runtime.begin("fits", total_units=100000)  # never finished, never closed

        forgetful()
        gc.collect()
        self.assertEqual(runtime.budget.active(), 1)
        self.assertEqual(keeper.workers, 7, "the forgotten job did not keep the cores")

    def test_asking_for_a_size_takes_no_share(self) -> None:
        runtime = self.runtime()
        keeper = runtime.begin("export", total_units=100000)
        limits = runtime.plan()
        self.assertEqual((limits.maximum, runtime.budget.active(), keeper.workers), (7, 1, 7))

    def test_a_shared_run_does_not_save_what_it_learned(self) -> None:
        """A rate measured while another job took cores is not what this computer does alone."""
        runtime = self.runtime(TUNE)
        tuning = runtime.begin("export", total_units=100000)
        other = runtime.begin("fits", total_units=100000)
        for _ in range(80):
            tuning.tuner.observe_rate(10.0 * min(tuning.workers, 3))
        other.close()
        for _ in range(80):
            tuning.tuner.observe_rate(10.0 * min(tuning.workers, 3))
        report = tuning.finish()
        self.assertTrue(report.shared)
        self.assertEqual(runtime.learned_all(), [])

    def test_a_run_that_was_alone_still_saves_what_it_learned(self) -> None:
        runtime = self.runtime(TUNE)
        tuning = runtime.begin("export", total_units=100000)
        for _ in range(80):
            tuning.tuner.observe_rate(10.0 * min(tuning.workers, 5))
        self.assertFalse(tuning.finish().shared)
        self.assertEqual(len(runtime.learned_all()), 1)

    def test_a_tuned_run_searches_inside_its_share(self) -> None:
        runtime = self.runtime(TUNE)
        tuning = runtime.begin("export", total_units=100000)
        other = runtime.begin("fits", total_units=100000)
        for _ in range(80):
            tuning.tuner.observe_rate(10.0 * min(tuning.workers, 5))
            self.assertLessEqual(tuning.workers, tuning.share)
        self.assertLessEqual(tuning.workers, 4)
        other.finish()
        for _ in range(80):
            tuning.tuner.observe_rate(10.0 * min(tuning.workers, 5))
        self.assertGreaterEqual(tuning.workers, 5, "it climbs back once the other job is gone")

    def test_the_user_changing_the_cores_kept_free_reaches_the_next_job(self) -> None:
        runtime = self.runtime()
        runtime.begin("export", total_units=100000).finish()
        runtime.set_preferences(PerfPreferences(reserved_cores=3))
        run = runtime.begin("export", total_units=100000)
        self.assertEqual((run.workers, runtime.budget.capacity), (5, 5))

    def test_the_report_says_whether_the_run_was_shared(self) -> None:
        runtime = self.runtime()
        alone = runtime.begin("export", total_units=100).finish().as_fields()
        self.assertIs(alone["shared"], False)

    def test_two_threads_starting_and_ending_jobs_never_leave_a_run_outside_its_share(self) -> None:
        runtime, errors = self.runtime(), []

        def job(seed: int) -> None:
            rng = random.Random(seed)
            try:
                for _ in range(60):
                    with runtime.begin(f"job{seed}", total_units=100000) as run:
                        self.assertTrue(1 <= run.workers <= run.maximum)
                        self.assertLessEqual(run.gate.limit, run.maximum)
                        if rng.random() < 0.5:
                            run.unit_done(1)
            except Exception as exc:  # noqa: BLE001 - reported to the test below
                errors.append(exc)

        threads = [threading.Thread(target=job, args=(seed,)) for seed in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(runtime.budget.active(), 0)


if __name__ == "__main__":
    unittest.main()
