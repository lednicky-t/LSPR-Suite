from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PyQt6 import QtWidgets

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

    def test_launch_card_states_and_inline_mode_chip(self) -> None:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.assertIsNotNone(app)
        target = next(target for target in TARGETS if target.key == "slspr_acq")
        card = LaunchCard(
            target,
            theme=type("Theme", (), {"accent": "#4C8BF5", "accent_soft": "#183563"})(),
            launch_callback=lambda _target: None,
            profile_key_callback=lambda: "full",
            profile_cycle_callback=lambda: None,
            profile_text_callback=lambda: "Full",
            profile_tooltip_callback=lambda: "Full mode",
        )
        self.assertTrue(hasattr(card, "mode_label"))
        self.assertIn("Mode:", card.mode_label.text())
        self.assertIn("Full", card.mode_label.text())
        self.assertEqual(card.button.text(), "Launch")
        card.set_auto_launch_pending(True, 3)
        self.assertIn("Stop launch", card.button.text())
        card.set_running(True)
        self.assertEqual(card.button.text(), "Kill")
        card.set_running(False)
        self.assertEqual(card.button.text(), "Launch")
