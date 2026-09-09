"""Coverage for MainWindow._format_sensorgram_completion_summary - the
status-bar summary shown after "Start analysis" finishes or is stopped
(elapsed time, ROI/cube/wavelength counts for THIS run, and the loaded
dataset's on-disk size). Pure staticmethod, split out for testability
without a real Qt MainWindow - same pattern as _format_busy_detail_text.
"""

from __future__ import annotations

import sys
import unittest

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.main_window import MainWindow


class TestFormatSensorgramCompletionSummary(unittest.TestCase):
    def test_done_includes_elapsed_counts_and_dataset_size(self) -> None:
        text = MainWindow._format_sensorgram_completion_summary(
            cancelled=False,
            completed_cubes=314,
            total_cubes=314,
            roi_count=160,
            wavelength_count=26,
            dataset_size_text="Dataset size: 2.4 GB",
            total_seconds=332.0,
        )
        self.assertEqual(text, "SG done | 5:32 | 160 ROIs, 314 cubes, 26 wavelengths | Dataset size: 2.4 GB")

    def test_stopped_shows_completed_over_total_cubes(self) -> None:
        text = MainWindow._format_sensorgram_completion_summary(
            cancelled=True,
            completed_cubes=42,
            total_cubes=314,
            roi_count=160,
            wavelength_count=26,
            dataset_size_text="Dataset size: 2.4 GB",
            total_seconds=45.0,
        )
        self.assertEqual(text, "SG stopped | 0:45 | 160 ROIs, 42/314 cubes, 26 wavelengths | Dataset size: 2.4 GB")

    def test_done_omits_slash_total_for_cube_count(self) -> None:
        # A completed (not stopped) run always finishes every requested
        # cube - showing "N/N cubes" would be redundant noise.
        text = MainWindow._format_sensorgram_completion_summary(
            cancelled=False,
            completed_cubes=10,
            total_cubes=10,
            roi_count=1,
            wavelength_count=1,
            dataset_size_text="",
            total_seconds=1.0,
        )
        self.assertIn("10 cubes", text)
        self.assertNotIn("10/10", text)

    def test_singular_roi_and_wavelength_counts(self) -> None:
        text = MainWindow._format_sensorgram_completion_summary(
            cancelled=False,
            completed_cubes=1,
            total_cubes=1,
            roi_count=1,
            wavelength_count=1,
            dataset_size_text="",
            total_seconds=1.0,
        )
        self.assertIn("1 ROI,", text)
        self.assertIn("1 wavelength", text)
        self.assertNotIn("1 ROIs", text)
        self.assertNotIn("1 wavelengths", text)

    def test_empty_dataset_size_text_is_omitted_not_shown_as_blank_segment(self) -> None:
        text = MainWindow._format_sensorgram_completion_summary(
            cancelled=False,
            completed_cubes=5,
            total_cubes=5,
            roi_count=3,
            wavelength_count=2,
            dataset_size_text="",
            total_seconds=10.0,
        )
        self.assertEqual(text, "SG done | 0:10 | 3 ROIs, 5 cubes, 2 wavelengths")
        self.assertNotIn("|  |", text)
        self.assertFalse(text.endswith("|"))

    def test_none_total_seconds_omits_elapsed_segment(self) -> None:
        text = MainWindow._format_sensorgram_completion_summary(
            cancelled=False,
            completed_cubes=5,
            total_cubes=5,
            roi_count=3,
            wavelength_count=2,
            dataset_size_text="",
            total_seconds=None,
        )
        self.assertEqual(text, "SG done | 3 ROIs, 5 cubes, 2 wavelengths")


if __name__ == "__main__":
    unittest.main()
