from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
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
    compose_affine_matrices,
    compose_similarity_matrix,
    decompose_similarity_matrix,
    estimate_affine_chromatic_transform,
    fit_affine_matrix,
    fit_similarity_matrix,
    identity_affine_matrix,
    invert_affine_matrix,
    prepare_registration_image,
)
from lspr_imaging_app.domain.models import ChromaticTransformModel, PreprocessingSettings
from lspr_imaging_app.gui.analysis_tasks import _estimate_chromatic_models_task, _sampled_wavelengths
from lspr_imaging_app.gui.chromatic_controller import ChromaticController


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


class TestComposeAffineMatrices(unittest.TestCase):
    def test_matches_applying_inner_then_outer(self) -> None:
        outer = np.array([[1.1, 0.05, 3.0], [-0.02, 0.95, -1.5]])
        inner = np.array([[0.9, -0.1, 2.0], [0.15, 1.05, 4.0]])
        points = np.array([[10.0, 20.0], [-5.0, 30.0], [0.0, 0.0]])
        composed = compose_affine_matrices(outer, inner)
        direct = apply_affine_to_points(apply_affine_to_points(points, inner), outer)
        via_composed = apply_affine_to_points(points, composed)
        np.testing.assert_allclose(via_composed, direct, atol=1e-10)

    def test_composing_with_its_own_inverse_is_identity(self) -> None:
        matrix = np.array([[1.2, 0.1, 5.0], [-0.1, 0.8, -2.0]])
        composed = compose_affine_matrices(matrix, invert_affine_matrix(matrix))
        np.testing.assert_allclose(composed, identity_affine_matrix(), atol=1e-8)


class TestModelForImageKey(unittest.TestCase):
    """ChromaticController.model_for_image_key looks up a model by
    (spectral_cube_index, wavelength_nm) through a dict cached on the
    controller instance, keyed by id(chromatic_models) - replaced an O(N)
    linear scan that made a full dataset's worth of lookups O(N^2)."""

    def setUp(self) -> None:
        self.models = [
            ChromaticTransformModel(spectral_cube_index=0, wavelength_nm=450.0, rmse_px=0.1),
            ChromaticTransformModel(spectral_cube_index=0, wavelength_nm=550.0, rmse_px=0.2),
            ChromaticTransformModel(spectral_cube_index=1, wavelength_nm=450.0, rmse_px=0.3),
        ]
        self.window = SimpleNamespace(_state=SimpleNamespace(chromatic_models=self.models))
        self.controller = ChromaticController(self.window)

    def test_finds_the_matching_model(self) -> None:
        found = self.controller.model_for_image_key((1, 450.0))
        self.assertIs(found, self.models[2])

    def test_returns_none_for_a_missing_key(self) -> None:
        self.assertIsNone(self.controller.model_for_image_key((5, 999.0)))

    def test_returns_none_for_a_none_key(self) -> None:
        self.assertIsNone(self.controller.model_for_image_key(None))

    def test_picks_up_a_reassigned_models_list(self) -> None:
        # Populate the cache against the original list, then simulate a
        # chromatic re-fit (window._state.chromatic_models is always
        # replaced wholesale, never mutated in place).
        self.controller.model_for_image_key((0, 450.0))
        new_models = [ChromaticTransformModel(spectral_cube_index=0, wavelength_nm=450.0, rmse_px=9.0)]
        self.window._state.chromatic_models = new_models
        found = self.controller.model_for_image_key((0, 450.0))
        self.assertIs(found, new_models[0])


