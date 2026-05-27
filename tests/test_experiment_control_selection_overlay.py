from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
import sys

if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6.QtCore import QObject

from lspr_app.gui.experiment_control_editing import ExperimentControlEditingController


class _FakeSignal:
    def connect(self, _slot) -> None:
        return None


class _FakeViewport:
    def __init__(self) -> None:
        self.props: dict[str, object] = {}
        self.visible = True

    def setProperty(self, key: str, value: object) -> None:
        self.props[key] = value

    def installEventFilter(self, _obj) -> None:
        return None

    def isVisible(self) -> bool:
        return self.visible

    def width(self) -> int:
        return 320

    def height(self) -> int:
        return 180


class _FakeSelectionModel:
    def __init__(self) -> None:
        self.selectionChanged = _FakeSignal()


class _FakeTable:
    def __init__(self) -> None:
        self._viewport = _FakeViewport()
        self.props: dict[str, object] = {}
        self.visible = True
        self.installed_filters: list[object] = []
        self._selection_model = _FakeSelectionModel()

    def viewport(self):
        return self._viewport

    def selectionModel(self):
        return self._selection_model

    def setProperty(self, key: str, value: object) -> None:
        self.props[key] = value

    def installEventFilter(self, obj) -> None:
        self.installed_filters.append(obj)

    def isVisible(self) -> bool:
        return self.visible

    def currentColumn(self) -> int:
        return 0

    def currentRow(self) -> int:
        return 0

    def rowCount(self) -> int:
        return 1

    def columnCount(self) -> int:
        return 1


class _FakeOverlay:
    created_parent = None

    def __init__(self, parent, table) -> None:
        self.__class__.created_parent = parent
        self.table = table
        self.visible = False
        self.geometry = None

    def hide(self) -> None:
        self.visible = False

    def show(self) -> None:
        self.visible = True

    def raise_(self) -> None:
        return None

    def update(self) -> None:
        return None

    def setGeometry(self, geometry) -> None:
        self.geometry = geometry


class _FakeWindow(QObject):
    def isVisible(self) -> bool:
        return True


class ExperimentControlSelectionOverlayTests(unittest.TestCase):
    def test_overlay_is_attached_to_table_viewport(self) -> None:
        window = _FakeWindow()
        table = _FakeTable()
        button = SimpleNamespace()

        with patch("lspr_app.gui.experiment_control_editing._SelectionOverlay", _FakeOverlay):
            controller = ExperimentControlEditingController(window, table, button)

        self.assertIs(_FakeOverlay.created_parent, table.viewport())
        self.assertIsNotNone(controller._overlay)

    def test_sync_overlay_uses_viewport_geometry(self) -> None:
        controller = ExperimentControlEditingController.__new__(ExperimentControlEditingController)
        controller._overlay = _FakeOverlay(object(), object())
        controller._table = _FakeTable()
        controller._window = SimpleNamespace(isVisible=lambda: True)

        controller._sync_overlay()

        self.assertTrue(controller._overlay.visible)
        self.assertIsNotNone(controller._overlay.geometry)
        self.assertEqual(controller._overlay.geometry.x(), 0)
        self.assertEqual(controller._overlay.geometry.y(), 0)
        self.assertEqual(controller._overlay.geometry.width(), 320)
        self.assertEqual(controller._overlay.geometry.height(), 180)


if __name__ == "__main__":
    unittest.main()
