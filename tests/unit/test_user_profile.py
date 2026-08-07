"""Unit tests for lspr_acq_shell.user_profile - the per-user identity
registry backing the User look-up field next to the recording destination
(no password; see the module docstring). Moved here from
apps/sLSPR/acq/tests (Phase 1 shell extraction, 2026-08-07) - this is now
the real owner of the module-level state these tests patch;
apps/sLSPR/acq/src/lspr_app/storage/user_profile.py is a thin re-export
shim over this module and has no state of its own to test. Every test
points the module's three path constants at a fresh temp directory so
nothing touches the real user_config_dir("lspr-suite") location.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._paths import ensure_repo_paths

ensure_repo_paths()

from lspr_acq_shell import user_profile as up


class _TempConfigDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self._patches = [
            patch.object(up, "_SHARED_CONFIG_DIR", base),
            patch.object(up, "_REGISTRY_PATH", base / "lspr_users.json"),
            patch.object(up, "GLOBAL_CONFIG_PATH", base / "lspr_settings.json"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        self.base = base


class RegistryBasicsTests(_TempConfigDirTestCase):
    def test_no_registry_file_yet_reports_no_active_user(self) -> None:
        self.assertFalse(up.registry_exists())
        self.assertIsNone(up.active_user())
        self.assertEqual(up.list_known_users(), [])

    def test_set_active_user_creates_registry_and_active_user(self) -> None:
        up.set_active_user("Alex Chen")
        self.assertTrue(up.registry_exists())
        self.assertEqual(up.active_user(), "Alex Chen")
        self.assertEqual(up.list_known_users(), ["Alex Chen"])

    def test_set_active_user_twice_does_not_duplicate_known_list(self) -> None:
        up.set_active_user("Alex Chen")
        up.set_active_user("Alex Chen")
        self.assertEqual(up.list_known_users(), ["Alex Chen"])

    def test_switching_between_two_users_keeps_both_known(self) -> None:
        up.set_active_user("Alex Chen")
        up.set_active_user("Jamie Lee")
        self.assertEqual(up.active_user(), "Jamie Lee")
        self.assertEqual(up.list_known_users(), ["Alex Chen", "Jamie Lee"])

    def test_blank_name_is_a_no_op(self) -> None:
        up.set_active_user("   ")
        self.assertIsNone(up.active_user())
        self.assertFalse(up.registry_exists())

    def test_corrupt_registry_file_is_treated_as_empty(self) -> None:
        up._REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        up._REGISTRY_PATH.write_text("{not valid json", encoding="utf-8")
        self.assertEqual(up.list_known_users(), [])
        self.assertIsNone(up.active_user())


class UserSettingsPathTests(_TempConfigDirTestCase):
    def test_current_config_path_falls_back_to_global_when_no_user_active(self) -> None:
        self.assertEqual(up.current_config_path(), up.GLOBAL_CONFIG_PATH)

    def test_current_config_path_follows_active_user(self) -> None:
        up.set_active_user("Alex Chen")
        self.assertEqual(up.current_config_path(), up.user_settings_path("Alex Chen"))
        self.assertNotEqual(up.current_config_path(), up.GLOBAL_CONFIG_PATH)

    def test_two_different_users_get_two_different_paths(self) -> None:
        path_a = up.user_settings_path("Alex Chen")
        path_b = up.user_settings_path("Jamie Lee")
        self.assertNotEqual(path_a, path_b)

    def test_unsafe_characters_in_name_are_sanitized(self) -> None:
        path = up.user_settings_path('Alex/Chen:<>"?')
        self.assertNotIn("/", path.name)
        self.assertNotIn(":", str(path.relative_to(self.base)))


class MigrationTests(_TempConfigDirTestCase):
    def test_first_user_ever_migrates_existing_flat_settings(self) -> None:
        up.GLOBAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        up.GLOBAL_CONFIG_PATH.write_text(json.dumps({"app": {"theme_mode": "light"}}), encoding="utf-8")

        up.set_active_user("Alex Chen")

        migrated_path = up.user_settings_path("Alex Chen")
        self.assertTrue(migrated_path.exists())
        payload = json.loads(migrated_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["app"]["theme_mode"], "light")
        # The original flat file is a safety net - untouched, not moved.
        self.assertTrue(up.GLOBAL_CONFIG_PATH.exists())

    def test_second_user_does_not_re_migrate(self) -> None:
        up.GLOBAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        up.GLOBAL_CONFIG_PATH.write_text(json.dumps({"app": {"theme_mode": "light"}}), encoding="utf-8")
        up.set_active_user("Alex Chen")

        # Change the flat file after the first migration - a second new
        # user must NOT pick this up, since migration only ever runs once.
        up.GLOBAL_CONFIG_PATH.write_text(json.dumps({"app": {"theme_mode": "dark"}}), encoding="utf-8")
        up.set_active_user("Jamie Lee")

        self.assertFalse(up.user_settings_path("Jamie Lee").exists())

    def test_no_flat_file_yet_is_a_harmless_no_op(self) -> None:
        up.set_active_user("Alex Chen")
        self.assertFalse(up.user_settings_path("Alex Chen").exists())


class RemoveKnownUserTests(_TempConfigDirTestCase):
    def test_remove_known_user(self) -> None:
        up.set_active_user("Alex Chen")
        up.set_active_user("Jamie Lee")
        up.remove_known_user("Alex Chen")
        self.assertEqual(up.list_known_users(), ["Jamie Lee"])

    def test_cannot_remove_the_active_user(self) -> None:
        up.set_active_user("Alex Chen")
        up.remove_known_user("Alex Chen")
        self.assertEqual(up.list_known_users(), ["Alex Chen"])

    def test_removing_unknown_name_is_a_no_op(self) -> None:
        up.set_active_user("Alex Chen")
        up.remove_known_user("Nobody")
        self.assertEqual(up.list_known_users(), ["Alex Chen"])

    def test_removal_never_deletes_the_settings_file(self) -> None:
        up.set_active_user("Alex Chen")
        up.set_active_user("Jamie Lee")
        settings_path = up.user_settings_path("Alex Chen")
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{}", encoding="utf-8")
        up.remove_known_user("Alex Chen")
        self.assertTrue(settings_path.exists())


if __name__ == "__main__":
    unittest.main()
