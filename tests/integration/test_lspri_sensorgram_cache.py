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

from lspr_imaging_app.domain.models import FormulaSpectrumResult
from lspr_imaging_app.gui.analysis_controller import AnalysisController


def _make_result(formula_key: str = "absorbance", sample: float = 2.0, reference: float = 4.0) -> FormulaSpectrumResult:
    """Minimal real FormulaSpectrumResult (not a bare object()) - needed
    because project_formula_spectrum uses dataclasses.replace, which
    requires an actual dataclass instance, not a duck-typed stand-in."""
    return FormulaSpectrumResult(
        wavelengths_nm=np.asarray([500.0]),
        formula_values=np.asarray([0.0]),
        sample_reduced_value=np.asarray([sample]),
        reference_reduced_value=np.asarray([reference]),
        sample_pixel_count=np.asarray([1], dtype=np.int32),
        reference_pixel_count=np.asarray([1], dtype=np.int32),
        formula_key=formula_key,
    )


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


class _FakeCombo:
    """Minimal currentData()-only stand-in for a QComboBox - avoids pulling
    in a real Qt widget just for AnalysisController._analysis_fit_method_key,
    which (unlike its metric/poly_order siblings) reads window.analysis_fit_
    method_combo directly rather than going through a window-level helper."""

    def __init__(self, value: str) -> None:
        self._value = value

    def currentData(self):
        return self._value


class _FakeWindow:
    """Duck-typed stand-in for MainWindow exposing only what
    _sensorgram_spectral_cube_payload_signature (and the result-cache methods keyed
    by it) actually read, so the cache logic can be tested without Qt widgets."""

    def __init__(self, result_cache_size: int = 3) -> None:
        self.SENSORGRAM_SPECTRAL_CUBE_RESULT_CACHE_SIZE = result_cache_size
        self._state = _FakeState()
        self._wavelength_values = [500.0, 510.0, 520.0]
        self._analysis_cache_lock = threading.Lock()
        self._sensorgram_spectral_cube_result_cache: OrderedDict = OrderedDict()
        self.analysis_fit_method_combo = _FakeCombo("poly")

    def _roi_signature(self, rois):
        return tuple(rois)

    def _preprocessing_signature(self, image_key):
        return ("preproc", image_key)

    def _analysis_wavelength_range(self):
        return None

    def _analysis_fit_method_key(self):
        return "poly"

    def _analysis_metric_key(self):
        return "centroid"

    def _analysis_poly_order(self):
        return 3


class TestSensorgramSpectralCubeResultCache(unittest.TestCase):
    """Covers the Fix-A cache added in gui/analysis_controller.py: a per-frame
    (spectral_cube_index, ROI selection) cache of AbsorbanceSpectrumResult, keyed by
    _sensorgram_spectral_cube_payload_signature - the same signature already used for
    the payload cache, which by design excludes poly_order/metric_key. This is what
    lets the sensorgram sweep re-fit without re-reading pixels when only the fit
    settings change."""

    def _make_controller(self, cache_size: int = 3) -> tuple[AnalysisController, _FakeWindow]:
        window = _FakeWindow(cache_size)
        return AnalysisController(window), window

    def test_hit_is_independent_of_fit_parameters(self) -> None:
        controller, _window = self._make_controller()
        rois = ["roiA"]
        roi_ids = (1,)
        result = _make_result()
        controller._store_sensorgram_spectral_cube_result(0, roi_ids, rois, result)
        # The getter/setter pair takes no poly_order/metric_key argument at all -
        # that is the actual fix: a stored math-layer result for a given frame and
        # ROI selection is reusable no matter what the fit settings later become.
        # Identity (assertIs, not just equality) is preserved because the fake
        # window's default active formula ("absorbance") already matches the
        # stored result's own formula_key - project_formula_spectrum is a no-op
        # (returns the same object) whenever the two already match.
        self.assertIs(controller._cached_sensorgram_spectral_cube_result(0, roi_ids, rois), result)

    def test_different_spectral_cube_is_a_miss(self) -> None:
        controller, _window = self._make_controller()
        rois = ["roiA"]
        roi_ids = (1,)
        controller._store_sensorgram_spectral_cube_result(0, roi_ids, rois, _make_result())
        self.assertIsNone(controller._cached_sensorgram_spectral_cube_result(1, roi_ids, rois))

    def test_different_roi_selection_is_a_miss(self) -> None:
        controller, _window = self._make_controller()
        controller._store_sensorgram_spectral_cube_result(0, (1,), ["roiA"], _make_result())
        self.assertIsNone(controller._cached_sensorgram_spectral_cube_result(0, (2,), ["roiB"]))

    def test_different_reduction_method_is_a_miss(self) -> None:
        """ROI's-math changes must invalidate the per-frame result cache -
        otherwise switching Reduction method would keep showing values
        computed under the old method."""
        controller, window = self._make_controller()
        controller._store_sensorgram_spectral_cube_result(0, (1,), ["roiA"], _make_result())
        window._state.area_roi_settings.reduction_method = "median"
        self.assertIsNone(controller._cached_sensorgram_spectral_cube_result(0, (1,), ["roiA"]))

    def test_formula_key_change_still_hits_and_gets_projected(self) -> None:
        """Formula is deliberately NOT part of this cache's signature (only
        Reduction/Trim are - see AnalysisController._roi_reduction_signature_
        elements): sample_reduced_value/reference_reduced_value don't depend
        on formula_key, so a formula switch must still hit and come back
        re-expressed onto the new formula (processing/analysis.py's
        project_formula_spectrum), never force a pixel-reduction miss."""
        controller, window = self._make_controller()
        stored = _make_result(formula_key="absorbance", sample=2.0, reference=4.0)
        controller._store_sensorgram_spectral_cube_result(0, (1,), ["roiA"], stored)
        window._state.area_roi_settings.formula_key = "ratio"
        projected = controller._cached_sensorgram_spectral_cube_result(0, (1,), ["roiA"])
        self.assertIsNotNone(projected)
        self.assertEqual(projected.formula_key, "ratio")
        self.assertAlmostEqual(float(projected.formula_values[0]), 0.5)  # sample/reference = 2/4

    def test_lru_eviction_drops_least_recently_used(self) -> None:
        controller, window = self._make_controller(cache_size=2)
        r0, r1, r2 = _make_result(), _make_result(), _make_result()
        controller._store_sensorgram_spectral_cube_result(0, (1,), ["roiA"], r0)
        controller._store_sensorgram_spectral_cube_result(1, (1,), ["roiA"], r1)
        # Touch frame 0 again so frame 1 becomes the least-recently-used entry.
        controller._cached_sensorgram_spectral_cube_result(0, (1,), ["roiA"])
        controller._store_sensorgram_spectral_cube_result(2, (1,), ["roiA"], r2)

        self.assertEqual(len(window._sensorgram_spectral_cube_result_cache), 2)
        self.assertIsNone(controller._cached_sensorgram_spectral_cube_result(1, (1,), ["roiA"]))
        self.assertIs(controller._cached_sensorgram_spectral_cube_result(0, (1,), ["roiA"]), r0)
        self.assertIs(controller._cached_sensorgram_spectral_cube_result(2, (1,), ["roiA"]), r2)

    def test_no_dataset_is_a_no_op(self) -> None:
        controller, window = self._make_controller()
        window._state.dataset = None
        controller._store_sensorgram_spectral_cube_result(0, (1,), ["roiA"], _make_result())
        self.assertEqual(len(window._sensorgram_spectral_cube_result_cache), 0)
        self.assertIsNone(controller._cached_sensorgram_spectral_cube_result(0, (1,), ["roiA"]))


