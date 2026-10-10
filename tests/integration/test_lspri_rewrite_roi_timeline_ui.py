"""ROI geometry timeline in the Image panel and the ROI table: the scope toggle, edits at a cube, the context menu.

A real panel and table over a two-cube dataset; canvas events are hand-built (as in `test_lspri_rewrite_roi_gestures.py`).
Design: apps/LSPRi/eva/docs/roi_timeline_design_2026-10-08.md. One process per file.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PyQt6.QtCore import QPointF, Qt

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.app_rewrite import _build_analysis_engine
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image.roi_context_menu import APPLY_ALL_CUBES, REMOVE_CUBE_EDIT, RoiMenuChoice
    from lspr_imaging_app.panels.roi_table.panel import RoiTablePanel
    from lspr_imaging_app.panels.roi_table.rows import COLUMN_X
    from lspr_imaging_app.panels.ui_state import UiStateStore
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.model import SCOPE_INDIVIDUAL, SCOPE_PERSISTENT
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_image_panel import _pump, _write_dataset
    from tests.integration.test_lspri_rewrite_roi_gestures import _Click, _Drag
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

_MENU = "lspr_imaging_app.panels.image.interaction.show_roi_context_menu"


class RoiTimelineUiTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset_model = _write_dataset(Path(self._tmp.name))  # cubes 0 and 1
        self.dataset = DatasetModule()
        self.geometry = GeometryModule()
        self.chromatic = ChromaticModule()
        self.background = BackgroundModule()
        self.roi_toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.panel = ImagePanel(
            self.dataset, self.geometry, MaskModule(), self.chromatic, self.background, self.roi_toolbox,
            self.selection, ActiveToolModule(), ReferenceFrameModule(), HighlightRangeModule(),
            mask_scope=MaskScopeModule(), analysis_running=lambda: False,
        )
        self.panel.resize(900, 700)
        self.panel.show()
        self.dataset.load_dataset(self.dataset_model)
        _pump()
        self.panel._plot.vb.setRange(xRange=(0.0, 80.0), yRange=(0.0, 64.0), padding=0.0)
        self.panel._tool_ribbon.set_category("ROIs")
        _pump(0.1)
        for x, y in ((20.0, 20.0), (40.0, 30.0), (60.0, 45.0)):
            self.roi_toolbox.add_roi(x, y, sample_diameter_px=10.0)
        _pump(0.2)
        undo_manager.clear()
        self.engine = _build_analysis_engine(
            self.dataset, self.geometry, MaskModule(), self.chromatic, self.background, self.roi_toolbox
        )
        self.table = RoiTablePanel(self.roi_toolbox, self.selection, self.geometry, self.engine, edit_target=self.panel.edit_target)

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        self.table.close()
        self._tmp.cleanup()
        undo_manager.clear()

    # -- helpers ----------------------------------------------------------------------

    def scene(self, x: float, y: float) -> QPointF:
        return self.panel._plot.vb.mapViewToScene(QPointF(x, y))

    def drag(self, points: list[tuple[float, float]]) -> None:
        handler = self.panel._on_left_drag_event
        down = self.scene(*points[0])
        handler(_Drag(down, Qt.MouseButton.LeftButton, start=True, finish=False, down=down))
        for x, y in points[1:]:
            handler(_Drag(self.scene(x, y), Qt.MouseButton.LeftButton, start=False, finish=False, down=down))
        handler(_Drag(self.scene(*points[-1]), Qt.MouseButton.LeftButton, start=False, finish=True, down=down))

    def x_at(self, roi_id: int, cube: int) -> float:
        return round(self.roi_toolbox.geometry_at(roi_id, cube).center_x, 3)

    def go_to_cube(self, cube: int) -> None:
        self.selection.set_cube(cube)
        _pump(0.2)

    # -- scope toggle ---------------------------------------------------------------------

    def test_the_scope_toggle_drives_the_shared_edit_target(self) -> None:
        target = self.panel.edit_target
        self.assertEqual(target.scope(), SCOPE_PERSISTENT)
        self.go_to_cube(1)
        self.assertEqual(target.kwargs(), {"cube": 1, "scope": SCOPE_PERSISTENT})
        self.panel._roi_tab._scope_toggle._buttons[SCOPE_INDIVIDUAL].click()
        self.assertEqual(self.panel._roi_scope.scope(), SCOPE_INDIVIDUAL)
        self.assertEqual(target.kwargs(), {"cube": 1, "scope": SCOPE_INDIVIDUAL})
        self.go_to_cube(0)
        self.assertEqual(target.kwargs(), {"cube": 0, "scope": SCOPE_INDIVIDUAL})
        self.panel._roi_tab._scope_toggle._buttons[SCOPE_PERSISTENT].click()
        self.assertEqual(target.kwargs(), {})  # persistent on the first cube edits the base: every cube, as before

    def test_the_scope_is_remembered_across_restarts(self) -> None:
        saved: list[dict] = []
        store = UiStateStore({"roi/scope": "individual"}, on_changed=saved.append)
        self.panel.restore_ui_state(store)
        self.assertEqual(self.panel._roi_scope.scope(), SCOPE_INDIVIDUAL)
        store.flush()
        self.assertEqual(saved, [], "restoring must not save what it just read")
        self.panel._roi_tab._scope_toggle._buttons[SCOPE_PERSISTENT].click()
        store.flush()
        self.assertEqual(saved[-1]["roi/scope"], "persistent")

    # -- canvas edits ----------------------------------------------------------------------

    def test_moving_a_roi_on_the_first_cube_moves_it_on_every_cube_as_before(self) -> None:
        self.panel._selection.set_roi_selection({2})
        self.drag([(40.0, 30.0), (44.0, 30.0), (46.0, 30.0)])
        self.assertEqual([self.x_at(2, 0), self.x_at(2, 1)], [46.0, 46.0])
        self.assertFalse(self.roi_toolbox.has_timeline())

    def test_moving_a_roi_on_a_later_cube_leaves_the_first_cube_alone(self) -> None:
        self.go_to_cube(1)
        self.selection.set_roi_selection({2})
        self.drag([(40.0, 30.0), (44.0, 30.0), (46.0, 30.0)])
        self.assertEqual([self.x_at(2, 0), self.x_at(2, 1)], [40.0, 46.0])
        self.assertEqual(self.roi_toolbox.timeline_cubes(), (1,))
        undo_manager.undo()  # the whole drag is one undo step
        self.assertEqual([self.x_at(2, 0), self.x_at(2, 1)], [40.0, 40.0])
        self.assertFalse(undo_manager.can_undo)

    def test_individual_scope_changes_only_the_cube_you_are_on(self) -> None:
        self.panel._roi_tab._scope_toggle._buttons[SCOPE_INDIVIDUAL].click()
        self.selection.set_roi_selection({2})
        self.drag([(40.0, 30.0), (45.0, 30.0)])
        self.assertEqual([self.x_at(2, 0), self.x_at(2, 1)], [45.0, 40.0])

    def test_resizing_at_a_later_cube_writes_a_timeline_change_too(self) -> None:
        self.go_to_cube(1)
        self.selection.set_roi_selection({2})
        self.drag([(45.0, 30.0), (47.0, 30.0), (48.0, 30.0)])
        self.assertAlmostEqual(self.roi_toolbox.geometry_at(2, 1).sample_diameter_px, 16.0)
        self.assertAlmostEqual(self.roi_toolbox.geometry_at(2, 0).sample_diameter_px, 10.0)

    def test_the_overlay_shows_the_geometry_of_the_cube_being_viewed(self) -> None:
        self.roi_toolbox.move_roi(2, 50.0, 30.0, cube=1)
        self.selection.set_roi_selection({2})
        curve = self.panel._roi_overlay.selection_curve

        def centre_x() -> float:
            self.panel._draw_roi_overlay()
            xs = curve.getData()[0]
            return float(xs[~np.isnan(xs)].mean())

        self.assertAlmostEqual(centre_x(), 40.0, delta=0.5)
        self.go_to_cube(1)
        self.assertAlmostEqual(centre_x(), 50.0, delta=0.5)
        self.go_to_cube(0)
        self.assertAlmostEqual(centre_x(), 40.0, delta=0.5)

    def test_hit_testing_uses_the_geometry_of_the_cube(self) -> None:
        self.roi_toolbox.move_roi(2, 50.0, 30.0, cube=1)
        self.go_to_cube(1)
        self.assertEqual(self.panel.roi_at(50.0, 30.0), 2)
        self.assertIsNone(self.panel.roi_at(40.0, 30.0))
        self.go_to_cube(0)
        self.assertEqual(self.panel.roi_at(40.0, 30.0), 2)

    # -- context menu ----------------------------------------------------------------------

    def right_click(self, x: float, y: float, choice: RoiMenuChoice | None):
        with mock.patch(_MENU, return_value=choice) as menu:
            self.panel._on_scene_clicked(_Click(self.scene(x, y), Qt.MouseButton.RightButton))
        return menu

    def test_apply_to_all_cubes_and_remove_cube_edit_from_the_menu(self) -> None:
        self.go_to_cube(1)
        self.selection.set_roi_selection({2})
        self.roi_toolbox.move_roi(2, 50.0, 30.0, cube=1)
        menu = self.right_click(50.0, 30.0, RoiMenuChoice(APPLY_ALL_CUBES))
        kwargs = menu.call_args.kwargs
        self.assertTrue(kwargs["can_apply_all_cubes"])
        self.assertTrue(kwargs["can_remove_cube_edit"])
        self.assertEqual([self.x_at(2, 0), self.x_at(2, 1)], [50.0, 50.0])
        self.assertFalse(self.roi_toolbox.has_timeline())
        # remove the edit of a cube
        self.roi_toolbox.move_roi(2, 55.0, 30.0, cube=1)
        self.right_click(55.0, 30.0, RoiMenuChoice(REMOVE_CUBE_EDIT))
        self.assertEqual([self.x_at(2, 0), self.x_at(2, 1)], [50.0, 50.0])
        self.assertFalse(self.roi_toolbox.has_timeline())

    def test_the_menu_entries_are_disabled_without_a_timeline(self) -> None:
        self.selection.set_roi_selection({2})
        menu = self.right_click(40.0, 30.0, None)
        self.assertFalse(menu.call_args.kwargs["can_apply_all_cubes"])
        self.assertFalse(menu.call_args.kwargs["can_remove_cube_edit"])

    # -- the table -------------------------------------------------------------------------

    def test_the_table_shows_and_edits_the_geometry_of_the_cube(self) -> None:
        self.roi_toolbox.move_roi(2, 50.0, 30.0, cube=1)
        self.go_to_cube(1)
        self.table.refresh_now()
        self.assertEqual(self.table_x(2), 50.0)
        self.go_to_cube(0)
        self.table.refresh_now()
        self.assertEqual(self.table_x(2), 40.0)
        # typing a new x in the table at cube 1 (persistent) writes a change there, not on cube 0
        self.go_to_cube(1)
        self.table.refresh_now()
        self.table._model.setData(self.table._model.roi_index(2, COLUMN_X), "52", Qt.ItemDataRole.EditRole)
        self.assertEqual([self.x_at(2, 0), self.x_at(2, 1)], [40.0, 52.0])

    def table_x(self, roi_id: int) -> float:
        index = self.table._model.roi_index(roi_id, COLUMN_X)
        self.assertTrue(index.isValid())
        return float(self.table._model.data(index, Qt.ItemDataRole.DisplayRole))


if __name__ == "__main__":
    unittest.main()
