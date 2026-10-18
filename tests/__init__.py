"""Test package. Importing it (every Qt test file does, through `tests._paths`) pins Qt to its headless platform.

Several tests compare painted pixels or measure widget sizes. On a real screen those numbers depend on the display
scale (125 % is common on laptops) and on Windows' text smoothing, so the same code passed on one machine and failed on
another. The thresholds in those tests were measured headless, so that is where they run, on every machine and in CI.

`setdefault`: to look at the tests on a real display, set `QT_QPA_PLATFORM=windows` before running them.
Headless Qt on Windows has no system fonts; a test that measures text calls `tests/_qt_fonts.py` `load_system_fonts()`.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
