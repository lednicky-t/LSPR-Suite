from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal


SUCCESS_LOG_LEVEL = 25
logging.addLevelName(SUCCESS_LOG_LEVEL, "SUCCESS")


class GuiLogBridge(QObject):
    record_received = pyqtSignal(int, str, str)


class GuiLogHandler(logging.Handler):
    def __init__(self, bridge: GuiLogBridge) -> None:
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        self._bridge.record_received.emit(int(record.levelno), str(record.name), message)
