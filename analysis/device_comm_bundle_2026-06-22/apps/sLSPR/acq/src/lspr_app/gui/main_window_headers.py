from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QTabBar, QWidget, QToolButton
from lspr_app.gui.icon_helpers import math_function_tab_icon, prism_tab_icon, tabler_icon, tint_tabler_icon


def install_source_link_buttons(window) -> None:
    window._source_link_buttons = {}
    for tab_index, mode in ((0, "spectrometer"), (1, "simulation")):
        button = QToolButton(window.source_tabs)
        button.setObjectName(f"{mode}SourceLinkButton")
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(20, 20)
        button.setStyleSheet(
            "QToolButton { border: none; padding: 0px; margin: 0px; background: transparent; }"
            "QToolButton:hover { background: transparent; }"
            "QToolButton:pressed { background: transparent; }"
        )
        button.clicked.connect(
            lambda _checked=False, source_mode=mode, source_tab_index=tab_index: handle_source_link_clicked(
                window,
                source_mode,
                source_tab_index,
            )
        )
        window._source_link_buttons[mode] = button


def install_source_tab_headers(window) -> None:
    tab_bar = window.source_tabs.tabBar()
    window._source_tab_headers = {}
    window._source_tab_header_widgets = {}

    for tab_index, mode, title in ((0, "spectrometer", "Spectrometer"), (1, "simulation", "Simulation")):
        header = QWidget(window.source_tabs)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)

        icon_label = QLabel(header)
        icon_label.setFixedSize(16, 16)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_label = QLabel(title, header)
        title_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        button = window._source_link_buttons[mode]
        button.setParent(header)
        button.setFixedSize(20, 20)

        if mode == "spectrometer":
            header_layout.addWidget(icon_label)
            header_layout.addWidget(title_label)
            header_layout.addWidget(button)
            header_layout.addStretch(1)
        else:
            header_layout.addStretch(1)
            header_layout.addWidget(icon_label)
            header_layout.addWidget(title_label)
            header_layout.addWidget(button)

        tab_bar.setTabButton(tab_index, QTabBar.ButtonPosition.LeftSide, header)
        window._source_tab_headers[mode] = (icon_label, title_label)
        window._source_tab_header_widgets[mode] = header

    update_source_tab_headers(window)


def update_source_tab_headers(window) -> None:
    headers = getattr(window, "_source_tab_headers", None)
    if not headers:
        return
    theme_text = QColor("#e6ebf1") if window._theme_mode == "dark" else QColor("#1d2733")
    spec_icon_label, spec_title_label = headers["spectrometer"]
    sim_icon_label, sim_title_label = headers["simulation"]
    spec_icon_label.setPixmap(prism_tab_icon().pixmap(16, 16))
    sim_icon_label.setPixmap(math_function_tab_icon(QColor("#8a98a8")).pixmap(16, 16))
    spec_title_label.setStyleSheet(f"color: {theme_text.name()};")
    sim_title_label.setStyleSheet(f"color: {theme_text.name()};")


def source_link_button_icon(window, mode: str) -> tuple[QIcon, str, bool]:
    if mode == "spectrometer":
        if not window._hardware_available:
            return (
                tint_tabler_icon(tabler_icon("link_off", "link-off"), QColor("#8a98a8")),
                "Spectrometer is not connected.",
                False,
            )
        if window._source_mode == "spectrometer":
            return (
                tint_tabler_icon(tabler_icon("link"), QColor("#47a861")),
                "Spectrometer feed active.",
                True,
            )
        return (
            tint_tabler_icon(tabler_icon("link_off", "link-off"), QColor("#d05b5b")),
            "Switch feed to spectrometer.",
            True,
        )
    if window._source_mode == "simulation":
        return (
            tint_tabler_icon(tabler_icon("link"), QColor("#47a861")),
            "Simulation feed active.",
            True,
        )
    return (
        tint_tabler_icon(tabler_icon("link_off", "link-off"), QColor("#d05b5b")),
        "Switch feed to simulation.",
        True,
    )


def update_source_link_buttons(window) -> None:
    buttons = getattr(window, "_source_link_buttons", None)
    if not buttons:
        return
    for mode, button in buttons.items():
        icon, tooltip, enabled = source_link_button_icon(window, mode)
        button.setIcon(icon)
        button.setEnabled(enabled and not window._measurement_active)
        if window._measurement_active and enabled:
            button.setToolTip("Source switching is disabled during measurement.")
        else:
            button.setToolTip(tooltip)


def handle_source_link_clicked(window, new_mode: str, tab_index: int) -> None:
    if new_mode == window._source_mode:
        window.source_tabs.blockSignals(True)
        window.source_tabs.setCurrentIndex(tab_index)
        window.source_tabs.blockSignals(False)
        return
    request_source_mode_switch(window, new_mode, tab_index)


def request_source_mode_switch(window, new_mode: str, select_tab_index: int | None = None) -> None:
    if new_mode == "spectrometer" and not window._hardware_available:
        window.status_label.setText("Spectrometer is not connected.")
        window._log_warning("Spectrometer source requested while no hardware is available.")
        return
    if window._measurement_active:
        window.status_label.setText("Stop measurement before switching source.")
        window._log_warning("Source switch blocked while measurement is active.")
        return
    if new_mode == window._source_mode:
        if select_tab_index is not None:
            window.source_tabs.blockSignals(True)
            window.source_tabs.setCurrentIndex(select_tab_index)
            window.source_tabs.blockSignals(False)
        return
    if window._busy:
        window._pending_source_mode = new_mode
        window._resume_live_after_source_switch = window._live_active
        if window._live_active:
            window._live_active = False
        window.status_label.setText("Source switch queued. Waiting for the current acquisition to finish...")
        window._log_info(f"Queued source switch to {new_mode}.")
        return

    was_live = window._live_active
    if window._live_active:
        window._stop_live_acquisition("Switching source...")
    window._log_info(f"Switching source to {new_mode}.")
    window._apply_source_mode(new_mode, restart_live=was_live)
    if select_tab_index is not None:
        window.source_tabs.blockSignals(True)
        window.source_tabs.setCurrentIndex(select_tab_index)
        window.source_tabs.blockSignals(False)
