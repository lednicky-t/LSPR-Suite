from __future__ import annotations

import os
import multiprocessing as mp
import queue
import sys
import unittest
from datetime import datetime, timezone
from time import perf_counter

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.models import AcquisitionSettings, ProcessingSettings, Spectrum
from lspr_app.device.simulated import SimulationParameters
from lspr_app.gui.workers import AcquisitionRequest, AcquisitionResult, LiveAcquisitionEvent, LiveAcquisitionWorker, LiveProcessingWorker


def _build_test_spectrum() -> Spectrum:
    wavelengths = np.linspace(500.0, 700.0, 1001, dtype=np.float64)
    values = np.exp(-0.5 * ((wavelengths - 610.0) / 8.0) ** 2)
    return Spectrum(
        wavelengths_nm=wavelengths,
        values=values.astype(np.float64, copy=False),
        y_label="Absorbance",
        acquired_at=datetime.now(timezone.utc),
        metadata={"integration_time_ms": 1.0, "averages": 1},
    )


class LiveProcessingWorkerProcessTest(unittest.TestCase):
    def test_live_processing_worker_processes_one_event(self) -> None:
        ctx = mp.get_context("spawn")
        stop_event = ctx.Event()
        result_queue = ctx.Queue(maxsize=4)
        input_queue = ctx.Queue(maxsize=4)
        worker = LiveProcessingWorker(
            result_queue,
            input_queue,
            stop_event,
            ProcessingSettings(),
            debug_mode_enabled=False,
            log_queue=ctx.Queue(maxsize=8),
        )

        worker.start()
        spectrum = _build_test_spectrum()
        input_queue.put(
            LiveAcquisitionEvent(
                result=AcquisitionResult(
                    spectrum=spectrum,
                    elapsed_ms=1.0,
                    settings=AcquisitionSettings(),
                    source_epoch=1,
                ),
                source_epoch=1,
                produced_at_perf=perf_counter(),
            )
        )

        try:
            event = result_queue.get(timeout=5.0)
        finally:
            stop_event.set()
            worker.join(timeout=5.0)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2.0)

        self.assertIsNotNone(event.result)
        self.assertIsNotNone(event.result.processed)
        self.assertGreaterEqual(event.result.processing_ms, 0.0)
        self.assertGreaterEqual(event.result.queue_wait_ms, 0.0)
        self.assertEqual(event.result.epoch, 1)

    def test_live_processing_worker_relays_child_logs(self) -> None:
        ctx = mp.get_context("spawn")
        stop_event = ctx.Event()
        result_queue = ctx.Queue(maxsize=4)
        input_queue = ctx.Queue(maxsize=4)
        log_queue = ctx.Queue(maxsize=8)
        previous = os.environ.get("LSPR_PROCESSING_SLOW_LOG_MS")
        os.environ["LSPR_PROCESSING_SLOW_LOG_MS"] = "0"
        worker = LiveProcessingWorker(
            result_queue,
            input_queue,
            stop_event,
            ProcessingSettings(),
            debug_mode_enabled=True,
            log_queue=log_queue,
        )

        worker.start()
        spectrum = _build_test_spectrum()
        input_queue.put(
            LiveAcquisitionEvent(
                result=AcquisitionResult(
                    spectrum=spectrum,
                    elapsed_ms=1.0,
                    settings=AcquisitionSettings(),
                    source_epoch=1,
                ),
                source_epoch=1,
                produced_at_perf=perf_counter(),
            )
        )

        try:
            _ = result_queue.get(timeout=5.0)
            records: list[tuple[int, str, str]] = []
            while True:
                try:
                    records.append(log_queue.get_nowait())
                except queue.Empty:
                    break
        finally:
            if previous is None:
                os.environ.pop("LSPR_PROCESSING_SLOW_LOG_MS", None)
            else:
                os.environ["LSPR_PROCESSING_SLOW_LOG_MS"] = previous
            stop_event.set()
            worker.join(timeout=5.0)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2.0)

        self.assertTrue(any("Slow spectrum processing" in message for _, _, message in records))


