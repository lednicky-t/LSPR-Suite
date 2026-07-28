"""Unit tests for lspr_app.gui.main_window_state.switch_active_user - the
handler behind the User look-up field next to the recording destination.
Uses a MagicMock window (auto-creates whatever widget attributes
apply_processing_settings_to_widgets touches) so this stays a pure-logic
test with no QApplication/real widgets required.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "sLSPR" / "acq" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_app.gui import main_window_state as mws


def _fake_window(**kwargs) -> MagicMock:
    window = MagicMock()
    window._measurement_active = False
    for key, value in kwargs.items():
        setattr(window, key, value)
    return window


class SwitchActiveUserTests(unittest.TestCase):
    def test_blank_name_is_a_no_op(self) -> None:
        window = _fake_window()
        with patch.object(mws, "user_profile") as mock_profile:
            result = mws.switch_active_user(window, "  ")
        self.assertFalse(result)
        mock_profile.set_active_user.assert_not_called()

    def test_refused_while_measurement_is_actively_recording(self) -> None:
        window = _fake_window(_measurement_active=True)
        with patch.object(mws, "user_profile") as mock_profile:
            result = mws.switch_active_user(window, "Alex Chen")
        self.assertFalse(result)
        mock_profile.set_active_user.assert_not_called()
        window.status_label.setText.assert_called_once()
        self.assertIn("recording", window.status_label.setText.call_args[0][0].lower())

    def test_switching_to_the_already_active_user_is_a_no_op(self) -> None:
        window = _fake_window()
        with patch.object(mws, "user_profile") as mock_profile:
            mock_profile.active_user.return_value = "Alex Chen"
            result = mws.switch_active_user(window, "Alex Chen")
        self.assertFalse(result)
        mock_profile.set_active_user.assert_not_called()

    def test_switching_to_a_new_user_sets_active_user_and_reloads_settings(self) -> None:
        window = _fake_window()
        with patch.object(mws, "user_profile") as mock_profile, \
             patch.object(mws, "load_processing_settings", return_value="SETTINGS") as mock_load_processing, \
             patch.object(mws, "apply_processing_settings_to_widgets") as mock_apply, \
             patch.object(mws, "load_app_setting", return_value="dark"):
            mock_profile.active_user.return_value = None
            result = mws.switch_active_user(window, "Jamie Lee")

        self.assertTrue(result)
        mock_profile.set_active_user.assert_called_once_with("Jamie Lee")
        mock_load_processing.assert_called_once_with()
        mock_apply.assert_called_once_with(window, "SETTINGS")
        self.assertEqual(window._processing_settings, "SETTINGS")
        window.set_theme.assert_called_once_with("dark")

    def test_invalid_theme_value_does_not_call_set_theme(self) -> None:
        window = _fake_window()
        with patch.object(mws, "user_profile") as mock_profile, \
             patch.object(mws, "load_processing_settings", return_value="SETTINGS"), \
             patch.object(mws, "apply_processing_settings_to_widgets"), \
             patch.object(mws, "load_app_setting", return_value="not-a-real-theme"):
            mock_profile.active_user.return_value = None
            mws.switch_active_user(window, "Jamie Lee")

        window.set_theme.assert_not_called()


if __name__ == "__main__":
    unittest.main()
