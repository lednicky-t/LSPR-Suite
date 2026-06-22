from __future__ import annotations

from time import perf_counter
from typing import Any

from PyQt6.QtCore import QTimer

from lspr_app.device.simulated import SimulatedSpectrometer
from lspr_app.gui.hardware_initializer import HardwareInitResult, HardwareInitStepResult, HardwareInitTask
from lspr_app.gui.main_window_state import (
    acquisition_state_payload,
    apply_acquisition_state_to_widgets,
    collapsible_section_state,
    launch_profile_settings,
    persist_acquisition_state,
    ensure_visible_top_content_splitter,
    restore_collapsible_section_state,
    restore_ui_state,
    normalize_top_content_mode,
    save_ui_state,
    schedule_acquisition_state_persist,
)
from lspr_app.gui.main_window_titlebar import refresh_hw_device_status_strip
from lspr_app.storage.app_config import save_app_setting


def restore_ui_state_for(window) -> None:
    setattr(window, "_restoring_ui_state", True)
    try:
        restore_ui_state(window)
    finally:
        setattr(window, "_restoring_ui_state", False)


def save_ui_state_for(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_ui_state_requested_at", None)
    if requested_at is not None:
        try:
            window._last_ui_state_delay_ms = max((started - float(requested_at)) * 1000.0, 0.0)
        except (TypeError, ValueError):
            window._last_ui_state_delay_ms = None

    def _callback() -> None:
        save_ui_state(window)

    window._run_gui_callback_timed("ui_state_save", _callback)
    window._last_ui_state_save_ms = (perf_counter() - started) * 1000.0
    window._ui_state_requested_at = None


def schedule_ui_state_persist_for(window) -> None:
    if getattr(window, "_restoring_ui_state", False):
        return
    if not getattr(window, "_ui_state_persistence_enabled", True):
        window._ui_state_requested_at = None
        timer = getattr(window, "_ui_state_timer", None)
        if timer is not None:
            timer.stop()
        return
    window._ui_state_requested_at = perf_counter()
    timer = getattr(window, "_ui_state_timer", None)
    if timer is not None:
        timer.start()


def collapsible_section_state_for(window) -> dict[str, bool]:
    return collapsible_section_state(window)


def restore_collapsible_section_state_for(window) -> None:
    restore_collapsible_section_state(window)


def acquisition_state_payload_for(window) -> dict[str, object]:
    return acquisition_state_payload(window)


def persist_acquisition_state_for(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_acquisition_state_requested_at", None)
    if requested_at is not None:
        try:
            window._last_acquisition_state_delay_ms = max((started - float(requested_at)) * 1000.0, 0.0)
        except (TypeError, ValueError):
            window._last_acquisition_state_delay_ms = None

    def _callback() -> None:
        persist_acquisition_state(window)

    window._run_gui_callback_timed("acquisition_state_save", _callback)
    window._last_acquisition_state_save_ms = (perf_counter() - started) * 1000.0
    window._acquisition_state_requested_at = None


def schedule_acquisition_state_persist_for(window) -> None:
    schedule_acquisition_state_persist(window)


def set_ui_state_autosave_enabled_for(window, enabled: bool) -> None:
    window._ui_state_persistence_enabled = bool(enabled)
    window._ui_state_autosave_enabled = bool(enabled)
    save_app_setting("ui_state_autosave_enabled", window._ui_state_persistence_enabled)
    timer = getattr(window, "_ui_state_timer", None)
    if not window._ui_state_persistence_enabled and timer is not None:
        timer.stop()
        window._ui_state_requested_at = None
    state_text = "enabled" if window._ui_state_persistence_enabled else "disabled"
    window._log_info(f"UI layout persistence {state_text}.")


def set_acquisition_state_autosave_enabled_for(window, enabled: bool) -> None:
    window._acquisition_state_autosave_enabled = bool(enabled)
    save_app_setting("acquisition_state_autosave_enabled", window._acquisition_state_autosave_enabled)
    timer = getattr(window, "_acquisition_state_timer", None)
    if not window._acquisition_state_autosave_enabled and timer is not None:
        timer.stop()
        window._acquisition_state_requested_at = None
    state_text = "enabled" if window._acquisition_state_autosave_enabled else "disabled"
    window._log_info(f"Acquisition state autosave {state_text}.")


def set_log_buffering_enabled_for(window, enabled: bool) -> None:
    window._log_buffering_enabled = bool(enabled)
    save_app_setting("log_buffering_enabled", window._log_buffering_enabled)
    buffer_timer = getattr(window, "_log_buffer_timer", None)
    if not window._log_buffering_enabled and buffer_timer is not None:
        if buffer_timer.isActive() or getattr(window, "_log_buffer", None):
            window._flush_log_buffer()
        buffer_timer.stop()
        window._log_buffer_requested_at = None
    state_text = "enabled" if window._log_buffering_enabled else "disabled"
    window._log_info(f"Log buffering {state_text}.")


def ensure_experiment_control_panel_for(window) -> None:
    try:
        if window._experiment_control_window is None:
            from lspr_app.gui.experiment_control_window import ExperimentControlWindow

            profile = launch_profile_settings(window)
            window._experiment_control_window = ExperimentControlWindow(
                window._experiment_control_window_ui_state,
                known_probe=window._discovered_pump_probe,
                theme_mode=window._theme_mode,
                initial_mswitch_devices=[probe for probe in window._initial_mswitch_devices if probe is not None],
                auto_connect_devices=profile.scan_devices,
                show_runtime_controls=profile.show_runtime_controls,
                parent=getattr(window, "_top_content_stack", window),
            )
            window._experiment_control_window.setVisible(False)
            window._experiment_control_window.availability_changed.connect(window._handle_flow_availability_changed)
            window._experiment_control_window.valve_availability_changed.connect(window._handle_valve_availability_changed)
            window._experiment_control_window.mswitch_availability_changed.connect(window._handle_mswitch_availability_changed)
            window._experiment_control_window.recording_control_requested.connect(window._handle_flow_recording_control)
            window._experiment_control_window.experimental_control_state_recorded.connect(window._handle_experimental_control_state_recorded)
            window._experiment_control_window.recording_controller = window
            window._experiment_control_window.theme_changed.connect(window.set_theme)
            if hasattr(window._experiment_control_window, "_set_record_with_flow_recording_active"):
                window._experiment_control_window._set_record_with_flow_recording_active(bool(window._measurement_active))
            window._experiment_control_window._ui_startup_ready = bool(getattr(window, "_ui_startup_ready", False))
            header_label = getattr(window._experiment_control_window, "_experiment_control_header_label", None)
            if header_label is not None:
                header_label.installEventFilter(window)
            sync_experiment_control_startup_ports_for(window)
            if hasattr(window, "_top_content_stack"):
                placeholder = getattr(window, "_experiment_control_panel_placeholder", getattr(window, "_flow_panel_placeholder", None))
                if placeholder is not None:
                    index = window._top_content_stack.indexOf(placeholder)
                    if index >= 0:
                        placeholder.hide()
                        window._top_content_stack.removeWidget(placeholder)
                        placeholder.setParent(None)
                        window._top_content_stack.insertWidget(index, window._experiment_control_window)
                    else:
                        window._top_content_stack.addWidget(window._experiment_control_window)
                ensure_visible_top_content_splitter(window, mode=getattr(window, "_top_view_mode", "spectra"))
            pending_layout_preset = str(getattr(window, "_pending_layout_preset_selected", "") or "").strip().lower()
            if pending_layout_preset == "measurement":
                from lspr_app.gui.main_window_state import apply_layout_preset

                def _replay_selected_layout_preset() -> None:
                    try:
                        if hasattr(window, "_log_info"):
                            window._log_info("Replaying restored measurement preset after experiment control panel creation.")
                        apply_layout_preset(window, "measurement", save=False)
                    except Exception as exc:
                        if hasattr(window, "_log_warning"):
                            window._log_warning(f"Deferred measurement preset replay failed: {exc}")

                QTimer.singleShot(0, _replay_selected_layout_preset)
            window._log_info("Experiment control panel created.")
    except Exception as exc:
        window._log_error(f"Experiment control panel creation failed: {exc}")


def ensure_flow_panel_for(window) -> None:
    ensure_experiment_control_panel_for(window)


def sync_experiment_control_startup_ports_for(window) -> None:
    try:
        hardware_init_ready = bool(object.__getattribute__(window, "_hardware_init_ready_emitted"))
    except Exception:
        hardware_init_ready = False
    if not hardware_init_ready:
        return
    profile = launch_profile_settings(window)
    if not bool(getattr(profile, "scan_devices", False)):
        return
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if experiment_control_window is None:
        return
    if hasattr(experiment_control_window, "enable_startup_device_auto_connect"):
        try:
            experiment_control_window.enable_startup_device_auto_connect()
        except Exception as exc:
            window._log_warning(f"Could not enable experiment-control startup auto-connect: {exc}")
    if not hasattr(experiment_control_window, "refresh_device_ports"):
        return
    try:
        refreshed = bool(experiment_control_window.refresh_device_ports())
    except Exception as exc:
        window._log_warning(f"Could not refresh experiment-control ports after initialization: {exc}")
    else:
        if refreshed:
            window._log_info("Refreshing experiment-control ports after initialization.")


def disconnect_all_devices_for(window) -> None:
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if experiment_control_window is None or not hasattr(experiment_control_window, "shutdown_devices"):
        window._log_info("No hardware controller window is available to disconnect.")
        return
    window._log_info("Disconnecting all hardware devices.")
    try:
        experiment_control_window.shutdown_devices()
    except Exception as exc:
        window._log_warning(f"Disconnect all devices failed: {exc}")
        return
    refresh_hw_device_status_strip(window)
    window._log_info("All hardware devices disconnected.")


def sync_hardware_menu_actions_for(window) -> None:
    action = getattr(window, "_hw_disconnect_all_action", None)
    if action is None:
        return
    action.setEnabled(window._hardware_init_task is None)


def finish_hardware_initialization_for(window, text: str = "Hardware initialization scan finished.") -> None:
    window.status_label.setText(text)
    if window._hardware_init_ready_emitted:
        return
    window._hardware_init_ready_emitted = True
    window._hardware_status_overrides.clear()
    refresh_hw_device_status_strip(window)
    window._sync_hardware_menu_actions()
    window._emit_hardware_init_progress(100, text)
    window._set_startup_loading_indicator(False)
    window.hardware_init_finished.emit()


def start_hardware_initialization_for(window) -> None:
    if window._hardware_init_task is not None:
        return
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if experiment_control_window is not None and hasattr(experiment_control_window, "shutdown_devices"):
        window._log_info("Resetting live hardware connections before reinitialization.")
        try:
            experiment_control_window.shutdown_devices()
        except Exception as exc:
            window._log_warning(f"Could not fully reset live hardware connections: {exc}")
    window._emit_hardware_init_progress(12, "Scanning connected devices...")
    window._sync_hardware_menu_actions()
    task = HardwareInitTask(window._hardware_init_steps())
    task.signals.progress.connect(window._emit_hardware_init_progress)
    task.signals.step.connect(window._handle_hardware_init_step)
    task.signals.finished.connect(window._handle_hardware_init_finished)
    window._hardware_init_task = task
    window._thread_pool.start(task)


def handle_hardware_init_step_for(window, result: object) -> None:
    if not isinstance(result, HardwareInitStepResult):
        return
    window._hardware_status_overrides[result.key] = (bool(result.connected), result.message)
    window.status_label.setText(result.message)
    if result.key == "spectrometer":
        window._hardware_available = not isinstance(window._spectrometer, SimulatedSpectrometer)
    elif result.key == "pump":
        window._discovered_pump_probe = result.probe
        window._update_pump_status(result.probe)
    elif result.key == "mswitch":
        window._initial_mswitch_devices = list(result.payload or [])
        window._mswitch_probe = result.probe if result.probe is not None else None
    refresh_hw_device_status_strip(window)


def handle_hardware_init_finished_for(window, result: object) -> None:
    window._hardware_init_task = None
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if not isinstance(result, HardwareInitResult):
        window._log_warning("Hardware initialization finished with an unexpected result payload.")
        finish_hardware_initialization_for(window, "Hardware initialization finished.")
        return
    if result.pump_probe is not None:
        window._discovered_pump_probe = result.pump_probe
        window._update_pump_status(result.pump_probe)
        window._log_info(f"Pump controller discovered on {result.pump_probe.port}.")
    elif result.pump_error:
        window._log_warning(f"Pump controller scan completed with no usable device ({result.pump_error}).")
    else:
        window._log_warning("No pump controller discovered.")

    if result.spectrometer_name:
        if isinstance(window._spectrometer, SimulatedSpectrometer):
            window._log_info(result.spectrometer_name)
        else:
            window._log_success(result.spectrometer_name)
    else:
        window._log_warning("Spectrometer initialization produced no name.")

    window._initial_mswitch_devices = list(result.mswitch_devices)
    window._mswitch_probe = result.mswitch_devices[0] if result.mswitch_devices else None
    refresh_hw_device_status_strip(window)
    if window._mswitch_probe is not None:
        window._log_info(f"M-Switch discovered on {window._mswitch_probe.port}.")
    elif result.mswitch_error:
        window._log_warning(f"M-Switch scan failed: {result.mswitch_error}")
    else:
        window._log_warning("M-Switch not discovered at startup.")

    if result.valve_probe is not None:
        window._log_info(f"Valve controller discovered on {result.valve_probe.port}.")
    elif result.valve_error:
        window._log_warning(f"Valve controller scan failed: {result.valve_error}")
    else:
        window._log_warning("No valve controller discovered.")

    window._emit_hardware_init_progress(100, "Hardware initialization complete.")
    finish_hardware_initialization_for(window, "Hardware initialization complete.")
    sync_experiment_control_startup_ports_for(window)
