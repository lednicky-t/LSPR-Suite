"""The GIL switch interval option (`gil_tuning.apply_switch_interval`): off unless `LSPR_GIL_SWITCH_MS` asks.

Pure: no Qt, no files. The process-wide interval is restored after every test."""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.gil_tuning import ENV_VAR, apply_switch_interval
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc


class SwitchIntervalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = sys.getswitchinterval()
        self.addCleanup(sys.setswitchinterval, self._original)

    def test_without_the_environment_variable_nothing_changes(self) -> None:
        sys.setswitchinterval(0.004)
        applied = apply_switch_interval({})
        self.assertAlmostEqual(applied, 0.004, places=6)
        self.assertAlmostEqual(sys.getswitchinterval(), 0.004, places=6)

    def test_environment_value_is_read_in_milliseconds(self) -> None:
        applied = apply_switch_interval({ENV_VAR: "0.5"})
        self.assertAlmostEqual(applied, 0.0005, places=6)
        self.assertAlmostEqual(sys.getswitchinterval(), 0.0005, places=6)

    def test_bad_environment_value_is_reported_and_ignored(self) -> None:
        for bad in ("fast", "0", "-3", "nan", "inf"):
            with self.subTest(value=bad), self.assertLogs("lspr_imaging_app.gil_tuning", level="WARNING"):
                sys.setswitchinterval(0.004)
                apply_switch_interval({ENV_VAR: bad})
                self.assertAlmostEqual(sys.getswitchinterval(), 0.004, places=6)


if __name__ == "__main__":
    unittest.main()
