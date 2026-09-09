"""Coverage for UIStateManager._apply_analysis_settings_lock: Reduction,
Formula, Metric, Fitting, and Order must be disabled (with an explanatory
tooltip) for the whole duration of a "Start analysis" run, and restored
exactly afterward. None of these five controls were gated on
_sensorgram_running before this - changing any of them mid-run would mix
inconsistent settings into what's meant to be one internally-consistent
saved "core data" trace (see docs/imaging_measurement_export_format.md and
the per-ROI sensorgram/reduction-completeness work in the same change).
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.ui_state_manager import UIStateManager

_ORIGINAL_TOOLTIP = "Original explanatory tooltip"
_LOCKED_WIDGET_NAMES = (
    "analysis_reduction_method_combo",
    "analysis_formula_combo",
    "analysis_metric_combo",
    "analysis_fit_method_combo",
    "analysis_poly_order_spin",
)


class TestApplyAnalysisSettingsLock(unittest.TestCase):
    def _make_manager(self) -> tuple[UIStateManager, SimpleNamespace]:
        window = SimpleNamespace(
            analysis_reduction_method_combo=QtWidgets.QComboBox(),
            analysis_formula_combo=QtWidgets.QComboBox(),
            analysis_metric_combo=QtWidgets.QComboBox(),
            analysis_fit_method_combo=QtWidgets.QComboBox(),
            analysis_poly_order_spin=QtWidgets.QSpinBox(),
        )
        for name in _LOCKED_WIDGET_NAMES:
            getattr(window, name).setToolTip(_ORIGINAL_TOOLTIP)
        manager = UIStateManager.__new__(UIStateManager)
        manager._window = window
        return manager, window

    def test_locks_all_five_and_sets_explanatory_tooltip(self) -> None:
        manager, window = self._make_manager()
        manager._apply_analysis_settings_lock(True)
        for name in _LOCKED_WIDGET_NAMES:
            widget = getattr(window, name)
            self.assertFalse(widget.isEnabled(), msg=name)
            self.assertEqual(widget.toolTip(), UIStateManager._ANALYSIS_SETTINGS_LOCK_TOOLTIP, msg=name)

    def test_unlocking_restores_the_original_tooltip(self) -> None:
        manager, window = self._make_manager()
        manager._apply_analysis_settings_lock(True)
        manager._apply_analysis_settings_lock(False)
        for name in _LOCKED_WIDGET_NAMES:
            widget = getattr(window, name)
            self.assertTrue(widget.isEnabled(), msg=name)
            self.assertEqual(widget.toolTip(), _ORIGINAL_TOOLTIP, msg=name)

    def test_never_locked_stays_enabled_with_original_tooltip(self) -> None:
        manager, window = self._make_manager()
        manager._apply_analysis_settings_lock(False)
        for name in _LOCKED_WIDGET_NAMES:
            widget = getattr(window, name)
            self.assertTrue(widget.isEnabled(), msg=name)
            self.assertEqual(widget.toolTip(), _ORIGINAL_TOOLTIP, msg=name)

    def test_repeated_lock_unlock_cycles_do_not_drift(self) -> None:
        # The tooltip cache must not clobber the real original with the
        # locked message on a second lock cycle.
        manager, window = self._make_manager()
        for _ in range(3):
            manager._apply_analysis_settings_lock(True)
            manager._apply_analysis_settings_lock(False)
        for name in _LOCKED_WIDGET_NAMES:
            widget = getattr(window, name)
            self.assertTrue(widget.isEnabled(), msg=name)
            self.assertEqual(widget.toolTip(), _ORIGINAL_TOOLTIP, msg=name)


if __name__ == "__main__":
    unittest.main()
