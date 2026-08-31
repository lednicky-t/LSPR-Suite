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
from lspr_imaging_app.storage.measurement_export import FormulaSpectrumTraceIndex


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


def _make_disk_trace(
    controller: AnalysisController,
    roi: AreaRoi,
    spectral_cube_index: int,
    methods: dict,
    *,
    reduction_method: str = "mean",
    formula_key: str = "absorbance",
    stale: bool = False,
    legacy: bool = False,
) -> FormulaSpectrumTraceIndex:
    """A FormulaSpectrumTraceIndex whose stored signature_hash actually
    matches what _roi_disk_signature_for_cube computes for this roi/cube -
    exactly what a real HDF5-backed reader would hand back after a schema
    6.7 row was written and re-read.

    `legacy=True` instead simulates a row written BEFORE schema 6.7: its
    hash was computed with the actual reduction method baked in (via
    `_roi_formula_spectrum_signature_for_cube`, no override), not today's
    reduction-independent placeholder - see
    `_formula_spectrum_signature_matches_legacy_hash`."""
    reduced_values_by_method = {
        method: (np.asarray([s, s]), np.asarray([r, r])) for method, (s, r) in methods.items()
    }
    if stale:
        stored_hash = "stale-hash"
    elif legacy:
        legacy_signature = controller._roi_formula_spectrum_signature_for_cube(
            roi, spectral_cube_index, reduction_method_override=reduction_method
        )
        stored_hash = controller._signature_hash(legacy_signature)
    else:
        disk_signature = controller._roi_disk_signature_for_cube(roi, spectral_cube_index)
        stored_hash = controller._signature_hash(disk_signature)
    return FormulaSpectrumTraceIndex(
        wavelengths_nm=np.asarray([500.0, 510.0]),
        formula_key=formula_key,
        reduction_method=reduction_method,
        by_cube={spectral_cube_index: (stored_hash, reduced_values_by_method)},
    )


class TestFormulaSpectrumResultFromDiskRow(unittest.TestCase):
    """_formula_spectrum_result_from_disk_row: builds a FormulaSpectrumResult
    straight from a disk-read FormulaSpectrumTraceIndex entry, without any
    RAM cache involved."""

    def _make_controller(self) -> tuple[AnalysisController, _FakeWindow]:
        window = _FakeWindow()
        return AnalysisController(window), window

    def test_valid_row_builds_result_with_all_methods_attached(self) -> None:
        controller, _ = self._make_controller()
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
        methods = {"mean": (10.0, 20.0), "median": (11.0, 21.0)}
        trace = _make_disk_trace(controller, roi, 0, methods)
        disk_signature = controller._roi_disk_signature_for_cube(roi, 0)

        result = controller._formula_spectrum_result_from_disk_row(roi, 0, disk_signature, {1: trace})

        self.assertIsNotNone(result)
        self.assertEqual(result.reduction_method, "mean")
        self.assertAlmostEqual(float(result.sample_reduced_value[0]), 10.0, places=9)
        self.assertAlmostEqual(float(result.reference_reduced_value[0]), 20.0, places=9)
        self.assertEqual(set(result.reduced_values_by_method), {"mean", "median"})

    def test_stale_hash_is_a_miss(self) -> None:
        controller, _ = self._make_controller()
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
        trace = _make_disk_trace(controller, roi, 0, {"mean": (10.0, 20.0)}, stale=True)
        disk_signature = controller._roi_disk_signature_for_cube(roi, 0)

        result = controller._formula_spectrum_result_from_disk_row(roi, 0, disk_signature, {1: trace})

        self.assertIsNone(result)

    def test_missing_roi_is_a_miss(self) -> None:
        controller, _ = self._make_controller()
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
        disk_signature = controller._roi_disk_signature_for_cube(roi, 0)

        result = controller._formula_spectrum_result_from_disk_row(roi, 0, disk_signature, {})

        self.assertIsNone(result)

    def test_pre_6_7_legacy_hash_is_still_a_hit(self) -> None:
        """Rows written before the schema-6.7 migration have signature_hash
        computed with the actual reduction method baked in, not today's
        reduction-independent placeholder (_roi_disk_signature_for_cube) -
        without the legacy fallback, every previously-saved cube would read
        as "never calculated" the moment the app is upgraded to schema 6.7,
        which also breaks the disk-resume shortcut and the backup writer's
        dedup check (duplicate rows). Regression coverage for that fix."""
        controller, _ = self._make_controller()
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
        trace = _make_disk_trace(controller, roi, 0, {"mean": (10.0, 20.0)}, legacy=True)
        disk_signature = controller._roi_disk_signature_for_cube(roi, 0)

        result = controller._formula_spectrum_result_from_disk_row(roi, 0, disk_signature, {1: trace})

        self.assertIsNotNone(result)
        self.assertEqual(result.reduction_method, "mean")
        self.assertAlmostEqual(float(result.sample_reduced_value[0]), 10.0, places=9)
        self.assertAlmostEqual(float(result.reference_reduced_value[0]), 20.0, places=9)


