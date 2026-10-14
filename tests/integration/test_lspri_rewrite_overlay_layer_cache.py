"""The ROI overlay's layer cache (`panels/image/overlay_cache.py`, 2026-10-14, testing10 analysis).

One cached picture of the overlay per overlay state; the same wavelength shows the same overlay in every cube. These tests pin:
- what the cache is allowed to change on screen (a measured, bounded pixel difference),
- when it hits, when it must not (a changed epoch, a changed view, `invalidate`),
- its memory policy (admit until full, never evict: a cycle of 27 states with a smaller LRU would get no hits at all).

Real classes (`pg.PlotWidget`, `RoiOverlay`, `RoiToolbox`) on the offscreen platform; a real paint (`grab`) with an image underneath.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import pyqtgraph as pg
    from PyQt6.QtWidgets import QApplication

    from lspr_imaging_app.panels.image.overlay_cache import OverlayLayers, budget_bytes_from_environment
    from lspr_imaging_app.panels.image.roi_overlay import RoiOverlay
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.undo import undo_manager
except ImportError as error:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite not importable: {error}") from error

_APP = QApplication.instance() or QApplication([])

# Measured 2026-10-14 (offscreen, 1000x600 view, 40 ROIs, all overlay parts on): see `test_cached_picture_differs_from_direct_painting_only_slightly`.
MAX_PIXEL_DIFFERENCE = 3  # of 255, any channel
MAX_DIFFERING_SHARE = 0.002  # of all pixels


def _affine(state: int) -> np.ndarray:
    shift = 0.7 * (state - 6)
    return np.array([[1.0, 0.0, shift], [0.0, 1.0, -0.5 * shift]])


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        ids = [self.toolbox.add_roi(40.0 + 45.0 * col, 40.0 + 45.0 * row, sample_diameter_px=26.0) for row in range(4) for col in range(10)]
        self.toolbox.group_rois(tuple(ids[:10]), "Row A")
        self.toolbox.group_rois(tuple(ids[10:20]), "Row B")
        self.rois = self.toolbox.rois_at(None)
        self.plot = pg.PlotWidget()
        self.plot.resize(1000, 600)
        self.plot.setAspectLocked(True)
        self.image = pg.ImageItem()
        self.plot.addItem(self.image)
        self.frames = [(np.random.default_rng(i).random((220, 520)) * 3000 + 500).astype(np.float32) for i in range(3)]
        self.image.setImage(self.frames[0], autoLevels=False, levels=(500, 3500))
        self.plot.getPlotItem().vb.setRange(xRange=(0, 520), yRange=(0, 220), padding=0)
        self.overlay = RoiOverlay(self.plot.getPlotItem())
        self.overlay.attach_labels()
        self.overlay.labels_visible = True
        self.overlay.group_labels.visible = True
        self.overlay.group_label_item.set_settings(self.overlay.group_labels)
        self.current_state = 0
        # What the panel registers: bring the items up to date when the view changes after a cache hit.
        self.overlay.layers.set_refresher(lambda: self.draw(self.current_state))
        self.plot.show()
        _APP.processEvents()

    def tearDown(self) -> None:
        self.plot.close()
        undo_manager.clear()

    def draw(self, state: int) -> None:
        affine = _affine(state)
        centers = self.toolbox.display_positions((0, 500.0 + state), affine)
        self.overlay.draw(self.rois, centers, [], affine, 18.0, 28.0, self.toolbox.groups())
        self.overlay.layers.mark_drawn(("state", state))

    def paint(self) -> np.ndarray:
        image = self.plot.grab().toImage().convertToFormat(self.plot.grab().toImage().Format.Format_RGBA8888)
        pointer = image.constBits()
        pointer.setsize(image.sizeInBytes())
        return np.frombuffer(pointer, dtype=np.uint8).reshape(image.height(), image.width(), 4).copy()

    def show_state(self, state: int, epoch=("epoch",)) -> bool:
        """What the panel does per frame: ask the cache; re-draw the items only on a miss. Returns the hit."""
        self.current_state = state
        hit = self.overlay.layers.begin(epoch, ("state", state))
        if not hit:
            self.draw(state)
        return hit


class AppearanceTests(_Case):
    def test_cached_picture_differs_from_direct_painting_only_slightly(self) -> None:
        self.overlay.layers.enabled = False
        self.draw(3)
        direct = self.paint()

        self.overlay.layers.enabled = True
        self.assertFalse(self.show_state(3))  # first frame of an epoch: made, not yet kept
        self.paint()
        self.assertFalse(self.show_state(3))  # now stable: made and kept at this paint
        made = self.paint()
        self.assertTrue(self.show_state(3))
        from_cache = self.paint()
        for label, picture in (("made", made), ("from cache", from_cache)):
            difference = np.abs(picture.astype(np.int16) - direct.astype(np.int16))
            share = float((difference.max(axis=2) > 0).mean())
            print(f"\n[overlay layer cache] {label}: max channel difference {int(difference.max())}/255, differing pixels {share:.4%}")
            self.assertLessEqual(int(difference.max()), MAX_PIXEL_DIFFERENCE, label)
            self.assertLessEqual(share, MAX_DIFFERING_SHARE, label)

    def test_the_overlay_really_is_in_the_picture(self) -> None:
        self.overlay.layers.enabled = False
        bare = self.paint()
        self.draw(2)
        with_overlay = self.paint()
        self.assertGreater(float((np.abs(with_overlay.astype(np.int16) - bare.astype(np.int16)).max(axis=2) > 0).mean()), 0.01)


class HitAndMissTests(_Case):
    def warm(self, states=range(5)) -> None:
        for state in states:
            self.show_state(state)
            self.paint()  # kept at the paint, when the epoch is stable
        for state in states:
            self.show_state(state)
            self.paint()

    def test_a_state_seen_again_is_a_hit_and_needs_no_redraw(self) -> None:
        self.warm()
        self.overlay.layers.hits = self.overlay.layers.misses = 0
        draws = []
        original = self.overlay.draw
        self.overlay.draw = lambda *a, **k: (draws.append(1), original(*a, **k))[1]
        for state in (0, 1, 2, 3, 4, 0, 1):
            self.assertTrue(self.show_state(state), state)
            self.paint()
        self.assertEqual(draws, [])
        self.assertEqual((self.overlay.layers.hits, self.overlay.layers.misses), (7, 0))

    def test_a_hit_shows_the_picture_made_for_that_state(self) -> None:
        self.warm()
        self.show_state(1)
        first = self.paint()
        self.show_state(3)
        other = self.paint()
        self.show_state(1)
        again = self.paint()
        self.assertFalse(np.array_equal(first, other))
        np.testing.assert_array_equal(first, again)

    def test_a_changed_epoch_drops_every_picture(self) -> None:
        self.warm()
        self.assertTrue(self.show_state(2))
        self.assertFalse(self.show_state(2, epoch=("another epoch",)))
        self.assertEqual(self.overlay.layers.cached_states, 0)

    def test_an_epoch_that_changes_at_every_step_keeps_nothing(self) -> None:
        for step in range(6):  # a drag
            self.show_state(0, epoch=("drag", step))
            self.paint()
        self.assertEqual(self.overlay.layers.cached_states, 0)
        self.assertEqual(self.overlay.layers.cached_megabytes, 0.0)

    def test_pan_or_zoom_drops_the_pictures_made_for_the_old_view(self) -> None:
        self.warm()
        self.assertTrue(self.show_state(3))
        self.assertTrue(self.show_state(2))  # the state on screen when the view changes
        self.plot.getPlotItem().vb.setRange(xRange=(50, 400), yRange=(20, 180), padding=0)
        _APP.processEvents()  # the repaint makes (and keeps) the picture of the state on screen for the NEW view only
        self.assertFalse(self.show_state(3), "a picture made for the old view was reused")
        self.assertFalse(self.show_state(4), "a picture made for the old view was reused")

    def _direct(self, state: int) -> np.ndarray:
        """The overlay of `state` painted without the cache, in the view as it is now."""
        self.overlay.layers.invalidate()
        self.draw(state)
        picture = self.paint()
        self.overlay.layers.invalidate()
        return picture

    def test_a_zoom_after_cache_hits_shows_the_overlay_of_the_current_state_not_the_last_drawn_one(self) -> None:
        """Regression (found by the first version of this file): after a hit the items still hold the previous state's
        geometry; a repaint caused by a zoom must not draw them, nor keep that picture under the current state's key."""
        self.warm()
        self.assertTrue(self.show_state(2))  # the items still hold state 0 (the last one drawn: a miss on the second lap)
        self.assertEqual(self.overlay.layers.items_state, ("state", 0))
        self.plot.getPlotItem().vb.setRange(xRange=(50, 400), yRange=(20, 180), padding=0)  # runs the refresher
        _APP.processEvents()
        self.assertEqual(self.overlay.layers.items_state, ("state", 2))
        after_zoom = self.paint()
        direct = self._direct(2)
        difference = np.abs(after_zoom.astype(np.int16) - direct.astype(np.int16))
        self.assertLessEqual(int(difference.max()), MAX_PIXEL_DIFFERENCE)
        self.assertLessEqual(float((difference.max(axis=2) > 0).mean()), MAX_DIFFERING_SHARE)

    def test_nothing_is_kept_from_items_that_hold_another_state(self) -> None:
        self.warm()
        self.assertTrue(self.show_state(1))
        self.overlay.layers.set_refresher(None)  # no refresh: the items stay on state 0
        self.plot.getPlotItem().vb.setRange(xRange=(50, 400), yRange=(20, 180), padding=0)
        _APP.processEvents()
        self.paint()
        self.assertEqual(self.overlay.layers.cached_states, 0, "a picture of the wrong state was kept")

    def test_invalidate_paints_plainly_until_the_next_frame_step(self) -> None:
        self.warm()
        self.overlay.layers.invalidate()
        self.assertEqual(self.overlay.layers.cached_states, 0)
        self.assertFalse(self.show_state(2))

    def test_a_style_change_drops_the_pictures(self) -> None:
        self.warm()
        self.assertTrue(self.show_state(1))
        self.overlay.set_sample_width(1.0)
        self.assertEqual(self.overlay.layers.cached_states, 0)

    def test_the_appearance_key_follows_every_setting_that_changes_the_picture(self) -> None:
        keys = [self.overlay.appearance_key()]
        for change in (
            lambda: setattr(self.overlay.sample, "alpha", 0.3),
            lambda: setattr(self.overlay.reference, "visible", False),
            lambda: setattr(self.overlay, "labels_visible", False),
            lambda: setattr(self.overlay, "fill_max_opacity", 0.4),
            lambda: setattr(self.overlay.group_labels, "angle", 45),
            lambda: self.overlay.sample_circles.set_width(1.0),
            lambda: self.overlay.set_label_placement("bottom"),
        ):
            change()
            keys.append(self.overlay.appearance_key())
        self.assertEqual(len(set(keys)), len(keys), "a setting that changes the look left the appearance key unchanged")


