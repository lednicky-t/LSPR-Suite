"""Screen resolution choice (`lspr_imaging_app/display_options.py`, 2026-11-11): what Qt's platform option becomes for each saved choice.

Pure logic, no Qt and no files except where the saved choice is read from a temporary settings file.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app import display_options as opts
    from lspr_imaging_app.storage.app_settings import AppSettings, save_app_settings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class PlatformValueTests(unittest.TestCase):
    def test_full_resolution_never_changes_the_variable(self) -> None:
        for current in ("", "windows", "offscreen", "windows:dpiawareness=2"):
            self.assertIsNone(opts.platform_value("full", current, "win32"), current)

    def test_standard_on_a_unset_variable_asks_windows_for_96_dpi(self) -> None:
        self.assertEqual(opts.platform_value("standard", "", "win32"), "windows:dpiawareness=0")
        self.assertEqual(opts.platform_value("standard", "  ", "win32"), "windows:dpiawareness=0")

    def test_standard_adds_the_option_to_an_existing_windows_value(self) -> None:
        self.assertEqual(opts.platform_value("standard", "windows", "win32"), "windows:dpiawareness=0")
        self.assertEqual(opts.platform_value("standard", "windows:nodirectwrite", "win32"), "windows:nodirectwrite,dpiawareness=0")

    def test_a_dpi_option_somebody_set_by_hand_is_left_alone(self) -> None:
        self.assertIsNone(opts.platform_value("standard", "windows:dpiawareness=2", "win32"))

    def test_another_platform_is_never_overridden(self) -> None:
        """The tests run on `offscreen`: a saved "standard" must not turn that into a real window."""
        self.assertIsNone(opts.platform_value("standard", "offscreen", "win32"))
        self.assertIsNone(opts.platform_value("standard", "", "linux"))
        self.assertIsNone(opts.platform_value("standard", "", "darwin"))

    def test_asks_for_96_dpi_reads_the_variable(self) -> None:
        self.assertTrue(opts._asks_for_96_dpi("windows:dpiawareness=0"))
        self.assertTrue(opts._asks_for_96_dpi("windows:nodirectwrite, dpiawareness=0"))
        self.assertFalse(opts._asks_for_96_dpi("windows:dpiawareness=2"))
        self.assertFalse(opts._asks_for_96_dpi("offscreen"))
        self.assertFalse(opts._asks_for_96_dpi(""))


class SavedChoiceTests(unittest.TestCase):
    def _settings_file(self, ui_state: dict) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "settings.json"
        save_app_settings(AppSettings(ui_state=ui_state), path)
        return path

    def _saved_with(self, ui_state: dict) -> str:
        path = self._settings_file(ui_state)
        with mock.patch("lspr_imaging_app.storage.app_settings.default_settings_path", return_value=path):
            return opts.saved_resolution()

    def test_the_default_is_full(self) -> None:
        self.assertEqual(self._saved_with({}), "full")

    def test_a_saved_standard_is_read(self) -> None:
        self.assertEqual(self._saved_with({"display/resolution": "standard"}), "standard")

    def test_a_wrong_value_or_type_falls_back_to_full(self) -> None:
        self.assertEqual(self._saved_with({"display/resolution": "huge"}), "full")
        self.assertEqual(self._saved_with({"display/resolution": 3}), "full")

    def test_an_unreadable_settings_file_means_full_and_never_raises(self) -> None:
        with mock.patch("lspr_imaging_app.storage.app_settings.load_app_settings", side_effect=OSError("disk gone")):
            self.assertEqual(opts.saved_resolution(), "full")


class ApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._environment = mock.patch.dict(os.environ, clear=False)
        self._environment.start()
        self.addCleanup(self._environment.stop)
        self.addCleanup(setattr, opts, "_running", opts._running)

    def test_a_saved_standard_sets_the_variable_and_is_reported_as_running(self) -> None:
        os.environ.pop("QT_QPA_PLATFORM", None)
        with mock.patch.object(opts, "saved_resolution", return_value="standard"), mock.patch.object(opts.sys, "platform", "win32"):
            self.assertEqual(opts.apply_saved_resolution(), "standard")
            self.assertEqual(os.environ["QT_QPA_PLATFORM"], "windows:dpiawareness=0")
            self.assertEqual(opts.running_resolution(), "standard")

    def test_full_leaves_the_variable_alone(self) -> None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        with mock.patch.object(opts, "saved_resolution", return_value="full"):
            self.assertEqual(opts.apply_saved_resolution(), "full")
        self.assertEqual(os.environ["QT_QPA_PLATFORM"], "offscreen")

    def test_standard_saved_but_not_possible_runs_at_full_and_says_so(self) -> None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        with mock.patch.object(opts, "saved_resolution", return_value="standard"), self.assertLogs(opts.logger, "INFO"):
            self.assertEqual(opts.apply_saved_resolution(), "full")
        self.assertEqual(os.environ["QT_QPA_PLATFORM"], "offscreen")
        self.assertEqual(opts.running_resolution(), "full")

    def test_a_variable_already_asking_for_96_dpi_counts_as_standard(self) -> None:
        os.environ["QT_QPA_PLATFORM"] = "windows:dpiawareness=0"
        with mock.patch.object(opts, "saved_resolution", return_value="full"), mock.patch.object(opts.sys, "platform", "win32"):
            self.assertEqual(opts.apply_saved_resolution(), "standard")


if __name__ == "__main__":
    unittest.main()
