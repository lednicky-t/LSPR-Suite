"""The "Open dataset" entry of the status bar's task indicator (2026-11-20).

A dataset folder of 71,685 TIFFs on a slow drive made the old open look frozen for an hour: no sign of which step was running and
no way to stop it. Now `io.load_dataset` reports each step (`progress`) and checks `cancelled` at every report, and
`DatasetModule.load_dataset_from_folder` turns that into `task_progress` / `task_finished` for the indicator, with `cancel_load`
behind its Cancel button.

Real `DatasetModule` and loader on a tiny TIFF dataset; a real in-process `QApplication`, never `.exec()`. Rewrite-only; skipped
when the `apps/LSPRi/eva` submodule is not on its `rewrite` branch.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from PyQt6.QtWidgets import QApplication

    import lspr_imaging_app.dataset.module as dataset_module_file
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.io import load_dataset
    from lspr_imaging_app.dataset.model import ImageDataset
    from lspr_imaging_app.progress import Cancelled
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QApplication.instance() or QApplication([])

# Small synthetic subset in the real legacy format - not real measurement data.
_META_DATA_TXT = """User: testuser
Date and time: 12.09.2025 12:59:11
Camera
BinningH=4
BinningV=4
MultiplExpTime=1.0
Averaging=1
ImageWidth=1300
ImageHeight=900
XoffsetImage=0
YoffsetImage=0
Measurement
FilterSetTime=100
Wavelength and exposure time camera
defined wl:real wl:exposure time
470:470:210
480:480:220
eof
"""

_MEASURING_TIMES_CSV = (
    "Image;Measuring time [ms];Measuring time [s];Note pump plan\n"
    "imLCTFatWL470Frame0;144;0;Pump is not running\n"
    "imLCTFatWL480Frame0;472;0;Pump is not running\n"
)


def _pump(until, timeout: float = 30.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        _APP.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return until()


class _RunFolderTest(unittest.TestCase):
    """`root/run/` holds the sidecar files and `images/`, the layout of a real dataset: the user points at `run`."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_folder = Path(self._tmp.name) / "run"
        images = self.run_folder / "images"
        images.mkdir(parents=True)
        for wavelength in (470, 480):
            tifffile.imwrite(str(images / f"imLCTFatWL{wavelength}Frame0.tif"), np.zeros((8, 8), dtype=np.uint16))
        (self.run_folder / "measureing_times.csv").write_text(_MEASURING_TIMES_CSV, encoding="utf-8")
        (self.run_folder / "metaData.txt").write_text(_META_DATA_TXT, encoding="utf-8")


class LoaderReportsItsStepsTests(_RunFolderTest):
    def test_every_step_is_reported_in_order_and_the_bar_ends_at_one(self) -> None:
        reports: list[tuple[float, str]] = []
        result = load_dataset(self.run_folder, progress=lambda fraction, message: reports.append((fraction, message)))

        self.assertIsInstance(result, ImageDataset)
        fractions = [fraction for fraction, _ in reports]
        self.assertEqual(fractions, sorted(fractions))  # never backwards
        self.assertTrue(all(0.0 <= fraction <= 1.0 for fraction in fractions))
        self.assertEqual(fractions[-1], 1.0)
        messages = [message for _, message in reports]
        self.assertTrue(any(message.startswith("looking for images in run") for message in messages), messages)
        self.assertIn("found 2 images in images", messages)
        self.assertTrue(any(message.startswith("searching for metaData.txt") for message in messages), messages)
        self.assertTrue(any(message.startswith("reading measureing_times.csv") for message in messages), messages)
        self.assertEqual(messages[-1], "acquisition metadata read")

    def test_a_folder_with_no_sidecar_files_says_so(self) -> None:
        (self.run_folder / "measureing_times.csv").unlink()
        (self.run_folder / "metaData.txt").unlink()
        reports: list[str] = []
        load_dataset(self.run_folder, progress=lambda _fraction, message: reports.append(message))
        # Only meaningful when no ancestor of the temp folder happens to hold such files either.
        self.assertIn(reports[-1], {"no acquisition metadata found", "acquisition metadata read"})

    def test_cancelled_from_the_start_stops_before_anything_is_read(self) -> None:
        reports: list[str] = []
        with self.assertRaises(Cancelled):
            load_dataset(self.run_folder, progress=lambda _f, message: reports.append(message), cancelled=lambda: True)
        self.assertEqual(reports, [])

    def test_a_cancel_during_the_metadata_search_stops_at_the_next_step(self) -> None:
        state = {"cancel": False}
        reports: list[str] = []

        def progress(_fraction: float, message: str) -> None:
            reports.append(message)
            if message.startswith("searching for metaData.txt"):
                state["cancel"] = True  # the user presses Cancel while the search runs

        with self.assertRaises(Cancelled):
            load_dataset(self.run_folder, progress=progress, cancelled=lambda: state["cancel"])
        self.assertFalse(any(message.startswith("reading measureing_times.csv") for message in reports), reports)


