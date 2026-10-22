"""The throughput tuner, driven by simulated machines (no hardware, no clock, no threads).

Each machine is a function ``rate(workers) -> units per second``. The tuner is fed one window at a time, exactly as it is when a
real window closes, and must end near the best count without ever leaving its range. Noise is multiplicative and seeded, so a
failure is reproducible.
"""

from __future__ import annotations

import random
import unittest

from tests._paths import ensure_repo_paths

ensure_repo_paths()

from lspr_core.perf import ThroughputTuner, WorkerLimits  # noqa: E402
from lspr_core.perf.tuner import PHASE_FIXED, PHASE_SETTLED  # noqa: E402


def limits(minimum: int, start: int, maximum: int, fixed: bool = False) -> WorkerLimits:
    return WorkerLimits(minimum=minimum, start=start, maximum=maximum, fixed=fixed, reason="test")


def cpu_bound(n: int) -> float:
    """4 real cores (10 units/s each); the hyper-threaded second thread of a core adds 25 %; more workers than 8 only hurt."""
    real = min(n, 4)
    hyper = max(0, min(n, 8) - 4)
    return 10.0 * real + 2.5 * hyper - 0.5 * max(0, n - 8)


def disk_bound(n: int) -> float:
    """The disk delivers 35 units/s however many workers ask."""
    return min(10.0 * n, 35.0)


def contended(n: int) -> float:
    """Linear up to 6 workers, then they fight over memory bandwidth and the rate falls."""
    return 10.0 * n if n <= 6 else 60.0 - 8.0 * (n - 6)


def drive(tuner: ThroughputTuner, rate_of, *, windows: int = 200, noise: float = 0.0, seed: int = 1) -> list[int]:
    """Feed the tuner ``windows`` windows of the simulated machine; returns the worker count used in each."""
    rng = random.Random(seed)
    used: list[int] = []
    for _ in range(windows):
        used.append(tuner.workers)
        tuner.observe_rate(rate_of(tuner.workers) * (1.0 + noise * rng.uniform(-1.0, 1.0)))
    return used


class TunerFindsTheBestCountTests(unittest.TestCase):
    def assert_close_to_best(self, tuner: ThroughputTuner, rate_of, lo: int, hi: int, tolerance: float = 0.07) -> None:
        best = max(rate_of(n) for n in range(lo, hi + 1))
        self.assertGreaterEqual(rate_of(tuner.workers), best * (1.0 - tolerance), f"settled on {tuner.workers} workers")

    def test_cpu_bound_machine_from_the_physical_core_count(self) -> None:
        tuner = ThroughputTuner(limits(1, 4, 7))
        drive(tuner, cpu_bound, windows=60)
        self.assertEqual(tuner.phase, PHASE_SETTLED)
        self.assert_close_to_best(tuner, cpu_bound, 1, 7)

    def test_starting_too_high_comes_back_down(self) -> None:
        tuner = ThroughputTuner(limits(1, 16, 24))
        drive(tuner, cpu_bound, windows=80)
        self.assert_close_to_best(tuner, cpu_bound, 1, 24)
        self.assertLessEqual(tuner.workers, 8)

    def test_disk_bound_machine_uses_the_fewest_workers_that_saturate_the_disk(self) -> None:
        tuner = ThroughputTuner(limits(1, 8, 16))
        drive(tuner, disk_bound, windows=80)
        self.assertEqual(tuner.phase, PHASE_SETTLED)
        self.assertGreaterEqual(disk_bound(tuner.workers), 35.0 * 0.95)
        self.assertLessEqual(tuner.workers, 5, "more workers than needed to saturate the disk are wasted memory")

    def test_contended_machine_finds_the_peak_not_the_maximum(self) -> None:
        tuner = ThroughputTuner(limits(1, 4, 14))
        drive(tuner, contended, windows=80)
        self.assert_close_to_best(tuner, contended, 1, 14, tolerance=0.1)
        self.assertLessEqual(tuner.workers, 8)

    def test_noisy_plateau_does_not_wander_off(self) -> None:
        def plateau(n: int) -> float:
            return 10.0 * min(n, 4)

        tuner = ThroughputTuner(limits(1, 6, 12))
        used = drive(tuner, plateau, windows=120, noise=0.03, seed=7)
        self.assertIn(tuner.workers, (4, 5), "the cheapest count within noise of the best is 4")
        self.assertTrue(all(1 <= n <= 12 for n in used))

    def test_several_noise_seeds_all_end_within_a_few_percent_of_the_best(self) -> None:
        for seed in range(12):
            with self.subTest(seed=seed):
                tuner = ThroughputTuner(limits(1, 4, 7))
                drive(tuner, cpu_bound, windows=100, noise=0.03, seed=seed)
                self.assert_close_to_best(tuner, cpu_bound, 1, 7, tolerance=0.12)

    def test_never_leaves_its_range(self) -> None:
        for machine in (cpu_bound, disk_bound, contended):
            tuner = ThroughputTuner(limits(2, 5, 9))
            used = drive(tuner, machine, windows=150, noise=0.05)
            self.assertTrue(all(2 <= n <= 9 for n in used), machine.__name__)

    def test_a_search_finishes_quickly(self) -> None:
        """Each window is at least 3 s on a real run, so the search must settle within about 20 windows."""
        tuner = ThroughputTuner(limits(1, 4, 7))
        for count in range(1, 41):
            tuner.observe_rate(cpu_bound(tuner.workers))
            if tuner.phase == PHASE_SETTLED:
                self.assertLessEqual(count, 20)
                return
        self.fail("the search did not settle within 40 windows")


