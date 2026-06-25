from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PyQt6 import QtWidgets
from PyQt6.QtCore import QSettings

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from lspr_core import (
    LAUNCH_PROFILE_ENV_VAR,
    LAUNCH_PROFILE_CONTROL_EDITOR,
    LAUNCH_PROFILE_SIMULATION,
    launch_profile_spec,
    normalize_launch_profile,
)
from suite_launcher.app import LaunchCard
from suite_launcher.app import _settings_bool
from suite_launcher.targets import TARGETS, _candidate_paths
from suite_launcher.targets import AppTarget


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
        quiet_enabled = False

        def _is_quiet_enabled() -> bool:
            return quiet_enabled

        def _toggle_quiet_enabled() -> None:
            nonlocal quiet_enabled
            quiet_enabled = not quiet_enabled

        info_logs_enabled = False

        def _is_info_logs_enabled() -> bool:
            return info_logs_enabled

        def _toggle_info_logs_enabled() -> None:
            nonlocal info_logs_enabled
            info_logs_enabled = not info_logs_enabled

        card = LaunchCard(
            target,
            theme=type("Theme", (), {"accent": "#4C8BF5", "accent_soft": "#183563"})(),
            launch_callback=lambda _target: None,
            profile_key_callback=lambda: "full",
            profile_cycle_callback=lambda: None,
            profile_text_callback=lambda: "Full",
            profile_tooltip_callback=lambda: "Full mode",
            diagnostics_enabled_callback=_is_quiet_enabled,
            diagnostics_toggle_callback=_toggle_quiet_enabled,
            diagnostics_info_logs_enabled_callback=_is_info_logs_enabled,
            diagnostics_info_logs_toggle_callback=_toggle_info_logs_enabled,
        )
        self.assertTrue(hasattr(card, "mode_label"))
        self.assertIn("Mode:", card.mode_label.text())
        self.assertIn("Full", card.mode_label.text())
        self.assertTrue(hasattr(card, "diagnostics_button"))
        self.assertTrue(hasattr(card, "diagnostics_info_logs_button"))
        self.assertIn("Quiet logs: Off", card.diagnostics_button.text())
        self.assertIn("File info: On", card.diagnostics_info_logs_button.text())
        self.assertIn("Flags: quiet=off | file=on", card.launch_flags_label.text())
        self.assertEqual(card.button.text(), "Launch")
        card.set_auto_launch_pending(True, 3)
        self.assertIn("Stop launch", card.button.text())
        card._toggle_diagnostics()
        card._update_diagnostics_button()
        card._update_launch_flags_label()
        self.assertIn("Quiet logs: On", card.diagnostics_button.text())
        self.assertIn("Flags: quiet=on | file=on", card.launch_flags_label.text())
        card._toggle_diagnostics_info_logs()
        card._update_diagnostics_info_logs_button()
        self.assertIn("File info: Off", card.diagnostics_info_logs_button.text())
        card._update_launch_flags_label()
        self.assertIn("Flags: quiet=on | file=off", card.launch_flags_label.text())
        card.set_running(True)
        self.assertEqual(card.button.text(), "Kill")
        card.set_running(False)
        self.assertEqual(card.button.text(), "Launch")
