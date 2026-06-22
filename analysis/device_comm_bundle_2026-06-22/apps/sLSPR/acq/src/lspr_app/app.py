from __future__ import annotations

import logging
import ctypes
import os
import socket
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from time import perf_counter

from PyQt6.QtCore import QEvent, QObject, QEasingCurve, QLockFile, QPropertyAnimation, QStandardPaths, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QStyleOptionProgressBar,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import app_icon, apply_base_app_theme
from lspr_app.resources import app_icon_path
from lspr_app.storage.app_config import load_app_setting, save_app_setting

from lspr_app import __version__
from lspr_app.diagnostics import DiagnosticsConfig, apply_diagnostic_info_filter
from lspr_app.gui.main_window_logging import build_recording_experiment_log_path
from lspr_core import (
    DEFAULT_LAUNCH_PROFILE,
    LAUNCH_PROFILE_ENV_VAR,
    launch_profile_spec,
    normalize_launch_profile,
)


def _brand_logo_path() -> Path:
    return app_icon_path()


def _lock_file_path() -> Path:
    app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    base = Path(app_data) if app_data else (Path.cwd() / ".appdata")
    base.mkdir(parents=True, exist_ok=True)
    return base / "lspr_acquisition.lock"


def _process_is_running(pid: int) -> bool:
    if pid <= 0 or os.name != "nt":
        return False

    kernel32 = ctypes.windll.kernel32
    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    handle = kernel32.OpenProcess(process_query_limited_information | synchronize, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True

    return kernel32.GetLastError() == 5


def _recover_stale_lock(lock: QLockFile) -> bool:
    found, pid, host, _app_name = lock.getLockInfo()
    if not found or pid <= 0:
        return False
    if host and host.lower() != socket.gethostname().lower():
        return False
    if _process_is_running(pid):
        return False

    try:
        lock.removeStaleLockFile()
    except Exception:
        pass

    if lock.tryLock(0):
        return True

    try:
        Path(lock.fileName()).unlink(missing_ok=True)
    except OSError:
        pass

    return lock.tryLock(0)


class StartupSuspiciousWidgetTracer(QObject):
    _IGNORED_WIDGET_CLASSES = {"MainWindow", "StartupSplash", "QMenu", "ViewBoxMenu"}
    _SUSPICIOUS_WIDGET_CLASSES = {"QWidget", "QFrame", "QPushButton", "QToolButton", "QComboBox", "QLabel", "QScrollArea"}

    def __init__(self, logger: logging.Logger, enabled: bool) -> None:
        super().__init__()
        self._logger = logger
        self._enabled = bool(enabled)

    def eventFilter(self, obj, event) -> bool:  # pragma: no cover - GUI runtime path
        if not self._enabled or not isinstance(obj, QWidget):
            return False
        if not obj.isWindow():
            return False

        event_type = event.type()
        if event_type not in {
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.Close,
            QEvent.Type.ShowToParent,
            QEvent.Type.Polish,
        }:
            return False

        if not self._is_suspicious_widget(obj):
            return False

        self._log_widget(obj, event_type)
        return False

    def _is_suspicious_widget(self, widget: QWidget) -> bool:
        class_name = widget.__class__.__name__
        if class_name in self._IGNORED_WIDGET_CLASSES:
            return False
        if class_name not in self._SUSPICIOUS_WIDGET_CLASSES:
            return False
        try:
            object_name = widget.objectName().strip()
        except Exception:
            object_name = ""
        try:
            title = widget.windowTitle().strip()
        except Exception:
            title = ""
        try:
            parent = widget.parentWidget()
        except Exception:
            parent = None

        geometry_suspicious = False
        try:
            geo = widget.geometry()
            geometry_suspicious = (geo.width(), geo.height()) in {
                (640, 480),
                (100, 30),
                (34, 34),
            }
        except Exception:
            geometry_suspicious = False

        return (
            parent is None
            or not object_name
            or object_name.startswith("qt_")
            or not title
            or geometry_suspicious
        )

    def _format_parent_info(self, widget: QWidget) -> tuple[str, str]:
        try:
            parent = widget.parentWidget()
        except Exception:
            parent = None
        if parent is None:
            return "-", "-"

        try:
            parent_text = (
                f"{parent.__class__.__name__}(objectName={parent.objectName()!r}, "
                f"title={parent.windowTitle()!r}, visible={parent.isVisible()}, window={parent.isWindow()})"
            )
        except Exception:
            parent_text = parent.__class__.__name__
        chain: list[str] = [parent_text]
        depth = 0
        while depth < 5:
            try:
                parent = parent.parentWidget()
            except Exception:
                parent = None
            if not isinstance(parent, QWidget):
                break
            try:
                chain.append(
                    f"{parent.__class__.__name__}(objectName={parent.objectName()!r}, "
                    f"title={parent.windowTitle()!r}, visible={parent.isVisible()}, window={parent.isWindow()})"
                )
            except Exception:
                chain.append(parent.__class__.__name__)
            depth += 1
        return chain[0], " -> ".join(chain)

    def _log_widget(self, widget: QWidget, event_type: QEvent.Type) -> None:
        try:
            geo = widget.geometry()
            frame = widget.frameGeometry()
            parent_text, parent_chain = self._format_parent_info(widget)
            try:
                object_name = widget.objectName()
            except Exception:
                object_name = ""
            try:
                title = widget.windowTitle()
            except Exception:
                title = ""
            message = (
                "Suspicious startup widget | "
                f"event={event_type.name} | "
                f"class={widget.__class__.__name__} | "
                f"objectName={object_name!r} | "
                f"title={title!r} | "
                f"visible={widget.isVisible()} | "
                f"window={widget.isWindow()} | "
                f"flags=0x{int(getattr(widget.windowFlags(), 'value', widget.windowFlags())):x} | "
                f"geo=({geo.x()},{geo.y()},{geo.width()},{geo.height()}) | "
                f"frame=({frame.x()},{frame.y()},{frame.width()},{frame.height()}) | "
                f"parent={parent_text} | "
                f"parent_chain={parent_chain}"
            )
            if event_type == QEvent.Type.Show:
                message = f"{message}\n{''.join(traceback.format_stack(limit=16))}"
            self._logger.info(message)
        except Exception as exc:
            self._logger.warning(f"Suspicious startup widget trace failed: {exc}")


class StartupProgressBar(QProgressBar):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._glow_phase = 0.0
        self._glow_timer = QTimer(self)
        self._glow_timer.setInterval(20)
        self._glow_timer.timeout.connect(self._advance_glow)
        self._glow_timer.start()

    def _advance_glow(self) -> None:
        self._glow_phase = (self._glow_phase + 0.018) % 1.0
        self.update()

    def paintEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        option = QStyleOptionProgressBar()
        self.initStyleOption(option)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            rect = self.rect().adjusted(0, 0, -1, -1)
            radius = 8.0
            bg = QColor("#1f242b")
            border = QColor("#2f353d")
            fill_left = QColor("#7a5cff")
            fill_mid1 = QColor("#4f88ff")
            fill_mid2 = QColor("#39c7ba")
            fill_mid3 = QColor("#e8d85f")
            fill_right = QColor("#ef6b58")

            painter.setPen(QPen(border, 1))
            painter.setBrush(bg)
            painter.drawRoundedRect(rect, radius, radius)

            value = max(min(int(option.progress), int(option.maximum)), int(option.minimum))
            span = max(option.maximum - option.minimum, 1)
            fill_width = int(round(rect.width() * (value - option.minimum) / span))
            fill_rect = rect.adjusted(1, 1, 0, -1)
            fill_rect.setWidth(max(fill_width - 1, 0))
            if fill_rect.width() > 0:
                painter.setPen(Qt.PenStyle.NoPen)
                fill = QLinearGradient(fill_rect.left(), fill_rect.top(), fill_rect.right(), fill_rect.top())
                fill.setColorAt(0.0, fill_left)
                fill.setColorAt(0.22, fill_mid1)
                fill.setColorAt(0.45, fill_mid2)
                fill.setColorAt(0.72, fill_mid3)
                fill.setColorAt(1.0, fill_right)
                painter.setBrush(fill)
                painter.drawRoundedRect(fill_rect, radius, radius)

                glow_width = max(int(fill_rect.width() * 0.42), 92)
                travel_width = fill_rect.width() + glow_width
                glow_center = fill_rect.left() + int(travel_width * self._glow_phase)
                glow_left = int(glow_center - glow_width / 2)
                glow_rect = fill_rect.adjusted(0, 0, 0, 0)
                glow_rect.setLeft(glow_left)
                glow_rect.setWidth(glow_width)
                if glow_rect.right() >= fill_rect.left() and glow_rect.left() <= fill_rect.right():
                    visible_left = max(glow_rect.left(), rect.left())
                    visible_right = min(glow_rect.right(), fill_rect.right())
                    if visible_right > visible_left:
                        shine = QLinearGradient(visible_left, glow_rect.top(), visible_right, glow_rect.top())
                        shine.setColorAt(0.0, QColor(255, 255, 255, 0))
                        shine.setColorAt(0.36, QColor(255, 255, 255, 18))
                        shine.setColorAt(0.50, QColor(255, 255, 255, 130))
                        shine.setColorAt(0.64, QColor(255, 255, 255, 22))
                        shine.setColorAt(1.0, QColor(255, 255, 255, 0))
                        painter.save()
                        painter.setClipRect(fill_rect)
                        painter.setBrush(shine)
                        painter.drawRoundedRect(fill_rect, radius, radius)
                        painter.restore()
        finally:
            painter.end()


class StartupSplash(QWidget):
    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint,
        )
        self.setObjectName("startupSplash")
        self.setWindowTitle("Starting LSPR Acquisition")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        accent = QFrame(self)
        accent.setObjectName("startupAccent")
        accent.setFixedHeight(4)
        self._icon_opacity_effect = QGraphicsOpacityEffect(self)
        self._icon_opacity_effect.setOpacity(0.25)
        self._icon_label = QLabel(self)
        self._icon_label.setObjectName("startupIcon")
        self._icon_label.setFixedSize(108, 108)
        self._icon_label.setPixmap(QIcon(str(_brand_logo_path())).pixmap(92, 92))
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setGraphicsEffect(self._icon_opacity_effect)
        self._icon_opacity_anim = QPropertyAnimation(self._icon_opacity_effect, b"opacity", self)
        self._icon_opacity_anim.setDuration(220)
        self._icon_opacity_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        title = QLabel("LSPR Acquisition", self)
        title.setObjectName("startupTitle")
        title.setContentsMargins(0, 0, 0, 4)

        version_label = QLabel(f"ver. {__version__}", self)
        version_label.setObjectName("startupVersion")
        version_label.setContentsMargins(0, 0, 0, 4)

        subtitle = QLabel("Spectroscopy and experiment control are waking up.", self)
        subtitle.setObjectName("startupSubtitle")
        self._status_label = QLabel("Starting...", self)
        self._status_label.setObjectName("startupStatus")
        self._progress = StartupProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(accent)

        body = QWidget(self)
        content_row = QWidget(body)
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(3)

        title_row = QWidget(content_row)
        title_row_layout = QHBoxLayout()
        title_row_layout.setContentsMargins(0, 0, 0, 0)
        title_row_layout.setSpacing(8)
        title_row_layout.addWidget(title, 1)
        title_row_layout.addWidget(version_label, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        title_row.setLayout(title_row_layout)

        content_layout.addWidget(title_row)
        content_layout.addWidget(subtitle)
        content_layout.addWidget(self._status_label)
        content_layout.addWidget(self._progress)
        content_row.setLayout(content_layout)

        header_row = QWidget(body)
        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)
        header_layout.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        header_row.setLayout(header_layout)

        body_layout = QHBoxLayout()
        body_layout.setContentsMargins(24, 16, 24, 18)
        body_layout.setSpacing(14)
        body_layout.addWidget(header_row, 0, Qt.AlignmentFlag.AlignTop)
        body_layout.addWidget(content_row, 1)
        body.setLayout(body_layout)

        layout.addWidget(body)
        self.setLayout(layout)
        self.setFixedSize(560, 190)
        self.setStyleSheet(
            """
            QWidget#startupSplash {
                background: #15191f;
                border: 1px solid #2d333b;
                border-radius: 18px;
            }
            QFrame#startupAccent {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #7a5cff,
                    stop: 0.22 #4f88ff,
                    stop: 0.45 #39c7ba,
                    stop: 0.72 #e8d85f,
                    stop: 1 #ef6b58
                );
                border-top-left-radius: 18px;
                border-top-right-radius: 18px;
            }
            QLabel#startupIcon {
                background: transparent;
            }
            QLabel#startupTitle {
                font-size: 18px;
                font-weight: 700;
                color: #eef2f6;
                min-height: 24px;
                padding-bottom: 0px;
            }
            QLabel#startupVersion {
                color: #8a98a8;
                font-size: 10px;
                padding-top: 2px;
            }
            QLabel#startupSubtitle {
                color: #95a0ac;
                font-size: 12px;
            }
            QLabel#startupStatus {
                color: #d7dce2;
                font-size: 12px;
            }
            QProgressBar {
                border: none;
                background: transparent;
                min-height: 18px;
                max-height: 18px;
                padding: 0px;
            }
            """
        )
    def show_centered(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            x_pos = available.x() + (available.width() - self.width()) // 2
            y_pos = available.y() + (available.height() - self.height()) // 2
            self.move(x_pos, y_pos)
        self.show()
        QApplication.processEvents()

    def update_progress(self, value: int, text: str) -> None:
        value = max(0, min(value, 100))
        self._progress.setValue(value)
        self._status_label.setText(text)
        self._animate_icon_opacity(value)
        QApplication.processEvents()

    def _animate_icon_opacity(self, progress_value: int) -> None:
        target = 0.25 + (max(0, min(progress_value, 100)) / 100.0) * 0.75
        self._icon_opacity_anim.stop()
        self._icon_opacity_anim.setStartValue(self._icon_opacity_effect.opacity())
        self._icon_opacity_anim.setEndValue(target)
        self._icon_opacity_anim.start()


class StartupLoader(QObject):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, launch_profile: str = DEFAULT_LAUNCH_PROFILE) -> None:
        super().__init__()
        self._launch_profile_key = normalize_launch_profile(launch_profile)

    def run(self) -> None:
        try:
            profile = launch_profile_spec(self._launch_profile_key)
            self.progress.emit(4, "Checking for an already running instance...")
            lock = QLockFile(str(_lock_file_path()))
            self.progress.emit(6, "Waiting for previous instance to close...")
            if not lock.tryLock(5000):
                if not _recover_stale_lock(lock):
                    self.failed.emit(
                        "Another instance is still open or closing. "
                        "Please wait a moment and try again."
                    )
                    return

            self.progress.emit(10, "Loading core modules...")
            from lspr_app.domain.session import MeasurementSession

            self.progress.emit(30, "Preparing spectrometer backend...")
            spectrometer = create_spectrometer(force_simulator=profile.force_simulator)
            session = MeasurementSession()

            if profile.scan_devices:
                self.progress.emit(45, "Checking connected devices...")
                pump_probe = discover_pump()
            else:
                self.progress.emit(45, "Skipping connected device lookup for this launch mode.")
                pump_probe = None

            self.progress.emit(60, "Loading user interface...")
            from lspr_app.gui.experiment_control_window import ExperimentControlWindow  # noqa: F401
            from lspr_app.gui.main_window import MainWindow

            self.progress.emit(88, "Building main window...")
            self.finished.emit((lock, spectrometer, session, pump_probe, MainWindow, self._launch_profile_key))
        except Exception as exc:  # pragma: no cover - startup failure path
            self.failed.emit(str(exc))


