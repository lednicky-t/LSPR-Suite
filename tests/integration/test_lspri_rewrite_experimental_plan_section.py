"""Tests for the rewrite's "Experimental plan" Workflow section
(`panels/workflow/dataset_experimental_plan.py`), specifically the "cube
interval" line added 2026-09-30 (`docs/rewrite_build_log_2026-09.md`'s
same-dated entries have the full "why", including the same-day revision
from "sampling interval"/median to "cube interval"/mean+std).

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch.**

A real `QApplication` built in-process, never `.exec()`; the real widget
constructed directly and driven via direct method/signal calls - this
app's own CLAUDE.md GUI-testability convention.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_core import ImagingAcquisitionMetadata, ImagingCubeTiming

    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.panels.workflow.dataset_experimental_plan import ExperimentalPlanSection
    from lspr_imaging_app.selection import SelectionModule
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


def _dataset_with_regular_cadence(cube_count: int, interval_ms: int) -> ImageDataset:
    timings = [
        ImagingCubeTiming(spectral_cube_index=cube, wavelength_nm=500.0, acquired_at_unix_ms=cube * interval_ms)
        for cube in range(cube_count)
    ]
    metadata = ImagingAcquisitionMetadata(source_format="legacy_measuring_times_csv", image_timings=timings)
    records = [
        ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=cube), path=Path(f"c{cube}.tif"))
        for cube in range(cube_count)
    ]
    return ImageDataset(folder=Path("."), records=records, acquisition_metadata=metadata)


class ExperimentalPlanSectionCubeIntervalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = DatasetModule()
        self.selection = SelectionModule()
        self.section = ExperimentalPlanSection(self.dataset, self.selection)

    def test_no_metadata_shows_no_interval_line(self) -> None:
        self.dataset.load_dataset(ImageDataset(folder=Path("."), records=[]))
        self.assertNotIn("Cube interval", self.section._status_label.text())

    def test_regular_cadence_shows_mean_and_zero_spread(self) -> None:
        self.dataset.load_dataset(_dataset_with_regular_cadence(cube_count=5, interval_ms=8_000))
        text = self.section._status_label.text()
        self.assertIn("Cube interval: ~8.0 s ± 0.0 s (4 gaps)", text)

    def test_tooltip_reports_min_and_max(self) -> None:
        self.dataset.load_dataset(_dataset_with_regular_cadence(cube_count=5, interval_ms=8_000))
        tooltip = self.section._status_label.toolTip()
        self.assertIn("min 8.0 s", tooltip)
        self.assertIn("max 8.0 s", tooltip)

    def test_the_n_over_m_timed_header_stat_is_unaffected_by_compaction(self) -> None:
        """Real regression coverage for the ordering trap this feature
        introduced: `cube_interval_stats()` compacts (and thereby empties)
        `metadata.image_timings` as a side effect - the existing "N/M
        timed" header stat reads that same list and must still see the
        pre-compaction count, not zero."""
        self.dataset.load_dataset(_dataset_with_regular_cadence(cube_count=5, interval_ms=8_000))
        self.assertIn("5/5 timed", self.section.header_stats_label.text())

    def test_a_single_cube_shows_no_interval_line(self) -> None:
        self.dataset.load_dataset(_dataset_with_regular_cadence(cube_count=1, interval_ms=8_000))
        self.assertNotIn("Cube interval", self.section._status_label.text())

    def test_exactly_two_cubes_shows_mean_with_no_spread_suffix(self) -> None:
        """One gap has a mean but no meaningful standard deviation - the
        label falls back to "(1 gap)" instead of a fabricated "± 0.0 s"."""
        self.dataset.load_dataset(_dataset_with_regular_cadence(cube_count=2, interval_ms=8_000))
        text = self.section._status_label.text()
        self.assertIn("Cube interval: ~8.0 s (1 gap)", text)
        self.assertNotIn("±", text)

    def test_dataset_cleared_removes_the_interval_line_and_tooltip(self) -> None:
        self.dataset.load_dataset(_dataset_with_regular_cadence(cube_count=5, interval_ms=8_000))
        self.dataset.clear_dataset()
        self.assertNotIn("Cube interval", self.section._status_label.text())
        self.assertEqual(self.section._status_label.toolTip(), "")


if __name__ == "__main__":
    unittest.main()
