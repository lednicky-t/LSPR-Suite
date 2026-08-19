"""Regression test: the "relative-threshold" mask preview (the eye-toggle
button next to the relative-mask threshold/sigma controls) computed a
candidate mask correctly, but nothing ever drew it - `_on_mask_preview_ready`
stored the "relative" tool's result into `window._mask_histogram_preview`,
while `_update_ignore_mask_overlay` unconditionally hid `histogram_mask_item`
and only ever rendered `_mask_figure_preview` (populated by the
"local_contrast"/"morphology" tools instead). So turning the relative preview
on, or changing its threshold/sigma, never changed anything on screen - the
maintainer reported exactly that symptom ("apply the preview... nothing
happen[s]").

This pins down that `_update_ignore_mask_overlay` now actually reads and
renders `_mask_histogram_preview` via `histogram_mask_item`.
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtGui import QColor

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.domain.models import PreprocessingSettings  # noqa: E402
from lspr_imaging_app.gui.overlay_manager import OverlayManager  # noqa: E402


class _FakeState:
    def __init__(self) -> None:
        self.preprocessing = PreprocessingSettings()


class _FakeWindow:
    def __init__(self, image_shape: tuple[int, int] = (20, 20)) -> None:
        self._showing_background_profile_main = False
        self._current_processed_image = np.zeros(image_shape, dtype=np.float32)
        self._state = _FakeState()
        self._mask_visible = True
        self._mask_visual_color = QColor(255, 0, 0)
        self._mask_alpha = 0.4
        self._mask_histogram_preview: np.ndarray | None = None
        self._mask_figure_preview: np.ndarray | None = None
        self.ignore_mask_item = mock.MagicMock()
        self.histogram_mask_item = mock.MagicMock()
        self.figure_mask_item = mock.MagicMock()
        self._ignored_mask = mock.MagicMock(return_value=np.zeros(image_shape, dtype=bool))


class MaskPreviewOverlayTests(unittest.TestCase):
    def test_relative_preview_is_drawn_when_present(self) -> None:
        window = _FakeWindow()
        candidate = np.zeros((20, 20), dtype=bool)
        candidate[5:10, 5:10] = True
        window._mask_histogram_preview = candidate

        OverlayManager(window)._update_ignore_mask_overlay()

        window.histogram_mask_item.setImage.assert_called_once()
        window.histogram_mask_item.show.assert_called_once()
        window.histogram_mask_item.hide.assert_not_called()

    def test_relative_preview_hidden_when_absent(self) -> None:
        window = _FakeWindow()
        window._mask_histogram_preview = None

        OverlayManager(window)._update_ignore_mask_overlay()

        window.histogram_mask_item.setImage.assert_not_called()
        window.histogram_mask_item.hide.assert_called()

    def test_relative_preview_hidden_when_all_false(self) -> None:
        window = _FakeWindow()
        window._mask_histogram_preview = np.zeros((20, 20), dtype=bool)

        OverlayManager(window)._update_ignore_mask_overlay()

        window.histogram_mask_item.setImage.assert_not_called()
        window.histogram_mask_item.hide.assert_called()

    def test_relative_and_figure_previews_can_render_simultaneously(self) -> None:
        window = _FakeWindow()
        relative_candidate = np.zeros((20, 20), dtype=bool)
        relative_candidate[0:5, 0:5] = True
        figure_candidate = np.zeros((20, 20), dtype=bool)
        figure_candidate[15:20, 15:20] = True
        window._mask_histogram_preview = relative_candidate
        window._mask_figure_preview = figure_candidate

        OverlayManager(window)._update_ignore_mask_overlay()

        window.histogram_mask_item.show.assert_called_once()
        window.figure_mask_item.show.assert_called_once()


if __name__ == "__main__":
    unittest.main()
