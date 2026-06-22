from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QMenuBar, QWidget

from lspr_core import DEFAULT_LAUNCH_PROFILE, launch_profile_spec
from lspr_app.device.connection_registry import snapshot_port_ownership
from lspr_app.gui.icon_helpers import device_status_icon


def device_status_state(connected: bool, discovered: bool) -> str:
    if connected:
        return "connected"
    if discovered:
        return "discovered"
    return "disconnected"


def _device_status_entry(connected: bool, discovered: bool, detail: str = "") -> tuple[str, str]:
    return device_status_state(connected, discovered), detail


def device_status_tooltip(label_text: str, state: str, *, port_name: str = "", detail: str = "") -> str:
    normalized = device_status_state(state == "connected", state == "discovered")
    tooltip = f"{label_text}: {normalized}."
    if port_name:
        tooltip = f"{label_text}: {normalized} on {port_name}."
    if detail:
        suffix = str(detail).strip()
        if suffix and not suffix.startswith("("):
            suffix = f"({suffix})"
        tooltip = f"{tooltip} {suffix}".strip()
    return tooltip


def build_title_bar(window, menu_bar: QMenuBar, brand_icon_path) -> QWidget:
    profile = getattr(window, "_launch_profile_spec", None)
    if profile is None:
        profile = launch_profile_spec(DEFAULT_LAUNCH_PROFILE)

    title_widget = QWidget(window)
    title_widget.setObjectName("titleBar")

    window._window_mode_label = QLabel(title_widget)
    window._window_mode_label.setObjectName("windowModeLabel")
    window._window_mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    window._window_mode_label.setContentsMargins(0, 0, 0, 0)
    window._window_mode_label.setToolTip("Current application mode.")
    window._update_window_mode_label()

    window._window_mode_icon_label = QLabel(title_widget)
    window._window_mode_icon_label.setObjectName("windowModeIconLabel")
    window._window_mode_icon_label.setFixedSize(16, 16)
    window._window_mode_icon_label.setToolTip("Current source icon.")

    center_cluster = QWidget(title_widget)
    center_layout = QHBoxLayout()
    center_layout.setContentsMargins(0, 0, 0, 0)
    center_layout.setSpacing(5)
    center_layout.addWidget(window._window_mode_label)
    center_layout.addWidget(window._window_mode_icon_label)
    center_cluster.setLayout(center_layout)
    window._window_mode_cluster = center_cluster

    status_cluster = QWidget(title_widget)
    status_layout = QHBoxLayout()
    status_layout.setContentsMargins(0, 0, 0, 0)
    status_layout.setSpacing(8)
    window._startup_loading_frames = ["â—", "â—“", "â—‘", "â—’"]
    window._startup_loading_frame_index = 0
    window._startup_loading_label = QLabel("â—", title_widget)
    window._startup_loading_label.setObjectName("startupLoadingLabel")
    window._startup_loading_label.setFixedSize(16, 16)
    window._startup_loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    window._startup_loading_label.setToolTip("Device initialization is still in progress.")
    window._startup_loading_label.setVisible(False)
    window._startup_loading_timer = QTimer(window)
    window._startup_loading_timer.setInterval(140)
    window._startup_loading_timer.timeout.connect(window._advance_startup_loading_indicator)
    window._recording_blink_visible = True
    window._recording_blink_timer = QTimer(window)
    window._recording_blink_timer.setInterval(650)
    window._recording_blink_timer.timeout.connect(window._advance_recording_blink_indicator)
    window._hw_status_items = []
    for key, label_text in (
        ("spectrometer", "Spectrometer"),
        ("pump", "Pump"),
        ("valve", "Valve"),
        ("mswitch", "M-Switch"),
    ):
        icon_label = QLabel(title_widget)
        icon_label.setFixedSize(16, 16)
        icon_label.setToolTip(f"{label_text} connection status.")
        text_label = QLabel(label_text, title_widget)
        text_label.setToolTip(f"{label_text} connection status.")
        item = QWidget(title_widget)
        item_layout = QHBoxLayout()
        item_layout.setContentsMargins(0, 0, 0, 0)
        item_layout.setSpacing(4)
        item_layout.addWidget(icon_label)
        item_layout.addWidget(text_label)
        item.setLayout(item_layout)
        status_layout.addWidget(item)
        window._hw_status_items.append((key, icon_label, text_label))
    status_cluster.setLayout(status_layout)
    status_cluster.setVisible(bool(profile.show_device_statuses))
    window._titlebar_status_cluster = status_cluster

    left_cluster = QWidget(title_widget)
    left_layout = QHBoxLayout()
    left_layout.setContentsMargins(0, 0, 0, 0)
    left_layout.setSpacing(6)
    left_layout.addWidget(menu_bar)
    left_cluster.setLayout(left_layout)

    right_cluster = QWidget(title_widget)
    right_layout = QHBoxLayout()
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.setSpacing(4)
    right_layout.addWidget(window._startup_loading_label)
    right_layout.addWidget(status_cluster)
    right_cluster.setLayout(right_layout)

    title_layout = QGridLayout()
    title_layout.setContentsMargins(8, 3, 8, 3)
    title_layout.setHorizontalSpacing(8)
    title_layout.setVerticalSpacing(0)
    title_layout.setColumnStretch(0, 1)
    title_layout.setColumnStretch(1, 0)
    title_layout.setColumnStretch(2, 1)
    title_layout.addWidget(left_cluster, 0, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    title_layout.addWidget(center_cluster, 0, 1, alignment=Qt.AlignmentFlag.AlignCenter)
    title_layout.addWidget(right_cluster, 0, 2, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    title_widget.setLayout(title_layout)
    title_widget.installEventFilter(window)
    window._update_window_mode_label()
    return title_widget


def refresh_hw_device_status_strip(window) -> None:
    items = getattr(window, "_hw_status_items", None)
    if not items:
        return
    profile = getattr(window, "_launch_profile_spec", None)
    if profile is None:
        profile = launch_profile_spec(DEFAULT_LAUNCH_PROFILE)
    status_cluster = getattr(window, "_titlebar_status_cluster", None)
    if status_cluster is not None:
        status_cluster.setVisible(bool(profile.show_device_statuses))
    if not bool(profile.show_device_statuses):
        return
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if experiment_control_window is not None and hasattr(experiment_control_window, "synchronize_device_connections"):
        try:
            experiment_control_window.synchronize_device_connections()
        except Exception:
            pass
    overrides = getattr(window, "_hardware_status_overrides", {})
    init_active = bool(getattr(window, "_hardware_init_task", None)) or not bool(getattr(window, "_hardware_init_ready_emitted", False))
    pump_client = getattr(experiment_control_window, "_client", None) if experiment_control_window is not None else None
    valve_client = getattr(experiment_control_window, "_valve_client", None) if experiment_control_window is not None else None
    mswitch_client = getattr(experiment_control_window, "_mswitch_client", None) if experiment_control_window is not None else None
    pump_probe = getattr(experiment_control_window, "_probe", None) if experiment_control_window is not None else None
    valve_probe = getattr(experiment_control_window, "_valve_probe", None) if experiment_control_window is not None else None
    mswitch_probe = getattr(experiment_control_window, "_mswitch_probe", None) if experiment_control_window is not None else None
    owner_snapshot = snapshot_port_ownership()

    def _is_connected(client, owner_label: str) -> bool:
        if client is None or not getattr(client, "is_connected", lambda: False)():
            return False
        port = getattr(client, "port", None)
        port_name = getattr(port, "device", port)
        if not port_name:
            return False
        return owner_label in owner_snapshot.get(str(port_name), "")

    def _port_name(probe: object | None) -> str:
        if probe is None:
            return ""
        port = getattr(probe, "port", None)
        return str(getattr(port, "device", port) or "").strip()

    def _is_discovered(client, probe: object | None, owner_label: str) -> bool:
        port_name = _port_name(probe)
        if not port_name:
            return False
        if _is_connected(client, owner_label):
            return False
        return True

    devices = {
        "spectrometer": ("connected" if bool(window._hardware_available) else "disconnected", "", ""),
        "pump": (
            device_status_state(
                _is_connected(pump_client, "Experiment Control / Pump"),
                _is_discovered(pump_client, pump_probe, "Experiment Control / Pump"),
            ),
            "",
            _port_name(pump_probe) if not _is_connected(pump_client, "Experiment Control / Pump") else str(
                getattr(getattr(pump_client, "port", None), "device", getattr(pump_client, "port", "")) or ""
            ).strip(),
        ),
        "valve": (
            device_status_state(
                _is_connected(valve_client, "Experiment Control / Valve"),
                _is_discovered(valve_client, valve_probe, "Experiment Control / Valve"),
            ),
            "",
            _port_name(valve_probe) if not _is_connected(valve_client, "Experiment Control / Valve") else str(
                getattr(getattr(valve_client, "port", None), "device", getattr(valve_client, "port", "")) or ""
            ).strip(),
        ),
        "mswitch": (
            device_status_state(
                _is_connected(mswitch_client, "Experiment Control / M-Switch"),
                _is_discovered(mswitch_client, mswitch_probe, "Experiment Control / M-Switch"),
            ),
            "",
            _port_name(mswitch_probe) if not _is_connected(mswitch_client, "Experiment Control / M-Switch") else str(
                getattr(getattr(mswitch_client, "port", None), "device", getattr(mswitch_client, "port", "")) or ""
            ).strip(),
        ),
    }
    for key, icon_label, text_label in items:
        state, detail, port_name = devices.get(key, ("disconnected", "", ""))
        if init_active and key in overrides:
            override_online, _override_detail = overrides.get(key, (state == "connected", detail))
            state = "connected" if override_online else state
        elif key in overrides and not detail:
            override_online, _override_detail = overrides.get(key, (state == "connected", detail))
            if override_online:
                state = "connected"
        icon_label.setPixmap(device_status_icon(state).pixmap(16, 16))
        base_text = text_label.text().split(":", 1)[0].strip()
        text_label.setText(base_text if not detail else f"{base_text}: {detail}")
        tooltip = device_status_tooltip(base_text, state, port_name=port_name, detail=detail)
        icon_label.setToolTip(tooltip)
        text_label.setToolTip(tooltip)


def sync_window_control_icons(window) -> None:
    return

