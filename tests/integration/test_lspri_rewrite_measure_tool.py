"""Tests for the LSPRimaging Evaluation rewrite's measurement/calibration
tool (`panels/image/measure_line_tool.py`), the floating calibration
controls it drives (`panels/image/measure_controls.py`), and the Workflow
panel's Measure button (`panels/workflow/transforms_settings.py`).

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring.

Same split and conventions as `test_lspri_rewrite_rotate_tool.py`/
`test_lspri_rewrite_crop_tool.py`: driven by direct method calls
(`MeasureLineTool.on_left_click`, `ImagePanel._on_scene_clicked`/
`_on_measure_drag_event` with small stand-in events, real `QToolButton.
click()`) and signal emissions, never screen coordinates. The calibration
math itself (the symmetric/asymmetric-axis fallback, the three `ValueError`
guards) is pinned in `tests/unit/test_lspri_rewrite_measurement_
calibration.py`; what is pinned here is the wiring: the two-click
placement, that dragging an already-placed point (added 2026-09-29 after
the maintainer tried the click-only version) updates the anchors live
without resetting typed um values or pushing an undo step, that placing a
ruler is not an undo step but applying it is, the floating controls'
square-pixel auto-calc of the sibling axis, and that the tool is mutually
exclusive with Rotate/Crop.

**No unit-toggle tests here** - the px/um/mm toggle was built, tried, and
removed the same day (2026-09-29); `GeometryModule.set_display_units`
reverted to px/um-only and its own reversal is pinned in
`test_lspri_rewrite_measurement_calibration.py` instead.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PyQt6 import QtWidgets
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QAction

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        ImageTool,
        MaskModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.workflow.transforms_settings import TransformsSection
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402
import tifffile  # noqa: E402


def _pump(seconds: float = 0.5) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)


def _pick_first_enabled_action(menu: QtWidgets.QMenu, *_args: object, **_kwargs: object) -> QAction | None:
    for action in menu.actions():
        if action.isEnabled():
            return action
    return None


def _write_dataset(root: Path) -> ImageDataset:
    rng = np.random.default_rng(5)
    path = root / "c0_w500.tif"
    tifffile.imwrite(str(path), rng.uniform(100.0, 200.0, size=(64, 80)).astype(np.float32))
    record = ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)
    return ImageDataset(folder=root, records=[record], source_format="image_stack")


class _Click:
    """The slice of pyqtgraph's MouseClickEvent the panel reads."""

    def __init__(self, scene_pos: QPointF, button: Qt.MouseButton) -> None:
        self._pos, self._button = scene_pos, button

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._pos

    def button(self) -> Qt.MouseButton:
        return self._button

    def modifiers(self) -> Qt.KeyboardModifier:
        return Qt.KeyboardModifier.NoModifier


class _Drag:
    """The slice of pyqtgraph's MouseDragEvent `_on_measure_drag_event`
    reads - same shape as `test_lspri_rewrite_crop_tool.py`'s own `_Drag`."""

    def __init__(self, scene_pos: QPointF, *, start: bool, finish: bool) -> None:
        self._pos, self._start, self._finish = scene_pos, start, finish

    def scenePos(self) -> QPointF:  # noqa: N802
        return self._pos

    def isStart(self) -> bool:  # noqa: N802 - Qt/pyqtgraph naming
        return self._start

    def isFinish(self) -> bool:  # noqa: N802 - Qt/pyqtgraph naming
        return self._finish


class MeasureToolTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset_model = _write_dataset(Path(self._tmp.name))

        self.dataset = DatasetModule()
        self.geometry = GeometryModule()
        self.active_tool = ActiveToolModule()
        self.roi_toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.panel = ImagePanel(
            self.dataset, self.geometry, MaskModule(), ChromaticModule(), BackgroundModule(),
            self.roi_toolbox, self.selection, self.active_tool, ReferenceFrameModule(),
        )
        self.tool = self.panel._measure_tool
        self.controls = self.panel._measure_controls
        self.status_messages: list[str] = []
        self.panel.tool_status_changed.connect(self.status_messages.append)
        self.enterContext(mock.patch.object(QtWidgets.QMenu, "exec", _pick_first_enabled_action))
        self.panel.resize(900, 700)
        self.panel.show()
        self.dataset.load_dataset(self.dataset_model)
        _pump()
        self.panel._plot.vb.setRange(xRange=(0.0, 80.0), yRange=(0.0, 64.0), padding=0.0)
        _pump(0.1)

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self.panel.close()
        self._tmp.cleanup()
        undo_manager.clear()

    def _click(self, x: float, y: float, button: Qt.MouseButton = Qt.MouseButton.LeftButton) -> None:
        vb = self.panel._plot.vb
        scene_pos = vb.mapViewToScene(QPointF(x, y))
        self.panel._on_scene_clicked(_Click(scene_pos, button))

    def _drag_event(self, x: float, y: float, *, start: bool = False, finish: bool = False) -> _Drag:
        scene_pos = self.panel._plot.vb.mapViewToScene(QPointF(x, y))
        return _Drag(scene_pos, start=start, finish=finish)

    def _move(self, x: float, y: float) -> None:
        """A plain mouse move (no button), routed the way pyqtgraph would -
        through `ImagePanel._on_scene_moved`, same as `_click` does for
        `_on_scene_clicked`."""
        scene_pos = self.panel._plot.vb.mapViewToScene(QPointF(x, y))
        self.panel._on_scene_moved(scene_pos)

    def assertPointAlmostEqual(self, actual, expected) -> None:  # noqa: N802
        # Clicks travel view -> scene -> view, which is not bit-exact.
        self.assertIsNotNone(actual)
        np.testing.assert_allclose(actual, expected, atol=1e-6)

    # -- two-click gesture ----------------------------------------------

    def test_clicks_do_nothing_until_the_tool_is_active(self) -> None:
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        self.assertIsNone(self.tool.first_point())
        self.assertIsNone(self.tool.current_anchor_point())

    def test_two_clicks_place_a_ruler_and_show_the_controls(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self.assertPointAlmostEqual(self.tool.first_point(), (10.0, 20.0))
        self._click(70.0, 22.0)  # dx=60, dy=2
        self.assertIsNone(self.tool.first_point())  # ready for another pair
        self.assertPointAlmostEqual(self.tool.current_anchor_point(), (70.0, 22.0))
        settings = self.geometry.settings()
        self.assertPointAlmostEqual(
            (settings.measurement_anchor1_x_px, settings.measurement_anchor1_y_px),
            (10.0, 20.0),
        )
        self.assertPointAlmostEqual(
            (settings.measurement_anchor2_x_px, settings.measurement_anchor2_y_px),
            (70.0, 22.0),
        )
        self.assertTrue(self.controls.isVisible())

    # -- live preview while placing point 2 (before it's clicked) -----------

    def test_hovering_after_point_one_shows_live_controls(self) -> None:
        """Maintainer's spec, 2026-09-29: "add the fields also during the
        first drag before the second point is placed"."""
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self.assertFalse(self.controls.isVisible())  # nothing to show yet - no movement since point 1
        self._move(70.0, 22.0)  # dx=60, dy=2 from point 1 at (10, 20)
        self.assertTrue(self.controls.isVisible())
        self.assertIn("60.0", self.controls._dx_px_label.text())
        self.assertIn("2.0", self.controls._dy_px_label.text())

    def test_hovering_live_updates_geometry_anchors_before_point_two_is_clicked(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self._move(70.0, 22.0)
        settings = self.geometry.settings()
        self.assertPointAlmostEqual(
            (settings.measurement_anchor2_x_px, settings.measurement_anchor2_y_px), (70.0, 22.0)
        )

    def test_apply_works_from_a_hover_preview_without_clicking_point_two(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._move(60.0, 0.0)  # never clicked - just hovering
        self.panel._on_measure_apply_requested(50.0, 0.0)
        settings = self.geometry.settings()
        self.assertTrue(settings.calibration_enabled)
        self.assertAlmostEqual(settings.microns_per_pixel_x, 50.0 / 60.0)

    def test_placing_point_one_resets_the_controls_once(self) -> None:
        """The `measured(0, 0, True)` reset fires exactly once, at point 1 -
        not again when point 2 is clicked (which would wipe out anything
        typed while hovering)."""
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self._move(70.0, 22.0)
        self.controls._dx_um_spin.setValue(123.0)
        self.controls._dx_um_spin.editingFinished.emit()
        self._click(70.0, 22.0)  # commits point 2 at the same spot
        self.assertAlmostEqual(self.controls._dx_um_spin.value(), 123.0)  # not reset by the commit

    def test_hovering_before_point_one_does_nothing(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._move(40.0, 30.0)
        self.assertIsNone(self.tool.current_anchor_point())
        self.assertFalse(self.controls.isVisible())

    def test_placed_line_stays_visible_after_the_second_click(self) -> None:
        """Unlike Rotate's rubber band (which disappears the instant it
        applies a rotation), the placed ruler stays on screen - there is
        nothing to compare the typed distance against otherwise."""
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        self.assertTrue(self.tool._placed_line.isVisible())
        xs, ys = self.tool._placed_line.getData()
        np.testing.assert_allclose([list(xs), list(ys)], [[10.0, 70.0], [20.0, 22.0]], atol=1e-6)

    def test_starting_a_new_pair_hides_the_previous_placed_line(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        self._click(5.0, 5.0)  # point 1 of a new pair
        self.assertFalse(self.tool._placed_line.isVisible())
        self.assertIsNone(self.tool.current_anchor_point())

    def test_placing_a_ruler_is_not_an_undo_step(self) -> None:
        """`set_measurement_anchors` is cosmetic, not undo-tracked - a
        prior real edit (rotation) must be the only thing one undo() call
        reverts, leaving the freshly placed anchors untouched."""
        self.geometry.set_rotation(5.0)
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        undo_manager.undo()
        self.assertEqual(self.geometry.settings().rotation_angle_deg, 0.0)
        self.assertAlmostEqual(self.geometry.settings().measurement_anchor2_x_px, 70.0)

    def test_right_click_menu_cancel_action_exits_measure_mode(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self._click(0.0, 0.0, Qt.MouseButton.RightButton)
        self.assertIsNone(self.active_tool.active())
        self.assertIsNone(self.tool.first_point())

    def test_escape_cancels_point_one_only(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self.assertTrue(self.tool.handle_key(Qt.Key.Key_Escape))
        self.assertIsNone(self.tool.first_point())
        self.assertIs(self.active_tool.active(), ImageTool.MEASURE)  # Esc stays in Measure mode

    def test_deactivating_drops_the_placed_ruler(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        self.active_tool.set_active(ImageTool.MEASURE, False)
        self.assertIsNone(self.tool.current_anchor_point())
        self.assertFalse(self.controls.isVisible())

    def test_reactivating_does_not_show_a_stale_placement(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        self.active_tool.set_active(ImageTool.MEASURE, False)
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self.assertFalse(self.controls.isVisible())

    def test_clicks_never_select_rois_while_measuring(self) -> None:
        self.roi_toolbox.add_roi(40.0, 30.0, sample_radius_px=5.0)
        _pump()
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(40.0, 30.0)
        self.assertEqual(self.selection.selected_roi_ids(), set())

    # -- dragging an already-placed point ----------------------------------

    def test_dragging_near_point_two_moves_it(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        self.assertTrue(self.panel._on_measure_drag_event(self._drag_event(70.0, 22.0, start=True)))
        self.panel._on_measure_drag_event(self._drag_event(80.0, 30.0))
        self.panel._on_measure_drag_event(self._drag_event(80.0, 30.0, finish=True))
        self.assertPointAlmostEqual(self.tool.current_anchor_point(), (80.0, 30.0))
        self.assertPointAlmostEqual(
            (self.geometry.settings().measurement_anchor2_x_px, self.geometry.settings().measurement_anchor2_y_px),
            (80.0, 30.0),
        )

    def test_dragging_near_point_one_moves_it_not_point_two(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        self.panel._on_measure_drag_event(self._drag_event(10.0, 20.0, start=True))
        self.panel._on_measure_drag_event(self._drag_event(0.0, 5.0))
        self.panel._on_measure_drag_event(self._drag_event(0.0, 5.0, finish=True))
        settings = self.geometry.settings()
        self.assertPointAlmostEqual((settings.measurement_anchor1_x_px, settings.measurement_anchor1_y_px), (0.0, 5.0))
        self.assertPointAlmostEqual(self.tool.current_anchor_point(), (70.0, 22.0))  # untouched

    def test_drag_away_from_either_point_is_not_claimed(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        self.assertFalse(self.panel._on_measure_drag_event(self._drag_event(40.0, 40.0, start=True)))

    def test_drag_before_a_pair_is_placed_is_not_claimed(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self.assertFalse(self.panel._on_measure_drag_event(self._drag_event(10.0, 20.0, start=True)))

    def test_dragging_does_not_reset_typed_um_fields(self) -> None:
        """Unlike a fresh two-click placement, a drag refinement must not
        throw away a distance the maintainer already typed in."""
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        self.controls._dx_um_spin.setValue(123.0)
        self.panel._on_measure_drag_event(self._drag_event(70.0, 22.0, start=True))
        self.panel._on_measure_drag_event(self._drag_event(80.0, 30.0))
        self.panel._on_measure_drag_event(self._drag_event(80.0, 30.0, finish=True))
        self.assertEqual(self.controls._dx_um_spin.value(), 123.0)

    def test_dragging_is_not_an_undo_step(self) -> None:
        self.geometry.set_rotation(5.0)
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        self.panel._on_measure_drag_event(self._drag_event(70.0, 22.0, start=True))
        self.panel._on_measure_drag_event(self._drag_event(80.0, 30.0))
        self.panel._on_measure_drag_event(self._drag_event(80.0, 30.0, finish=True))
        undo_manager.undo()
        self.assertEqual(self.geometry.settings().rotation_angle_deg, 0.0)
        self.assertPointAlmostEqual(self.tool.current_anchor_point(), (80.0, 30.0))

    def test_hover_cursor_is_move_cursor_over_a_placed_point(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        scene_pos = self.panel._plot.vb.mapViewToScene(QPointF(70.0, 22.0))
        self.panel._on_scene_moved(scene_pos)
        self.assertEqual(self.panel._view.viewport().cursor().shape(), Qt.CursorShape.SizeAllCursor)
        away = self.panel._plot.vb.mapViewToScene(QPointF(40.0, 40.0))
        self.panel._on_scene_moved(away)
        self.assertNotEqual(self.panel._view.viewport().cursor().shape(), Qt.CursorShape.SizeAllCursor)

    def test_apply_button_lands_under_point_two(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 20.0)
        self._click(70.0, 22.0)
        expected_scene = self.panel._plot.vb.mapViewToScene(QPointF(70.0, 22.0))
        expected_view_x = self.panel._view.mapFromScene(expected_scene).x()
        apply_x_in_view = self.controls.x() + self.controls.apply_button_center_x()
        self.assertAlmostEqual(apply_x_in_view, expected_view_x, delta=2)

    # -- apply calibration ------------------------------------------------

    def test_apply_calibrates_as_one_undo_step(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(60.0, 0.0)  # dx_px = 60
        self.panel._on_measure_apply_requested(50.0, 0.0)  # 50 um over 60 px
        settings = self.geometry.settings()
        self.assertTrue(settings.calibration_enabled)
        self.assertAlmostEqual(settings.microns_per_pixel_x, 50.0 / 60.0)
        self.assertAlmostEqual(settings.microns_per_pixel_y, 50.0 / 60.0)  # symmetric fallback
        self.assertEqual(settings.display_units, "um")
        undo_manager.undo()
        self.assertFalse(self.geometry.settings().calibration_enabled)

    def test_apply_with_no_real_distance_reports_status_and_changes_nothing(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(60.0, 0.0)
        self.panel._on_measure_apply_requested(0.0, 0.0)
        self.assertFalse(self.geometry.settings().calibration_enabled)
        self.assertIn("Enter a real dx", self.status_messages[-1])

    def test_apply_switches_display_units_to_micrometers(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(60.0, 0.0)
        self.panel._on_measure_apply_requested(50.0, 0.0)
        self.assertEqual(self.geometry.settings().display_units, "um")

    def test_apply_exits_measure_mode(self) -> None:
        """Maintainer's spec, 2026-09-29: "when click on apply, the tool
        will not disappear... it should cancel and settings applied" -
        matches Crop's own apply-exits-the-tool convention."""
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(60.0, 0.0)
        self.panel._on_measure_apply_requested(50.0, 0.0)
        self.assertIsNone(self.active_tool.active())
        self.assertFalse(self.controls.isVisible())

    def test_a_failed_apply_stays_in_measure_mode(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(60.0, 0.0)
        self.panel._on_measure_apply_requested(0.0, 0.0)  # rejected - no real distance
        self.assertIs(self.active_tool.active(), ImageTool.MEASURE)

    # -- absolute values ----------------------------------------------------

    def test_px_labels_never_show_a_negative_number(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(60.0, 40.0)
        self._click(10.0, 5.0)  # dx_px=-50, dy_px=-35
        self.assertNotIn("-", self.controls._dx_px_label.text())
        self.assertNotIn("-", self.controls._dy_px_label.text())
        self.assertIn("50.0", self.controls._dx_px_label.text())
        self.assertIn("35.0", self.controls._dy_px_label.text())

    # -- live readout once a calibration already exists ----------------------

    def test_placing_a_new_ruler_shows_the_calibrated_distance_when_already_calibrated(self) -> None:
        self.geometry.set_measurement_anchors(0.0, 0.0, 50.0, 0.0)
        self.geometry.apply_measurement_calibration(dx_um=100.0, dy_um=0.0)  # 2 um/px, both axes (symmetric)
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(30.0, 20.0)  # dx_px=30, dy_px=20
        self.assertAlmostEqual(self.controls._dx_um_spin.value(), 60.0)  # 30 * 2
        self.assertAlmostEqual(self.controls._dy_um_spin.value(), 40.0)  # 20 * 2

    def test_dragging_updates_the_calibrated_readout_live(self) -> None:
        self.geometry.set_measurement_anchors(0.0, 0.0, 50.0, 0.0)
        self.geometry.apply_measurement_calibration(dx_um=100.0, dy_um=0.0)
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(30.0, 0.0)
        self.assertAlmostEqual(self.controls._dx_um_spin.value(), 60.0)  # 30 * 2
        self.panel._on_measure_drag_event(self._drag_event(30.0, 0.0, start=True))
        self.panel._on_measure_drag_event(self._drag_event(40.0, 0.0))
        self.panel._on_measure_drag_event(self._drag_event(40.0, 0.0, finish=True))
        self.assertAlmostEqual(self.controls._dx_um_spin.value(), 80.0)  # 40 * 2 - tracks the drag

    def test_without_a_calibration_a_fresh_placement_still_resets_to_zero(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(30.0, 20.0)
        self.assertEqual(self.controls._dx_um_spin.value(), 0.0)
        self.assertEqual(self.controls._dy_um_spin.value(), 0.0)

    # -- square-pixel auto-calc across dx/dy/d --------------------------------
    # Coordinates stay within setUp's configured view range (x: 0-80, y:
    # 0-64) - a click outside it is silently rejected as out-of-view
    # (`_in_view`), which would leave a pending point 1 instead of
    # completing the pair, and these tests would then pass for the wrong
    # reason (never actually exercising the auto-calc path at all).

    def test_editing_dx_um_fills_in_dy_um_assuming_square_pixels(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(60.0, 24.0)  # dx_px=60, dy_px=24
        self.controls._dx_um_spin.setValue(50.0)
        self.controls._dx_um_spin.editingFinished.emit()
        self.assertAlmostEqual(self.controls._dy_um_spin.value(), 20.0)  # 50 * 24/60

    def test_editing_dy_um_fills_in_dx_um_assuming_square_pixels(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(60.0, 24.0)
        self.controls._dy_um_spin.setValue(20.0)
        self.controls._dy_um_spin.editingFinished.emit()
        self.assertAlmostEqual(self.controls._dx_um_spin.value(), 50.0)  # 20 * 60/24

    def test_auto_calc_uses_the_magnitude_even_with_a_negative_pixel_delta(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(10.0, 40.0)
        self._click(70.0, 16.0)  # dx_px=60, dy_px=-24
        self.controls._dx_um_spin.setValue(50.0)
        self.controls._dx_um_spin.editingFinished.emit()
        self.assertAlmostEqual(self.controls._dy_um_spin.value(), 20.0)  # never negative - QDoubleSpinBox floors at 0 anyway

    def test_auto_calc_is_skipped_when_the_source_axis_has_no_pixel_span(self) -> None:
        """A perfectly vertical ruler has dx_px=0 - nothing to derive an
        x-per-y scale from, so editing dy_um must not touch dx_um."""
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(0.0, 40.0)  # dx_px=0
        self.controls._dy_um_spin.setValue(20.0)
        self.controls._dy_um_spin.editingFinished.emit()
        self.assertEqual(self.controls._dx_um_spin.value(), 0.0)

    def test_d_px_label_shows_the_hypotenuse(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(60.0, 25.0)  # 5-12-13 triangle x5: dx=60, dy=25, d=65
        self.assertIn("65.0", self.controls._d_px_label.text())

    def test_editing_dx_um_also_updates_d_um(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(60.0, 25.0)
        self.controls._dx_um_spin.setValue(120.0)
        self.controls._dx_um_spin.editingFinished.emit()
        self.assertAlmostEqual(self.controls._dy_um_spin.value(), 50.0)  # 120 * 25/60
        self.assertAlmostEqual(self.controls._d_um_spin.value(), 130.0)  # sqrt(120^2+50^2)

    def test_editing_d_um_decomposes_into_dx_and_dy(self) -> None:
        """Maintainer's spec, 2026-09-29: "d" editable, decomposing into
        dx/dy using the measured line's own x:y ratio."""
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(60.0, 25.0)
        self.controls._d_um_spin.setValue(130.0)
        self.controls._d_um_spin.editingFinished.emit()
        self.assertAlmostEqual(self.controls._dx_um_spin.value(), 120.0)  # 130 * 60/65
        self.assertAlmostEqual(self.controls._dy_um_spin.value(), 50.0)  # 130 * 25/65

    def test_editing_d_um_is_skipped_when_the_line_has_zero_length(self) -> None:
        """Both points coincide - d_px is 0, nothing to derive a scale
        from, matching the per-axis guard dx/dy already have."""
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(40.0, 30.0)
        self._click(40.0, 30.0)
        self.controls._d_um_spin.setValue(50.0)
        self.controls._d_um_spin.editingFinished.emit()
        self.assertEqual(self.controls._dx_um_spin.value(), 0.0)
        self.assertEqual(self.controls._dy_um_spin.value(), 0.0)

    def test_editing_the_other_field_afterward_re_syncs_to_the_new_scale(self) -> None:
        """No independent per-axis override through this UI - editing
        either field always re-derives the other, so a second edit
        replaces the first sync rather than leaving it stale."""
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(60.0, 24.0)  # dx_px=60, dy_px=24
        self.controls._dx_um_spin.setValue(50.0)
        self.controls._dx_um_spin.editingFinished.emit()
        self.assertAlmostEqual(self.controls._dy_um_spin.value(), 20.0)  # first sync
        self.controls._dy_um_spin.setValue(100.0)
        self.controls._dy_um_spin.editingFinished.emit()
        self.assertAlmostEqual(self.controls._dx_um_spin.value(), 250.0)  # 100 * 60/24 - re-synced, not left at 50

    def test_a_fresh_placement_resets_both_um_fields_not_just_deltas(self) -> None:
        self.active_tool.set_active(ImageTool.MEASURE, True)
        self._click(0.0, 0.0)
        self._click(60.0, 24.0)
        self.controls._dx_um_spin.setValue(50.0)
        self.controls._dx_um_spin.editingFinished.emit()
        self._click(5.0, 5.0)  # a new pair starting
        self._click(70.0, 22.0)
        self.assertEqual(self.controls._dx_um_spin.value(), 0.0)
        self.assertEqual(self.controls._dy_um_spin.value(), 0.0)

    # -- controls table ---------------------------------------------------

    def test_help_text_lists_the_measure_tools_controls(self) -> None:
        from lspr_imaging_app.panels.image.image_controls import controls_text

        text = controls_text(ImageTool.MEASURE)
        for expected in ("Left-click", "Right-click", "Middle-drag", "Wheel"):
            self.assertIn(expected, text)


class MeasureButtonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.geometry = GeometryModule()
        self.active_tool = ActiveToolModule()
        self.section = TransformsSection(self.geometry, self.active_tool)

    def test_measure_button_drives_the_active_tool(self) -> None:
        self.section._measure_button.click()
        self.assertIs(self.active_tool.active(), ImageTool.MEASURE)
        self.section._measure_button.click()
        self.assertIsNone(self.active_tool.active())

    def test_measure_is_mutually_exclusive_with_rotate_and_crop(self) -> None:
        self.section._rotate_button.click()
        self.section._measure_button.click()
        self.assertIs(self.active_tool.active(), ImageTool.MEASURE)
        self.assertFalse(self.section._rotate_button.isChecked())
        self.assertTrue(self.section._measure_button.isChecked())

    def test_measure_button_follows_an_outside_change(self) -> None:
        self.section._measure_button.click()
        self.active_tool.clear()
        self.assertFalse(self.section._measure_button.isChecked())


if __name__ == "__main__":
    unittest.main()
