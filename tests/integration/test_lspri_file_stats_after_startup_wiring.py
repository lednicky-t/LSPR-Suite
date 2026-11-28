"""`launch_wiring.wire_file_stats_after_startup` (2026-11-28): the dataset's file count is remembered in the saved UI state and held back
until the start-up has ended, whatever the outcome. The start-up tracker is a stand-in with the same signal; the count is faked."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QObject, pyqtSignal

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app import launch_wiring  # noqa: E402
from lspr_imaging_app.dataset import module as dataset_module_file  # noqa: E402
from lspr_imaging_app.dataset.module import DatasetModule  # noqa: E402
from lspr_imaging_app.panels.ui_state import UiStateStore  # noqa: E402
from lspr_imaging_app.storage.ui_state_keys import FILE_STATS, read_file_stats  # noqa: E402

FOLDER = Path("D:/data/run1")


class _Startup(QObject):
    task_finished = pyqtSignal(str, str, str)  # the tracker's signal: task id, outcome, message


def _wait_for(predicate, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class FileStatsAfterStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls = 0

        def counted(_dataset: object) -> tuple[int, float | None]:
            self.calls += 1
            return 700, 5.0

        patcher = mock.patch.object(dataset_module_file, "dataset_file_stats", counted)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dataset = SimpleNamespace(is_ome_zarr=False, folder=FOLDER, records=[], home=FOLDER)
        self.module = DatasetModule()
        self.startup = _Startup()
        self.store = UiStateStore({})
        with mock.patch.object(launch_wiring.QTimer, "singleShot"):  # the 60 s fallback is not waited for
            launch_wiring.wire_file_stats_after_startup(self.module, self.startup, self.store)  # type: ignore[arg-type]
        self.module.load_dataset(self.dataset)  # type: ignore[arg-type]
        self.module.dataset_file_stats()  # the info bar asks when the dataset is loaded

    def test_the_count_waits_for_the_end_of_the_startup(self) -> None:
        _wait_for(lambda: False, seconds=0.2)
        self.assertEqual(self.calls, 0)
        self.startup.task_finished.emit("app.startup", "completed", "ready")
        self.assertTrue(_wait_for(lambda: self.calls == 1))

    def test_a_failed_or_cancelled_startup_also_lets_it_run(self) -> None:
        self.startup.task_finished.emit("app.startup", "failed", "could not open")
        self.assertTrue(_wait_for(lambda: self.calls == 1))

    def test_the_finished_count_is_saved_and_read_back_by_the_next_launch(self) -> None:
        self.startup.task_finished.emit("app.startup", "completed", "ready")
        self.assertTrue(_wait_for(lambda: read_file_stats(self.store, str(FOLDER)) == (700, 5.0)))
        self.assertIn(str(FOLDER), self.store.get(FILE_STATS))  # type: ignore[operator]
        # the next launch: a new module, the same saved state; the size is there before any count
        again = DatasetModule()
        with mock.patch.object(launch_wiring.QTimer, "singleShot"):
            launch_wiring.wire_file_stats_after_startup(again, _Startup(), self.store)  # type: ignore[arg-type]
        again.load_dataset(self.dataset)  # type: ignore[arg-type]
        self.assertEqual(again.dataset_file_stats(), (700, 5.0))


if __name__ == "__main__":
    unittest.main()
