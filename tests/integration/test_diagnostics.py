"""Moved here from apps/sLSPR/acq/tests (Phase 1 shell extraction,
2026-08-07) - lspr_acq_shell.diagnostics is the real owner of DiagnosticsConfig
now; apps/sLSPR/acq/src/lspr_app/diagnostics.py is a thin re-export shim with
no behavior of its own to test.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from lspr_acq_shell.diagnostics import DiagnosticsConfig


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
