"""The Workflow dock is resizable and movable like every other panel (maintainer
decision 2026-10-18, reversing the 2026-09-24 "fixed width, always its own left
column" rule), and the panel arrangement leaves room for four columns.

What each test pins, and the measurement behind it (all in a 1400 px window):

- Workflow is a column of the *same* dock tree as the others. When it still had
  an area of its own, Qt gave 100 % of any extra window width to it (a 1900 px
  window made it 798 px) and ``resizeDocks`` on it did nothing.
- ``QMainWindow.resizeDocks`` closes only part of the gap per call, so the
  helper below repeats it (the same reason ``PanelContainer`` repeats it when
  it gives a collapsed panel its width back).
- A layout preset saved under an older panel arrangement is rejected by Qt;
  ``LayoutPresetManager.apply`` must fall back instead of silently doing nothing.

Run alone (one pytest process per LSPRi test file); offscreen Qt on Windows has
no fonts, so the text-width-dependent check loads system fonts first.
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

from lspr_imaging_app.app_rewrite import (  # noqa: E402
    _DOCK_LAYOUT_STATE_VERSION,
    _WORKFLOW_DEFAULT_WIDTH_PX,
    _WORKFLOW_MIN_WIDTH_PX,
    build_main_window,
)
from lspr_imaging_app.panels.dock_container import PanelContainer  # noqa: E402
from lspr_imaging_app.panels.layout_presets import LayoutPresetManager  # noqa: E402
from lspr_imaging_app.panels.workflow.collapsible_section import CollapsibleSection  # noqa: E402
from lspr_imaging_app.panels.workflow.panel import WorkflowPanel  # noqa: E402

_HORIZONTAL = Qt.Orientation.Horizontal
_WINDOW_WIDTH_PX = 1400
_FOUR_COLUMN_WINDOW_PX = _WINDOW_WIDTH_PX  # the default window; the four columns need 1336 px of it, see the test
_COLLAPSED_WIDTH_PX = 36
_PIXEL_TOLERANCE = 2


def _pump(turns: int = 6) -> None:
    for _ in range(turns):
        _APP.processEvents()


def _left_edge(dock: PanelContainer, window: QtWidgets.QMainWindow) -> int:
    return dock.mapTo(window, dock.rect().topLeft()).x()


class WorkflowDockResizableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = build_main_window()
        self.window.resize(_WINDOW_WIDTH_PX, 900)
        self.window.show()
        _pump()
        self.docks = {dock.windowTitle(): dock for dock in self.window.findChildren(PanelContainer)}
        self.workflow = self.docks["Workflow"]

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        _APP.processEvents()

    def _drag_workflow_to(self, width: int) -> None:
        """What dragging the separator does, repeated until Qt has closed the gap."""
        for _ in range(10):
            self.window.resizeDocks([self.workflow], [width], _HORIZONTAL)
            _pump()
            if abs(self.workflow.width() - width) <= 1:
                return

    # --- width ---------------------------------------------------------------------------------

    def test_has_a_floor_but_no_ceiling(self) -> None:
        self.assertEqual(self.workflow.minimumWidth(), _WORKFLOW_MIN_WIDTH_PX)
        self.assertGreater(self.workflow.maximumWidth(), 10_000)

    def test_can_be_made_narrower_and_wider_than_the_default(self) -> None:
        narrower = _WORKFLOW_MIN_WIDTH_PX + 20
        self.assertLess(narrower, _WORKFLOW_DEFAULT_WIDTH_PX)  # really narrower than the start
        for target in (narrower, _WORKFLOW_DEFAULT_WIDTH_PX - 40):
            self._drag_workflow_to(target)
            self.assertAlmostEqual(self.workflow.width(), target, delta=_PIXEL_TOLERANCE, msg=f"asked {target}")

    def test_collapse_then_expand_gives_the_width_back(self) -> None:
        width = _WORKFLOW_DEFAULT_WIDTH_PX - 40
        self._drag_workflow_to(width)
        self.workflow._set_collapsed(True)
        _pump()
        self.assertEqual(self.workflow.width(), _COLLAPSED_WIDTH_PX)
        self.workflow._set_collapsed(False)
        _pump(40)  # the width comes back in steps, one per event-loop turn
        self.assertAlmostEqual(self.workflow.width(), width, delta=_PIXEL_TOLERANCE)
        self.assertEqual(self.workflow.minimumWidth(), _WORKFLOW_MIN_WIDTH_PX)  # floor back, not the strip's 36

    def test_a_widened_window_is_not_all_handed_to_workflow(self) -> None:
        workflow_before = self.workflow.width()
        image_before = self.docks["Image"].width()
        extra = 300
        self.window.resize(_WINDOW_WIDTH_PX + extra, 900)
        _pump()
        self.assertLess(self.workflow.width() - workflow_before, extra // 2)
        self.assertGreater(self.docks["Image"].width() - image_before, extra // 2)

    def test_every_stage_fits_at_the_floor_width_without_sideways_scrolling(self) -> None:
        """The floor is the width the widest stage needs. The stages are opened one at a time (the panel
        is a single-open accordion, and a collapsed section does not report its content's width), with
        every nested section expanded - the same way the width-budget test measures."""
        panel = self.workflow.findChild(WorkflowPanel)
        scroll = panel.findChild(QtWidgets.QScrollArea)
        self._drag_workflow_to(_WORKFLOW_MIN_WIDTH_PX)
        too_wide: list[tuple[str, int, int]] = []
        for stage, top_section in panel._sections:
            top_section.set_expanded(True)
            for nested in top_section.findChildren(CollapsibleSection):
                nested.set_expanded(True)
            _pump()
            needed, available = scroll.widget().minimumSizeHint().width(), scroll.viewport().width()
            if needed > available or scroll.horizontalScrollBar().maximum() > 0:
                too_wide.append((stage.name, needed, available))
        self.assertEqual(too_wide, [], "stage, width its rows need, width the panel has at the floor")

    def test_scrolls_sideways_instead_of_clipping_when_a_row_cannot_shrink(self) -> None:
        """The safety net: a row wider than the dock (a bigger font, a future wide control) must be
        reachable, not cut off - the horizontal scroll bar was 'always off' while the dock was fixed."""
        panel = self.workflow.findChild(WorkflowPanel)
        scroll = panel.findChild(QtWidgets.QScrollArea)
        wide_row = QtWidgets.QWidget(scroll.widget())
        wide_row.setMinimumWidth(_WORKFLOW_MIN_WIDTH_PX + 300)
        scroll.widget().layout().insertWidget(0, wide_row)
        self._drag_workflow_to(_WORKFLOW_MIN_WIDTH_PX)
        _pump()
        self.assertGreater(scroll.horizontalScrollBar().maximum(), 0)

    # --- position ------------------------------------------------------------------------------

    def test_by_default_workflow_is_a_full_height_column_beside_the_others(self) -> None:
        others = [dock for name, dock in self.docks.items() if name not in ("Workflow", "Log")]
        workflow_top = self.workflow.mapTo(self.window, self.workflow.rect().topLeft()).y()
        workflow_bottom = self.workflow.mapTo(self.window, self.workflow.rect().bottomLeft()).y()
        for dock in others:
            self.assertGreaterEqual(_left_edge(dock, self.window), _left_edge(self.workflow, self.window) + self.workflow.width())
            self.assertLessEqual(workflow_top, dock.mapTo(self.window, dock.rect().topLeft()).y())
            self.assertGreaterEqual(workflow_bottom, dock.mapTo(self.window, dock.rect().bottomLeft()).y())

    def test_workflow_can_be_docked_into_the_middle_of_the_arrangement(self) -> None:
        self.window.splitDockWidget(self.docks["ROI / Groups"], self.workflow, _HORIZONTAL)
        _pump()
        self.assertGreater(_left_edge(self.workflow, self.window), _left_edge(self.docks["ROI / Groups"], self.window))
        self.assertFalse(self.workflow.isFloating())

    # --- four columns --------------------------------------------------------------------------

    def test_four_columns_fit_the_default_window(self) -> None:
        """Workflow | Image | ROI / Groups | Histogram, side by side. There is no column limit in the code:
        the sum of the panels' minimum widths is what limits it. Measured 2026-10-18: Workflow floor 240 +
        Image 763 + ROI table 188 + Histogram 133 + separators = 1336 px (with the old fixed Workflow, 28 px
        ribbon icons and the old ROI table it was 340 + 823 + 212 + 133 = 1520 px). Image's 763 px, set by
        the tool ribbon, is still the largest part: if this test fails after a ribbon change, the
        four-column layout no longer fits the default window."""
        self.window.resize(_FOUR_COLUMN_WINDOW_PX, 900)
        self.window.splitDockWidget(self.docks["ROI / Groups"], self.docks["Histogram"], _HORIZONTAL)
        _pump()
        columns = [self.docks[name] for name in ("Workflow", "Image", "ROI / Groups", "Histogram")]
        edges = [_left_edge(dock, self.window) for dock in columns]
        self.assertEqual(edges, sorted(edges))
        self.assertEqual(len(set(edges)), 4)  # four distinct columns, not a stack
        self.assertLessEqual(self.window.minimumSizeHint().width(), _FOUR_COLUMN_WINDOW_PX)
        for dock in columns:
            self.assertGreaterEqual(dock.width(), dock.minimumSizeHint().width())


class StaleLayoutPresetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = build_main_window()
        self.window.resize(_WINDOW_WIDTH_PX, 900)
        self.window.show()
        _pump()
        docks = {dock.windowTitle(): dock for dock in self.window.findChildren(PanelContainer)}
        self.docks = {name: docks[name] for name in ("Image", "Histogram", "ROI / Groups", "Spectra", "Sensorgram")}
        self.manager = LayoutPresetManager(
            self.window, docks["Workflow"], self.docks, state_version=_DOCK_LAYOUT_STATE_VERSION
        )

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        _APP.processEvents()

    def test_a_preset_blob_Qt_rejects_falls_back_to_showing_the_right_panels(self) -> None:
        self.manager.load_custom_blobs({"ROI": QByteArray(b"saved under an older panel arrangement")})
        self.manager.apply("ROI")
        _pump()
        visible = {name for name, dock in self.docks.items() if dock.isVisible()}
        self.assertEqual(visible, {"Image", "ROI / Groups"})
        self.assertNotIn("ROI", self.manager.custom_blobs())  # useless blob dropped, not kept to fail again
        self.assertEqual(self.manager.current(), "ROI")


if __name__ == "__main__":
    unittest.main()
