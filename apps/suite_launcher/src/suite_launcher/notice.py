"""The launcher's About dialog and its first-run notice (what the software is, how to acknowledge it, that it comes as is).

First-run notice - screen purpose: tell the user once (and again when the text is revised) that this is research software provided as
is, and how to acknowledge it. It is information, not a gate: the licence (MIT) does not require anyone to click anything.
Main actions: "OK, I understand" (records the revision of the text that was seen), "Read the full notice" (opens NOTICE.md on GitHub).
Layout: a modal dialog over the launcher: a title, the disclaimer points and the acknowledgement request as short paragraphs, the
licence line, the two buttons at the bottom right.
States: first run, or the revision below was raised -> shown; already seen -> not shown. Closing the window without the button is not
recorded, so it is shown again at the next start. If the setting cannot be saved it is simply shown again.

The wording comes from `lspr_core.citation`, imported only here and only when needed, with a plain fallback: nothing in this module may
stop the launcher from starting.
"""

from __future__ import annotations

from PyQt6.QtCore import QSettings, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from lspr_ui import show_about_dialog

from .version import APP_VERSION

NOTICE_REVISION = 1  # raise it when the wording changes in a way users should read again
_ACCEPTED_KEY = "notice/accepted_revision"
_FALLBACK_URL = "https://github.com/lednicky-t/LSPR-Suite/blob/main/NOTICE.md"
_FALLBACK_REQUEST = "If you use it to produce results in a publication, thesis or report, please cite it and name it in the acknowledgements."
_FALLBACK_POINTS = (
    'Provided "as is", without warranty of any kind. The author is not liable for loss or corruption of data or for wrong results.',
    "Check the analysis on a measurement with a known answer, and keep your own backups of raw data.",
    "Not validated for diagnostic, clinical or safety-critical use.",
)
_LICENSE_LINE = "MIT License, Copyright (c) 2026 Tomáš Lednický. Free to use, also commercially."


def needs_notice(settings: QSettings) -> bool:
    """True until the user has clicked through the current revision of the notice."""
    try:
        seen = int(settings.value(_ACCEPTED_KEY, 0) or 0)
    except (TypeError, ValueError):
        seen = 0
    return seen < NOTICE_REVISION


def record_notice_seen(settings: QSettings) -> None:
    settings.setValue(_ACCEPTED_KEY, NOTICE_REVISION)
    settings.sync()


def citation_parts() -> tuple[str, str, tuple[str, ...], str]:
    """(citation line, acknowledgement request, disclaimer points, URL of the full notice), from `lspr_core` if it can be imported."""
    try:
        from lspr_core import ACKNOWLEDGEMENT_REQUEST, DISCLAIMER_POINTS, NOTICE_URL, citation_text
    except ImportError:
        return f"Lednický, T. LSPR Suite, version {APP_VERSION}. {_FALLBACK_URL}", _FALLBACK_REQUEST, _FALLBACK_POINTS, _FALLBACK_URL
    return citation_text(None, APP_VERSION), ACKNOWLEDGEMENT_REQUEST, tuple(DISCLAIMER_POINTS), NOTICE_URL


class FirstRunNoticeDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _citation, request, points, notice_url = citation_parts()
        self._notice_url = notice_url
        self.setWindowTitle("Before you start")
        self.setMinimumWidth(480)
        title = QLabel("LSPR Suite is research software", self)
        font = title.font()
        font.setPointSizeF(font.pointSizeF() * 1.25)
        font.setBold(True)
        title.setFont(font)
        body = QLabel("<br>".join(f"&bull; {text}" for text in points), self)
        body.setWordWrap(True)
        acknowledge = QLabel(request, self)
        acknowledge.setWordWrap(True)
        licence = QLabel(_LICENSE_LINE, self)
        licence.setWordWrap(True)
        self.read_button = QPushButton("Read the full notice", self)
        self.read_button.clicked.connect(self._open_notice)
        self.ok_button = QPushButton("OK, I understand", self)
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(self.read_button)
        buttons.addStretch(1)
        buttons.addWidget(self.ok_button)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        for widget in (title, body, acknowledge, licence):
            layout.addWidget(widget)
        layout.addLayout(buttons)

    def _open_notice(self) -> None:
        QDesktopServices.openUrl(QUrl(self._notice_url))


def show_notice_if_needed(settings: QSettings, parent: QWidget | None = None) -> bool:
    """Show the first-run notice unless this revision was already seen; remember it once the user clicked through. True if it was shown."""
    if not needs_notice(settings):
        return False
    if FirstRunNoticeDialog(parent).exec() == QDialog.DialogCode.Accepted:
        record_notice_seen(settings)
    return True


def show_about(parent: QWidget | None = None) -> None:
    """The launcher's About dialog (the Suite itself: its version is the Suite's version)."""
    citation, request, points, notice_url = citation_parts()
    show_about_dialog(
        parent,
        title="About LSPR Suite",
        app_line=f"LSPR Suite {APP_VERSION}",
        description="Launcher for the LSPR acquisition and evaluation apps.",
        citation=citation,
        acknowledgement=request,
        license_line=_LICENSE_LINE,
        notice_url=notice_url,
        disclaimer=points,
    )
