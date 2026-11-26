"""lspr_core.perf apart from the tuner: the machine probe, the worker limits, the gate, the profile file and the runtime."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests._paths import ensure_repo_paths

ensure_repo_paths()

from lspr_core.perf import (  # noqa: E402
    ConcurrencyGate,
    PerfPreferences,
    PerfRuntime,
    DestinationProfile,
    ProfileStore,
    SystemProfile,
    get_runtime,
    pool_threads,
    probe_destination,
    probe_system,
    set_runtime,
    worker_limits,
)
from lspr_core.perf import system_profile  # noqa: E402
from lspr_core.perf.preferences import MODE_AUTO, MODE_MANUAL, MODE_STANDARD  # noqa: E402

TUNE = PerfPreferences(mode=MODE_AUTO)  # tuning switched on (the default is Standard)

GIB = 1024**3


def machine(logical: int = 8, physical: int = 4, free_gib: float = 10.0, battery: bool | None = False) -> SystemProfile:
    return SystemProfile(
        logical_cores=logical, physical_cores=physical, total_ram_bytes=int(16 * GIB), available_ram_bytes=int(free_gib * GIB),
        on_battery=battery, platform="test-x86_64",
    )


class SystemProfileTests(unittest.TestCase):
    def test_the_real_probe_returns_sane_numbers(self) -> None:
        system = probe_system()
        self.assertGreaterEqual(system.logical_cores, 1)
        self.assertTrue(1 <= system.physical_cores <= system.logical_cores)
        self.assertIn(system.on_battery, (True, False, None))
        self.assertTrue(system.fingerprint())
        self.assertTrue(system.describe())

    def test_fingerprint_ignores_free_memory_and_battery(self) -> None:
        self.assertEqual(machine(free_gib=2.0, battery=True).fingerprint(), machine(free_gib=12.0, battery=False).fingerprint())
        self.assertNotEqual(machine(logical=8).fingerprint(), machine(logical=16).fingerprint())

    def test_describe_reads_as_a_sentence_fragment(self) -> None:
        self.assertEqual(machine().describe(), "4 cores / 8 threads, 16.0 GB memory (10.0 GB free), on mains")
        self.assertIn("on battery", machine(battery=True).describe())

    def test_destination_of_a_folder_that_does_not_exist_yet(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            destination = probe_destination(Path(folder) / "not" / "yet" / "export.ome.zarr")
        self.assertIn(destination.kind, ("local", "removable", "network", "unknown"))
        self.assertIsNotNone(destination.free_bytes)
        self.assertGreater(destination.free_bytes, 0)

    def test_the_disk_type_is_a_hint_that_is_always_one_of_three_words(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            destination = probe_destination(folder)
        self.assertIn(destination.media, ("ssd", "hdd", "unknown"))
        if destination.kind != "local":
            self.assertEqual(destination.media, "unknown", "only a fixed disk is asked")

    def test_a_disk_type_that_cannot_be_read_is_unknown_not_an_error(self) -> None:
        for path in (Path("relative/folder"), Path("\\\\server\\share\\folder"), Path("/")):
            self.assertEqual(system_profile._windows_drive_media(path), "unknown", path)

    def test_the_disk_type_never_changes_the_key_of_what_tuning_saved(self) -> None:
        self.assertEqual(DestinationProfile("local", 1, "ssd").key(), DestinationProfile("local", 1, "hdd").key())
        self.assertEqual(DestinationProfile("local", 1).media, "unknown")

    def test_the_destination_reads_as_a_few_words(self) -> None:
        self.assertEqual(DestinationProfile("local", 1, "ssd").describe(), "local SSD")
        self.assertEqual(DestinationProfile("local", 1).describe(), "local disk")
        self.assertEqual(DestinationProfile("network", 1).describe(), "network")
        self.assertEqual(DestinationProfile("unknown", None).describe(), "unknown disk")


class PoolThreadsTests(unittest.TestCase):
    """The small fixed pools that are not tuned: one rule, instead of `os.cpu_count()` in several places."""

    def test_at_the_default_setting_it_is_every_core_but_one_up_to_the_callers_cap(self) -> None:
        self.assertEqual(pool_threads(machine(logical=8), PerfPreferences(), cap=8), 7)
        self.assertEqual(pool_threads(machine(logical=8), PerfPreferences(), cap=4), 4)
        self.assertEqual(pool_threads(machine(logical=32), PerfPreferences(), cap=8), 8)

    def test_the_transient_scan_start_is_the_number_it_always_was(self) -> None:
        # was max(2, min(6, cpu_count - 2)); now cores - cores kept free (1) - 1 for its second pool
        for logical, expected in ((2, 2), (3, 2), (4, 2), (6, 4), (8, 6), (12, 6), (32, 6)):
            self.assertEqual(pool_threads(machine(logical=logical), PerfPreferences(), cap=6, extra_free=1, floor=2), expected, logical)

    def test_cores_kept_free_are_honoured_and_a_tiny_machine_keeps_all_of_its_cores(self) -> None:
        self.assertEqual(pool_threads(machine(logical=8), PerfPreferences(reserved_cores=3), cap=8), 5)
        self.assertEqual(pool_threads(machine(logical=2), PerfPreferences(reserved_cores=1), cap=8), 2)
        self.assertEqual(pool_threads(machine(logical=1), PerfPreferences(), cap=8), 1)

    def test_it_never_goes_below_the_floor_or_above_the_cap(self) -> None:
        self.assertEqual(pool_threads(machine(logical=4), PerfPreferences(reserved_cores=60), cap=8), 1)
        self.assertEqual(pool_threads(machine(logical=4), PerfPreferences(reserved_cores=60), cap=8, floor=2), 2)
        self.assertEqual(pool_threads(machine(logical=64), PerfPreferences(), cap=3), 3)

    def test_a_fixed_number_of_workers_is_for_the_big_jobs_not_for_these_pools(self) -> None:
        self.assertEqual(pool_threads(machine(logical=8), PerfPreferences(mode=MODE_MANUAL, manual_workers=1), cap=8), 7)

    def test_the_runtime_asks_the_machine_it_was_given(self) -> None:
        runtime = PerfRuntime(probe=lambda: machine(logical=4))
        self.assertEqual(runtime.pool_threads(8), 3)
        runtime.set_preferences(PerfPreferences(reserved_cores=0))
        self.assertEqual(runtime.pool_threads(8), 4)


class WorkerLimitsTests(unittest.TestCase):
    def test_standard_is_the_default_and_means_every_core_but_one_with_no_searching(self) -> None:
        self.assertEqual(PerfPreferences().mode, MODE_STANDARD)
        limits = worker_limits(machine(), PerfPreferences())
        self.assertEqual((limits.minimum, limits.start, limits.maximum, limits.fixed, limits.reason), (7, 7, 7, True, "standard"))

    def test_standard_still_respects_memory_and_the_number_of_tasks(self) -> None:
        self.assertEqual(worker_limits(machine(free_gib=2.0), PerfPreferences(), per_worker_bytes=int(0.5 * GIB)).start, 2)
        self.assertEqual(worker_limits(machine(), PerfPreferences(), max_useful=3).start, 3)

    def test_standard_uses_a_saved_count_as_its_fixed_count(self) -> None:
        limits = worker_limits(machine(), PerfPreferences(), start_hint=4)
        self.assertEqual((limits.minimum, limits.start, limits.maximum, limits.fixed), (4, 4, 4, True))

    def test_tuning_range_keeps_a_core_free_and_starts_at_the_top(self) -> None:
        limits = worker_limits(machine(), TUNE)
        self.assertEqual((limits.minimum, limits.start, limits.maximum, limits.fixed), (1, 7, 7, False))

    def test_reserved_cores_are_not_taken_from_a_tiny_machine(self) -> None:
        limits = worker_limits(machine(logical=2, physical=2), PerfPreferences(reserved_cores=1))
        self.assertEqual(limits.maximum, 2)

    def test_free_memory_caps_the_workers(self) -> None:
        limits = worker_limits(machine(free_gib=2.0), TUNE, per_worker_bytes=int(0.5 * GIB))
        self.assertEqual(limits.maximum, 2)  # 0.6 x 2 GiB / 0.5 GiB
        self.assertEqual(limits.reason, "memory")
        self.assertLessEqual(limits.start, limits.maximum)

    def test_memory_never_takes_the_range_below_one_worker(self) -> None:
        limits = worker_limits(machine(free_gib=0.1), PerfPreferences(), per_worker_bytes=GIB)
        self.assertEqual((limits.minimum, limits.start, limits.maximum), (1, 1, 1))

    def test_fewer_tasks_than_workers(self) -> None:
        limits = worker_limits(machine(), TUNE, max_useful=3)
        self.assertEqual((limits.maximum, limits.reason), (3, "tasks"))

    def test_a_learned_start_is_used_but_clamped(self) -> None:
        self.assertEqual(worker_limits(machine(), TUNE, start_hint=4).start, 4)
        self.assertEqual(worker_limits(machine(), TUNE, start_hint=50).start, 7)

    def test_manual_mode_is_one_fixed_count(self) -> None:
        limits = worker_limits(machine(), PerfPreferences(mode=MODE_MANUAL, manual_workers=5))
        self.assertEqual((limits.minimum, limits.start, limits.maximum, limits.fixed, limits.reason), (5, 5, 5, True, "manual"))

    def test_manual_count_is_still_bounded_by_the_work_and_by_sense(self) -> None:
        self.assertEqual(worker_limits(machine(), PerfPreferences(mode=MODE_MANUAL, manual_workers=99)).start, 16)  # 2 x logical
        self.assertEqual(worker_limits(machine(), PerfPreferences(mode=MODE_MANUAL, manual_workers=6), max_useful=2).start, 2)

    def test_manual_without_a_count_uses_the_automatic_starting_value(self) -> None:
        self.assertEqual(worker_limits(machine(), PerfPreferences(mode=MODE_MANUAL)).start, 7)

    def test_tuning_switched_off_pins_the_count(self) -> None:
        limits = worker_limits(machine(), TUNE, allow_tuning=False)
        self.assertTrue(limits.fixed)
        self.assertEqual(limits.minimum, limits.maximum)

    def test_preferences_are_forced_into_range(self) -> None:
        bad = PerfPreferences(mode="turbo", manual_workers=-4, reserved_cores=-1).normalized()
        self.assertEqual((bad.mode, bad.manual_workers, bad.reserved_cores), ("standard", 0, 0))


class ConcurrencyGateTests(unittest.TestCase):
    def test_never_more_than_the_limit_run_at_once_and_the_limit_can_change(self) -> None:
        gate = ConcurrencyGate(2)
        lock = threading.Lock()
        running = peak = 0
        started = threading.Event()

        def task() -> None:
            nonlocal running, peak
            with gate:
                with lock:
                    running += 1
                    peak = max(peak, running)
                started.set()
                time.sleep(0.02)
                with lock:
                    running -= 1

        threads = [threading.Thread(target=task) for _ in range(12)]
        for thread in threads:
            thread.start()
        started.wait(2)
        gate.set_limit(4)
        for thread in threads:
            thread.join(5)
        self.assertLessEqual(peak, 4)
        self.assertGreaterEqual(peak, 2)
        self.assertEqual(gate.active, 0)

    def test_wrap_releases_the_slot_when_the_function_raises(self) -> None:
        gate = ConcurrencyGate(1)

        def boom() -> None:
            raise ValueError("x")

        with self.assertRaises(ValueError):
            gate.wrap(boom)()
        self.assertEqual(gate.active, 0)

    def test_lowering_the_limit_lets_running_tasks_finish(self) -> None:
        gate = ConcurrencyGate(3)
        gate.acquire()
        gate.acquire()
        gate.set_limit(1)
        self.assertEqual(gate.active, 2)
        gate.release()
        gate.release()
        self.assertEqual(gate.active, 0)


class ProfileStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "sub" / "perf_profile.json"

    def test_round_trip_and_run_count(self) -> None:
        store = ProfileStore(self.path)
        self.assertIsNone(store.lookup("m", "export", "local"))
        store.record("m", "export", "local", workers=6, rate=31.4159)
        store.record("m", "export", "local", workers=7, rate=33.0)
        learned = store.lookup("m", "export", "local")
        assert learned is not None
        self.assertEqual((learned.workers, learned.rate, learned.runs), (7, 33.0, 2))

    def test_machine_task_and_disk_kind_are_separate_keys(self) -> None:
        store = ProfileStore(self.path)
        store.record("m", "export", "local", workers=6, rate=30.0)
        self.assertIsNone(store.lookup("other-machine", "export", "local"))
        self.assertIsNone(store.lookup("m", "scan", "local"))
        self.assertIsNone(store.lookup("m", "export", "network"))

    def test_a_corrupt_or_foreign_file_is_ignored_not_fatal(self) -> None:
        self.path.parent.mkdir(parents=True)
        for text in ("{not json", json.dumps({"schema": 99, "entries": {}}), json.dumps([1, 2]), ""):
            self.path.write_text(text, encoding="utf-8")
            store = ProfileStore(self.path)
            self.assertIsNone(store.lookup("m", "export", "local"))
            store.record("m", "export", "local", workers=3, rate=1.0)  # and it can be written over
            self.assertEqual(store.lookup("m", "export", "local").workers, 3)

    def test_an_invalid_worker_count_in_the_file_is_not_used(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"schema": 1, "entries": {"m|export|local": {"workers": -2}}}), encoding="utf-8")
        self.assertIsNone(ProfileStore(self.path).lookup("m", "export", "local"))

    def test_an_unwritable_location_does_not_raise(self) -> None:
        blocker = Path(self._tmp.name) / "file"
        blocker.write_text("x")
        ProfileStore(blocker / "inside.json").record("m", "export", "local", workers=2, rate=1.0)  # parent is a file


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = ProfileStore(Path(self._tmp.name) / "perf.json")
        self.previous = set_runtime(None)
        self.addCleanup(set_runtime, self.previous)

    def runtime(self, **kwargs: object) -> PerfRuntime:
        return PerfRuntime(store=self.store, probe=machine, **kwargs)

    def test_a_standard_run_uses_every_core_but_one_and_reports_it(self) -> None:
        run = self.runtime().begin("export", total_units=100000, destination=self._tmp.name)
        self.assertEqual((run.workers, run.maximum), (7, 7))
        report = run.finish()
        self.assertEqual((report.mode, report.workers, report.describe()), ("standard", 7, "7 workers (standard)"))

    def test_tuning_on_but_a_job_too_short_to_tune_reports_fixed(self) -> None:
        run = self.runtime(preferences=TUNE).begin("export", total_units=10, destination=self._tmp.name)
        self.assertEqual((run.workers, run.maximum), (7, 7))
        self.assertEqual(run.finish().mode, "fixed", "ten units are too few to tune")

    def test_the_gate_follows_the_tuner(self) -> None:
        run = self.runtime(preferences=TUNE).begin("export", total_units=100000)
        self.assertEqual(run.gate.limit, 7)
        run.tuner.observe_rate(10.0)  # warm-up window, discarded
        run.tuner.observe_rate(10.0)  # baseline at the top: the tuner now tries fewer workers
        self.assertLess(run.workers, 7)
        self.assertEqual(run.gate.limit, run.workers)

    def test_a_settled_run_is_remembered_and_the_next_run_starts_from_it(self) -> None:
        first = self.runtime(preferences=TUNE).begin("export", total_units=100000, destination=self._tmp.name)
        for _ in range(60):
            first.tuner.observe_rate(10.0 * min(first.workers, 5))
        report = first.finish()
        self.assertEqual(report.mode, "auto")
        self.assertIn(report.workers, (5, 6))
        self.assertLess(report.workers, 7, "the cheapest count within noise of the best")
        second = self.runtime(preferences=TUNE).begin("export", total_units=100000, destination=self._tmp.name)
        self.assertEqual(second.workers, report.workers)
        self.assertTrue(second.finish().started_from_learned)

    def _kind(self) -> str:
        return probe_destination(self._tmp.name).key()

    def test_standard_mode_then_runs_at_the_saved_count_without_searching(self) -> None:
        """The user switches tuning on once; afterwards Standard uses what it found (and never searches or overwrites it)."""
        tuning = self.runtime(preferences=TUNE).begin("export", total_units=100000, destination=self._tmp.name)
        for _ in range(60):
            tuning.tuner.observe_rate(10.0 * min(tuning.workers, 5))
        saved = tuning.finish().workers
        self.assertLess(saved, 7)
        standard = self.runtime().begin("export", total_units=100000, destination=self._tmp.name)
        self.assertEqual((standard.workers, standard.maximum), (saved, saved))
        standard.tuner.observe_rate(1.0)  # nothing is searched
        self.assertEqual(standard.workers, saved)
        report = standard.finish()
        self.assertEqual((report.mode, report.workers), ("saved", saved))
        self.assertEqual(self.store.lookup(machine().fingerprint(), "export", self._kind()).workers, saved, "Standard never overwrites it")

    def test_forgetting_the_saved_counts_returns_standard_to_every_core_but_one(self) -> None:
        runtime = self.runtime(preferences=TUNE)
        tuning = runtime.begin("export", total_units=100000, destination=self._tmp.name)
        for _ in range(60):
            tuning.tuner.observe_rate(10.0 * min(tuning.workers, 5))
        tuning.finish()
        self.assertEqual(len(runtime.learned_all()), 1)
        self.assertEqual(runtime.forget_learned(), 1)
        self.assertEqual(runtime.learned_all(), [])
        runtime.set_preferences(PerfPreferences())
        self.assertEqual(runtime.begin("export", total_units=100000, destination=self._tmp.name).workers, 7)

    def test_forgetting_leaves_other_machines_alone(self) -> None:
        self.store.record("some-other-machine", "export", "", workers=3, rate=1.0)
        self.store.record(machine().fingerprint(), "export", "", workers=5, rate=1.0)
        self.assertEqual(self.runtime().forget_learned(), 1)
        self.assertIsNotNone(self.store.lookup("some-other-machine", "export", ""))

    def test_a_run_that_never_settled_teaches_nothing(self) -> None:
        run = self.runtime(preferences=TUNE).begin("export", total_units=100000)
        run.tuner.observe_rate(10.0)
        run.tuner.observe_rate(10.0)
        run.finish()
        self.assertIsNone(self.store.lookup(machine().fingerprint(), "export", ""))

    def test_manual_mode_is_not_tuned_and_not_remembered(self) -> None:
        runtime = self.runtime(preferences=PerfPreferences(mode=MODE_MANUAL, manual_workers=3))
        run = runtime.begin("export", total_units=100000)
        self.assertEqual((run.workers, run.finish().mode), (3, "manual"))
        self.assertIsNone(self.store.lookup(machine().fingerprint(), "export", ""))

    def test_new_preferences_apply_to_the_next_run_only(self) -> None:
        runtime = self.runtime()
        run = runtime.begin("export", total_units=100000)
        runtime.set_preferences(PerfPreferences(mode=MODE_MANUAL, manual_workers=2))
        self.assertEqual(run.workers, 7)
        self.assertEqual(runtime.begin("export", total_units=100000).workers, 2)

    def test_the_process_wide_runtime_can_be_replaced(self) -> None:
        mine = self.runtime()
        set_runtime(mine)
        self.assertIs(get_runtime(), mine)
        set_runtime(None)
        self.assertIsNot(get_runtime(), mine)

    def test_report_fields_are_plain_values_for_the_log(self) -> None:
        fields = self.runtime().begin("export", total_units=5).finish().as_fields()
        self.assertTrue(all(isinstance(value, (str, int, float, bool)) for value in fields.values()), fields)
        self.assertEqual(fields["media"], "-", "a job that writes nowhere has no disk type")

    def test_a_run_that_writes_somewhere_reports_the_disk_type(self) -> None:
        fields = self.runtime().begin("export", total_units=5, destination=self._tmp.name).finish().as_fields()
        self.assertIn(fields["media"], ("ssd", "hdd", "unknown"))


if __name__ == "__main__":
    unittest.main()
