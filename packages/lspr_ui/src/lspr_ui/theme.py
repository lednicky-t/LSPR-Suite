from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication


@dataclass(frozen=True)
class GuiTheme:
    text_primary: str = "#f8fafc"
    text_secondary: str = "#dbe5f3"
    text_muted: str = "#cbd5e1"
    text_dim: str = "#94a3b8"
    window_bg: str = "#11161f"
    toolbar_bg: str = "#11161f"
    toolbar_section_bg: str = "#18212f"
    toolbar_border: str = "#243041"
    control_bg: str = "#243041"
    control_bg_hover: str = "#304155"
    control_bg_pressed: str = "#0b1220"
    control_border: str = "#324256"
    control_border_hover: str = "#60a5fa"
    control_border_pressed: str = "#fbbf24"
    control_disabled_bg: str = "#111827"
    control_disabled_border: str = "#1f2937"
    control_disabled_text: str = "#64748b"
    accent_green: str = "#22c55e"
    accent_blue: str = "#38bdf8"
    accent_red: str = "#ef4444"
    accent_purple: str = "#a855f7"
    accent_gold: str = "#f59e0b"
    primary_action_bg: str = "#1d4ed8"
    primary_action_border: str = "#60a5fa"
    primary_action_checked_bg: str = "#15803d"
    primary_action_checked_border: str = "#fef08a"
    spot_color: str = "#22c55e"
    mask_color: str = "#ef4444"
    histogram_mask_color: str = "#f59e0b"
    figure_mask_color: str = "#8b5cf6"
    ring_color: str = "#94a3b8"
    highlight_color: str = "#38bdf8"
    scale_bar_color: str = "#000000"
    icon_button_outer: int = 28
    icon_button_inner: int = 24
    plain_icon_outer: int = 34
    plain_icon_inner: int = 28
    compact_icon_outer: int = 26
    compact_icon_inner: int = 22
    header_pin_outer: int = 18
    header_pin_inner: int = 14
    header_apply_outer_w: int = 24
    header_apply_outer_h: int = 24
    header_apply_inner: int = 20


BLUE_DARK_THEME = GuiTheme()
GRAY_DARK_THEME = GuiTheme(
    text_primary="#f8fafc",
    text_secondary="#d7dde7",
    text_muted="#b4bdc9",
    text_dim="#8b95a3",
    window_bg="#14161a",
    toolbar_bg="#14161a",
    toolbar_section_bg="#1b1f26",
    toolbar_border="#2a313b",
    control_bg="#22262d",
    control_bg_hover="#2d333d",
    control_bg_pressed="#0f1318",
    control_border="#3a4250",
    control_border_hover="#8b949e",
    control_border_pressed="#c0a46b",
    control_disabled_bg="#111317",
    control_disabled_border="#22262d",
    control_disabled_text="#67707d",
    accent_green="#22c55e",
    accent_blue="#38bdf8",
    accent_red="#ef4444",
    accent_purple="#a855f7",
    accent_gold="#f59e0b",
    primary_action_bg="#334155",
    primary_action_border="#64748b",
    primary_action_checked_bg="#15803d",
    primary_action_checked_border="#86efac",
    spot_color="#22c55e",
    mask_color="#ef4444",
    histogram_mask_color="#f59e0b",
    figure_mask_color="#8b5cf6",
    ring_color="#94a3b8",
    highlight_color="#38bdf8",
    scale_bar_color="#000000",
)

_ACTIVE_THEME = GRAY_DARK_THEME


def get_active_theme() -> GuiTheme:
    return _ACTIVE_THEME


def set_active_theme(theme: GuiTheme) -> None:
    global _ACTIVE_THEME
    _ACTIVE_THEME = theme


APP_THEME = _ACTIVE_THEME


def hex_to_rgba(color: str, alpha: float) -> str:
    value = color.lstrip("#")
    if len(value) != 6:
        return color
    red = int(value[0:2], 16)
    green = int(value[2:4], 16)
    blue = int(value[4:6], 16)
    return f"rgba({red}, {green}, {blue}, {alpha:.2f})"


