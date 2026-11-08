"""Pure-logic tests for the report of the data checks (`analysis/check_report.py`) and the streaming image-motion measurement (`analysis/image_motion.py`). No Qt, no files:
the table of numbers per step, the motion report's findings, and copy / save as text, CSV and JSON."""

from __future__ import annotations

import json
import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()
APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.check_report import (
        CheckReport,
        ReportTable,
        diagnostics_report,
        format_cell,
        motion_report,
        step_table,
        to_csv,
        to_json,
        to_text,
    )
    from lspr_imaging_app.analysis.image_motion import StepMotion, step_motion
    from lspr_imaging_app.analysis.step_diagnostics import INFO, OK, WARN, DiagnosticsReport, Finding, StepNumbers
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402


def numbers() -> StepNumbers:
    n = 3
    full = lambda v: np.full(n, v, dtype=float)  # noqa: E731
    metric = np.array([0.05, np.nan, 0.09])  # the second step had too few cubes for a noise
    return StepNumbers(
        labels=("1: H2O", "2: EG", "3: H2O"), n_cubes=np.array([30, 3, 28]), metric_noise_nm=metric, p2p_noise_nm=full(0.04), drift_sigmas=full(1.0),
        absorbance_noise=full(3.5e-4), sample_noise=full(2.0e-4), reference_noise=full(1.5e-4), sample_reference_corr=full(0.8), shared_light=full(2.5e-4), roi_corr=full(0.01),
    )


def motion(label: str, jitter: float, drift: float) -> StepMotion:
    return StepMotion(label, tuple(range(10)), np.zeros((10, 2)), (jitter, jitter / 2), (drift, drift / 2))


class StepTableTests(unittest.TestCase):
    def test_one_row_per_step_with_the_noise_in_scaled_units(self) -> None:
        table = step_table(numbers())
        self.assertEqual(len(table.rows), 3)
        self.assertEqual(len(table.headers), len(table.rows[0]))
        self.assertEqual(table.rows[0][0], "1: H2O")
        self.assertEqual(table.rows[0][1], 30)
        self.assertAlmostEqual(table.rows[0][2], 0.05)
        self.assertAlmostEqual(table.rows[0][5], 3.5)  # absorbance noise shown in 1e-4
        self.assertTrue(np.isnan(table.rows[1][2]))

    def test_a_missing_number_is_shown_as_a_dash(self) -> None:
        self.assertEqual(format_cell(float("nan")), "–")
        self.assertEqual(format_cell(0.123456), "0.123")
        self.assertEqual(format_cell("ok"), "ok")
        self.assertEqual(format_cell(30), "30")

    def test_the_report_carries_the_findings_and_the_table(self) -> None:
        finding = Finding("noisy_step", WARN, "Step 1 is noisier", "1.7x the median step.", {"ratio": 1.7})
        report = diagnostics_report(DiagnosticsReport((finding,), numbers()), "160 ROIs")
        self.assertEqual(report.warnings, 1)
        self.assertEqual(report.level, WARN)
        self.assertEqual(report.subtitle, "160 ROIs")
        self.assertIsNotNone(report.table)
        quiet = diagnostics_report(DiagnosticsReport((Finding("gain", OK, "fine", "fine"), Finding("pair", INFO, "note", "note")), None), "")
        self.assertEqual((quiet.warnings, quiet.level, quiet.table), (0, OK, None))  # a note is not a warning


