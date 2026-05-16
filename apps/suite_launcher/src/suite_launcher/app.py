from __future__ import annotations

import subprocess
import sys
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

from .targets import TARGETS, AppTarget
from .version import APP_NAME, APP_VERSION


@dataclass(frozen=True)
class CardTheme:
    accent: str
    accent_soft: str


CARD_THEMES = [
    CardTheme("#4C8BF5", "#183563"),
    CardTheme("#3AAFA9", "#163B3B"),
    CardTheme("#F39C12", "#4A2E0A"),
    CardTheme("#8E6CFF", "#2E215A"),
]

AUTO_LAUNCH_DELAY_MS = 3000
SETTINGS_LAST_TARGET_KEY = "lastTargetKey"
SETTINGS_ORGANIZATION = "LSPR Suite"
SETTINGS_APPLICATION = "Suite Launcher"


class LaunchCard(QFrame):
    def __init__(
        self,
        target: AppTarget,
        theme: CardTheme,
        launch_callback: Callable[[AppTarget], None],
        kill_callback: Callable[[AppTarget], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.target = target
        self.theme = theme
        self.launch_callback = launch_callback
        self.kill_callback = kill_callback
        self._launch_button_base_text = "Launch"
        self._kill_button_base_text = "Kill"
        self.setObjectName("LaunchCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(180)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        title = QLabel(target.title)
        title.setObjectName("CardTitle")
        subtitle = QLabel(target.subtitle)
        subtitle.setObjectName("CardSubtitle")
        subtitle.setWordWrap(True)

        badge = QLabel("Available" if target.is_available() else "Coming soon")
        badge.setObjectName("CardBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header_col = QVBoxLayout()
        header_col.setSpacing(4)
        header_col.addWidget(title)
        header_col.addWidget(subtitle)
        address = QLabel(target.address)
        address.setObjectName("CardAddress")
        address.setWordWrap(True)
        header_col.addWidget(address)
        top_row.addLayout(header_col, 1)
        top_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top_row)

        note = QLabel(target.note or "")
        note.setObjectName("CardNote")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.state_label = QLabel("Status: closed")
        self.state_label.setObjectName("CardState")
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)
        layout.addStretch(1)

        self.button = QPushButton("Launch")
        self.button.setObjectName("LaunchButton")
        self.button.setEnabled(target.is_available())
        self.button.clicked.connect(self.request_launch)
        layout.addWidget(self.button)

        self.kill_button = QPushButton("Kill")
        self.kill_button.setObjectName("KillButton")
        self.kill_button.setEnabled(False)
        self.kill_button.clicked.connect(self.request_kill)
        layout.addWidget(self.kill_button)

        self.setStyleSheet(
            f"""
            QFrame#LaunchCard {{
                background-color: rgba(18, 24, 36, 230);
                border: 1px solid {theme.accent_soft};
                border-radius: 18px;
            }}
            QLabel#CardTitle {{
                color: #f5f8ff;
                font-size: 18px;
                font-weight: 700;
            }}
            QLabel#CardSubtitle {{
                color: #c6d0df;
                font-size: 11px;
            }}
            QLabel#CardAddress {{
                color: #92a0b3;
                font-size: 10px;
                font-family: Consolas, 'Courier New', monospace;
            }}
            QLabel#CardBadge {{
                min-width: 92px;
                padding: 6px 10px;
                border-radius: 999px;
                background-color: {theme.accent_soft};
                color: #f4f7ff;
                font-size: 11px;
                font-weight: 600;
            }}
            QLabel#CardNote {{
                color: #a6b2c4;
                font-size: 11px;
            }}
            QLabel#CardState {{
                color: #8fb3d9;
                font-size: 10px;
                font-weight: 600;
                padding-top: 2px;
            }}
            QPushButton#LaunchButton {{
                background-color: {theme.accent};
                color: white;
                border: none;
                border-radius: 12px;
                padding: 10px 14px;
                font-size: 12px;
                font-weight: 700;
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
            QPushButton#KillButton {{
                background-color: #3a4454;
                color: #d8e0eb;
                border: none;
                border-radius: 12px;
                padding: 10px 14px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#KillButton[activeRun="true"] {{
                background-color: #8a2f2f;
                color: #ffe0e0;
            }}
            QPushButton#KillButton[activeRun="true"]:hover:!disabled {{
                background-color: #a63a3a;
            }}
            QPushButton#KillButton:disabled {{
                background-color: #2c3440;
                color: #6f7b89;
            }}
            """
        )

    def request_launch(self) -> None:
        self.launch_callback(self.target)

    def request_kill(self) -> None:
        self.kill_callback(self.target)

    def mark_started(self) -> None:
        self.set_running(True)

    def set_auto_launch_pending(self, pending: bool, seconds: int | None = None) -> None:
        self.button.setProperty("autoLaunchPending", bool(pending))
        if pending:
            if seconds is None:
                self.button.setText("Launching soon")
            else:
                self.button.setText(f"Launch in {max(int(seconds), 0)}s")
            self.button.setToolTip(f"{self.target.title} will open automatically soon.")
        else:
            self.button.setText(self._launch_button_base_text)
            self.button.setToolTip(f"Open {self.target.title}.")
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)
        self.button.update()

    def set_running(self, running: bool) -> None:
        if running:
            self.state_label.setText("Status: running")
            self.state_label.setStyleSheet("color: #86efac; font-size: 10px; font-weight: 700; padding-top: 2px;")
            self.kill_button.setProperty("activeRun", True)
            self.kill_button.setEnabled(True)
        else:
            self.state_label.setText("Status: closed")
            self.state_label.setStyleSheet("color: #8fb3d9; font-size: 10px; font-weight: 600; padding-top: 2px;")
            self.kill_button.setProperty("activeRun", False)
            self.kill_button.setEnabled(False)
        self.kill_button.style().unpolish(self.kill_button)
        self.kill_button.style().polish(self.kill_button)
        self.kill_button.update()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
        self._countdown_remaining = 0
        self._auto_launch_target: AppTarget | None = None
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
        self.setMinimumSize(1120, 720)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(28, 28, 28, 28)
        root_layout.setSpacing(20)

        header = QFrame()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(8, 8, 8, 8)
        header_layout.setSpacing(10)
        title = QLabel("LSPR Suite")
        title.setObjectName("MainTitle")
        subtitle = QLabel("Choose the acquisition or evaluation workspace you want to open.")
        subtitle.setObjectName("MainSubtitle")
        subtitle.setWordWrap(True)
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)

        self.hub_label = QLabel("Hub mode: the launcher stays open so you can start multiple apps.")
        self.hub_label.setObjectName("HubModeText")
        self.hub_label.setWordWrap(True)
        header_layout.addWidget(self.hub_label)

        self.countdown_label = QLabel()
        self.countdown_label.setObjectName("CountdownText")
        self.countdown_label.setWordWrap(True)
        header_layout.addWidget(self.countdown_label)
        root_layout.addWidget(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(18)
        self.cards: dict[str, LaunchCard] = {}
        for index, target in enumerate(TARGETS):
            card = LaunchCard(target, CARD_THEMES[index % len(CARD_THEMES)], self.handle_launch_request, self.handle_kill_request)
            self.cards[target.key] = card
            grid.addWidget(card, index // 2, index % 2)
        root_layout.addLayout(grid)

        footer = QLabel(
            "Shared logic lives in `packages/lspr_core` and `packages/lspr_io`. "
            "This launcher prefers the suite copies when they exist."
        )
        footer.setObjectName("FooterText")
        footer.setWordWrap(True)
        root_layout.addWidget(footer)

        self.setCentralWidget(root)
        self.setStyleSheet(
            """
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                            stop:0 #0d1320, stop:1 #111a29);
            }
            QLabel#MainTitle {
                color: #ffffff;
                font-size: 34px;
                font-weight: 800;
                letter-spacing: 1px;
            }
            QLabel#MainSubtitle {
                color: #c0ccdb;
                font-size: 13px;
            }
            QLabel#HubModeText {
                color: #d9e8ff;
                font-size: 12px;
                font-weight: 600;
                padding-top: 2px;
            }
            QLabel#CountdownText {
                color: #a6d4ff;
                font-size: 12px;
                padding-top: 4px;
            }
            QLabel#FooterText {
                color: #8fa0b8;
                font-size: 11px;
                padding-top: 2px;
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

    def _mark_started(self, target: AppTarget) -> None:
        card = self.cards.get(target.key)
        if card is not None:
            card.set_running(True)

    def handle_launch_request(self, target: AppTarget) -> None:
        self._clear_auto_launch()
        self._remember_target(target)
        self._launch_target(target)

    def handle_kill_request(self, target: AppTarget) -> None:
        self._clear_auto_launch()
        self._kill_target(target)

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
            command, cwd, env = target.build_command()
            process = subprocess.Popen(command, cwd=str(cwd), env=env)
            self._processes_by_key.setdefault(target.key, []).append(process)
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

        failed: list[int] = []
        for process in alive:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except Exception:
                failed.append(process.pid)
        self._refresh_running_app_statuses()
        if failed:
            QMessageBox.warning(
                self,
                "Kill request",
                f"Tried to stop {target.title}, but some processes could not be reached: {', '.join(str(pid) for pid in failed)}.",
            )
            return
        QMessageBox.information(self, "Stopped", f"Stopped running instance(s) of {target.title}.")

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