class LiveAcquisitionWorkerProcessTest(unittest.TestCase):
    def test_live_acquisition_worker_processes_one_event(self) -> None:
        ctx = mp.get_context("spawn")
        stop_event = ctx.Event()
        result_queue = ctx.Queue(maxsize=4)
        processing_queue = ctx.Queue(maxsize=4)
        recording_queue = ctx.Queue(maxsize=4)
        log_queue = ctx.Queue(maxsize=8)
        worker = LiveAcquisitionWorker(
            AcquisitionRequest(
                kind="sample",
                settings=AcquisitionSettings(),
                source_epoch=7,
                archive_enabled=True,
            ),
            result_queue,
            processing_queue,
            recording_queue,
            stop_event,
            source_mode="simulation",
            simulation_parameters=SimulationParameters(wavelength_resolution_nm=1.0),
            debug_mode_enabled=False,
            log_queue=log_queue,
        )

        worker.start()

        try:
            # Drain every queue we need while the worker is still running: it
            # forwards the same event to result_queue, processing_queue, and
            # recording_queue via separate mp.Queue.put() calls that go through
            # an async feeder thread. Stopping/terminating the process before
            # all three have been read can lose whichever puts hadn't finished
            # flushing yet, so read everything first and only then tear down.
            event = result_queue.get(timeout=5.0)
            processing_event = processing_queue.get(timeout=5.0)
            recording_event = recording_queue.get(timeout=5.0)
        finally:
            stop_event.set()
            worker.join(timeout=5.0)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2.0)

        self.assertIsNotNone(event.result)
        self.assertIsNotNone(event.result.spectrum)
        self.assertGreaterEqual(event.result.elapsed_ms, 0.0)
        self.assertEqual(event.result.source_epoch, 7)
        self.assertEqual(event.source_epoch, 7)
        self.assertIsNotNone(processing_event.result)
        self.assertIsNotNone(recording_event.result)
        self.assertEqual(recording_event.source_epoch, 7)

    def test_live_acquisition_worker_relays_child_logs(self) -> None:
        ctx = mp.get_context("spawn")
        stop_event = ctx.Event()
        result_queue = ctx.Queue(maxsize=4)
        processing_queue = ctx.Queue(maxsize=4)
        recording_queue = ctx.Queue(maxsize=4)
        log_queue = ctx.Queue(maxsize=8)
        worker = LiveAcquisitionWorker(
            AcquisitionRequest(
                kind="sample",
                settings=AcquisitionSettings(),
                source_epoch=11,
                archive_enabled=True,
            ),
            result_queue,
            processing_queue,
            recording_queue,
            stop_event,
            source_mode="simulation",
            simulation_parameters=SimulationParameters(wavelength_resolution_nm=1.0),
            debug_mode_enabled=True,
            log_queue=log_queue,
        )

        worker.start()

        try:
            _ = result_queue.get(timeout=5.0)
        finally:
            stop_event.set()
            worker.join(timeout=5.0)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2.0)

        records: list[tuple[int, str, str]] = []
        while True:
            try:
                records.append(log_queue.get_nowait())
            except queue.Empty:
                break

        self.assertTrue(any("Live acquisition backend stopped" in message for _, _, message in records))


