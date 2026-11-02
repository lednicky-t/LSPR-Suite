"""The shared About dialog (`lspr_ui.AboutDialog`) and the launcher's first-run notice (`suite_launcher.notice`).

A real `QApplication` built in-process, never `.exec()` on the application; the modal dialogs' own `exec` is replaced where a test would
otherwise block. The settings live in a temporary .ini file, never in the registry."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QDialog

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import ensure_repo_paths  # noqa: E402

ensure_repo_paths()

from lspr_core import ACKNOWLEDGEMENT_REQUEST, DISCLAIMER_POINTS, NOTICE_URL, citation_text  # noqa: E402
from lspr_ui import AboutDialog  # noqa: E402
from suite_launcher import notice  # noqa: E402
from suite_launcher.version import APP_VERSION  # noqa: E402

CITATION = citation_text("LSPR Acquisition", "0.4.1")


def _about(parent: QtWidgets.QWidget | None = None) -> AboutDialog:
    return AboutDialog(
        parent,
        title="About LSPR Acquisition",
        app_line="LSPR Acquisition 0.4.1",
        description="Spectrometer acquisition.",
        citation=CITATION,
        acknowledgement=ACKNOWLEDGEMENT_REQUEST,
        license_line="MIT License, Copyright (c) 2026 Tomáš Lednický.",
        notice_url=NOTICE_URL,
        disclaimer=DISCLAIMER_POINTS,
    )


class AboutDialogTests(unittest.TestCase):
    def test_the_dialog_shows_the_citation_and_the_link_to_the_notice(self) -> None:
        dialog = _about()
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.citation_edit.toPlainText(), CITATION)
        self.assertTrue(dialog.citation_edit.isReadOnly())
        labels = " ".join(label.text() for label in dialog.findChildren(QtWidgets.QLabel))
        self.assertIn(NOTICE_URL, labels)
        self.assertIn("diagnostic", labels)
        self.assertEqual(dialog.windowTitle(), "About LSPR Acquisition")

    def test_copy_citation_puts_the_line_on_the_clipboard_and_says_so(self) -> None:
        dialog = _about()
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.copy_button.text(), "Copy citation")
        dialog.copy_button.click()
        self.assertEqual(QGuiApplication.clipboard().text(), CITATION)
        self.assertEqual(dialog.copy_button.text(), "Copied")


class FirstRunNoticeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = QSettings(str(Path(self._tmp.name) / "launcher.ini"), QSettings.Format.IniFormat)

    def test_the_notice_is_due_until_the_current_revision_was_clicked_through(self) -> None:
        self.assertTrue(notice.needs_notice(self.settings))
        notice.record_notice_seen(self.settings)
        self.assertFalse(notice.needs_notice(self.settings))
        with mock.patch.object(notice, "NOTICE_REVISION", notice.NOTICE_REVISION + 1):  # the wording was revised
            self.assertTrue(notice.needs_notice(self.settings))

    def test_a_damaged_setting_counts_as_not_seen(self) -> None:
        self.settings.setValue("notice/accepted_revision", "not a number")
        self.assertTrue(notice.needs_notice(self.settings))

    def test_the_dialog_says_what_the_user_has_to_know_and_its_button_accepts(self) -> None:
        dialog = notice.FirstRunNoticeDialog()
        self.addCleanup(dialog.deleteLater)
        text = " ".join(label.text() for label in dialog.findChildren(QtWidgets.QLabel)).replace('"', "").lower()
        for phrase in ("research software", "as is", "backups", "diagnostic", "cite"):
            self.assertIn(phrase, text)
        self.assertEqual(dialog.ok_button.text(), "OK, I understand")
        dialog.ok_button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_clicking_through_is_remembered_and_closing_the_window_is_not(self) -> None:
        with mock.patch.object(notice.FirstRunNoticeDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            self.assertTrue(notice.show_notice_if_needed(self.settings))
        self.assertTrue(notice.needs_notice(self.settings))  # shown again at the next start
        with mock.patch.object(notice.FirstRunNoticeDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            self.assertTrue(notice.show_notice_if_needed(self.settings))
        self.assertFalse(notice.needs_notice(self.settings))
        with mock.patch.object(notice.FirstRunNoticeDialog, "exec") as exec_dialog:
            self.assertFalse(notice.show_notice_if_needed(self.settings))  # already seen: not even built
        exec_dialog.assert_not_called()

    def test_without_lspr_core_the_launcher_still_has_text_to_show(self) -> None:
        with mock.patch.dict(sys.modules, {"lspr_core": None}):  # makes `from lspr_core import ...` raise ImportError
            citation, request, points, url = notice.citation_parts()
        self.assertIn(APP_VERSION, citation)
        self.assertIn("cite", request)
        self.assertEqual(len(points), 3)
        self.assertTrue(url.endswith("NOTICE.md"))

    def test_the_launcher_about_names_the_suite_version_and_cites_it(self) -> None:
        with mock.patch.object(notice, "show_about_dialog") as show:
            notice.show_about(None)
        arguments = show.call_args.kwargs
        self.assertEqual(arguments["title"], "About LSPR Suite")
        self.assertIn(APP_VERSION, arguments["app_line"])
        self.assertIn(f"version {APP_VERSION}", arguments["citation"])


if __name__ == "__main__":
    unittest.main()
