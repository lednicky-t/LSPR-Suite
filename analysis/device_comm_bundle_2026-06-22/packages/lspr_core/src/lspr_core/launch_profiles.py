from __future__ import annotations

from dataclasses import dataclass


LAUNCH_PROFILE_ENV_VAR = "LSPR_ACQ_LAUNCH_PROFILE"
LAUNCH_PROFILE_FULL = "full"
LAUNCH_PROFILE_SIMULATION = "simulation"
LAUNCH_PROFILE_CONTROL_EDITOR = "control_editor"
DEFAULT_LAUNCH_PROFILE = LAUNCH_PROFILE_FULL

_VALID_LAUNCH_PROFILE_KEYS = {
    LAUNCH_PROFILE_FULL,
    LAUNCH_PROFILE_SIMULATION,
    LAUNCH_PROFILE_CONTROL_EDITOR,
}


@dataclass(frozen=True, slots=True)
class LaunchProfileSpec:
    key: str
    label: str
    description: str
    force_simulator: bool
    scan_devices: bool
    start_live_acquisition: bool
    source_mode: str
    show_left_controls: bool
    show_sensorgram: bool
    show_runtime_controls: bool
    show_recording_context: bool
    show_device_statuses: bool
    show_source_icon: bool
    show_experiment_status: bool
    window_mode_label_text: str | None
    window_mode_label_color: str | None
    initial_top_view_mode: str


_LAUNCH_PROFILE_SPECS = {
    LAUNCH_PROFILE_FULL: LaunchProfileSpec(
        key=LAUNCH_PROFILE_FULL,
        label="Full",
        description="Run the acquisition app with the normal hardware discovery and auto-connect flow.",
        force_simulator=False,
        scan_devices=True,
        start_live_acquisition=True,
        source_mode="spectrometer",
        show_left_controls=True,
        show_sensorgram=True,
        show_runtime_controls=True,
        show_recording_context=True,
        show_device_statuses=True,
        show_source_icon=True,
        show_experiment_status=True,
        window_mode_label_text=None,
        window_mode_label_color=None,
        initial_top_view_mode="spectra",
    ),
    LAUNCH_PROFILE_SIMULATION: LaunchProfileSpec(
        key=LAUNCH_PROFILE_SIMULATION,
        label="Simulation",
        description="Skip hardware lookup at startup and launch the acquisition UI in simulation mode.",
        force_simulator=True,
        scan_devices=False,
        start_live_acquisition=True,
        source_mode="simulation",
        show_left_controls=True,
        show_sensorgram=True,
        show_runtime_controls=True,
        show_recording_context=True,
        show_device_statuses=False,
        show_source_icon=True,
        show_experiment_status=True,
        window_mode_label_text=None,
        window_mode_label_color=None,
        initial_top_view_mode="spectra",
    ),
    LAUNCH_PROFILE_CONTROL_EDITOR: LaunchProfileSpec(
        key=LAUNCH_PROFILE_CONTROL_EDITOR,
        label="Control editor",
        description="Open the experiment-control editor only, without startup device discovery or runtime control buttons.",
        force_simulator=True,
        scan_devices=False,
        start_live_acquisition=False,
        source_mode="spectrometer",
        show_left_controls=False,
        show_sensorgram=False,
        show_runtime_controls=False,
        show_recording_context=False,
        show_device_statuses=False,
        show_source_icon=False,
        show_experiment_status=False,
        window_mode_label_text="Control Plan Editor Mode",
        window_mode_label_color="#9FD7A6",
        initial_top_view_mode="flow",
    ),
}


def normalize_launch_profile(value: str | None) -> str:
    key = str(value or "").strip().lower()
    if key in _VALID_LAUNCH_PROFILE_KEYS:
        return key
    return DEFAULT_LAUNCH_PROFILE


def launch_profile_spec(value: str | None = None) -> LaunchProfileSpec:
    return _LAUNCH_PROFILE_SPECS[normalize_launch_profile(value)]


def launch_profile_specs() -> tuple[LaunchProfileSpec, ...]:
    return (
        _LAUNCH_PROFILE_SPECS[LAUNCH_PROFILE_CONTROL_EDITOR],
        _LAUNCH_PROFILE_SPECS[LAUNCH_PROFILE_SIMULATION],
        _LAUNCH_PROFILE_SPECS[LAUNCH_PROFILE_FULL],
    )
