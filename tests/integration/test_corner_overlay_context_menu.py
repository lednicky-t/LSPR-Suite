"""Regression coverage for the corner overlay boxes (spectrum_stats_overlay,
trace_stats_overlay, etc.) and the spectrum/sensorgram plots' right-click
context menu.

History: WA_TransparentForMouseEvents was applied to these overlay containers
to stop them from swallowing right-clicks meant for the plot underneath -
but per Qt's own docs that attribute "disables the delivery of mouse events
to the widget AND ITS CHILDREN", which silently broke the labels' own
click-to-cycle/toggle-cursor behavior (a real regression the maintainer
found). Replaced with _CornerOverlayContainer.contextMenuEvent, which only
intercepts right-click/context-menu requests and forwards them to the
plot's ViewBox, leaving ordinary mouse press/release delivery to the label
untouched. This test exercises a *real* QContextMenuEvent through that
override (not a mock) specifically because the first implementation used
QContextMenuEvent.globalPosition(), which doesn't exist in PyQt6 (only
QMouseEvent has it) - a real event is what actually caught that bug.
"""
from __future__ import annotations

import unittest

from tests._paths import ensure_repo_paths


ensure_repo_paths()

import sys
from pathlib import Path

APP_SRC = Path(__file__).resolve().parents[2] / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QContextMenuEvent
from PyQt6.QtWidgets import QApplication, QLabel

from lspr_app.gui.main_window_plotting import _CornerOverlayContainer


class _FakeViewBox:
    def __init__(self, *, menu_enabled: bool = True) -> None:
        self._menu_enabled = menu_enabled
        self.raise_calls: list[object] = []

    def menuEnabled(self) -> bool:
        return self._menu_enabled

    def raiseContextMenu(self, ev) -> None:
        self.raise_calls.append(ev)


class _FakePlotItem:
    def __init__(self, view_box) -> None:
        self.vb = view_box


class _FakePlotWidget:
    def __init__(self, view_box) -> None:
        self._plot_item = _FakePlotItem(view_box)

    def getPlotItem(self):
        return self._plot_item


def _make_context_menu_event() -> QContextMenuEvent:
    return QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(123, 456))


class CornerOverlayContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_right_click_on_container_forwards_to_the_view_box(self) -> None:
        view_box = _FakeViewBox(menu_enabled=True)
        plot_widget = _FakePlotWidget(view_box)
        container = _CornerOverlayContainer(None, plot_widget)

        event = _make_context_menu_event()
        container.contextMenuEvent(event)

        self.assertEqual(len(view_box.raise_calls), 1)
        self.assertTrue(event.isAccepted())
        # The shim's screenPos() must support .toPoint() (what pyqtgraph's
        # real raiseContextMenu calls) - this is exactly what broke with the
        # first (globalPosition()) implementation.
        shim = view_box.raise_calls[0]
        self.assertEqual(shim.screenPos().toPoint(), QPoint(123, 456))

    def test_menu_disabled_view_box_ignores_the_event(self) -> None:
        view_box = _FakeViewBox(menu_enabled=False)
        plot_widget = _FakePlotWidget(view_box)
        container = _CornerOverlayContainer(None, plot_widget)

        event = _make_context_menu_event()
        container.contextMenuEvent(event)

        self.assertEqual(view_box.raise_calls, [])
        self.assertFalse(event.isAccepted())

    def test_ignored_context_menu_event_on_child_label_propagates_to_container(self) -> None:
        """The real end-to-end path: a right-click landing exactly on the
        label (not the container's margin) reaches this override too, since
        QLabel doesn't handle context menu events and Qt propagates the
        ignored event up to its parent."""
        view_box = _FakeViewBox(menu_enabled=True)
        plot_widget = _FakePlotWidget(view_box)
        container = _CornerOverlayContainer(None, plot_widget)
        label = QLabel("peak: 605.234 nm", container)
        container.layout()  # no-op guard; container has no layout in this minimal test
        label.setParent(container)

        event = _make_context_menu_event()
        self.app.sendEvent(label, event)

        self.assertEqual(len(view_box.raise_calls), 1)


if __name__ == "__main__":
    unittest.main()
