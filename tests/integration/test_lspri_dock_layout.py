"""The default panel arrangement and stale layout presets (was test_lspri_workflow_dock_resizable.py until the Workflow
panel was retired, 2026-11-11).

What each test pins (all in a 1400 px window):

- There is no Workflow dock any more; Image is the first column and every panel lives in one dock tree.
- Image | ROI / Groups | Histogram fit side by side in the default window (there is no column limit in the code: the sum
  of the panels' minimum widths is what limits it; Image's width, set by the tool ribbon, is the largest part).
- A layout preset saved under an older panel arrangement (a version-4 blob still has the Workflow column) is rejected
  by Qt; ``LayoutPresetManager.apply`` must fall back instead of silently doing nothing.

Run alone (one pytest process per LSPRi test file); offscreen Qt on Windows has no fonts, so the text-width-dependent
check loads system fonts first.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import QByteArray, Qt

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

from tests._qt_fonts import load_system_fonts  # noqa: E402

load_system_fonts()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.app_rewrite import _DOCK_LAYOUT_STATE_VERSION, build_main_window  # noqa: E402
from lspr_imaging_app.panels.dock_container import PanelContainer  # noqa: E402
from lspr_imaging_app.panels.layout_presets import LayoutPresetManager  # noqa: E402

_HORIZONTAL = Qt.Orientation.Horizontal
_WINDOW_WIDTH_PX = 1400


def _pump(turns: int = 6) -> None:
    for _ in range(turns):
        _APP.processEvents()


def _left_edge(dock: PanelContainer, window: QtWidgets.QMainWindow) -> int:
    return dock.mapTo(window, dock.rect().topLeft()).x()


class _WindowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = build_main_window()
        self.window.resize(_WINDOW_WIDTH_PX, 900)
        self.window.show()
        _pump()
        self.docks = {dock.windowTitle(): dock for dock in self.window.findChildren(PanelContainer)}

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        _APP.processEvents()


class DefaultArrangementTests(_WindowTest):
    def test_there_is_no_workflow_dock_and_image_is_the_first_column(self) -> None:
        self.assertNotIn("Workflow", self.docks)
        image_left = _left_edge(self.docks["Image"], self.window)
        for name in ("Histogram", "ROI / Groups", "Spectra", "Sensorgram"):
            self.assertGreaterEqual(_left_edge(self.docks[name], self.window), image_left, name)

    def test_three_columns_fit_the_default_window(self) -> None:
        self.window.splitDockWidget(self.docks["ROI / Groups"], self.docks["Histogram"], _HORIZONTAL)
        _pump()
        columns = [self.docks[name] for name in ("Image", "ROI / Groups", "Histogram")]
        edges = [_left_edge(dock, self.window) for dock in columns]
        self.assertEqual(edges, sorted(edges))
        self.assertEqual(len(set(edges)), 3)  # three distinct columns, not a stack
        self.assertLessEqual(self.window.minimumSizeHint().width(), _WINDOW_WIDTH_PX)
        for dock in columns:
            self.assertGreaterEqual(dock.width(), dock.minimumSizeHint().width())


class StaleLayoutPresetTests(_WindowTest):
    def setUp(self) -> None:
        super().setUp()
        self.docks = {name: self.docks[name] for name in ("Image", "Histogram", "ROI / Groups", "Spectra", "Sensorgram")}
        self.manager = LayoutPresetManager(self.window, self.docks, state_version=_DOCK_LAYOUT_STATE_VERSION)

    def test_a_preset_blob_Qt_rejects_falls_back_to_showing_the_right_panels(self) -> None:
        self.manager.load_custom_blobs({"ROI": QByteArray(b"saved under an older panel arrangement")})
        self.manager.apply("ROI")
        _pump()
        visible = {name for name, dock in self.docks.items() if dock.isVisible()}
        self.assertEqual(visible, {"Image", "ROI / Groups"})
        self.assertNotIn("ROI", self.manager.custom_blobs())  # useless blob dropped, not kept to fail again
        self.assertEqual(self.manager.current(), "ROI")

    def test_a_layout_saved_with_the_workflow_dock_is_not_restored(self) -> None:
        """Version 4 had the Workflow column; restoring its blob onto the new tree would mis-map the sizes."""
        blob = self.window.saveState(_DOCK_LAYOUT_STATE_VERSION - 1)
        self.assertFalse(self.window.restoreState(blob, _DOCK_LAYOUT_STATE_VERSION))


if __name__ == "__main__":
    unittest.main()
