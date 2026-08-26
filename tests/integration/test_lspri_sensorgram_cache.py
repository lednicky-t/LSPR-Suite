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

from lspr_imaging_app.gui.analysis_controller import AnalysisController


class _FakeAreaRoiSettings:
    def __init__(self) -> None:
        self.reference_inner_radius_px = 5.0
        self.reference_outer_radius_px = 10.0
        self.reduction_method = "mean"
        self.trimmed_mean_fraction = 0.10
        self.formula_key = "absorbance"


class _FakeState:
    def __init__(self) -> None:
        self.dataset = SimpleNamespace(folder="dataset_folder")
        self.area_roi_settings = _FakeAreaRoiSettings()
        self.image_exclusions: list = []


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
        result = object()
        controller._store_sensorgram_spectral_cube_result(0, roi_ids, rois, result)
        # The getter/setter pair takes no poly_order/metric_key argument at all -
        # that is the actual fix: a stored math-layer result for a given frame and
        # ROI selection is reusable no matter what the fit settings later become.
        self.assertIs(controller._cached_sensorgram_spectral_cube_result(0, roi_ids, rois), result)

    def test_different_spectral_cube_is_a_miss(self) -> None:
        controller, _window = self._make_controller()
        rois = ["roiA"]
        roi_ids = (1,)
        controller._store_sensorgram_spectral_cube_result(0, roi_ids, rois, object())
        self.assertIsNone(controller._cached_sensorgram_spectral_cube_result(1, roi_ids, rois))

    def test_different_roi_selection_is_a_miss(self) -> None:
        controller, _window = self._make_controller()
        controller._store_sensorgram_spectral_cube_result(0, (1,), ["roiA"], object())
        self.assertIsNone(controller._cached_sensorgram_spectral_cube_result(0, (2,), ["roiB"]))

    def test_different_reduction_method_is_a_miss(self) -> None:
        """ROI's-math changes must invalidate the per-frame result cache -
        otherwise switching Reduction method would keep showing values
        computed under the old method."""
        controller, window = self._make_controller()
        controller._store_sensorgram_spectral_cube_result(0, (1,), ["roiA"], object())
        window._state.area_roi_settings.reduction_method = "median"
        self.assertIsNone(controller._cached_sensorgram_spectral_cube_result(0, (1,), ["roiA"]))

    def test_different_formula_key_is_a_miss(self) -> None:
        controller, window = self._make_controller()
        controller._store_sensorgram_spectral_cube_result(0, (1,), ["roiA"], object())
        window._state.area_roi_settings.formula_key = "ratio"
        self.assertIsNone(controller._cached_sensorgram_spectral_cube_result(0, (1,), ["roiA"]))

    def test_lru_eviction_drops_least_recently_used(self) -> None:
        controller, window = self._make_controller(cache_size=2)
        r0, r1, r2 = object(), object(), object()
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
        controller._store_sensorgram_spectral_cube_result(0, (1,), ["roiA"], object())
        self.assertEqual(len(window._sensorgram_spectral_cube_result_cache), 0)
        self.assertIsNone(controller._cached_sensorgram_spectral_cube_result(0, (1,), ["roiA"]))


class TestSensorgramSignatureForSelectionIncludesRoiMath(unittest.TestCase):
    """Tier-B signature (fit-dependent, _sensorgram_signature_for_selection)
    must also change when ROI's-math settings change - otherwise a stale
    fitted sensorgram result could survive a Reduction/Formula change even
    though the Tier-A per-frame cache above correctly invalidated. Guards the
    exact bug described in AnalysisController._roi_math_signature_elements'
    docstring."""

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

    def test_different_trimmed_mean_fraction_changes_signature(self) -> None:
        controller, window = self._make_controller()
        rois, roi_ids = ["roiA"], (1,)
        before = controller._sensorgram_signature_for_selection([0, 1], roi_ids, rois)
        window._state.area_roi_settings.trimmed_mean_fraction = 0.20
        after = controller._sensorgram_signature_for_selection([0, 1], roi_ids, rois)
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
