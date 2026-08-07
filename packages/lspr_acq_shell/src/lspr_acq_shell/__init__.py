"""Shared live-acquisition shell for the LSPR Suite's acquisition apps.

See README.md and
docs/architecture/general/lspri_acq_architecture_and_shared_shell_plan.md for
what's extracted here, in what order, and what's still outstanding (§12's
delivery-milestones checklist). Only re-export names that have actually been
moved in - do not add speculative re-exports ahead of the real extraction.
"""

from __future__ import annotations

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
