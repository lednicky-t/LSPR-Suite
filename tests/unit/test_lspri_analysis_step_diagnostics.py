"""Pure-logic tests for the data-quality checks (`analysis/step_diagnostics.py`) and the image-motion measurement (`analysis/image_motion.py`). A synthetic
scan with known faults: one step with extra noise in the sample ROIs while one pump runs faster, one step that has not settled, a ROI with a weak
signal, a mask that changes inside the baseline. No Qt, no files."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()
APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.image_motion import frame_shifts, step_motion
    from lspr_imaging_app.analysis.scan_store import StoredScan
    from lspr_imaging_app.analysis.step_diagnostics import OK, WARN, run_diagnostics
    from lspr_imaging_app.analysis.step_stats import assign_cubes
    from lspr_imaging_app.plan.edits import EffectiveStep
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

WL = np.linspace(470.0, 720.0, 26)
N_ROI = 20
PER_STEP = 20
N_STEP = 4
N_CUBE = PER_STEP * N_STEP
CUBE_MS = 10_000
SHIFTS = (0.0, 2.0, 4.0, 0.0)  # nm per step
NOISY_STEP, DRIFTING_STEP = 2, 1  # positions
WEAK_ROI = 7  # its signal is half the others'


def band(centre: float) -> np.ndarray:
    return 0.3 * np.exp(-0.5 * ((WL - centre) / 45.0) ** 2)


def build() -> tuple[StoredScan, dict[int, np.ndarray], object]:
    rng = np.random.default_rng(5)
    mean = np.empty((N_CUBE, N_ROI, WL.size, 2))
    truth = np.empty((N_ROI, N_CUBE))
    for c in range(N_CUBE):
        step = c // PER_STEP
        drift = 0.5 * (c % PER_STEP) / PER_STEP if step == DRIFTING_STEP else 0.0
        light = rng.normal(0, 3e-4, WL.size)  # shared by every ROI, cancelled by the reference ROI
        for r in range(N_ROI):
            shift = SHIFTS[step] * (0.5 if r == WEAK_ROI else 1.0) + drift
            truth[r, c] = 580.0 + shift
            extra = 1e-3 if step == NOISY_STEP else 2e-4
            log_ref = 3.0 + light + rng.normal(0, 2e-4, WL.size)
            log_sample = log_ref - band(580.0 + shift) + rng.normal(0, extra, WL.size)  # the extra noise is in the sample only
            mean[c, r, :, 0], mean[c, r, :, 1] = 10**log_sample, 10**log_ref
    shape = mean.shape
    n_valid = np.full(shape, 100, dtype=np.uint32)
    n_valid[3, 4, 2, 0] = 90  # ROI 5 loses pixels in one baseline cube
    times = (np.arange(N_CUBE) * CUBE_MS)[:, None] + np.arange(WL.size)[None, :] * 100
    stored = StoredScan(
        path=Path("memory.h5"), attrs={}, cube_indices=tuple(range(N_CUBE)), roi_ids=tuple(range(1, N_ROI + 1)), wavelength_nm=tuple(WL.tolist()),
        acquired_at_unix_ms=times.astype(np.int64), values={"mean": mean}, std=np.full(shape, 5.0), n_valid=n_valid, n_nominal=np.full(shape, 100, dtype=np.uint32),
        n_masked=np.zeros(shape, dtype=np.uint32), cell_hash=np.ones((N_CUBE, N_ROI), dtype=np.uint64), recipe_id=np.zeros((N_CUBE, N_ROI), dtype=np.int32),
        layout_id=np.zeros(N_CUBE, dtype=np.int32),
    )
    steps = []
    for k in range(N_STEP):
        start, end = k * PER_STEP * CUBE_MS, (k + 1) * PER_STEP * CUBE_MS
        devices = {"pump1": {"rate": "0.04" if k == NOISY_STEP else "0.02"}, "valve": "closed"}
        steps.append(EffectiveStep(k + 1, f"step {k + 1}", start, end, None, f"step {k + 1}", start, end, devices=devices, order=k + 1))
    membership = assign_cubes(steps, times.min(1).astype(float), times.max(1).astype(float))
    louder = np.where(np.arange(N_CUBE) // PER_STEP == NOISY_STEP, 3.0, 1.0)  # the metric is 3x noisier in the noisy step
    traces = {r + 1: truth[r] + rng.normal(0, 0.01, N_CUBE) * louder for r in range(N_ROI)}
    return stored, traces, membership


class StepChecksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.stored, cls.traces, cls.membership = build()
        cls.report = run_diagnostics(cls.stored, "absorbance", cls.traces, cls.membership, pair=(0, 2), template_traces=cls.traces)
        cls.by = {}
        for f in cls.report.findings:
            cls.by.setdefault(f.check, []).append(f)

    def test_the_noisy_step_is_found_with_the_device_setting_it_alone_has(self) -> None:
        (f,) = self.by["noisy_step"]
        self.assertEqual(f.level, WARN)
        self.assertEqual(f.numbers["noisy_steps"], ["3: step 3"])
        self.assertIn(["pump1.rate", "0.04"], f.numbers["shared_device_settings"])
        self.assertNotIn(["valve", "closed"], f.numbers["shared_device_settings"])  # every step has it: not a clue

    def test_the_extra_noise_is_placed_in_the_sample_rois(self) -> None:
        found = [f for f in self.by["noise_source"] if f.level == WARN]
        self.assertEqual(len(found), 1)
        self.assertIn("sample ROIs, not the reference ROIs", found[0].detail)

    def test_the_drifting_step_is_not_settled(self) -> None:
        titles = [f.title for f in self.by["drift"]]
        self.assertTrue(any("2: step 2" in t and "not settled" in t for t in titles), titles)

    def test_independent_noise_is_not_called_shared(self) -> None:
        self.assertEqual(self.by["shared"][0].level, OK)

    def test_the_pair_checks(self) -> None:
        self.assertEqual(self.by["gain"][0].level, OK)  # the traces are their own template
        self.assertEqual(self.by["masks"][0].level, WARN)
        self.assertEqual(self.by["masks"][0].numbers["baseline"], [5])
        outliers = self.by["outliers"][0]
        self.assertIn(WEAK_ROI + 1, outliers.numbers["rois"])
        self.assertEqual(self.by["shape"][0].level, OK)  # a pure shift
        self.assertGreater(self.by["shape"][0].numbers["explained"]["shift"], 0.95)

    def test_a_metric_that_reads_half_the_shift_is_flagged_and_judged_in_template_nm(self) -> None:
        half = {r: 0.5 * t for r, t in self.traces.items()}
        report = run_diagnostics(self.stored, "absorbance", half, self.membership, pair=(0, 2), template_traces=self.traces)
        gain = next(f for f in report.findings if f.check == "gain")
        self.assertEqual(gain.level, WARN)
        self.assertAlmostEqual(gain.numbers["gain"], 0.5, delta=0.02)
        eff_half = next(f for f in report.findings if f.check == "efficiency").numbers["ratio"]
        eff_full = next(f for f in self.report.findings if f.check == "efficiency").numbers["ratio"]
        self.assertAlmostEqual(eff_half, eff_full, delta=0.02 * eff_full)  # halving the nm does not make a method better

    def test_the_checks_never_change_the_traces(self) -> None:
        before = {r: t.copy() for r, t in self.traces.items()}
        run_diagnostics(self.stored, "absorbance", self.traces, self.membership, pair=(0, 2))
        for r in before:
            np.testing.assert_array_equal(before[r], self.traces[r])

    def test_no_steps_is_explained(self) -> None:
        empty = assign_cubes([], np.zeros(N_CUBE), np.zeros(N_CUBE))
        report = run_diagnostics(self.stored, "absorbance", self.traces, empty)
        self.assertEqual(report.findings[0].check, "steps")


class ImageMotionTests(unittest.TestCase):
    def test_a_known_shift_is_measured_and_a_drift_is_flagged(self) -> None:
        rng = np.random.default_rng(1)
        base = rng.random((128, 160)).astype(np.float32)
        frames = [base, np.roll(base, 2, axis=1), np.roll(base, (1, 3), axis=(0, 1))]
        shifts = frame_shifts(frames)
        np.testing.assert_allclose(shifts[1], (2.0, 0.0), atol=0.05)
        np.testing.assert_allclose(shifts[2], (3.0, 1.0), atol=0.05)
        motion = step_motion("s", [0, 1, 2], lambda c: frames[c])
        self.assertTrue(any("drift" in text for text in motion.flagged))
        still = step_motion("s", [0, 1, 2], lambda c: base)
        self.assertEqual(still.flagged, [])
        self.assertIsNone(step_motion("s", [0, 1], lambda c: base))  # too few cubes


if __name__ == "__main__":
    unittest.main()
