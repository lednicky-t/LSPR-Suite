"""Tests for `HiddenPanelGate` (panels/visibility_gate.py): hidden panels skip work and catch up once."""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.panels.visibility_gate import HiddenPanelGate
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class HiddenPanelGateTest(unittest.TestCase):
    def test_a_visible_panel_is_never_deferred(self) -> None:
        shown = []
        gate = HiddenPanelGate(on_shown=lambda: shown.append(1))
        self.assertTrue(gate.visible)
        self.assertFalse(gate.defer())
        self.assertFalse(gate.stale)
        gate.set_visible(True)
        self.assertEqual(shown, [])

    def test_while_hidden_requests_are_remembered_and_caught_up_once(self) -> None:
        shown = []
        gate = HiddenPanelGate(on_shown=lambda: shown.append(1))
        gate.set_visible(False)
        self.assertTrue(gate.defer())
        self.assertTrue(gate.defer())
        self.assertTrue(gate.defer())  # many requests while hidden ...
        self.assertTrue(gate.stale)
        gate.set_visible(True)
        self.assertEqual(shown, [1])  # ... exactly one catch-up
        self.assertFalse(gate.stale)
        self.assertFalse(gate.defer())

    def test_showing_a_panel_that_missed_nothing_does_no_work(self) -> None:
        shown = []
        gate = HiddenPanelGate(on_shown=lambda: shown.append(1))
        gate.set_visible(False)
        gate.set_visible(True)
        self.assertEqual(shown, [])

    def test_the_catch_up_may_ask_for_work_again_without_being_deferred(self) -> None:
        """`on_shown` normally calls the panel's own schedule function, which asks the gate again."""
        log = []
        gate = HiddenPanelGate(on_shown=lambda: log.append("deferred" if gate.defer() else "ran"))
        gate.set_visible(False)
        gate.defer()
        gate.set_visible(True)
        self.assertEqual(log, ["ran"])

    # -- held: an edit drag is running (2026-11-19) -----------------------------------------------

    def test_a_held_panel_defers_and_catches_up_once_when_released(self) -> None:
        shown = []
        gate = HiddenPanelGate(on_shown=lambda: shown.append(1))
        gate.set_held(True)
        self.assertTrue(gate.held)
        self.assertTrue(gate.visible, "held is not hidden")
        self.assertTrue(gate.defer())
        self.assertTrue(gate.defer())
        self.assertEqual(shown, [], "nothing runs during the drag")
        gate.set_held(False)
        self.assertEqual(shown, [1], "one catch-up on release")
        self.assertFalse(gate.defer())
        self.assertFalse(gate.stale)

    def test_releasing_a_hold_that_missed_nothing_does_no_work(self) -> None:
        shown = []
        gate = HiddenPanelGate(on_shown=lambda: shown.append(1))
        gate.set_held(True)
        gate.set_held(False)
        self.assertEqual(shown, [])

    def test_hidden_and_held_are_independent_and_the_catch_up_waits_for_both(self) -> None:
        shown = []
        gate = HiddenPanelGate(on_shown=lambda: shown.append(1))
        gate.set_held(True)
        gate.set_visible(False)
        gate.defer()
        gate.set_held(False)
        self.assertEqual(shown, [], "released, but still hidden")
        gate.set_visible(True)
        self.assertEqual(shown, [1])
        gate.set_visible(False)
        gate.defer()
        gate.set_held(True)
        gate.set_visible(True)
        self.assertEqual(shown, [1], "shown again, but still held")
        gate.set_held(False)
        self.assertEqual(shown, [1, 1])

    def test_a_gate_that_is_never_held_behaves_as_before(self) -> None:
        gate = HiddenPanelGate(on_shown=lambda: None)
        self.assertFalse(gate.held)
        self.assertFalse(gate.defer())


if __name__ == "__main__":
    unittest.main()