class TunerReactsToChangeTests(unittest.TestCase):
    def test_a_slowdown_after_settling_triggers_a_new_search(self) -> None:
        tuner = ThroughputTuner(limits(1, 4, 8), reprobe_windows=50)
        drive(tuner, cpu_bound, windows=40)
        self.assertEqual(tuner.phase, PHASE_SETTLED)
        searches_before = tuner.summary().changes

        def throttled(n: int) -> float:  # another program takes half the machine
            return cpu_bound(n) * 0.5

        drive(tuner, throttled, windows=40)
        self.assertGreater(tuner.summary().changes, searches_before)

    def test_periodic_recheck_happens_when_nothing_changes(self) -> None:
        tuner = ThroughputTuner(limits(1, 4, 8), reprobe_windows=6)
        drive(tuner, cpu_bound, windows=30)
        settled_changes = tuner.summary().changes
        drive(tuner, cpu_bound, windows=30)
        self.assertGreater(tuner.summary().changes, settled_changes, "a re-check tries a neighbouring count")
        self.assertGreaterEqual(cpu_bound(tuner.workers), max(cpu_bound(n) for n in range(1, 9)) * 0.93)


class TunerEdgeCaseTests(unittest.TestCase):
    def test_a_fixed_range_never_changes(self) -> None:
        tuner = ThroughputTuner(limits(3, 3, 3, fixed=True))
        used = drive(tuner, cpu_bound, windows=30)
        self.assertEqual(set(used), {3})
        self.assertEqual(tuner.phase, PHASE_FIXED)

    def test_a_job_too_short_for_a_search_is_not_tuned(self) -> None:
        tuner = ThroughputTuner(limits(1, 4, 8), total_units=20)
        self.assertEqual(tuner.phase, PHASE_FIXED)
        self.assertEqual(set(drive(tuner, cpu_bound, windows=20)), {4})

    def test_a_long_job_is_tuned(self) -> None:
        tuner = ThroughputTuner(limits(1, 4, 8), total_units=8000)
        self.assertNotEqual(tuner.phase, PHASE_FIXED)

    def test_no_new_probe_near_the_end_of_the_job(self) -> None:
        tuner = ThroughputTuner(limits(1, 4, 8), total_units=1000)
        tuner.record(units=990, now=0.0)  # almost everything is done
        used = drive(tuner, cpu_bound, windows=10)
        self.assertEqual(set(used), {4}, "with 10 units left a search cannot pay back")
        self.assertEqual(tuner.phase, PHASE_SETTLED)

    def test_the_first_window_is_discarded_as_warm_up(self) -> None:
        tuner = ThroughputTuner(limits(1, 4, 8))
        tuner.observe_rate(0.1)  # pool still starting: a terrible rate that must not become the baseline
        tuner.observe_rate(cpu_bound(4))
        self.assertEqual([entry[1] for entry in tuner.history], [cpu_bound(4)])

    def test_learned_start_searches_in_steps_of_one(self) -> None:
        """From a remembered count the search probes the neighbours (+1, then -1 from the best), never a quarter-size jump."""
        tuner = ThroughputTuner(limits(1, 12, 24), initial_step=1)  # a quarter step would be 3
        used = drive(tuner, cpu_bound, windows=14)
        self.assertTrue(all(abs(b - a) <= 2 for a, b in zip(used, used[1:], strict=False)), used)

    def test_summary_reports_the_settled_count_and_its_median_rate(self) -> None:
        tuner = ThroughputTuner(limits(1, 4, 8))
        drive(tuner, cpu_bound, windows=60)
        summary = tuner.summary()
        self.assertTrue(summary.settled)
        self.assertEqual(summary.rate, cpu_bound(summary.workers))
        self.assertEqual(summary.workers, tuner.workers)

    def test_on_change_is_called_with_the_reason(self) -> None:
        events: list[tuple[int, int, str]] = []
        tuner = ThroughputTuner(limits(1, 4, 8), on_change=lambda old, new, why: events.append((old, new, why)))
        drive(tuner, cpu_bound, windows=30)
        self.assertTrue(events)
        self.assertTrue(all(old != new and why for old, new, why in events))


