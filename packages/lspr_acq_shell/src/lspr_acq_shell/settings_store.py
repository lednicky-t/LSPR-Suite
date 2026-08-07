"""Generic JSON settings-file engine: atomic writes, corruption quarantine,
an in-process read cache, and a small `ui_state`/`app` key-value convention
on top of one JSON payload per settings file.

Extracted from singleLSPR Acquisition's `storage/app_config.py` (Phase 1,
2026-08-07) - this module is the app-agnostic "lspr_settings.json-style"
plumbing; anything shaped like a specific settings schema (sLSPR acq's
`ProcessingSettings`, dark/reference spectrum cache, HDF5 export) stays
behind in the app that owns that schema and calls back into
`read_settings_payload`/`write_settings_payload` for the shared parts.

Every function below defaults its `path` parameter to
`user_profile.current_config_path()` (sLSPR acq's historical settings file)
so sLSPR acq's existing call sites are unaffected - a second app should pass
its own `path` explicitly, e.g.
`save_app_setting("theme", "dark", path=user_profile.current_config_path("lspri_acq_settings.json"))`.
"""
from __future__ import annotations

import copy
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from lspr_acq_shell.user_profile import current_config_path

_logger = logging.getLogger(__name__)


def _resolve_path(path: Path | None) -> Path:
    """Every load/save function's `path` parameter defaults to None, not a
    fixed constant - the active user can change during a run, and a
    `path: Path = current_config_path()` default would be baked in once at
    import time, never re-evaluated. Resolving here means callers that pass
    no explicit path automatically follow whichever user is active right
    now."""
    return path if path is not None else current_config_path()


# Set by _load_payload() when it has to quarantine a corrupted settings file.
# Callers check this once QApplication exists so the user sees a warning
# instead of silently getting fresh-default settings.
_last_corruption_notice: str | None = None


def get_and_clear_settings_corruption_notice() -> str | None:
    """Return (and clear) the most recent settings-file corruption notice, if any."""
    global _last_corruption_notice
    notice = _last_corruption_notice
    _last_corruption_notice = None
    return notice


def _quarantine_corrupt_file(path: Path, exc: Exception) -> Path:
    """Move an unreadable settings file aside so a fresh one can be written."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    quarantine_path = path.with_name(f"{path.name}.corrupt-{timestamp}")
    try:
        shutil.move(str(path), str(quarantine_path))
    except OSError:
        quarantine_path = path  # best effort - leave the original in place
    _logger.warning("Settings file %s was unreadable (%s); moved to %s and reset to defaults.", path, exc, quarantine_path)
    global _last_corruption_notice
    _last_corruption_notice = (
        "Your saved settings file could not be read and was reset to defaults.\n\n"
        f"The corrupted file was moved to:\n{quarantine_path}\n\n"
        f"Reason: {exc}"
    )
    return quarantine_path


# In-process cache for read_settings_payload/write_settings_payload, keyed by
# path and validated against the file's mtime. This module has many call
# sites app-wide (every load_app_setting/save_app_setting/save_window_ui_state/
# etc. call goes through here), so avoiding a disk read + full JSON parse on
# every single one is a real win. The mtime check means an external writer
# (e.g. the suite launcher's "reset settings" / "restore backup" actions,
# which touch this same file directly while the app may be running) is still
# picked up on the next call instead of being silently overwritten by a stale
# in-memory copy.
_payload_cache: dict | None = None
_payload_cache_mtime: float | None = None
_payload_cache_path: Path | None = None


def _reset_payload_cache() -> None:
    global _payload_cache, _payload_cache_mtime, _payload_cache_path
    _payload_cache = None
    _payload_cache_mtime = None
    _payload_cache_path = None


def read_settings_payload(path: Path) -> dict:
    """Read+parse *path* as JSON, returning {} if missing/corrupt.

    Always returns a deep copy of the cached payload, so callers remain free
    to mutate the result without corrupting the cache or a later save -
    matching the old no-cache behavior where every call freshly parsed the
    file from scratch.
    """
    global _payload_cache, _payload_cache_mtime, _payload_cache_path
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _reset_payload_cache()
        return {}
    if _payload_cache is not None and _payload_cache_path == path and _payload_cache_mtime == mtime:
        return copy.deepcopy(_payload_cache)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        _quarantine_corrupt_file(path, exc)
        _reset_payload_cache()
        return {}
    _payload_cache, _payload_cache_mtime, _payload_cache_path = payload, mtime, path
    return copy.deepcopy(payload)


def write_settings_payload(payload: dict, path: Path) -> None:
    """Write *payload* atomically so a crash mid-write can't corrupt the file."""
    global _payload_cache, _payload_cache_mtime, _payload_cache_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _reset_payload_cache()
        return
    _payload_cache, _payload_cache_mtime, _payload_cache_path = copy.deepcopy(payload), mtime, path


def save_ui_state(state: dict[str, object], path: Path | None = None) -> None:
    path = _resolve_path(path)
    payload = read_settings_payload(path)
    payload["ui_state"] = state
    write_settings_payload(payload, path)


def load_ui_state(path: Path | None = None) -> dict[str, object]:
    path = _resolve_path(path)
    if not path.exists():
        return {}
    payload = read_settings_payload(path)
    ui_state = payload.get("ui_state", {})
    return ui_state if isinstance(ui_state, dict) else {}


def save_window_ui_state(
    window_name: str,
    state: dict[str, object],
    path: Path | None = None,
) -> None:
    path = _resolve_path(path)
    payload = read_settings_payload(path)
    ui_state = payload.get("ui_state", {})
    if not isinstance(ui_state, dict):
        ui_state = {}
    ui_state[window_name] = state
    payload["ui_state"] = ui_state
    write_settings_payload(payload, path)


def load_window_ui_state(window_name: str, path: Path | None = None) -> dict[str, object]:
    path = _resolve_path(path)
    ui_state = load_ui_state(path)
    window_state = ui_state.get(window_name)
    if isinstance(window_state, dict):
        return window_state
    if window_name == "main_window":
        # Backward compatibility with older flat ui_state payloads.
        legacy_keys = {"x", "y", "width", "height", "maximized", "splitter_sizes"}
        if any(key in ui_state for key in legacy_keys):
            return ui_state
    return {}


def save_app_setting(
    key: str,
    value: object,
    path: Path | None = None,
) -> None:
    path = _resolve_path(path)
    payload = read_settings_payload(path)
    app_state = payload.get("app", {})
    if not isinstance(app_state, dict):
        app_state = {}
    app_state[key] = value
    payload["app"] = app_state
    write_settings_payload(payload, path)


def load_app_setting(
    key: str,
    default: object = None,
    path: Path | None = None,
) -> object:
    path = _resolve_path(path)
    payload = read_settings_payload(path)
    app_state = payload.get("app", {})
    if not isinstance(app_state, dict):
        return default
    return app_state.get(key, default)
