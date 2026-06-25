from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from pathlib import Path
import sys

APP_SRC = Path(__file__).resolve().parents[1] / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.diagnostics import DiagnosticsConfig


class DiagnosticsConfigTests(unittest.TestCase):
    def test_from_env_and_summary_lines(self) -> None:
        config = DiagnosticsConfig.from_env(
            {
                "LSPR_QUIET_DIAGNOSTICS": "1",
                "LSPR_SUPPRESS_DIAGNOSTIC_INFO_LOGS": "off",
            }
        )
        self.assertTrue(config.quiet_mode)
        self.assertTrue(config.suppress_info_logs)
        self.assertIn("Diagnostics profile: off", config.summary_lines()[0])
        self.assertTrue(any(line.startswith("Runtime drift probe:") for line in config.summary_lines()))

    def test_from_window(self) -> None:
        window = SimpleNamespace(_quiet_diagnostics_mode=False, _suppress_diagnostic_info_logs=True)
        config = DiagnosticsConfig.from_window(window)
        self.assertFalse(config.quiet_mode)
        self.assertTrue(config.suppress_info_logs)
        self.assertIn("profile=normal", config.launch_flag_text())

    def test_normal_profile_keeps_diagnostics_panel_opt_in(self) -> None:
        config = DiagnosticsConfig.for_profile("normal")
        self.assertFalse(config.diagnostics_panel_enabled)
