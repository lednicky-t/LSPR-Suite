"""`roi/array_pipeline.py`: detect / refine / place on synthetic arrays of known geometry (pure, no Qt)."""

from __future__ import annotations

import math
import unittest
from dataclasses import replace

import numpy as np
from scipy import ndimage

from lspr_imaging_app.roi.array_detection import ArrayPrior, lattice_nodes
from lspr_imaging_app.roi.array_pipeline import (
    ArrayError,
    ArraySettings,
    Cancelled,
    detect_array,
    place_array,
    refine_array,
)
from lspr_imaging_app.roi.edge_size import EdgeSizeParams
from lspr_imaging_app.roi.ring_size import RingParams


def _image(rows=4, cols=6, pitch=70.0, diameter=26.0, tilt=0.0, blur=1.5, origin=(70.0, 60.0), shape=(320, 480), noise=0.01):
    rng = np.random.default_rng(2)
    a1 = pitch * np.array([math.cos(math.radians(tilt)), math.sin(math.radians(tilt))])
    a2 = pitch * np.array([-math.sin(math.radians(tilt)), math.cos(math.radians(tilt))])
    nodes = lattice_nodes(rows, cols, origin, tuple(a1), tuple(a2))
    up = 3
    yy, xx = np.mgrid[0 : shape[0] * up, 0 : shape[1] * up]
    mask = np.zeros(xx.shape, dtype=bool)
    for cx, cy in nodes:
        mask |= np.hypot((xx + 0.5) / up - 0.5 - cx, (yy + 0.5) / up - 0.5 - cy) <= diameter / 2.0
    mask = ndimage.gaussian_filter(mask.reshape(shape[0], up, shape[1], up).mean(axis=(1, 3)), blur)
    column = np.linspace(0.0, 1.0, shape[1])[None, :]
    image = 1000.0 * (1.0 + 0.2 * (column - 0.5)) * (1.0 - 0.6 * mask) + rng.normal(0.0, noise * 1000.0, shape)
    return image.astype(np.float32), nodes


class DetectArrayTest(unittest.TestCase):
    def test_auto_detect_finds_sizes_and_rings(self) -> None:
        image, truth = _image()
        result = detect_array(image, ArraySettings(edge=EdgeSizeParams(model="half_max")))
        self.assertEqual((result.rows, result.cols), (4, 6))
        self.assertEqual(result.count, 24)
        self.assertAlmostEqual(result.pitch_x_px, 70.0, delta=0.3)
        self.assertLess(np.abs(result.centers_xy - truth).max(), 0.8)
        # half-max recovers the true 26 px; one value for the whole array (default size mode)
        self.assertAlmostEqual(float(result.sample_diameters_px[0]), 26.0, delta=1.0)
        self.assertEqual(len(set(np.round(result.sample_diameters_px, 6))), 1)
        # ring: inner clear of the disk, equal-area outer, one ring size for the array
        inner, outer = float(result.ring_inner_px[0]), float(result.ring_outer_px[0])
        self.assertGreater(inner, 26.0 + 1.0)
        self.assertAlmostEqual(outer, math.hypot(inner, float(result.sample_diameters_px[0])), places=6)
        self.assertIn("4 x 6", result.report)
        self.assertIsNone(result.rotate_suggestion_deg)

    def test_individual_sizes_differ_per_spot(self) -> None:
        image, _truth = _image(noise=0.02)
        result = detect_array(
            image, ArraySettings(edge=EdgeSizeParams(model="half_max"), size_mode="individual", ring_size_mode="individual")
        )
        self.assertGreater(len(set(np.round(result.sample_diameters_px, 4))), 1)
        self.assertGreater(len(set(np.round(result.ring_inner_px, 4))), 1)
        self.assertTrue(np.all(result.ring_inner_px > result.sample_diameters_px))

    def test_a_tilt_above_two_degrees_is_suggested_for_rotation(self) -> None:
        image, _truth = _image(tilt=3.0, origin=(80.0, 50.0), shape=(340, 500))
        result = detect_array(image, ArraySettings())
        self.assertAlmostEqual(result.tilt_deg, 3.0, delta=0.3)
        self.assertAlmostEqual(result.rotate_suggestion_deg, 3.0, delta=0.3)

    def test_priors_that_disagree_become_warnings(self) -> None:
        image, _truth = _image()
        result = detect_array(image, ArraySettings(prior=ArrayPrior(rows=5, cols=6)))
        self.assertTrue(any("expected" in w for w in result.warnings))

    def test_no_array_is_a_readable_error(self) -> None:
        with self.assertRaises(ArrayError) as caught:
            detect_array(np.full((200, 200), 500.0, dtype=np.float32), ArraySettings())
        self.assertTrue(str(caught.exception))

    def test_cancel(self) -> None:
        image, _truth = _image()
        with self.assertRaises(Cancelled):
            detect_array(image, ArraySettings(), cancelled=lambda: True)

    def test_progress_is_monotonic_and_reaches_one(self) -> None:
        image, _truth = _image()
        seen: list[float] = []
        detect_array(image, ArraySettings(), progress=lambda f, _t: seen.append(f))
        self.assertTrue(seen and seen == sorted(seen))
        self.assertAlmostEqual(seen[-1], 1.0)

    def test_bad_modes_are_errors(self) -> None:
        with self.assertRaises(ValueError):
            detect_array(np.zeros((10, 10), np.float32), ArraySettings(size_mode="nope"))


