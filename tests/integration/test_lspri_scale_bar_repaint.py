"""The scale bar asks for a repaint only when its layout changes (2026-11-11): it is laid out again at every Play frame, and a
needless update() made Qt repaint the whole Image view a second time per frame."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from lspr_imaging_app.panels.image.scale_bar_overlay import ScaleBarItem  # noqa: E402

_APP = QApplication.instance() or QApplication([])


class ScaleBarRepaintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plot = pg.PlotWidget()
        self.plot.resize(600, 400)
        self.plot.show()
        _APP.processEvents()
        self.view_box = self.plot.getPlotItem().vb
        self.view_box.setRange(xRange=(0, 500), yRange=(0, 300), padding=0)
        self.bar = ScaleBarItem(self.view_box)
        self.updates = 0
        original = self.bar.update
        self.bar.update = lambda *a: (setattr(self, "updates", self.updates + 1), original(*a))[1]

    def tearDown(self) -> None:
        self.plot.close()

    def test_the_same_layout_again_asks_for_no_repaint(self) -> None:
        self.bar.update_view(True, None)
        self.assertEqual(self.updates, 1)
        for _ in range(5):
            self.bar.update_view(True, None)  # Play: the next frame, same view and units
        self.assertEqual(self.updates, 1)

    def test_a_zoom_or_new_units_or_showing_it_again_repaints(self) -> None:
        self.bar.update_view(True, None)
        self.view_box.setRange(xRange=(0, 100), yRange=(0, 60), padding=0)
        self.bar.update_view(True, None)
        self.assertEqual(self.updates, 2)
        self.bar.update_view(True, 0.25)  # micrometres
        self.assertEqual(self.updates, 3)
        self.bar.update_view(False, 0.25)
        self.assertFalse(self.bar.isVisible())
        self.bar.update_view(True, 0.25)
        self.assertTrue(self.bar.isVisible())
        self.assertEqual(self.updates, 4)


if __name__ == "__main__":
    unittest.main()
