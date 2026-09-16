"""Characterization test for `_refresh_formula_spectrum`'s cache-hit
shortcut (`analysis_worker_mixin.py`, near the top of the function) -
verifies a hit applies immediately with no computation triggered, and a
miss starts one.

Originally written (Stage 0 of the Spectra/Sensogram cache-unification work,
docs/analysis_caching_architecture.md) against a *different*, now-removed
branch: a final `signature in self.window._formula_spectrum_cache` check at
the tail of the cascade, reached only after two earlier checks (a single-ROI
shortcut, then a linear "covers ROI ids" scan) had already missed. That
three-tier cascade is gone - `_cached_formula_spectrum_result_from_roi_cache`
(looping the one shared per-ROI cache, atomic per ROI) is now the only
check, and it runs first. Rewritten to match; the guarantee this file
protects - a hit is instant, a miss computes - is unchanged."""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from PyQt6 import QtWidgets

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


class _FakeWindow:
    """Duck-typed stand-in exposing only what `_refresh_formula_spectrum`
    actually reads, so the cache-hit/miss branches can be exercised without a
    real MainWindow. `__getattr__` auto-vivifies anything not explicitly set
    into a harmless MagicMock (mirrors the pattern in
    test_lspri_sensorgram_missing_data_message.py)."""

    def __init__(self) -> None:
        self._sensorgram_running = False
        self._formula_spectrum_running = False
        self._formula_spectrum_running_signature = None
        self._pending_formula_spectrum_payload = None
        self._formula_spectrum_dirty = True
        self._rois = [_fake_roi(1), _fake_roi(2)]
        # Real empty dict, not left to __getattr__'s auto-vivified MagicMock -
        # a MagicMock's .get(...) would return a truthy mock instead of None,
        # silently making every ROI look "not missing" regardless of intent.
        self._roi_formula_spectrum_cache: dict = {}
        self._status_text: str | None = None

    def _selected_source_rois_snapshot(self):
        return list(self._rois)

    def _roi_formula_spectrum_signature(self, roi) -> tuple:
        return ("roi-signature", int(roi.area_roi_id))

    def _format_elapsed_seconds(self, _seconds: float) -> str:
        return "0.00s"

    def _set_status_text(self, text: str) -> None:
        self._status_text = text

    def _append_workflow_log(self, message: str, *, level: str = "info") -> None:
        pass

    def __getattr__(self, name: str):
        value = mock.MagicMock()
        setattr(self, name, value)
        return value


class FormulaSpectrumCacheShortcutTests(unittest.TestCase):
    def test_cache_hit_is_applied_immediately_without_starting_computation(self) -> None:
        window = _FakeWindow()
        controller = AnalysisController(window)
        cached_result = object()

        with mock.patch.object(controller, "_cached_formula_spectrum_result_from_roi_cache", return_value=cached_result), \
                mock.patch.object(controller, "_apply_formula_spectrum_result") as apply_mock, \
                mock.patch.object(window, "_start_formula_spectrum_preparation") as start_mock:
            controller._refresh_formula_spectrum()

        apply_mock.assert_called_once_with(cached_result)
        start_mock.assert_not_called()
        self.assertFalse(window._formula_spectrum_dirty)

    def test_sensorgram_sweep_fallback_hit_is_applied_immediately(self) -> None:
        # A bulk "Start analysis" sweep populates _sensorgram_spectral_cube_
        # result_cache but never _roi_formula_spectrum_cache directly (see
        # docs/analysis_caching_architecture.md) - this is the bridge that
        # lets browsing to an already-swept cube in the Spectra panel show
        # it instantly anyway, checked between the roi-cache miss and the
        # disk-backup fallback.
        window = _FakeWindow()
        controller = AnalysisController(window)
        cached_result = object()

        with mock.patch.object(controller, "_cached_formula_spectrum_result_from_roi_cache", return_value=None), \
                mock.patch.object(controller, "_cached_formula_spectrum_result_from_sensorgram_sweep", return_value=cached_result), \
                mock.patch.object(controller, "_fill_roi_formula_spectrum_cache_from_disk") as disk_fill_mock, \
                mock.patch.object(controller, "_apply_formula_spectrum_result") as apply_mock, \
                mock.patch.object(window, "_start_formula_spectrum_preparation") as start_mock:
            controller._refresh_formula_spectrum()

        apply_mock.assert_called_once_with(cached_result)
        disk_fill_mock.assert_not_called()
        start_mock.assert_not_called()
        self.assertFalse(window._formula_spectrum_dirty)

    def test_cache_miss_starts_computation(self) -> None:
        window = _FakeWindow()
        controller = AnalysisController(window)
        final_signature = ("final-signature",)

        with mock.patch.object(controller, "_cached_formula_spectrum_result_from_roi_cache", return_value=None), \
                mock.patch.object(controller, "_cached_formula_spectrum_result_from_sensorgram_sweep", return_value=None), \
                mock.patch.object(controller, "_fill_roi_formula_spectrum_cache_from_disk", return_value=False), \
                mock.patch.object(controller, "_formula_spectrum_signature_for_source_rois", return_value=final_signature), \
                mock.patch.object(controller, "_apply_formula_spectrum_result") as apply_mock, \
                mock.patch.object(window, "_start_formula_spectrum_preparation") as start_mock:
            controller._refresh_formula_spectrum()

        apply_mock.assert_not_called()
        start_mock.assert_called_once_with(final_signature, window._selected_source_rois_snapshot())


if __name__ == "__main__":
    unittest.main()
