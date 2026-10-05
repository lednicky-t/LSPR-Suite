"""Small UI choices survive a restart (`panels/ui_state.py`, wired in
`app_rewrite.build_main_window`): the store itself, then the real window.

A real in-process `QApplication`, never `.exec()`; driven through widget
methods and signals, no screen coordinates.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PyQt6 import QtWidgets
from PyQt6.QtTest import QTest

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.app_rewrite import build_main_window
    from lspr_imaging_app.image_tools import MaskScope
    from lspr_imaging_app.image_tools.mask_edit_tool import MaskEditTool
    from lspr_imaging_app.panels.dock_container import PanelContainer
    from lspr_imaging_app.panels.histogram import HistogramPanel
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.panels.workflow.dataset_export import DatasetExportSection
    from lspr_imaging_app.storage.app_settings import AppSettings, load_app_settings, save_app_settings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc


class UiStateStoreTests(unittest.TestCase):
    def test_widgets_restore_and_save(self) -> None:
        written: list[dict] = []
        store = UiStateStore({"c": True, "s": 7, "d": 2.5, "m": "b", "bad": 9999}, on_changed=written.append)
        check = QtWidgets.QCheckBox()
        spin = QtWidgets.QSpinBox()
        spin.setRange(0, 100)
        dspin = QtWidgets.QDoubleSpinBox()
        combo = QtWidgets.QComboBox()
        combo.addItem("A", "a")
        combo.addItem("B", "b")
        out_of_range = QtWidgets.QSpinBox()
        out_of_range.setRange(0, 10)
        out_of_range.setValue(3)
        for key, widget in (("c", check), ("s", spin), ("d", dspin), ("m", combo), ("bad", out_of_range)):
            store.bind(key, widget)
        self.assertTrue(check.isChecked())
        self.assertEqual(spin.value(), 7)
        self.assertEqual(dspin.value(), 2.5)
        self.assertEqual(combo.currentData(), "b")
        self.assertEqual(out_of_range.value(), 3)  # invalid saved value ignored
        self.assertEqual(written, [])  # restoring is not a change
        spin.setValue(8)
        spin.setValue(9)
        self.assertEqual(written, [])  # debounced
        store.flush()
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0]["s"], 9)

    def test_unsupported_widget_is_a_loud_error(self) -> None:
        with self.assertRaises(TypeError):
            UiStateStore().bind("x", QtWidgets.QLabel())


def _wait_until(condition, timeout_ms: int = 5000) -> None:
    """Process events until `condition()` is true (the settings write is debounced, so it lands a moment later)."""
    waited = 0
    while not condition() and waited < timeout_ms:
        QTest.qWait(50)
        waited += 50


def _panel(window: QtWidgets.QMainWindow, title: str, panel_type: type) -> object:
    return [d for d in window.findChildren(PanelContainer) if d.windowTitle() == title][0].findChild(panel_type)


class WindowRestoreTests(unittest.TestCase):
    def _build(self, **kwargs: object) -> QtWidgets.QMainWindow:
        window = build_main_window(**kwargs)
        self.addCleanup(window.close)
        self.addCleanup(window.deleteLater)
        window.show()
        _APP.processEvents()
        return window

    def test_saved_choices_are_applied_at_launch(self) -> None:
        window = self._build(
            initial_settings=AppSettings(
                ui_state={
                    "export/chunk_size": 128,
                    "export/shard": "per_spectral_cube",
                    "export/compression": False,
                    "export/skip_excluded": True,
                    "mask/scope": "individual",
                    "image/mask_edit_tool": "morphology",
                    "image/cursor_readout": True,
                    "histogram/cursor_readout": True,
                }
            )
        )
        export = window.findChildren(DatasetExportSection)[0]
        self.assertEqual(export._chunk_spin.value(), 128)
        self.assertEqual(export._shard_combo.currentData(), "per_spectral_cube")
        self.assertFalse(export._compression_check.isChecked())
        self.assertTrue(export._skip_excluded_check.isChecked())
        image = _panel(window, "Image", ImagePanel)
        self.assertIs(image._mask_scope.scope(), MaskScope.INDIVIDUAL)
        self.assertIs(image._mask_edit_tool.tool(), MaskEditTool.MORPHOLOGY)
        self.assertTrue(image._cursor_overlay._enabled)
        self.assertTrue(_panel(window, "Histogram", HistogramPanel)._plot.cursor_overlay()._enabled)

    def test_garbage_saved_values_fall_back_to_defaults(self) -> None:
        window = self._build(
            initial_settings=AppSettings(
                ui_state={"mask/scope": "nope", "image/mask_edit_tool": 5, "export/chunk_size": "x", "export/shard": "gone"}
            )
        )
        image = _panel(window, "Image", ImagePanel)
        self.assertIs(image._mask_scope.scope(), MaskScope.PERSISTENT)
        self.assertIs(image._mask_edit_tool.tool(), MaskEditTool.HISTOGRAM_SELECTION)
        self.assertEqual(window.findChildren(DatasetExportSection)[0]._chunk_spin.value(), 64)

    def test_changes_are_saved_after_a_pause(self) -> None:
        saved: list[AppSettings] = []
        window = self._build(on_settings_changed=saved.append)
        image = _panel(window, "Image", ImagePanel)
        image._mask_scope.set_scope(MaskScope.INDIVIDUAL)
        image._cursor_overlay.toggle()
        window.findChildren(DatasetExportSection)[0]._chunk_spin.setValue(256)
        _wait_until(lambda: any("mask/scope" in s.ui_state for s in saved))
        state = saved[-1].ui_state
        self.assertEqual(state["mask/scope"], "individual")
        self.assertIs(state["image/cursor_readout"], True)
        self.assertEqual(state["export/chunk_size"], 256)

    def test_settings_file_round_trips_ui_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            save_app_settings(AppSettings(ui_state={"a/b": 1}), path)
            self.assertEqual(load_app_settings(path).ui_state, {"a/b": 1})

    # -- reference frame: remembered per dataset ---------------------------

    def _homes(self) -> tuple[str, str]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d1, d2 = Path(tmp.name) / "D1", Path(tmp.name) / "D2"
        d1.mkdir()
        d2.mkdir()
        return str(d1), str(d2)

    def test_manual_reference_frame_returns_only_for_the_same_dataset(self) -> None:
        d1, d2 = self._homes()
        saved_ref = {"dataset": d1, "mode": "manual", "cube": 2, "wavelength": 640.0}
        window = self._build(
            initial_settings=AppSettings(ui_state={"reference_frame": saved_ref}, auto_reopen_last_dataset=False)
        )
        image = _panel(window, "Image", ImagePanel)
        reference = image._reference_frame
        image._dataset.dataset_loaded.emit(SimpleNamespace(home=Path(d2)))
        self.assertEqual(reference.mode(), "auto")
        image._dataset.dataset_loaded.emit(SimpleNamespace(home=Path(d1)))
        self.assertEqual(reference.mode(), "manual")
        self.assertEqual(reference.manual_frame(), (2, 640.0))

    def test_choosing_a_reference_frame_is_saved_with_its_dataset(self) -> None:
        d1, _d2 = self._homes()
        saved: list[AppSettings] = []
        window = self._build(
            initial_settings=AppSettings(last_dataset_folder=d1, auto_reopen_last_dataset=False),
            on_settings_changed=saved.append,
        )
        _panel(window, "Image", ImagePanel)._reference_frame.set_manual_frame(1, 550.0)
        _wait_until(lambda: any("reference_frame" in s.ui_state for s in saved))
        self.assertEqual(
            saved[-1].ui_state["reference_frame"],
            {"dataset": d1, "mode": "manual", "cube": 1, "wavelength": 550.0},
        )


if __name__ == "__main__":
    unittest.main()
