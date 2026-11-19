"""The ROI / Groups table waits while a ROI is being dragged (2026-11-19): the wiring in the real main window.

The behaviour itself is pinned where it lives (`HiddenPanelGate.set_held` in `test_lspri_visibility_gate.py`, `RoiTablePanel.set_edit_running` in
`test_lspri_rewrite_roi_table_panel.py`, `RoiGestures.edit_running` in `test_lspri_rectangle_border_panel.py`); this test only checks that the app connects the
Image panel's signal to the table, which is one line in `app_rewrite.py` that nothing else would notice losing.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

from tests._qt_fonts import load_system_fonts  # noqa: E402

load_system_fonts()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.app_rewrite import build_main_window
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.roi_table.panel import RoiTablePanel
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class RoiEditHoldWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = build_main_window()
        self.window.show()
        _APP.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        _APP.processEvents()

    def test_the_image_panels_roi_drag_holds_and_releases_the_table(self) -> None:
        image_panel = self.window.findChild(ImagePanel)
        table = self.window.findChild(RoiTablePanel)
        self.assertIsNotNone(image_panel)
        self.assertIsNotNone(table)
        self.assertFalse(table._gate.held)
        image_panel.roi_edit_running.emit(True)
        self.assertTrue(table._gate.held, "a drag that edits ROIs holds the table")
        image_panel.roi_edit_running.emit(False)
        self.assertFalse(table._gate.held, "and releases it")


if __name__ == "__main__":
    unittest.main()
