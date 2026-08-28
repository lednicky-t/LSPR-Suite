from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.models import AreaRoiDetectionSettings, PreprocessingSettings, StatisticsSettings
from lspr_imaging_app.storage.workspace import (
    build_processing_profile_payload,
    load_processing_profile,
    save_processing_profile,
)


class TestStatisticsSettingsPersistence(unittest.TestCase):
    """statistics_settings and the ROI's-math fields on area_roi_settings are
    session-scoped (not QSettings) since they change what the saved numbers
    mean - see StatisticsSettings' docstring in domain/models.py. Round-trip
    through the same save/load functions the app itself uses."""

    def test_round_trips_non_default_values(self) -> None:
        statistics_settings = StatisticsSettings(
            smoothing_method="savgol",
            smoothing_window=21,
            smoothing_polyorder=3,
            spike_rejection_enabled=True,
            spike_rejection_method="running_median",
            spike_rejection_window=7,
            spike_rejection_threshold=2.5,
            baseline_enabled=True,
            baseline_window_start=10.0,
            baseline_window_end=30.0,
            group_stats_enabled=True,
            group_stats_center="median",
            group_stats_band="sem",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            save_processing_profile(
                path,
                PreprocessingSettings(),
                AreaRoiDetectionSettings(),
                [],
                statistics_settings=statistics_settings,
            )
            loaded = load_processing_profile(path)
        self.assertEqual(loaded[-1], statistics_settings)

    def test_missing_key_falls_back_to_defaults(self) -> None:
        # Simulates a profile saved before this feature existed - no
        # "statistics_settings" key in the JSON at all.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            payload = build_processing_profile_payload(PreprocessingSettings(), AreaRoiDetectionSettings(), [])
            del payload["statistics_settings"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_processing_profile(path)
        self.assertEqual(loaded[-1], StatisticsSettings())

    def test_roi_math_fields_round_trip_on_area_roi_settings(self) -> None:
        area_roi_settings = AreaRoiDetectionSettings(
            reduction_method="median",
            formula_key="ratio",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            save_processing_profile(path, PreprocessingSettings(), area_roi_settings, [])
            loaded = load_processing_profile(path)
        loaded_area_roi_settings = loaded[1]
        self.assertEqual(loaded_area_roi_settings.reduction_method, "median")
        self.assertEqual(loaded_area_roi_settings.formula_key, "ratio")

    def test_roi_math_fields_default_when_missing_from_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            payload = build_processing_profile_payload(PreprocessingSettings(), AreaRoiDetectionSettings(), [])
            del payload["area_roi_settings"]["reduction_method"]
            del payload["area_roi_settings"]["formula_key"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_processing_profile(path)
        loaded_area_roi_settings = loaded[1]
        self.assertEqual(loaded_area_roi_settings.reduction_method, "mean")
        self.assertEqual(loaded_area_roi_settings.formula_key, "absorbance")


if __name__ == "__main__":
    unittest.main()