def apply_base_app_theme(app: QApplication, theme: GuiTheme | None = None) -> None:
    theme = theme or get_active_theme()
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(theme.window_bg))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.Base, QColor(theme.window_bg))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(theme.toolbar_section_bg))
    palette.setColor(QPalette.ColorRole.Text, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.Button, QColor(theme.control_bg))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(theme.primary_action_bg))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(theme.text_primary))
    app.setPalette(palette)
    app.setStyleSheet(startup_app_stylesheet(theme))


def transparent_icon_button_stylesheet(
    *,
    hover: str | None = None,
    pressed: str | None = None,
    checked: str | None = None,
    disabled: str = "transparent",
) -> str:
    rules = [
        "QToolButton {",
        "    background: transparent;",
        "    border: none;",
        "    border-radius: 0;",
        "    padding: 0;",
        "    margin: 0;",
        "}",
        "QToolButton:hover {",
        f"    background: {hover or 'transparent'};",
        "    border: none;",
        "}",
        "QToolButton:pressed {",
        f"    background: {pressed or hover or 'transparent'};",
        "    border: none;",
        "}",
    ]
    if checked is not None:
        rules.extend(
            [
                "QToolButton:checked {",
                f"    background: {checked};",
                "    border: none;",
                "}",
            ]
        )
    rules.extend(
        [
            "QToolButton:disabled {",
            f"    background: {disabled};",
            "    border: none;",
            "}",
        ]
    )
    return "\n".join(rules)


def icon_accent_colors(accent: str, theme: GuiTheme | None = None) -> tuple[str, str, str]:
    theme = theme or get_active_theme()
    if accent == "green":
        return (
            hex_to_rgba(theme.accent_green, 0.16),
            hex_to_rgba(theme.accent_green, 0.24),
            hex_to_rgba(theme.accent_green, 0.28),
        )
    if accent == "blue":
        return (
            hex_to_rgba(theme.accent_blue, 0.16),
            hex_to_rgba(theme.accent_blue, 0.24),
            hex_to_rgba(theme.accent_blue, 0.28),
        )
    if accent == "red":
        return (
            hex_to_rgba(theme.accent_red, 0.14),
            hex_to_rgba(theme.accent_red, 0.22),
            hex_to_rgba(theme.accent_red, 0.26),
        )
    if accent == "purple":
        return (
            hex_to_rgba(theme.accent_purple, 0.14),
            hex_to_rgba(theme.accent_purple, 0.22),
            hex_to_rgba(theme.accent_purple, 0.26),
        )
    return (
        hex_to_rgba(theme.control_border_hover, 0.14),
        hex_to_rgba(theme.control_border_pressed, 0.18),
        hex_to_rgba(theme.accent_green, 0.20),
    )


def collapsible_toggle_stylesheet(theme: GuiTheme | None = None) -> str:
    theme = theme or get_active_theme()
    return f"""
    QToolButton {{
        border: none;
        background: transparent;
        color: {theme.text_secondary};
        font-weight: 600;
        padding: 4px 0;
        text-align: left;
    }}
    QToolButton:hover {{
        border: none;
        background: transparent;
        color: {theme.text_primary};
    }}
    QToolButton:pressed {{
        border: none;
        background: transparent;
        color: {theme.text_primary};
    }}
    QToolButton:checked {{
        border: none;
        background: transparent;
        color: {theme.text_secondary};
    }}
    QToolButton:checked:hover {{
        border: none;
        background: transparent;
        color: {theme.text_primary};
    }}
    """


def collapsible_pin_stylesheet(theme: GuiTheme | None = None) -> str:
    theme = theme or get_active_theme()
    return f"""
    QToolButton {{
        border: none;
        padding: 0;
        color: {theme.text_muted};
        background: transparent;
    }}
    QToolButton:hover {{
        border: none;
        background: transparent;
        color: {theme.text_primary};
    }}
    QToolButton:checked {{
        border: none;
        background: transparent;
        color: {theme.accent_gold};
    }}
    """


