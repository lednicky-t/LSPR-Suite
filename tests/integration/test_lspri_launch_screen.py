"""The launch screen and the launch's stage timing (`launch.py`), plus `build_main_window(progress=...)`."""

from __future__ import annotations

import logging
import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.launch import LaunchSplash, StageClock  # noqa: E402


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class StageClockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = _Capture()
        self.logger = logging.getLogger("lspr_imaging_app.launch")
        self._old_level = self.logger.level
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(self.handler)
        self.addCleanup(self.logger.removeHandler, self.handler)
        self.addCleanup(self.logger.setLevel, self._old_level)

    def _stages(self) -> list[str]:
        return [r.lspr_fields["stage"] for r in self.handler.records if getattr(r, "lspr_event", "") == "app.build_stage"]

    def test_each_done_logs_one_stage_and_the_message_goes_to_the_screen(self) -> None:
        shown: list[str] = []
        clock = StageClock(shown.append)
        clock.done("modules", "Building panels...")
        clock.done("panels")  # no message: the screen keeps the last one
        clock.finish()
        self.assertEqual(self._stages(), ["modules", "panels", "total"])
        self.assertEqual(shown, ["Building panels..."])
        for record in self.handler.records:
            self.assertGreaterEqual(record.lspr_fields["ms"], 0)

    def test_without_a_screen_it_only_times(self) -> None:
        clock = StageClock()
        clock.done("x", "ignored")
        self.assertEqual(self._stages(), ["x"])


class LaunchSplashTests(unittest.TestCase):
    def test_shows_a_message_then_closes_with_the_window(self) -> None:
        splash = LaunchSplash()
        splash.show("Loading modules...")
        inner = splash._splash
        self.assertIsNotNone(inner)
        self.assertTrue(inner.isVisible())
        self.assertIn("Loading modules...", inner.message())
        splash.show("Building the Image panel...")
        self.assertIn("Image panel", inner.message())
        window = QtWidgets.QMainWindow()
        window.show()
        splash.finish(window)
        self.assertFalse(inner.isVisible())
        splash.show("after finish")  # a finished screen ignores further messages instead of failing
        window.close()


class BuildMainWindowProgressTests(unittest.TestCase):
    def test_the_window_reports_its_main_steps(self) -> None:
        from lspr_imaging_app.app_rewrite import build_main_window

        shown: list[str] = []
        window = build_main_window(progress=shown.append)
        try:
            self.assertIn("Building the Image panel...", shown)
            self.assertEqual(shown[-1], "Opening the last dataset...")
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
