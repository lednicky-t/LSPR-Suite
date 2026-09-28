"""Regression test: the Workflow panel's top-level accordion section
restores across a restart (2026-09-28) - `AppSettings.active_workflow_stage`
/ `WorkflowPanel(initial_stage=...)` / `app_rewrite.py`'s `stage_changed` ->
`_persist` wiring.

Before this, `panels/workflow/panel.py` hardcoded which of the 5 top-level
stage sections (Dataset / Image tools / ROI editor / Analysis / Outputs)
started expanded - every launch always opened with Dataset open and
everything else collapsed, with nothing recording which one the user had
open when they last quit.
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
from lspr_imaging_app.panels.workflow.panel import WorkflowPanel, WorkflowStage  # noqa: E402
from lspr_imaging_app.storage.app_settings import AppSettings  # noqa: E402


def _workflow_panel(window: QtWidgets.QMainWindow) -> WorkflowPanel:
    docks = [dock for dock in window.findChildren(PanelContainer) if dock.windowTitle() == "Workflow"]
    return docks[0].findChild(WorkflowPanel)


class WorkflowPanelStageRestoreTests(unittest.TestCase):
    def _build(self, **kwargs: object) -> QtWidgets.QMainWindow:
        window = build_main_window(**kwargs)
        self.addCleanup(window.close)
        self.addCleanup(window.deleteLater)
        window.show()
        _APP.processEvents()
        return window

    def test_no_saved_stage_falls_back_to_dataset_open(self) -> None:
        panel = _workflow_panel(self._build())
        expanded = dict(panel._sections)
        self.assertTrue(expanded[WorkflowStage.DATASET].is_expanded())
        for stage, section in panel._sections:
            if stage is not WorkflowStage.DATASET:
                self.assertFalse(section.is_expanded(), f"{stage} should start collapsed")

    def test_a_saved_stage_opens_instead_of_the_dataset_default(self) -> None:
        window = self._build(initial_settings=AppSettings(active_workflow_stage="ANALYSIS"))
        panel = _workflow_panel(window)
        sections = dict(panel._sections)
        self.assertTrue(sections[WorkflowStage.ANALYSIS].is_expanded())
        self.assertFalse(sections[WorkflowStage.DATASET].is_expanded())
        # Still a real single-open accordion, not two sections left open.
        self.assertEqual(sum(section.is_expanded() for section in sections.values()), 1)

    def test_an_unknown_saved_stage_name_falls_back_silently(self) -> None:
        """A settings file from a build with different stage names (or
        hand-edited JSON) must not crash startup - see `app_rewrite.py`'s
        `WorkflowStage[...]` lookup guard."""
        window = self._build(initial_settings=AppSettings(active_workflow_stage="NOT_A_REAL_STAGE"))
        panel = _workflow_panel(window)
        self.assertTrue(dict(panel._sections)[WorkflowStage.DATASET].is_expanded())

    def test_switching_stage_persists_it(self) -> None:
        """The other half of the round trip: opening a different top-level
        section calls back into the settings layer with its name, the same
        immediate-persist pattern as theme/auto-apply
        (`app_rewrite.py`'s `workflow.stage_changed` wiring)."""
        saved: list[AppSettings] = []
        window = self._build(on_settings_changed=saved.append)
        panel = _workflow_panel(window)
        dict(panel._sections)[WorkflowStage.OUTPUTS].set_expanded(True)
        _APP.processEvents()
        self.assertTrue(saved)
        self.assertEqual(saved[-1].active_workflow_stage, "OUTPUTS")


if __name__ == "__main__":
    unittest.main()
