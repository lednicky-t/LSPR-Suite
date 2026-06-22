from __future__ import annotations

from PyQt6.QtGui import QActionGroup, QIcon, QKeySequence
from PyQt6.QtWidgets import QMenuBar, QSizePolicy


def build_menu_bar(window) -> QMenuBar:
    menu_bar = QMenuBar()
    menu_bar.setNativeMenuBar(False)
    menu_bar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

    file_menu = menu_bar.addMenu("File")
    preferences_action = file_menu.addAction("Preferences")
    preferences_action.setToolTip("Open the application preferences dialog.")
    preferences_action.triggered.connect(window._show_preferences_dialog)

    file_menu.addSeparator()
    exit_action = file_menu.addAction("Exit")
    exit_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Quit))
    exit_action.triggered.connect(window.close)

    view_menu = menu_bar.addMenu("View")
    view_menu_actions = {"top_view": {}}
    top_view_group = QActionGroup(menu_bar)
    top_view_group.setExclusive(True)

    presets_menu = view_menu.addMenu("Presets")
    layout_preset_group = QActionGroup(menu_bar)
    layout_preset_group.setExclusive(True)
    preset_actions = {}
    for preset_key, label, callback in (
        ("spectra", "Spectra", window._apply_layout_preset),
        ("control", "Control", window._apply_layout_preset),
        ("measurement", "Measurement", window._apply_layout_preset),
    ):
        action = presets_menu.addAction(label)
        action.setCheckable(True)
        action.setActionGroup(layout_preset_group)
        action.triggered.connect(lambda _checked=False, key=preset_key, cb=callback: cb(key))
        preset_actions[preset_key] = action
    view_menu_actions["layout_presets"] = preset_actions
    presets_menu.addSeparator()
    save_preset_action = presets_menu.addAction("Save preset")
    save_preset_action.setToolTip("Save the current layout and panel visibility to the selected preset.")
    save_preset_action.triggered.connect(window._save_current_layout_to_preset)
    reset_preset_action = presets_menu.addAction("Reset default presets")
    reset_preset_action.setToolTip("Restore the built-in preset layouts.")
    reset_preset_action.triggered.connect(window._reset_layout_presets_to_defaults)

    view_menu.addSeparator()

    spectra_action = view_menu.addAction("Spectra")
    spectra_action.setCheckable(True)
    spectra_action.setActionGroup(top_view_group)
    spectra_action.setChecked(True)
    spectra_action.triggered.connect(lambda _checked=False: window._activate_spectra_view())
    view_menu_actions["top_view"]["spectra"] = spectra_action

    experimental_control_action = view_menu.addAction("Experimental control")
    experimental_control_action.setCheckable(True)
    experimental_control_action.setActionGroup(top_view_group)
    experimental_control_action.triggered.connect(lambda _checked=False: window._activate_experimental_control_view())
    view_menu_actions["top_view"]["experimental_control"] = experimental_control_action

    left_controls_action = view_menu.addAction("Tool panel")
    left_controls_action.setCheckable(True)
    left_controls_action.setChecked(True)
    left_controls_action.triggered.connect(window._toggle_left_controls)
    view_menu_actions["left_controls"] = left_controls_action

    sensorgram_action = view_menu.addAction("Sensorgram")
    sensorgram_action.setCheckable(True)
    sensorgram_action.setChecked(True)
    sensorgram_action.triggered.connect(window._toggle_sensorgram)
    view_menu_actions["sensorgram"] = sensorgram_action

    diagnostics_panel_action = view_menu.addAction("Diagnostic panel")
    diagnostics_panel_action.setCheckable(True)
    diagnostics_panel_action.setChecked(bool(getattr(window, "_diagnostics_panel_enabled", False)))
    diagnostics_panel_action.setToolTip(
        "Show or hide the log and diagnostics panel on the left side of the window."
    )
    diagnostics_panel_action.toggled.connect(window._toggle_diagnostics_panel)
    window._diagnostics_panel_action = diagnostics_panel_action

    view_menu.addSeparator()
    window._view_menu_actions = view_menu_actions

    hw_menu = menu_bar.addMenu("HW")
    hw_init_action = hw_menu.addAction("Initialize devices")
    hw_init_action.setToolTip("Scan the connected spectrometer and pump controller.")
    hw_init_action.triggered.connect(window._start_hardware_initialization)

    hw_inventory_action = hw_menu.addAction("Connected devices")
    hw_inventory_action.setToolTip("Show connected COM ports and the device type recognized for each port.")
    hw_inventory_action.triggered.connect(window._show_connected_devices_dialog)

    usb_probe_action = hw_menu.addAction("USB probe diagnostics")
    usb_probe_action.setToolTip("Show the recent USB/COM probe history, including skipped ports and probe results.")
    usb_probe_action.triggered.connect(window._show_usb_probe_diagnostics_dialog)

    hw_disconnect_all_action = hw_menu.addAction("Disconnect all devices")
    hw_disconnect_all_action.setToolTip("Stop the active devices and release all app-owned hardware connections.")
    hw_disconnect_all_action.triggered.connect(window._disconnect_all_devices)
    window._hw_disconnect_all_action = hw_disconnect_all_action

    hw_menu.addSeparator()
    device_settings_menu = hw_menu.addMenu("Device settings")
    device_settings_placeholder = device_settings_menu.addAction("Reserved for device-specific settings")
    device_settings_placeholder.setEnabled(False)

    help_menu = menu_bar.addMenu("Help")

    quick_help_action = help_menu.addAction("Quick help")
    quick_help_action.setToolTip("Show a short guide for the condensed status readouts.")
    quick_help_action.triggered.connect(window._show_quick_help_dialog)
    quick_help_action.setShortcut("F1")

    shortcuts_action = help_menu.addAction("Shortcuts")
    shortcuts_action.setToolTip("Show all keyboard shortcuts and panel tips.")
    shortcuts_action.triggered.connect(window._show_shortcuts_dialog)
    shortcuts_action.setShortcut("Ctrl+/")

    legend_action = help_menu.addAction("Diagnostics legend")
    legend_action.setToolTip("Show a short legend for the status and telemetry labels.")
    legend_action.triggered.connect(window._show_diagnostics_legend_dialog)

    debug_mode_action = help_menu.addAction("Debug mode")
    debug_mode_action.setCheckable(True)
    debug_mode_action.setChecked(bool(getattr(window, "_processing_debug_mode_enabled", False)))
    debug_mode_action.setToolTip(
        "Enable slow-spectrum profiling and other developer diagnostics.\n"
        "This is the developer path behind the Debug/Deep diagnostics profiles."
    )
    debug_mode_action.toggled.connect(window._set_processing_debug_mode_enabled)

    performance_menu = help_menu.addMenu("Performance switches")

    acquisition_autosave_action = performance_menu.addAction("Acquisition-state autosave")
    acquisition_autosave_action.setCheckable(True)
    acquisition_autosave_action.setChecked(bool(getattr(window, "_acquisition_state_autosave_enabled", True)))
    acquisition_autosave_action.setToolTip("Automatically save acquisition state during UI and acquisition changes.")
    acquisition_autosave_action.toggled.connect(window._set_acquisition_state_autosave_enabled)

    log_buffering_action = performance_menu.addAction("Log buffering")
    log_buffering_action.setCheckable(True)
    log_buffering_action.setChecked(bool(getattr(window, "_log_buffering_enabled", True)))
    log_buffering_action.setToolTip("Batch log writes before rendering them in the log panel.")
    log_buffering_action.toggled.connect(window._set_log_buffering_enabled)

    gui_housekeeping_action = performance_menu.addAction("GUI housekeeping")
    gui_housekeeping_action.setCheckable(True)
    gui_housekeeping_action.setChecked(bool(getattr(window, "_gui_housekeeping_enabled", True)))
    gui_housekeeping_action.setToolTip(
        "Enable deferred GUI maintenance tasks such as log flushing and state saves. "
        "Turn it off to isolate housekeeping overhead."
    )
    gui_housekeeping_action.toggled.connect(window._set_gui_housekeeping_enabled)

    metric_plot_action = performance_menu.addAction("Metric plot")
    metric_plot_action.setCheckable(True)
    metric_plot_action.setChecked(bool(getattr(window, "_metric_plot_enabled", True)))
    metric_plot_action.setToolTip(
        "Enable the sensorgram metric line plot. Turn it off to isolate heatmap rendering performance."
    )
    metric_plot_action.toggled.connect(window._set_metric_plot_enabled)

    help_menu.addSeparator()
    about_action = help_menu.addAction("About")
    about_action.setToolTip("Show application information.")
    about_action.triggered.connect(window._show_about_dialog)

    return menu_bar
