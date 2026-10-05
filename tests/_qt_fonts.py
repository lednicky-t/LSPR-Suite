"""Real fonts for headless Qt tests that measure text.

Qt's "offscreen" platform on Windows does not connect to the system font
database (it reports 0 families) and falls back to a stand-in font with much
larger glyph widths, so `fontMetrics()`/`sizeHint()` numbers are wrong. Call
`load_system_fonts()` once after creating the `QApplication` in any test that
checks text widths or clipping. It loads the app's fonts from the Windows font
folder and does nothing where they do not exist. Widths are then close to the
real app's, not guaranteed pixel-identical (no Windows font smoothing).
"""

from __future__ import annotations

from pathlib import Path

FONT_DIR = Path("C:/Windows/Fonts")
FONT_FILES = ("segoeui.ttf", "segoeuib.ttf", "consola.ttf", "consolab.ttf")


def load_system_fonts() -> list[str]:
    """Returns the files actually loaded (empty off Windows)."""
    from PyQt6.QtGui import QFontDatabase

    loaded = []
    for name in FONT_FILES:
        path = FONT_DIR / name
        if path.is_file() and QFontDatabase.addApplicationFont(str(path)) >= 0:
            loaded.append(name)
    return loaded