class TestSensorgramSignatureForSelectionIncludesRoiMath(unittest.TestCase):
    """Tier-B signature (fit-dependent, _sensorgram_signature_for_selection)
    must also change when Reduction or the active Formula change - otherwise
    a stale fitted sensorgram result could survive either change even though
    the Tier-A per-frame cache above deliberately does NOT invalidate on a
    Formula change (see test_formula_key_change_still_hits_and_gets_projected
    above). Guards the exact bug described in
    AnalysisController._roi_reduction_signature_elements' docstring: unlike
    Reduction, Formula is intentionally absent from the per-frame signature,
    so it must be re-added explicitly wherever a *finished* value (a fitted
    metric) is cached instead."""

    def _make_controller(self) -> tuple[AnalysisController, _FakeWindow]:
        window = _FakeWindow()
        return AnalysisController(window), window

    def test_different_reduction_method_changes_signature(self) -> None:
        controller, window = self._make_controller()
        rois, roi_ids = ["roiA"], (1,)
        before = controller._sensorgram_signature_for_selection([0, 1], roi_ids, rois)
        window._state.area_roi_settings.reduction_method = "median"
        after = controller._sensorgram_signature_for_selection([0, 1], roi_ids, rois)
        self.assertNotEqual(before, after)

    def test_different_formula_key_changes_signature(self) -> None:
        controller, window = self._make_controller()
        rois, roi_ids = ["roiA"], (1,)
        before = controller._sensorgram_signature_for_selection([0, 1], roi_ids, rois)
        window._state.area_roi_settings.formula_key = "ratio"
        after = controller._sensorgram_signature_for_selection([0, 1], roi_ids, rois)
        self.assertNotEqual(before, after)

    # No test_different_trimmed_mean_fraction_changes_signature: Trim % is no
    # longer a live setting (processing/roi_math.py's DEFAULT_TRIMMED_MEAN_
    # FRACTION is fixed, not read from area_roi_settings) - there is nothing
    # left to vary here. See gui/analysis_worker_mixin.py's
    # _write_through_reduced_values_by_method for how switching among the
    # four Reduction methods (including trimmed_mean) stays instant instead.


class TestSensorgramPointSignatureHashIncludesActiveFormula(unittest.TestCase):
    """`_sensorgram_point_signature_hash` keys one backed-up HDF5 sensorgram
    row (a *finished* metric_value), unlike `_sensorgram_spectral_cube_
    payload_signature` which it builds on and which is deliberately formula-
    independent. Explicitly re-adds the active formula on top of that payload
    signature - if this regressed (e.g. a future refactor forgot the explicit
    re-add), a formula switch would silently reuse a disk metric_value
    computed under the previous formula. See analysis_worker_mixin.py's
    _sensorgram_point_signature_hash docstring."""

    def _make_controller(self) -> tuple[AnalysisController, _FakeWindow]:
        window = _FakeWindow()
        return AnalysisController(window), window

    def test_different_formula_key_changes_hash(self) -> None:
        controller, window = self._make_controller()
        roi_ids = (1,)
        rois = ["roiA"]
        before = controller._sensorgram_point_signature_hash(0, roi_ids, rois)
        window._state.area_roi_settings.formula_key = "ratio"
        after = controller._sensorgram_point_signature_hash(0, roi_ids, rois)
        self.assertNotEqual(before, after)
        self.assertNotEqual(before, "")


if __name__ == "__main__":
    unittest.main()
