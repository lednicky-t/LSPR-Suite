"""Regression tests for the Sensogram's live-during-a-run display, for a
multi-ROI selection using "Average by group"/"Average all"/"Individual"
(`domain/models.py`'s `StatisticsSettings.sensorgram_display_mode`).

Before this fix, `AnalysisController._render_sensorgram_display` skipped its
mode-aware bucket logic entirely whenever `window._sensorgram_running` was
True, falling back to a single legacy trace (`window._sensorgram_metric_
values`, `_sensorgram_metric_task`'s "combine each ROI's own spectrum, then
fit once" value - see `analysis_tasks.py`, the same computation the
Individual/Average all/Average by group redesign retired for every OTHER
path). So a multi-ROI group selection during a "Start analysis" run showed
that one legacy trace growing, never the correct per-mode view, even though
each ROI's own live per-cube value was already available (delivered every
cube via `on_sensorgram_partial_result`).

The fix: `window._sensorgram_live_per_roi_values` accumulates each selected
ROI's own value as it arrives live (`on_sensorgram_partial_result`), and
`_render_sensorgram_display` sources from it (via the new
`_render_live_sensorgram_update`/`_sensorgram_display_buckets`/
`_render_sensorgram_buckets` helpers, shared with the idle RAM/disk-backed
path) whenever a run is in progress and more than one ROI is selected -
falling back to the legacy single trace only when no live bucket has data
yet (e.g. the very first tick of a run).
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

import numpy as np
from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt

# Must exist before any lspr_imaging_app.gui module is imported below - some
# Qt objects are touched at import time.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import AreaRoiGroup, StatisticsSettings
from lspr_imaging_app.gui.analysis_controller import AnalysisController


def _fake_roi(roi_id: int) -> SimpleNamespace:
    return SimpleNamespace(area_roi_id=roi_id, sample_color_hex=None, reference_color_hex=None)


class _FakeSummaryLabel:
    def __init__(self) -> None:
        self.text: str | None = None

    def setText(self, text: str) -> None:
        self.text = text


class _FakeWindow:
    """Duck-typed stand-in for `_render_sensorgram_display` while a run is
    in progress - no real MainWindow (Qt plot widgets, live dataset).
    Anything not explicitly set below auto-vivifies into a harmless
    MagicMock via __getattr__, same approach as
    test_lspri_sensorgram_missing_data_message.py."""

    def __init__(self, selected_roi_ids, *, mode: str, groups: list[AreaRoiGroup] | None = None) -> None:
        self._selected_roi_ids = set(selected_roi_ids)
        self._state = SimpleNamespace(
            dataset=None,
            area_rois=[_fake_roi(1), _fake_roi(2), _fake_roi(3)],
            area_roi_groups=groups or [],
            statistics_settings=StatisticsSettings(
                sensorgram_display_mode=mode, sensorgram_aggregation="mean", sensorgram_band="sd"
            ),
        )
        self._sensorgram_running = True
        self._sensorgram_spectral_cube_indices = np.asarray([0, 1, 2], dtype=np.int32)
        self._sensorgram_metric_values = np.asarray([10.0, 10.0, 10.0], dtype=np.float64)
        self._sensorgram_live_per_roi_values: dict[int, dict[int, float]] = {}
        self.sensorgram_summary_label = _FakeSummaryLabel()
        self._sensorgram_series_items: list = []
        group_by_roi = {roi_id: group for group in (groups or []) for roi_id in group.area_roi_ids}
        self._group_for_roi = lambda roi_id: group_by_roi.get(int(roi_id))
        self._sensorgram_average_all_color = None
        self._sensorgram_roi_gradient_palette = "viridis"
        self._sample_visual_color = "#38bdf8"
        # Real (not auto-mocked) values: the drawing branches pass these
        # straight into real pyqtgraph/Qt calls (pg.mkPen, float(...)) which
        # reject a MagicMock.
        self._sensorgram_line_width_px = 2.2
        self._sensorgram_line_style = Qt.PenStyle.SolidLine
        self._sensorgram_show_symbols = True
        self._sensorgram_symbol_size_px = 6.0
        self._sensorgram_active_curve_color = None

    def __getattr__(self, name: str):
        from unittest import mock

        value = mock.MagicMock()
        setattr(self, name, value)
        return value


def _make_group(roi_ids: list[int], *, color: str = "#a855f7") -> AreaRoiGroup:
    return AreaRoiGroup(group_id="g1", name="Group 1", sample_color_hex=color, area_roi_ids=roi_ids)


class LiveGroupAverageDisplayTests(unittest.TestCase):
    def test_average_by_group_draws_the_live_group_average_not_the_legacy_trace(self) -> None:
        window = _FakeWindow((1, 2), mode="average_by_group", groups=[_make_group([1, 2])])
        controller = AnalysisController(window)
        window._sensorgram_live_per_roi_values = {
            1: {0: 1.0, 1: 2.0, 2: 3.0},
            2: {0: 3.0, 1: 4.0, 2: 5.0},
        }

        controller._render_sensorgram_display()

        # The group average of (1,3), (2,4), (3,5) is (2, 3, 4) - nothing
        # like the legacy single trace (10.0, 10.0, 10.0) set on the window.
        plotted_x, plotted_y = window.sensorgram_curve.setData.call_args[0]
        np.testing.assert_allclose(np.sort(plotted_y), [2.0, 3.0, 4.0])
        self.assertEqual(len(plotted_x), 3)
        # The group's own color was used, matching every other mode/path.
        self.assertEqual(window._sensorgram_active_curve_color.name(), "#a855f7")
        # The single-ROI current-point marker doesn't make sense for a
        # multi-ROI aggregate and must not linger from the legacy path.
        window.sensorgram_current_point.hide.assert_called()

    def test_individual_mode_draws_one_curve_per_roi_live(self) -> None:
        window = _FakeWindow((1, 2), mode="individual")
        controller = AnalysisController(window)
        window._sensorgram_live_per_roi_values = {
            1: {0: 1.0, 1: 2.0, 2: 3.0},
            2: {0: 5.0, 1: 6.0, 2: 7.0},
        }

        controller._render_sensorgram_display()

        # Two ROIs with no shared group -> two separate live curve items,
        # not the single legacy trace.
        self.assertEqual(window.sensorgram_plot.plot.call_count, 2)
        window.sensorgram_curve.hide.assert_called()

    def test_falls_back_to_legacy_trace_when_no_live_data_yet(self) -> None:
        # Right at the start of a run - on_sensorgram_partial_result hasn't
        # delivered anything yet, so there's nothing to build a live bucket
        # view from. Must not hide/blank the plot in this case; the caller
        # (the <=1-or-running branch) draws the legacy single trace instead.
        window = _FakeWindow((1, 2), mode="average_by_group", groups=[_make_group([1, 2])])
        controller = AnalysisController(window)

        controller._render_sensorgram_display()

        # No live-bucket data was available, so the legacy single-trace
        # branch ran instead: it shows the curve but never calls setData
        # itself (assumes set_sensorgram_series already populated it) and
        # never touches the bucket-only state the live path would have set.
        window.sensorgram_curve.setData.assert_not_called()
        window.sensorgram_curve.show.assert_called()
        self.assertIsNone(window._sensorgram_active_curve_color)
        window.sensorgram_plot.plot.assert_not_called()

    def test_average_all_uses_configured_color_for_a_mixed_selection(self) -> None:
        # ROI 1 is in a group, ROI 2 is not - a genuinely mixed selection
        # (not "everyone happens to be ungrouped", which the average_all
        # branch also treats as a single collapsed bucket) - so Average
        # all's configurable fallback color applies, not a group color.
        window = _FakeWindow((1, 2), mode="average_all", groups=[_make_group([1])])
        controller = AnalysisController(window)
        window._sensorgram_average_all_color = "#123456"
        window._sensorgram_live_per_roi_values = {
            1: {0: 1.0, 1: 2.0, 2: 3.0},
            2: {0: 3.0, 1: 4.0, 2: 5.0},
        }

        controller._render_sensorgram_display()

        plotted_x, plotted_y = window.sensorgram_curve.setData.call_args[0]
        np.testing.assert_allclose(np.sort(plotted_y), [2.0, 3.0, 4.0])
        self.assertEqual(window._sensorgram_active_curve_color.name(), "#123456")


if __name__ == "__main__":
    unittest.main()
