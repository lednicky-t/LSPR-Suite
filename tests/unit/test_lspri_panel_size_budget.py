"""Size budgets for the biggest files of LSPRi Evaluation (maintainability audit 2026-11-01 item D; maintenance pass 2026-11-25).

`ImagePanel` was one class of about 180 methods that shared about 120 attributes, so every feature added to it made the next change
harder to follow. The 2026-11-25 maintenance pass found the same growth in three more places: `roi/toolbox.py` (`RoiToolbox`, +650 lines
in three weeks), `app_rewrite.py` (the wiring of the whole app, +500) and `panels/roi_table/panel.py` (`RoiTablePanel`, +370).

These tests do not forbid growth; they make growth a decision: when one fails, either the new code belongs in a file of its own, or the
budget below is raised on purpose, in the same commit and with the reason in the commit message. When a file is split, LOWER its numbers
to the new size. Each budget leaves about 3 % of room, so a small fix needs no ceremony.

Static: it reads the source text and imports nothing, so it needs no Qt and no dataset.
"""

from __future__ import annotations

import ast
import unittest
from dataclasses import dataclass

from tests._paths import REPO_ROOT

SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src" / "lspr_imaging_app"


@dataclass(frozen=True)
class Budget:
    path: str  # relative to the package
    max_lines: int
    cls: str | None  # the class whose methods are counted; None = the module's top-level functions
    max_methods: int
    note: str  # where new code should go instead


BUDGETS = (
    # 2026-11-02: 2,630 lines / 120 methods after split batch 3 (3,492 / 178 before the audit's item D). 2026-11-15: the construction code
    # moved into canvas_build.py, ribbon_build.py, navigation_build.py (2,093 / 115). Measured 2026-11-25: 2,020 / 116.
    Budget("panels/image/panel.py", 2150, "ImagePanel", 118,
           "a file of its own with a small builder (the pattern of panels/image/selection_tools.py)"),
    # Measured 2026-11-25: 2,399 lines, 108 methods (1,748 lines on 2026-11-02). Raised on purpose the same day to 2,500 / 113 for the array finder's command
    # (`place_found_array`) plus the helpers it shares with `stamp_array` (`_next_array_slot`, `_commit_array_plan`: stamp_array's undo code moved there, not copied).
    Budget("roi/toolbox.py", 2500, "RoiToolbox", 114,
           "a pure helper module under roi/ or domain/ that the toolbox calls (the toolbox keeps the command, undo and signal)"),
    # Measured 2026-11-25: 2,483 lines, 47 top-level functions (1,983 lines on 2026-11-02).
    Budget("app_rewrite.py", 2560, None, 49,
           "a wiring helper module next to it (the pattern of the startup tracker), called from build_main_window"),
    # Measured 2026-11-25: 1,492 lines, 97 methods (1,120 lines on 2026-11-02).
    Budget("panels/roi_table/panel.py", 1540, "RoiTablePanel", 100,
           "a helper in panels/roi_table/ (the pattern of freeze.py, hide_columns.py, step_controls.py)"),
)


def _methods(tree: ast.Module, cls: str | None) -> int:
    body = tree.body if cls is None else next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == cls).body
    return sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in body)


@unittest.skipUnless((SRC / "app_rewrite.py").is_file(), "LSPRi Evaluation is not in this checkout")
class SizeBudgetTests(unittest.TestCase):
    def test_each_file_stays_inside_its_line_budget(self) -> None:
        for budget in BUDGETS:
            with self.subTest(path=budget.path):
                lines = len((SRC / budget.path).read_text(encoding="utf-8").splitlines())
                self.assertLessEqual(
                    lines, budget.max_lines,
                    f"{budget.path} has {lines} lines, the budget is {budget.max_lines}. Put the new code in {budget.note}, or raise "
                    "the budget in this test on purpose (see the docstring).",
                )

    def test_each_class_stays_inside_its_method_budget(self) -> None:
        for budget in BUDGETS:
            with self.subTest(path=budget.path):
                tree = ast.parse((SRC / budget.path).read_text(encoding="utf-8"))
                methods = _methods(tree, budget.cls)
                what = budget.cls or f"{budget.path} (top-level functions)"
                self.assertLessEqual(
                    methods, budget.max_methods,
                    f"{what} has {methods} methods, the budget is {budget.max_methods}. Put the new behaviour in {budget.note}, or "
                    "raise the budget in this test on purpose (see the docstring).",
                )


if __name__ == "__main__":
    unittest.main()
