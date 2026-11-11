"""The Play paint census (`panels/image/paint_census.py`): counts repaints per widget only while installed."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget  # noqa: E402

from lspr_imaging_app.panels.image.paint_census import PaintCensus  # noqa: E402

_APP = QApplication.instance() or QApplication([])


class PaintCensusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = QWidget()
        self.window.resize(300, 200)
        layout = QVBoxLayout(self.window)
        self.label = QLabel("frame 1", self.window)
        self.label.setObjectName("status")
        layout.addWidget(self.label)
        self.window.show()
        _APP.processEvents()
        self.census = PaintCensus()

    def tearDown(self) -> None:
        self.census.stop()
        self.window.close()

    def test_counts_the_repaints_of_a_widget_by_name_while_started(self) -> None:
        self.census.start()
        for i in range(3):
            self.label.setText(f"frame {i + 2}")
            self.label.repaint()
        fields = self.census.window_fields(frames=3)
        self.assertIn("QLabel#status < QWidget", fields["top"])
        self.assertGreaterEqual(fields["paints_per_frame"], 1.0)

    def test_counts_nothing_once_stopped_and_starts_a_new_window_after_reporting(self) -> None:
        self.census.start()
        self.label.repaint()
        self.census.window_fields(frames=1)
        self.assertEqual(self.census.window_fields(frames=1)["paints_per_frame"], 0.0)
        self.census.stop()
        self.label.repaint()
        self.assertEqual(self.census.window_fields(frames=1)["top"], "")


if __name__ == "__main__":
    unittest.main()
