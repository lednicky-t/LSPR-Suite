"""Regression tests for the disk half of the Cube/Time slider's cached-tick
indicator (AnalysisController._ensure_disk_formula_spectrum_trace_cached /
_formula_spectrum_signature_saved_on_disk, used by
_refresh_cube_slider_cache_indicators).

Before this, a tick only reflected window._roi_formula_spectrum_cache (RAM,
LRU-capped, reset every app restart). These two methods let a tick also mean
"was this cube's formula spectrum ever calculated and permanently saved to
the HDF5 export backup", even if it's no longer warm in RAM. Covers the one
correctness trap in the lazy per-ROI disk cache: a real disk hit should be
cached (and not re-read), but "nothing on disk yet" must NOT be cached
permanently, or a ROI computed partway through the same session would be
stuck reporting stale negatives.
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - some
# Qt objects (QColor, pyqtgraph internals) are touched at import time.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.gui.analysis_controller import AnalysisController
from lspr_imaging_app.storage.measurement_export import FormulaSpectrumTraceIndex


def _make_trace(by_cube: dict) -> FormulaSpectrumTraceIndex:
    return FormulaSpectrumTraceIndex(
        wavelengths_nm=np.asarray([500.0, 510.0]),
        formula_key="absorbance",
        reduction_method="mean",
        by_cube=by_cube,
    )


class _FakeWriter:
    def __init__(self, traces: dict) -> None:
        self._traces = traces
        self.calls: list[int] = []

    def formula_spectrum_index(self, roi_id):
        self.calls.append(int(roi_id))
        return self._traces.get(int(roi_id))


class _FakeWindow:
    def __init__(self, writer=None) -> None:
        self._measurement_export_writer = writer
        self._formula_spectrum_disk_trace_cache: dict = {}


class TestEnsureDiskFormulaSpectrumTraceCached(unittest.TestCase):
    def _make_controller(self, writer=None) -> tuple[AnalysisController, _FakeWindow]:
        window = _FakeWindow(writer)
        return AnalysisController(window), window

    def test_real_hit_gets_cached(self) -> None:
        trace = _make_trace({0: ("h0", np.zeros(2), np.zeros(2), np.zeros(2))})
        writer = _FakeWriter({1: trace})
        controller, window = self._make_controller(writer)
        roi = SimpleNamespace(area_roi_id=1)

        result = controller._ensure_disk_formula_spectrum_trace_cached([roi])

        self.assertIs(window._formula_spectrum_disk_trace_cache[1], trace)
        self.assertIs(result[1], trace)

    def test_no_disk_data_is_not_permanently_cached(self) -> None:
        writer = _FakeWriter({})  # ROI 1 has nothing saved yet
        controller, window = self._make_controller(writer)
        roi = SimpleNamespace(area_roi_id=1)

        controller._ensure_disk_formula_spectrum_trace_cached([roi])
        controller._ensure_disk_formula_spectrum_trace_cached([roi])

        # Both calls re-queried the writer (no stale "nothing on disk"
        # answer stuck in the cache) - simulates a ROI computed partway
        # through the same session.
        self.assertEqual(writer.calls, [1, 1])
        self.assertNotIn(1, window._formula_spectrum_disk_trace_cache)

    def test_already_cached_roi_is_not_reread(self) -> None:
        trace = _make_trace({0: ("h0", np.zeros(2), np.zeros(2), np.zeros(2))})
        writer = _FakeWriter({1: trace})
        controller, window = self._make_controller(writer)
        window._formula_spectrum_disk_trace_cache[1] = trace
        roi = SimpleNamespace(area_roi_id=1)

        controller._ensure_disk_formula_spectrum_trace_cached([roi])

        self.assertEqual(writer.calls, [])

    def test_no_writer_is_a_noop(self) -> None:
        controller, window = self._make_controller(writer=None)
        roi = SimpleNamespace(area_roi_id=1)

        result = controller._ensure_disk_formula_spectrum_trace_cached([roi])

        self.assertEqual(result, {})
        self.assertEqual(window._formula_spectrum_disk_trace_cache, {})


class TestFormulaSpectrumSignatureSavedOnDisk(unittest.TestCase):
    def _make_controller(self) -> AnalysisController:
        return AnalysisController(_FakeWindow())

    def test_matching_hash_is_a_hit(self) -> None:
        controller = self._make_controller()
        signature = (1, "roi", "some", "signature")
        stored_hash = controller._signature_hash(signature)
        trace = _make_trace({7: (stored_hash, np.zeros(2), np.zeros(2), np.zeros(2))})

        self.assertTrue(
            controller._formula_spectrum_signature_saved_on_disk(1, 7, signature, {1: trace})
        )

    def test_stale_hash_is_a_miss(self) -> None:
        """Settings changed since this cube was saved (e.g. a different
        Reduction/Formula/chromatic setting) - the saved row is no longer
        valid for the *current* signature, so it must not count as cached."""
        controller = self._make_controller()
        signature = (1, "roi", "some", "signature")
        trace = _make_trace({7: ("stale-hash", np.zeros(2), np.zeros(2), np.zeros(2))})

        self.assertFalse(
            controller._formula_spectrum_signature_saved_on_disk(1, 7, signature, {1: trace})
        )

    def test_missing_cube_is_a_miss(self) -> None:
        controller = self._make_controller()
        signature = (1, "roi", "some", "signature")
        stored_hash = controller._signature_hash(signature)
        trace = _make_trace({7: (stored_hash, np.zeros(2), np.zeros(2), np.zeros(2))})

        self.assertFalse(
            controller._formula_spectrum_signature_saved_on_disk(1, 8, signature, {1: trace})
        )

    def test_missing_roi_is_a_miss(self) -> None:
        controller = self._make_controller()
        signature = (1, "roi", "some", "signature")

        self.assertFalse(
            controller._formula_spectrum_signature_saved_on_disk(1, 7, signature, {})
        )


if __name__ == "__main__":
    unittest.main()
