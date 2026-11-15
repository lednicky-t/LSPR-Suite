"""A size budget for the biggest file of LSPRi Evaluation, `panels/image/panel.py` (maintainability audit 2026-11-01, item D).

`ImagePanel` is one class of about 180 methods that share about 120 attributes, so every feature added to it makes the next change
harder to follow. This test does not forbid growth; it makes growth a decision: when it fails, either the new code belongs in a file of its
own (the audit lists the cleanest sections to extract: `docs/maintainability_audit_2026-11-01.md`), or the budget below is raised on
purpose, in the same commit and with the reason in the commit message. When the class is split, LOWER the numbers to the new size.

Static: it reads the source text and imports nothing, so it needs no Qt and no dataset.
"""

from __future__ import annotations

import ast
import unittest

from tests._paths import REPO_ROOT

PANEL = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src" / "lspr_imaging_app" / "panels" / "image" / "panel.py"

# Measured 2026-11-02 after split batch 3 (slider marks and slideshow settings moved out): 2,630 lines in the file, 120 methods in
# `ImagePanel` (before the split: 3,492 and 178). About 3 % of room, so a small fix needs no ceremony.
# 2026-11-15: the construction code (canvas, ribbon tabs and top bar, navigation bar) moved into `canvas_build.py`, `ribbon_build.py`,
# `navigation_build.py`, the constants they share into `panel_constants.py`, and `__init__` was split into named steps: 2,093 lines, 115 methods (it was
# 2,711 and 121 an hour before; 3,492 and 178 before the audit's item D). The budget follows the new size with about 3 % of room.
MAX_FILE_LINES = 2150
MAX_IMAGE_PANEL_METHODS = 118


@unittest.skipUnless(PANEL.is_file(), "LSPRi Evaluation is not in this checkout")
class ImagePanelSizeBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = PANEL.read_text(encoding="utf-8")

    def test_the_file_stays_inside_its_line_budget(self) -> None:
        lines = len(self.source.splitlines())
        self.assertLessEqual(
            lines, MAX_FILE_LINES,
            f"panels/image/panel.py has {lines} lines, the budget is {MAX_FILE_LINES}. Put the new code in a file of its own, or raise "
            "MAX_FILE_LINES in this test on purpose (see the docstring).",
        )

    def test_image_panel_stays_inside_its_method_budget(self) -> None:
        tree = ast.parse(self.source)
        image_panel = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ImagePanel")
        methods = sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in image_panel.body)
        self.assertLessEqual(
            methods, MAX_IMAGE_PANEL_METHODS,
            f"ImagePanel has {methods} methods, the budget is {MAX_IMAGE_PANEL_METHODS}. Put the new behaviour in a class of its own, or "
            "raise MAX_IMAGE_PANEL_METHODS in this test on purpose (see the docstring).",
        )


if __name__ == "__main__":
    unittest.main()
