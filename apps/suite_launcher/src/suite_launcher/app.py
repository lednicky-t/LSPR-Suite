from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import ctypes
from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QSettings, Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import apply_base_app_theme, app_icon, get_active_theme
from lspr_core import (
    DEFAULT_LAUNCH_PROFILE,
    LAUNCH_PROFILE_ENV_VAR,
    LAUNCH_PROFILE_CONTROL_EDITOR,
    LAUNCH_PROFILE_FULL,
    LAUNCH_PROFILE_SIMULATION,
    launch_profile_spec,
    launch_profile_specs,
    normalize_launch_profile,
)

from .targets import TARGETS, AppTarget
from .version import APP_NAME, APP_VERSION


@dataclass(frozen=True)
class CardTheme:
    accent: str
    accent_soft: str


class ClickableLabel(QLabel):
    def __init__(self, text: str = "", clicked_callback: Callable[[], None] | None = None, parent=None) -> None:
        super().__init__(text, parent)
        self._clicked_callback = clicked_callback

    def mouseReleaseEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.button() == Qt.MouseButton.LeftButton and self._clicked_callback is not None:
            self._clicked_callback()
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _settings_bool(settings: QSettings, key: str, default: bool = False) -> bool:
    raw = settings.value(key, None)
    if raw is None:
        return bool(default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return bool(default)


CARD_THEMES = [
    CardTheme("#4C8BF5", "#183563"),
    CardTheme("#3AAFA9", "#163B3B"),
    CardTheme("#F39C12", "#4A2E0A"),
    CardTheme("#8E6CFF", "#2E215A"),
]

AUTO_LAUNCH_DELAY_MS = 3000
SETTINGS_LAST_TARGET_KEY = "lastTargetKey"
SETTINGS_LAUNCH_PROFILE_KEY = "launchProfileKey"
SETTINGS_DIAGNOSTICS_PROFILE_KEY = "diagnosticsProfileKey"
SETTINGS_ORGANIZATION = "LSPR Suite"
SETTINGS_APPLICATION = "Suite Launcher"
_LOG_LEVEL_RE = re.compile(r"^(?P<level>[A-Z]+):(?P<logger>[^:]+):")
_LOG_LEVEL_BRACKET_RE = re.compile(r"^\[(?P<level>[A-Z]+)\]")
_DIAGNOSTICS_PROFILES = ("off", "normal", "debug", "deep")
_WM_CLOSE = 0x0010


def _post_close_message_to_process(pid: int) -> bool:
    if os.name != "nt":
        return False
    user32 = ctypes.windll.user32
    result = {"sent": False}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum_windows(hwnd, _lparam):
        proc_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(proc_id))
        if int(proc_id.value) != int(pid):
            return True
        if user32.IsWindowVisible(ctypes.c_void_p(hwnd)) and user32.PostMessageW(ctypes.c_void_p(hwnd), _WM_CLOSE, 0, 0):
            result["sent"] = True
            return False
        return True

    try:
        user32.EnumWindows(_enum_windows, 0)
    except Exception:
        return False
    return bool(result["sent"])


def _normalize_diagnostics_profile(value: object, default: str = "normal") -> str:
    profile = str(value or "").strip().casefold()
    if profile in _DIAGNOSTICS_PROFILES:
        return profile
    return default


def _next_diagnostics_profile(profile: str) -> str:
    normalized = _normalize_diagnostics_profile(profile)
    index = _DIAGNOSTICS_PROFILES.index(normalized)
    return _DIAGNOSTICS_PROFILES[(index + 1) % len(_DIAGNOSTICS_PROFILES)]


def _diagnostics_profile_label(profile: str) -> str:
    normalized = _normalize_diagnostics_profile(profile)
    return normalized.capitalize()


def _diagnostics_profile_tooltip(profile: str) -> str:
    normalized = _normalize_diagnostics_profile(profile)
    if normalized == "off":
        return "Diagnostics off. Minimal GUI/file logging and no diagnostic export."
    if normalized == "normal":
        return "Normal diagnostics. User-relevant logs only, runtime drift in memory, no JSONL export."
    if normalized == "debug":
        return "Debug diagnostics. Extra timing/logging for developers, but still no JSONL export by default."
    return "Deep diagnostics. Enables diagnostic JSONL export and default session stats capture."


