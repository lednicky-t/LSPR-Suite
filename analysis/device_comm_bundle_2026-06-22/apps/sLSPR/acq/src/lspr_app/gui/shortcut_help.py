from __future__ import annotations


def build_shortcuts_help_text() -> str:
    return (
        "Keyboard shortcuts and panel tips:\n\n"
        "General\n"
        "F1 = Quick help\n"
        "Ctrl+/ = Shortcuts and panel tips\n"
        "Ctrl+Q = Exit application\n"
        "Ctrl+S = Save processing settings\n"
        "Ctrl+O = Load processing settings\n"
        "Ctrl+E = Export current plot\n"
        "Ctrl+Space = Start or stop measurement\n\n"
        "Spectrum and sensorgram\n"
        "Ctrl+L = Clear metric history\n"
        "Ctrl+Left = Previous pump-plan step\n"
        "Ctrl+Right = Next pump-plan step\n"
        "Click a metric name = switch the displayed sensorgram metric\n"
        "Click the noise value = edit the noise window inline\n\n"
        "Experiment control\n"
        "Single click a timeline step = select it\n"
        "Double-click a timeline step = apply it to the pump\n"
        "Click the Experimental control title info icon = show these shortcuts and tips\n"
        "Use the Color, Valve, and Switch buttons in the step editor = open inline editors\n"
        "Use Tab, Page Up, and Page Down in popup editors = move through editable rows\n\n"
        "Popup editors and palettes\n"
        "Color palette dialog = add, remove, and reorder custom colors\n"
        "Valve label dialog = edit labels for open and close\n"
        "Switch solution dialog = map switch ports to solution names\n"
        "Apply = save changes and close the popup"
    )
