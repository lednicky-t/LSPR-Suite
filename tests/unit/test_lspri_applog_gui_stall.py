"""`applog.gui_stall.GuiStallWatchdog`: one `gui.stall` record per GUI-thread block, naming where it was. No Qt, no files, fake clock."""

from __future__ import annotations

import sys
import threading
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.applog.gui_stall import GuiStallWatchdog, describe_stack
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

LOGGER = "lspr_imaging_app.applog.gui_stall"
MAIN = 1


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _app_frame(module: str = "lspr_imaging_app.panels.demo"):
    """The frame of a function that lives in a module with the given name (as if it were the application's)."""
    namespace = {"__name__": module}
    exec("def work():\n    import sys\n    return sys._getframe()\n", namespace)  # noqa: S102 - a test fixture, no input
    return namespace["work"]()


def _watchdog(clock: _Clock, frame) -> GuiStallWatchdog:
    return GuiStallWatchdog(MAIN, clock=clock, frames=lambda: {MAIN: frame}, threshold_s=0.3, poll_s=0.1)


class DescribeStackTests(unittest.TestCase):
    def test_names_the_application_function_and_line(self) -> None:
        where, stack = describe_stack(_app_frame())
        self.assertTrue(where.startswith("panels.demo.work:"), where)
        self.assertEqual(stack[0], where)

    def test_outside_the_application_the_innermost_frame_is_named(self) -> None:
        where, stack = describe_stack(_app_frame("somewhere.else"))
        self.assertTrue(where.startswith("somewhere.else.work:"), where)
        self.assertEqual(stack, [])  # no application frame: nothing to list

    def test_no_frame_is_a_question_mark(self) -> None:
        self.assertEqual(describe_stack(None), ("?", []))


class WatchdogTests(unittest.TestCase):
    def test_a_beating_gui_thread_logs_nothing(self) -> None:
        clock = _Clock()
        dog = _watchdog(clock, _app_frame())
        with self.assertNoLogs(LOGGER, "DEBUG"):
            for _ in range(20):
                clock.now += 0.1
                dog.beat()
                dog.check()

    def test_a_short_delay_is_not_a_stall(self) -> None:
        clock = _Clock()
        dog = _watchdog(clock, _app_frame())
        with self.assertNoLogs(LOGGER, "DEBUG"):
            clock.now += 0.25  # under the 0.3 s threshold
            dog.check()
            dog.beat()
            dog.check()

    def test_a_stall_is_reported_once_after_it_ends(self) -> None:
        clock = _Clock()
        dog = _watchdog(clock, _app_frame())
        dog.beat()  # the last beat before the stall, at 100.0
        with self.assertLogs(LOGGER, "DEBUG") as logs:
            for _ in range(4):  # 0.2, 0.4, 0.6, 0.8 s without a beat: three looks are over the 0.3 s threshold
                clock.now += 0.2
                dog.check()
            self.assertFalse([r for r in logs.records if "gui.stall" in r.getMessage()])  # nothing is written while it lasts
            clock.now += 0.1
            dog.beat()  # the loop is back, 0.9 s after the last beat
            dog.check()
        stalls = [r for r in logs.records if "gui.stall" in r.getMessage()]
        self.assertEqual(len(stalls), 1)
        fields = stalls[0].lspr_fields
        self.assertEqual(fields["dur_ms"], 900)
        self.assertEqual(fields["samples"], 3)
        self.assertTrue(str(fields["where"]).startswith("panels.demo.work:"))
        self.assertIn("panels.demo.work:", str(fields["stack"]))
        self.assertEqual(fields["others"], "-")

    def test_two_stalls_are_two_records(self) -> None:
        clock = _Clock()
        dog = _watchdog(clock, _app_frame())
        with self.assertLogs(LOGGER, "DEBUG") as logs:
            for _ in range(2):
                dog.beat()
                clock.now += 0.5
                dog.check()
                dog.beat()
                dog.check()
        self.assertEqual(len([r for r in logs.records if "gui.stall" in r.getMessage()]), 2)

    def test_the_most_common_place_comes_first(self) -> None:
        clock = _Clock()
        frames = {MAIN: _app_frame("lspr_imaging_app.a")}
        dog = GuiStallWatchdog(MAIN, clock=clock, frames=lambda: frames, threshold_s=0.3, poll_s=0.1)
        dog.beat()
        with self.assertLogs(LOGGER, "DEBUG") as logs:
            for index in range(6):  # lates 0.2 .. 1.2 s: five samples, the last one elsewhere
                clock.now += 0.2
                if index == 5:
                    frames[MAIN] = _app_frame("lspr_imaging_app.b")  # one late sample elsewhere
                dog.check()
            dog.beat()
            dog.check()
        fields = [r for r in logs.records if "gui.stall" in r.getMessage()][0].lspr_fields
        self.assertTrue(str(fields["where"]).startswith("a.work:"))
        self.assertIn("b.work:", str(fields["others"]))

    def test_the_thread_starts_and_stops(self) -> None:
        dog = GuiStallWatchdog(threading.main_thread().ident, poll_s=0.01)
        dog.start()
        dog.start()  # a second start does nothing
        dog.stop()
        dog._thread.join(timeout=2)  # type: ignore[union-attr]
        self.assertFalse(dog._thread.is_alive())  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
