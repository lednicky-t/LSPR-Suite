"""The tables behind "Save data" on a plot (`analysis/export_tables.py`) and their writer (`storage/plot_export.py`), 2026-11-03. No Qt.

Pinned: a CSV reads back to exactly the numbers drawn (full precision), no value is an empty cell, out-of-date cubes are a column and a note, a band
gets its own columns; the step statistics are one row per line and step; spectra carry nm and eV; histogram bins pair edges with values; the
writer never replaces a file unless asked, leaves no half-written file, and writes a record with the format, app and notes."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import export_tables as et
    from lspr_imaging_app.analysis.scan_trace import Series
    from lspr_imaging_app.analysis.scan_view import RoiSpectrum
    from lspr_imaging_app.plan.bar_model import PlanBarStep
    from lspr_imaging_app.plan.stats_table import StatsLine, build_stats_table
    from lspr_imaging_app.storage import plot_export
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402


def read_csv(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


class TableTests(unittest.TestCase):
    def test_the_sensorgram_reads_back_to_the_numbers_drawn(self) -> None:
        rng = np.random.default_rng(0)
        x = np.array([0.0, 1.5, 3.0, 4.5])
        a = 600.0 + rng.normal(0, 1, 4)
        a[2] = np.nan  # a gap
        b = 610.0 + rng.normal(0, 1, 4)
        series = [Series("roi:1", "ROI 1", "#f00", x, a), Series("group:g", "g (n=2)", "#0f0", x, b, b - 0.1, b + 0.1, n_members=2)]
        table = et.sensorgram_table(series, "Time (s)", (10, 11, 12, 13), np.array([False, True, False, False]))
        self.assertEqual(table.header, ("cube", "Time (s)", "out_of_date", "ROI 1 (nm)", "g (n=2) (nm)", "g (n=2) low (nm)", "g (n=2) high (nm)"))
        self.assertEqual(len(table.notes), 1)
        self.assertIn("1 of 4 cubes are out of date", table.notes[0])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "s.csv"
            plot_export.write_plot_export(path, {"sensorgram": table}, {})
            rows = read_csv(path)
        self.assertEqual(rows[1][:3], ["10", "0.0", "0"])
        self.assertEqual(rows[2][2], "1")
        self.assertEqual(rows[3][3], "")  # the gap is an empty cell
        self.assertEqual([float(r[3]) for r in rows[1:] if r[3]], [v for v in a if np.isfinite(v)])  # exact round trip
        self.assertEqual([float(r[5]) for r in rows[1:]], list(b - 0.1))

    def test_the_step_statistics_are_one_row_per_line_and_step(self) -> None:
        bars = [PlanBarStep(index=0, x_start=0.0, x_end=3.0, text="water", color="#f00", tooltip="", levels=None),
                PlanBarStep(index=1, x_start=3.0, x_end=6.0, text="", color="#f00", tooltip="", levels=None)]
        lines = [StatsLine("ROI 1", "#fff", (600.0, 601.5), (0.1, 0.2), (3, 3), False), StatsLine("ROI 2", "#fff", (602.0, float("nan")), (0.1, float("nan")), (3, 0), False)]
        table = et.step_stats_table(build_stats_table(bars, lines))
        self.assertEqual(table.header, ("line", "step", "step_title", "step_start", "step_end", "mean (nm)", "sd (nm)", "n"))
        self.assertEqual(table.rows[0], ("ROI 1", 1, "water", 0.0, 3.0, 600.0, 0.1, 3))
        self.assertEqual(table.rows[1][:3], ("ROI 1", 2, "Step 2"))
        self.assertEqual(len(table.rows), 4)
        self.assertIsNone(et.step_stats_table(None))

    def test_spectra_carry_nm_and_ev_and_say_what_each_row_is(self) -> None:
        s = RoiSpectrum(3, np.array([500.0, 600.0]), np.array([0.1, 0.2]), np.array([0.01, np.nan]))
        s.fit_wavelengths_nm, s.fit_values = np.array([500.0, 550.0, 600.0]), np.array([0.1, 0.15, 0.2])
        s.metric_wavelength_nm, s.metric_value = 600.0, 0.2
        table = et.spectra_table([s], ["ROI 3"], 1239.84198)
        kinds = [row[2] for row in table.rows]
        self.assertEqual(kinds, ["measured", "measured", "fit", "fit", "fit", "metric"])
        self.assertAlmostEqual(table.rows[0][4], 1239.84198 / 500.0)
        self.assertEqual(et.cell(table.rows[1][6]), "")  # no error known

    def test_histogram_bins_pair_edges_with_values(self) -> None:
        table = et.histogram_table([("All pixels", np.array([0.0, 1.0, 2.0]), np.array([5.0, 7.0]))], "(DN)", "Pixels")
        self.assertEqual(table.rows, (("All pixels", 0.0, 1.0, 5.0), ("All pixels", 1.0, 2.0, 7.0)))

    def test_cells(self) -> None:
        self.assertEqual(et.cell(0.1 + 0.2), "0.30000000000000004")  # full precision
        self.assertEqual(et.cell(float("nan")), "")
        self.assertEqual(et.cell(None), "")
        self.assertEqual(et.cell(True), "1")
        self.assertEqual(et.cell(np.int64(4)), "4")


class WriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.tables = {"main": et.Table(("a", "b"), ((1, 2.5),), ("note one",)), "extra": et.Table(("c",), (("x",),))}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_files_and_record(self) -> None:
        written = plot_export.write_plot_export(self.folder / "plot.csv", self.tables, {"plot": "test", "settings": {"k": 1}})
        self.assertEqual([p.name for p in written], ["plot.csv", "plot_extra.csv", "plot.json"])
        record = json.loads((self.folder / "plot.json").read_text(encoding="utf-8"))
        self.assertEqual((record["format_name"], record["format_version"]), (et.EXPORT_FORMAT_NAME, et.EXPORT_FORMAT_VERSION))
        self.assertEqual(record["notes"], ["note one"])
        self.assertEqual(record["settings"], {"k": 1})
        self.assertEqual([f["rows"] for f in record["files"]], [1, 1])
        self.assertIn("app_version", record)
        self.assertIn("created_at_utc", record)

    def test_an_existing_file_is_never_replaced_unless_asked(self) -> None:
        (self.folder / "plot_extra.csv").write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            plot_export.write_plot_export(self.folder / "plot.csv", self.tables, {})
        self.assertEqual((self.folder / "plot_extra.csv").read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.folder / "plot.csv").exists(), "nothing is written when a file is in the way")
        plot_export.write_plot_export(self.folder / "plot.csv", self.tables, {}, overwrite=True)
        self.assertNotEqual((self.folder / "plot_extra.csv").read_text(encoding="utf-8"), "keep")

    def test_a_failed_write_leaves_no_half_written_file(self) -> None:
        with mock.patch.object(plot_export.csv, "writer", side_effect=OSError("disk full")), self.assertRaises(OSError):
            plot_export.write_plot_export(self.folder / "plot.csv", self.tables, {})
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_failure_after_the_first_file_removes_it_again(self) -> None:
        real = plot_export.json.dump
        with mock.patch.object(plot_export.json, "dump", side_effect=OSError("disk full")), self.assertRaises(OSError):
            plot_export.write_plot_export(self.folder / "plot.csv", self.tables, {})
        self.assertIs(plot_export.json.dump, real)
        self.assertEqual(list(self.folder.iterdir()), [], "an export is complete or not there")


if __name__ == "__main__":
    unittest.main()
