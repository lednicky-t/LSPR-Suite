from __future__ import annotations

from pathlib import Path
import sys


if not getattr(sys, "frozen", False):
    # Only needed for `python run.py` from a source checkout: sets up sys.path
    # for the shared packages and this app's own src/ layout. A PyInstaller
    # build already bundles all of that directly (see build_portable.ps1's
    # --paths), and apps/run_bootstrap.py itself isn't part of the frozen
    # bundle, so importing it here would fail.
    THIS_DIR = Path(__file__).resolve().parent
    APPS_DIR = THIS_DIR.parent

    if str(APPS_DIR) not in sys.path:
        sys.path.insert(0, str(APPS_DIR))

    from run_bootstrap import bootstrap_app_environment

    bootstrap_app_environment("apps/suite_launcher/src")

from suite_launcher.app import main  # noqa: E402 - must follow the bootstrap above when unfrozen


if __name__ == "__main__":
    main()
