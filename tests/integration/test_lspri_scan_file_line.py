"""`analysis.scan_file_line.ScanFileLine` (2026-11-28): the Workflow panel's line about scan.h5 is read on a thread and shown on the GUI thread.
The read is faked and held back with a `threading.Event` (no sleeping, no timing); `scan_store.describe_file` itself is tested in `test_lspri_analysis_scan_row.py`."""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.analysis.scan_file_line import NO_SCAN_TEXT, READING_TEXT, ScanFileLine  # noqa: E402


def _wait_for(predicate, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class ScanFileLineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.shown: list[str] = []
        self.current: Path | None = None  # set in setUp below, once the temporary folder exists
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        self.read_on: list[str] = []

        def describe(path: Path) -> str | None:
            self.read_on.append(threading.current_thread().name)
            self.release.wait(10.0)
            return f"{path.parent.name}: 3 of 3 cubes saved" if path.name == "scan.h5" else None

        self.line = ScanFileLine(self.shown.append, lambda: self.current, describe=describe)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.current = Path(self.folder.name) / "a" / "scan.h5"

    def _file(self, name: str = "scan.h5", folder: str = "a") -> Path:
        path = Path(self.folder.name) / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return path

    def test_no_path_says_no_scan_at_once(self) -> None:
        self.line.show(None)
        self.assertEqual(self.shown, [NO_SCAN_TEXT])

    def test_the_file_is_read_on_another_thread_and_the_line_arrives_after(self) -> None:
        self.line.show(self._file())
        self.assertEqual(self.shown, [READING_TEXT])  # the GUI thread did not wait for the read
        self.release.set()
        self.assertTrue(_wait_for(lambda: len(self.shown) == 2))
        self.assertEqual(self.shown[-1], "a: 3 of 3 cubes saved")
        self.assertEqual(self.read_on, ["scan-file-line"])

    def test_a_path_without_a_file_says_no_scan_at_once_and_reads_nothing(self) -> None:
        self.line.show(Path(self.folder.name) / "nothing" / "scan.h5")
        self.assertEqual(self.shown, [NO_SCAN_TEXT])
        self.assertEqual(self.read_on, [])

    def test_a_file_the_description_cannot_make_a_line_of_reads_as_no_scan(self) -> None:
        other = self._file("other.h5")
        self.current = other
        self.line.show(other)
        self.release.set()
        self.assertTrue(_wait_for(lambda: len(self.shown) == 2))
        self.assertEqual(self.shown[-1], NO_SCAN_TEXT)

    def test_a_line_for_a_session_that_was_left_is_dropped(self) -> None:
        self.line.show(self._file())
        self.current = Path("b/scan.h5")  # another session was opened while the file was being read
        self.release.set()
        _wait_for(lambda: False, seconds=0.3)
        self.assertEqual(self.shown, [READING_TEXT])

    def test_only_the_newest_request_is_shown(self) -> None:
        self.line.show(self._file())
        self.line.show(None)  # newer: "No scan yet."
        self.release.set()
        _wait_for(lambda: False, seconds=0.3)
        self.assertEqual(self.shown, [READING_TEXT, NO_SCAN_TEXT])

    def test_a_failing_read_ends_as_no_scan_instead_of_hanging(self) -> None:
        def broken(_path: Path) -> str | None:
            raise OSError("disk went away")

        line = ScanFileLine(self.shown.append, lambda: self.current, describe=broken)
        line.show(self._file())
        self.assertTrue(_wait_for(lambda: len(self.shown) == 2))
        self.assertEqual(self.shown[-1], NO_SCAN_TEXT)


if __name__ == "__main__":
    unittest.main()
