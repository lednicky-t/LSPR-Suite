from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths


ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.domain.models import ProcessingSettings, Spectrum
from lspr_app.domain.processing import (
    process_spectrum,
    processing_debug_mode_enabled,
    set_processing_debug_mode_enabled,
)


class ProcessingProfileTests(unittest.TestCase):
    def test_slow_processing_profile_logs_stage_breakdown(self) -> None:
        spectrum = Spectrum(
            wavelengths_nm=np.asarray([610.0, 620.0, 630.0], dtype=np.float64),
            values=np.asarray([1.0, 2.0, 3.0], dtype=np.float64),
            y_label="sample",
            acquired_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        settings = ProcessingSettings()
        previous = os.environ.get("LSPR_PROCESSING_SLOW_LOG_MS")
        previous_debug = processing_debug_mode_enabled()
        os.environ["LSPR_PROCESSING_SLOW_LOG_MS"] = "0"
        set_processing_debug_mode_enabled(True)
        try:
            with self.assertLogs("lspr_app.processing", level="INFO") as captured:
                processed, fit = process_spectrum(spectrum, settings)
        finally:
            set_processing_debug_mode_enabled(previous_debug)
            if previous is None:
                os.environ.pop("LSPR_PROCESSING_SLOW_LOG_MS", None)
            else:
                os.environ["LSPR_PROCESSING_SLOW_LOG_MS"] = previous

        self.assertIsNotNone(processed)
        self.assertIsNone(fit)
        self.assertTrue(any("Slow spectrum processing" in line for line in captured.output))
        self.assertTrue(any("sanitize=" in line for line in captured.output))
        self.assertTrue(any("fit=" in line for line in captured.output))
        self.assertTrue(any("wall/cpu" in line for line in captured.output))

    def test_slow_processing_profile_stays_silent_when_debug_mode_disabled(self) -> None:
        spectrum = Spectrum(
            wavelengths_nm=np.asarray([610.0, 620.0, 630.0], dtype=np.float64),
            values=np.asarray([1.0, 2.0, 3.0], dtype=np.float64),
            y_label="sample",
            acquired_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        settings = ProcessingSettings()
        previous = os.environ.get("LSPR_PROCESSING_SLOW_LOG_MS")
        previous_debug = processing_debug_mode_enabled()
        set_processing_debug_mode_enabled(False)
        os.environ["LSPR_PROCESSING_SLOW_LOG_MS"] = "0"
        try:
            with self.assertNoLogs("lspr_app.processing", level="INFO"):
                processed, fit = process_spectrum(spectrum, settings)
        finally:
            set_processing_debug_mode_enabled(previous_debug)
            if previous is None:
                os.environ.pop("LSPR_PROCESSING_SLOW_LOG_MS", None)
            else:
                os.environ["LSPR_PROCESSING_SLOW_LOG_MS"] = previous

        self.assertIsNotNone(processed)
        self.assertIsNone(fit)


if __name__ == "__main__":
    unittest.main()