def dark_image_toolbar_stylesheet(theme: GuiTheme | None = None) -> str:
    theme = theme or get_active_theme()
    plain_hover = hex_to_rgba(theme.control_border_hover, 0.12)
    plain_pressed = hex_to_rgba(theme.control_border_pressed, 0.16)
    plain_checked = hex_to_rgba(theme.accent_green, 0.18)
    return f"""
    QWidget#imageToolbar {{
        border: none;
        padding: 2px 0;
        background: {theme.toolbar_bg};
    }}
    QWidget#toolbarSection {{
        background: {theme.toolbar_section_bg};
        border: 1px solid {theme.toolbar_border};
        border-radius: 10px;
    }}
    QLabel#toolbarSectionTitle {{
        color: {theme.text_dim};
        font-size: 7px;
        font-weight: 700;
    }}
    QLabel#toolbarMiniLabel {{
        color: {theme.text_muted};
        font-size: 8px;
        font-weight: 600;
    }}
    QLabel#imageNameLabel {{
        color: {theme.text_muted};
        background: {theme.toolbar_section_bg};
        border: 1px solid {theme.toolbar_border};
        border-radius: 8px;
        padding: 4px 8px;
        font-size: 10px;
        font-weight: 600;
    }}
    QCheckBox#toolbarCheck {{
        color: #e5edf7;
        font-weight: 600;
        spacing: 3px;
        min-width: 44px;
        font-size: 7px;
    }}
    QCheckBox#toolbarCheck::indicator {{
        width: 12px;
        height: 12px;
        border-radius: 3px;
        border: 1px solid #475569;
        background: #111827;
    }}
    QCheckBox#toolbarCheck::indicator:checked {{
        background: #0f766e;
        border: 1px solid #5eead4;
    }}
    QPushButton {{
        background: {theme.control_bg};
        border: 1px solid {theme.control_border};
        border-radius: 7px;
        color: {theme.text_primary};
        padding: 1px 5px;
        font-weight: 600;
        font-size: 9px;
    }}
    QPushButton:hover {{
        background: {theme.control_bg_hover};
        border-color: {theme.control_border_hover};
    }}
    QPushButton:pressed {{
        background: {theme.control_bg_pressed};
        border-color: {theme.control_border_pressed};
        color: {theme.text_primary};
    }}
    QPushButton:checked {{
        background: #16a34a;
        border-color: #bbf7d0;
        color: #ffffff;
    }}
    QPushButton:checked:pressed {{
        background: #15803d;
        border-color: #fef08a;
    }}
    QPushButton:disabled {{
        background: {theme.control_disabled_bg};
        border-color: {theme.control_disabled_border};
        color: {theme.control_disabled_text};
    }}
    QToolButton {{
        background: {theme.control_bg};
        border: 1px solid {theme.control_border};
        border-radius: 7px;
        color: #e5edf7;
        padding: 0 3px;
        font-weight: 600;
        font-size: 9px;
    }}
    QToolButton[toolRole="primary"] {{
        background: {theme.primary_action_bg};
        border-color: {theme.primary_action_border};
        color: white;
        padding: 1px 5px;
    }}
    QToolButton[iconOnly="true"] {{
        padding: 0;
    }}
    QToolButton#leftSpotToolButton,
    QToolButton#toolbarPlainIconButton,
    QToolButton#spotLabelIconButton {{
        background: transparent;
        border: none;
        border-radius: 0;
        padding: 0;
    }}
    QToolButton#leftSpotToolButton:hover,
    QToolButton#toolbarPlainIconButton:hover,
    QToolButton#spotLabelIconButton:hover {{
        background: {plain_hover};
        border: none;
    }}
    QToolButton#leftSpotToolButton:pressed,
    QToolButton#toolbarPlainIconButton:pressed,
    QToolButton#spotLabelIconButton:pressed {{
        background: {plain_pressed};
        border: none;
    }}
    QToolButton#leftSpotToolButton:checked,
    QToolButton#spotLabelIconButton:checked {{
        background: {plain_checked};
        border: none;
    }}
    QToolButton#leftSpotToolButton:disabled,
    QToolButton#toolbarPlainIconButton:disabled,
    QToolButton#spotLabelIconButton:disabled {{
        background: transparent;
        border: none;
    }}
    QToolButton:checked {{
        background: #16a34a;
        border-color: #bbf7d0;
        color: white;
    }}
    QToolButton[toolRole="primary"]:checked {{
        background: {theme.primary_action_checked_bg};
        border-color: {theme.primary_action_checked_border};
    }}
    QToolButton:pressed {{
        background: {theme.control_bg_pressed};
        border-color: {theme.control_border_pressed};
    }}
    QToolButton:disabled {{
        background: {theme.control_disabled_bg};
        border-color: {theme.control_disabled_border};
        color: {theme.control_disabled_text};
    }}
    """


