from __future__ import annotations

from pathlib import Path
import sys


THIS_DIR = Path(__file__).resolve().parent
APPS_DIR = THIS_DIR.parent

if str(APPS_DIR) not in sys.path:
    sys.path.insert(0, str(APPS_DIR))

from run_bootstrap import bootstrap_app_environment  # noqa: E402 - must follow the sys.path bootstrap above


bootstrap_app_environment("apps/suite_launcher/src")

from suite_launcher.app import main  # noqa: E402 - must follow bootstrap_app_environment() above


if __name__ == "__main__":
    main()
