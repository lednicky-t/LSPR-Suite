from __future__ import annotations

import sys
import unittest
from unittest import mock

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
    estimate_affine_chromatic_transform,
    fit_affine_matrix,
    fit_similarity_matrix,
    identity_affine_matrix,
    invert_affine_matrix,
    populate_dense_roi_positions,
    prepare_registration_image,
)
from lspr_imaging_app.domain.models import AreaRoi, PreprocessingSettings
from lspr_imaging_app.gui.analysis_tasks import _estimate_chromatic_models_task, _sampled_wavelengths


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


class TestPopulateDenseRoiPositions(unittest.TestCase):
    def setUp(self) -> None:
        self.rois = [
            AreaRoi(area_roi_id=1, center_x=10.0, center_y=20.0, sample_radius_px=5.0),
            AreaRoi(area_roi_id=2, center_x=50.0, center_y=60.0, sample_radius_px=5.0),
        ]
        self.matrices = {
            (0, 450.0): np.array([[1.0, 0.0, 3.0], [0.0, 1.0, -2.0]]),
            (0, 550.0): identity_affine_matrix(),
        }

    def test_matches_apply_affine_to_points_per_key(self) -> None:
        populate_dense_roi_positions(self.rois, list(self.matrices), self.matrices.get)
        for roi in self.rois:
            for key, matrix in self.matrices.items():
                expected = apply_affine_to_points(np.array([[roi.center_x, roi.center_y]]), matrix)[0]
                np.testing.assert_allclose(roi.per_wavelength[key], expected)

    def test_overwrites_existing_entries_on_recall(self) -> None:
        populate_dense_roi_positions(self.rois, list(self.matrices), self.matrices.get)
        self.rois[0].per_wavelength[(0, 450.0)] = (999.0, 999.0)  # simulate a manual nudge
        populate_dense_roi_positions(self.rois, list(self.matrices), self.matrices.get)
        expected = apply_affine_to_points(np.array([[10.0, 20.0]]), self.matrices[(0, 450.0)])[0]
        np.testing.assert_allclose(self.rois[0].per_wavelength[(0, 450.0)], expected)

    def test_missing_affine_for_a_key_leaves_that_key_unpopulated(self) -> None:
        populate_dense_roi_positions(self.rois, [(0, 450.0), (0, 999.0)], lambda key: self.matrices.get(key))
        self.assertIn((0, 450.0), self.rois[0].per_wavelength)
        self.assertNotIn((0, 999.0), self.rois[0].per_wavelength)

    def test_empty_rois_or_keys_is_a_no_op(self) -> None:
        populate_dense_roi_positions([], list(self.matrices), self.matrices.get)
        populate_dense_roi_positions(self.rois, [], self.matrices.get)
        self.assertIsNone(self.rois[0].per_wavelength)


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


class TestEstimateAffineChromaticTransformReferencePrepared(unittest.TestCase):
    def test_reference_prepared_skips_recomputation_and_matches_the_default_path(self) -> None:
        # Regression test for the perf fix in gui/analysis_tasks.py's chromatic
        # loop: prepare_registration_image(reference_image) - two full-image
        # Gaussian blurs + two Sobel passes - used to run again on every
        # wavelength even though the reference image never changes within one
        # "Estimate chromatic transforms" run. reference_prepared lets a caller
        # supply that once. This confirms it actually avoids the recomputation
        # (mocked_prepare.call_count) and that doing so doesn't change the result.
        rng = np.random.default_rng(42)
        reference = rng.normal(loc=500.0, scale=80.0, size=(220, 220)).astype(np.float32)
        target = np.roll(reference, shift=(2, 3), axis=(0, 1))
        kwargs = dict(mode="fast", tile_size_px=32, search_radius_px=8)

        result_default = estimate_affine_chromatic_transform(reference, target, **kwargs)

        prepared = prepare_registration_image(reference)
        with mock.patch(
            "lspr_imaging_app.processing.chromatic.prepare_registration_image", wraps=prepare_registration_image
        ) as mocked_prepare:
            result_with_prepared = estimate_affine_chromatic_transform(
                reference, target, reference_prepared=prepared, **kwargs
            )
        # Only the target should be prepared inside the call - the reference
        # was supplied pre-prepared, so it must not be re-derived.
        self.assertEqual(mocked_prepare.call_count, 1)

        np.testing.assert_array_equal(result_with_prepared.affine_matrix, result_default.affine_matrix)
        self.assertEqual(result_with_prepared.tile_count, result_default.tile_count)
        self.assertEqual(result_with_prepared.inlier_count, result_default.inlier_count)
        self.assertEqual(result_with_prepared.rmse_px, result_default.rmse_px)


