"""The selection toolbar scrolls instead of making the Image panel taller (2026-11-15).

11 buttons and 4 dividers are about 330 px tall. Before the column was wrapped in `ScrollingColumn` it forced the Image panel's minimum height to 573 px (289 px
without it). Now the column reports a small minimum, and when the panel is short it shows an arrow at each end and scrolls (arrows, held
arrows, the wheel over a gap); when the panel is tall enough it shows nothing extra. Every button stays reachable.
"""

from __future__ import annotations

import sys
import time
import unittest

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import AreaSelectionMode, AreaSelectionModule, HighlightRangeModule, ReferenceFrameModule, SelectionModule
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _pump(seconds: float = 0.1) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


class SelectionToolbarScrollTest(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = ImagePanel(
            DatasetModule(), GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), RoiToolbox(),
            SelectionModule(), ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), area_selection=AreaSelectionModule(),
        )
        self.toolbar = self.panel._selection_tools.toolbar
        self.column = self.toolbar.column

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()

    def _show(self, height: int) -> None:
        self.panel.resize(1100, height)
        self.panel.show()
        _pump(0.2)

    def _all_buttons(self):
        return [
            *(self.toolbar.tool_button(mode) for mode in AreaSelectionMode if mode is not AreaSelectionMode.ALL),  # 7 tools
            self.toolbar.mode_button,
            self.toolbar.modify_button,
            self.toolbar.lock_button,  # one button for Lock / Unlock since 2026-11-16
        ]

    def _visible_in_column(self, button: QtWidgets.QWidget) -> bool:
        viewport = self.column.area().viewport()
        centre = button.mapTo(viewport, button.rect().center())
        return viewport.rect().contains(centre)

    def _wheel(self, delta_y: int) -> None:
        point = QPointF(2.0, 2.0)  # a wheel reaches a scroll area through its viewport
        QtWidgets.QApplication.sendEvent(
            self.column.area().viewport(),
            QWheelEvent(point, point, QPoint(0, 0), QPoint(0, delta_y), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False),
        )

    def test_the_column_does_not_make_the_panel_taller(self) -> None:
        self.assertEqual(len(self._all_buttons()), 10)
        self.assertGreater(self.toolbar.sizeHint().height(), 300, "it wants all its buttons on show")
        self.assertLess(self.toolbar.minimumSizeHint().height(), 100, "but it does not insist on them")
        self.assertLess(self.panel.minimumSizeHint().height(), 400, "so the panel's own minimum height is not the toolbar's")

    def test_with_room_there_are_no_arrows_and_every_button_is_in_view(self) -> None:
        self._show(1000)
        self.assertFalse(self.column.is_overflowing())
        self.assertFalse(self.column.up_arrow().isVisible() or self.column.down_arrow().isVisible())
        for button in self._all_buttons():
            self.assertTrue(self._visible_in_column(button), button.toolTip()[:30])

    def test_in_a_short_panel_the_arrows_appear_and_scroll_the_column_to_the_last_button(self) -> None:
        self._show(self.panel.minimumSizeHint().height() + 20)
        self.assertTrue(self.column.is_overflowing())
        self.assertTrue(self.column.up_arrow().isVisible() and self.column.down_arrow().isVisible())
        self.assertFalse(self.column.up_arrow().isEnabled(), "at the top, nothing above")
        self.assertTrue(self.column.down_arrow().isEnabled())
        self.assertFalse(self._visible_in_column(self.toolbar.lock_button), "the last button (Lock) is out of view at first")
        guard = 0
        while self.column.down_arrow().isEnabled() and guard < 40:
            self.column.down_arrow().click()
            guard += 1
        self.assertEqual(self.column.scroll_value(), self.column.scroll_maximum())
        self.assertTrue(self._visible_in_column(self.toolbar.lock_button))
        self.assertFalse(self.column.down_arrow().isEnabled(), "at the bottom")
        self.assertTrue(self.column.up_arrow().isEnabled())
        self.column.up_arrow().click()
        self.assertLess(self.column.scroll_value(), self.column.scroll_maximum())

    def test_the_wheel_over_the_column_scrolls_it_and_does_nothing_when_it_fits(self) -> None:
        self._show(1000)
        self._wheel(-120)
        self.assertEqual(self.column.scroll_value(), 0)
        self._show(self.panel.minimumSizeHint().height() + 20)
        self.assertTrue(self.column.is_overflowing())
        self._wheel(-120)  # towards the user: down
        self.assertGreater(self.column.scroll_value(), 0)
        self._wheel(120)
        self.assertEqual(self.column.scroll_value(), 0)

    def test_ensure_visible_brings_a_button_into_view(self) -> None:
        self._show(self.panel.minimumSizeHint().height() + 20)
        self.assertFalse(self._visible_in_column(self.toolbar.lock_button))
        self.column.ensure_visible(self.toolbar.lock_button)
        self.assertTrue(self._visible_in_column(self.toolbar.lock_button))

    def test_growing_the_panel_again_hides_the_arrows(self) -> None:
        self._show(self.panel.minimumSizeHint().height() + 20)
        self.assertTrue(self.column.is_overflowing())
        self.panel.resize(1100, 1000)
        _pump(0.2)
        self.assertFalse(self.column.is_overflowing())
        self.assertFalse(self.column.down_arrow().isVisible())


if __name__ == "__main__":
    unittest.main()