def section_header_label_stylesheet(theme: GuiTheme | None = None, *, top_padding: int = 4) -> str:
    theme = theme or get_active_theme()
    return f"color:{theme.text_primary}; font-weight:700; padding-top:{top_padding}px;"


def standard_push_button_stylesheet(
    theme: GuiTheme | None = None,
    *,
    padding: str = "2px 6px",
    font_size: int = 10,
) -> str:
    theme = theme or get_active_theme()
    return f"""
    QPushButton {{
        background: {theme.control_bg};
        border: 1px solid {theme.control_border};
        border-radius: 7px;
        color: {theme.text_primary};
        padding: {padding};
        font-weight: 600;
        font-size: {font_size}px;
    }}
    QPushButton:hover {{
        background: {theme.control_bg_hover};
        border-color: {theme.control_border_hover};
    }}
    QPushButton:pressed {{
        background: {theme.control_bg_pressed};
        border-color: {theme.control_border_pressed};
        color: #ffffff;
    }}
    QPushButton:checked {{
        background: #16a34a;
        border-color: #bbf7d0;
        color: #ffffff;
    }}
    QPushButton:checked:pressed {{
        background: #15803d;
        border-color: #fef08a;
    }}
    QPushButton:disabled {{
        background: {theme.control_disabled_bg};
        border-color: {theme.control_disabled_border};
        color: {theme.control_disabled_text};
    }}
    """


