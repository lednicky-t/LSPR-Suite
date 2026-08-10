from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from PyQt6.QtCore import QObject, QModelIndex

from lspr_app.gui.flow_plan_model import _BaseFlowDelegate


class _FakeWindow(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.plan_table = QObject()
        self._ui_startup_ready = False
        self.begin_calls: list[tuple[int, int]] = []
        self.end_calls: list[tuple[int, int]] = []
        self.scroll_filters: list[object] = []

    def _install_table_wheel_scroll_filter(self, widget) -> None:
        self.scroll_filters.append(widget)

    def _begin_plan_table_edit(self, row: int, column: int) -> None:
        self.begin_calls.append((row, column))

    def _end_plan_table_edit(self, row: int, column: int) -> None:
        self.end_calls.append((row, column))


class _FakeSignal:
    def connect(self, _slot) -> None:
        return None


class FlowPlanModelStartupPopupTests(unittest.TestCase):
    def test_combo_editor_does_not_autopopup_before_startup_ready(self) -> None:
        window = _FakeWindow()
        delegate = _BaseFlowDelegate(window)
        calls: list[object] = []

        class _FakeCombo:
            def __init__(self) -> None:
                self.destroyed = _FakeSignal()

            def setProperty(self, _key, _value) -> None:
                return None

            def installEventFilter(self, _obj) -> None:
                return None

            def showPopup(self) -> None:
                calls.append("popup")

        with patch("lspr_acq_shell.experiment_plan_table_model.QComboBox", _FakeCombo), patch(
            "lspr_acq_shell.experiment_plan_table_model.QTimer.singleShot",
            side_effect=lambda _interval, callback: calls.append(callback),
        ):
            delegate._prepare_editor(_FakeCombo(), QModelIndex())

        self.assertEqual(calls, [])

    def test_combo_editor_autopopup_after_startup_ready(self) -> None:
        window = _FakeWindow()
        window._ui_startup_ready = True
        delegate = _BaseFlowDelegate(window)
        calls: list[object] = []

        class _FakeCombo:
            def __init__(self) -> None:
                self.destroyed = _FakeSignal()

            def setProperty(self, _key, _value) -> None:
                return None

            def installEventFilter(self, _obj) -> None:
                return None

            def showPopup(self) -> None:
                calls.append("popup")

        with patch("lspr_acq_shell.experiment_plan_table_model.QComboBox", _FakeCombo), patch(
            "lspr_acq_shell.experiment_plan_table_model.QTimer.singleShot",
            side_effect=lambda _interval, callback: calls.append(callback),
        ):
            delegate._prepare_editor(_FakeCombo(), QModelIndex())

        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
