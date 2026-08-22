from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from lspr_ui import (
    APP_ID_LSPRI_EVALUATION,
    APP_ID_SLSPR_ACQUISITION,
    APP_ID_SLSPR_EVALUATION,
    APP_ID_SUITE_LAUNCHER,
    app_icon,
)

from .targets import SUITE_ROOT, TARGETS

_logger = logging.getLogger("suite_launcher.start_menu_shortcuts")

# (target key in TARGETS, AppUserModelID, name shown for the shortcut/jump-list)
# The AppUserModelID here must exactly match what the app itself claims via
# set_windows_app_user_model_id() in its own main() - see lspr_ui.windows_taskbar.
_APP_SHORTCUTS = (
    ("slspr_acq", APP_ID_SLSPR_ACQUISITION, "sLSPR Acquisition"),
    ("slspr_eva", APP_ID_SLSPR_EVALUATION, "sLSPR Evaluation"),
    ("lspri_eva", APP_ID_LSPRI_EVALUATION, "LSPRi Evaluation"),
)


def _start_menu_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    return root / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "LSPR Suite"


def _icon_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "LSPR" / "Suite" / "shortcut_icons"


def _save_icon_as_ico(icon, ico_path: Path, *, size: int = 256) -> bool:
    """Render a QIcon to a real .ico file on disk, if it isn't already there.

    Start Menu shortcuts need an actual .ico (or an icon embedded in an
    .exe/.dll) - the SVG-rendered QIcon used for in-app window icons can't be
    pointed to directly. The icon never changes between runs, so this only
    renders once: Explorer watches the Start Menu / linked icon files for
    changes and re-indexes them (briefly flashing an icon-thumbnail
    placeholder window) whenever they're rewritten, so touching this file on
    every single launch was producing a visible flicker for no benefit.
    """
    if ico_path.exists():
        return True
    try:
        ico_path.parent.mkdir(parents=True, exist_ok=True)
        pixmap = icon.pixmap(size, size)
        return bool(pixmap.save(str(ico_path), "ICO"))
    except Exception:
        _logger.warning("Could not render shortcut icon to %s", ico_path, exc_info=True)
        return False


def _load_shortcut(shell, pythoncom, shortcut_path: Path):
    shell_link = pythoncom.CoCreateInstance(
        shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
    )
    persist_file = shell_link.QueryInterface(pythoncom.IID_IPersistFile)
    persist_file.Load(str(shortcut_path))
    return shell_link


def _shortcut_matches(
    shell,
    pythoncom,
    propsys,
    pscon,
    *,
    shortcut_path: Path,
    target: Path,
    arguments: str,
    working_directory: Path,
    icon_path: Path,
    app_id: str,
) -> bool:
    """Whether `shortcut_path` already has exactly the values we'd write.

    Reading it back and comparing (rather than always overwriting) is what
    lets refresh_start_menu_shortcuts() stay a no-op on every launch after
    the first - see _save_icon_as_ico's docstring for why that matters.
    """
    if not shortcut_path.exists():
        return False
    try:
        shell_link = _load_shortcut(shell, pythoncom, shortcut_path)
        existing_path, _ = shell_link.GetPath(0)
        existing_icon, _ = shell_link.GetIconLocation()
        property_store = shell_link.QueryInterface(propsys.IID_IPropertyStore)
        existing_app_id = property_store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue()
        return (
            existing_path.lower() == str(target).lower()
            and shell_link.GetArguments() == arguments
            and shell_link.GetWorkingDirectory().lower() == str(working_directory).lower()
            and existing_icon.lower() == str(icon_path).lower()
            and existing_app_id == app_id
        )
    except Exception:
        return False


