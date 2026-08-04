from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import ctypes
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Callable

from platformdirs import user_config_dir

from PyQt6.QtCore import QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
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

from . import updater
from .targets import SUITE_ROOT, TARGETS, AppTarget
from .version import APP_NAME, APP_VERSION


@dataclass(frozen=True)
class CardTheme:
    accent: str
    accent_soft: str


class ClickableLabel(QLabel):
    def __init__(self, text: str = "", clicked_callback: Callable[[], None] | None = None, parent=None) -> None:
        super().__init__(text, parent)
        self._clicked_callback = clicked_callback
        # Set here (not just at the one current call site) so any future
        # instance with a real callback shows a pointing hand by default
        # instead of silently needing the caller to remember it.
        if clicked_callback is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.button() == Qt.MouseButton.LeftButton and self._clicked_callback is not None:
            self._clicked_callback()
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _console_write(text: str) -> None:
    """Write to stdout if one exists.

    The portable build is packaged with PyInstaller's --windowed flag, which
    means there is no console attached and sys.stdout/sys.stderr are None
    (not just discarded) - a bare sys.stdout.write() would raise
    AttributeError there.
    """
    stream = sys.stdout
    if stream is None:
        return
    try:
        stream.write(text)
        stream.flush()
    except Exception:
        pass


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

# Shared config directory used by the JSON-backed apps (acq, sLSPR eva) -
# see apps/sLSPR/acq/.../storage/app_config.py and apps/sLSPR/eva/.../processing.py.
SHARED_CONFIG_DIR = Path(user_config_dir("lspr-suite", appauthor=False))
SETTINGS_BACKUP_DIR = SHARED_CONFIG_DIR / "settings_backups"


@dataclass(frozen=True)
class SettingsTarget:
    """One app's persisted settings, as known to the launcher's reset/backup panel."""

    key: str
    title: str
    kind: str  # "file" (JSON on disk) or "qsettings" (Windows registry / QSettings store)
    path: Path | None = None
    org: str | None = None
    app: str | None = None

    def describe_state(self) -> str:
        if self.kind == "file":
            if self.path is not None and self.path.exists():
                size_kb = self.path.stat().st_size / 1024
                return f"{self.path.name} ({size_kb:.1f} KB)"
            return "No settings saved yet"
        keys = QSettings(self.org, self.app).allKeys()
        return f"{len(keys)} saved value(s)" if keys else "No settings saved yet"


def settings_targets() -> list[SettingsTarget]:
    """Every persisted-settings store the launcher knows how to back up / reset."""
    return [
        SettingsTarget(
            "slspr_acq", "singleLSPR Acquisition", "file",
            path=SHARED_CONFIG_DIR / "lspr_settings.json",
        ),
        SettingsTarget(
            "slspr_eva", "singleLSPR Evaluation", "file",
            path=SHARED_CONFIG_DIR / "lspr_evaluation_settings.json",
        ),
        SettingsTarget(
            "lspri_eva", "LSPRimaging Evaluation", "qsettings",
            org="LSPR", app="LSPRImaging",
        ),
        SettingsTarget(
            "suite_launcher", "Suite Launcher (this app)", "qsettings",
            org=SETTINGS_ORGANIZATION, app=SETTINGS_APPLICATION,
        ),
    ]


def backup_settings_target(target: SettingsTarget, backup_dir: Path) -> str:
    """Copy *target*'s settings into *backup_dir*. Returns a short human-readable result."""
    if target.kind == "file":
        if target.path is None or not target.path.exists():
            return "Nothing to back up (no settings file yet)."
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target.path, backup_dir / target.path.name)
        return f"Backed up {target.path.name}."
    values = {key: QSettings(target.org, target.app).value(key) for key in QSettings(target.org, target.app).allKeys()}
    if not values:
        return "Nothing to back up (no saved values)."
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"{target.key}_settings.json"
    dest.write_text(json.dumps(values, indent=2, default=str), encoding="utf-8")
    return f"Backed up {len(values)} value(s) to {dest.name}."