class TunerWindowTests(unittest.TestCase):
    def test_the_clock_starts_at_the_first_finished_unit_and_that_window_is_discarded(self) -> None:
        tuner = ThroughputTuner(limits(1, 4, 8), window_seconds=3.0, clock=lambda: 0.0)
        tuner.record(units=1, now=6.0)  # workers took 6 s to start: the first unit starts the clock
        tuner.record(units=40, now=10.0)  # window 1 (40 units in 4 s): warm-up, discarded
        self.assertEqual(tuner.history, [])
        tuner.record(units=40, now=14.0)  # window 2: the baseline at 4 workers, 40 units in 4 s = 10 units/s
        self.assertEqual(tuner.history, [(4, 10.0, "baseline")])

    def test_a_window_waits_for_its_units_even_when_the_time_is_up(self) -> None:
        tuner = ThroughputTuner(limits(1, 4, 8), window_seconds=3.0, units_per_worker=2.0, clock=lambda: 0.0)
        tuner.record(units=1, now=0.0)
        tuner.record(units=3, now=10.0)  # 3 units < 2 per worker x 4 workers: the window stays open
        tuner.record(units=5, now=11.0)  # 8 units: it closes (the discarded warm-up window)
        tuner.record(units=40, now=15.0)  # the next one is the baseline: 40 units in 4 s
        self.assertEqual(tuner.history, [(4, 10.0, "baseline")])


class SearchCostTests(unittest.TestCase):
    """The search runs while real work is being done, so its own cost matters: windows spent at a worse count are lost work."""

    def mean_rate_during_search(self, rate_of, lo: int, start: int, hi: int, windows: int = 40) -> float:
        tuner = ThroughputTuner(limits(lo, start, hi))
        rates = []
        for _ in range(windows):
            rate = rate_of(tuner.workers)
            rates.append(rate)
            tuner.observe_rate(rate)
        return sum(rates) / len(rates)

    def test_a_flat_machine_loses_almost_nothing_to_the_search(self) -> None:
        """When the count barely matters (the development laptop: 3 to 7 workers give the same rate) the search costs only the windows
        at the probed counts, which are as fast: no rate below 95 % of the best is ever sampled except the one failed probe."""
        def flat(n: int) -> float:
            return 25.0 if n >= 3 else 25.0 * n / 3

        self.assertGreaterEqual(self.mean_rate_during_search(flat, 1, 7, 7), 25.0 * 0.90)

    def test_a_downward_probe_costs_one_window_not_two(self) -> None:
        tuner = ThroughputTuner(limits(1, 8, 8))
        tuner.observe_rate(10.0)  # warm-up
        tuner.observe_rate(10.0)  # baseline at 8; the tuner steps down at once
        first_probe = tuner.workers
        self.assertLess(first_probe, 8)
        tuner.observe_rate(10.0)  # the very next window is already the probe's measurement
        self.assertTrue(any(workers == first_probe for workers, _, _ in tuner.history))

    def test_a_downward_search_slows_down_when_the_rate_starts_to_fall(self) -> None:
        def sagging(n: int) -> float:
            return 100.0 - 1.5 * (8 - n) if n >= 4 else 50.0  # a gentle slope, then a cliff below 4

        tuner = ThroughputTuner(limits(1, 8, 8))
        used = drive(tuner, sagging, windows=40)
        self.assertGreaterEqual(min(used), 3)
        self.assertGreaterEqual(tuner.workers, 4)


if __name__ == "__main__":
    unittest.main()
