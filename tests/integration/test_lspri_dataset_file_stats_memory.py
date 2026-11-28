"""The dataset's size is remembered between launches and counted after start-up (`DatasetModule.set_file_stats_memory`,
`hold_file_stats_walk`, 2026-11-28). Counting a big OME-Zarr takes ~21 s even alone, so the last count is shown at once, the new one
replaces it, and the count waits while the app starts. The count is faked (a `threading.Event`), no sleeping, no timing."""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.dataset import module as dataset_module_file  # noqa: E402
from lspr_imaging_app.dataset.module import DatasetModule  # noqa: E402

FOLDER = Path("D:/data/run1")


def _wait_for(predicate, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class _Memory:
    """Stands in for the app's saved UI state."""

    def __init__(self, saved: dict[str, tuple[int, float | None]] | None = None) -> None:
        self.saved = dict(saved or {})
        self.writes: list[tuple[str, tuple[int, float | None]]] = []

    def recall(self, folder: str):
        return self.saved.get(folder)

    def remember(self, folder: str, stats) -> None:
        self.saved[folder] = stats
        self.writes.append((folder, stats))


class FileStatsMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = SimpleNamespace(is_ome_zarr=False, folder=FOLDER, records=[], home=FOLDER)
        self.module = DatasetModule()
        self.module.load_dataset(self.dataset)  # type: ignore[arg-type]
        self.ready: list[object] = []
        self.module.file_stats_ready.connect(self.ready.append)
        self.release = threading.Event()
        self.calls = 0

        def counted(_dataset: object) -> tuple[int, float | None]:
            self.calls += 1
            self.release.wait(10.0)
            return 700, 5.0

        patcher = mock.patch.object(dataset_module_file, "dataset_file_stats", counted)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.release.set)

    def test_the_remembered_size_is_answered_at_once_and_replaced_by_the_new_count(self) -> None:
        memory = _Memory({str(FOLDER): (500, 1.0)})
        self.module.set_file_stats_memory(memory.recall, memory.remember)
        self.assertEqual(self.module.dataset_file_stats(), (500, 1.0))  # the count is still running
        self.assertEqual(self.ready, [])
        self.release.set()
        self.assertTrue(_wait_for(lambda: bool(self.ready)))
        self.assertEqual(self.module.dataset_file_stats(), (700, 5.0))
        self.assertEqual(memory.writes, [(str(FOLDER), (700, 5.0))])

    def test_nothing_remembered_answers_none_like_before(self) -> None:
        memory = _Memory()
        self.module.set_file_stats_memory(memory.recall, memory.remember)
        self.assertIsNone(self.module.dataset_file_stats())

    def test_a_held_count_does_not_start_until_released(self) -> None:
        memory = _Memory({str(FOLDER): (500, 1.0)})
        self.module.set_file_stats_memory(memory.recall, memory.remember)
        self.module.hold_file_stats_walk(True)
        self.assertEqual(self.module.dataset_file_stats(), (500, 1.0))  # the remembered size, no thread
        _wait_for(lambda: False, seconds=0.2)
        self.assertEqual(self.calls, 0)
        self.module.hold_file_stats_walk(False)  # the start-up has ended
        self.assertTrue(_wait_for(lambda: self.calls == 1))
        self.release.set()
        self.assertTrue(_wait_for(lambda: bool(self.ready)))
        self.assertEqual(self.calls, 1)  # one count, however often it was asked
        self.assertEqual(self.module.dataset_file_stats(), (700, 5.0))

    def test_a_held_count_without_memory_shows_nothing_and_counts_after_release(self) -> None:
        self.module.hold_file_stats_walk(True)
        self.assertIsNone(self.module.dataset_file_stats())
        self.module.hold_file_stats_walk(False)
        self.release.set()
        self.assertTrue(_wait_for(lambda: bool(self.ready)))
        self.assertEqual(self.module.dataset_file_stats(), (700, 5.0))

    def test_releasing_with_no_dataset_wanted_starts_nothing(self) -> None:
        self.module.hold_file_stats_walk(True)
        self.module.hold_file_stats_walk(False)
        _wait_for(lambda: False, seconds=0.2)
        self.assertEqual(self.calls, 0)  # nobody asked for the size

    def test_a_failed_count_does_not_overwrite_a_good_remembered_one(self) -> None:
        memory = _Memory({str(FOLDER): (500, 1.0)})
        self.module.set_file_stats_memory(memory.recall, memory.remember)

        def broken(_dataset: object) -> tuple[int, float | None]:
            raise OSError("disk went away")

        with mock.patch.object(dataset_module_file, "dataset_file_stats", broken):
            self.module.dataset_file_stats()
            self.assertTrue(_wait_for(lambda: bool(self.ready)))
        self.assertEqual(memory.writes, [])
        self.assertEqual(memory.saved[str(FOLDER)], (500, 1.0))


if __name__ == "__main__":
    unittest.main()
