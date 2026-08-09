"""Small, reusable icon-button builders for the experiment-control panel.

Extracted from singleLSPR Acquisition's `gui/experiment_control_builders.py`
(Phase 2, LSPRi acq experiment-control reuse - visual-parity effort started
2026-08-09) verbatim. `create_flow_step_action_button`/`direction_glyph` are
pure (no window coupling at all); `create_direction_button`/
`set_direction_button`/`set_step_valve_button_state_for_button` take a
`window` parameter duck-typed to anything providing `_theme_palette()` and,
for the valve-button helper, `_valve_state_label()` - the same pattern
`PlanRunLoopMixin` and `plan_step_commands` already use, not a formal
`Protocol` (this project doesn't use one for GUI wiring elsewhere).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QToolButton


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
    return "↺" if normalized == "CCW" else "↻"


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