def create_spectrometer(*, force_simulator: bool = False):
    from lspr_app.device.base import SpectrometerError
    from lspr_app.device.ocean import OceanSpectrometer
    from lspr_app.device.simulated import SimulatedSpectrometer

    force_env = os.environ.get("LSPR_FORCE_SIMULATOR", "").lower() in {"1", "true", "yes"}
    if force_simulator or force_env:
        return SimulatedSpectrometer()

    try:
        return OceanSpectrometer()
    except (ImportError, SpectrometerError):
        return SimulatedSpectrometer()


def discover_pump():
    from lspr_app.device.reglo_icc import RegloICCClient, is_probable_reglo_port

    sink = StringIO()
    with redirect_stdout(sink), redirect_stderr(sink):
        ports = [port for port in RegloICCClient.list_ports() if is_probable_reglo_port(port)]
        ordered = sorted(
            ports,
            key=lambda port: ("265C:0001" not in port.hwid.upper(), port.device),
        )
        for port in ordered:
            try:
                return RegloICCClient.probe_port(port.device)
            except Exception:
                continue
    return None


def _attach_startup_file_logging() -> tuple[logging.Handler | None, DiagnosticsConfig]:
    diagnostics = DiagnosticsConfig.from_env()
    project_destination = str(load_app_setting("recording_project_destination", "") or "").strip()
    experiment_name = str(load_app_setting("recording_experiment_name", "") or "").strip()
    try:
        log_path = build_recording_experiment_log_path(
            project_destination,
            experiment_name,
            prefix="startup_log",
            suffix=".log",
        )
    except Exception:
        return None, diagnostics

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s %(message)s", "%H:%M:%S"))
    apply_diagnostic_info_filter(handler, diagnostics)

    root_logger = logging.getLogger()
    root_logger.setLevel(int(diagnostics.file_log_min_level))
    root_logger.addHandler(handler)
    logging.getLogger("lspr_app").setLevel(int(diagnostics.gui_log_min_level))
    logging.getLogger("lspr_app.bootstrap").setLevel(int(diagnostics.gui_log_min_level))
    return handler, diagnostics