def _create_or_update_shortcut(
    *,
    shortcut_path: Path,
    target: Path,
    arguments: str,
    working_directory: Path,
    icon_path: Path,
    app_id: str,
    description: str,
) -> bool:
    """Create/overwrite a Start Menu .lnk stamped with `app_id`, if needed.

    This is what actually gives an app its own name and icon in Windows'
    taskbar right-click / jump-list popup: without a Start Menu shortcut
    carrying the same AppUserModelID the running process claims via
    set_windows_app_user_model_id(), Explorer has nothing to resolve that ID
    to, and falls back to reading identity straight off python.exe (hence
    the generic "Python" label). Requires pywin32; no-op if it isn't
    installed.
    """
    try:
        import pythoncom
        from win32com.propsys import propsys, pscon
        from win32com.shell import shell
    except ImportError:
        _logger.info("pywin32 not installed - skipping Start Menu shortcut for %s", shortcut_path.name)
        return False

    try:
        if _shortcut_matches(
            shell,
            pythoncom,
            propsys,
            pscon,
            shortcut_path=shortcut_path,
            target=target,
            arguments=arguments,
            working_directory=working_directory,
            icon_path=icon_path,
            app_id=app_id,
        ):
            return True

        shortcut_path.parent.mkdir(parents=True, exist_ok=True)
        shell_link = pythoncom.CoCreateInstance(
            shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
        )
        shell_link.SetPath(str(target))
        shell_link.SetArguments(arguments)
        shell_link.SetWorkingDirectory(str(working_directory))
        shell_link.SetIconLocation(str(icon_path), 0)
        shell_link.SetDescription(description[:260])

        property_store = shell_link.QueryInterface(propsys.IID_IPropertyStore)
        property_store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(app_id))
        property_store.Commit()

        persist_file = shell_link.QueryInterface(pythoncom.IID_IPersistFile)
        persist_file.Save(str(shortcut_path), 0)
        return True
    except Exception:
        _logger.warning("Could not create Start Menu shortcut %s", shortcut_path, exc_info=True)
        return False


def refresh_start_menu_shortcuts() -> None:
    """Create/refresh a "LSPR Suite" Start Menu folder with one shortcut per app.

    Purely cosmetic - fixes the taskbar right-click popup showing "Python"
    instead of each app's real name/icon (see set_windows_app_user_model_id's
    docstring for why that happens). Skips any shortcut that's already
    correct instead of rewriting it every launch - Explorer reacts to a
    rewritten Start Menu file by re-indexing it, which was showing up as a
    brief flash on every startup for no benefit once the shortcuts already
    exist. Any failure here is logged and swallowed, never allowed to block
    the launcher from starting. Windows-only; no-op elsewhere.
    """
    if sys.platform != "win32":
        return
    try:
        _refresh_start_menu_shortcuts_impl()
    except Exception:
        _logger.warning("Start Menu shortcut refresh failed", exc_info=True)


def _refresh_start_menu_shortcuts_impl() -> None:
    shared_ico = _icon_cache_dir() / "lspr_suite.ico"
    if shared_ico.exists():
        icon_ready = True
    else:
        icon_ready = _save_icon_as_ico(app_icon(), shared_ico)
    if not icon_ready:
        return

    start_menu_dir = _start_menu_dir()
    targets_by_key = {target.key: target for target in TARGETS}

    launcher_script = SUITE_ROOT / "apps" / "suite_launcher" / "run.py"
    _create_or_update_shortcut(
        shortcut_path=start_menu_dir / "LSPR Suite.lnk",
        target=Path(sys.executable),
        arguments=f'"{launcher_script}"',
        working_directory=SUITE_ROOT,
        icon_path=shared_ico,
        app_id=APP_ID_SUITE_LAUNCHER,
        description="LSPR Suite launcher",
    )

    for key, app_id, display_name in _APP_SHORTCUTS:
        target = targets_by_key.get(key)
        if target is None:
            continue
        root = target.resolve_root()
        if root is None:
            continue
        arguments = f"-m {target.module}" if target.module is not None else f'"{root / target.script}"'
        _create_or_update_shortcut(
            shortcut_path=start_menu_dir / f"{display_name}.lnk",
            target=target.resolve_python(),
            arguments=arguments,
            working_directory=root,
            icon_path=shared_ico,
            app_id=app_id,
            description=target.subtitle,
        )
