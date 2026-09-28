"""Regression test: every ``CollapsibleSection`` inside the Workflow panel
must fit within its fixed 340px dock width
(``docs/rewrite_gui_shell_design_2026-09.md`` §4) - the panel is a real
fixed-width tool panel now (``PanelContainer``'s ``fixed_width`` option),
not a resizable one a user could drag wider to make room for an
overlooked wide row.

Catches exactly the class of bug found 2026-09-25: three real rows/
sections (``ReferenceFrameRow``, ``MaskSettingsSection``,
``BackgroundRemovalSection``) measured 380-650px wide against a real
320px budget, invisible from reading the code - a ``QFormLayout``/
``QHBoxLayout`` with long label text just quietly requests more width
than it's given, Qt doesn't warn, it just clips or forces the whole dock
wider. Run this (or the LSPRi test subset, ``pytest tests/ -k lspri``)
after adding or editing any Workflow-panel row/section, not just when
something looks visually wrong - a headless run never shows the visual
symptom at all, only ``minimumSizeHint()`` does.

**A second, real blind spot found the same day, in this very test**: a
*collapsed* ``CollapsibleSection``'s ``minimumSizeHint()`` does not
reliably reflect its content's real width - the rebuilt Export section
measured ~140px collapsed (its default state) vs. its real ~392px content
width once actually expanded, because one checkbox's label
("Compression (lz4 + bitshuffle)") was never laid out at real size while
hidden. The first version of this test only ever measured sections in
whatever state they start in, so it would not have caught that overflow.
``test_every_collapsible_section_fits_the_width_budget`` below now force-
expands every section - the top-level stages one at a time (they're a
real single-open accordion, see ``panel.py``), each stage's nested
children all at once - before measuring anything.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.app_rewrite import build_main_window  # noqa: E402
from lspr_imaging_app.panels.dock_container import PanelContainer  # noqa: E402
from lspr_imaging_app.panels.workflow.collapsible_section import CollapsibleSection  # noqa: E402
from lspr_imaging_app.panels.workflow.panel import WorkflowPanel  # noqa: E402
from lspr_imaging_app.panels.workflow.reference_frame_row import ReferenceFrameRow  # noqa: E402

# The dock is fixed at 340px (app_rewrite.build_main_window). The budget
# checked against is intentionally narrower, leaving headroom for a
# vertical scrollbar that only appears once content overflows the current
# window height - a widget must already fit before one is ever visible, or
# the moment enough sections are expanded to need one, everything shifts.
_DOCK_WIDTH_PX = 340
_WIDTH_BUDGET_PX = 320


class WorkflowPanelWidthBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = build_main_window()
        self.window.resize(1400, 900)
        self.window.show()
        _APP.processEvents()
        docks = [dock for dock in self.window.findChildren(PanelContainer) if dock.windowTitle() == "Workflow"]
        self.workflow_dock = docks[0]
        self.workflow_panel = self.workflow_dock.findChild(WorkflowPanel)

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        _APP.processEvents()

    def test_dock_is_fixed_at_the_expected_width(self) -> None:
        # If this assumption ever changes, _WIDTH_BUDGET_PX above needs to
        # change with it - a silent drift here would make every other
        # check in this file measure against the wrong number.
        self.assertEqual(self.workflow_dock.width(), _DOCK_WIDTH_PX)

    def test_every_collapsible_section_fits_the_width_budget(self) -> None:
        """Force-expands every section before measuring - see this file's
        module docstring for why a section's *default* (often collapsed)
        state cannot be trusted to reveal an overflow."""
        too_wide: list[tuple[str, str, int]] = []
        for stage, top_section in self.workflow_panel._sections:
            top_section.set_expanded(True)
            _APP.processEvents()
            nested_sections = top_section.findChildren(CollapsibleSection)
            for nested_section in nested_sections:
                nested_section.set_expanded(True)
            _APP.processEvents()
            for section in (top_section, *nested_sections):
                width = section.minimumSizeHint().width()
                if width > _WIDTH_BUDGET_PX:
                    too_wide.append((stage.name, section._toggle.text(), width))
        self.assertEqual(too_wide, [], f"Sections wider than the {_WIDTH_BUDGET_PX}px budget: {too_wide}")

    def test_reference_frame_row_fits_with_large_cube_and_wavelength_values(self) -> None:
        """A long-running, many-cube dataset (a 4+ digit spectral-cube
        index) plus ordinary float imprecision in the wavelength is
        exactly the case that overflowed before word-wrap was added to
        this row's status label - a static layout check alone (the
        previous test) would not have caught it, since the widget starts
        out showing the short placeholder text "[Ref.frame: -]"."""
        row = self.workflow_dock.findChild(ReferenceFrameRow)
        row._selection_module.set_cube(9999)
        row._selection_module.set_wavelength(505.333333)
        row._refresh()
        _APP.processEvents()
        self.assertLessEqual(row.minimumSizeHint().width(), _WIDTH_BUDGET_PX)


if __name__ == "__main__":
    unittest.main()
