"""Standalone helper that swaps in a downloaded LSPR Suite Launcher update.

Deliberately has no Qt / lspr_* dependency: it is built as its own PyInstaller
exe and lives *outside* the "LSPR Suite Launcher" folder it replaces, so it is
never one of the files being overwritten while it runs. See
apps/suite_launcher/build_portable.ps1 (builds it) and
apps/suite_launcher/src/suite_launcher/app.py (launches it) for the other half
of this flow.
"""
from __future__ import annotations

import argparse
import ctypes
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
MB_ICONERROR = 0x10


def process_is_running(pid: int) -> bool:
    if sys.platform != "win32":  # pragma: no cover - this helper only ships on Windows
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def wait_for_exit(pid: int, timeout_s: float = 30.0, poll_interval_s: float = 0.25) -> bool:
    """Wait until *pid* is no longer running. Returns False if it times out."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not process_is_running(pid):
            return True
        time.sleep(poll_interval_s)
    return not process_is_running(pid)


def extract_update(zip_path: Path, extract_dir: Path) -> Path:
    """Extract *zip_path* into *extract_dir* and return the path to the launcher folder inside it."""
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract_dir)
    candidates = [entry for entry in extract_dir.iterdir() if entry.is_dir() and entry.name == "LSPR Suite Launcher"]
    if not candidates:
        raise LookupError(f"Update zip did not contain a 'LSPR Suite Launcher' folder (found: {[p.name for p in extract_dir.iterdir()]}).")
    return candidates[0]


def swap_in_place(new_folder: Path, target: Path) -> None:
    """Replace *target*'s contents with *new_folder*, keeping *target*'s path stable.

    Renames the current folder aside first so a failure partway through can be
    rolled back; only deletes the backup once the new folder is safely in place.
    """
    backup = target.with_name(target.name + ".old")
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)

    target_existed = target.exists()
    if target_existed:
        target.rename(backup)
    try:
        shutil.move(str(new_folder), str(target))
    except Exception:
        if target_existed:
            shutil.rmtree(target, ignore_errors=True)
            backup.rename(target)
        raise
    if target_existed:
        shutil.rmtree(backup, ignore_errors=True)


def _show_error(message: str) -> None:
    if sys.platform == "win32":  # pragma: no cover - GUI call, not exercised in tests
        ctypes.windll.user32.MessageBoxW(None, message, "LSPR Suite Update", MB_ICONERROR)
    else:  # pragma: no cover
        print(message, file=sys.stderr)


def run_update(wait_pid: int, zip_path: Path, target: Path, relaunch: Path) -> bool:
    if not wait_for_exit(wait_pid):
        _show_error("LSPR Suite Launcher did not close in time. Update cancelled - please close it and try again.")
        return False

    extract_dir = zip_path.parent / "lspr_update_extracted"
    try:
        new_folder = extract_update(zip_path, extract_dir)
    except Exception as exc:
        _show_error(f"Could not unpack the update:\n{exc}")
        return False

    try:
        swap_in_place(new_folder, target)
    except Exception as exc:
        _show_error(f"Update failed, previous version was restored:\n{exc}")
        return False
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
        zip_path.unlink(missing_ok=True)

    try:
        subprocess.Popen([str(relaunch)], cwd=str(target))
    except Exception as exc:  # pragma: no cover - best-effort relaunch
        _show_error(f"Update installed, but LSPR Suite Launcher could not be restarted automatically:\n{exc}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-pid", type=int, required=True)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--relaunch", type=Path, required=True)
    args = parser.parse_args()

    succeeded = run_update(args.wait_pid, args.zip, args.target, args.relaunch)
    return 0 if succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
