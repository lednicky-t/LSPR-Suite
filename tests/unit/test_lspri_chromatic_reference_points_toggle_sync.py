"""Regression tests for the two "show chromatic reference points" toggles
in LSPRimaging Evaluation staying linked: the master toggle under the image
panel (`show_reference_points_check`) and the "show all across wavelengths"
toggle in the Workflow/Chromatic correction panel
(`chromatic_reference_points_all_button`). Both must always mirror each
other -- unchecking either one unchecks both, and checking either one
checks both.
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.chromatic_controller import ChromaticController
from lspr_imaging_app.gui.main_window import MainWindow


class _FakeToggle:
    def __init__(self, checked: bool) -> None:
        self._checked = checked
        self._signals_blocked = False

    def blockSignals(self, blocked: bool) -> None:
        self._signals_blocked = blocked

    def setChecked(self, checked: bool) -> None:
        self._checked = checked

    def isChecked(self) -> bool:
        return self._checked


def _make_window(*, reference_points_visible: bool, all_visible: bool) -> SimpleNamespace:
    icon_refresh_calls: list[tuple[object, str, bool]] = []
    window = SimpleNamespace(
        _reference_points_visible=reference_points_visible,
        _chromatic_reference_points_all_visible=all_visible,
        show_reference_points_check=_FakeToggle(reference_points_visible),
        chromatic_reference_points_all_button=_FakeToggle(all_visible),
        _update_landmark_overlays=lambda: None,
        _save_visual_preferences=lambda: None,
        _schedule_processing_state_save=lambda: None,
        icon_refresh_calls=icon_refresh_calls,
    )
    window._update_view_toggle_icon = lambda button, kind, visible: icon_refresh_calls.append((button, kind, visible))
    return window


class TestMasterToggleSyncsAllToggle(unittest.TestCase):
    def test_unchecking_master_unchecks_all_mode(self) -> None:
        window = _make_window(reference_points_visible=True, all_visible=True)
        MainWindow._on_show_reference_points_toggled(window, False)
        self.assertFalse(window._reference_points_visible)
        self.assertFalse(window._chromatic_reference_points_all_visible)
        self.assertFalse(window.chromatic_reference_points_all_button.isChecked())
        # Regression: setChecked() was called with blockSignals(True), which
        # also silences the toggled->_update_view_toggle_icon connection --
        # the synced button's checked state changed but its green/gray icon
        # never repainted unless something calls _update_view_toggle_icon by
        # hand.
        self.assertIn(
            (window.chromatic_reference_points_all_button, "reference_points", False),
            window.icon_refresh_calls,
        )

    def test_checking_master_checks_all_mode(self) -> None:
        # Regression: previously turning the master toggle back on left the
        # "all" toggle wherever it happened to be, so the two toggles could
        # disagree after a check/uncheck/check cycle.
        window = _make_window(reference_points_visible=False, all_visible=False)
        MainWindow._on_show_reference_points_toggled(window, True)
        self.assertTrue(window._reference_points_visible)
        self.assertTrue(window._chromatic_reference_points_all_visible)
        self.assertTrue(window.chromatic_reference_points_all_button.isChecked())
        self.assertIn(
            (window.chromatic_reference_points_all_button, "reference_points", True),
            window.icon_refresh_calls,
        )


class TestAllToggleSyncsMasterToggle(unittest.TestCase):
    def test_unchecking_all_mode_unchecks_master(self) -> None:
        # Regression: unchecking "show all across wavelengths" in the
        # Workflow panel used to leave the master toggle (under the image)
        # checked, so the two toggles appeared unlinked.
        window = _make_window(reference_points_visible=True, all_visible=True)
        ChromaticController(window)._on_chromatic_reference_points_all_toggled(False)
        self.assertFalse(window._chromatic_reference_points_all_visible)
        self.assertFalse(window._reference_points_visible)
        self.assertFalse(window.show_reference_points_check.isChecked())
        self.assertIn(
            (window.show_reference_points_check, "reference_points", False),
            window.icon_refresh_calls,
        )

    def test_checking_all_mode_checks_master(self) -> None:
        window = _make_window(reference_points_visible=False, all_visible=False)
        ChromaticController(window)._on_chromatic_reference_points_all_toggled(True)
        self.assertTrue(window._chromatic_reference_points_all_visible)
        self.assertTrue(window._reference_points_visible)
        self.assertTrue(window.show_reference_points_check.isChecked())
        self.assertIn(
            (window.show_reference_points_check, "reference_points", True),
            window.icon_refresh_calls,
        )


if __name__ == "__main__":
    unittest.main()
