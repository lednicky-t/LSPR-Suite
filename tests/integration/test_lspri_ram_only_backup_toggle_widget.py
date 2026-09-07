"""Integration test for the real [disk]/[RAM] toggle widget in the Analysis
section's title row (gui/layout_builder.py's _make_ram_only_backup_toggle) -
clicks the actual QToolButton on a real MainWindow, the same way this
repo's other GUI tests drive real buttons directly rather than via screen
coordinates (see CLAUDE.md's "Prefer widgets that are directly callable"
performance-work note). The pure decision logic this toggle gates is
covered separately in tests/unit/test_lspri_ram_only_backup_toggle.py; this
test is only about the widget/state wiring itself - does a real click
actually flip `_analysis_ram_only_backup` and re-render the button.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

# Must exist before any lspr_imaging_app.gui module is imported below - Qt
# objects get built at import time in some of those modules.
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.main_window import MainWindow  # noqa: E402


class TestRamOnlyBackupToggleWidget(unittest.TestCase):
    def setUp(self) -> None:
        self.window = MainWindow(REPO_ROOT, fast_startup=True)

    def tearDown(self) -> None:
        self.window._state.dataset = None
        self.window.close()
        self.window.deleteLater()

    def test_defaults_to_off_every_launch(self) -> None:
        # Deliberately not persisted via QSettings (unlike the neighboring
        # [λ,t]/[λ] toggle) - see MainWindow._analysis_ram_only_backup's
        # docstring for why. A fresh window must always start in the safe
        # (incremental-backup) state regardless of what a previous session
        # left it as.
        self.assertFalse(self.window._analysis_ram_only_backup)
        self.assertEqual(self.window.analysis_ram_only_backup_toggle.text(), "[disk]")

    def test_clicking_the_real_button_flips_state_and_label(self) -> None:
        button = self.window.analysis_ram_only_backup_toggle
        button.click()
        self.assertTrue(self.window._analysis_ram_only_backup)
        self.assertEqual(button.text(), "[RAM]")

        button.click()
        self.assertFalse(self.window._analysis_ram_only_backup)
        self.assertEqual(button.text(), "[disk]")

    def test_toggle_is_parented_under_the_analysis_section(self) -> None:
        # Confirms it's actually wired into the section's widget tree, not
        # just constructed and forgotten (e.g. never added to a layout) -
        # mirrors how analysis_time_independent_toggle sits in the same
        # title row.
        button = self.window.analysis_ram_only_backup_toggle
        ancestor = button.parentWidget()
        found = False
        while ancestor is not None:
            if ancestor is self.window.analysis_section:
                found = True
                break
            ancestor = ancestor.parentWidget()
        self.assertTrue(found, "toggle button is not parented anywhere under analysis_section")


if __name__ == "__main__":
    unittest.main()
