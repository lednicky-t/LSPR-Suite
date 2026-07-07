from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.processing.chromatic import (
    affine_residuals,
    apply_affine_to_points,
    compose_similarity_matrix,
    decompose_similarity_matrix,
    fit_affine_matrix,
    fit_similarity_matrix,
    identity_affine_matrix,
    invert_affine_matrix,
)


class TestAffineFit(unittest.TestCase):
    def setUp(self) -> None:
        # 4 non-collinear points, enough to over-determine a 6-parameter affine fit.
        self.source = np.array([[10.0, 10.0], [200.0, 15.0], [30.0, 180.0], [190.0, 175.0]])

    def test_recovers_a_known_affine_transform_exactly(self) -> None:
        known_matrix = np.array([[1.05, 0.03, 4.0], [-0.02, 0.97, -6.5]])
        target = apply_affine_to_points(self.source, known_matrix)
        fitted = fit_affine_matrix(self.source, target)
        np.testing.assert_allclose(fitted, known_matrix, atol=1e-8)

    def test_residuals_are_zero_for_an_exact_fit(self) -> None:
        known_matrix = np.array([[1.1, 0.0, 2.0], [0.0, 0.9, -3.0]])
        target = apply_affine_to_points(self.source, known_matrix)
        residuals = affine_residuals(self.source, target, known_matrix)
        np.testing.assert_allclose(residuals, np.zeros(4), atol=1e-8)

    def test_identity_matrix_leaves_points_unchanged(self) -> None:
        result = apply_affine_to_points(self.source, identity_affine_matrix())
        np.testing.assert_allclose(result, self.source, atol=1e-12)

    def test_invert_affine_matrix_round_trips(self) -> None:
        known_matrix = np.array([[1.2, 0.1, 5.0], [-0.1, 0.8, -2.0]])
        transformed = apply_affine_to_points(self.source, known_matrix)
        inverse = invert_affine_matrix(known_matrix)
        recovered = apply_affine_to_points(transformed, inverse)
        np.testing.assert_allclose(recovered, self.source, atol=1e-8)

    def test_empty_points_returns_empty(self) -> None:
        empty = np.zeros((0, 2))
        result = apply_affine_to_points(empty, identity_affine_matrix())
        self.assertEqual(result.shape, (0, 2))


class TestSimilarityFit(unittest.TestCase):
    def setUp(self) -> None:
        self.source = np.array([[10.0, 10.0], [200.0, 15.0], [30.0, 180.0]])

    def test_recovers_a_known_similarity_transform(self) -> None:
        # A pure rotation + uniform scale + translation is exactly representable
        # by the similarity model (unlike a general affine/shear transform).
        known = compose_similarity_matrix(scale=1.15, angle_rad=0.2, shift_x_px=8.0, shift_y_px=-3.0)
        target = apply_affine_to_points(self.source, known)
        fitted = fit_similarity_matrix(self.source, target)
        np.testing.assert_allclose(fitted, known, atol=1e-6)

    def test_raises_with_fewer_than_two_points(self) -> None:
        with self.assertRaises(ValueError):
            fit_similarity_matrix(self.source[:1], self.source[:1])

    def test_raises_for_degenerate_identical_points(self) -> None:
        degenerate = np.array([[5.0, 5.0], [5.0, 5.0], [5.0, 5.0]])
        with self.assertRaises(ValueError):
            fit_similarity_matrix(degenerate, self.source)


class TestSimilarityComposeDecompose(unittest.TestCase):
    def test_round_trips_through_compose_and_decompose(self) -> None:
        for scale, angle_rad, shift_x, shift_y in [
            (1.0, 0.0, 0.0, 0.0),
            (1.3, 0.4, 12.5, -7.25),
            (0.85, -1.1, -30.0, 40.0),
        ]:
            matrix = compose_similarity_matrix(scale, angle_rad, shift_x, shift_y)
            recovered_scale, recovered_angle, recovered_x, recovered_y = decompose_similarity_matrix(matrix)
            self.assertAlmostEqual(recovered_scale, scale, places=8)
            self.assertAlmostEqual(recovered_angle, angle_rad, places=8)
            self.assertAlmostEqual(recovered_x, shift_x, places=8)
            self.assertAlmostEqual(recovered_y, shift_y, places=8)

    def test_decompose_of_identity_is_unit_scale_zero_rotation(self) -> None:
        scale, angle, shift_x, shift_y = decompose_similarity_matrix(identity_affine_matrix())
        self.assertAlmostEqual(scale, 1.0, places=8)
        self.assertAlmostEqual(angle, 0.0, places=8)
        self.assertAlmostEqual(shift_x, 0.0, places=8)
        self.assertAlmostEqual(shift_y, 0.0, places=8)


if __name__ == "__main__":
    unittest.main()
