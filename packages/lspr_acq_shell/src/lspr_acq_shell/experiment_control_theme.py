"""Shared experiment-control panel theme: palette + stylesheet template.

Moved from sLSPR acq's `ExperimentControlWindow._theme_palette`/`_apply_style`
(Tier 3b, 2026-08-10) - this closes the actual class of bug the direction-
button stylesheet fix earlier the same day was a symptom of: two apps each
keeping their own copy of "the same" theme, silently drifting apart. Now
there is exactly one.

LSPRi acq's own copy had one real addition not in sLSPR acq's:
`QToolButton#flowIconButton:checked`, used by its checkable hold/pause/
edit-mode toggle buttons. Merged into the shared template rather than
dropped - it's genuine, working style, harmless to sLSPR acq (that app
never puts a `flowIconButton` into the checked state, so the rule simply
never matches there), and per the maintainer's "one shared model" direction
this is meant to be the single implementation both apps use, not sLSPR
acq's own history frozen in place.

`experiment_control_theme_palette(mode)` returns the full dark/light dict
(sLSPR acq is the only app that currently switches between them - LSPRi
acq's own `_theme_mode` is still hardcoded to `"dark"` - but sharing the
whole bidirectional palette costs nothing and means light-mode support is
already here if/when LSPRi acq wants it).
"""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget

_DARK_PALETTE: dict[str, str] = {
    "bg": "#13161b",
    "fg": "#e6ebf1",
    "muted": "#a8b0ba",
    "field": "#171b21",
    "button": "#20252d",
    "button_hover": "#272d36",
    "button_pressed": "#303640",
    "accent_button": "#5d6876",
    "accent_hover": "#707d8c",
    "title": "#8fbaff",
    "danger_button": "#8f5a61",
    "danger_hover": "#a46a72",
    "border": "#2b3138",
    "border_hover": "#414852",
    "pressed": "#252b33",
    "scroll": "#49505a",
    "scroll_hover": "#5c6470",
    "splitter": "#2b3138",
    "timeline_bg": "#0f1216",
    "header": "#1b2026",
    "selection": "#252b33",
}

_LIGHT_PALETTE: dict[str, str] = {
    "bg": "#f4f6f8",
    "fg": "#1d2733",
    "muted": "#5f7388",
    "field": "#f4f6f8",
    "button": "#eef3f7",
    "button_hover": "#e6edf3",
    "button_pressed": "#dde9f3",
    "accent_button": "#2f80c1",
    "accent_hover": "#3e8dcf",
    "title": "#2f80c1",
    "danger_button": "#d65a63",
    "danger_hover": "#e06a73",
    "border": "#d9e0e7",
    "border_hover": "#9dbbd4",
    "pressed": "#dde9f3",
    "scroll": "#bcc9d5",
    "scroll_hover": "#9fb3c5",
    "splitter": "#dde5ec",
    "timeline_bg": "#f4f6f8",
    "header": "#eef3f7",
    "selection": "#dbeafe",
}


def experiment_control_theme_palette(mode: str) -> dict[str, str]:
    return dict(_DARK_PALETTE if mode == "dark" else _LIGHT_PALETTE)


