"""Regression test: the Workflow panel's *nested* accordion sections (e.g.
"Circles" under ROI editor) restore
across a restart, the same as the 5 top-level stage sections already do
(see `test_lspri_workflow_panel_stage_restore.py`) -
`AppSettings.expanded_subsections` / `WorkflowPanel(initial_subsections=...)`
/ `app_rewrite.py`'s `subsection_expanded_changed` -> `_persist` wiring.

Before this (2026-09-29), only the top-level stage was persisted
(`active_workflow_stage`, a single string) - every nested section always
fell back to its hardcoded `expanded=` default in `panel.py` on every
launch, regardless of what the user had open/closed last.

**Updated 2026-10-02**: "Transforms" and "Mask" (this file's original
targets) were removed from the Workflow panel's "Image tools" stage - both
are now fully covered by the Image panel's own ribbon tabs (maintainer
request: "remove the Image tools and Mask sections from the Workflow
panel, as they are fully in the Image panel"). **Updated again
2026-10-06**: the last ones left under Image tools ("Chromatic correction",
"Background removal") moved to the Image panel's ribbon too, so this now
targets "Circles" under ROI editor - the restore *mechanism* under test here is generic to
any nested section, not specific to which ones happen to still exist.
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
from lspr_imaging_app.panels.workflow.panel import WorkflowPanel  # noqa: E402
from lspr_imaging_app.storage.app_settings import AppSettings  # noqa: E402


def _workflow_panel(window: QtWidgets.QMainWindow) -> WorkflowPanel:
    docks = [dock for dock in window.findChildren(PanelContainer) if dock.windowTitle() == "Workflow"]
    return docks[0].findChild(WorkflowPanel)


class WorkflowPanelSubsectionRestoreTests(unittest.TestCase):
    def _build(self, **kwargs: object) -> QtWidgets.QMainWindow:
        window = build_main_window(**kwargs)
        self.addCleanup(window.close)
        self.addCleanup(window.deleteLater)
        window.show()
        _APP.processEvents()
        return window

    def test_no_saved_subsections_falls_back_to_each_hardcoded_default(self) -> None:
        panel = _workflow_panel(self._build())
        sections = dict(panel._subsections)
        # Hardcoded defaults from panel.py's builder functions.
        self.assertTrue(sections["ROI_SELECTION:Circles"].is_expanded())
        self.assertFalse(sections["ANALYSIS:Statistics"].is_expanded())

    def test_a_saved_subsection_state_overrides_the_hardcoded_default(self) -> None:
        window = self._build(
            initial_settings=AppSettings(expanded_subsections={"ANALYSIS:Statistics": True})
        )
        sections = dict(_workflow_panel(window)._subsections)
        self.assertTrue(sections["ANALYSIS:Statistics"].is_expanded())
        # A key with no saved entry keeps its own hardcoded default.
        self.assertTrue(sections["ROI_SELECTION:Circles"].is_expanded())

    def test_multiple_nested_sections_can_stay_open_at_once(self) -> None:
        """Unlike the 5 top-level stages (a real single-open accordion),
        nested sections within a stage have no such exclusivity - see
        `panel.py`'s module docstring."""
        window = self._build(
            initial_settings=AppSettings(
                expanded_subsections={"ANALYSIS:Statistics": True, "ROI_SELECTION:Circles": True}
            )
        )
        sections = dict(_workflow_panel(window)._subsections)
        self.assertTrue(sections["ANALYSIS:Statistics"].is_expanded())
        self.assertTrue(sections["ROI_SELECTION:Circles"].is_expanded())

    def test_an_unknown_saved_key_is_ignored_silently(self) -> None:
        """A settings file from a build with different section titles (or
        hand-edited JSON) must not crash startup."""
        window = self._build(
            initial_settings=AppSettings(expanded_subsections={"IMAGE_TOOLS:Not A Real Section": True})
        )
        # No crash is the assertion; sanity-check an ordinary section too.
        sections = dict(_workflow_panel(window)._subsections)
        self.assertTrue(sections["ROI_SELECTION:Circles"].is_expanded())

    def test_toggling_a_nested_section_persists_it(self) -> None:
        """The other half of the round trip: collapsing/expanding a nested
        section calls back into the settings layer keyed by
        `"<STAGE>:<title>"`, the same immediate-persist pattern
        `stage_changed` already uses for the top-level stage."""
        saved: list[AppSettings] = []
        window = self._build(on_settings_changed=saved.append)
        sections = dict(_workflow_panel(window)._subsections)
        sections["ANALYSIS:Statistics"].set_expanded(True)
        _APP.processEvents()
        self.assertTrue(saved)
        self.assertEqual(saved[-1].expanded_subsections.get("ANALYSIS:Statistics"), True)

    def test_toggling_two_nested_sections_accumulates_rather_than_overwriting(self) -> None:
        saved: list[AppSettings] = []
        window = self._build(on_settings_changed=saved.append)
        sections = dict(_workflow_panel(window)._subsections)
        sections["ANALYSIS:Statistics"].set_expanded(True)
        _APP.processEvents()
        sections["ROI_SELECTION:Circles"].set_expanded(False)
        _APP.processEvents()
        latest = saved[-1].expanded_subsections
        self.assertEqual(latest.get("ANALYSIS:Statistics"), True)
        self.assertEqual(latest.get("ROI_SELECTION:Circles"), False)


if __name__ == "__main__":
    unittest.main()
