from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from lspr_app.domain.models import ProcessingSettings
from lspr_app.storage.app_config import load_processing_settings, save_processing_settings


class AppConfigProcessingSettingsTests(unittest.TestCase):
    def test_analysis_resolution_round_trip_reaches_one_micro_nm(self) -> None:
        settings = ProcessingSettings(analysis_resolution_nm=0.000001)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "lspr_settings.json"
            save_processing_settings(settings, path)
            loaded = load_processing_settings(path)

        self.assertEqual(loaded.analysis_resolution_nm, 0.000001)

    def test_analysis_resolution_clamps_to_one_micro_nm_floor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "lspr_settings.json"
            save_processing_settings(ProcessingSettings(analysis_resolution_nm=0.0000001), path)
            loaded = load_processing_settings(path)

        self.assertEqual(loaded.analysis_resolution_nm, 0.000001)
