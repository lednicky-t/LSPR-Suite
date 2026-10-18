"""`diagnostics.thread_times.thread_cpu_times`: user / kernel CPU time of the calling thread (Windows)."""

from __future__ import annotations

import sys
import time
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.diagnostics.thread_times import thread_cpu_times
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class ThreadTimesTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "GetThreadTimes is Windows only")
    def test_busy_work_shows_up_as_user_time(self) -> None:
        before = thread_cpu_times()
        self.assertIsNotNone(before)
        end = time.perf_counter() + 0.4  # more than a scheduler tick (~15.6 ms) of pure computation
        total = 0
        while time.perf_counter() < end:
            total += sum(range(1000))
        after = thread_cpu_times()
        self.assertGreaterEqual(after[0] - before[0], 0.2)  # user seconds
        self.assertGreaterEqual(after[1], before[1])  # kernel time never goes backwards
        self.assertLess(after[1] - before[1], 0.2)  # a pure loop is not kernel work

    def test_off_windows_it_says_it_cannot_tell(self) -> None:
        if sys.platform == "win32":
            self.skipTest("only meaningful off Windows")
        self.assertIsNone(thread_cpu_times())


if __name__ == "__main__":
    unittest.main()
