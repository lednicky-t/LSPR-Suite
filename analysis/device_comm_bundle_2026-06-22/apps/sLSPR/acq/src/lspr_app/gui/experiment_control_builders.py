from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QToolButton, QSizePolicy



def create_flow_step_action_button(icon: QIcon, tooltip: str) -> QToolButton:
    button = QToolButton()
    button.setObjectName("flowStepActionButton")
    button.setAutoRaise(True)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setFixedSize(32, 32)
    button.setIconSize(QSize(24, 24))
    button.setIcon(icon)
    button.setToolTip(tooltip)
    button.setStyleSheet(
        "QToolButton#flowStepActionButton { background: transparent; border: none; padding: 0px; margin: 0px; }"
        "QToolButton#flowStepActionButton:hover { background: rgba(127, 127, 127, 0.10); border: none; }"
        "QToolButton#flowStepActionButton:pressed { background: rgba(127, 127, 127, 0.18); border: none; }"
    )
    return button


def create_direction_button(window, direction: str) -> QToolButton:
    button = QToolButton()
    button.setObjectName("directionButton")
    button.setFixedSize(30, 28)
    button.setStyleSheet(
        "QToolButton#directionButton {"
        " background: transparent;"
        " border: 1px solid %(border)s;"
        " border-radius: 10px;"
        " padding: 0px;"
        " margin: 0px;"
        " font-size: 15px;"
        " font-weight: 800;"
        " color: %(fg)s;"
        "}" % window._theme_palette()
        + "QToolButton#directionButton:hover { background: %(button_hover)s; border-color: %(border_hover)s; }"
        + "QToolButton#directionButton:pressed { background: %(button_pressed)s; }" % window._theme_palette()
    )
    button.setToolTip("Pump direction")
    set_direction_button(window, button, direction)
    return button


def direction_glyph(direction: str) -> str:
    normalized = "CCW" if str(direction or "").upper() == "CCW" else "CW"
    return "\u21ba" if normalized == "CCW" else "\u21bb"


def set_direction_button(window, button: QToolButton, direction: str) -> None:
    normalized = "CCW" if str(direction or "").upper() == "CCW" else "CW"
    button.setText(direction_glyph(normalized))
    button.setProperty("direction", normalized)
    button.setToolTip(
        f"Pump direction. Current state: {normalized} ({direction_glyph(normalized)})."
        f" Click to toggle between CW and CCW."
    )


def set_step_valve_button_state_for_button(window, button: QToolButton, valve: str) -> None:
    normalized = "Close" if str(valve or "").strip().lower() == "close" else "Open"
    button.setProperty("valve", normalized)
    button.setChecked(normalized == "Close")
    button.setText(window._valve_state_label(normalized))
    button.setToolTip(
        f"Valve state to associate with this step. Current state: {normalized}."
        f" Display label: {window._valve_state_label(normalized)}. Click to toggle."
    )


def create_table_flow_spin(
    window,
    value: float,
    direction: str,
    *,
    notify_change: bool = True,
    install_filters: bool = True,
) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 9999.0)
    spin.setDecimals(0)
    spin.setSingleStep(1.0)
    spin.setFrame(False)
    spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
    spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    spin.setFixedWidth(68)
    spin.setToolTip("Flow rate for this channel.")
    spin.blockSignals(True)
    spin.setValue(max(round(float(value)), 0))
    spin.setProperty("direction", "CCW" if str(direction or "").upper() == "CCW" else "CW")
    spin.blockSignals(False)
    if notify_change:
        spin.editingFinished.connect(lambda s=spin: window._handle_experiment_control_table_change(s))
    if install_filters:
        window._install_table_wheel_scroll_filter(spin)
    return spin


def create_table_duration_spin(
    window,
    seconds: float,
    *,
    notify_change: bool = True,
    install_filters: bool = True,
) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 86400.0)
    decimals = int(window._duration_display_decimals()) if hasattr(window, "_duration_display_decimals") else 0
    spin.setDecimals(decimals)
    spin.setSingleStep(1.0 if decimals == 0 else (0.1 if decimals == 1 else 0.01))
    spin.setFrame(False)
    spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
    spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    spin.setToolTip("Step duration.")
    spin.setFixedWidth(74)
    spin.blockSignals(True)
    value = max(float(seconds), 0.0)
    if hasattr(window, "_seconds_to_display"):
        value = float(window._seconds_to_display(value))
    spin.setValue(round(value, decimals))
    spin.blockSignals(False)
    if notify_change:
        spin.editingFinished.connect(lambda s=spin: window._handle_experiment_control_table_change(s))
    if install_filters:
        window._install_table_wheel_scroll_filter(spin)
    return spin