class MotionReportTests(unittest.TestCase):
    def test_a_moving_step_is_a_warning_naming_the_step_and_why_it_matters(self) -> None:
        report = motion_report([motion("1: H2O", 0.01, 0.02), motion("2: EG", 0.2, 0.5)], [], 600.0, "raw plane")
        (warning,) = [f for f in report.findings if f.level == WARN]
        self.assertIn("2: EG", warning.title)
        self.assertIn("jitter", warning.detail)
        self.assertIn("drift", warning.detail)
        self.assertIn("ROI", warning.detail)
        self.assertEqual(len(report.table.rows), 2)
        self.assertEqual(report.table.rows[0][-1], "ok")
        self.assertIn("600 nm", report.table.title)

    def test_a_still_image_is_one_ok_finding_with_the_worst_numbers(self) -> None:
        report = motion_report([motion("1", 0.01, 0.03), motion("2", 0.02, 0.04)], [], 600.0, "")
        (finding,) = report.findings
        self.assertEqual(finding.level, OK)
        self.assertAlmostEqual(finding.numbers["jitter_px"], 0.02)
        self.assertAlmostEqual(finding.numbers["range_px"], 0.04)

    def test_steps_too_short_to_measure_are_listed_and_no_steps_is_explained(self) -> None:
        report = motion_report([motion("1", 0.01, 0.03)], ["2: short"], 600.0, "")
        self.assertIn("2: short", [f for f in report.findings if f.level == INFO][0].detail)
        empty = motion_report([], [], 600.0, "")
        self.assertEqual(empty.findings[0].level, INFO)
        self.assertIsNone(empty.table)


class SaveAndCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = CheckReport(
            "Data checks", "made from 160 ROIs",
            (Finding("a", WARN, "A warning", "Because.", {"x": np.float64(1.5), "bad": float("nan"), "ids": [np.int64(3)]}), Finding("b", OK, "Fine", "All fine.")),
            ReportTable("Numbers", ("Step", "Noise"), (("1", 0.0123456789), ("2", float("nan")))),
        )

    def test_text_has_levels_findings_and_the_table_tab_separated(self) -> None:
        text = to_text(self.report)
        self.assertIn("[WARNING] A warning", text)
        self.assertIn("[ok] Fine", text)
        self.assertIn("Step\tNoise", text)
        self.assertIn("1\t0.0123", text)

    def test_csv_keeps_full_precision_and_leaves_nan_empty(self) -> None:
        lines = to_csv(self.report).splitlines()
        self.assertEqual(lines[0], "Step,Noise")
        self.assertEqual(lines[1], "1,0.0123456789")
        self.assertEqual(lines[2], "2,")
        self.assertEqual(to_csv(CheckReport("t", "", ())), "")

    def test_json_round_trips_and_turns_nan_into_null(self) -> None:
        data = json.loads(to_json(self.report))
        self.assertEqual(data["findings"][0]["numbers"], {"x": 1.5, "bad": None, "ids": [3]})
        self.assertEqual(data["table"]["rows"][1], ["2", None])
        self.assertEqual(data["title"], "Data checks")


class StreamingMotionTests(unittest.TestCase):
    def test_planes_are_read_one_at_a_time_and_counted(self) -> None:
        rng = np.random.default_rng(2)
        base = rng.random((64, 80)).astype(np.float32)
        reads: list[int] = []
        ticks: list[int] = []

        def read(cube: int) -> np.ndarray:
            reads.append(cube)
            return np.roll(base, cube % 2, axis=1)

        result = step_motion("s", [4, 5, 6, 7], read, on_plane=lambda: ticks.append(1))
        self.assertEqual(reads, [4, 5, 6, 7])
        self.assertEqual(len(ticks), 4)
        self.assertAlmostEqual(result.range_px[0], 1.0, delta=0.1)  # the odd cubes sit one pixel to the right

    def test_cancelling_stops_reading_and_returns_none(self) -> None:
        base = np.random.default_rng(3).random((32, 32)).astype(np.float32)
        reads: list[int] = []
        flags = {"stop": False}

        def read(cube: int) -> np.ndarray:
            reads.append(cube)
            flags["stop"] = len(reads) >= 2
            return base

        self.assertIsNone(step_motion("s", [0, 1, 2, 3, 4], read, cancelled=lambda: flags["stop"]))
        self.assertEqual(reads, [0, 1])


if __name__ == "__main__":
    unittest.main()