class LiveProcessingWorkerDrainTest(unittest.TestCase):
    """The processing worker must skip intermediate frames and process only the
    latest one when multiple frames accumulate in the input queue faster than
    the display rate can consume them."""

    def _run_worker_with_n_frames(self, n_frames: int) -> list:
        """Push *n_frames* into the input queue before the worker starts, then
        collect all results."""
        ctx = mp.get_context("spawn")
        stop_event = ctx.Event()
        result_queue = ctx.Queue(maxsize=n_frames + 4)
        input_queue = ctx.Queue(maxsize=n_frames + 4)
        worker = LiveProcessingWorker(
            result_queue,
            input_queue,
            stop_event,
            ProcessingSettings(),
            debug_mode_enabled=False,
            log_queue=ctx.Queue(maxsize=8),
        )

        spectrum = _build_test_spectrum()
        for i in range(n_frames):
            input_queue.put(
                LiveAcquisitionEvent(
                    result=AcquisitionResult(
                        spectrum=spectrum,
                        elapsed_ms=1.0,
                        settings=AcquisitionSettings(),
                        source_epoch=1,
                    ),
                    source_epoch=1,
                    source_sample_index=i,
                    produced_at_perf=perf_counter(),
                )
            )

        worker.start()
        results = []
        deadline = perf_counter() + 8.0
        try:
            # Collect at most n_frames results (may be fewer due to drain-skip).
            # Keep polling until the deadline — don't break on Empty, because
            # the spawned process takes ~1-2 s to start.
            while perf_counter() < deadline and len(results) < n_frames:
                try:
                    results.append(result_queue.get(timeout=0.2))
                except queue.Empty:
                    if results:
                        # Got at least one result and queue is now empty — done.
                        break
                    # Still waiting for the process to start — keep polling.
        finally:
            stop_event.set()
            worker.join(timeout=5.0)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2.0)
        return results

    def test_single_frame_is_processed(self) -> None:
        results = self._run_worker_with_n_frames(1)
        self.assertEqual(len(results), 1)
        self.assertIsNotNone(results[0].result)
        self.assertIsNotNone(results[0].result.processed)

    def test_multiple_queued_frames_produce_fewer_results(self) -> None:
        # A single pre-queued burst races against process-spawn timing: put()
        # only hands items to an internal feeder thread that flushes them to
        # the OS pipe asynchronously, so "N items queued before start()" does
        # not guarantee the child sees them as one batch -- depending on
        # unrelated OS scheduling it can instead see them trickle in one at a
        # time, with nothing to collapse. (qsize() doesn't help either: it's a
        # put()-side counter, not a "flushed and readable" signal.)
        #
        # Feed frames continuously once the worker is confirmed running instead,
        # faster than it can process them, for a sustained window. This matches
        # the real production scenario the drain-skip logic exists for (a
        # source faster than the display/processing rate) and is not sensitive
        # to how fast the child process happens to spawn.
        ctx = mp.get_context("spawn")
        stop_event = ctx.Event()
        result_queue = ctx.Queue(maxsize=64)
        input_queue = ctx.Queue(maxsize=64)
        worker = LiveProcessingWorker(
            result_queue,
            input_queue,
            stop_event,
            ProcessingSettings(),
            debug_mode_enabled=False,
            log_queue=ctx.Queue(maxsize=8),
        )
        worker.start()

        spectrum = _build_test_spectrum()
        results = []
        frames_sent = 0
        try:
            feed_deadline = perf_counter() + 3.0
            while perf_counter() < feed_deadline:
                try:
                    input_queue.put_nowait(
                        LiveAcquisitionEvent(
                            result=AcquisitionResult(
                                spectrum=spectrum,
                                elapsed_ms=1.0,
                                settings=AcquisitionSettings(),
                                source_epoch=1,
                            ),
                            source_epoch=1,
                            source_sample_index=frames_sent,
                            produced_at_perf=perf_counter(),
                        )
                    )
                    frames_sent += 1
                except queue.Full:
                    pass
                while True:
                    try:
                        results.append(result_queue.get_nowait())
                    except queue.Empty:
                        break
            # Let any result still in flight for the last frame(s) arrive.
            drain_deadline = perf_counter() + 2.0
            while perf_counter() < drain_deadline:
                try:
                    results.append(result_queue.get(timeout=0.2))
                except queue.Empty:
                    if results:
                        break
        finally:
            stop_event.set()
            worker.join(timeout=5.0)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2.0)

        self.assertGreater(frames_sent, 50, "test should have fed a large burst of frames")
        self.assertGreater(len(results), 0, "at least one result must be produced")
        self.assertLess(len(results), frames_sent, "sustained high-rate feed should be collapsed by drain-skip")
        for r in results:
            self.assertIsNotNone(r.result)
            self.assertIsNotNone(r.result.processed)


if __name__ == "__main__":
    unittest.main()