class TestEstimateChromaticModelsExclusion(unittest.TestCase):
    def test_sample_candidates_are_scoped_to_reference_cube_exclusions(self) -> None:
        # Wavelength 520 is excluded only for spectral cube 0 (the reference
        # cube); cube 1 still has a valid 520 nm record. The GUI would never
        # have offered 520 nm on cube 0 for landmark-marking, so the sample
        # candidates used to validate "did you mark every sample" must be
        # scoped to cube 0's own non-excluded wavelengths -- not the union
        # across every cube -- or this raises a spurious "missing reference
        # point" error for a wavelength the user could never have marked.
        wavelengths = [500.0, 510.0, 520.0, 530.0, 540.0]
        record_specs = [
            (cube, wavelength, f"cube{cube}_wl{int(wavelength)}.tif")
            for cube in (0, 1)
            for wavelength in wavelengths
            if not (cube == 0 and wavelength == 520.0)
        ]
        reference_key = (0, 500.0)
        preprocessing = PreprocessingSettings(
            chromatic_registration_mode="landmark_radial",
            chromatic_sample_image_count=3,
            chromatic_feature_count=2,
        )
        cube0_candidates = [wl for wl in wavelengths if wl != 520.0]
        sample_wavelengths = _sampled_wavelengths(cube0_candidates, 3)
        landmarks_payload = [
            (feature_id, 0, wavelength, 10.0 * feature_id, 20.0 * feature_id)
            for wavelength in sorted(set(sample_wavelengths) | {reference_key[1]})
            for feature_id in (1, 2)
        ]
        models = _estimate_chromatic_models_task(record_specs, preprocessing, reference_key, landmarks_payload)
        modeled_keys = {(model.spectral_cube_index, model.wavelength_nm) for model in models}
        self.assertEqual(modeled_keys, {(cube, wavelength) for cube, wavelength, _path in record_specs})
        self.assertNotIn((0, 520.0), modeled_keys)
        self.assertIn((1, 520.0), modeled_keys)

    def test_zero_nm_broadband_frame_is_never_selected_as_a_sample_wavelength(self) -> None:
        # 0 nm is the broadband/no-filter frame some acquisitions capture
        # alongside the narrowband wavelengths. ChromaticController.
        # candidate_chromatic_wavelengths excludes it from what the GUI lets
        # the user mark landmarks on (its illumination breaks landmark
        # tracking), so the estimation task must exclude it from its own
        # sample-wavelength candidates too -- otherwise it demands landmarks
        # at 0 nm that the user could never have provided and raises a
        # spurious "missing reference point" error (regression: it used to
        # pick 0 nm here because only image-exclusions were filtered, not
        # the 0 nm broadband frame).
        wavelengths = [0.0, 500.0, 510.0, 520.0, 530.0, 540.0]
        record_specs = [(0, wavelength, f"cube0_wl{int(wavelength)}.tif") for wavelength in wavelengths]
        reference_key = (0, 500.0)
        preprocessing = PreprocessingSettings(
            chromatic_registration_mode="landmark_radial",
            chromatic_sample_image_count=3,
            chromatic_feature_count=2,
        )
        non_zero_candidates = [wl for wl in wavelengths if wl != 0.0]
        sample_wavelengths = _sampled_wavelengths(non_zero_candidates, 3)
        self.assertNotIn(0.0, sample_wavelengths)
        # Landmarks are only ever provided for the non-zero sample wavelengths,
        # matching what the GUI would actually let the user mark.
        landmarks_payload = [
            (feature_id, 0, wavelength, 10.0 * feature_id, 20.0 * feature_id)
            for wavelength in sorted(set(sample_wavelengths) | {reference_key[1]})
            for feature_id in (1, 2)
        ]
        models = _estimate_chromatic_models_task(record_specs, preprocessing, reference_key, landmarks_payload)
        modeled_keys = {(model.spectral_cube_index, model.wavelength_nm) for model in models}
        # 0 nm still gets a model (interpolated/extrapolated from the sampled
        # wavelengths), it's just never required to have its own landmarks.
        self.assertEqual(modeled_keys, {(cube, wavelength) for cube, wavelength, _path in record_specs})


if __name__ == "__main__":
    unittest.main()
