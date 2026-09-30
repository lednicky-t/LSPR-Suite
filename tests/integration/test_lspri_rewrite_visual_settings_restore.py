"""Regression test: Histogram display settings and the Image panel's
viewport (pan/zoom) both restore across a restart (2026-09-30, maintainer
request: "there are histogram settings, whose are not remembered (restore)
after app launch... Same applies for image area, position").

Same shape as `test_lspri_workflow_panel_stage_restore.py`:
`AppSettings.histogram_*` / `HistogramPanel(initial_*=...)` /
`display_settings_changed` -> `_persist`, and `AppSettings.image_view_*` /
`ImagePanel(initial_view_range=...)` / `view_range_changed` -> `_persist`,
both wired in `app_rewrite.py`'s `build_main_window`. Panel-level behavior
(the one-shot restore, the debounced persist) is pinned in
`test_lspri_rewrite_histogram_panel.py` and
`test_lspri_rewrite_image_panel.py`; this file only pins that
`build_main_window` actually wires those panels to `AppSettings`, the same
"one field plus one wiring line" mechanism already used for
theme/active_workflow_stage.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.app_rewrite import build_main_window
    from lspr_imaging_app.panels.dock_container import PanelContainer
    from lspr_imaging_app.panels.histogram import HistogramPanel
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.storage.app_settings import AppSettings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


def _panel(window: QtWidgets.QMainWindow, title: str, panel_type: type) -> object:
    docks = [dock for dock in window.findChildren(PanelContainer) if dock.windowTitle() == title]
    return docks[0].findChild(panel_type)


class VisualSettingsRestoreTests(unittest.TestCase):
    def _build(self, **kwargs: object) -> QtWidgets.QMainWindow:
        window = build_main_window(**kwargs)
        self.addCleanup(window.close)
        self.addCleanup(window.deleteLater)
        window.show()
        _APP.processEvents()
        return window

    # -- Histogram ------------------------------------------------------

    def test_no_saved_histogram_settings_fall_back_to_the_panel_defaults(self) -> None:
        panel = _panel(self._build(), "Histogram", HistogramPanel)
        self.assertEqual(panel._y_mode, "percent")
        self.assertFalse(panel._log_y)

    def test_saved_histogram_settings_are_applied_at_launch(self) -> None:
        window = self._build(
            initial_settings=AppSettings(
                histogram_y_mode="counts",
                histogram_log_y=True,
                histogram_bin_width_px=128.0,
                histogram_line_width_px=2.5,
            )
        )
        panel = _panel(window, "Histogram", HistogramPanel)
        self.assertEqual(panel._y_mode, "counts")
        self.assertTrue(panel._log_y)
        self.assertEqual(panel._bin_width, 128.0)
        self.assertEqual(panel._line_width, 2.5)

    def test_changing_a_histogram_setting_persists_it(self) -> None:
        saved: list[AppSettings] = []
        window = self._build(on_settings_changed=saved.append)
        panel = _panel(window, "Histogram", HistogramPanel)
        panel._show_settings_dialog()
        panel._settings_dialog.scale_combo.setCurrentIndex(1)  # Log
        self.assertTrue(saved)
        self.assertTrue(saved[-1].histogram_log_y)

    # -- Image viewport ---------------------------------------------------

    def test_no_saved_view_range_is_passed_through_as_none(self) -> None:
        panel = _panel(self._build(), "Image", ImagePanel)
        self.assertIsNone(panel._initial_view_range)

    def test_a_saved_view_range_reaches_the_image_panel(self) -> None:
        window = self._build(
            initial_settings=AppSettings(
                image_view_x_min=1.0, image_view_x_max=79.0, image_view_y_min=2.0, image_view_y_max=63.0,
            )
        )
        panel = _panel(window, "Image", ImagePanel)
        self.assertEqual(panel._initial_view_range, ((1.0, 79.0), (2.0, 63.0)))

    def test_a_partially_saved_view_range_is_treated_as_none(self) -> None:
        """All four fields are written together (`_emit_view_range_changed`
        always emits all four) - a settings file with only some of them set
        (hand-edited, or a future partial-write bug) must not be applied as
        a bogus range rather than silently falling back."""
        window = self._build(initial_settings=AppSettings(image_view_x_min=1.0, image_view_x_max=79.0))
        panel = _panel(window, "Image", ImagePanel)
        self.assertIsNone(panel._initial_view_range)

    def test_the_image_panel_reporting_a_new_view_range_persists_it(self) -> None:
        saved: list[AppSettings] = []
        window = self._build(on_settings_changed=saved.append)
        panel = _panel(window, "Image", ImagePanel)
        panel.view_range_changed.emit(3.0, 77.0, 4.0, 60.0)
        self.assertTrue(saved)
        self.assertEqual(
            (saved[-1].image_view_x_min, saved[-1].image_view_x_max,
             saved[-1].image_view_y_min, saved[-1].image_view_y_max),
            (3.0, 77.0, 4.0, 60.0),
        )


if __name__ == "__main__":
    unittest.main()
