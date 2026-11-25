"""`roi/shape_guess.guess_shape`: circle, rectangle or pixel map for a blob."""

from __future__ import annotations

import unittest

import numpy as np

from lspr_imaging_app.roi.shape_guess import guess_shape


def _blob(kind: str) -> np.ndarray:
    yy, xx = np.mgrid[0:80, 0:80]
    if kind == "disk":
        return (xx - 40) ** 2 + (yy - 38) ** 2 <= 14**2
    if kind == "rectangle":
        return (abs(xx - 40) <= 18) & (abs(yy - 38) <= 9)
    if kind == "turned":
        u = (xx - 40) * np.cos(0.4) + (yy - 38) * np.sin(0.4)
        v = -(xx - 40) * np.sin(0.4) + (yy - 38) * np.cos(0.4)
        return (abs(u) <= 20) & (abs(v) <= 8)
    star = ((xx - 40) ** 2 + (yy - 40) ** 2 <= 16**2) & ~((xx > 40) & (yy > 40))  # a disk with a quarter missing
    return star


class GuessShapeTest(unittest.TestCase):
    def test_a_disk_is_a_circle(self) -> None:
        guess = guess_shape(_blob("disk"))
        self.assertEqual(guess.kind, "circle")
        self.assertAlmostEqual(guess.shape.diameter_px, 28.0, delta=0.5)
        self.assertGreater(guess.overlap, 0.95)

    def test_a_box_is_a_rectangle_and_a_turned_one_keeps_its_angle(self) -> None:
        self.assertEqual(guess_shape(_blob("rectangle")).kind, "rectangle")
        turned = guess_shape(_blob("turned"))
        self.assertEqual(turned.kind, "rectangle")
        self.assertAlmostEqual(abs(turned.shape.angle_deg), np.degrees(0.4), delta=2.0)

    def test_a_shape_that_is_neither_stays_a_pixel_map(self) -> None:
        guess = guess_shape(_blob("pacman"))
        self.assertEqual(guess.kind, "mask")

    def test_the_threshold_decides(self) -> None:
        self.assertEqual(guess_shape(_blob("pacman"), min_overlap=0.5).kind, "circle")


if __name__ == "__main__":
    unittest.main()