class ModuleReportsToTheStatusBarTests(_RunFolderTest):
    def setUp(self) -> None:
        super().setUp()
        self.module = DatasetModule()
        self.events: list[tuple] = []
        self.progress: list[tuple[str, str, float, str]] = []
        self.module.task_progress.connect(lambda *args: self.progress.append(args))
        self.module.task_finished.connect(lambda *args: self.events.append(("finished", *args)))
        self.module.dataset_loaded.connect(lambda _dataset: self.events.append(("loaded",)))
        self.module.dataset_load_failed.connect(lambda message: self.events.append(("failed", message)))
        self.module.dataset_choice_needed.connect(lambda _choice: self.events.append(("choice",)))

    def _settled(self) -> bool:
        return not self.module.is_loading()

    def test_a_load_reports_steps_and_finishes_before_the_dataset_arrives(self) -> None:
        self.module.load_dataset_from_folder(self.run_folder)
        self.assertTrue(_pump(lambda: any(event[0] == "loaded" for event in self.events)))

        self.assertTrue(self.progress)
        self.assertEqual({(task_id, label) for task_id, label, _f, _m in self.progress}, {("dataset_load", "Open dataset")})
        self.assertEqual(self.progress[0][3], "starting (run)")
        self.assertTrue(all(fraction <= 1.0 for _id, _label, fraction, _msg in self.progress))
        self.assertEqual([event[0] for event in self.events], ["finished", "loaded"])  # the task ends, then the dataset arrives
        self.assertEqual(self.events[0], ("finished", "dataset_load", "completed", "2 images"))

    def test_a_folder_without_a_dataset_ends_the_task_as_failed_and_still_raises_the_dialog_signal(self) -> None:
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        self.module.load_dataset_from_folder(empty)
        self.assertTrue(_pump(lambda: any(event[0] == "failed" for event in self.events)))

        kinds = [event[0] for event in self.events]
        self.assertEqual(kinds, ["finished", "failed"])
        self.assertEqual(self.events[0][1:3], ("dataset_load", "failed"))
        self.assertIn("No TIFF image stack", self.events[0][3])
        self.assertEqual(self.events[0][3], self.events[1][1])  # the same message goes to the dialog

    def test_cancel_ends_the_task_as_cancelled_with_no_dataset_and_no_error_dialog(self) -> None:
        started = threading.Event()

        def stuck_load(_folder, *, progress, cancelled):
            progress(0.0, "looking for images in run")
            started.set()
            while not cancelled():  # a load that is still working when the user presses Cancel
                time.sleep(0.005)
            raise Cancelled()

        with mock.patch.object(dataset_module_file, "load_dataset", stuck_load):
            self.module.load_dataset_from_folder(self.run_folder)
            self.assertTrue(started.wait(10.0))
            self.module.cancel_load()
            self.assertTrue(_pump(lambda: any(event[0] == "finished" for event in self.events)))

        self.assertEqual(self.events, [("finished", "dataset_load", "cancelled", "")])
        self.assertTrue(_pump(self._settled))  # the worker is free again: a new Open works at once

    def test_cancel_with_nothing_running_does_nothing(self) -> None:
        self.module.cancel_load()
        self.assertFalse(self.module._worker.cancel_event.is_set())
        self.module.load_dataset_from_folder(self.run_folder)  # and the next load is not born cancelled
        self.assertTrue(_pump(lambda: any(event[0] == "loaded" for event in self.events)))


if __name__ == "__main__":
    unittest.main()