def main() -> None:
    file_handler, diagnostics = _attach_startup_file_logging()
    bootstrap_logger = logging.getLogger("lspr_app.bootstrap")
    bootstrap_level = logging.WARNING if diagnostics.profile == "off" else logging.INFO
    bootstrap_logger.log(bootstrap_level, diagnostics.startup_summary_text())
    app = QApplication(sys.argv)
    startup_widget_tracer = StartupSuspiciousWidgetTracer(
        bootstrap_logger,
        enabled=bool(diagnostics.debug_timing_enabled or diagnostics.deep_timing_enabled),
    )
    app.installEventFilter(startup_widget_tracer)
    app._startup_widget_tracer = startup_widget_tracer
    app.setApplicationDisplayName("LSPR Acquisition")
    app.setApplicationVersion(__version__)
    apply_base_app_theme(app)
    save_app_setting("theme_mode", "dark")
    app.setWindowIcon(app_icon())
    splash = StartupSplash()
    splash.show_centered()
    loader_thread = QThread()
    loader = StartupLoader(os.environ.get(LAUNCH_PROFILE_ENV_VAR, DEFAULT_LAUNCH_PROFILE))
    loader.moveToThread(loader_thread)
    loader.progress.connect(splash.update_progress)

    def _cleanup_loader() -> None:
        loader_thread.quit()
        loader_thread.wait()
        loader.deleteLater()
        loader_thread.deleteLater()

    def _show_main_window(payload: object) -> None:
        try:
            instance_lock, spectrometer, session, pump_probe, main_window_cls, launch_profile = payload  # type: ignore[misc]
        except Exception as exc:  # pragma: no cover - defensive startup path
            splash.close()
            QMessageBox.critical(None, "Startup error", f"Failed to unpack startup payload: {exc}")
            app.quit()
            return
        splash.update_progress(100, "Ready.")
        app.instance_lock = instance_lock
        bootstrap_logger = logging.getLogger("lspr_app.bootstrap")
        bootstrap_logger.info("Constructing main window...")
        try:
            window = main_window_cls(
                spectrometer=spectrometer,
                session=session,
                discovered_pump_probe=pump_probe,
                launch_profile=launch_profile,
            )
        except Exception as exc:  # pragma: no cover - defensive startup path
            bootstrap_logger.exception("Main window construction failed.")
            splash.close()
            QMessageBox.critical(None, "Startup error", f"Failed to construct main window: {exc}")
            app.quit()
            return
        bootstrap_logger.info("Main window constructed.")
        app.main_window = window
        window._startup_splash = splash
        window._startup_show_requested_t0 = perf_counter()
        window._startup_show_requested = True
        QTimer.singleShot(0, window._complete_startup_show)

    def _handle_startup_failure(message: str) -> None:
        tracer = getattr(app, "_startup_widget_tracer", None)
        if tracer is not None:
            try:
                app.removeEventFilter(tracer)
            except Exception:
                pass
            app._startup_widget_tracer = None
        splash.close()
        QMessageBox.critical(None, "Startup error", message)
        app.quit()

    loader.finished.connect(_show_main_window)
    loader.finished.connect(_cleanup_loader)
    loader.failed.connect(_handle_startup_failure)
    loader.failed.connect(_cleanup_loader)
    loader_thread.started.connect(loader.run)
    loader_thread.start()

    try:
        sys.exit(app.exec())
    finally:
        if file_handler is not None:
            root_logger = logging.getLogger()
            root_logger.removeHandler(file_handler)
            file_handler.close()