def reset_settings_target(target: SettingsTarget) -> str:
    """Clear *target*'s settings so the owning app falls back to defaults."""
    if target.kind == "file":
        if target.path is None or not target.path.exists():
            return "Already empty."
        target.path.unlink()
        return "Settings file removed."
    qsettings = QSettings(target.org, target.app)
    if not qsettings.allKeys():
        return "Already empty."
    qsettings.clear()
    qsettings.sync()
    return "Registry entries cleared."


class SettingsResetDialog(QDialog):
    """Lets the user back up or reset each app's saved settings.

    Settings corruption (a malformed JSON file, for instance) can make an app
    fail to start with no visible explanation. This panel gives a safe way
    out: back up what's there, or wipe it so the app regenerates defaults on
    next launch - with a confirmation prompt before anything destructive.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings — Backup & Reset")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        intro = QLabel(
            "Each app below stores its own settings (window layout, last-used values, "
            "processing options). If an app won't start or is behaving oddly, resetting "
            "its settings often fixes it. A backup is always made first."
        )
        intro.setObjectName("SettingsDialogIntro")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._status_label = QLabel("")
        self._status_label.setObjectName("SettingsDialogStatus")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._rows: dict[str, QLabel] = {}
        for target in settings_targets():
            row = QFrame()
            row.setObjectName("SettingsTargetRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 8, 10, 8)
            row_layout.setSpacing(8)

            text_col = QVBoxLayout()
            text_col.setSpacing(1)
            title_label = QLabel(target.title)
            title_label.setObjectName("SettingsTargetTitle")
            state_label = QLabel(target.describe_state())
            state_label.setObjectName("SettingsTargetState")
            text_col.addWidget(title_label)
            text_col.addWidget(state_label)
            self._rows[target.key] = state_label
            row_layout.addLayout(text_col, 1)

            backup_button = QPushButton("Backup")
            backup_button.setObjectName("SettingsRowButton")
            backup_button.setCursor(Qt.CursorShape.PointingHandCursor)
            backup_button.clicked.connect(lambda _checked=False, t=target: self._backup_one(t))
            row_layout.addWidget(backup_button)

            reset_button = QPushButton("Reset...")
            reset_button.setObjectName("SettingsRowResetButton")
            reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
            reset_button.clicked.connect(lambda _checked=False, t=target: self._reset_one(t))
            row_layout.addWidget(reset_button)

            layout.addWidget(row)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        backup_all_button = QPushButton("Backup all")
        backup_all_button.setObjectName("SettingsRowButton")
        backup_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        backup_all_button.clicked.connect(self._backup_all)
        button_row.addWidget(backup_all_button)

        reset_all_button = QPushButton("Reset all...")
        reset_all_button.setObjectName("SettingsRowResetButton")
        reset_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_all_button.clicked.connect(self._reset_all)
        button_row.addWidget(reset_all_button)

        button_row.addStretch(1)

        open_backups_button = QPushButton("Open backups folder")
        open_backups_button.setObjectName("SettingsRowButton")
        open_backups_button.setCursor(Qt.CursorShape.PointingHandCursor)
        open_backups_button.clicked.connect(self._open_backup_folder)
        button_row.addWidget(open_backups_button)

        close_button = QPushButton("Close")
        close_button.setObjectName("SettingsRowButton")
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.clicked.connect(self.accept)
        button_row.addWidget(close_button)

        layout.addLayout(button_row)

        self.setStyleSheet(
            """
            QDialog { background-color: #131a27; }
            QLabel#SettingsDialogIntro { color: #c6d0df; font-size: 11px; }
            QLabel#SettingsDialogStatus { color: #8fd6a6; font-size: 10px; font-weight: 600; min-height: 14px; }
            QFrame#SettingsTargetRow {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }
            QLabel#SettingsTargetTitle { color: #f5f8ff; font-size: 12px; font-weight: 700; }
            QLabel#SettingsTargetState { color: #92a0b3; font-size: 9px; }
            QPushButton#SettingsRowButton {
                background-color: transparent;
                color: #d9e8ff;
                border: 1px solid #5f7088;
                border-radius: 8px;
                padding: 4px 10px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton#SettingsRowButton:hover { border-color: #8ca3bd; background-color: rgba(255, 255, 255, 0.05); }
            QPushButton#SettingsRowResetButton {
                background-color: transparent;
                color: #ffb4b4;
                border: 1px solid #8c4646;
                border-radius: 8px;
                padding: 4px 10px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton#SettingsRowResetButton:hover { border-color: #d66d6d; background-color: rgba(255, 90, 90, 0.08); }
            """
        )

    def _timestamped_backup_dir(self) -> Path:
        return SETTINGS_BACKUP_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")

    def _refresh_row(self, target: SettingsTarget) -> None:
        label = self._rows.get(target.key)
        if label is not None:
            label.setText(target.describe_state())

    def _backup_one(self, target: SettingsTarget) -> None:
        backup_dir = self._timestamped_backup_dir()
        result = backup_settings_target(target, backup_dir)
        self._status_label.setText(f"{target.title}: {result}")

    def _backup_all(self) -> None:
        backup_dir = self._timestamped_backup_dir()
        results = [backup_settings_target(target, backup_dir) for target in settings_targets()]
        empty = sum(1 for result in results if result.startswith("Nothing to back up"))
        if empty:
            self._status_label.setText(f"Backed up all apps to {backup_dir} ({empty} had nothing to back up).")
        else:
            self._status_label.setText(f"Backed up all apps to {backup_dir}.")

    def _reset_one(self, target: SettingsTarget) -> None:
        confirmed = QMessageBox.question(
            self,
            "Reset settings?",
            (
                f"Reset settings for {target.title}?\n\n"
                "A backup will be saved first. The app will use default settings "
                "next time it starts. This cannot be undone from here."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        backup_settings_target(target, self._timestamped_backup_dir())
        result = reset_settings_target(target)
        self._refresh_row(target)
        self._status_label.setText(f"{target.title}: {result} (backup saved).")

    def _reset_all(self) -> None:
        confirmed = QMessageBox.question(
            self,
            "Reset all settings?",
            (
                "Reset settings for every app in the suite?\n\n"
                "A backup will be saved first for each app. All apps will use "
                "default settings next time they start. This cannot be undone from here."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        backup_dir = self._timestamped_backup_dir()
        for target in settings_targets():
            backup_settings_target(target, backup_dir)
            reset_settings_target(target)
            self._refresh_row(target)
        self._status_label.setText(f"All apps reset to defaults (backup saved to {backup_dir}).")

    def _open_backup_folder(self) -> None:
        SETTINGS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(SETTINGS_BACKUP_DIR))  # noqa: S606 - opening our own backup folder in Explorer


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

        local_version = target.resolve_local_version()
        self.version_label = QLabel(f"v{local_version}" if local_version else "")
        self.version_label.setObjectName("CardVersion")
        self.version_label.setWordWrap(True)
        self.version_label.setVisible(bool(local_version))
        header_col.addWidget(self.version_label)
        self._local_version = local_version

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
            QLabel#CardVersion {{
                color: #92a0b3;
                font-size: 8px;
                font-weight: 600;
            }}
            QLabel#CardVersion[updateAvailable="true"] {{
                color: #F39C12;
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

    def set_release_status(self, info: "updater.AppReleaseInfo | None", error: str | None) -> None:
        """Update the version label once the background release check for this app returns.

        `info` is None on error (network failure, no release published yet, etc.) -
        in that case we just keep showing the local version with no comparison.
        """
        if not self._local_version:
            return
        if error is not None or info is None:
            self.version_label.setText(f"v{self._local_version}")
            self.version_label.setToolTip(f"Could not check for updates: {error}" if error else "")
            self.version_label.setProperty("updateAvailable", False)
        elif updater.is_newer(self._local_version, info.tag):
            release_date = updater.format_release_date(info.published_at)
            date_part = f", released {release_date}" if release_date else ""
            self.version_label.setText(f"v{self._local_version} → {info.tag} available{date_part}")
            self.version_label.setToolTip(info.notes or "")
            self.version_label.setProperty("updateAvailable", True)
        else:
            self.version_label.setText(f"v{self._local_version} (up to date)")
            self.version_label.setToolTip("")
            self.version_label.setProperty("updateAvailable", False)
        self.version_label.style().unpolish(self.version_label)
        self.version_label.style().polish(self.version_label)


class MainWindow(QMainWindow):
    launch_progress_changed = pyqtSignal(str, int, str)
    update_check_finished = pyqtSignal(object, object)
    update_download_progress = pyqtSignal(int)
    update_download_finished = pyqtSignal(object, object)
    app_release_check_finished = pyqtSignal(str, object, object)

    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
        self._countdown_remaining = 0
        self._countdown_elapsed_ms = 0
        self._auto_launch_target: AppTarget | None = None
        self._launch_progress_target_key: str | None = None
        self._launch_progress_percent = 0
        self._launch_progress_stage = "Idle"
        self._launch_progress_detail = "Ready."
        self._diagnostics_profile = _settings_diagnostics_profile(self.settings)
        self.settings.setValue(SETTINGS_DIAGNOSTICS_PROFILE_KEY, self._diagnostics_profile)
        self.settings.sync()
        self.launch_progress_changed.connect(self._handle_launch_progress_changed)
        self.update_check_finished.connect(self._handle_update_check_finished)
        self.update_download_progress.connect(self._handle_update_download_progress)
        self.update_download_finished.connect(self._handle_update_download_finished)
        self.app_release_check_finished.connect(self._handle_app_release_check_finished)
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(250)
        self._countdown_timer.timeout.connect(self._tick_auto_launch_countdown)
        self._auto_launch_timer = QTimer(self)
        self._auto_launch_timer.setSingleShot(True)
        self._auto_launch_timer.timeout.connect(self._launch_pending_target)
        self._processes_by_key: dict[str, list[subprocess.Popen[object]]] = {}
        # Populated on each launch, drained/cleared in _handle_process_exit once
        # that launch's exit is reported (or the process outlives the "just
        # crashed" window) - see _refresh_running_app_statuses.
        self._process_launch_started_at: dict[str, float] = {}
        self._process_output_tail: dict[str, deque[str]] = {}
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

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title = QLabel("LSPR Suite")
        title.setObjectName("MainTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.update_button = QPushButton("Check for Updates")
        self.update_button.setObjectName("SettingsHeaderButton")
        self.update_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_button.setToolTip(
            "Check GitHub for a newer LSPR Suite release, and for newer versions of each app "
            "(shown on its card)."
        )
        self.update_button.clicked.connect(self._check_for_updates)
        title_row.addWidget(self.update_button, 0, Qt.AlignmentFlag.AlignRight)
        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("SettingsHeaderButton")
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.setToolTip("Back up or reset saved settings for the suite's apps.")
        self.settings_button.clicked.connect(self._open_settings_dialog)
        title_row.addWidget(self.settings_button, 0, Qt.AlignmentFlag.AlignRight)
        header_layout.addLayout(title_row)

        self.countdown_label = QLabel()
        self.countdown_label.setObjectName("CountdownText")
        self.countdown_label.setWordWrap(True)
        header_layout.addWidget(self.countdown_label)

        self.launch_progress_bar = QProgressBar()
        self.launch_progress_bar.setObjectName("LaunchProgressBar")
        self.launch_progress_bar.setRange(0, 100)
        self.launch_progress_bar.setValue(0)
        self.launch_progress_bar.setTextVisible(False)
        self.launch_progress_bar.setFixedHeight(8)
        self.launch_progress_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header_layout.addWidget(self.launch_progress_bar)

        self.launch_stage_label = QLabel("Idle")
        self.launch_stage_label.setObjectName("LaunchStageText")
        self.launch_stage_label.setWordWrap(True)
        header_layout.addWidget(self.launch_stage_label)
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
            QProgressBar#LaunchProgressBar {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 4px;
            }
            QProgressBar#LaunchProgressBar::chunk {
                border-radius: 4px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                            stop:0 #4C8BF5, stop:0.55 #3AAFA9, stop:1 #F39C12);
            }
            QLabel#LaunchStageText {
                color: #d7e7ff;
                font-size: 9px;
                font-weight: 600;
                padding-top: 1px;
            }
            QPushButton#SettingsHeaderButton {
                background-color: transparent;
                color: #d9e8ff;
                border: 1px solid #5f7088;
                border-radius: 10px;
                padding: 4px 10px;
                font-size: 10px;
                font-weight: 700;
            }
            QPushButton#SettingsHeaderButton:hover {
                border-color: #8ca3bd;
                background-color: rgba(255, 255, 255, 0.05);
            }
            """
        )

        self._schedule_auto_launch()
        self._refresh_running_app_statuses()
        self._process_poll_timer.start()

    def _open_settings_dialog(self) -> None:
        dialog = SettingsResetDialog(self)
        dialog.exec()

    def _check_for_updates(self) -> None:
        self.update_button.setEnabled(False)
        self.update_button.setText("Checking...")

        def _worker() -> None:
            try:
                info = updater.fetch_latest_release()
            except Exception as exc:
                self.update_check_finished.emit(None, str(exc))
                return
            self.update_check_finished.emit(info, None)

        threading.Thread(target=_worker, daemon=True).start()
        self._check_app_updates()

    def _check_app_updates(self) -> None:
        """Check each app's own repo for a newer tagged release than what's pinned here.

        Runs alongside the Suite bundle check, but reports back per-card instead of a
        dialog - each card's version label updates itself when its check returns.
        """
        for key, card in self.cards.items():
            repo = card.target.github_repo
            if not repo:
                continue

            def _worker(key: str = key, repo: str = repo) -> None:
                try:
                    info = updater.fetch_latest_app_release(repo)
                except Exception as exc:
                    self.app_release_check_finished.emit(key, None, str(exc))
                    return
                self.app_release_check_finished.emit(key, info, None)

            threading.Thread(target=_worker, daemon=True).start()

    def _handle_app_release_check_finished(self, key: object, info: object, error: object) -> None:
        card = self.cards.get(str(key))
        if card is None:
            return
        card.set_release_status(info, error)  # type: ignore[arg-type]

    def _handle_update_check_finished(self, info: object, error: object) -> None:
        self.update_button.setEnabled(True)
        self.update_button.setText("Check for Updates")
        if error is not None:
            QMessageBox.warning(
                self,
                "Couldn't check for updates",
                f"Could not reach GitHub to check for updates:\n{error}",
            )
            return

        release: updater.ReleaseInfo = info  # type: ignore[assignment]
        if not updater.is_newer(APP_VERSION, release.tag):
            QMessageBox.information(
                self,
                "Up to date",
                f"You're running the latest version ({APP_VERSION}).",
            )
            return

        if not getattr(sys, "frozen", False):
            QMessageBox.information(
                self,
                "Update available",
                (
                    f"Version {release.tag} is available (you have {APP_VERSION}).\n\n"
                    "This is a development checkout running from source, so automatic "
                    "update doesn't apply here - use 'git pull' to get the new code.\n\n"
                    f"Release notes:\n{release.notes or '(none provided)'}"
                ),
            )
            return

        confirmed = QMessageBox.question(
            self,
            "Update available",
            (
                f"Version {release.tag} is available (you have {APP_VERSION}).\n\n"
                f"Release notes:\n{release.notes or '(none provided)'}\n\n"
                "Download and install it now? LSPR Suite Launcher will close and "
                "reopen automatically once the update is applied."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self._start_update_download(release)

    def _start_update_download(self, release: "updater.ReleaseInfo") -> None:
        self.update_button.setEnabled(False)
        self._set_launch_progress(None, 0, "Downloading update", f"Downloading {release.tag}...")
        dest_path = Path(tempfile.gettempdir()) / "lspr_suite_updates" / release.asset_name

        def _worker() -> None:
            try:
                updater.download_release(
                    release,
                    dest_path,
                    on_progress=lambda percent: self.update_download_progress.emit(percent),
                )
            except Exception as exc:
                self.update_download_finished.emit(None, str(exc))
                return
            self.update_download_finished.emit(dest_path, None)

        threading.Thread(target=_worker, daemon=True).start()

    def _handle_update_download_progress(self, percent: int) -> None:
        self._set_launch_progress(None, percent, "Downloading update", f"Downloading update... {percent}%")

    def _handle_update_download_finished(self, zip_path: object, error: object) -> None:
        self.update_button.setEnabled(True)
        if error is not None:
            self._set_launch_progress(None, 0, "Idle", "Ready.")
            QMessageBox.warning(self, "Update download failed", f"Could not download the update:\n{error}")
            return
        self._set_launch_progress(None, 100, "Update ready", "Applying update...")
        self._launch_updater_and_quit(Path(str(zip_path)))

    def _launch_updater_and_quit(self, zip_path: Path) -> None:
        updater_exe = SUITE_ROOT.parent / "Updater.exe"
        if not updater_exe.exists():
            QMessageBox.critical(
                self,
                "Updater missing",
                f"Could not find Updater.exe at {updater_exe}. The update was downloaded "
                "but cannot be applied automatically.",
            )
            return
        try:
            subprocess.Popen(
                [
                    str(updater_exe),
                    "--wait-pid", str(os.getpid()),
                    "--zip", str(zip_path),
                    "--target", str(SUITE_ROOT),
                    "--relaunch", str(Path(sys.executable)),
                ]
            )
        except Exception as exc:
            QMessageBox.critical(self, "Update failed", f"Could not start the updater:\n{exc}")
            return
        QApplication.quit()

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
            self._set_launch_progress(None, 0, "Idle", "Waiting for a remembered app.")
            return

        self._auto_launch_target = target
        self._countdown_remaining = AUTO_LAUNCH_DELAY_MS // 1000
        self._countdown_elapsed_ms = 0
        self._update_auto_launch_card_state()
        self.countdown_label.setText(
            f"Last opened app remembered: {target.title}. Launching automatically in {self._countdown_remaining}s."
        )
        self._set_launch_progress(
            target.key,
            8,
            "Queued",
            f"Remembered app selected: {target.title}. Preparing automatic launch.",
        )
        self._countdown_timer.start()
        self._auto_launch_timer.start(AUTO_LAUNCH_DELAY_MS)

    def _tick_auto_launch_countdown(self) -> None:
        if self._auto_launch_target is None:
            self._countdown_timer.stop()
            return
        self._countdown_elapsed_ms += self._countdown_timer.interval()
        remaining_ms = max(AUTO_LAUNCH_DELAY_MS - self._countdown_elapsed_ms, 0)
        self._countdown_remaining = int((remaining_ms + 999) // 1000)
        progress = int(round(8 + ((AUTO_LAUNCH_DELAY_MS - remaining_ms) / max(AUTO_LAUNCH_DELAY_MS, 1)) * 12))
        self._set_launch_progress(
            self._auto_launch_target.key,
            progress,
            "Queued",
            f"Auto-launch in {max(self._countdown_remaining, 0)}s for {self._auto_launch_target.title}.",
        )
        if remaining_ms <= 0:
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
        self._countdown_elapsed_ms = 0
        if self._launch_progress_stage == "Queued":
            self._set_launch_progress(None, 0, "Idle", "Ready.")

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
        self._set_launch_progress(target.key, 100, "Running", f"{target.title} is running.")

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
            self._set_launch_progress(target.key, 22, "Preparing", f"Preparing launch for {target.title}.")
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
                self._set_launch_progress(
                    target.key,
                    35,
                    "Configuring",
                    f"Configuring diagnostics and launch profile for {target.title}.",
                )
                extra_env = {
                    LAUNCH_PROFILE_ENV_VAR: self._current_launch_profile_key(),
                    "LSPR_DIAGNOSTICS_PROFILE": diagnostics_profile,
                    "LSPR_QUIET_DIAGNOSTICS": "1" if diagnostics_profile == "off" else "0",
                    "LSPR_SUPPRESS_DIAGNOSTIC_INFO_LOGS": "1" if diagnostics_profile == "off" else "0",
                    "LSPR_DISABLE_DIAGNOSTIC_EXPORT": "0" if diagnostics_export_enabled else "1",
                    "TOP_CONTENT_TRACE": os.environ.get("TOP_CONTENT_TRACE", "1" if diagnostics_profile == "deep" else "0"),
                }
                _console_write(
                    f"[suite_launcher] launching {target.title} | "
                    f"diagnostics={diagnostics_profile} | "
                    f"top_content_trace={extra_env['TOP_CONTENT_TRACE']}\n"
                )
            self._set_launch_progress(target.key, 45, "Spawning", f"Starting {target.title} process.")
            command, cwd, env = target.build_command(extra_env=extra_env)
            self._set_launch_progress(target.key, 58, "Spawning", f"Launching from {cwd}.")
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
            self._process_launch_started_at[target.key] = perf_counter()
            self._process_output_tail[target.key] = deque(maxlen=200)
            self._set_launch_progress(target.key, 70, "Running", f"{target.title} is starting up.")
            self._forward_process_output(process, target)
            self._mark_started(target)
            self._refresh_running_app_statuses()
        except Exception as exc:
            self._set_launch_progress(None, 0, "Failed", f"Launch failed: {exc}")
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
            dead = [process for process in processes if process not in alive]
            if alive:
                self._processes_by_key[target.key] = alive
            else:
                self._processes_by_key.pop(target.key, None)
            for process in dead:
                self._handle_process_exit(target, process)
            card = self.cards.get(target.key)
            if card is not None:
                card.set_running(bool(alive))

    def _handle_process_exit(self, target: AppTarget, process: subprocess.Popen[object]) -> None:
        """Surface an app that exited on its own shortly after this hub launched
        it - e.g. the bundled interpreter itself failing to start. Without this,
        _refresh_running_app_statuses silently flips the card back to "Launch"
        with no indication anything went wrong, indistinguishable from the user
        closing the app normally."""
        started_at = self._process_launch_started_at.pop(target.key, None)
        tail = list(self._process_output_tail.pop(target.key, ()))
        if started_at is None:
            return
        returncode = process.returncode
        if returncode in (0, None):
            return
        elapsed_s = perf_counter() - started_at
        detail = "\n".join(tail[-40:]) if tail else "(no output captured)"
        QMessageBox.critical(
            self,
            "App exited unexpectedly",
            f"{target.title} exited {elapsed_s:.1f}s after launch with exit code {returncode}.\n\n"
            f"Last output:\n{detail}",
        )

    def _forward_process_output(self, process: subprocess.Popen[object], target: AppTarget) -> None:
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

        def _maybe_emit_progress(text: str) -> None:
            key = target.key
            if key != "slspr_acq":
                return
            progress_map: list[tuple[str, int, str]] = [
                ("Constructing main window...", 78, "Constructing main window"),
                ("Main window constructed.", 84, "Main window constructed"),
                ("startup wiring complete", 90, "Startup wiring complete"),
                ("first showEvent reached", 94, "Main window shown"),
                ("first data render on trace", 97, "First sensorgram data rendered"),
                ("first data render on spectrum", 100, "First spectrum data rendered"),
                ("Ready | source=", 88, "Application ready"),
            ]
            lowered = text.casefold()
            for needle, percent, stage in progress_map:
                if needle.casefold() in lowered:
                    self.launch_progress_changed.emit(
                        key,
                        percent,
                        stage,
                    )
                    return

        def _pump_output() -> None:
            try:
                for line in iter(stream.readline, ""):
                    if not line:
                        break
                    text = line.strip()
                    if text:
                        _maybe_emit_progress(text)
                        tail = self._process_output_tail.get(target.key)
                        if tail is not None:
                            tail.append(text)
                    if _should_forward_line(line):
                        _console_write(f"[{target.title}] {line}")
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        threading.Thread(target=_pump_output, daemon=True).start()

    def _set_launch_progress(self, target_key: str | None, percent: int, stage: str, detail: str) -> None:
        self._launch_progress_target_key = target_key
        self._launch_progress_percent = max(0, min(int(percent), 100))
        self._launch_progress_stage = str(stage).strip() or "Working"
        self._launch_progress_detail = str(detail).strip() or "Working..."
        self.launch_progress_bar.setValue(self._launch_progress_percent)
        self.launch_stage_label.setText(f"{self._launch_progress_stage}: {self._launch_progress_detail}")
        if target_key is None:
            self.launch_progress_bar.setFormat("")
        else:
            self.launch_progress_bar.setFormat(f"{self._launch_progress_percent}%")
        self.launch_progress_bar.setVisible(True)

    def _handle_launch_progress_changed(self, target_key: str, percent: int, stage: str) -> None:
        if self._launch_progress_target_key is not None and target_key != self._launch_progress_target_key:
            return
        target = TARGETS_BY_KEY.get(target_key)
        detail = stage
        if target is not None:
            detail = f"{target.title}: {stage}"
        self._set_launch_progress(target_key, percent, stage, detail)


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
