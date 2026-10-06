"""Stored analysis results must follow their ROI when ROI ids are renumbered.

Results are filed under the ROI's id (in memory and as `/cells/roi_<id>` in
`data.h5`). When a delete closes the gap, or a fresh detection reuses 1..N,
the id of a ROI changes or is reused; a result left under the old number
would be shown for a different ROI. These are the pure parts of the fix: the
file rename, the numbering-independent exclusion digests, and the
`roi_ids_renumbered` signal contract. The engine itself, end to end, is in
`tests/integration/test_lspri_rewrite_roi_renumber.py`.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch**, like the
other `test_lspri_rewrite_*` files.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import h5py

    from lspr_imaging_app.analysis.provenance import (
        ProvenanceRecord,
        background_exclusion_digest,
        sample_exclusion_digest,
    )
    from lspr_imaging_app.analysis.store import read_all_cells, remap_cell_roi_ids, write_cell
    from lspr_imaging_app.analysis.tasks import CellResult
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.roi.model import AreaRoi, AreaRoiDetectionSettings
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc


def _cell(marker: float) -> CellResult:
    """A cell whose `sample_values` identifies which ROI it was written for."""
    return CellResult(
        wavelengths_nm=(500.0,),
        sample_values=(marker,),
        reference_values=(1.0,),
        provenance=ProvenanceRecord(roi_geometry={"center_x": marker}, reduction_method="mean", per_wavelength_settings=()),
    )


class RemapCellRoiIdsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(self._tmp.name) / "analysis" / "data.h5"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, *roi_ids: int, cubes: tuple[int, ...] = (0,)) -> None:
        for roi_id in roi_ids:
            for cube in cubes:
                write_cell(self.path, roi_id, cube, _cell(float(roi_id)))

    def _markers(self) -> dict[tuple[int, int], float]:
        return {key: cell.sample_values[0] for key, cell in read_all_cells(self.path).items()}

    def test_a_cycle_swaps_two_rois_results(self) -> None:
        """1->2 and 2->1 cannot be done by renaming one group at a time."""
        self._write(1, 2, 3)
        remap_cell_roi_ids(self.path, {1: 2, 2: 1, 3: 3})
        self.assertEqual(self._markers(), {(2, 0): 1.0, (1, 0): 2.0, (3, 0): 3.0})

    def test_every_cube_of_a_roi_moves_together(self) -> None:
        self._write(1, 2, cubes=(0, 1, 2))
        remap_cell_roi_ids(self.path, {1: 2, 2: 1})
        markers = self._markers()
        self.assertEqual(len(markers), 6)
        self.assertTrue(all(markers[(2, cube)] == 1.0 and markers[(1, cube)] == 2.0 for cube in (0, 1, 2)))

    def test_a_deleted_roi_is_dropped_and_the_gap_closes(self) -> None:
        self._write(1, 2, 3)
        remap_cell_roi_ids(self.path, {1: 1, 3: 2})
        self.assertEqual(self._markers(), {(1, 0): 1.0, (2, 0): 3.0})

    def test_an_empty_map_drops_everything(self) -> None:
        self._write(1, 2)
        remap_cell_roi_ids(self.path, {})
        self.assertEqual(self._markers(), {})

    def test_two_ids_to_one_target_is_refused_and_changes_nothing(self) -> None:
        self._write(1, 2)
        with self.assertRaises(ValueError):
            remap_cell_roi_ids(self.path, {1: 1, 2: 1})
        self.assertEqual(self._markers(), {(1, 0): 1.0, (2, 0): 2.0})

    def test_a_missing_file_or_empty_store_is_a_no_op(self) -> None:
        remap_cell_roi_ids(self.path, {1: 2})  # no file yet
        self.assertFalse(self.path.exists())
        self.path.parent.mkdir(parents=True)
        with h5py.File(self.path, "w"):
            pass
        remap_cell_roi_ids(self.path, {1: 2})  # file without /cells
        self.assertEqual(self._markers(), {})

    def test_the_reader_skips_a_leftover_temporary_group(self) -> None:
        """What a crash between the two rename steps would leave behind: shown
        as "not analyzed", never as another ROI's result."""
        self._write(1)
        with h5py.File(self.path, "a") as handle:
            handle.create_group("/cells/roi_tmp_4/cube_0")
        self.assertEqual(self._markers(), {(1, 0): 1.0})


