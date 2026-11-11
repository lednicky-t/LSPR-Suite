"""Switching the Image panel's viewport (Preferences -> GPU drawing) keeps the widgets that float on it.

Qt deletes the old viewport with its children; the crop size fields, the measure calibration and the cursor readout live there, so
`gpu_view.replace_viewport` must move them over first (bug found 2026-11-11 in the launch logs). The tests run without a screen,
where OpenGL is never used, so the swap is tested with a plain QWidget as the new viewport: the same Qt call either way.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.image.gpu_view import replace_viewport
except ImportError as exc:  # pragma: no cover - submodule absent
    skip_if_lspri_rewrite_absent(exc)
    raise


class ReplaceViewportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.view = QtWidgets.QGraphicsView()
        self.view.resize(400, 300)
        self.view.show()

    def tearDown(self) -> None:
        self.view.close()
        self.view.deleteLater()

    def test_floating_widgets_survive_with_their_place_and_visibility(self) -> None:
        old = self.view.viewport()
        shown = QtWidgets.QLabel("shown", old)
        shown.setGeometry(10, 20, 60, 15)
        shown.setVisible(True)
        hidden = QtWidgets.QSpinBox(old)
        hidden.setGeometry(100, 120, 50, 22)
        hidden.setVisible(False)

        new = QtWidgets.QWidget()
        replace_viewport(self.view, new)

        self.assertIs(self.view.viewport(), new)
        for widget in (shown, hidden):
            self.assertIs(widget.parent(), new)  # raises RuntimeError if Qt deleted it with the old viewport
        self.assertEqual(shown.geometry().topLeft().x(), 10)
        self.assertEqual(shown.geometry().topLeft().y(), 20)
        self.assertTrue(shown.isVisibleTo(new))
        self.assertFalse(hidden.isVisibleTo(new))
        self.assertFalse(shown.isWindow(), "never a separate top-level window")

    def test_swapping_back_and_forth_keeps_them(self) -> None:
        label = QtWidgets.QLabel("x", self.view.viewport())
        for _ in range(3):
            replace_viewport(self.view, QtWidgets.QWidget())
        self.assertIs(label.parent(), self.view.viewport())


if __name__ == "__main__":
    unittest.main()