class BudgetTests(_Case):
    def test_admits_until_the_budget_is_full_and_never_evicts(self) -> None:
        layers = OverlayLayers(self.plot.getPlotItem(), budget_bytes=3_000_000)  # a few pictures only
        self.assertTrue(layers.enabled)
        overlay_layers = self.overlay.layers
        overlay_layers.enabled = False
        for state in range(8):
            layers.begin(("epoch",), ("state", state))
            self.paint()
        first_seen = layers.cached_states
        for state in range(8):  # a second lap: what was kept must still be there (an LRU would have lost all of it)
            layers.begin(("epoch",), ("state", state))
            self.paint()
        self.assertEqual(layers.cached_states, first_seen)
        self.assertLessEqual(layers.cached_megabytes * 1024 * 1024, 3_000_000 + 1)

    def test_budget_zero_switches_the_cache_off(self) -> None:
        layers = OverlayLayers(self.plot.getPlotItem(), budget_bytes=0)
        self.assertFalse(layers.enabled)
        self.assertFalse(layers.begin(("epoch",), ("state", 0)))

    def test_environment_budget(self) -> None:
        self.assertEqual(budget_bytes_from_environment({"LSPR_OVERLAY_CACHE_MB": "0"}), 0)
        self.assertEqual(budget_bytes_from_environment({"LSPR_OVERLAY_CACHE_MB": "64"}), 64 * 1024 * 1024)
        with self.assertLogs("lspr_imaging_app.panels.image.overlay_cache", level="WARNING"):
            self.assertEqual(budget_bytes_from_environment({"LSPR_OVERLAY_CACHE_MB": "lots"}), 256 * 1024 * 1024)
        self.assertEqual(budget_bytes_from_environment({}), 256 * 1024 * 1024)


