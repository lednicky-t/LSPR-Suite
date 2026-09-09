"""Coverage for _sensorgram_metric_task's per-ROI metric fit
(SensorgramPointResult.per_roi_metric_values) - the "core data" companion to
the existing combined-selection metric_value/metric_signal on the same
point. Each selected ROI's own metric is fit from that ROI's own spectrum
(spectrum.area_roi_results, already populated for roi_formula_spectrum_
results) under the exact same fit_method_key/metric_key/poly_order/wl_min/
wl_max as the combined value on the same point - not a per-ROI copy of the
combined (pooled-selection) value.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.analysis_tasks import _sensorgram_metric_task  # noqa: E402
from lspr_imaging_app.gui.worker import SensorgramPointResult  # noqa: E402
from lspr_imaging_app.processing.analysis import fit_curve_for_method, metric_value_from_fit, metric_value_from_spectrum  # noqa: E402


class _FakeRoiSpectrum:
    def __init__(self, wavelengths_nm, formula_values) -> None:
        self.wavelengths_nm = wavelengths_nm
        self.formula_values = formula_values


class _FakeSpectrum:
    def __init__(self, wavelengths_nm, formula_values, area_roi_results=None) -> None:
        self.wavelengths_nm = wavelengths_nm
        self.formula_values = formula_values
        self.area_roi_results = area_roi_results


def _run_single_cube(spectrum: _FakeSpectrum, *, fit_method_key: str, metric_key: str, poly_order: int = 2) -> SensorgramPointResult:
    points: list[SensorgramPointResult] = []
    _sensorgram_metric_task(
        [0],
        poly_order=poly_order,
        metric_key=metric_key,
        partial_callback=points.append,
        spectral_cube_payload_builder=lambda spectral_cube_index: (spectral_cube_index,),
        task_fn=lambda *_a, **_k: spectrum,
        fit_method_key=fit_method_key,
    )
    assert len(points) == 1
    return points[0]


class TestPerRoiMetricPopulation(unittest.TestCase):
    def test_none_when_area_roi_results_absent(self) -> None:
        spectrum = _FakeSpectrum([500.0, 550.0, 600.0], [0.1, 0.3, 0.1], area_roi_results=None)
        point = _run_single_cube(spectrum, fit_method_key="none", metric_key="maximum")
        self.assertIsNone(point.per_roi_metric_values)

    def test_none_when_area_roi_results_empty(self) -> None:
        spectrum = _FakeSpectrum([500.0, 550.0, 600.0], [0.1, 0.3, 0.1], area_roi_results={})
        point = _run_single_cube(spectrum, fit_method_key="none", metric_key="maximum")
        self.assertIsNone(point.per_roi_metric_values)

    def test_one_entry_per_roi_no_fit(self) -> None:
        area_roi_results = {
            5: _FakeRoiSpectrum([500.0, 550.0, 600.0], [0.1, 0.3, 0.1]),
            7: _FakeRoiSpectrum([500.0, 550.0, 600.0], [0.05, 0.4, 0.05]),
        }
        spectrum = _FakeSpectrum([500.0, 550.0, 600.0], [0.08, 0.35, 0.08], area_roi_results=area_roi_results)
        point = _run_single_cube(spectrum, fit_method_key="none", metric_key="maximum")
        self.assertEqual(set(point.per_roi_metric_values.keys()), {5, 7})
        for roi_id, roi_spectrum in area_roi_results.items():
            expected_value, expected_signal = metric_value_from_spectrum(
                roi_spectrum.wavelengths_nm, roi_spectrum.formula_values, "maximum"
            )
            value, signal = point.per_roi_metric_values[roi_id]
            self.assertAlmostEqual(value, expected_value, places=9, msg=roi_id)
            self.assertAlmostEqual(signal, expected_signal, places=9, msg=roi_id)

    def test_one_entry_per_roi_with_fit(self) -> None:
        area_roi_results = {
            1: _FakeRoiSpectrum([500.0, 520.0, 540.0, 560.0, 580.0], [0.1, 0.3, 0.5, 0.3, 0.1]),
        }
        spectrum = _FakeSpectrum(
            [500.0, 520.0, 540.0, 560.0, 580.0], [0.1, 0.3, 0.5, 0.3, 0.1], area_roi_results=area_roi_results
        )
        point = _run_single_cube(spectrum, fit_method_key="poly", metric_key="maximum", poly_order=2)
        roi_spectrum = area_roi_results[1]
        expected_fit = fit_curve_for_method(roi_spectrum.wavelengths_nm, roi_spectrum.formula_values, "poly", poly_order=2)
        expected_value, expected_signal = metric_value_from_fit(expected_fit, "maximum")
        value, signal = point.per_roi_metric_values[1]
        self.assertAlmostEqual(value, expected_value, places=6)
        self.assertAlmostEqual(signal, expected_signal, places=6)

    def test_single_roi_selection_matches_the_combined_value(self) -> None:
        # Correctness property from the design: when the pooled/combined
        # spectrum IS that one ROI's own spectrum (a single-ROI selection),
        # the per-ROI value must exactly match the combined metric_value -
        # both are the same computation over the same data.
        wavelengths_nm = [500.0, 520.0, 540.0, 560.0, 580.0]
        formula_values = [0.1, 0.3, 0.5, 0.3, 0.1]
        spectrum = _FakeSpectrum(
            wavelengths_nm, formula_values, area_roi_results={9: _FakeRoiSpectrum(wavelengths_nm, formula_values)}
        )
        point = _run_single_cube(spectrum, fit_method_key="poly", metric_key="centroid", poly_order=2)
        value, signal = point.per_roi_metric_values[9]
        self.assertAlmostEqual(value, point.metric_value, places=9)
        self.assertAlmostEqual(signal, point.metric_signal, places=9)


if __name__ == "__main__":
    unittest.main()
