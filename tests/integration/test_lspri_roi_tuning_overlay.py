"""ROI position tuning, step D2 (`panels/image/tuning_overlay.py`, `ImagePanel.set_tuning_view`): the crosses of the tried centres and the ROIs drawn at their best
centres. Part one is the cross item alone on a bare plot, part two the Image panel with real ROIs. A drawing only: the tests also pin that no ROI ever moves.
Design: docs/roi_position_tuning_plan_2026-10-28.md (step D2).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.roi_tuning import Lattice
    from lspr_imaging_app.analysis.roi_tuning_module import TuningView
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.image_tools import (
        ActiveToolModule,
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        ImageTool,
        MaskModule,
        MaskScopeModule,
    )
    from lspr_imaging_app.panels.image import ImagePanel
    from lspr_imaging_app.panels.image import tuning_overlay as to
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import HighlightRangeModule, ReferenceFrameModule, SelectionModule
    from lspr_imaging_app.undo import undo_manager

    from tests.integration.test_lspri_rewrite_image_panel import _pump, _write_dataset
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
LATTICE = Lattice(3, 2.0)  # offsets from (-2, -2) to (+2, +2), the centre at row 4


def make_view(
    roi_xy=((40.0, 30.0), (60.0, 45.0)), best=(8, 4), *, revision=1, stale=False, show_crosses=True, show_best=False, scores=None, roi_ids=(1, 2)
) -> TuningView:
    """A view of ``len(roi_xy)`` ROIs on the 3 x 3 lattice; ``best`` = lattice row of each ROI's best position (-1 = could not be ranked)."""
    roi_xy = np.asarray(roi_xy, dtype=np.float64)
    offsets = LATTICE.offsets()
    n_rois = len(roi_xy)
    if scores is None:
        scores = np.tile(np.linspace(0.0, 1.0, LATTICE.n_points)[:, None], (1, n_rois))
    best = np.asarray(best, dtype=np.int64)
    best_offsets = np.zeros((n_rois, 2))
    moving = (best >= 0) & (best != LATTICE.centre_index)
    best_offsets[moving] = offsets[best[moving]]
    return TuningView(
        revision, tuple(roi_ids), roi_xy, offsets, np.asarray(scores, dtype=np.float64), best, best_offsets, LATTICE.centre_index, stale, show_crosses, show_best
    )


class RampTest(unittest.TestCase):
    def test_the_ends_of_the_ramp_are_viridis_dark_purple_and_yellow(self) -> None:
        rgb = to.ramp_rgb(np.array([0.0, 1.0]))
        self.assertEqual(rgb.tolist(), [[68, 1, 84], [253, 231, 37]])

    def test_the_ramp_gets_steadily_brighter_so_it_reads_on_a_grey_image_and_without_red_green(self) -> None:
        rgb = to.ramp_rgb(np.linspace(0.0, 1.0, 50)).astype(float)
        luminance = 0.2126 * rgb[:, 0] + 0.7152 * rgb[:, 1] + 0.0722 * rgb[:, 2]
        self.assertTrue(np.all(np.diff(luminance) > 0))

    def test_no_value_is_grey_and_out_of_range_scores_are_clipped(self) -> None:
        rgb = to.ramp_rgb(np.array([np.nan, -3.0, 7.0]))
        self.assertEqual(rgb.tolist(), [list(to.NO_VALUE_RGB), [68, 1, 84], [253, 231, 37]])


class OverlayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plot = pg.PlotItem()
        self.overlay = to.TuningOverlay(self.plot)

    def test_nothing_is_drawn_without_a_view(self) -> None:
        self.overlay.update(None, IDENTITY)
        self.assertEqual(self.overlay.point_count(), 0)

    def test_one_cross_per_roi_and_lattice_position_at_the_centre_plus_the_offset(self) -> None:
        view = make_view()
        self.overlay.update(view, IDENTITY)
        self.assertEqual(self.overlay.point_count(), 9 * 2)
        expected = (view.roi_xy[None] + view.offsets[:, None]).reshape(-1, 2)
        np.testing.assert_allclose(self.overlay.positions(), expected)

    def test_the_best_position_of_each_roi_has_the_larger_cross(self) -> None:
        self.overlay.update(make_view(best=(8, 4)), IDENTITY)
        sizes = self.overlay.sizes().reshape(9, 2)
        self.assertEqual(sizes[8, 0], to.BEST_CROSS_SIZE)  # ROI 1: the corner (+2, +2)
        self.assertEqual(sizes[4, 1], to.BEST_CROSS_SIZE)  # ROI 2: its own centre is best
        self.assertEqual(int((sizes == to.BEST_CROSS_SIZE).sum()), 2)
        self.assertEqual(int((sizes == to.CROSS_SIZE).sum()), 16)

    def test_a_roi_that_could_not_be_ranked_has_no_best_cross_and_grey_crosses(self) -> None:
        scores = np.full((9, 2), np.nan)
        scores[:, 0] = np.linspace(0.0, 1.0, 9)
        self.overlay.update(make_view(best=(8, -1), scores=scores), IDENTITY)
        sizes = self.overlay.sizes().reshape(9, 2)
        self.assertEqual(int((sizes == to.BEST_CROSS_SIZE).sum()), 1)
        colours = self.overlay.pen_rgb().reshape(9, 2, 3)
        self.assertTrue(np.all(colours[:, 1] == np.array(to.NO_VALUE_RGB)))

    def test_the_colour_runs_from_the_worst_to_the_best_of_each_roi(self) -> None:
        self.overlay.update(make_view(), IDENTITY)
        colours = self.overlay.pen_rgb().reshape(9, 2, 3)
        for roi in range(2):
            self.assertEqual(colours[0, roi].tolist(), [68, 1, 84])  # worst
            self.assertEqual(colours[8, roi].tolist(), [253, 231, 37])  # best

    def test_the_crosses_follow_the_chromatic_shift_of_the_frame_on_screen(self) -> None:
        view = make_view()
        self.overlay.update(view, IDENTITY)
        before = self.overlay.positions()
        shifted = np.array([[1.0, 0.0, 3.5], [0.0, 1.0, -1.25]])
        self.overlay.update(view, shifted)
        np.testing.assert_allclose(self.overlay.positions(), before + [3.5, -1.25])

    def test_a_result_that_is_out_of_date_is_drawn_faint(self) -> None:
        self.overlay.update(make_view(stale=False), IDENTITY)
        self.assertEqual(self.overlay.opacity(), 1.0)
        self.overlay.update(make_view(revision=2, stale=True), IDENTITY)
        self.assertEqual(self.overlay.opacity(), to.STALE_OPACITY)
        self.overlay.update(make_view(revision=3, stale=False), IDENTITY)
        self.assertEqual(self.overlay.opacity(), 1.0)

    def test_crosses_switched_off_or_a_cleared_trial_draw_nothing(self) -> None:
        self.overlay.update(make_view(), IDENTITY)
        self.overlay.update(make_view(revision=2, show_crosses=False), IDENTITY)
        self.assertEqual(self.overlay.point_count(), 0)
        self.overlay.update(make_view(revision=3), IDENTITY)
        self.assertEqual(self.overlay.point_count(), 18)
        self.overlay.update(None, IDENTITY)
        self.assertEqual(self.overlay.point_count(), 0)

    def test_the_same_view_and_frame_cost_a_comparison_not_a_redraw(self) -> None:
        view = make_view()
        self.overlay.update(view, IDENTITY)
        with mock.patch.object(self.overlay._item, "setData") as set_data:
            self.overlay.update(view, IDENTITY.copy())
            self.assertEqual(set_data.call_count, 0)
            self.overlay.update(make_view(revision=2), IDENTITY)
            self.assertEqual(set_data.call_count, 1)

    def test_a_click_goes_through_to_the_image(self) -> None:
        from PyQt6.QtCore import Qt

        self.assertEqual(self.overlay._item.acceptedMouseButtons(), Qt.MouseButton.NoButton)


class PanelTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset = DatasetModule()
        self.roi_toolbox = RoiToolbox()
        self.active_tool = ActiveToolModule()
        self.panel = ImagePanel(
            self.dataset, GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), self.roi_toolbox,
            SelectionModule(), self.active_tool, ReferenceFrameModule(), HighlightRangeModule(), mask_scope=MaskScopeModule(),
        )
        self.dataset.load_dataset(_write_dataset(Path(self._tmp.name)))
        _pump()
        self.roi_toolbox.add_roi(40.0, 30.0, sample_diameter_px=10.0)
        self.roi_toolbox.add_roi(60.0, 45.0, sample_diameter_px=10.0)
        _pump(0.3)

    def tearDown(self) -> None:
        self.panel._renderer.stop()
        self._tmp.cleanup()
        undo_manager.clear()

    def circle_centres(self) -> np.ndarray:
        """The mean of each drawn sample circle (the circles are separated by NaN), in ROI order."""
        xs, ys = self.panel._roi_overlay.sample_circles.outline_xy(None)
        out, current = [], []
        for x, y in zip(xs, ys, strict=True):
            if x != x:
                if current:
                    out.append(np.mean(current, axis=0))
                current = []
            else:
                current.append((x, y))
        if current:
            out.append(np.mean(current, axis=0))
        return np.asarray(out)

    def real_positions(self) -> list[tuple[float, float]]:
        return [(roi.center_x, roi.center_y) for roi in self.roi_toolbox.rois()]

    def test_the_crosses_are_drawn_for_every_tried_centre(self) -> None:
        self.panel.set_tuning_view(make_view())
        self.assertEqual(self.panel._tuning_overlay.point_count(), 18)
        self.panel.set_tuning_view(None)
        self.assertEqual(self.panel._tuning_overlay.point_count(), 0)

    def test_the_best_view_draws_the_roi_at_its_best_centre_and_never_moves_the_roi(self) -> None:
        before = self.circle_centres()
        real = self.real_positions()
        self.panel.set_tuning_view(make_view(best=(8, 4), show_best=True))  # ROI 1 to (+2, +2); ROI 2 stays
        after = self.circle_centres()
        np.testing.assert_allclose(after[0], before[0] + [2.0, 2.0], atol=1e-9)
        np.testing.assert_allclose(after[1], before[1], atol=1e-9)
        self.assertEqual(self.real_positions(), real)  # a view: the ROI table is untouched
        self.panel.set_tuning_view(make_view(best=(8, 4), show_best=False, revision=2))
        np.testing.assert_allclose(self.circle_centres(), before, atol=1e-9)

    def test_switching_the_crosses_does_not_redraw_the_rois(self) -> None:
        self.panel.set_tuning_view(make_view(show_best=True))
        with mock.patch.object(self.panel._roi_overlay, "draw") as draw:
            self.panel.set_tuning_view(make_view(show_best=True, show_crosses=False, revision=2))
            self.assertEqual(draw.call_count, 1)  # the revision changed while the ROIs are shown at their best: they follow it
        self.panel.set_tuning_view(make_view(show_best=False, revision=3))
        with mock.patch.object(self.panel._roi_overlay, "draw") as draw:
            self.panel.set_tuning_view(make_view(show_best=False, show_crosses=False, revision=4))
            self.assertEqual(draw.call_count, 0)  # ROIs where they are: only the crosses change

    def test_a_roi_that_is_not_in_the_trial_is_not_moved(self) -> None:
        before = self.circle_centres()
        self.panel.set_tuning_view(make_view(roi_xy=((40.0, 30.0),), best=(8,), roi_ids=(1,), show_best=True))
        after = self.circle_centres()
        np.testing.assert_allclose(after[0], before[0] + [2.0, 2.0], atol=1e-9)
        np.testing.assert_allclose(after[1], before[1], atol=1e-9)

    def test_the_best_view_follows_the_chromatic_affine_of_the_frame(self) -> None:
        self.panel.set_tuning_view(make_view(best=(8, 4), show_best=True))
        scale = np.array([[2.0, 0.0, 0.0], [0.0, 2.0, 0.0]])  # a made-up correction that doubles lengths: a shift of (2, 2) shows as (4, 4)
        centres = np.array([(40.0, 30.0), (60.0, 45.0)])
        moved = self.panel._with_tuning_best(self.roi_toolbox.rois(), centres, scale)
        np.testing.assert_allclose(moved[0], centres[0] + [4.0, 4.0])
        np.testing.assert_allclose(moved[1], centres[1])

    def test_the_rotate_tool_hides_the_crosses_with_the_roi_overlay(self) -> None:
        self.panel.set_tuning_view(make_view())
        self.active_tool.set_active(ImageTool.ROTATE, True)
        _pump(0.3)
        self.assertEqual(self.panel._tuning_overlay.point_count(), 0)

    def test_a_view_given_before_any_image_is_kept_and_drawn_when_the_image_arrives(self) -> None:
        fresh = ImagePanel(
            DatasetModule(), GeometryModule(), MaskModule(), ChromaticModule(), BackgroundModule(), RoiToolbox(), SelectionModule(), ActiveToolModule(),
            ReferenceFrameModule(), HighlightRangeModule(), mask_scope=MaskScopeModule(),
        )
        try:
            view = make_view()
            fresh.set_tuning_view(view)  # no image yet: nothing to draw on, and no error
            self.assertEqual(fresh._tuning_overlay.point_count(), 0)
            self.assertIs(fresh._tuning_view, view)  # kept for the first draw
        finally:
            fresh._renderer.stop()


if __name__ == "__main__":
    unittest.main()