class TestCombinedResultsDiskResumeProjection(unittest.TestCase):
    """_combined_formula_spectrum_results_from_ram_or_disk's disk-resume
    branch: a cube with nothing in RAM but a valid schema-6.7 disk row must
    resolve under WHATEVER reduction/formula is currently active (not just
    the one it was originally saved under), and must write-through the
    other reduction methods into RAM so a later switch for this same cube
    is a pure RAM hit."""

    def _make_controller(self) -> tuple[AnalysisController, _FakeWindow]:
        window = _FakeWindow()
        return AnalysisController(window), window

    def test_disk_only_hit_is_projected_onto_the_active_reduction_method(self) -> None:
        controller, window = self._make_controller()
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
        methods = {"mean": (10.0, 20.0), "median": (11.0, 21.0)}
        trace = _make_disk_trace(controller, roi, 0, methods, reduction_method="mean")
        window._state.area_roi_settings.reduction_method = "median"

        results = controller._combined_formula_spectrum_results_from_ram_or_disk(
            0, [roi], disk_trace_index={1: trace}
        )

        self.assertIsNotNone(results)
        result = results[1]
        self.assertEqual(result.reduction_method, "median")
        self.assertAlmostEqual(float(result.sample_reduced_value[0]), 11.0, places=9)
        self.assertAlmostEqual(float(result.reference_reduced_value[0]), 21.0, places=9)

    def test_disk_only_hit_writes_through_the_other_methods_into_ram(self) -> None:
        controller, window = self._make_controller()
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
        methods = {"mean": (10.0, 20.0), "median": (11.0, 21.0), "trimmed_mean": (10.5, 20.5), "plane_fit": (9.5, 19.5)}
        trace = _make_disk_trace(controller, roi, 0, methods, reduction_method="mean")

        results = controller._combined_formula_spectrum_results_from_ram_or_disk(
            0, [roi], disk_trace_index={1: trace}
        )

        self.assertIsNotNone(results)
        # Every method's own RAM signature should now be a direct hit - no
        # second disk read required for a later Reduction switch.
        for method in REDUCTION_METHODS:
            window._state.area_roi_settings.reduction_method = method
            signature = controller._roi_formula_spectrum_signature_for_cube(roi, 0)
            self.assertIn(signature, window._roi_formula_spectrum_cache, msg=f"missing RAM entry for {method}")

    def test_row_missing_the_requested_method_is_a_miss(self) -> None:
        """A disk row saved before this Reduction method existed (or with a
        gap for any other reason) must not silently resolve to a wrong
        method - the caller falls back to a full recompute instead."""
        controller, window = self._make_controller()
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
        trace = _make_disk_trace(controller, roi, 0, {"mean": (10.0, 20.0)}, reduction_method="mean")
        window._state.area_roi_settings.reduction_method = "plane_fit"

        results = controller._combined_formula_spectrum_results_from_ram_or_disk(
            0, [roi], disk_trace_index={1: trace}
        )

        self.assertIsNone(results)

    def test_stale_disk_row_is_a_miss(self) -> None:
        controller, window = self._make_controller()
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
        trace = _make_disk_trace(controller, roi, 0, {"mean": (10.0, 20.0)}, stale=True)

        results = controller._combined_formula_spectrum_results_from_ram_or_disk(
            0, [roi], disk_trace_index={1: trace}
        )

        self.assertIsNone(results)

    def test_ram_hit_is_preferred_over_disk(self) -> None:
        """When a signature is already warm in RAM, the disk trace must not
        even be consulted - RAM stays the fast path."""
        controller, window = self._make_controller()
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=10.0, sample_radius_px=5.0)
        ram_result = _make_roi_result("mean", {"mean": (99.0, 199.0)})
        signature = controller._roi_formula_spectrum_signature_for_cube(roi, 0)
        window._roi_formula_spectrum_cache[signature] = ram_result
        # A disk trace that, if consulted, would resolve to different values -
        # proves RAM wins without needing to touch this at all.
        trace = _make_disk_trace(controller, roi, 0, {"mean": (10.0, 20.0)})

        results = controller._combined_formula_spectrum_results_from_ram_or_disk(
            0, [roi], disk_trace_index={1: trace}
        )

        self.assertIsNotNone(results)
        self.assertAlmostEqual(float(results[1].sample_reduced_value[0]), 99.0, places=9)


if __name__ == "__main__":
    unittest.main()