def _settings_diagnostics_profile(settings: QSettings) -> str:
    stored = str(settings.value(SETTINGS_DIAGNOSTICS_PROFILE_KEY, "", type=str) or "").strip()
    if stored:
        return _normalize_diagnostics_profile(stored)
    quiet = _settings_bool(settings, "quietDiagnosticsEnabled", False)
    suppress_file_info = _settings_bool(settings, "suppressDiagnosticInfoLogsEnabled", False)
    if quiet:
        return "off"
    if suppress_file_info:
        return "debug"
    return "normal"


class LaunchCard(QFrame):
    def __init__(
        self,
        target: AppTarget,
        theme: CardTheme,
        launch_callback: Callable[[AppTarget], None],
        profile_key_callback: Callable[[], str] | None = None,
        profile_cycle_callback: Callable[[], None] | None = None,
        profile_text_callback: Callable[[], str] | None = None,
        profile_tooltip_callback: Callable[[], str] | None = None,
        diagnostics_profile_cycle_callback: Callable[[], None] | None = None,
        diagnostics_profile_text_callback: Callable[[], str] | None = None,
        diagnostics_profile_tooltip_callback: Callable[[], str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.target = target
        self.theme = theme
        self.launch_callback = launch_callback
        self.profile_key_callback = profile_key_callback
        self.profile_cycle_callback = profile_cycle_callback
        self.profile_text_callback = profile_text_callback
        self.profile_tooltip_callback = profile_tooltip_callback
        self.diagnostics_profile_cycle_callback = diagnostics_profile_cycle_callback
        self.diagnostics_profile_text_callback = diagnostics_profile_text_callback
        self.diagnostics_profile_tooltip_callback = diagnostics_profile_tooltip_callback
        self._launch_button_base_text = "Launch"
        self._launch_pending = False
        self._launch_pending_seconds = 0
        self._running = False
        self._stopping = False
        self._launch_profile_text_colors = {
            LAUNCH_PROFILE_FULL: "#9FD7A6",
            LAUNCH_PROFILE_SIMULATION: "#F39C12",
            LAUNCH_PROFILE_CONTROL_EDITOR: "#8E6CFF",
        }
        self._launch_profile_text_colors = {
            LAUNCH_PROFILE_FULL: "#9FD7A6",
            LAUNCH_PROFILE_SIMULATION: "#F39C12",
            LAUNCH_PROFILE_CONTROL_EDITOR: "#8E6CFF",
        }
        self.setObjectName("LaunchCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setMinimumHeight(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        title = QLabel(target.title)
        title.setObjectName("CardTitle")
        subtitle = QLabel(target.subtitle)
        subtitle.setObjectName("CardSubtitle")
        subtitle.setWordWrap(True)

        badge = QLabel("Available" if target.is_available() else "Coming soon")
        badge.setObjectName("CardBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header_col = QVBoxLayout()
        header_col.setSpacing(2)
        header_col.addWidget(title)
        header_col.addWidget(subtitle)
        address = QLabel(target.address)
        address.setObjectName("CardAddress")
        address.setWordWrap(True)
        header_col.addWidget(address)
        top_row.addLayout(header_col, 1)
        top_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top_row)

        self.state_label = QLabel("Status: closed")
        self.state_label.setObjectName("CardState")
        self.state_label.setWordWrap(True)
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        status_row.addWidget(self.state_label, 0)
        status_row.addStretch(1)

        self.mode_label = None
        self.diagnostics_button = None
        if self.target.key == "slspr_acq":
            self.mode_label = ClickableLabel(clicked_callback=self._cycle_launch_profile)
            self.mode_label.setObjectName("ModeInlineLabel")
            self.mode_label.setTextFormat(Qt.TextFormat.RichText)
            self.mode_label.setCursor(Qt.CursorShape.PointingHandCursor)
            self.mode_label.setToolTip("Click to cycle the singleLSPR acquisition launch mode.")
            status_row.addWidget(self.mode_label, 0, Qt.AlignmentFlag.AlignRight)
            self.diagnostics_button = QPushButton()
            self.diagnostics_button.setObjectName("DiagnosticsInlineButton")
            self.diagnostics_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.diagnostics_button.clicked.connect(self._cycle_diagnostics_profile)
            status_row.addWidget(self.diagnostics_button, 0, Qt.AlignmentFlag.AlignRight)
            self._update_mode_label()
            self._update_diagnostics_button()
            self._update_diagnostics_profile_button()

        status_row_widget = QWidget()
        status_row_widget.setLayout(status_row)
        layout.addWidget(status_row_widget)

        self.button = QPushButton("Launch")
        self.button.setObjectName("LaunchButton")
        self.button.setEnabled(target.is_available())
        self.button.clicked.connect(self.request_launch)
        layout.addWidget(self.button)

        self.setStyleSheet(
            f"""
            QFrame#LaunchCard {{
                background-color: rgba(18, 24, 36, 230);
                border: 1px solid {theme.accent_soft};
                border-radius: 18px;
            }}
            QLabel#CardTitle {{
                color: #f5f8ff;
                font-size: 15px;
                font-weight: 700;
            }}
            QLabel#CardSubtitle {{
                color: #c6d0df;
                font-size: 9px;
            }}
            QLabel#CardAddress {{
                color: #92a0b3;
                font-size: 8px;
                font-family: Consolas, 'Courier New', monospace;
            }}
            QLabel#CardBadge {{
                min-width: 84px;
                padding: 5px 9px;
                border-radius: 999px;
                background-color: {theme.accent_soft};
                color: #f4f7ff;
                font-size: 10px;
                font-weight: 600;
            }}
            QLabel#CardNote {{
                color: #a6b2c4;
                font-size: 9px;
            }}
            QLabel#CardState {{
                color: #8fb3d9;
                font-size: 8px;
                font-weight: 600;
                padding-top: 0px;
            }}
            QLabel#ModeInlineLabel {{
                background: transparent;
                border: none;
                padding: 0px;
                font-size: 9px;
                font-weight: 700;
            }}
            QLabel#LaunchFlagsInlineLabel {{
                background: transparent;
                border: none;
                padding: 0px 0px 0px 4px;
                color: #8fb3d9;
                font-size: 8px;
                font-weight: 600;
            }}
            QPushButton#DiagnosticsInlineButton {{
                background-color: transparent;
                color: #d9e8ff;
                border: 1px solid #5f7088;
                border-radius: 10px;
                padding: 2px 8px;
                font-size: 9px;
                font-weight: 700;
            }}
            QPushButton#DiagnosticsInlineButton:checked {{
                background-color: #1f4d37;
                border-color: #2f8f5f;
                color: #d8ffe9;
            }}
            QPushButton#DiagnosticsInlineButton:hover:!disabled {{
                border-color: #8ca3bd;
                background-color: rgba(255, 255, 255, 0.04);
            }}
            QPushButton#LaunchButton {{
                background-color: {theme.accent};
                color: white;
                border: none;
                border-radius: 12px;
                padding: 7px 12px;
                font-size: 10px;
                font-weight: 700;
            }}
            QPushButton#LaunchButton[launchState="pending"] {{
                background-color: #b08924;
                color: #fff6cc;
            }}
            QPushButton#LaunchButton[launchState="pending"]:hover:!disabled {{
                background-color: #d39d2e;
            }}
            QPushButton#LaunchButton[launchState="closing"] {{
                background-color: #8c6b1f;
                color: #fff2c6;
            }}
            QPushButton#LaunchButton[launchState="closing"]:hover:!disabled {{
                background-color: #a88327;
            }}
            QPushButton#LaunchButton[autoLaunchPending="true"] {{
                background-color: #b08924;
                color: #fff6cc;
            }}
            QPushButton#LaunchButton[autoLaunchPending="true"]:hover:!disabled {{
                background-color: #d39d2e;
            }}
            QPushButton#LaunchButton:disabled {{
                background-color: #394556;
                color: #9aa7bb;
            }}
            QPushButton#LaunchButton:hover:!disabled {{
                background-color: #5a9af7;
            }}
            """
        )
        self._refresh_primary_button_state()
        self._update_mode_label()

    def request_launch(self) -> None:
        self.launch_callback(self.target)

    def mark_started(self) -> None:
        self.set_running(True)

    def _cycle_launch_profile(self) -> None:
        if self.profile_cycle_callback is not None:
            self.profile_cycle_callback()

    def _cycle_diagnostics_profile(self) -> None:
        if self.diagnostics_profile_cycle_callback is not None:
            self.diagnostics_profile_cycle_callback()

    def _update_profile_chip(self) -> None:
        self._update_mode_label()

    def _update_diagnostics_button(self) -> None:
        if self.target.key != "slspr_acq" or self.diagnostics_button is None or self.diagnostics_profile_text_callback is None:
            return
        profile_text = str(self.diagnostics_profile_text_callback() or "").strip() or "Normal"
        self.diagnostics_button.setText(f"Diagnostics: {profile_text}")
        if self.diagnostics_profile_tooltip_callback is not None:
            self.diagnostics_button.setToolTip(self.diagnostics_profile_tooltip_callback())

    def _update_diagnostics_profile_button(self) -> None:
        self._update_diagnostics_button()

    def _update_mode_label(self) -> None:
        if self.target.key != "slspr_acq" or self.mode_label is None or self.profile_text_callback is None:
            return
        raw_text = str(self.profile_text_callback() or "").strip() or "Full"
        profile_key = self.profile_key_callback() if self.profile_key_callback is not None else LAUNCH_PROFILE_FULL
        profile_key = normalize_launch_profile(profile_key)
        color = self._launch_profile_text_colors.get(profile_key, "#9FD7A6")
        self.mode_label.setText(
            f"Mode: <span style='color:#d9e8ff; font-weight:400;'>&lt;</span> "
            f"<span style='color:{color}; font-weight:800;'>{raw_text}</span> "
            f"<span style='color:#d9e8ff; font-weight:400;'>&gt;</span>"
        )
        if self.profile_tooltip_callback is not None:
            self.mode_label.setToolTip(self.profile_tooltip_callback())

    def _refresh_primary_button_state(self) -> None:
        self.button.blockSignals(True)
        try:
            if self._stopping:
                self.button.setText("Closing...")
                self.button.setProperty("launchState", "closing")
                self.button.setToolTip(f"Waiting for {self.target.title} to close.")
            elif self._running:
                self.button.setText("Kill")
                self.button.setProperty("launchState", "running")
                self.button.setToolTip(f"Stop {self.target.title}.")
            elif self._launch_pending:
                if self._launch_pending_seconds > 0:
                    self.button.setText(f"Stop launch ({self._launch_pending_seconds}s)")
                else:
                    self.button.setText("Stop launch")
                self.button.setProperty("launchState", "pending")
                self.button.setToolTip(f"Cancel the pending launch of {self.target.title}.")
            else:
                self.button.setText(self._launch_button_base_text)
                self.button.setProperty("launchState", "idle")
                self.button.setToolTip(f"Open {self.target.title}.")
        finally:
            self.button.blockSignals(False)
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)
        self.button.update()

    def set_auto_launch_pending(self, pending: bool, seconds: int | None = None) -> None:
        self._launch_pending = bool(pending)
        self._launch_pending_seconds = max(int(seconds), 0) if seconds is not None else 0
        self.button.setProperty("autoLaunchPending", bool(pending))
        self._refresh_primary_button_state()

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        if self._running:
            self._launch_pending = False
        self._stopping = False
        if running:
            self.state_label.setText("Status: running")
            self.state_label.setStyleSheet("color: #86efac; font-size: 10px; font-weight: 700; padding-top: 2px;")
        else:
            self.state_label.setText("Status: closed")
            self.state_label.setStyleSheet("color: #8fb3d9; font-size: 10px; font-weight: 600; padding-top: 2px;")
        self._refresh_primary_button_state()
        self._update_profile_chip()

    def set_stopping(self, stopping: bool) -> None:
        self._stopping = bool(stopping)
        if self._stopping:
            self._launch_pending = False
        if self._stopping:
            self.state_label.setText("Status: closing")
            self.state_label.setStyleSheet("color: #f0b45a; font-size: 10px; font-weight: 700; padding-top: 2px;")
        elif self._running:
            self.state_label.setText("Status: running")
            self.state_label.setStyleSheet("color: #86efac; font-size: 10px; font-weight: 700; padding-top: 2px;")
        else:
            self.state_label.setText("Status: closed")
            self.state_label.setStyleSheet("color: #8fb3d9; font-size: 10px; font-weight: 600; padding-top: 2px;")
        self._refresh_primary_button_state()
        self._update_profile_chip()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
        self._countdown_remaining = 0
        self._auto_launch_target: AppTarget | None = None
        self._diagnostics_profile = _settings_diagnostics_profile(self.settings)
        self.settings.setValue(SETTINGS_DIAGNOSTICS_PROFILE_KEY, self._diagnostics_profile)
        self.settings.sync()
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._tick_auto_launch_countdown)
        self._auto_launch_timer = QTimer(self)
        self._auto_launch_timer.setSingleShot(True)
        self._auto_launch_timer.timeout.connect(self._launch_pending_target)
        self._processes_by_key: dict[str, list[subprocess.Popen[object]]] = {}
        self._process_poll_timer = QTimer(self)
        self._process_poll_timer.setInterval(1000)
        self._process_poll_timer.timeout.connect(self._refresh_running_app_statuses)

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(900, 560)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        header = QFrame()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(6, 3, 6, 3)
        header_layout.setSpacing(2)
        title = QLabel("LSPR Suite")
        title.setObjectName("MainTitle")
        header_layout.addWidget(title)

        self.countdown_label = QLabel()
        self.countdown_label.setObjectName("CountdownText")
        self.countdown_label.setWordWrap(True)
        header_layout.addWidget(self.countdown_label)
        root_layout.addWidget(header)

        self.cards: dict[str, LaunchCard] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for index, target in enumerate(TARGETS):
            card = LaunchCard(
                target,
                CARD_THEMES[index % len(CARD_THEMES)],
                self.handle_launch_request,
                self._current_launch_profile_key if target.key == "slspr_acq" else None,
                self._cycle_launch_profile if target.key == "slspr_acq" else None,
                self._launch_profile_chip_text if target.key == "slspr_acq" else None,
                self._launch_profile_chip_tooltip if target.key == "slspr_acq" else None,
                self._cycle_diagnostics_profile if target.key == "slspr_acq" else None,
                self._diagnostics_profile_label if target.key == "slspr_acq" else None,
                self._diagnostics_profile_tooltip if target.key == "slspr_acq" else None,
            )
            self.cards[target.key] = card
            grid.addWidget(card, index // 2, index % 2)
        root_layout.addLayout(grid, 1)

        self.setCentralWidget(root)
        self.setStyleSheet(
            """
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                            stop:0 #0d1320, stop:1 #111a29);
            }
            QLabel#MainTitle {
                color: #ffffff;
                font-size: 22px;
                font-weight: 800;
                letter-spacing: 0.3px;
            }
            QLabel#CountdownText {
                color: #a6d4ff;
                font-size: 10px;
                padding-top: 1px;
            }
            """
        )

        self._schedule_auto_launch()
        self._refresh_running_app_statuses()
        self._process_poll_timer.start()

    def _get_last_target(self) -> AppTarget | None:
        key = self.settings.value(SETTINGS_LAST_TARGET_KEY, "", type=str) or ""
        target = TARGETS_BY_KEY.get(key)
        if target is None or not target.is_available():
            return None
        return target

    def _schedule_auto_launch(self) -> None:
        target = self._get_last_target()
        if target is None:
            self.countdown_label.setText("No recent app selected yet. Choose one to remember it for next time.")
            return

        self._auto_launch_target = target
        self._countdown_remaining = AUTO_LAUNCH_DELAY_MS // 1000
        self._update_auto_launch_card_state()
        self.countdown_label.setText(
            f"Last opened app remembered: {target.title}. Launching automatically in {self._countdown_remaining}s."
        )
        self._countdown_timer.start()
        self._auto_launch_timer.start(AUTO_LAUNCH_DELAY_MS)

    def _tick_auto_launch_countdown(self) -> None:
        if self._auto_launch_target is None:
            self._countdown_timer.stop()
            return
        self._countdown_remaining -= 1
        if self._countdown_remaining <= 0:
            self.countdown_label.setText(f"Launching {self._auto_launch_target.title}...")
            self._countdown_timer.stop()
            self._update_auto_launch_card_state()
            return
        self._update_auto_launch_card_state()
        self.countdown_label.setText(
            f"Last opened app remembered: {self._auto_launch_target.title}. "
            f"Launching automatically in {self._countdown_remaining}s."
        )

    def _clear_auto_launch(self) -> None:
        self._update_auto_launch_card_state(clear=True)
        self._auto_launch_timer.stop()
        self._countdown_timer.stop()
        self._auto_launch_target = None
        self._countdown_remaining = 0

    def _update_auto_launch_card_state(self, *, clear: bool = False) -> None:
        pending_key = None if clear else getattr(self._auto_launch_target, "key", None)
        for target in TARGETS:
            card = self.cards.get(target.key)
            if card is None:
                continue
            if pending_key is not None and target.key == pending_key and self._countdown_remaining > 0:
                card.set_auto_launch_pending(True, self._countdown_remaining)
            else:
                card.set_auto_launch_pending(False)

    def _remember_target(self, target: AppTarget) -> None:
        self.settings.setValue(SETTINGS_LAST_TARGET_KEY, target.key)
        self.settings.sync()

    def _current_launch_profile_key(self) -> str:
        key = self.settings.value(SETTINGS_LAUNCH_PROFILE_KEY, DEFAULT_LAUNCH_PROFILE, type=str) or DEFAULT_LAUNCH_PROFILE
        return normalize_launch_profile(key)

    def _current_launch_profile_spec(self):
        return launch_profile_spec(self._current_launch_profile_key())

    def _launch_profile_chip_text(self) -> str:
        return self._current_launch_profile_spec().label

    def _launch_profile_chip_tooltip(self) -> str:
        spec = self._current_launch_profile_spec()
        return f"{spec.description} Click to cycle to the next launch mode."

    def _cycle_launch_profile(self) -> None:
        specs = launch_profile_specs()
        keys = [spec.key for spec in specs]
        current_key = self._current_launch_profile_key()
        try:
            index = keys.index(current_key)
        except ValueError:
            index = 0
        next_key = keys[(index + 1) % len(keys)]
        self.settings.setValue(SETTINGS_LAUNCH_PROFILE_KEY, next_key)
        self.settings.sync()
        self._refresh_launch_profile_cards()

    def _refresh_launch_profile_cards(self) -> None:
        card = self.cards.get("slspr_acq")
        if card is not None:
            card._update_profile_chip()
            card._update_diagnostics_button()

    def _current_diagnostics_profile(self) -> str:
        return self._diagnostics_profile

    def _cycle_diagnostics_profile(self) -> None:
        self._diagnostics_profile = _next_diagnostics_profile(self._diagnostics_profile)
        self.settings.setValue(SETTINGS_DIAGNOSTICS_PROFILE_KEY, self._diagnostics_profile)
        self.settings.sync()
        self._refresh_launch_profile_cards()

    def _diagnostics_profile_label(self) -> str:
        return _diagnostics_profile_label(self._diagnostics_profile)

    def _diagnostics_profile_tooltip(self) -> str:
        return _diagnostics_profile_tooltip(self._diagnostics_profile)

    def _mark_started(self, target: AppTarget) -> None:
        card = self.cards.get(target.key)
        if card is not None:
            card.set_running(True)

    def handle_launch_request(self, target: AppTarget) -> None:
        pending_key = getattr(self._auto_launch_target, "key", None)
        if pending_key == target.key and self._countdown_remaining > 0:
            self._clear_auto_launch()
            return

        processes = self._processes_by_key.get(target.key, [])
        if any(process.poll() is None for process in processes):
            self._kill_target(target)
            return

        self._clear_auto_launch()
        self._remember_target(target)
        self._launch_target(target)

    def _launch_pending_target(self) -> None:
        target = self._auto_launch_target
        self._clear_auto_launch()
        if target is None:
            return
        self._remember_target(target)
        self._launch_target(target)

    def _launch_target(self, target: AppTarget) -> None:
        try:
            processes = self._processes_by_key.get(target.key, [])
            if any(process.poll() is None for process in processes):
                card = self.cards.get(target.key)
                if card is not None:
                    card.set_running(True)
                QMessageBox.information(
                    self,
                    "Already running",
                    f"{target.title} was already launched from this hub.",
                )
                return
            extra_env = None
            if target.key == "slspr_acq":
                diagnostics_profile = self._current_diagnostics_profile()
                diagnostics_export_enabled = diagnostics_profile == "deep"
                extra_env = {
                    LAUNCH_PROFILE_ENV_VAR: self._current_launch_profile_key(),
                    "LSPR_DIAGNOSTICS_PROFILE": diagnostics_profile,
                    "LSPR_QUIET_DIAGNOSTICS": "1" if diagnostics_profile == "off" else "0",
                    "LSPR_SUPPRESS_DIAGNOSTIC_INFO_LOGS": "1" if diagnostics_profile == "off" else "0",
                    "LSPR_DISABLE_DIAGNOSTIC_EXPORT": "0" if diagnostics_export_enabled else "1",
                }
                sys.stdout.write(
                    f"[suite_launcher] launching {target.title} | "
                    f"diagnostics={diagnostics_profile}\n"
                )
                sys.stdout.flush()
            command, cwd, env = target.build_command(extra_env=extra_env)
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self._processes_by_key.setdefault(target.key, []).append(process)
            self._forward_process_output(process, target.title)
            self._mark_started(target)
            self._refresh_running_app_statuses()
        except Exception as exc:
            QMessageBox.critical(self, "Launch failed", f"Could not open {target.title}:\n{exc}")
            return

    def _kill_target(self, target: AppTarget) -> None:
        processes = self._processes_by_key.get(target.key, [])
        alive = [process for process in processes if process.poll() is None]
        if not alive:
            card = self.cards.get(target.key)
            if card is not None:
                card.set_running(False)
            QMessageBox.information(self, "Not running", f"{target.title} does not have a running instance from this hub.")
            return

        card = self.cards.get(target.key)
        if card is not None:
            card.set_stopping(True)

        failed: list[int] = []
        forced: list[int] = []
        for process in alive:
            try:
                try:
                    close_sent = _post_close_message_to_process(process.pid)
                except Exception:
                    close_sent = False
                if close_sent:
                    try:
                        process.wait(timeout=6.0)
                    except subprocess.TimeoutExpired:
                        pass
                if process.poll() is None:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    try:
                        process.wait(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        pass
                if process.poll() is None:
                    forced.append(process.pid)
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        pass
                if process.poll() is None:
                    failed.append(process.pid)
            except Exception:
                failed.append(process.pid)
        self._refresh_running_app_statuses()
        if failed:
            if card is not None:
                card.set_running(False)
            QMessageBox.warning(
                self,
                "Kill request",
                f"Tried to stop {target.title}, but some processes could not be reached: {', '.join(str(pid) for pid in failed)}.",
            )
            return
        if forced:
            QMessageBox.information(
                self,
                "Stopped",
                f"Stopped running instance(s) of {target.title}. Some processes required a forced stop: {', '.join(str(pid) for pid in forced)}.",
            )
        else:
            QMessageBox.information(self, "Stopped", f"Stopped running instance(s) of {target.title}.")
        if card is not None:
            card.set_running(False)

    def _refresh_running_app_statuses(self) -> None:
        for target in TARGETS:
            processes = self._processes_by_key.get(target.key, [])
            alive = [process for process in processes if process.poll() is None]
            if alive:
                self._processes_by_key[target.key] = alive
            else:
                self._processes_by_key.pop(target.key, None)
            card = self.cards.get(target.key)
            if card is not None:
                card.set_running(bool(alive))

    def _forward_process_output(self, process: subprocess.Popen[object], title: str) -> None:
        stream = process.stdout
        if stream is None:
            return

        def _should_forward_line(line: str) -> bool:
            text = line.strip()
            if not text:
                return False
            match = _LOG_LEVEL_RE.match(text)
            if match is not None:
                return match.group("level") in {"WARNING", "ERROR", "CRITICAL"}
            match = _LOG_LEVEL_BRACKET_RE.match(text)
            if match is not None:
                return match.group("level") in {"WARNING", "ERROR", "CRITICAL"}
            return False

        def _pump_output() -> None:
            try:
                for line in iter(stream.readline, ""):
                    if not line:
                        break
                    if _should_forward_line(line):
                        sys.stdout.write(f"[{title}] {line}")
                        sys.stdout.flush()
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        threading.Thread(target=_pump_output, daemon=True).start()


def _apply_palette(app: QApplication) -> None:
    apply_base_app_theme(app, get_active_theme())


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(SETTINGS_ORGANIZATION)
    app.setOrganizationDomain("lspr.local")
    app.setStyle("Fusion")
    _apply_palette(app)
    app.setWindowIcon(app_icon())
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


TARGETS_BY_KEY = {target.key: target for target in TARGETS}
