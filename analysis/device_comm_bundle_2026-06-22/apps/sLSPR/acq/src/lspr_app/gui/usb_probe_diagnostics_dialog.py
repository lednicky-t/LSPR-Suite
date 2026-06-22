from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout

from lspr_app.device.probe_diagnostics import snapshot_port_probe_events


def _format_probe_events_text() -> str:
    events = snapshot_port_probe_events()
    if not events:
        return "No USB probe events recorded yet."
    lines: list[str] = []
    for event in events:
        duration = f"{event.duration_ms:.1f} ms" if event.duration_ms else "-"
        command = event.command or "-"
        lines.append(
            f"{event.phase} | {event.role} | {event.port} | assignment={event.assignment} | "
            f"{event.action} | {event.result} | {duration} | cmd={command} | owner={event.owner} | {event.message}"
        )
    return "\n".join(lines)


class UsbProbeDiagnosticsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("USB probe diagnostics")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(980, 420)

        self._text = QPlainTextEdit(self)
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._text.setPlaceholderText("USB probe events will appear here.")

        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.clicked.connect(self.refresh_content)

        self._copy_button = QPushButton("Copy text")
        self._copy_button.clicked.connect(self._copy_text)

        self._close_button = QPushButton("Close")
        self._close_button.clicked.connect(self.close)

        button_row = QHBoxLayout()
        button_row.addWidget(self._refresh_button)
        button_row.addWidget(self._copy_button)
        button_row.addStretch(1)
        button_row.addWidget(self._close_button)

        layout = QVBoxLayout()
        layout.addWidget(self._text, 1)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self.refresh_content()

    def refresh_content(self) -> None:
        self._text.setPlainText(_format_probe_events_text())
        self._text.moveCursor(self._text.textCursor().MoveOperation.End)

    def _copy_text(self) -> None:
        self._text.selectAll()
        self._text.copy()
        self._text.moveCursor(self._text.textCursor().MoveOperation.End)


def show_usb_probe_diagnostics_dialog(window) -> UsbProbeDiagnosticsDialog:
    dialog = getattr(window, "_usb_probe_diagnostics_dialog", None)
    if not isinstance(dialog, UsbProbeDiagnosticsDialog):
        dialog = UsbProbeDiagnosticsDialog(window)
        window._usb_probe_diagnostics_dialog = dialog
    else:
        dialog.refresh_content()
    if dialog.isVisible():
        dialog.raise_()
        dialog.activateWindow()
    else:
        dialog.show()
    return dialog
