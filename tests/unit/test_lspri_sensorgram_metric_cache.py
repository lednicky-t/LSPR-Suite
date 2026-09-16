"""Coverage for the atomic per-(ROI, cube) fitted-metric RAM cache
(`_sensorgram_metric_signature`/`_sensorgram_metric_from_cache`/
`_store_sensorgram_metric_in_cache`, `analysis_worker_mixin.py`) added while
migrating the Sensogram panel onto the shared cache design in
`docs/analysis_caching_architecture.md`.

Keyed by `_sensorgram_signature_for_selection_with_cube_signatures`'s cheap
raw-tuple scheme (the same one `_roi_formula_spectrum_cache`/`_sensorgram_
cache` already use as dict keys) applied to a single ROI and a single cube's
own signature element - deliberately NOT `_sensorgram_point_signature_hash_
for_roi`'s SHA256 string, which exists only for the disk column and would
reintroduce the per-ROI-recomputation cost fixed 2026-09-12 if used for a RAM
lookup done once per (ROI, cube) during rendering. This file exercises the
real `_sensorgram_metric_signature` (not a stub) against a minimal but real
signature-input window, to actually prove the two calls it makes internally
agree with each other - `_backup_per_roi_sensorgram_points`'s own tests stub
this method out, so nothing else currently checks that.

`_backup_per_roi_sensorgram_points`'s own write-through is covered in
`test_lspri_per_roi_sensorgram_backup.py`; this file isolates the cache
helpers themselves - signature agreement, hit/miss, LRU move-to-end, and
eviction."""

from __future__ import annotations

import sys
import threading
import unittest
from collections import OrderedDict
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import AreaRoi  # noqa: E402
from lspr_imaging_app.gui.analysis_controller import AnalysisController  # noqa: E402


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


def _make_controller(*, cache_limit: int = 999) -> tuple[AnalysisController, SimpleNamespace]:
    window = SimpleNamespace(
        _state=_FakeState(),
        _wavelength_values=[500.0, 510.0],
        _analysis_cache_lock=threading.Lock(),
        _sensorgram_metric_cache=OrderedDict(),
        _sensorgram_metric_cache_limit=lambda: cache_limit,
        # _sensorgram_signature_for_selection_with_cube_signatures calls
        # these on `self.window`, not `self` (controller) - MainWindow's own
        # delegating-shim convention, confirmed by reading the real method
        # body rather than assumed (see this session's earlier mistake in
        # test_lspri_formula_spectrum_cache_shortcut.py for why this is
        # worth double-checking every time, not just for this one method).
        _analysis_fit_method_key=lambda: "poly",
        _analysis_metric_key=lambda: "centroid",
        _analysis_poly_order=lambda: 3,
        _analysis_wavelength_range=lambda: None,
        _roi_signature=lambda rois: tuple(int(r.area_roi_id) for r in rois),
    )
    controller = AnalysisController(window)
    # These two, by contrast, really are called via `self.` (the
    # controller) in the real method - confirmed the same way.
    controller._roi_reduction_signature_elements = lambda: ("mean",)
    controller._active_formula_key = lambda: "absorbance"
    return controller, window


def _roi(roi_id: int) -> AreaRoi:
    return AreaRoi(area_roi_id=roi_id, center_x=0.0, center_y=0.0, sample_radius_px=5.0)


_CUBE_7 = (7, ("preprocessing-7",), ("exclusion-7",))
_CUBE_8 = (8, ("preprocessing-8",), ("exclusion-8",))


class SensorgramMetricSignatureTests(unittest.TestCase):
    def test_same_roi_and_cube_signature_produce_the_same_signature(self) -> None:
        controller, _window = _make_controller()
        first = controller._sensorgram_metric_signature(_roi(1), _CUBE_7)
        second = controller._sensorgram_metric_signature(_roi(1), _CUBE_7)
        self.assertIsNotNone(first)
        self.assertEqual(first, second)

    def test_different_roi_changes_the_signature(self) -> None:
        controller, _window = _make_controller()
        self.assertNotEqual(
            controller._sensorgram_metric_signature(_roi(1), _CUBE_7),
            controller._sensorgram_metric_signature(_roi(2), _CUBE_7),
        )

    def test_different_cube_signature_changes_the_signature(self) -> None:
        controller, _window = _make_controller()
        self.assertNotEqual(
            controller._sensorgram_metric_signature(_roi(1), _CUBE_7),
            controller._sensorgram_metric_signature(_roi(1), _CUBE_8),
        )


class SensorgramMetricCacheTests(unittest.TestCase):
    def test_miss_on_empty_cache(self) -> None:
        controller, _window = _make_controller()
        self.assertIsNone(controller._sensorgram_metric_from_cache(_roi(1), _CUBE_7))

    def test_store_then_read_is_a_hit_with_the_stored_value(self) -> None:
        controller, window = _make_controller()
        controller._store_sensorgram_metric_in_cache(_roi(1), _CUBE_7, 1.5, 0.2)
        self.assertEqual(controller._sensorgram_metric_from_cache(_roi(1), _CUBE_7), (1.5, 0.2))
        self.assertEqual(list(window._sensorgram_metric_cache.values()), [(1.5, 0.2)])

    def test_different_roi_is_a_miss(self) -> None:
        controller, _window = _make_controller()
        controller._store_sensorgram_metric_in_cache(_roi(1), _CUBE_7, 1.5, 0.2)
        self.assertIsNone(controller._sensorgram_metric_from_cache(_roi(2), _CUBE_7))

    def test_different_cube_is_a_miss(self) -> None:
        controller, _window = _make_controller()
        controller._store_sensorgram_metric_in_cache(_roi(1), _CUBE_7, 1.5, 0.2)
        self.assertIsNone(controller._sensorgram_metric_from_cache(_roi(1), _CUBE_8))

    def test_read_hit_moves_entry_to_end(self) -> None:
        controller, window = _make_controller()
        controller._store_sensorgram_metric_in_cache(_roi(1), _CUBE_7, 1.5, 0.2)
        controller._store_sensorgram_metric_in_cache(_roi(2), _CUBE_7, 2.5, 0.3)
        first_key, second_key = list(window._sensorgram_metric_cache.keys())
        controller._sensorgram_metric_from_cache(_roi(1), _CUBE_7)
        self.assertEqual(list(window._sensorgram_metric_cache.keys()), [second_key, first_key])

    def test_lru_eviction_drops_least_recently_used(self) -> None:
        controller, window = _make_controller(cache_limit=2)
        controller._store_sensorgram_metric_in_cache(_roi(1), _CUBE_7, 1.0, 0.0)
        controller._store_sensorgram_metric_in_cache(_roi(2), _CUBE_7, 2.0, 0.0)
        controller._store_sensorgram_metric_in_cache(_roi(3), _CUBE_7, 3.0, 0.0)
        self.assertEqual(len(window._sensorgram_metric_cache), 2)
        self.assertIsNone(controller._sensorgram_metric_from_cache(_roi(1), _CUBE_7))
        self.assertIsNotNone(controller._sensorgram_metric_from_cache(_roi(2), _CUBE_7))
        self.assertIsNotNone(controller._sensorgram_metric_from_cache(_roi(3), _CUBE_7))


if __name__ == "__main__":
    unittest.main()
