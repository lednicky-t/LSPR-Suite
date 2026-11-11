"""One repaint per Play frame (2026-11-11): `panels/image/play_repaint.py` (the Image view) and `panels/sensorgram/play_repaint.py` (the Sensorgram's
cursor strip). While Play runs the two views repaint only when told to; everything the scene reports that is not the cursor's own strip is still
forwarded; the normal mode comes back, with a repaint, when Play stops."""

from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg  # noqa: E402
from PyQt6.QtCore import QRect  # noqa: E402
from PyQt6.QtWidgets import QApplication, QGraphicsScene, QGraphicsView  # noqa: E402

from lspr_imaging_app.panels.axis_cursor import AxisCursor  # noqa: E402
from lspr_imaging_app.panels.image import play_repaint as image_repaint  # noqa: E402
from lspr_imaging_app.panels.image.play_repaint import PlayRepaint  # noqa: E402
from lspr_imaging_app.panels.sensorgram.play_repaint import PlayCursorRepaint  # noqa: E402

_APP = QApplication.instance() or QApplication([])
_NONE = QGraphicsView.ViewportUpdateMode.NoViewportUpdate
_FULL = QGraphicsView.ViewportUpdateMode.FullViewportUpdate


def _pump(seconds: float = 0.05) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)


def _record_updates(view: QGraphicsView) -> list[tuple]:
    calls: list[tuple] = []
    viewport = view.viewport()
    viewport.update = lambda *args: calls.append(args)  # the wrapper object stays the same for the same viewport
    return calls


class ImageViewRepaintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.view = QGraphicsView(QGraphicsScene())
        self.view.setViewportUpdateMode(_FULL)
        self.view.resize(200, 150)
        self.view.show()
        _pump()
        self.updates = _record_updates(self.view)
        self.repaint = PlayRepaint(self.view)

    def tearDown(self) -> None:
        self.repaint.stop()
        self.view.close()

    def test_play_stops_the_automatic_repaints_and_asks_for_one_per_frame(self) -> None:
        self.assertFalse(self.repaint.active)
        self.repaint.start()
        self.assertTrue(self.repaint.active)
        self.assertEqual(self.view.viewportUpdateMode(), _NONE)
        self.repaint.frame_shown()
        self.repaint.frame_shown()
        self.assertEqual(len(self.updates), 2)

    def test_stop_gives_the_views_own_mode_back_and_repaints_once(self) -> None:
        self.repaint.start()
        self.updates.clear()
        self.repaint.stop()
        self.assertEqual(self.view.viewportUpdateMode(), _FULL)
        self.assertEqual(len(self.updates), 1)
        self.repaint.stop()  # a second stop does nothing
        self.assertEqual(len(self.updates), 1)

    def test_nothing_happens_when_not_playing(self) -> None:
        self.repaint.frame_shown()
        self.assertEqual(self.updates, [])
        self.assertEqual(self.view.viewportUpdateMode(), _FULL)

    def test_the_guard_repaints_a_view_that_got_no_frame_for_a_while(self) -> None:
        """A fake clock: the guard is quiet while frames come, and repaints once none came for `GUARD_MS` (a zoom or a tool overlay during a stalled Play)."""
        from unittest import mock

        clock = {"now": 100.0}
        with mock.patch.object(image_repaint.time, "perf_counter", lambda: clock["now"]):
            self.repaint.start()
            self.updates.clear()
            clock["now"] += 0.5 * image_repaint.GUARD_MS / 1000.0
            self.repaint._guard()
            self.assertEqual(self.updates, [], "repainted although a frame was shown recently")
            clock["now"] += 2.0 * image_repaint.GUARD_MS / 1000.0
            self.repaint._guard()
            self.assertEqual(len(self.updates), 1)
            self.repaint._guard()  # right after: the guard just repainted
            self.assertEqual(len(self.updates), 1)
            self.repaint.frame_shown()
            clock["now"] += 0.5 * image_repaint.GUARD_MS / 1000.0
            self.repaint._guard()
            self.assertEqual(len(self.updates), 2, "a shown frame counts as a repaint")

    def test_the_guard_timer_runs_only_while_playing(self) -> None:
        self.assertFalse(self.repaint._timer.isActive())
        self.repaint.start()
        self.assertTrue(self.repaint._timer.isActive())
        self.repaint.stop()
        self.assertFalse(self.repaint._timer.isActive())


class SensorgramCursorRepaintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.widget = pg.PlotWidget()
        self.widget.resize(600, 300)
        self.item = self.widget.getPlotItem()
        self.item.plot(list(range(100)), [i % 7 for i in range(100)])
        self.cursor = AxisCursor(self.item, "#38bdf8")
        self.widget.show()
        self.item.getViewBox().setRange(xRange=(0, 100), yRange=(0, 7), padding=0)
        self.cursor.set_x(10.0)
        _pump(0.1)
        self.updates = _record_updates(self.widget)
        self.scene_changes: list = []
        self.item.scene().changed.connect(self.scene_changes.append)  # when the scene has reported, whatever the machine's load
        self.repaint = PlayCursorRepaint(self.widget, self.item.scene(), (self.cursor.line, self.cursor.handle))

    def tearDown(self) -> None:
        self.repaint.stop()
        self.widget.close()

    def _settle(self) -> None:
        """Pump until the scene has reported nothing for 100 ms (it reports in queued calls: how long that takes depends on the machine's load)."""
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            seen = len(self.scene_changes)
            _pump(0.1)
            if len(self.scene_changes) == seen:
                return

    def _wait_for_scene_report(self) -> None:
        """After a change was made: pump until the scene has reported it, then until it is quiet."""
        before = len(self.scene_changes)
        deadline = time.monotonic() + 5.0
        while len(self.scene_changes) == before and time.monotonic() < deadline:
            _pump(0.01)
        self._settle()

    def _strip(self) -> QRect:
        rect = QRect()
        for item in (self.cursor.line, self.cursor.handle):
            rect = rect.united(self.widget.mapFromScene(item.sceneBoundingRect()).boundingRect())
        return rect

    def test_it_switches_the_view_to_manual_repaints_and_back(self) -> None:
        before = self.widget.viewportUpdateMode()
        self.repaint.start()
        self.assertEqual(self.widget.viewportUpdateMode(), _NONE)
        self.updates.clear()
        self.repaint.stop()
        self.assertEqual(self.widget.viewportUpdateMode(), before)
        self.assertEqual(len(self.updates), 1)

    def test_each_frame_repaints_the_old_and_the_new_place_of_the_cursor(self) -> None:
        self.repaint.start()
        self.repaint.frame_shown()  # at x = 10
        first = self._strip()
        self.updates.clear()
        self.cursor.set_x(60.0)
        self.repaint.frame_shown()
        second = self._strip()
        self.assertEqual(len(self.updates), 1)
        dirty = self.updates[0][0]
        self.assertTrue(dirty.contains(first) and dirty.contains(second), (dirty, first, second))
        self.assertLess(dirty.height(), self.widget.viewport().height() + 20)  # a strip, not more than the plot
        self.assertGreater(dirty.width(), second.width())  # it spans both places

    def test_the_scene_change_a_cursor_move_causes_is_not_forwarded_again(self) -> None:
        self.repaint.start()
        self.repaint.frame_shown()
        self._settle()
        self.updates.clear()
        reports = len(self.scene_changes)
        for x in (20.0, 30.0, 40.0):
            self.cursor.set_x(x)
            self.repaint.frame_shown()
        count = len(self.updates)
        self._wait_for_scene_report()  # the scene reports the moves (queued) now
        self.assertGreater(len(self.scene_changes), reports, "the scene never reported the cursor moves: the test proves nothing")
        self.assertEqual(len(self.updates), count, "a cursor-only scene change was forwarded as a second repaint")

    def test_any_other_scene_change_is_still_repainted(self) -> None:
        self.repaint.start()
        self.repaint.frame_shown()
        self._settle()
        self.updates.clear()
        self.item.plot([5, 6, 7], [1, 2, 3], pen="r")  # a new curve (a sensorgram arriving while Play runs)
        self._wait_for_scene_report()
        self.assertTrue(self.updates, "a change that is not the cursor's was never repainted during Play")


if __name__ == "__main__":
    unittest.main()
