"""`panels/image_first.py`: a head start for the image, never a lock. Pure logic with a fake clock; no Qt."""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.panels.image_first import ImageFirst  # noqa: E402


class ImageFirstTests(unittest.TestCase):
    def test_never_waits_without_an_image_panel_or_when_it_is_idle(self) -> None:
        self.assertFalse(ImageFirst(None).should_wait())
        self.assertFalse(ImageFirst(lambda: False).should_wait())

    def test_waits_while_the_image_is_busy_and_goes_when_it_is_drawn(self) -> None:
        busy = {"on": True}
        gate = ImageFirst(lambda: busy["on"], clock=lambda: 0.0)
        self.assertTrue(gate.should_wait())
        self.assertTrue(gate.should_wait())
        busy["on"] = False
        self.assertFalse(gate.should_wait())

    def test_gives_up_after_the_maximum_wait_and_then_starts_a_fresh_head_start(self) -> None:
        now = [0.0]
        gate = ImageFirst(lambda: True, max_wait_s=5.0, clock=lambda: now[0])
        self.assertTrue(gate.should_wait())
        now[0] = 4.9
        self.assertTrue(gate.should_wait())
        now[0] = 5.0
        self.assertFalse(gate.should_wait())  # e.g. Play keeps the image busy: the computation must still run
        self.assertTrue(gate.should_wait())  # the next computation gets its own head start
        now[0] = 10.0
        self.assertFalse(gate.should_wait())


if __name__ == "__main__":
    unittest.main()
