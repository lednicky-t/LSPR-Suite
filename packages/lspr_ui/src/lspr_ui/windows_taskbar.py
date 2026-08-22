from __future__ import annotations

import sys

# One constant per app, shared between the app itself (which claims its ID via
# set_windows_app_user_model_id() below) and the Suite Launcher (which stamps
# the same ID onto that app's Start Menu shortcut - see
# suite_launcher.start_menu_shortcuts). The two sides must use an identical
# string or Windows can't tell they're the same application.
APP_ID_SLSPR_ACQUISITION = "LSPR.Suite.sLSPRAcquisition"
APP_ID_SLSPR_EVALUATION = "LSPR.Suite.sLSPREvaluation"
APP_ID_LSPRI_EVALUATION = "LSPR.Suite.LSPRiEvaluation"
APP_ID_SUITE_LAUNCHER = "LSPR.Suite.Launcher"


def set_windows_app_user_model_id(app_id: str) -> None:
    """Give this process its own identity in the Windows taskbar.

    Every Suite app runs as plain ``python.exe``, so without this call
    Windows Explorer has no app-specific identity to use: it falls back to
    reading python.exe's own file properties, which is why right-clicking a
    Suite app's taskbar icon shows "Python 3.14" instead of the app's name,
    and why unrelated Suite apps can get grouped under one taskbar button.
    Setting a unique, explicit AppUserModelID here fixes both. Must be
    called once, early in ``main()``, before any window is created. No-op
    on non-Windows platforms.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass
