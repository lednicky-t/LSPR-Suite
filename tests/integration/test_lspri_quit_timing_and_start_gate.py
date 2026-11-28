"""The close is timed (`quit_timing.QuitClock`) and the out-of-date check starts after the image (`ScanResultsModule.set_start_gate`) (2026-11-28).

Pinned: the four marks of a close are logged in order with `since_quit_ms`, the first before and the last after every other `aboutToQuit` slot connected
between them, and nothing is logged by the exit hook when no quit happened; a check held by the start gate does not snapshot, asks again by its own timer
(not the debounce timer), and starts as soon as the gate lets go; `ImageRenderer.read_ahead_pending` says what the gate needs (planes queued or being
read). Real Qt objects, no window, no dataset. Reasons: `apps/LSPRi/eva/docs/startup_and_close_2026-11-28.md`.
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

try:
    from lspr_imaging_app import quit_timing
    from lspr_imaging_app.analysis.scan_results import GATE_RETRY_MS, ScanResultsModule
    from lspr_imaging_app.panels.image.render import ImageRenderer
    from tests.unit.test_lspri_rewrite_scan_jobs import _plain
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class QuitClockTests(unittest.TestCase):
    def test_the_four_marks_come_in_order_around_the_other_slots(self) -> None:
        clock = quit_timing.QuitClock()
        order: list[str] = []
        with mock.patch.object(quit_timing, "log_event", side_effect=lambda _logger, event, *_a, **_kw: order.append(event)), \
                mock.patch.object(quit_timing.atexit, "register") as register:
            clock.connect_start(_app)
            _app.aboutToQuit.connect(lambda: order.append("a slot in between"))
            clock.connect_end(_app)
            _app.aboutToQuit.emit()
            clock.exec_returned()
            exit_hook = register.call_args.args[0]
            exit_hook()
        self.assertEqual(order, ["app.quit", "a slot in between", "app.quit_slots_done", "app.exec_returned", "app.exit"])

    def test_the_exit_hook_says_nothing_when_no_quit_happened(self) -> None:
        clock = quit_timing.QuitClock()
        with mock.patch.object(quit_timing, "log_event") as log:
            clock._on_exit()  # a start that failed before the event loop ran
        log.assert_not_called()

    def test_the_exit_line_names_the_threads_still_alive(self) -> None:
        clock = quit_timing.QuitClock()
        clock._on_quit()
        release = threading.Event()
        thread = threading.Thread(target=release.wait, name="a-test-thread", daemon=True)
        thread.start()
        try:
            with mock.patch.object(quit_timing, "log_event") as log:
                clock._on_exit()
            self.assertIn("a-test-thread", log.call_args.kwargs["threads"])
        finally:
            release.set()
            thread.join(2)


def _module(app) -> ScanResultsModule:
    module = ScanResultsModule(lambda: app.source(), scan_running=lambda: False)
    module._stored = object()
    module._path = Path("scan.h5")
    return module


class StartGateTests(unittest.TestCase):
    def test_a_gate_that_says_wait_holds_the_check_and_it_starts_when_the_gate_lets_go(self) -> None:
        module = _module(_plain(cubes=2))
        waiting = [True]
        module.set_start_gate(lambda: waiting[0])
        started: list[str] = []
        with mock.patch.object(module, "_start_thread", side_effect=lambda fn, name: started.append(name)):
            module._start_check()
            self.assertFalse(module.is_checking())
            self.assertEqual(started, [])
            self.assertTrue(module._gate_timer.isActive())  # asks again by its own timer ...
            self.assertEqual(module._gate_timer.interval(), GATE_RETRY_MS)
            self.assertFalse(module._timer.isActive())  # ... never the debounce timer (its interval is the debounce)
            waiting[0] = False
            module._start_check()
        self.assertTrue(module.is_checking())
        self.assertEqual(started, ["scan-results-check"])

    def test_without_a_gate_the_check_starts_at_once(self) -> None:
        module = _module(_plain(cubes=2))
        started: list[str] = []
        with mock.patch.object(module, "_start_thread", side_effect=lambda fn, name: started.append(name)):
            module._start_check()
        self.assertEqual(started, ["scan-results-check"])

    def test_a_check_held_by_the_gate_does_not_snapshot(self) -> None:
        module = _module(_plain(cubes=2))
        module.set_start_gate(lambda: True)
        with mock.patch("lspr_imaging_app.analysis.scan_results.snapshot_source") as snapshot:
            module._start_check()
        snapshot.assert_not_called()


class ReadAheadPendingTests(unittest.TestCase):
    def test_pending_while_planes_are_queued_or_being_read(self) -> None:
        renderer = ImageRenderer(lambda cube, wl: None)
        try:
            self.assertFalse(renderer.read_ahead_pending())
            with renderer._lock:
                renderer._prefetch_queue.append((0, 500.0))
            self.assertTrue(renderer.read_ahead_pending())
            with renderer._lock:
                renderer._prefetch_queue.clear()
                renderer._reading[(0, 500.0)] = threading.Event()
            self.assertTrue(renderer.read_ahead_pending())
            with renderer._lock:
                renderer._reading.clear()
            self.assertFalse(renderer.read_ahead_pending())
        finally:
            renderer.stop()


if __name__ == "__main__":
    unittest.main()