def startup_app_stylesheet(theme: GuiTheme | None = None) -> str:
    theme = theme or get_active_theme()
    return f"""
    QWidget {{
        background-color: {theme.window_bg};
        color: {theme.text_primary};
        font-family: "Segoe UI";
        font-size: 10pt;
    }}
    QMainWindow, QSplitter, QGroupBox, QTableWidget, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background-color: {theme.window_bg};
    }}
    QGroupBox {{
        border: 1px solid {theme.toolbar_border};
        border-radius: 10px;
        margin-top: 12px;
        padding-top: 12px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
        color: {theme.text_secondary};
    }}
    QPushButton {{
        background-color: {theme.control_bg};
        color: {theme.text_primary};
        border: 1px solid {theme.control_border};
        border-radius: 8px;
        padding: 7px 12px;
        font-weight: 700;
    }}
    QPushButton:hover {{ background-color: {theme.control_bg_hover}; border-color: {theme.control_border_hover}; }}
    QPushButton:pressed {{ background-color: {theme.control_bg_pressed}; border-color: {theme.control_border_pressed}; color: {theme.text_primary}; }}
    QPushButton:checked {{ background-color: {theme.primary_action_checked_bg}; border-color: {theme.primary_action_checked_border}; color: {theme.text_primary}; }}
    QPushButton:checked:pressed {{ background-color: {theme.primary_action_checked_bg}; border-color: {theme.primary_action_checked_border}; }}
    QPushButton:disabled {{ background-color: {theme.control_disabled_bg}; border-color: {theme.control_disabled_border}; color: {theme.control_disabled_text}; }}
    QToolButton {{
        background-color: {theme.control_bg};
        color: {theme.text_primary};
        border: 1px solid {theme.control_border};
        border-radius: 8px;
        padding: 5px 10px;
        font-weight: 700;
    }}
    QToolButton:hover {{ background-color: {theme.control_bg_hover}; border-color: {theme.control_border_hover}; }}
    QToolButton:pressed {{ background-color: {theme.control_bg_pressed}; border-color: {theme.control_border_pressed}; color: {theme.text_primary}; }}
    QToolButton:checked {{ background-color: {theme.primary_action_checked_bg}; border-color: {theme.primary_action_checked_border}; color: {theme.text_primary}; }}
    QToolButton:checked:pressed {{ background-color: {theme.primary_action_checked_bg}; border-color: {theme.primary_action_checked_border}; }}
    QToolButton:disabled {{ background-color: {theme.control_disabled_bg}; border-color: {theme.control_disabled_border}; color: {theme.control_disabled_text}; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        border: 1px solid {theme.control_border};
        border-radius: 6px;
        padding: 2px 5px;
        min-height: 18px;
        selection-background-color: {theme.primary_action_bg};
        font-size: 9pt;
    }}
    QSpinBox, QDoubleSpinBox {{
        padding-right: 18px;
    }}
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        subcontrol-origin: padding;
        width: 16px;
        background: {theme.control_bg};
        border-left: 1px solid {theme.control_border};
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{
        subcontrol-position: top right;
        border-top-right-radius: 5px;
        border-bottom: 1px solid {theme.control_border};
    }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        subcontrol-position: bottom right;
        border-bottom-right-radius: 5px;
    }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover,
    QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
        background: {theme.control_bg_hover};
    }}
    QSpinBox::up-button:pressed, QSpinBox::down-button:pressed,
    QDoubleSpinBox::up-button:pressed, QDoubleSpinBox::down-button:pressed {{
        background: {theme.control_bg_pressed};
    }}
    QSpinBox::up-arrow, QSpinBox::down-arrow,
    QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {{
        width: 7px;
        height: 7px;
    }}
    QMenuBar {{ background: {theme.toolbar_section_bg}; border-bottom: 1px solid {theme.toolbar_border}; padding: 2px 4px; }}
    QMenuBar::item {{ background: transparent; color: {theme.text_secondary}; padding: 4px 8px; margin: 0 1px; border-radius: 5px; }}
    QMenuBar::item:selected {{ background: {theme.primary_action_bg}; color: {theme.text_primary}; }}
    QMenu {{ background: {theme.toolbar_section_bg}; color: {theme.text_primary}; border: 1px solid {theme.toolbar_border}; padding: 4px; }}
    QMenu::item {{ padding: 5px 22px 5px 24px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {theme.primary_action_bg}; color: {theme.text_primary}; }}
    QMenu::separator {{ height: 1px; background: {theme.toolbar_border}; margin: 5px 8px; }}
    QScrollBar:vertical {{ background: transparent; width: 14px; margin: 4px 2px 4px 2px; }}
    QScrollBar::handle:vertical {{ background: {theme.control_border}; min-height: 28px; border-radius: 6px; }}
    QScrollBar::handle:vertical:hover {{ background: {theme.control_border_hover}; }}
    QScrollBar::handle:vertical:pressed {{ background: {theme.control_border_pressed}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; background: transparent; border: none; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ background: transparent; height: 14px; margin: 2px 4px 2px 4px; }}
    QScrollBar::handle:horizontal {{ background: {theme.control_border}; min-width: 28px; border-radius: 6px; }}
    QScrollBar::handle:horizontal:hover {{ background: {theme.control_border_hover}; }}
    QScrollBar::handle:horizontal:pressed {{ background: {theme.control_border_pressed}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; background: transparent; border: none; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    QTableWidget {{ border: 1px solid {theme.toolbar_border}; gridline-color: {theme.toolbar_border}; selection-background-color: {theme.primary_action_bg}; }}
    QHeaderView::section {{ background-color: {theme.toolbar_section_bg}; color: {theme.text_secondary}; border: none; border-right: 1px solid {theme.toolbar_border}; padding: 6px; font-weight: 600; }}
    QLabel {{ background: transparent; }}
    """