class TestMaxShiftPx(unittest.TestCase):
    """ChromaticController.max_shift_px: for an affine map, |predicted(p) - p|
    over a rectangle is maximized at a corner - this is the number the CC
    panel's status line now shows instead of a per-model mean RMSE, since a
    physical worst-case pixel displacement is far more directly interpretable
    than an abstract fit-quality residual. Only the dataset's first and last
    (non-0 nm) wavelengths are checked, not every sampled/interpolated one in
    between - chromatic displacement grows with distance from the reference,
    so the worst case is always at one of the two sweep extremes (see the
    2026-08-22 conversation)."""

    @staticmethod
    def _make_window(models, *, image_shape=(100, 200), wavelength_values=None, chromatic_landmarks=()):
        if wavelength_values is None:
            wavelength_values = sorted({float(model.wavelength_nm) for model in models})
        return SimpleNamespace(
            _state=SimpleNamespace(
                chromatic_models=models,
                chromatic_landmarks=list(chromatic_landmarks),
                preprocessing=SimpleNamespace(chromatic_feature_count=2),
            ),
            _current_processed_image=np.zeros(image_shape, dtype=np.float32),
            _wavelength_values=list(wavelength_values),
        )

    def test_returns_none_without_an_image(self) -> None:
        window = self._make_window([ChromaticTransformModel(spectral_cube_index=0, wavelength_nm=500.0)])
        window._current_processed_image = None
        controller = ChromaticController(window)
        controller.sample_image_keys = lambda: []
        self.assertIsNone(controller.max_shift_px())

    def test_returns_none_without_models(self) -> None:
        window = self._make_window([], wavelength_values=[500.0])
        controller = ChromaticController(window)
        controller.sample_image_keys = lambda: []
        self.assertIsNone(controller.max_shift_px())

    def test_returns_none_without_a_wavelength_range(self) -> None:
        # Only 0 nm present (or nothing at all) -> no usable range once it's
        # excluded.
        models = [ChromaticTransformModel(spectral_cube_index=0, wavelength_nm=0.0)]
        window = self._make_window(models, wavelength_values=[0.0])
        controller = ChromaticController(window)
        controller.sample_image_keys = lambda: []
        self.assertIsNone(controller.max_shift_px())

    def test_finds_the_larger_of_first_and_last_wavelength_shifts(self) -> None:
        height, width = 100, 200
        # Pure x-translations, so |displacement| is the same at every corner -
        # two different magnitudes make "the larger one" unambiguous. A
        # middle wavelength (550 nm) has the largest matrix-implied shift of
        # all three, but must be ignored: only 450 (first) and 650 (last)
        # are ever checked.
        first_shift = np.array([[1.0, 0.0, 3.0], [0.0, 1.0, 0.0]])
        middle_shift = np.array([[1.0, 0.0, 99.0], [0.0, 1.0, 0.0]])
        last_shift = np.array([[1.0, 0.0, 12.5], [0.0, 1.0, 0.0]])
        models = [
            ChromaticTransformModel(spectral_cube_index=0, wavelength_nm=450.0, affine_matrix=first_shift.tolist()),
            ChromaticTransformModel(spectral_cube_index=0, wavelength_nm=550.0, affine_matrix=middle_shift.tolist()),
            ChromaticTransformModel(spectral_cube_index=0, wavelength_nm=650.0, affine_matrix=last_shift.tolist()),
            # A second spectral cube repeating 650 nm's matrix (every cube
            # shares the same wavelength matrix) must not change the result.
            ChromaticTransformModel(spectral_cube_index=1, wavelength_nm=650.0, affine_matrix=last_shift.tolist()),
        ]
        window = self._make_window(models, image_shape=(height, width), wavelength_values=[450.0, 550.0, 650.0])
        controller = ChromaticController(window)
        controller.sample_image_keys = lambda: []
        result = controller.max_shift_px()
        self.assertIsNotNone(result)
        shift_px, wavelength, is_direct = result
        self.assertAlmostEqual(shift_px, 12.5, places=6)
        self.assertEqual(wavelength, 650.0)
        self.assertFalse(is_direct)  # no sample keys wired up -> nothing counts as directly fit here

    def test_ignores_0nm_when_picking_the_first_wavelength(self) -> None:
        height, width = 100, 200
        dark_frame_shift = np.array([[1.0, 0.0, 99.0], [0.0, 1.0, 0.0]])  # must never be picked as "first"
        first_shift = np.array([[1.0, 0.0, 3.0], [0.0, 1.0, 0.0]])
        last_shift = np.array([[1.0, 0.0, 12.5], [0.0, 1.0, 0.0]])
        models = [
            ChromaticTransformModel(spectral_cube_index=0, wavelength_nm=0.0, affine_matrix=dark_frame_shift.tolist()),
            ChromaticTransformModel(spectral_cube_index=0, wavelength_nm=450.0, affine_matrix=first_shift.tolist()),
            ChromaticTransformModel(spectral_cube_index=0, wavelength_nm=650.0, affine_matrix=last_shift.tolist()),
        ]
        window = self._make_window(models, image_shape=(height, width), wavelength_values=[0.0, 450.0, 650.0])
        controller = ChromaticController(window)
        controller.sample_image_keys = lambda: []
        shift_px, wavelength, _is_direct = controller.max_shift_px()
        self.assertAlmostEqual(shift_px, 12.5, places=6)
        self.assertEqual(wavelength, 650.0)

    def test_marks_a_directly_landmark_fit_wavelength_as_such(self) -> None:
        matrix = np.array([[1.0, 0.0, 5.0], [0.0, 1.0, 0.0]])
        models = [ChromaticTransformModel(spectral_cube_index=0, wavelength_nm=500.0, affine_matrix=matrix.tolist())]
        window = self._make_window(
            models,
            wavelength_values=[500.0],
            chromatic_landmarks=[
                SimpleNamespace(landmark_id=1, spectral_cube_index=0, wavelength_nm=500.0, x_px=1.0, y_px=1.0),
                SimpleNamespace(landmark_id=2, spectral_cube_index=0, wavelength_nm=500.0, x_px=2.0, y_px=2.0),
            ],
        )
        controller = ChromaticController(window)
        controller.sample_image_keys = lambda: [(0, 500.0)]
        controller.expected_feature_ids = lambda: [1, 2]
        _shift_px, _wavelength, is_direct = controller.max_shift_px()
        self.assertTrue(is_direct)

    def test_an_incompletely_marked_sample_wavelength_is_not_direct(self) -> None:
        matrix = np.array([[1.0, 0.0, 5.0], [0.0, 1.0, 0.0]])
        models = [ChromaticTransformModel(spectral_cube_index=0, wavelength_nm=500.0, affine_matrix=matrix.tolist())]
        window = self._make_window(
            models,
            wavelength_values=[500.0],
            chromatic_landmarks=[
                SimpleNamespace(landmark_id=1, spectral_cube_index=0, wavelength_nm=500.0, x_px=1.0, y_px=1.0),
                # landmark 2 missing at 500nm -> not a complete sample.
            ],
        )
        controller = ChromaticController(window)
        controller.sample_image_keys = lambda: [(0, 500.0)]
        controller.expected_feature_ids = lambda: [1, 2]
        _shift_px, _wavelength, is_direct = controller.max_shift_px()
        self.assertFalse(is_direct)


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


