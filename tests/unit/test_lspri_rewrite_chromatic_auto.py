"""Unit tests for the rewrite's automatic chromatic-correction landmarks:
the pure pipeline (`image_tools/chromatic/auto_landmarks.py`), the progress
helper (`progress.py`) and the `ChromaticModule` commands built for it.

Ground truth comes from synthetic frames with a known transform per
wavelength (`tests/_lspri_chromatic_synthetic.py`), so accuracy is checked
against the truth, not against another measurement.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.chromatic import ChromaticModule
    from lspr_imaging_app.image_tools.chromatic import auto_landmarks as al
    from lspr_imaging_app.image_tools.chromatic.model import ChromaticLandmarkObservation
    from lspr_imaging_app.image_tools.chromatic.wavelength_interpolation import sampled_wavelengths
    from lspr_imaging_app.progress import Cancelled, StageProgress
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402

from tests import _lspri_chromatic_synthetic as synth  # noqa: E402


def _params(count: int = 12, stride: int = 1, **extra) -> al.AutoLandmarkParams:
    wavelengths = synth.WAVELENGTHS
    samples = sampled_wavelengths(list(wavelengths), al.sample_count_for_stride(len(wavelengths), stride))
    return al.AutoLandmarkParams(
        reference_wavelength_nm=synth.REFERENCE_NM,
        all_wavelengths_nm=wavelengths,
        sample_wavelengths_nm=tuple(samples),
        landmark_count=count,
        **extra,
    )


class WavelengthColorTest(unittest.TestCase):
    def test_colours_follow_the_spectrum(self) -> None:
        from lspr_imaging_app.wavelength_color import wavelength_to_rgb

        r, g, b = wavelength_to_rgb(450.0)
        self.assertGreater(b, 200)
        self.assertLess(r, 60)  # blue
        r, g, b = wavelength_to_rgb(530.0)
        self.assertGreater(g, 200)
        self.assertGreater(g, b)  # green
        r, g, b = wavelength_to_rgb(600.0)
        self.assertGreater(r, 200)
        self.assertEqual(b, 0)
        self.assertGreater(r, g)  # orange-red
        self.assertEqual(wavelength_to_rgb(700.0), (255, 0, 0))  # red
        # every 10 nm over the measured range has its own colour, and none is black
        colours = [wavelength_to_rgb(float(w)) for w in range(470, 721, 10)]
        self.assertEqual(len(set(colours)), len(colours) - len([w for w in range(650, 721, 10)]) + 1)
        self.assertTrue(all(sum(c) > 150 for c in colours))


class GridHelpersTest(unittest.TestCase):
    def test_grid_shapes_for_the_standard_counts(self) -> None:
        self.assertEqual(al.grid_for_count(15, 1.5), (5, 3))
        self.assertEqual(al.grid_for_count(32, 1.5), (8, 4))
        self.assertEqual(al.grid_for_count(60, 1.5), (10, 6))

    def test_prime_counts_snap_to_a_reasonable_grid(self) -> None:
        snapped = al.snap_landmark_count(13, 1.5)
        nx, ny = al.grid_for_count(snapped, 1.5)
        self.assertEqual(nx * ny, snapped)
        self.assertGreaterEqual(ny, 2)
        self.assertLess(abs(np.log((nx / ny) / 1.5)), 0.5)

    def test_stride_to_sample_count(self) -> None:
        self.assertEqual(al.sample_count_for_stride(26, 1), 26)
        self.assertEqual(al.sample_count_for_stride(26, 3), 10)
        self.assertEqual(al.sample_count_for_stride(26, 5), 6)

    def test_every_wavelength_really_means_every_wavelength(self) -> None:
        wavelengths = [float(w) for w in range(470, 721, 10)]  # 26: even
        self.assertEqual(len(sampled_wavelengths(wavelengths, 26)), 26)


class PruneInconsistentTest(unittest.TestCase):
    """A landmark that drifts a little per step passes every step check but ends
    up far from the fit; `_prune_inconsistent` must drop it (and only it)."""

    def _positions(self, drift_landmark: int | None, drift_px: float = 1.2, noise: float = 0.08) -> tuple[np.ndarray, list[float]]:
        rng = np.random.default_rng(5)
        base = synth.disk_centres()[:12]
        wavelengths = list(synth.WAVELENGTHS)
        stack = []
        for index, wavelength in enumerate(wavelengths):
            points = synth.transform(base, wavelength) + rng.normal(0.0, noise, base.shape)
            if drift_landmark is not None:
                points[drift_landmark, 0] += drift_px * (wavelength - synth.REFERENCE_NM) / 60.0  # grows with distance from the reference
            stack.append(points)
        stack[wavelengths.index(synth.REFERENCE_NM)] = base
        return np.stack(stack), wavelengths

    def test_a_drifting_landmark_is_dropped_and_named(self) -> None:
        positions, wavelengths = self._positions(drift_landmark=3)
        kept, dropped = al._prune_inconsistent(positions, wavelengths, wavelengths.index(synth.REFERENCE_NM))
        self.assertEqual([index for index, _why in dropped], [3])
        self.assertNotIn(3, kept)
        self.assertEqual(len(kept), 11)
        self.assertIn("px from the fit at", dropped[0][1])

    def test_clean_data_loses_nothing_even_with_tiny_noise(self) -> None:
        for noise in (0.02, 0.08, 0.15):
            positions, wavelengths = self._positions(drift_landmark=None, noise=noise)
            kept, dropped = al._prune_inconsistent(positions, wavelengths, wavelengths.index(synth.REFERENCE_NM))
            self.assertEqual(dropped, [], noise)
            self.assertEqual(len(kept), 12)

    def test_it_never_drops_below_the_minimum_needed_for_a_fit(self) -> None:
        positions, wavelengths = self._positions(drift_landmark=None, noise=0.0)
        rng = np.random.default_rng(1)
        positions[:, :, :] += rng.normal(0.0, 2.0, positions.shape)  # garbage everywhere
        kept, _dropped = al._prune_inconsistent(positions, wavelengths, wavelengths.index(synth.REFERENCE_NM))
        self.assertGreaterEqual(len(kept), 4)


class ProgressTest(unittest.TestCase):
    def test_stages_map_to_one_monotonic_fraction(self) -> None:
        seen: list[float] = []
        report = StageProgress({"a": 1.0, "b": 3.0}, lambda fraction, _message: seen.append(fraction))
        report("a", 0.5)
        report("a", 1.0)
        report("b", 0.0)
        report("b", 1.0)
        self.assertEqual(seen, [0.125, 0.25, 0.25, 1.0])

    def test_every_report_is_a_cancellation_checkpoint(self) -> None:
        report = StageProgress({"a": 1.0}, None, lambda: True)
        with self.assertRaises(Cancelled):
            report("a", 0.1)


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frames = {w: synth.make_frame(w) for w in synth.WAVELENGTHS}

    def _load(self, wavelength_nm: float) -> np.ndarray:
        return self.frames[float(wavelength_nm)]

    def _assert_matches_truth(self, result: al.AutoLandmarkResult, tolerance_px: float) -> None:
        reference_index = result.wavelengths_nm.index(synth.REFERENCE_NM)
        reference = result.positions_px[reference_index]
        # The detected reference points sit on real disks (within a pixel of a true centre).
        truth_reference = synth.disk_centres()
        for point in reference:
            self.assertLess(float(np.min(np.hypot(*(truth_reference - point).T))), 1.0)
        worst = 0.0
        for index, wavelength in enumerate(result.wavelengths_nm):
            expected = synth.transform(reference, wavelength)  # reference points carried by the true transform
            worst = max(worst, float(np.max(np.hypot(*(result.positions_px[index] - expected).T))))
        self.assertLess(worst, tolerance_px)

    def test_recovers_the_true_transform_at_every_wavelength(self) -> None:
        result = al.detect_and_track(_params(12), self._load)
        self.assertEqual(result.kept_count, 12)
        self.assertEqual(len(result.wavelengths_nm), len(synth.WAVELENGTHS))
        self.assertLess(result.feature_diameter_px, 2.0 * synth.DISK_RADIUS * 1.25)
        self.assertGreater(result.feature_diameter_px, 2.0 * synth.DISK_RADIUS * 0.75)
        self._assert_matches_truth(result, tolerance_px=0.35)
        self.assertLess(result.loo_mean_px, 0.3)

    def test_landmarks_span_the_whole_feature_lattice_not_a_corner_of_it(self) -> None:
        """Regression (2026-10-04): on a cropped image the landmarks bunched up
        because nodes were laid over the raw image border while candidates could
        not sit within patch+search of the edge. The chosen landmarks must cover
        (nearly) the full extent of the features that exist."""
        result = al.detect_and_track(_params(12), self._load)
        reference = result.positions_px[result.wavelengths_nm.index(synth.REFERENCE_NM)]
        lattice = synth.disk_centres()
        for axis in (0, 1):
            available = float(np.ptp(lattice[:, axis]))
            self.assertGreaterEqual(float(np.ptp(reference[:, axis])), 0.9 * available)
        self.assertAlmostEqual(result.spread_fraction[0], float(np.ptp(reference[:, 0])) / synth.SHAPE[1])
        self.assertAlmostEqual(result.spread_fraction[1], float(np.ptp(reference[:, 1])) / synth.SHAPE[0])

    def test_landmarks_do_not_bunch_up_when_the_features_fill_only_part_of_the_image(self) -> None:
        def load(wavelength_nm: float) -> np.ndarray:
            frame = self.frames[float(wavelength_nm)].copy()
            frame[:, 260:] = frame[:, 255:256]  # right part blank: features only in the left 60 %
            return frame

        result = al.detect_and_track(_params(6), load)
        reference = result.positions_px[result.wavelengths_nm.index(synth.REFERENCE_NM)]
        lattice = synth.disk_centres()
        left = lattice[lattice[:, 0] < 245]
        self.assertGreaterEqual(float(np.ptp(reference[:, 0])), 0.85 * float(np.ptp(left[:, 0])))
        self.assertGreaterEqual(float(np.ptp(reference[:, 1])), 0.85 * float(np.ptp(left[:, 1])))

    def test_sparse_wavelengths_still_follow_the_larger_steps(self) -> None:
        result = al.detect_and_track(_params(12, stride=3), self._load)
        self.assertLess(len(result.wavelengths_nm), len(synth.WAVELENGTHS))
        self.assertIn(synth.REFERENCE_NM, result.wavelengths_nm)
        self._assert_matches_truth(result, tolerance_px=0.4)

    def test_nan_corners_from_rotation_do_not_break_it(self) -> None:
        def load(wavelength_nm: float) -> np.ndarray:
            frame = self.frames[float(wavelength_nm)].copy()
            frame[:40, :60] = np.nan  # a rotation-style invalid corner
            frame[-30:, -50:] = np.nan
            return frame

        result = al.detect_and_track(_params(12), load)
        self.assertGreaterEqual(result.kept_count, 10)
        self._assert_matches_truth(result, tolerance_px=0.4)

    def test_progress_is_monotonic_and_ends_at_one(self) -> None:
        seen: list[float] = []
        al.detect_and_track(_params(12, stride=2), self._load, progress=lambda f, _m: seen.append(f))
        self.assertGreater(len(seen), 5)
        self.assertTrue(all(a <= b + 1e-9 for a, b in zip(seen, seen[1:])))
        self.assertAlmostEqual(seen[-1], 1.0)

    def test_cancelling_stops_the_run(self) -> None:
        calls = {"n": 0}

        def cancelled() -> bool:
            calls["n"] += 1
            return calls["n"] > 3

        with self.assertRaises(Cancelled):
            al.detect_and_track(_params(12), self._load, cancelled=cancelled)

    def test_asking_for_more_landmarks_than_features_is_a_readable_error(self) -> None:
        with self.assertRaises(al.AutoLandmarkError) as caught:
            al.detect_and_track(_params(120), self._load)
        self.assertIn("landmarks were requested", str(caught.exception))

    def test_a_featureless_image_fails_loudly(self) -> None:
        flat = np.full(synth.SHAPE, 40000.0, dtype=np.float32)
        with self.assertRaises(al.AutoLandmarkError):
            al.detect_and_track(_params(12), lambda _w: flat)


class ModuleCommandsTest(unittest.TestCase):
    def setUp(self) -> None:
        undo_manager.clear()
        self.module = ChromaticModule()
        self.wavelengths = [float(w) for w in range(500, 601, 20)]  # 6 wavelengths
        self.keys = [(0, w) for w in self.wavelengths]
        self.reference = (0, 550.0)

    def _observations(self, drop_at: float | None = None) -> list[ChromaticLandmarkObservation]:
        base = np.array([[100.0, 80.0], [300.0, 90.0], [120.0, 220.0], [320.0, 210.0], [210.0, 150.0]])
        out = []
        for wavelength in self.wavelengths:
            if drop_at is not None and wavelength == drop_at:
                continue
            for index, point in enumerate(synth.transform(base, wavelength)):
                out.append(ChromaticLandmarkObservation(index + 1, 0, wavelength, float(point[0]), float(point[1])))
        return out

    def _apply(self, observations) -> None:
        self.module.apply_automatic_result(
            sample_image_count=len(self.wavelengths), reference_key=self.reference,
            observations=observations, image_keys=self.keys,
        )

    def test_apply_fits_every_model_enables_and_is_one_undo_step(self) -> None:
        self._apply(self._observations())
        self.assertEqual(len(self.module.models()), len(self.wavelengths))
        self.assertTrue(self.module.settings().chromatic_correction_enabled)
        matrix = self.module.affine_for((0, 600.0))
        moved = np.array([210.0, 150.0]) @ matrix[:, :2].T + matrix[:, 2]
        expected = synth.transform(synth.transform(np.array([[210.0, 150.0]]), 550.0), 600.0)[0]
        self.assertLess(float(np.hypot(*(moved - expected))), 0.05)
        self.assertEqual(undo_manager.undo_label, "Automatic chromatic correction")
        undo_manager.undo()
        self.assertEqual(self.module.models(), ())
        self.assertEqual(self.module.landmarks(), ())
        self.assertFalse(self.module.settings().chromatic_correction_enabled)

    def test_a_failed_fit_leaves_everything_as_it_was(self) -> None:
        self._apply(self._observations())
        before_models, before_landmarks = self.module.models(), self.module.landmarks()
        label_before = undo_manager.undo_label
        with self.assertRaises(ValueError):
            self._apply(self._observations(drop_at=500.0))  # a sampled wavelength has no landmarks
        self.assertEqual(self.module.models(), before_models)
        self.assertEqual(self.module.landmarks(), before_landmarks)
        self.assertEqual(undo_manager.undo_label, label_before)

    def test_the_apply_switch_really_turns_the_correction_off(self) -> None:
        self._apply(self._observations())
        self.assertFalse(np.allclose(self.module.affine_for((0, 600.0)), np.array([[1.0, 0, 0], [0, 1.0, 0]])))
        self.module.set_correction_enabled(False)
        self.assertTrue(np.allclose(self.module.affine_for((0, 600.0)), np.array([[1.0, 0, 0], [0, 1.0, 0]])))
        self.assertTrue(np.allclose(self.module.affine_between((0, 550.0), (0, 600.0)), np.array([[1.0, 0, 0], [0, 1.0, 0]])))
        self.assertFalse(np.allclose(self.module.fitted_affine_for((0, 600.0)), np.array([[1.0, 0, 0], [0, 1.0, 0]])))
        undo_manager.undo()  # the toggle is undoable
        self.assertTrue(self.module.settings().chromatic_correction_enabled)

    def test_replace_landmarks_is_one_signal_and_one_undo_step(self) -> None:
        emitted: list[str] = []
        self.module.chromatic_model_changed.connect(lambda change: emitted.append(change.reason))
        self.module.replace_landmarks(self._observations())
        self.assertEqual(emitted, ["landmarks_changed"])
        self.assertEqual(undo_manager.undo_label, "Chromatic landmarks")


if __name__ == "__main__":
    unittest.main()
