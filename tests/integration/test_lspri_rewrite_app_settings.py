"""App-level settings round trip for the LSPRimaging Evaluation rewrite.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring for why.

`storage/app_settings.py` (2026-09-26) is the rewrite's first app-level
persistence layer - before it, nothing survived a restart at all (not even
theme or window size), which is the bug this file exists to prevent
regressing. Deliberately lenient on read failures (unlike
`storage/session.py`'s `load_session`, which raises): losing a session file
looks identical to "this dataset was never analyzed", which matters; losing
convenience settings (last folder, theme, window size) does not, so this
falls back to defaults silently rather than blocking startup.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.storage.app_settings import (
        APP_SETTINGS_SCHEMA_NAME,
        AppSettings,
        load_app_settings,
        save_app_settings,
    )
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


class AppSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self._tmp.name) / "settings.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_missing_file_loads_as_defaults(self) -> None:
        self.assertEqual(load_app_settings(self.path), AppSettings())

    def test_every_field_round_trips(self) -> None:
        state = AppSettings(
            last_dataset_folder=r"C:\datasets\run7",
            auto_reopen_last_dataset=False,
            theme="bright",
            main_window_geometry="Z2VvbWV0cnk=",
            main_window_state="c3RhdGU=",
            active_layout_preset="Analysis",
            layout_presets={"Analysis": "YmxvYg=="},
            auto_apply_preset_on_stage_change=True,
        )
        save_app_settings(state, self.path)
        self.assertEqual(load_app_settings(self.path), state)

    def test_defaults_favor_auto_reopen_and_dark_theme(self) -> None:
        """The maintainer's explicit choice (2026-09-26): auto-reopen is on
        by default, changeable via the Options menu toggle - not an opt-in."""
        defaults = AppSettings()
        self.assertTrue(defaults.auto_reopen_last_dataset)
        self.assertEqual(defaults.theme, "dark")
        self.assertIsNone(defaults.last_dataset_folder)

    def test_an_unrecognised_schema_falls_back_to_defaults(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"schema_name": "something_else"}), encoding="utf-8")
        self.assertEqual(load_app_settings(self.path), AppSettings())

    def test_a_future_major_version_falls_back_to_defaults(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"schema_name": APP_SETTINGS_SCHEMA_NAME, "schema_version": "99.0"}), encoding="utf-8"
        )
        self.assertEqual(load_app_settings(self.path), AppSettings())

    def test_corrupt_json_falls_back_to_defaults_rather_than_raising(self) -> None:
        """Unlike `load_session`, a broken app-settings file must never
        block startup - see module docstring."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not valid json", encoding="utf-8")
        self.assertEqual(load_app_settings(self.path), AppSettings())

    def test_unknown_extra_keys_are_ignored(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({
                "schema_name": APP_SETTINGS_SCHEMA_NAME,
                "schema_version": "1.0",
                "theme": "bright",
                "a_future_field_this_build_does_not_know": 123,
            }),
            encoding="utf-8",
        )
        self.assertEqual(load_app_settings(self.path).theme, "bright")


if __name__ == "__main__":
    unittest.main()
