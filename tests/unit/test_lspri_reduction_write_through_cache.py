"""Regression tests for the write-through Reduction cache (gui/analysis_
worker_mixin.py's _write_through_reduced_values_by_method), the mechanism
that makes switching Reduction (mean/median/trimmed_mean/plane_fit) an
instant cache hit instead of a pixel re-read, once a cube has been visited
under any one of the four methods.

Covers the actual write-through wiring (not just project_reduction_result's
own pure logic, which test_lspri_analysis.py's TestProjectReductionResult
already covers in isolation): that the three non-active methods really do
get stored under their OWN, correctly-shaped signatures, and that a normal
read (via _roi_formula_spectrum_signature_for_cube with the live setting,
no override) actually finds what write-through stored.
"""

from __future__ import annotations

import sys
import threading
import unittest
from collections import OrderedDict
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

from lspr_imaging_app.domain.models import AreaRoi, FormulaSpectrumResult
from lspr_imaging_app.gui.analysis_controller import AnalysisController
from lspr_imaging_app.processing.roi_math import REDUCTION_METHODS


class _FakeAreaRoiSettings:
    def __init__(self) -> None:
        self.reference_inner_radius_px = 5.0
        self.reference_outer_radius_px = 10.0
        self.reduction_method = "mean"
        self.formula_key = "absorbance"


class _FakeState:
    def __init__(self) -> None:
        self.dataset = SimpleNamespace(folder="dataset_folder")
        self.area_roi_settings = _FakeAreaRoiSettings()
        self.image_exclusions: list = []


class _FakeWindow:
    """Duck-typed stand-in exposing only what _roi_formula_spectrum_
    signature_for_cube (and the RAM cache it keys) actually read, so the
    write-through logic can be tested without Qt widgets or real images."""

    ROI_FORMULA_SPECTRUM_CACHE_SIZE = 512

    def __init__(self) -> None:
        self._state = _FakeState()
        self._wavelength_values = [500.0, 510.0]
        self._analysis_cache_lock = threading.Lock()
        self._roi_formula_spectrum_cache: OrderedDict = OrderedDict()

    def _chromatic_signature_for_image_key(self, image_key):
        return ("chromatic", image_key)


def _make_roi_result(reduction_method: str, methods: dict) -> FormulaSpectrumResult:
    """A fresh per-ROI compute result, as reduce_sample_and_reference_all_
    methods + the task functions would produce it: reduced_values_by_method
    populated for every method actually computed this call."""
    sample, reference = methods[reduction_method]
    reduced_values_by_method = {
        method: (np.asarray([s, s]), np.asarray([r, r])) for method, (s, r) in methods.items()
    }
    return FormulaSpectrumResult(
        wavelengths_nm=np.asarray([500.0, 510.0]),
        formula_values=np.asarray([0.0, 0.0]),
        sample_reduced_value=np.asarray([sample, sample]),
        reference_reduced_value=np.asarray([reference, reference]),
        sample_pixel_count=np.asarray([10, 10], dtype=np.int32),
        reference_pixel_count=np.asarray([20, 20], dtype=np.int32),
        reduction_method=reduction_method,
        formula_key="absorbance",
        reduced_values_by_method=reduced_values_by_method,
    )


class TestWriteThroughReducedValuesByMethod(unittest.TestCase):
    def _make_controller(self) -> tuple[AnalysisController, _FakeWindow]:
        window = _FakeWindow()
        return AnalysisController(window), window

    def test_stores_all_three_other_methods(self) -> None:
        controller, window = self._make_controller()
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
        methods = {"mean": (10.0, 20.0), "median": (11.0, 21.0), "trimmed_mean": (10.5, 20.5), "plane_fit": (9.5, 19.5)}
        result = _make_roi_result("mean", methods)

        controller._write_through_reduced_values_by_method(roi, 0, result)

        self.assertEqual(len(window._roi_formula_spectrum_cache), 3)

    def test_each_stored_entry_is_readable_via_the_live_setting(self) -> None:
        """The real correctness claim: after write-through, switching the
        live Reduction setting and building the signature the NORMAL way
        (no override - exactly what an ordinary cache read does) finds the
        entry write-through stored, with the right projected values."""
        controller, window = self._make_controller()
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
        methods = {"mean": (10.0, 20.0), "median": (11.0, 21.0), "trimmed_mean": (10.5, 20.5), "plane_fit": (9.5, 19.5)}
        result = _make_roi_result("mean", methods)

        controller._write_through_reduced_values_by_method(roi, 0, result)

        for method, (expected_sample, expected_reference) in methods.items():
            if method == "mean":
                continue  # the active method's own entry is stored by the (untested-here) primary store call, not write-through
            window._state.area_roi_settings.reduction_method = method
            signature = controller._roi_formula_spectrum_signature_for_cube(roi, 0)
            self.assertIn(signature, window._roi_formula_spectrum_cache, msg=f"missing entry for {method}")
            cached = window._roi_formula_spectrum_cache[signature]
            self.assertEqual(cached.reduction_method, method)
            self.assertAlmostEqual(float(cached.sample_reduced_value[0]), expected_sample, places=9)
            self.assertAlmostEqual(float(cached.reference_reduced_value[0]), expected_reference, places=9)

    def test_different_methods_get_different_signatures(self) -> None:
        controller, window = self._make_controller()
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
        signatures = {
            method: controller._roi_formula_spectrum_signature_for_cube(roi, 0, reduction_method_override=method)
            for method in REDUCTION_METHODS
        }
        self.assertEqual(len(set(signatures.values())), len(REDUCTION_METHODS))

    def test_missing_method_is_not_written(self) -> None:
        """A cube where only 'mean' was ever computed (e.g. a disk-resumed
        entry re-used as the write-through source) must not fabricate
        entries for methods it has no data for."""
        controller, window = self._make_controller()
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
        result = _make_roi_result("mean", {"mean": (10.0, 20.0)})

        controller._write_through_reduced_values_by_method(roi, 0, result)

        self.assertEqual(len(window._roi_formula_spectrum_cache), 0)


if __name__ == "__main__":
    unittest.main()
