"""Tests for the Sensorgram's helper process (`analysis/trace_pool.py`, 2026-10-24). The process is really started (Windows "spawn"), so this file takes a
few seconds; no Qt, no files. Pinned: the traces from the process equal the in-thread ones; progress and cancel; a fit error is raised to the caller, not
swallowed; a process that cannot be used falls back to computing in the calling thread, with a WARNING, and gives up for the session after a second failure."""

from __future__ import annotations

import logging
import sys
import unittest
from concurrent.futures import BrokenExecutor
from unittest import mock

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import trace_pool
    from lspr_imaging_app.analysis.settings import MetricSettings
    from lspr_imaging_app.analysis.trace_fit import TraceInput, trace_from_input
    from tests.unit.test_lspri_analysis_trace_fit import make_input
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402


def jobs(n: int = 5) -> list[tuple[int, TraceInput]]:
    return [(roi_id, make_input(n_cube=12, seed=roi_id)) for roi_id in range(1, n + 1)]


class TracePoolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pool = trace_pool.TracePool()
        cls.pool.warm_up()  # the process starts once for the whole class

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pool.shutdown()

    def test_the_process_gives_the_same_traces_as_this_thread(self) -> None:
        metric = MetricSettings(poly_order=4, fit_wl_min=500.0, fit_wl_max=680.0, metric_key="centroid")
        work = jobs()
        done, was_cancelled = self.pool.run(work, "absorbance", metric)
        self.assertFalse(was_cancelled)
        self.assertEqual(sorted(done), [roi_id for roi_id, _ in work])
        for roi_id, data in work:
            np.testing.assert_array_equal(done[roi_id], trace_from_input(data, "absorbance", metric))

    def test_progress_is_reported_per_task_of_several_rois(self) -> None:
        seen: list[tuple[int, int]] = []
        with mock.patch.object(trace_pool, "BATCH_ROWS", 24):  # 12 cubes per ROI: two ROIs per task
            done, _ = self.pool.run(jobs(5), "absorbance", MetricSettings(), progress=lambda d, total: seen.append((d, total)))
        self.assertEqual(seen, [(2, 5), (4, 5), (5, 5)])
        self.assertEqual(len(done), 5)

    def test_the_task_size_does_not_change_the_traces(self) -> None:
        work = jobs(5)
        metric = MetricSettings(fit_method="gaussian")
        whole, _ = self.pool.run(work, "absorbance", metric)
        with mock.patch.object(trace_pool, "BATCH_ROWS", 12):  # one ROI per task
            single, _ = self.pool.run(work, "absorbance", metric)
        for roi_id, _data in work:
            np.testing.assert_array_equal(whole[roi_id], single[roi_id])

    def test_no_jobs_is_nothing(self) -> None:
        self.assertEqual(self.pool.run([], "absorbance", MetricSettings()), ({}, False))

    def test_cancel_returns_what_was_finished(self) -> None:
        work = dict(jobs(20))
        with mock.patch.object(trace_pool, "BATCH_ROWS", 12):  # 20 tasks of one ROI
            done, was_cancelled = self.pool.run(list(work.items()), "absorbance", MetricSettings(), cancelled=lambda: True)  # at the first look
        self.assertTrue(was_cancelled)
        self.assertLess(len(done), 20)
        for roi_id, trace in done.items():  # whatever finished is complete and right
            np.testing.assert_array_equal(trace, trace_from_input(work[roi_id], "absorbance", MetricSettings()))

    def test_an_error_inside_a_fit_reaches_the_caller(self) -> None:
        good = make_input(n_cube=4)
        broken = TraceInput(good.wavelengths[:5], good.sample, good.reference, good.usable, good.scanned)  # shapes that do not fit together
        with self.assertRaises(Exception):
            self.pool.run([(1, broken)], "absorbance", MetricSettings())
        done, _ = self.pool.run(jobs(2), "absorbance", MetricSettings())  # and the process is still usable
        self.assertEqual(len(done), 2)


class FallbackTests(unittest.TestCase):
    def test_a_process_that_cannot_be_used_falls_back_to_this_thread_with_a_warning(self) -> None:
        pool = trace_pool.TracePool()
        work = jobs(3)
        metric = MetricSettings()
        with mock.patch.object(pool, "_get", side_effect=BrokenExecutor("no process")), self.assertLogs(trace_pool.logger, logging.WARNING) as logs:
            done, was_cancelled = pool.run(work, "absorbance", metric)
        self.assertFalse(was_cancelled)
        for roi_id, data in work:
            np.testing.assert_array_equal(done[roi_id], trace_from_input(data, "absorbance", metric))
        self.assertTrue(any("sensorgram.pool_fallback" in line for line in logs.output))

    def test_after_two_failures_the_process_is_not_tried_again(self) -> None:
        pool = trace_pool.TracePool()
        with mock.patch.object(pool, "_get", side_effect=OSError("cannot start")), self.assertLogs(trace_pool.logger, logging.WARNING):
            pool.run(jobs(1), "absorbance", MetricSettings())
            pool.run(jobs(1), "absorbance", MetricSettings())
        self.assertIsNone(pool._get())  # gave up for this session
        done, _ = pool.run(jobs(2), "absorbance", MetricSettings())  # still answers, in this thread
        self.assertEqual(len(done), 2)

    def test_cancel_also_works_in_the_fallback(self) -> None:
        pool = trace_pool.TracePool()
        with mock.patch.object(pool, "_get", side_effect=BrokenExecutor("no process")), self.assertLogs(trace_pool.logger, logging.WARNING):
            done, was_cancelled = pool.run(jobs(5), "absorbance", MetricSettings(), cancelled=lambda: True)
        self.assertTrue(was_cancelled)
        self.assertEqual(done, {})

    def test_a_pool_that_was_shut_down_answers_in_this_thread(self) -> None:
        pool = trace_pool.TracePool()
        pool.shutdown()
        with self.assertLogs(trace_pool.logger, logging.WARNING):
            done, _ = pool.run(jobs(2), "absorbance", MetricSettings())
        self.assertEqual(len(done), 2)


if __name__ == "__main__":
    unittest.main()
