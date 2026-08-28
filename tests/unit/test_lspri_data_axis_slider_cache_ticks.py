"""Regression tests for DataAxisSlider's cached-tick indicator, added for the
Cube/Time slider feature: ticks representing spectral cubes already cached in
RAM for the current ROI selection paint in a distinct color
(AnalysisController._refresh_cube_slider_cache_indicators computes the set;
DataAxisSlider.set_tick_cache_state / paintEvent renders it).

Covers the one correctness trap in this feature: cached-tick state is stored
as *positions* into the values array passed to set_ticks, so a genuinely new
values array (different dataset, changed spectral-cube range) must invalidate
any previously computed cache-state - otherwise a stale position could color
the wrong tick after the underlying data changes. A re-call with the *same*
values (e.g. relabeling for the Cube/Time display toggle) must NOT clear it,
or every toggle would blank out an indicator that's still accurate.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - Qt
# objects get built at import time in some of those modules.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.widgets import DataAxisSlider  # noqa: E402


class DataAxisSliderCacheTickTests(unittest.TestCase):
    def test_set_tick_cache_state_stores_positions(self) -> None:
        slider = DataAxisSlider()
        slider.set_ticks([10, 11, 12, 13], {})
        slider.set_tick_cache_state(frozenset({0, 2}))
        self.assertEqual(slider._cached_tick_indices, frozenset({0, 2}))

    def test_new_values_array_clears_stale_cache_state(self) -> None:
        slider = DataAxisSlider()
        slider.set_ticks([10, 11, 12, 13], {})
        slider.set_tick_cache_state(frozenset({0, 2}))
        # Different spectral-cube range (e.g. dataset reload) - old
        # positions no longer mean anything against the new array.
        slider.set_ticks([20, 21, 22], {})
        self.assertIsNone(slider._cached_tick_indices)

    def test_relabeling_with_identical_values_preserves_cache_state(self) -> None:
        slider = DataAxisSlider()
        slider.set_ticks([10, 11, 12, 13], {0: "a"})
        slider.set_tick_cache_state(frozenset({1, 3}))
        # Same underlying cube list, different major-label choice - e.g.
        # toggling Cube/Time display mode. Must not blank the indicator.
        slider.set_ticks([10, 11, 12, 13], {2: "b"})
        self.assertEqual(slider._cached_tick_indices, frozenset({1, 3}))

    def test_none_clears_cache_state(self) -> None:
        slider = DataAxisSlider()
        slider.set_ticks([10, 11, 12], {})
        slider.set_tick_cache_state(frozenset({0}))
        slider.set_tick_cache_state(None)
        self.assertIsNone(slider._cached_tick_indices)


if __name__ == "__main__":
    unittest.main()
