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
            event = result_queue.get(timeout=5.0)
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
        self.assertIsNotNone(processing_queue.get(timeout=5.0).result)
        recording_event = recording_queue.get(timeout=5.0)
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


if __name__ == "__main__":
    unittest.main()
