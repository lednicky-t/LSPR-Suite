"""Help -> About in the LSPRimaging Evaluation window: last Help entry, opens the shared About dialog with the version and the citation.

A real window built in-process, never `.exec()`; the dialog itself is replaced where it would block."""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.version import APP_VERSION
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


class AboutMenuTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = app_rewrite.build_main_window()
        self.addCleanup(self.window.close)
        self.menu = next(a.menu() for a in self.window.menuBar().actions() if a.text() == "&Help")

    def test_about_is_the_last_help_entry_and_shortcuts_stay_first(self) -> None:
        actions = [a for a in self.menu.actions() if not a.isSeparator()]
        self.assertEqual(actions[0].text(), "Keyboard shortcuts")
        self.assertIn("About", actions[-1].text())
        self.assertIn("Log Console", [a.text() for a in actions])

    def test_the_action_opens_the_about_dialog_with_the_version_and_the_citation(self) -> None:
        about = next(a for a in self.menu.actions() if "About" in a.text())
        with mock.patch.object(app_rewrite, "show_about_dialog") as show:
            about.trigger()
        arguments = show.call_args.kwargs
        self.assertEqual(show.call_args.args[0], self.window)
        self.assertEqual(arguments["title"], "About LSPRimaging Evaluation")
        self.assertIn(APP_VERSION, arguments["app_line"])
        self.assertIn(f"version {APP_VERSION}", arguments["citation"])
        self.assertTrue(arguments["notice_url"].endswith("NOTICE.md"))


if __name__ == "__main__":
    unittest.main()