class RefineArrayTest(unittest.TestCase):
    def test_displaced_rois_move_back_onto_their_spots_and_get_measured(self) -> None:
        image, truth = _image()
        rng = np.random.default_rng(5)
        start = truth + rng.uniform(-4.0, 4.0, truth.shape)
        result = refine_array(image, start, np.full(len(truth), 24.0), ArraySettings(edge=EdgeSizeParams(model="half_max")))
        self.assertLess(np.abs(result.centers_xy - truth).max(), 0.8)
        self.assertEqual(result.found.sum(), len(truth))
        self.assertAlmostEqual(float(result.sample_diameters_px[0]), 26.0, delta=1.0)
        self.assertIn("Refined 24 of 24", result.report)

    def test_a_roi_on_empty_background_stays_where_it_is(self) -> None:
        image, truth = _image()
        start = np.vstack([truth, [(440.0, 290.0)]])
        result = refine_array(image, start, np.full(len(start), 26.0), ArraySettings(edge=EdgeSizeParams(model="half_max")))
        self.assertFalse(result.found[-1])
        np.testing.assert_allclose(result.centers_xy[-1], (440.0, 290.0))
        self.assertTrue(any("left in place" in w for w in result.warnings))

    def test_nothing_selected_is_an_error(self) -> None:
        with self.assertRaises(ArrayError):
            refine_array(np.zeros((50, 50), np.float32), np.empty((0, 2)), np.empty(0), ArraySettings())


class PlaceArrayTest(unittest.TestCase):
    def test_manual_lattice_and_ratio_ring(self) -> None:
        settings = ArraySettings(
            prior=ArrayPrior(diameter_px=30.0), anchor_xy=(100.0, 80.0), ring=RingParams(inner_mode="ratio", inner_ratio=1.5)
        )
        result = place_array(settings, rows=2, cols=3, pitch_x_px=60.5, pitch_y_px=70.0)
        self.assertEqual(result.count, 6)
        np.testing.assert_allclose(result.centers_xy[0], (100.0, 80.0))
        np.testing.assert_allclose(result.centers_xy[2], (221.0, 80.0))
        np.testing.assert_allclose(result.centers_xy[3], (100.0, 150.0))
        self.assertTrue(np.all(result.sample_diameters_px == 30.0))
        self.assertTrue(np.allclose(result.ring_inner_px, 45.0))
        self.assertFalse(result.found.any())

    def test_measured_ring_mode_falls_back_to_the_ratio_without_an_image(self) -> None:
        settings = ArraySettings(prior=ArrayPrior(diameter_px=30.0), ring=RingParams(inner_mode="measured", inner_ratio=1.4))
        result = place_array(settings, rows=1, cols=2, pitch_x_px=80.0, pitch_y_px=80.0)
        self.assertTrue(np.allclose(result.ring_inner_px, 42.0))

    def test_rotation_turns_the_lattice(self) -> None:
        settings = ArraySettings(prior=ArrayPrior(diameter_px=20.0), rotation_deg=90.0)
        result = place_array(settings, rows=1, cols=2, pitch_x_px=50.0, pitch_y_px=50.0)
        np.testing.assert_allclose(result.centers_xy[1], (0.0, 50.0), atol=1e-9)

    def test_snap_to_image_moves_nodes_onto_spots_and_measures(self) -> None:
        image, truth = _image()
        settings = ArraySettings(
            prior=ArrayPrior(diameter_px=26.0),
            anchor_xy=(truth[0, 0] + 3.0, truth[0, 1] - 3.0),
            snap_to_image=True,
            edge=EdgeSizeParams(model="half_max"),
        )
        result = place_array(settings, rows=4, cols=6, pitch_x_px=70.0, pitch_y_px=70.0, image=image)
        self.assertLess(np.abs(result.centers_xy - truth).max(), 0.8)
        self.assertTrue(result.found.all())
        self.assertAlmostEqual(float(result.sample_diameters_px[0]), 26.0, delta=1.0)

    def test_touching_disks_are_warned_and_bad_input_rejected(self) -> None:
        settings = ArraySettings(prior=ArrayPrior(diameter_px=30.0))
        self.assertTrue(any("touch" in w for w in place_array(settings, rows=1, cols=2, pitch_x_px=30.0, pitch_y_px=60.0).warnings))
        with self.assertRaises(ArrayError):
            place_array(ArraySettings(), rows=1, cols=2, pitch_x_px=30.0, pitch_y_px=60.0)  # no diameter
        with self.assertRaises(ArrayError):
            place_array(replace(settings), rows=0, cols=2, pitch_x_px=30.0, pitch_y_px=60.0)
        with self.assertRaises(ArrayError):
            place_array(settings, rows=1, cols=2, pitch_x_px=0.0, pitch_y_px=60.0)


if __name__ == "__main__":
    unittest.main()