def create_table_valve_button(window, valve: str) -> QToolButton:
    button = QToolButton()
    button.setAutoRaise(True)
    button.setCheckable(True)
    button.setText("Open")
    button.setFixedWidth(60)
    button.setToolTip("Toggle between Open and Close.")
    button.blockSignals(True)
    set_step_valve_button_state_for_button(window, button, valve)
    button.blockSignals(False)
    button.pressed.connect(lambda _checked=False, b=button: window._toggle_step_valve_button(b))
    return button


def create_table_switch_combo(
    window,
    position: int,
    *,
    notify_change: bool = True,
    install_filters: bool = True,
) -> QComboBox:
    combo = QComboBox()
    combo.setFixedWidth(96)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    combo.setAutoFillBackground(True)
    combo.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    palette = window._theme_palette()
    combo.setStyleSheet(
        "QComboBox {"
        f" background-color: {palette['field']};"
        f" color: {palette['fg']};"
        f" border: 1px solid {palette['border']};"
        " border-radius: 4px;"
        " padding: 0px 6px;"
        " margin: 0px;"
        "}"
        "QComboBox:hover {"
        f" background-color: {palette['field']};"
        f" border-color: {palette['border_hover']};"
        "}"
        "QComboBox::drop-down { border: none; width: 0px; }"
        "QComboBox::down-arrow { width: 0px; height: 0px; }"
        "QComboBox QAbstractItemView {"
        f" background-color: {palette['field']};"
        f" color: {palette['fg']};"
        f" border: 1px solid {palette['border']};"
        f" selection-background-color: {palette['button_hover']};"
        f" selection-color: {palette['fg']};"
        "}"
    )
    combo.blockSignals(True)
    window._populate_switch_solution_combo(combo, position)
    combo.view().setMinimumWidth(window._switch_solution_popup_width(combo))
    combo.blockSignals(False)
    combo.currentIndexChanged.connect(
        lambda _index, c=combo: window._populate_switch_solution_combo(
            c,
            window._switch_position_from_text(str(c.currentData() or c.currentIndex() + 1)),
            show_labels=window._switch_solution_mode,
        )
    )
    if notify_change:
        combo.currentIndexChanged.connect(lambda *_args, c=combo: window._handle_experiment_control_table_change(c))
    if install_filters:
        window._install_table_wheel_scroll_filter(combo)
    return combo


def create_table_color_combo(
    window,
    color: str,
    *,
    notify_change: bool = True,
    install_filters: bool = True,
) -> QComboBox:
    combo = QComboBox()
    combo.setMinimumWidth(70)
    combo.setMaximumWidth(70)
    combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(5)
    combo.setEditable(False)
    combo.blockSignals(True)
    window._populate_color_combo(combo)
    color_index = combo.findData(color)
    if color_index >= 0:
        combo.setCurrentIndex(color_index)
    window._update_color_combo_style(combo)
    combo.blockSignals(False)
    combo.currentIndexChanged.connect(lambda _index, c=combo: window._update_color_combo_style(c))
    if notify_change:
        combo.currentIndexChanged.connect(lambda *_args, c=combo: window._handle_experiment_control_table_change(c))
    if install_filters:
        window._install_click_to_open_combo_filter(combo)
        window._install_table_wheel_scroll_filter(combo)
    return combo


def create_table_comment_edit(
    window,
    text: str,
    *,
    notify_change: bool = True,
    install_filters: bool = True,
) -> QLineEdit:
    edit = QLineEdit()
    edit.setFrame(False)
    edit.setText(text)
    edit.setPlaceholderText("Comment")
    edit.setToolTip("Short step description shown in the timeline.")
    edit.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    edit.setStyleSheet(
        "QLineEdit {"
        " background: transparent;"
        " border: none;"
        " padding: 0px 2px;"
        " margin: 0px;"
        "}"
    )
    if notify_change:
        edit.editingFinished.connect(lambda e=edit: window._handle_experiment_control_table_change(e))
    if install_filters:
        window._install_table_wheel_scroll_filter(edit)
    return edit