class TestEstimateChromaticModelsReferenceWithoutLandmarks(unittest.TestCase):
    """Regression test for the 2026-08-22 investigation: the reference
    wavelength need not itself be landmark-marked. The sampled/landmark-marked
    wavelengths (chromatic_sample_image_count) are their own independent
    evenly-spaced grid, unrelated to whatever the reference is set to - a
    workflow that starts landmark-marking, then changes the reference to a
    wavelength outside that grid, is the common case, not an edge case.

    Previously, estimate_models() silently substituted a landmark-marked
    wavelength for the true reference when they didn't match, leaving every
    fitted transform correct relative to that substitute but wrong relative
    to the reference actually shown in the GUI (which never changed) - the
    substitute's own transform came out as identity, indistinguishable from
    "no chromatic shift at all" at that wavelength.
    """

    @staticmethod
    def _landmark_position(feature_id: int, wavelength: float) -> tuple[float, float]:
        # An exact, pure-translation "chromatic shift" as a function of
        # wavelength, so the true reference->wavelength transform is known
        # analytically: translation = (wavelength - reference_wavelength) *
        # (dx_per_nm, dy_per_nm). With no noise in the synthetic data, the
        # fitted/composed result should reproduce it to floating precision
        # regardless of which landmark-marked wavelength is picked as anchor.
        base_x, base_y = 100.0 + feature_id, 100.0 + feature_id
        dx_per_nm, dy_per_nm = 0.015, -0.02
        return base_x + wavelength * dx_per_nm, base_y + wavelength * dy_per_nm

    def test_reference_wavelength_gets_identity_and_correct_shift_elsewhere(self) -> None:
        sample_wavelengths = [400.0, 450.0, 500.0, 550.0, 600.0]
        reference_wavelength = 525.0  # not one of the sampled/landmark-marked wavelengths
        feature_ids = (1, 2, 3)
        record_specs = [
            (0, wavelength, f"cube0_wl{int(wavelength)}.tif")
            for wavelength in [*sample_wavelengths, reference_wavelength]
        ]
        landmarks_payload = [
            (feature_id, 0, wavelength, *self._landmark_position(feature_id, wavelength))
            for wavelength in sample_wavelengths
            for feature_id in feature_ids
        ]
        preprocessing = PreprocessingSettings(
            chromatic_registration_mode="landmark_radial",
            chromatic_sample_image_count=len(sample_wavelengths),
            chromatic_feature_count=len(feature_ids),
        )
        reference_key = (0, reference_wavelength)
        models = _estimate_chromatic_models_task(record_specs, preprocessing, reference_key, landmarks_payload)
        by_wavelength = {model.wavelength_nm: model for model in models}

        reference_matrix = np.asarray(by_wavelength[reference_wavelength].affine_matrix)
        np.testing.assert_allclose(reference_matrix, identity_affine_matrix(), atol=1e-6)

        roi = np.array([[300.0, 400.0]])
        for wavelength in sample_wavelengths:
            matrix = np.asarray(by_wavelength[wavelength].affine_matrix)
            predicted = apply_affine_to_points(roi, matrix)[0]
            expected_shift = (wavelength - reference_wavelength) * np.array([0.015, -0.02])
            np.testing.assert_allclose(predicted, roi[0] + expected_shift, atol=1e-4)

        # The old bug's exact signature: a sampled wavelength's transform
        # incorrectly collapsing onto the reference's identity matrix. 400 nm
        # is 125 nm from the reference and must show a real, non-trivial shift.
        near_matrix = np.asarray(by_wavelength[400.0].affine_matrix)
        self.assertGreater(float(np.abs(near_matrix - identity_affine_matrix()).max()), 0.5)


if __name__ == "__main__":
    unittest.main()
