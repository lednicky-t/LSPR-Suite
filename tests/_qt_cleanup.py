"""After every test, destroy the main windows that test created (installed once by `tests/__init__.py`).

Why: `widget.deleteLater()` only posts a request, which Qt carries out when an event loop runs, and the tests never run one (no
`.exec()`). A test window that was merely closed, or `deleteLater()`d, or not touched at all therefore stays alive for the rest of the
process, and every later `build_main_window()` has to cope with all of them. Measured 2026-10-30: the sixth window of a process took 57 s
to build instead of 2 s; one process for the whole suite needed 3 to 5 hours, and `test_lspri_rewrite_preferences` (13 windows) took
6 minutes alone instead of 35 seconds.

What it does: it wraps `unittest.TestCase.run`. Before a test it notes the top-level widgets that exist (so anything a `setUpClass` or the
module built stays), and after the test, tearDown and cleanups it closes and deletes the main windows that appeared and delivers the deletion.
It does nothing for a test file that has not imported `PyQt6.QtWidgets`, so pure-logic tests stay free of Qt.

Set `LSPR_TEST_KEEP_WINDOWS=1` to switch it off (to look at what a test left behind).
Details: docs/test_platform_and_ci_failures_2026-10-18.md, the sections of 2026-10-30.
"""

from __future__ import annotations

import os
import sys
import unittest


def destroy_new_top_level_widgets(app, existing: set[int]) -> int:
    """Close and delete every main window (`QMainWindow`) whose address is not in *existing*, deliver the deletion, return how many.

    Only main windows: they are what `build_main_window()` returns and what makes later builds slow. Other top-level widgets (a bare
    `ImagePanel`, dialogs, menus) are left alone on purpose: deleting some of them aborts the process ("QObject: shared QObject was
    deleted directly", seen 2026-10-30 in `test_lspri_rewrite_image_panel`), and none of them is a measured cost."""
    from PyQt6 import sip
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtWidgets import QMainWindow

    count = 0
    for widget in app.topLevelWidgets():
        try:
            if not isinstance(widget, QMainWindow) or sip.unwrapinstance(widget) in existing:
                continue
            widget.close()
            widget.deleteLater()
            count += 1
        except RuntimeError:  # the C++ object went away while we looked
            continue
    if count:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return count


def top_level_addresses(app) -> set[int]:
    from PyQt6 import sip

    return {sip.unwrapinstance(widget) for widget in app.topLevelWidgets()}


def install() -> None:
    """Wrap `unittest.TestCase.run` (once)."""
    original = unittest.TestCase.run
    if getattr(original, "_destroys_test_windows", False):
        return

    def run(self, result=None):
        widgets = sys.modules.get("PyQt6.QtWidgets")  # a test file that never imported Qt is left alone
        app = widgets.QApplication.instance() if widgets is not None and not os.environ.get("LSPR_TEST_KEEP_WINDOWS") else None
        if app is None or not isinstance(app, widgets.QApplication):  # a test file may run on a plain QCoreApplication: no widgets there
            return original(self, result)
        existing = top_level_addresses(app)
        try:
            return original(self, result)
        finally:
            destroy_new_top_level_widgets(app, existing)

    run._destroys_test_windows = True
    unittest.TestCase.run = run