_STYLE_TEMPLATE = """
QWidget {
    background: %(bg)s;
    color: %(fg)s;
    font-size: 12px;
}
QToolTip {
    background-color: %(bg)s;
    color: %(fg)s;
    border: 1px solid %(border)s;
    padding: 4px 6px;
}
QGroupBox {
    background: %(bg)s;
    border: 1px solid %(border)s;
    border-radius: 12px;
    margin-top: 8px;
    padding-top: 10px;
    font-weight: 600;
}
QGroupBox::title {
    left: 10px;
    top: 2px;
}
QPushButton, QToolButton, QComboBox, QDoubleSpinBox, QLineEdit, QTableWidget {
    background: %(field)s;
    border: 1px solid %(border)s;
    border-radius: 10px;
    padding: 4px 6px;
}
QSpinBox, QDoubleSpinBox {
    border-radius: 3px;
    padding: 1px 4px;
}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 0px;
    border: none;
    background: transparent;
}
QSpinBox::up-arrow, QSpinBox::down-arrow,
QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {
    width: 0px;
    height: 0px;
}
QPushButton:hover, QToolButton:hover, QComboBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {
    border-color: %(border_hover)s;
    background: %(button_hover)s;
}
QPushButton:pressed, QToolButton:pressed {
    background: %(button_pressed)s;
}
QPushButton#accentButton {
    background: %(accent_button)s;
    border-color: %(accent_button)s;
}
QPushButton#accentButton:hover, QToolButton#accentButton:hover {
    background: %(accent_hover)s;
    border-color: %(accent_hover)s;
}
QToolButton#accentButton {
    background: %(accent_button)s;
    border-color: %(accent_button)s;
}
QPushButton#dangerButton {
    background: %(danger_button)s;
    border-color: %(danger_button)s;
}
QPushButton#dangerButton:hover, QToolButton#dangerButton:hover {
    background: %(danger_hover)s;
    border-color: %(danger_hover)s;
}
QToolButton#dangerButton {
    background: %(danger_button)s;
    border-color: %(danger_button)s;
}
QToolButton#flowIconButton {
    background: transparent;
    border: none;
    padding: 0px;
}
QToolButton#flowIconButton:hover {
    background: rgba(127, 127, 127, 0.10);
    border: none;
}
QToolButton#flowIconButton:pressed {
    background: rgba(127, 127, 127, 0.18);
    border: none;
}
QToolButton#flowIconButton:checked {
    background: rgba(102, 167, 255, 0.18);
    border: none;
}
QToolButton#flowViewModeButton {
    background: transparent;
    border: none;
    padding: 0px;
    margin: 0px;
    color: #e8d85f;
    font-weight: 600;
}
QToolButton#flowViewModeButton:hover {
    background: rgba(127, 127, 127, 0.10);
    border: none;
}
QToolButton#flowViewModeButton:pressed {
    background: rgba(127, 127, 127, 0.18);
    border: none;
}
QToolButton#flowColorAddButton {
    background: transparent;
    border: none;
    padding: 0px;
    min-width: 18px;
    min-height: 18px;
}
QToolButton#flowColorAddButton:hover {
    background: rgba(47, 143, 83, 0.10);
}
QToolButton#flowColorAddButton:pressed {
    background: rgba(47, 143, 83, 0.18);
}
QToolButton#flowColorRemoveButton {
    background: transparent;
    border: none;
    padding: 0px;
    min-width: 18px;
    min-height: 18px;
    color: #b44a4a;
}
QToolButton#flowColorRemoveButton:hover {
    background: rgba(180, 74, 74, 0.10);
}
QToolButton#flowColorRemoveButton:pressed {
    background: rgba(180, 74, 74, 0.18);
}
QToolButton#flowSwitchModeButton,
QToolButton#flowSwitchSettingsButton,
QToolButton#flowCommentDisplayButton {
    background: transparent;
    border: none;
    padding: 0px;
    min-width: 18px;
    min-height: 18px;
    color: #f0f3f7;
}
QToolButton#flowValveSettingsButton {
    background: transparent;
    border: none;
    padding: 0px;
    min-width: 18px;
    min-height: 18px;
    color: #f0f3f7;
}
QToolButton#flowSwitchModeButton:hover,
QToolButton#flowSwitchSettingsButton:hover,
QToolButton#flowValveSettingsButton:hover,
QToolButton#flowCommentDisplayButton:hover {
    background: rgba(127, 127, 127, 0.10);
}
QToolButton#flowSwitchModeButton:pressed,
QToolButton#flowSwitchSettingsButton:pressed,
QToolButton#flowValveSettingsButton:pressed,
QToolButton#flowCommentDisplayButton:pressed {
    background: rgba(127, 127, 127, 0.18);
}
QLabel#flowHeaderLabel {
    color: %(muted)s;
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 0.8px;
}
QWidget#flowContent, QWidget#flowEditorContainer {
    background: %(bg)s;
    border: none;
}
QTableView#flowControlTable {
    background: %(bg)s;
    border: none;
    border-radius: 0px;
    gridline-color: %(border)s;
    alternate-background-color: %(button)s;
    selection-background-color: transparent;
    selection-color: %(fg)s;
    font-size: 11px;
}
QTableView#flowControlTable::viewport {
    background: %(bg)s;
    border: none;
}
QTableView#flowControlTable::item {
    border: none;
    padding: 1px 4px;
}
QTableView#flowControlTable QComboBox,
QTableView#flowControlTable QDoubleSpinBox,
QTableView#flowControlTable QLineEdit,
QTableView#flowControlTable QToolButton {
    background: transparent;
    border: none;
    padding: 0px 1px;
    margin: 0px;
}
QTableView#flowControlTable QComboBox::drop-down {
    border: none;
    background: transparent;
    width: 0px;
}
QTableView#flowControlTable QComboBox::down-arrow {
    width: 0px;
    height: 0px;
}
QTableView#flowControlTable QComboBox::item {
    padding: 0px 4px;
}
QTableView#flowControlTable QDoubleSpinBox::up-button,
QTableView#flowControlTable QDoubleSpinBox::down-button {
    width: 0px;
    border: none;
    background: transparent;
}
QTableView#flowControlTable QDoubleSpinBox::up-arrow,
QTableView#flowControlTable QDoubleSpinBox::down-arrow {
    width: 0px;
    height: 0px;
}
QTableView#flowControlTable::item:selected {
    background: transparent;
    background-color: transparent;
}
QTableView#flowControlTable::item:selected:active,
QTableView#flowControlTable::item:selected:!active {
    background: transparent;
    background-color: transparent;
}
QTableView#flowControlTable QHeaderView::section {
    background: %(header)s;
    border: none;
    border-right: 1px solid %(border)s;
    border-bottom: 1px solid %(border)s;
    padding: 0px 1px;
    font-size: 10px;
    font-weight: 600;
}
QScrollBar:vertical {
    background: transparent;
    width: 8px;
}
QScrollBar::handle:vertical {
    background: %(scroll)s;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: %(scroll_hover)s;
}
QSplitter::handle {
    background: %(splitter)s;
}
QSplitter::handle:vertical {
    height: 6px;
    margin: 0 4px;
    border-radius: 3px;
}
"""


def apply_experiment_control_style(widget: QWidget, palette: dict[str, str]) -> None:
    widget.setStyleSheet(_STYLE_TEMPLATE % palette)
