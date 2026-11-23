"""The corner label of the selection's numbers and the blinking View eye (2026-11-23)."""

from __future__ import annotations

import sys
import time
import unittest

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtGui import QIcon, QPixmap

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import pyqtgraph as pg

    from lspr_imaging_app.image_tools import GeometryModule
    from lspr_imaging_app.panels.image.selection_stats_overlay import SelectionStatsOverlay
    from lspr_imaging_app.panels.image.selection_view_blink import ViewButtonBlink
    from lspr_imaging_app.selection import AreaSelectionModule
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

SHAPE = (100, 200)


def _wait(condition, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _APP.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return False


def _region() -> np.ndarray:
    region = np.zeros(SHAPE, dtype=bool)
    region[10:20, 10:20] = True
    region[50:60, 100:110] = True
    return region  # 200 px of 20000 = 1 %, two blobs


class SelectionStatsOverlayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.selection = AreaSelectionModule()
        self.geometry = GeometryModule()
        self.view_box = pg.ViewBox()
        self.overlay = SelectionStatsOverlay(self.view_box, self.selection, self.geometry, lambda: SHAPE)

    def test_label_follows_the_selection(self) -> None:
        item = self.overlay.item()
        self.assertFalse(item.isVisible())
        self.selection.combine_region(_region())
        self.assertTrue(_wait(lambda: item.isVisible()))
        lines = item.lines()
        self.assertEqual(lines[0], "Selected: 1.0 %")
        self.assertTrue(lines[1].startswith("Blobs: n = 2"), lines[1])
        self.assertTrue(lines[2].startswith("⌀ = 11.3 ± 0.0 px"), lines[2])  # two equal blobs: 2 * sqrt(100 / pi) = 11.28, no spread
        self.selection.clear()
        self.assertFalse(item.isVisible())  # nothing selected: the label goes at once

    def test_a_burst_of_changes_ends_on_the_newest(self) -> None:
        for _ in range(5):
            self.selection.combine_region(_region())
        big = np.zeros(SHAPE, dtype=bool)
        big[:, :100] = True
        self.selection.combine_region(big, "replace")
        self.assertTrue(_wait(lambda: self.overlay.item().lines()[:1] == ("Selected: 50.0 %",)))
        self.assertTrue(self.overlay.item().lines()[1].startswith("Blobs: n = 1"))


class ViewButtonBlinkTest(unittest.TestCase):
    def test_blinks_nonstop_while_a_selection_exists_and_stops_plain_without_one(self) -> None:
        selection = AreaSelectionModule()
        button = QtWidgets.QToolButton()
        plain, lit = QIcon(QPixmap(8, 8)), QIcon(QPixmap(8, 8))
        blink = ViewButtonBlink(button, selection, button)
        blink.set_icons(plain, lit)
        shown: list[int] = []
        original = button.setIcon
        button.setIcon = lambda icon: (shown.append(icon.cacheKey()), original(icon))[1]  # type: ignore[method-assign]
        blink._timer.setInterval(20)  # the real phase is 500 ms; the rhythm is what is tested
        selection.combine_region(_region())
        self.assertTrue(blink.is_blinking())
        self.assertEqual(shown[-1], lit.cacheKey())  # lit at once
        self.assertTrue(_wait(lambda: len(shown) >= 6))  # keeps going: lit, plain, lit, plain, ...
        self.assertEqual(shown[:6], [lit.cacheKey(), plain.cacheKey()] * 3)
        self.assertTrue(blink.is_blinking())
        selection.clear()
        self.assertEqual(shown[-1], plain.cacheKey())
        self.assertFalse(blink.is_blinking())


if __name__ == "__main__":
    unittest.main()