class GuardTests(_Case):
    def test_states_that_never_repeat_pause_the_cache_instead_of_filling_memory(self) -> None:
        """testing12: every frame was a new state; the cache made a picture of each, filled its whole budget and never hit."""
        layers = self.overlay.layers
        for index in range(80):
            layers.begin(("epoch",), ("state", index))
            self.draw(index % 20)
            self.paint()
        self.assertTrue(layers.paused)
        self.assertEqual(layers.cached_states, 0)  # the pictures made so far were dropped
        self.assertIn("Paused", layers.status_text())
        self.assertFalse(layers.begin(("epoch",), ("state", 7)))
        layers.begin(("another epoch",), ("state", 0))  # a ROI edit or a style change gives it another chance
        self.assertFalse(layers.paused)

    def test_repeating_states_never_pause_it(self) -> None:
        layers = self.overlay.layers
        for index in range(120):
            self.show_state(index % 6)
            self.paint()
        self.assertFalse(layers.paused)
        self.assertGreater(layers.hits, 60)

    def test_when_the_budget_is_full_a_miss_is_painted_directly_without_an_offscreen_render(self) -> None:
        layers = OverlayLayers(self.plot.getPlotItem(), budget_bytes=2_000_000)  # about one picture
        self.overlay.layers.enabled = False
        effect = layers._effects[0]
        calls = []
        original = effect.sourcePixmap
        effect.sourcePixmap = lambda *a, **k: (calls.append(1), original(*a, **k))[1]
        for state in range(6):
            layers.begin(("epoch",), ("state", state))
            layers.mark_drawn(("state", state))
            self.paint()
        self.assertLessEqual(len(calls), 3, "renders that cannot be kept should stop once the budget is full")

    def test_the_layers_bounding_rectangles_are_the_view_and_clip_their_children(self) -> None:
        from PyQt6.QtWidgets import QGraphicsItem

        vb = self.plot.getPlotItem().vb
        layers = self.overlay.layers
        self.assertEqual(layers.screen.boundingRect(), vb.boundingRect())
        self.assertEqual(layers.data.boundingRect(), vb.viewRect())
        for layer in (layers.data, layers.screen):
            self.assertTrue(layer.flags() & QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape)


class HiddenPartsTests(_Case):
    def test_a_hidden_part_is_not_computed_and_comes_back_when_shown(self) -> None:
        self.overlay.reference.visible = False
        self.overlay.sample.visible = False
        self.overlay.apply_style()
        self.draw(1)
        self.assertEqual(self.overlay.sample_circles.circle_count(), 0)
        self.assertEqual(len(self.overlay.reference_curve.getData()[0] if self.overlay.reference_curve.getData()[0] is not None else []), 0)
        self.overlay.sample.visible = True
        self.overlay.reference.visible = True
        self.overlay.apply_style()
        self.draw(1)
        self.assertEqual(self.overlay.sample_circles.circle_count(), len(self.rois))
        self.assertGreater(len(self.overlay.reference_curve.getData()[0]), 0)


if __name__ == "__main__":
    unittest.main()
