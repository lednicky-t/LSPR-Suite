"""`tests/_qt_cleanup.py`: after a test, the main windows it created are destroyed; nothing else is touched.

Each case runs a small inner test through the real `TestCase.run` (that is where the hook sits) and looks at what is left afterwards.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from PyQt6 import QtWidgets, sip

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests import _qt_cleanup  # noqa: E402  (importing the tests package also installs the hook)


MADE: dict = {}  # the widgets the inner test built, for the outer test to look at afterwards


def _make_widgets() -> None:
    MADE.clear()
    MADE.update(window=QtWidgets.QMainWindow(), panel=QtWidgets.QWidget(), dialog=QtWidgets.QDialog())
    for widget in MADE.values():
        widget.show()


def _run_inner_test() -> None:
    """Run a small test that builds widgets through the real `TestCase.run` (that is where the hook sits), reporting nowhere."""
    result = unittest.TestResult()
    unittest.FunctionTestCase(_make_widgets).run(result)
    assert result.wasSuccessful(), result.errors + result.failures


def _dispose(*widgets) -> None:
    for widget in widgets:
        widget.close()
        widget.deleteLater()


class DestroysMainWindowsTests(unittest.TestCase):
    def test_a_main_window_a_test_made_is_destroyed_when_the_test_is_over(self) -> None:
        _run_inner_test()
        self.assertTrue(sip.isdeleted(MADE["window"]))

    def test_other_top_level_widgets_are_left_alone(self) -> None:
        _run_inner_test()
        self.assertFalse(sip.isdeleted(MADE["panel"]))
        self.assertFalse(sip.isdeleted(MADE["dialog"]))
        _dispose(MADE["panel"], MADE["dialog"])

    def test_a_main_window_that_existed_before_the_test_is_kept(self) -> None:
        existing = QtWidgets.QMainWindow()  # built here, like a setUpClass or module level window
        existing.show()
        _run_inner_test()
        self.assertFalse(sip.isdeleted(existing))
        _dispose(existing, MADE["panel"], MADE["dialog"])

    def test_the_hook_is_off_when_asked(self) -> None:
        with mock.patch.dict(os.environ, {"LSPR_TEST_KEEP_WINDOWS": "1"}):
            _run_inner_test()
        self.assertFalse(sip.isdeleted(MADE["window"]))
        _dispose(*MADE.values())

    def test_installing_twice_wraps_once(self) -> None:
        before = unittest.TestCase.run
        _qt_cleanup.install()
        self.assertIs(unittest.TestCase.run, before)

    def test_a_process_with_only_a_core_application_is_left_alone(self) -> None:
        # test_live_visual_refresh_scheduling runs on a QCoreApplication, which has no top-level widgets to look at
        import subprocess
        import sys

        code = (
            "import tests, unittest\n"
            "from PyQt6.QtCore import QCoreApplication\n"
            "from PyQt6 import QtWidgets\n"
            "app = QCoreApplication([])\n"
            "result = unittest.TestResult()\n"
            "unittest.FunctionTestCase(lambda: None).run(result)\n"
            "assert result.wasSuccessful(), result.errors\n"
        )
        done = subprocess.run([sys.executable, "-c", code], cwd=_REPO, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_pure_logic_test_file_never_loads_qt_because_of_the_hook(self) -> None:
        # the hook looks Qt up in sys.modules; it must not import it itself when the test file did not
        import subprocess
        import sys

        code = "import tests, sys; assert 'PyQt6.QtWidgets' not in sys.modules, 'tests/__init__ imported Qt'"
        done = subprocess.run([sys.executable, "-c", code], cwd=_REPO, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)


_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


if __name__ == "__main__":
    unittest.main()
