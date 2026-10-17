"""Exporting every mask of the timeline as PNG files named per frame: `mask/export.py` (2026-10-17).

Pure module logic, no widgets: the files are the same 0/255 images "Save mask" writes, named per frame and scope, with an index.
Design: apps/LSPRi/eva/docs/auto_transient_scan_and_mask_plan_2026-10-16.md.
"""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.provenance import FrameNamingScheme
    from lspr_imaging_app.image_tools.mask import export
    from lspr_imaging_app.image_tools.mask.io import read_mask_image
    from lspr_imaging_app.image_tools.mask.model import MaskChange
    from lspr_imaging_app.progress import Cancelled
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

SHAPE = (40, 60)
NAMING = FrameNamingScheme.for_dataset([0, 61, 313], [500.0, 550.0, 620.0])


def _change(cube, wavelength, scope, x=5):
    plane = np.zeros(SHAPE, dtype=bool)
    plane[10:20, x : x + 8] = True
    return MaskChange((cube, wavelength), scope, plane)


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.folder = Path(self._tmp.name) / "out"
        self.changes = [_change(5, 500.0, "persistent"), _change(61, 620.0, "individual", 20), _change(61, 550.0, "individual", 30)]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_one_png_per_mask_named_per_frame_and_scope(self) -> None:
        result = export.export_masks(self.changes, self.folder, NAMING)
        self.assertEqual(result.written, 3)
        self.assertEqual(
            list(result.files),
            ["mask_cube005_wl500_persistent.png", "mask_cube061_wl620_individual.png", "mask_cube061_wl550_individual.png"],
        )
        for change, name in zip(self.changes, result.files):
            np.testing.assert_array_equal(read_mask_image(self.folder / name, SHAPE), change.mask)

    def test_the_names_follow_the_datasets_own_number_format(self) -> None:
        wide = FrameNamingScheme(cube_digits=4, wavelength_decimals=1)
        self.assertEqual(export.mask_file_name(_change(7, 500.5, "individual"), wide), "mask_cube0007_wl500.5_individual.png")

    def test_the_index_says_what_each_file_is_and_what_it_applies_to(self) -> None:
        export.export_masks(self.changes, self.folder, NAMING)
        with open(self.folder / export.INDEX_FILE_NAME, encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([r["scope"] for r in rows], ["persistent", "individual", "individual"])
        self.assertEqual([r["masked_pixels"] for r in rows], ["80", "80", "80"])
        self.assertIn("cube 5 and every cube after it", rows[0]["applies_to"])
        self.assertEqual(rows[1]["applies_to"], "this frame only")
        self.assertEqual((rows[1]["cube"], rows[1]["wavelength_nm"]), ("61", "620"))

    def test_planned_names_match_what_is_written(self) -> None:
        planned = export.planned_files(self.changes, NAMING)
        self.assertEqual(planned, list(export.export_masks(self.changes, self.folder, NAMING).files))

    def test_progress_runs_to_one_and_names_the_file(self) -> None:
        seen: list[tuple[float, str]] = []
        export.export_masks(self.changes, self.folder, NAMING, progress=lambda f, m: seen.append((f, m)))
        self.assertEqual([f for f, _ in seen], sorted(f for f, _ in seen))
        self.assertAlmostEqual(seen[-1][0], 1.0, delta=1e-9)
        self.assertIn("mask_cube005_wl500_persistent.png", seen[0][1])

    def test_cancel_stops_between_files_and_keeps_what_was_written(self) -> None:
        calls: list[int] = []

        def cancelled() -> bool:
            calls.append(1)
            return len(calls) > 2

        with self.assertRaises(Cancelled):
            export.export_masks(self.changes, self.folder, NAMING, cancelled=cancelled)
        self.assertEqual(len(list(self.folder.glob("mask_*.png"))), 2)
        self.assertFalse((self.folder / export.INDEX_FILE_NAME).exists())

    def test_existing_files_are_replaced(self) -> None:
        export.export_masks(self.changes, self.folder, NAMING)
        changed = [_change(5, 500.0, "persistent", 40)]
        export.export_masks(changed, self.folder, NAMING)
        np.testing.assert_array_equal(read_mask_image(self.folder / "mask_cube005_wl500_persistent.png", SHAPE), changed[0].mask)


if __name__ == "__main__":
    unittest.main()
