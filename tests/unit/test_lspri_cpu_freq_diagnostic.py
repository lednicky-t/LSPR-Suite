"""Coverage for analysis_tasks.py's _cpu_freq_text - the "SG cube compute
timing" line's cpu_freq diagnostic, added to help confirm or rule out CPU
frequency throttling as the cause of the still-open, previously-observed
mid-run per-cube slowdown documented in bulk_analysis_performance_
investigation.md's Follow-up #11. Must degrade to "" (never raise) whenever
psutil is missing, errors, or doesn't expose a live reading on this
platform - this is diagnostic-only instrumentation, never allowed to break
a real analysis run.
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui import analysis_tasks  # noqa: E402


class TestCpuFreqText(unittest.TestCase):
    def test_psutil_missing_returns_empty_string(self) -> None:
        with mock.patch.object(analysis_tasks, "_psutil", None):
            self.assertEqual(analysis_tasks._cpu_freq_text(), "")

    def test_cpu_freq_raising_returns_empty_string_not_an_exception(self) -> None:
        fake_psutil = SimpleNamespace(cpu_freq=mock.Mock(side_effect=RuntimeError("not supported")))
        with mock.patch.object(analysis_tasks, "_psutil", fake_psutil):
            self.assertEqual(analysis_tasks._cpu_freq_text(), "")

    def test_none_reading_returns_empty_string(self) -> None:
        fake_psutil = SimpleNamespace(cpu_freq=lambda: None)
        with mock.patch.object(analysis_tasks, "_psutil", fake_psutil):
            self.assertEqual(analysis_tasks._cpu_freq_text(), "")

    def test_zero_current_returns_empty_string(self) -> None:
        fake_psutil = SimpleNamespace(cpu_freq=lambda: SimpleNamespace(current=0.0, min=0.0, max=2304.0))
        with mock.patch.object(analysis_tasks, "_psutil", fake_psutil):
            self.assertEqual(analysis_tasks._cpu_freq_text(), "")

    def test_valid_reading_is_formatted_with_current_and_max(self) -> None:
        fake_psutil = SimpleNamespace(cpu_freq=lambda: SimpleNamespace(current=1803.0, min=0.0, max=2304.0))
        with mock.patch.object(analysis_tasks, "_psutil", fake_psutil):
            text = analysis_tasks._cpu_freq_text()
        self.assertIn("current=1803MHz", text)
        self.assertIn("max=2304MHz", text)

    def test_zero_max_omits_the_max_segment(self) -> None:
        # Some platforms report current but not a real max (e.g. 0) -
        # shouldn't print a misleading "max=0MHz".
        fake_psutil = SimpleNamespace(cpu_freq=lambda: SimpleNamespace(current=1803.0, min=0.0, max=0.0))
        with mock.patch.object(analysis_tasks, "_psutil", fake_psutil):
            text = analysis_tasks._cpu_freq_text()
        self.assertIn("current=1803MHz", text)
        self.assertNotIn("max=", text)


if __name__ == "__main__":
    unittest.main()
