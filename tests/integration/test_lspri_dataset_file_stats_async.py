"""The dataset's size / date are counted on a background thread (`DatasetModule.dataset_file_stats`, 2026-10-24).

An OME-Zarr walk took ~3.5 s on the GUI thread right after a load. These tests pin: the call never blocks, `file_stats_ready` carries the
dataset when the count is in, the value is then cached, and a count that outlives its dataset is dropped. The count itself is held back
with a `threading.Event` (no sleeping, no timing).
"""

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

import tempfile  # noqa: E402

from lspr_imaging_app.dataset import module as dataset_module_file  # noqa: E402
from lspr_imaging_app.dataset.module import DatasetModule  # noqa: E402


def _wait_for(predicate, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class FileStatsAsyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        files = []
        for index in range(3):
            path = Path(self._tmp.name) / f"img{index}.tif"
            path.write_bytes(b"x" * (100 * (index + 1)))
            files.append(SimpleNamespace(path=path))
        # A TIFF-stack-shaped stand-in: `dataset.io.dataset_file_stats` only reads `is_ome_zarr`, `folder` and `records[].path`.
        self.dataset = SimpleNamespace(is_ome_zarr=False, folder=Path(self._tmp.name), records=files, home=Path(self._tmp.name))
        self.module = DatasetModule()
        self.module.load_dataset(self.dataset)  # type: ignore[arg-type]
        self.ready: list[object] = []
        self.module.file_stats_ready.connect(self.ready.append)

    def test_first_ask_returns_none_then_the_signal_then_the_cached_value(self) -> None:
        self.assertIsNone(self.module.dataset_file_stats())
        self.assertTrue(_wait_for(lambda: bool(self.ready)))
        self.assertIs(self.ready[0], self.dataset)
        stats = self.module.dataset_file_stats()
        self.assertEqual(stats[0], 600)  # 100 + 200 + 300 bytes
        self.assertIsNotNone(stats[1])
        self.assertEqual(len(self.ready), 1)  # asking again neither recounts nor re-emits
        self.module.dataset_file_stats()
        _APP.processEvents()
        self.assertEqual(len(self.ready), 1)

    def test_the_call_does_not_wait_for_a_slow_count(self) -> None:
        release = threading.Event()
        started = threading.Event()

        def slow_count(_dataset: object) -> tuple[int, float | None]:
            started.set()
            release.wait(10.0)
            return 42, None

        with mock.patch.object(dataset_module_file, "dataset_file_stats", slow_count):
            self.assertIsNone(self.module.dataset_file_stats())
            self.assertTrue(started.wait(5.0))
            self.assertIsNone(self.module.dataset_file_stats())  # still counting: no second thread, still None
            self.assertEqual(self.ready, [])
            release.set()
            self.assertTrue(_wait_for(lambda: bool(self.ready)))
        self.assertEqual(self.module.dataset_file_stats(), (42, None))

    def test_a_count_that_outlives_its_dataset_is_dropped(self) -> None:
        release = threading.Event()
        started = threading.Event()

        def slow_count(_dataset: object) -> tuple[int, float | None]:
            started.set()
            release.wait(10.0)
            return 42, None

        with mock.patch.object(dataset_module_file, "dataset_file_stats", slow_count):
            self.module.dataset_file_stats()
            self.assertTrue(started.wait(5.0))
            self.module.clear_dataset()
            release.set()
            _wait_for(lambda: False, seconds=0.3)  # let the queued hand-off arrive
        self.assertEqual(self.ready, [])
        self.assertIsNone(self.module.dataset_file_stats())  # no dataset: nothing to count

    def test_a_failing_count_ends_with_zero_instead_of_hanging(self) -> None:
        def broken(_dataset: object) -> tuple[int, float | None]:
            raise OSError("disk went away")

        with mock.patch.object(dataset_module_file, "dataset_file_stats", broken):
            self.module.dataset_file_stats()
            self.assertTrue(_wait_for(lambda: bool(self.ready)))
        self.assertEqual(self.module.dataset_file_stats(), (0, None))


class InfoBarShowsSizeWhenItArrivesTests(unittest.TestCase):
    def test_the_size_reads_dots_first_and_the_real_size_after_the_count(self) -> None:
        from lspr_imaging_app.panels.dataset_info_bar import DatasetInfoBar

        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "a.tif"
            image.write_bytes(b"x" * 2048)
            dataset = SimpleNamespace(
                is_ome_zarr=False, folder=Path(tmp), home=Path(tmp), records=[SimpleNamespace(path=image)],
                spectral_cube_indices=[0], wavelengths_nm=[500.0], acquisition_metadata=None,
            )
            module = DatasetModule()
            host = QtWidgets.QWidget()
            bar = DatasetInfoBar(module, host)
            release = threading.Event()
            real_count = dataset_module_file.dataset_file_stats

            def held_count(target: object) -> tuple[int, float | None]:
                release.wait(10.0)
                return real_count(target)  # type: ignore[arg-type]

            with mock.patch.object(dataset_module_file, "dataset_file_stats", held_count):
                module.load_dataset(dataset)  # type: ignore[arg-type]
                self.assertIn("...", bar._stats_label.text())
                release.set()
                self.assertTrue(_wait_for(lambda: "..." not in bar._stats_label.text()))
            self.assertIn("2.0 KB", bar._stats_label.text().replace("KiB", "KB"))
            host.close()


if __name__ == "__main__":
    unittest.main()
