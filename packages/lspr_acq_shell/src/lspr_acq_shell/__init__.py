"""Shared live-acquisition shell for the LSPR Suite's acquisition apps.

See README.md and
docs/architecture/general/lspri_acq_architecture_and_shared_shell_plan.md for
what's extracted here, in what order, and what's still outstanding (§12's
delivery-milestones checklist). Only re-export names that have actually been
moved in - do not add speculative re-exports ahead of the real extraction.
"""

from __future__ import annotations

from .async_writer import AsyncTaggedWriter, WriterProtocol
from .communication_models import DeviceCommand, DeviceStatus, PortRefreshData
from .device_driver import DeviceDriver, DeviceError, DeviceTimeoutError
from .device_lifecycle import (
    DeviceLifecycleController,
    DeviceLifecycleEvent,
    DeviceLifecycleReport,
    register_device_family,
    register_post_connect_hook,
    register_primary_detector_stage,
)
from .device_manager import DeviceCommunicationService, register_driver_connect_factory
from .device_types import PUMP, SELECTOR, SWITCH
from .experiment_control_backend import (
    ExperimentControlBackend,
    ExperimentControlDeviceState,
    NullExperimentControlBackend,
)
from .experiment_control_capabilities import ExperimentControlCapabilities
from .plot_view_cache import (
    MetricCompressionBlock,
    MetricDisplayCache,
    PlotViewCache,
    downsample_metric_series_for_view,
    level_raw_weight,
    quantize_view_target_points,
    sample_absolute_metric_series_for_view,
)
from .diagnostics import (
    DiagnosticsConfig,
    DiagnosticsProfile,
    apply_diagnostic_info_filter,
)
from .settings_store import (
    get_and_clear_settings_corruption_notice,
    load_app_setting,
    load_ui_state,
    load_window_ui_state,
    read_settings_payload,
    save_app_setting,
    save_ui_state,
    save_window_ui_state,
    write_settings_payload,
)
from .user_profile import (
    DEFAULT_SETTINGS_FILENAME,
    GLOBAL_CONFIG_PATH,
    active_user,
    current_config_path,
    global_config_path,
    list_known_users,
    registry_exists,
    remove_known_user,
    safe_path_component,
    set_active_user,
    user_settings_path,
)
from .version import APP_NAME, APP_VERSION, __version__, version_string

__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "__version__",
    "version_string",
    "AsyncTaggedWriter",
    "WriterProtocol",
    "DeviceCommand",
    "DeviceStatus",
    "PortRefreshData",
    "DeviceDriver",
    "DeviceError",
    "DeviceTimeoutError",
    "DeviceLifecycleController",
    "DeviceLifecycleEvent",
    "DeviceLifecycleReport",
    "register_device_family",
    "register_post_connect_hook",
    "register_primary_detector_stage",
    "DeviceCommunicationService",
    "register_driver_connect_factory",
    "PUMP",
    "SELECTOR",
    "SWITCH",
    "ExperimentControlBackend",
    "ExperimentControlCapabilities",
    "ExperimentControlDeviceState",
    "NullExperimentControlBackend",
    "MetricCompressionBlock",
    "MetricDisplayCache",
    "PlotViewCache",
    "downsample_metric_series_for_view",
    "level_raw_weight",
    "quantize_view_target_points",
    "sample_absolute_metric_series_for_view",
    "DiagnosticsConfig",
    "DiagnosticsProfile",
    "apply_diagnostic_info_filter",
    "DEFAULT_SETTINGS_FILENAME",
    "GLOBAL_CONFIG_PATH",
    "active_user",
    "current_config_path",
    "global_config_path",
    "list_known_users",
    "registry_exists",
    "remove_known_user",
    "safe_path_component",
    "set_active_user",
    "user_settings_path",
    "get_and_clear_settings_corruption_notice",
    "load_app_setting",
    "load_ui_state",
    "load_window_ui_state",
    "read_settings_payload",
    "save_app_setting",
    "save_ui_state",
    "save_window_ui_state",
    "write_settings_payload",
]
