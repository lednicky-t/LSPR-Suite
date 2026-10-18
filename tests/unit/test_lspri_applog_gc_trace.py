"""`applog.gc_trace.GcTrace`: where the cyclic garbage collector runs. No Qt, no files."""

from __future__ import annotations

import gc
import sys
import threading
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.applog.gc_trace import GcTrace
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

LOGGER = "lspr_imaging_app.applog.gc_trace"


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _fields(logs) -> dict:
    record = logs.records[-1]
    return record.lspr_fields


class GcTraceTests(unittest.TestCase):
    def test_callback_numbers_become_one_summary_record(self) -> None:
        clock = _Clock()
        trace = GcTrace(clock=clock)
        for generation, pause, collected in ((0, 0.002, 5), (2, 0.030, 40)):
            trace._on_gc("start", {"generation": generation})
            clock.now += pause
            trace._on_gc("stop", {"generation": generation, "collected": collected, "uncollectable": 0})
        clock.now += 5.0
        with self.assertLogs(LOGGER, level="DEBUG") as logs:
            self.assertTrue(trace.flush())
        fields = _fields(logs)
        self.assertEqual(fields["collections"], 2)
        self.assertEqual(fields["full"], 1)
        self.assertEqual(fields["collected"], 45)
        self.assertAlmostEqual(fields["total_ms"], 32.0, places=1)
        self.assertAlmostEqual(fields["max_ms"], 30.0, places=1)
        self.assertEqual(fields["off_main"], 0)  # the test thread is the main thread here
        self.assertEqual(fields["off_main_threads"], "")

    def test_nothing_to_report_logs_nothing(self) -> None:
        trace = GcTrace()
        with self.assertNoLogs(LOGGER, level="DEBUG"):
            self.assertFalse(trace.flush())

    def test_flush_starts_a_new_period(self) -> None:
        clock = _Clock()
        trace = GcTrace(clock=clock)
        trace._on_gc("start", {})
        trace._on_gc("stop", {"generation": 0})
        with self.assertLogs(LOGGER, level="DEBUG"):
            trace.flush()
        with self.assertNoLogs(LOGGER, level="DEBUG"):
            self.assertFalse(trace.flush())

    def test_a_real_collection_on_a_worker_thread_is_attributed_to_it(self) -> None:
        trace = GcTrace()
        trace.install()
        self.addCleanup(trace.uninstall)

        def make_cycles_and_collect() -> None:
            for _ in range(50):
                a: list = []
                b = [a]
                a.append(b)
            gc.collect()

        worker = threading.Thread(target=make_cycles_and_collect, name="TestWorker")
        worker.start()
        worker.join()
        gc.collect()  # on this (main) thread
        with self.assertLogs(LOGGER, level="DEBUG") as logs:
            self.assertTrue(trace.flush())
        fields = _fields(logs)
        self.assertGreaterEqual(fields["off_main"], 1)
        self.assertIn("TestWorker=", fields["off_main_threads"])
        self.assertGreaterEqual(fields["collections"], 2)

    def test_install_is_idempotent_and_uninstall_removes_the_callback(self) -> None:
        trace = GcTrace()
        before = list(gc.callbacks)
        trace.install()
        trace.install()
        self.assertEqual(len(gc.callbacks), len(before) + 1)
        trace.uninstall()
        trace.uninstall()
        self.assertEqual(list(gc.callbacks), before)


if __name__ == "__main__":
    unittest.main()