def _roi(roi_id: int, x: float, y: float, diameter: float = 6.0) -> AreaRoi:
    return AreaRoi(area_roi_id=roi_id, center_x=x, center_y=y, sample_diameter_px=diameter)


class ExclusionDigestIgnoresNumberingTest(unittest.TestCase):
    """A reorder must not look like a change to the set of excluded circles,
    or it would mark every stored cell stale in those modes."""

    def setUp(self) -> None:
        self.a = _roi(1, 40.0, 30.0)
        self.b = _roi(2, 47.0, 34.0, diameter=8.0)
        self.c = _roi(3, 20.0, 10.0)

    def _swapped(self) -> list[AreaRoi]:
        return [replace(self.b, area_roi_id=1), replace(self.a, area_roi_id=2), self.c]

    def test_the_sample_digest_is_the_same_after_a_reorder(self) -> None:
        before = sample_exclusion_digest([self.a, self.b, self.c])
        self.assertEqual(sample_exclusion_digest(self._swapped()), before)
        self.assertEqual(sample_exclusion_digest(list(reversed([self.a, self.b, self.c]))), before)

    def test_the_sample_digest_still_changes_when_a_circle_moves(self) -> None:
        before = sample_exclusion_digest([self.a, self.b, self.c])
        moved = [replace(self.a, center_x=41.0), self.b, self.c]
        self.assertNotEqual(sample_exclusion_digest(moved), before)

    def test_the_background_digest_is_the_same_after_a_reorder(self) -> None:
        settings = BackgroundSettings(flatten_background_enabled=True, flatten_background_exclude_area_rois=True)
        detection = AreaRoiDetectionSettings()
        before = background_exclusion_digest([self.a, self.b, self.c], settings, detection)
        self.assertIsNotNone(before)
        self.assertEqual(background_exclusion_digest(self._swapped(), settings, detection), before)

    def test_the_background_digest_still_changes_when_a_circle_resizes(self) -> None:
        settings = BackgroundSettings(flatten_background_enabled=True, flatten_background_exclude_area_rois=True)
        detection = AreaRoiDetectionSettings()
        before = background_exclusion_digest([self.a, self.b, self.c], settings, detection)
        resized = [replace(self.a, sample_diameter_px=9.0), self.b, self.c]
        self.assertNotEqual(background_exclusion_digest(resized, settings, detection), before)


class RenumberSignalContractTest(unittest.TestCase):
    """`roi_ids_renumbered` carries `{old: new}` for every ROI that survives;
    an id absent from it is gone. Subscribers (selection, the analysis
    engine) rely on that, including for the all-deleted and re-detected
    cases, where an empty map is the whole message."""

    def setUp(self) -> None:
        undo_manager.clear()
        self.toolbox = RoiToolbox()
        self.maps: list[dict[int, int]] = []
        self.toolbox.roi_ids_renumbered.connect(self.maps.append)
        for x in (10.0, 20.0, 30.0):
            self.toolbox.add_roi(x, 5.0)

    def tearDown(self) -> None:
        undo_manager.clear()

    def test_deleting_one_roi_maps_every_survivor_and_undo_maps_back(self) -> None:
        self.toolbox.delete_rois((1,))
        self.assertEqual(self.maps, [{2: 1, 3: 2}])
        undo_manager.undo()
        self.assertEqual(self.maps[-1], {1: 2, 2: 3})

    def test_deleting_every_roi_still_announces_it(self) -> None:
        self.toolbox.delete_rois((1, 2, 3))
        self.assertEqual(self.maps, [{}])

    def test_a_fresh_detection_announces_an_empty_map_both_ways(self) -> None:
        self.toolbox.detect_rois([_roi(1, 50.0, 50.0), _roi(2, 60.0, 60.0)])
        self.assertEqual(self.maps, [{}])
        undo_manager.undo()
        self.assertEqual(self.maps, [{}, {}])
        self.assertEqual(sorted(roi.area_roi_id for roi in self.toolbox.rois()), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
