"""The About dialog shared by the apps: which app and version this is, how to cite it, which licence it has.

Screen purpose: tell the user what they are running and make the acknowledgement of the software easy (a citation line to copy).
Main actions: read, "Copy citation", follow the link to the full notice, close.
Layout (one column): app name and version, one-line description; "How to cite" with the citation line (selectable) and the
"Copy citation" button; the acknowledgement request; the licence line with the link to NOTICE.md; the disclaimer points; "Close".
States: normal; after "Copy citation" the button says "Copied". Nothing to validate; if the clipboard is unavailable the button simply
does nothing.

The text comes from the caller (apps pass `lspr_core.citation` strings), so this package stays free of `lspr_core`.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class AboutDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        *,
        title: str,
        app_line: str,
        description: str,
        citation: str,
        acknowledgement: str,
        license_line: str,
        notice_url: str,
        disclaimer: Sequence[str] = (),
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)

        heading = QLabel(app_line, self)
        font = heading.font()
        font.setPointSizeF(font.pointSizeF() * 1.3)
        font.setBold(True)
        heading.setFont(font)
        about = QLabel(description, self)
        about.setWordWrap(True)

        cite_title = QLabel("How to cite", self)
        cite_font = cite_title.font()
        cite_font.setBold(True)
        cite_title.setFont(cite_font)
        self.citation_edit = QPlainTextEdit(citation, self)
        self.citation_edit.setReadOnly(True)
        self.citation_edit.setFixedHeight(self.citation_edit.fontMetrics().lineSpacing() * 4)
        self.copy_button = QPushButton("Copy citation", self)
        self.copy_button.clicked.connect(self.copy_citation)
        request = QLabel(acknowledgement, self)
        request.setWordWrap(True)

        licence = QLabel(f'{license_line} &nbsp;<a href="{notice_url}">Full notice and third-party licences</a>', self)
        licence.setTextFormat(Qt.TextFormat.RichText)
        licence.setOpenExternalLinks(True)
        licence.setWordWrap(True)
        points = QLabel("<br>".join(f"&bull; {text}" for text in disclaimer), self)
        points.setWordWrap(True)
        points.setVisible(bool(disclaimer))

        close_button = QPushButton("Close", self)
        close_button.setDefault(True)
        close_button.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        for widget in (heading, about, cite_title, self.citation_edit, request, licence, points):
            layout.addWidget(widget)
        layout.addLayout(buttons)

    def copy_citation(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(self.citation_edit.toPlainText())
        self.copy_button.setText("Copied")


def show_about_dialog(parent: QWidget | None, **kwargs: object) -> None:
    """Build the dialog (see `AboutDialog` for the keywords) and run it."""
    AboutDialog(parent, **kwargs).exec()
