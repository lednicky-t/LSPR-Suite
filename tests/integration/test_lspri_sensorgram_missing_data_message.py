"""Regression tests for `AnalysisController._render_sensorgram_display`'s
handling of a multi-ROI selection where some/all selected ROIs have no
sensorgram trace available yet (RAM cache or HDF5 backup).

Previously (see the now-deleted `_ensure_sensorgram_traces` /
`_sensorgram_backfill_active`), a missing ROI silently launched a background
sensorgram computation - the maintainer decided analysis should only ever be
triggered by explicitly pressing "Start analysis", never automatically from
selecting ROIs or changing the Sensogram display mode. Now a missing ROI is
just reported via the summary text; nothing is computed. These tests assert
on that directly (spies + the real summary-text value), not by reproducing
the full plot-widget machinery - same approach as
test_lspri_sensorgram_start_reentrancy.py and
test_lspri_sensorgram_backfill.py (now deleted) before it.
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

# Must exist before any lspr_imaging_app.gui module is imported below - some
# Qt objects are touched at import time.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.analysis_controller import AnalysisController


def _fake_roi(roi_id: int) -> SimpleNamespace:
    return SimpleNamespace(area_roi_id=roi_id)


class _FakeSummaryLabel:
    def __init__(self) -> None:
        self.text: str | None = None

    def setText(self, text: str) -> None:
        self.text = text


class _FakeWindow:
    """Duck-typed stand-in for `_render_sensorgram_display` - no real
    MainWindow (Qt plot widgets, live dataset). Anything not explicitly set
    below auto-vivifies into a harmless MagicMock via __getattr__ (the
    method touches several plot-item widgets whose internals these tests
    don't care about - only whether a computation gets triggered and what
    the summary text ends up saying)."""

    def __init__(self, selected_roi_ids) -> None:
        self._selected_roi_ids = set(selected_roi_ids)
        self._state = SimpleNamespace(
            area_rois=[_fake_roi(1), _fake_roi(2), _fake_roi(3)],
            area_roi_groups=[],
            statistics_settings=SimpleNamespace(
                sensorgram_display_mode="average_all", sensorgram_aggregation="mean", sensorgram_band="sd"
            ),
        )
        self._sensorgram_running = False
        self.sensorgram_summary_label = _FakeSummaryLabel()
        # No ROI belongs to a group in these tests - _sensorgram_group_buckets
        # (now consulted even in "average_all" mode, to check whether the
        # selection collapses to a single group) needs this to return None,
        # not an auto-vivified MagicMock, or every ROI would look like it
        # belongs to some fake group.
        self._group_for_roi = lambda roi_id: None
        # Real (not auto-mocked) values: the "len(bucket_traces) == 1" draw
        # branch passes these straight into real pyqtgraph/Qt calls
        # (pg.mkPen, QPen.setStyle, float(...)) which reject a MagicMock.
        self._sensorgram_line_width_px = 2.2
        self._sensorgram_line_style = Qt.PenStyle.SolidLine
        self._sensorgram_show_symbols = True
        self._sensorgram_symbol_size_px = 6.0
        self._sensorgram_average_all_color = QColor("#38bdf8")

    def __getattr__(self, name: str):
        value = mock.MagicMock()
        setattr(self, name, value)
        return value


class RenderSensorgramDisplayMissingDataTests(unittest.TestCase):
    def test_all_rois_missing_reports_message_and_triggers_nothing(self) -> None:
        window = _FakeWindow(selected_roi_ids=(1, 2, 3))
        controller = AnalysisController(window)

        with mock.patch.object(controller, "available_analysis_spectral_cubes", return_value=[0, 1, 2]), \
                mock.patch.object(controller, "_sensorgram_x_values", return_value=np.array([0.0, 1.0, 2.0])), \
                mock.patch.object(controller, "_sensorgram_spectral_cube_signatures", return_value=("sig",)), \
                mock.patch.object(controller, "_sensorgram_trace_for_roi", return_value=None), \
                mock.patch.object(controller, "_start_sensorgram_worker") as start_mock:
            controller._render_sensorgram_display()

        start_mock.assert_not_called()
        self.assertIsNotNone(window.sensorgram_summary_label.text)
        self.assertIn("3 of 3", window.sensorgram_summary_label.text)
        self.assertIn("Start analysis", window.sensorgram_summary_label.text)

    def test_some_rois_missing_reports_count_and_still_draws_available_data(self) -> None:
        window = _FakeWindow(selected_roi_ids=(1, 2, 3))
        controller = AnalysisController(window)

        def fake_trace(roi_id: int, *_args, **_kwargs):
            return None if int(roi_id) == 3 else np.array([1.0, 2.0, 3.0])

        with mock.patch.object(controller, "available_analysis_spectral_cubes", return_value=[0, 1, 2]), \
                mock.patch.object(controller, "_sensorgram_x_values", return_value=np.array([0.0, 1.0, 2.0])), \
                mock.patch.object(controller, "_sensorgram_spectral_cube_signatures", return_value=("sig",)), \
                mock.patch.object(controller, "_sensorgram_trace_for_roi", side_effect=fake_trace), \
                mock.patch.object(controller, "_update_processed_trace_overlay"), \
                mock.patch.object(controller, "_start_sensorgram_worker") as start_mock:
            controller._render_sensorgram_display()

        start_mock.assert_not_called()
        self.assertIsNotNone(window.sensorgram_summary_label.text)
        self.assertIn("1 of 3", window.sensorgram_summary_label.text)
        # ROIs 1 and 2 are still averaged and drawn despite ROI 3 missing.
        window.sensorgram_curve.setData.assert_called_once()
        plotted_x, plotted_y = window.sensorgram_curve.setData.call_args[0]
        np.testing.assert_allclose(np.asarray(plotted_x), [0.0, 1.0, 2.0])
        np.testing.assert_allclose(np.asarray(plotted_y), [1.0, 2.0, 3.0])

    def test_nothing_missing_leaves_summary_text_untouched(self) -> None:
        window = _FakeWindow(selected_roi_ids=(1, 2))
        controller = AnalysisController(window)

        with mock.patch.object(controller, "available_analysis_spectral_cubes", return_value=[0, 1, 2]), \
                mock.patch.object(controller, "_sensorgram_x_values", return_value=np.array([0.0, 1.0, 2.0])), \
                mock.patch.object(controller, "_sensorgram_spectral_cube_signatures", return_value=("sig",)), \
                mock.patch.object(controller, "_sensorgram_trace_for_roi", return_value=np.array([1.0, 2.0, 3.0])), \
                mock.patch.object(controller, "_update_processed_trace_overlay"), \
                mock.patch.object(controller, "_start_sensorgram_worker") as start_mock:
            controller._render_sensorgram_display()

        start_mock.assert_not_called()
        self.assertIsNone(window.sensorgram_summary_label.text)


if __name__ == "__main__":
    unittest.main()
