from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QMessageBox

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from lspr_core import (
    LAUNCH_PROFILE_ENV_VAR,
    LAUNCH_PROFILE_CONTROL_EDITOR,
    LAUNCH_PROFILE_SIMULATION,
    launch_profile_spec,
    normalize_launch_profile,
)
from suite_launcher import updater
from suite_launcher.app import LaunchCard, MainWindow
from suite_launcher.app import _settings_bool
from suite_launcher.targets import TARGETS, _candidate_paths
from suite_launcher.targets import AppTarget
from suite_launcher.version import APP_VERSION


class LauncherRegistryTests(unittest.TestCase):
    def test_candidate_paths_filters_none_values(self) -> None:
        path = Path("sample")
        self.assertEqual(_candidate_paths(None, path, None), (path,))

    def test_target_registry_contains_expected_apps(self) -> None:
        self.assertEqual(
            {target.key for target in TARGETS},
            {"slspr_acq", "slspr_eva", "lspri_acq", "lspri_eva"},
        )
        self.assertTrue(all(target.title for target in TARGETS))
        self.assertFalse(next(target for target in TARGETS if target.key == "lspri_acq").enabled)

    def test_launch_profile_helpers(self) -> None:
        self.assertEqual(normalize_launch_profile(" control_editor "), LAUNCH_PROFILE_CONTROL_EDITOR)
        self.assertEqual(normalize_launch_profile("unknown"), "full")
        self.assertFalse(launch_profile_spec(LAUNCH_PROFILE_CONTROL_EDITOR).show_runtime_controls)
        self.assertEqual(launch_profile_spec(LAUNCH_PROFILE_SIMULATION).source_mode, "simulation")
        self.assertFalse(launch_profile_spec(LAUNCH_PROFILE_CONTROL_EDITOR).show_recording_context)
        self.assertFalse(launch_profile_spec(LAUNCH_PROFILE_CONTROL_EDITOR).show_device_statuses)
        self.assertFalse(launch_profile_spec(LAUNCH_PROFILE_CONTROL_EDITOR).show_source_icon)
        self.assertFalse(launch_profile_spec(LAUNCH_PROFILE_CONTROL_EDITOR).show_experiment_status)
        self.assertEqual(launch_profile_spec(LAUNCH_PROFILE_CONTROL_EDITOR).window_mode_label_text, "Control Plan Editor Mode")
        self.assertTrue(launch_profile_spec(LAUNCH_PROFILE_SIMULATION).show_recording_context)
        self.assertFalse(launch_profile_spec(LAUNCH_PROFILE_SIMULATION).show_device_statuses)

    def test_target_build_command_merges_extra_env(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            script = root / "launch.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            target = AppTarget(
                key="demo",
                title="Demo",
                subtitle="Demo",
                address="demo.py",
                root_candidates=(root,),
                script="launch.py",
            )
            command, cwd, env = target.build_command(extra_env={LAUNCH_PROFILE_ENV_VAR: "simulation"})
        self.assertEqual(command[-1], "launch.py")
        self.assertEqual(cwd, root)
        self.assertEqual(env[LAUNCH_PROFILE_ENV_VAR], "simulation")

    def test_target_build_command_can_pass_quiet_diagnostics_env(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            script = root / "launch.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            target = AppTarget(
                key="demo",
                title="Demo",
                subtitle="Demo",
                address="demo.py",
                root_candidates=(root,),
                script="launch.py",
            )
            _, _, env = target.build_command(extra_env={"LSPR_QUIET_DIAGNOSTICS": "1"})
        self.assertEqual(env["LSPR_QUIET_DIAGNOSTICS"], "1")

    def test_target_build_command_can_pass_suppressed_info_logs_env(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            script = root / "launch.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            target = AppTarget(
                key="demo",
                title="Demo",
                subtitle="Demo",
                address="demo.py",
                root_candidates=(root,),
                script="launch.py",
            )
            _, _, env = target.build_command(extra_env={"LSPR_SUPPRESS_DIAGNOSTIC_INFO_LOGS": "1"})
        self.assertEqual(env["LSPR_SUPPRESS_DIAGNOSTIC_INFO_LOGS"], "1")

    def test_settings_bool_parses_common_string_values(self) -> None:
        settings = QSettings("LSPR Suite", "Launcher Test")
        settings.setValue("flag_true", "false")
        settings.setValue("flag_one", "1")
        settings.setValue("flag_zero", "0")

        self.assertFalse(_settings_bool(settings, "flag_true", True))
        self.assertTrue(_settings_bool(settings, "flag_one", False))
        self.assertFalse(_settings_bool(settings, "flag_zero", True))

    def test_launch_card_states_and_inline_mode_chip(self) -> None:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.assertIsNotNone(app)
        target = next(target for target in TARGETS if target.key == "slspr_acq")
        diagnostics_profile = "normal"
        cycle_calls = 0

        def _cycle_diagnostics_profile() -> None:
            nonlocal diagnostics_profile, cycle_calls
            cycle_calls += 1
            diagnostics_profile = "debug"

        def _diagnostics_profile_text() -> str:
            return diagnostics_profile.capitalize()

        card = LaunchCard(
            target,
            theme=type("Theme", (), {"accent": "#4C8BF5", "accent_soft": "#183563"})(),
            launch_callback=lambda _target: None,
            profile_key_callback=lambda: "full",
            profile_cycle_callback=lambda: None,
            profile_text_callback=lambda: "Full",
            profile_tooltip_callback=lambda: "Full mode",
            diagnostics_profile_cycle_callback=_cycle_diagnostics_profile,
            diagnostics_profile_text_callback=_diagnostics_profile_text,
            diagnostics_profile_tooltip_callback=lambda: "Normal diagnostics.",
        )
        self.assertTrue(hasattr(card, "mode_label"))
        self.assertIn("Mode:", card.mode_label.text())
        self.assertIn("Full", card.mode_label.text())
        self.assertTrue(hasattr(card, "diagnostics_button"))
        self.assertEqual(card.diagnostics_button.text(), "Diagnostics: Normal")
        self.assertEqual(card.button.text(), "Launch")
        card.set_auto_launch_pending(True, 3)
        self.assertIn("Stop launch", card.button.text())
        card.diagnostics_button.click()
        self.assertEqual(cycle_calls, 1)
        card._update_diagnostics_button()
        self.assertEqual(card.diagnostics_button.text(), "Diagnostics: Debug")
        card.set_running(True)
        self.assertEqual(card.button.text(), "Kill")
        card.set_running(False)
        self.assertEqual(card.button.text(), "Launch")


class MainWindowUpdateCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        # Keep a strong reference - an unassigned QApplication() gets garbage
        # collected immediately, which then crashes on the next widget access.
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.window = MainWindow()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()

    def test_update_button_exists_with_default_text(self) -> None:
        self.assertTrue(hasattr(self.window, "update_button"))
        self.assertEqual(self.window.update_button.text(), "Check for Updates")

    def test_check_for_updates_error_shows_warning_and_resets_button(self) -> None:
        with mock.patch.object(QMessageBox, "warning") as warning_mock:
            self.window._handle_update_check_finished(None, "no internet")
        warning_mock.assert_called_once()
        self.assertIn("no internet", warning_mock.call_args.args[2])
        self.assertTrue(self.window.update_button.isEnabled())
        self.assertEqual(self.window.update_button.text(), "Check for Updates")

    def test_check_for_updates_up_to_date_shows_information(self) -> None:
        release = updater.ReleaseInfo(
            tag=f"v{APP_VERSION}",
            version=updater.parse_version(APP_VERSION),
            notes="",
            download_url="https://example.invalid/bundle.zip",
            asset_name="bundle.zip",
        )
        with mock.patch.object(QMessageBox, "information") as info_mock:
            self.window._handle_update_check_finished(release, None)
        info_mock.assert_called_once()
        self.assertIn("Up to date", info_mock.call_args.args[1])

    def test_check_for_updates_newer_version_in_dev_checkout_informs_only(self) -> None:
        release = updater.ReleaseInfo(
            tag="v999.0.0",
            version=(999, 0, 0),
            notes="Adds auto-update.",
            download_url="https://example.invalid/bundle.zip",
            asset_name="bundle.zip",
        )
        with mock.patch.object(QMessageBox, "information") as info_mock, mock.patch.object(
            QMessageBox, "question"
        ) as question_mock:
            self.window._handle_update_check_finished(release, None)
        info_mock.assert_called_once()
        question_mock.assert_not_called()
        self.assertIn("v999.0.0", info_mock.call_args.args[2])
        self.assertIn("git pull", info_mock.call_args.args[2])
