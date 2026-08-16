"""Lightweight per-user identity for a shared-Windows-login lab PC.

No authentication - this is bookkeeping ("who is running this instrument
right now"), not access control. Several people share one Windows login on
the instrument PC, so per-user settings isolation and HDF5 traceability
both need the app's own identity concept rather than relying on the OS
account (which today gives no separation at all).

Extracted from singleLSPR Acquisition's `storage/user_profile.py` (Phase 1,
2026-08-07) as the first real caller besides sLSPR acq showed up: LSPRimaging
Acquisition needs the same "who's logged in" concept. The user *registry*
(known/active users, `lspr_users.json`) is genuinely suite-wide - one person
using both apps on the same PC is still one person - so it stays a single
shared file. The *settings file* a user's preferences land in is NOT shared
across apps: every path-returning function below takes a `filename` (default
`lspr_settings.json`, sLSPR acq's historical name, kept as the default so
sLSPR acq's existing on-disk per-user files and call sites need no changes)
so two apps sharing one user identity don't silently read/write each other's
settings.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from platformdirs import user_config_dir

_logger = logging.getLogger(__name__)

_SHARED_CONFIG_DIR = Path(user_config_dir("lspr-suite", appauthor=False))
_REGISTRY_PATH = _SHARED_CONFIG_DIR / "lspr_users.json"

# sLSPR acq's historical settings filename - kept as the default for every
# `filename` parameter below so sLSPR acq's existing behavior and on-disk
# per-user files are unaffected by this module's extraction. A second app
# (e.g. LSPRimaging acq) should pass its own `filename` explicitly.
DEFAULT_SETTINGS_FILENAME = "lspr_settings.json"

# The pre-user settings file - unchanged path/format. Also the explicit
# global scope for the one setting (enabled_devices) that describes the
# physical rig, not a person's preference - see sLSPR acq's
# device_lifecycle.py's load_enabled_devices/save_enabled_devices, which
# pass this path directly.
GLOBAL_CONFIG_PATH = _SHARED_CONFIG_DIR / DEFAULT_SETTINGS_FILENAME


def safe_path_component(value: object, *, fallback: str = "experiment") -> str:
    """Sanitize *value* for use as a single path segment (filename or folder
    name) - strips characters Windows/POSIX filesystems reject, collapses
    whitespace, and falls back to *fallback* if nothing usable is left."""
    text = " ".join(str(value or "").strip().split())
    if not text:
        return fallback
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = text.strip(" ._")
    return text or fallback


def _read_registry() -> dict:
    try:
        return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if _REGISTRY_PATH.exists():
            _logger.warning("User registry at %s was unreadable (%s); treating as empty.", _REGISTRY_PATH, exc)
        return {}


def _write_registry(payload: dict) -> None:
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _REGISTRY_PATH.with_suffix(_REGISTRY_PATH.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(_REGISTRY_PATH)


def registry_exists() -> bool:
    return _REGISTRY_PATH.exists()


def list_known_users() -> list[str]:
    names = _read_registry().get("known_users", [])
    return [str(name) for name in names] if isinstance(names, list) else []


def active_user() -> str | None:
    name = _read_registry().get("active_user")
    return str(name) if name else None


def global_config_path(filename: str = DEFAULT_SETTINGS_FILENAME) -> Path:
    """The pre-user, app-scoped flat settings file for *filename*."""
    return _SHARED_CONFIG_DIR / filename


def user_settings_path(name: str, filename: str = DEFAULT_SETTINGS_FILENAME) -> Path:
    safe_name = safe_path_component(name, fallback="user")
    return _SHARED_CONFIG_DIR / "users" / safe_name / filename


def current_config_path(filename: str = DEFAULT_SETTINGS_FILENAME) -> Path:
    """The settings file the active user's preferences should read from
    and write to - falls back to the pre-user flat file (scoped to the same
    *filename*) if no user is active yet (shouldn't normally happen once a
    name has been entered in the recording-context row, but keeps every
    settings-store function safe to call before that)."""
    name = active_user()
    return user_settings_path(name, filename) if name else global_config_path(filename)


def set_active_user(name: str) -> None:
    """Set the active user, adding them to the known list if new.

    The very first time this is ever called (no registry file exists yet),
    also copies the pre-existing flat settings file (sLSPR acq's
    `DEFAULT_SETTINGS_FILENAME`) into this user's new per-user file, so
    nobody's current preferences silently vanish when this feature is first
    used. Later calls (switching to a different already-known user, or
    adding a second/third user) never touch the flat file again. This
    migration is specific to sLSPR acq's historical flat file - a second
    app calling this with no such legacy file simply finds nothing to
    migrate, which is the correct no-op.
    """
    name = str(name or "").strip()
    if not name:
        return
    first_run = not registry_exists()
    registry = _read_registry()
    known = registry.get("known_users", [])
    known = [str(n) for n in known] if isinstance(known, list) else []
    if name not in known:
        known.append(name)
    registry["known_users"] = known
    registry["active_user"] = name
    _write_registry(registry)
    if first_run and GLOBAL_CONFIG_PATH.exists():
        _migrate_global_settings_to(name)


def remove_known_user(name: str) -> None:
    """Remove *name* from the look-up list (Preferences > Users). Only
    removes it from the registry - never deletes that user's actual
    settings file, so nothing is lost if they were removed by mistake or
    come back later. Refuses to remove the currently active user (switch
    to someone else first)."""
    name = str(name or "").strip()
    if not name or name == active_user():
        return
    registry = _read_registry()
    known = registry.get("known_users", [])
    known = [str(n) for n in known] if isinstance(known, list) else []
    if name not in known:
        return
    registry["known_users"] = [n for n in known if n != name]
    _write_registry(registry)


def _migrate_global_settings_to(name: str) -> None:
    try:
        payload = json.loads(GLOBAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Could not migrate existing settings for new user %r: %s", name, exc)
        return
    dest = user_settings_path(name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _logger.info("Migrated existing settings into user profile: %s -> %s", GLOBAL_CONFIG_PATH, dest)
