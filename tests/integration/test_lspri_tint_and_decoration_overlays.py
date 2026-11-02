"""`TintOverlays` and `DecorationOverlays` (panels/image/tint_overlays.py, decoration_overlays.py), built WITHOUT an `ImagePanel`.

Both were cut out of `ImagePanel` (maintainability audit item D, batch 1) and take what they need from the panel as small functions, so
they can be driven alone: real pyqtgraph items, real modules, fake functions. A real `QApplication` built in-process, never `.exec()`.
The panel-level behaviour (the overlays appearing on screen, the settings round trip) stays pinned by the Image panel tests."""

from __future__ import annotations

import sys
import unittest

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtWidgets
from PyQt6.QtGui import QColor

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.image_tools import ChromaticModule, GeometryModule
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.panels.image.decoration_overlays import DecorationOverlays
    from lspr_imaging_app.panels.image.overlay_tint import OverlayTint
    from lspr_imaging_app.panels.image.scale_bar_overlay import ScaleBarItem
    from lspr_imaging_app.panels.image.tint_overlays import TintOverlays
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class _FixedRange:
    """Stands in for `HighlightRangeModule`: all `TintOverlays` asks of it is `current_range()`."""

    def __init__(self, value: tuple[float, float] | None) -> None:
        self.value = value

    def current_range(self) -> tuple[float, float] | None:
        return self.value


class TintOverlaysTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mask_tint = OverlayTint(QColor("#ff0000"), 0.5)
        self.highlight_tint = OverlayTint(QColor("#00ff00"), 0.4)
        self.range = _FixedRange((4.0, 8.0))
        self.image: np.ndarray | None = np.arange(16, dtype=np.float32).reshape(4, 4)
        self.data_range: tuple[float, float] | None = (0.0, 15.0)
        self.background_on = False
        self.styles: list[tuple[str, object]] = []
        self.tints = TintOverlays(
            self.mask_tint,
            self.highlight_tint,
            self.range,
            display_image=lambda: self.image,
            data_range=lambda: self.data_range,
            show_background=lambda: self.background_on,
            style_changed=lambda kind, style: self.styles.append((kind, style)),
        )

    def test_the_highlight_tints_exactly_the_pixels_in_range(self) -> None:
        self.tints.update_highlight()
        self.assertTrue(self.highlight_tint.item.isVisible())
        alpha = self.highlight_tint.item.image[..., 3]
        np.testing.assert_array_equal(alpha > 0, (self.image >= 4.0) & (self.image <= 8.0))

    def test_the_highlight_is_hidden_when_nothing_or_everything_is_in_range(self) -> None:
        for value in ((100.0, 200.0), (-5.0, 99.0)):  # above the data / around the whole data range
            with self.subTest(range=value):
                self.range.value = value
                self.highlight_tint.item.show()
                self.tints.update_highlight()
                self.assertFalse(self.highlight_tint.item.isVisible())

    def test_the_highlight_is_hidden_without_an_image_or_while_the_background_estimate_is_shown(self) -> None:
        self.tints.update_highlight()
        self.assertTrue(self.highlight_tint.item.isVisible())
        self.background_on = True
        self.tints.update_highlight()
        self.assertFalse(self.highlight_tint.item.isVisible())
        self.background_on = False
        self.image = None
        self.tints.update_highlight()
        self.assertFalse(self.highlight_tint.item.isVisible())

    def test_a_highlight_control_changes_the_tint_and_reports_the_new_look(self) -> None:
        self.tints.on_highlight_color_changed(QColor("#0000ff"))
        self.tints.on_highlight_alpha_changed(0.9)
        self.tints.on_highlight_visibility_changed(False)
        self.assertEqual([kind for kind, _ in self.styles], ["highlight"] * 3)
        last = self.styles[-1][1]
        self.assertEqual((last.visible, last.color, last.alpha), (False, "#0000ff", 0.9))
        self.assertFalse(self.highlight_tint.item.isVisible())

    def test_the_mask_is_cached_so_a_control_can_redraw_without_the_render_pipeline(self) -> None:
        authored = np.zeros((6, 6), dtype=bool)
        authored[2:4, 2:4] = True
        geometry = GeometrySettings()
        self.tints.update_mask(authored, geometry, None, hidden=False)
        self.assertTrue(self.mask_tint.item.isVisible())
        self.assertEqual(self.tints.mask_state[3], False)
        self.tints.on_mask_visibility_changed(False)  # hides it again from the cache alone
        self.assertFalse(self.mask_tint.item.isVisible())
        self.tints.on_mask_visibility_changed(True)
        self.assertTrue(self.mask_tint.item.isVisible())
        self.assertEqual([kind for kind, _ in self.styles], ["mask", "mask"])

    def test_a_preview_tool_hides_the_mask_and_clear_forgets_everything(self) -> None:
        authored = np.ones((4, 4), dtype=bool)
        self.tints.update_mask(authored, GeometrySettings(), None, hidden=True)
        self.assertFalse(self.mask_tint.item.isVisible())
        self.assertIsNotNone(self.tints.mask_state)  # remembered, so the tint comes back when the tool is left
        self.tints.update_highlight()
        self.tints.clear()
        self.assertIsNone(self.tints.mask_state)
        self.assertFalse(self.highlight_tint.item.isVisible())


class DecorationOverlaysTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plot = pg.PlotItem()
        self.view = QtWidgets.QGraphicsView()
        self.crop_curve = pg.PlotDataItem()
        self.preview_tool = False
        self.redraws = 0
        self.view_changes: list[tuple[bool, bool]] = []
        self.geometry = GeometryModule()
        self.decorations = DecorationOverlays(
            DatasetModule(),
            self.geometry,
            ChromaticModule(),
            view=self.view,
            chunk_grid_curve=pg.PlotDataItem(),
            source_grid_curve=pg.PlotDataItem(),
            crop_outline_curve=self.crop_curve,
            landmark_line_item=pg.PlotDataItem(),
            landmark_observed_item=pg.ScatterPlotItem(),
            landmark_fitted_item=pg.ScatterPlotItem(),
            scale_bar_item=ScaleBarItem(self.plot.vb),
            preview_tool_active=lambda: self.preview_tool,
            last_image_shape=lambda: (100, 200),
            current_cube=lambda: 0,
            current_wavelength=lambda: 550.0,
            schedule_redraw=self._count_redraw,
            view_changed=lambda visible, every: self.view_changes.append((visible, every)),
            landmarks_visible=True,
            landmarks_all_wavelengths=False,
        )

    def _count_redraw(self) -> None:
        self.redraws += 1

    def test_the_landmark_toggles_redraw_and_report_both_values(self) -> None:
        self.decorations.on_show_landmarks_changed(False)
        self.decorations.on_landmark_scope_changed(True)
        self.assertEqual(self.redraws, 2)
        self.assertEqual(self.view_changes, [(False, False), (False, True)])
        self.assertFalse(self.decorations.landmarks_visible)
        self.assertTrue(self.decorations.landmarks_all_wavelengths)

    def _drawn_points(self) -> int:
        xs = self.crop_curve.getData()[0]
        return 0 if xs is None else len(xs)

    def test_the_crop_outline_shows_only_while_a_preview_tool_is_active(self) -> None:
        self.geometry.set_crop(10, 20, 30, 40)
        self.decorations.draw_crop_outline()
        self.assertEqual(self._drawn_points(), 0)
        self.preview_tool = True
        self.decorations.draw_crop_outline()
        xs, ys = self.crop_curve.getData()
        self.assertEqual((list(xs), list(ys)), ([10, 40, 40, 10, 10], [20, 20, 60, 60, 20]))
        self.preview_tool = False
        self.decorations.draw_crop_outline()
        self.assertEqual(self._drawn_points(), 0)

    def test_grid_lines_run_at_every_chunk_boundary_from_the_offset(self) -> None:
        identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])

        def lines(offset_x: int) -> tuple[list[float], list[float]]:
            """x of every vertical line and y of every horizontal one; the polyline is two points per line, a NaN between lines."""
            xs, ys = (np.array(v) for v in DecorationOverlays.grid_polyline((100, 200), identity, 50, 50, offset_x, 0))
            points = list(zip(xs, ys))
            segments = [points[i : i + 2] for i in range(0, len(points), 3)]  # (start, end, NaN) triples
            vertical = [a[0] for a, b in segments if a[0] == b[0]]
            horizontal = [a[1] for a, b in segments if a[1] == b[1]]
            return vertical, horizontal

        self.assertEqual(lines(0), ([0.0, 50.0, 100.0, 150.0, 200.0], [0.0, 50.0, 100.0]))
        self.assertEqual(lines(25), ([25.0, 75.0, 125.0, 175.0], [0.0, 50.0, 100.0]))


if __name__ == "__main__":
    unittest.main()
