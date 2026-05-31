from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests._paths import ensure_repo_paths


ensure_repo_paths()

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
        self.assertFalse(config.suppress_info_logs)
        self.assertEqual(
            config.summary_lines(),
            [
                "Diagnostics mode: quiet",
                "File info filter: on",
            ],
        )

    def test_from_window(self) -> None:
        window = SimpleNamespace(_quiet_diagnostics_mode=False, _suppress_diagnostic_info_logs=True)
        config = DiagnosticsConfig.from_window(window)
        self.assertFalse(config.quiet_mode)
        self.assertTrue(config.suppress_info_logs)
        self.assertEqual(config.launch_flag_text(), "quiet=off | file_info=off")
