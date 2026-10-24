"""Pure-logic tests for the scan result file (`analysis/scan_store.py`) and the cell hash (`analysis/scan_fingerprint.py`).

h5py and temporary files, no Qt, no images: the plans come from plain-callable fakes and the arrays are filled with known numbers.
Decisions being pinned: plan doc `apps/LSPRi/eva/docs/analysis_redesign_plan_2026-10-19.md`, sections 15 and 18.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import scan_store
    from lspr_imaging_app.analysis.scan_arrays import new_arrays
    from lspr_imaging_app.analysis.scan_fingerprint import SCAN_ALGORITHM_VERSION, cell_hashes, recipe_json, recipe_of
    from lspr_imaging_app.analysis.scan_jobs import MaskRef, ScanSource, build_scan_plan, subset_plan
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.roi.model import AreaRoi, AreaRoiDetectionSettings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import h5py  # noqa: E402
import numpy as np  # noqa: E402

H, W = 40, 60
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
WAVELENGTHS = (0.0, 500.0, 510.0, 520.0)


class _App:
    """A small fake app whose every input can be changed, to see which changes the cell hash reacts to."""

    def __init__(self, cubes=4, shift=0.0, ids=(1, 2, 3)):
        self.cubes = cubes
        self.shift = shift
        self.ids = ids
        self.affines: dict = {}
        self.masks: dict = {}
        self.names: dict = {}
        self.folder = "/data/run1"
        self.background = BackgroundSettings(flatten_background_enabled=True, flatten_background_sigma_px=10.0)
        self.detection = AreaRoiDetectionSettings()

    def source(self) -> ScanSource:
        rois = [AreaRoi(i, 12.0 + 15.0 * k + self.shift, 14.0, 8.0, reference_inner_diameter_px=11.0, reference_outer_diameter_px=16.0)
                for k, i in enumerate(self.ids)]
        return ScanSource(
            cube_indices=lambda: tuple(range(self.cubes)),
            wavelengths_for_cube=lambda c: WAVELENGTHS,
            is_spectral=lambda c, w: w != 0.0,
            record_path=lambda c, w: f"{self.folder}/{self.names.get((c, w), f'c{c}_w{int(w)}.tif')}",
            rois=lambda: rois, rois_at=lambda c: rois, has_timeline=lambda: False,
            chromatic_affine=lambda c, w: self.affines.get((c, w), IDENTITY),
            chromatic_affine_between=lambda a, b: IDENTITY,
            resolve_mask=lambda c, w: MaskRef((c, w), "individual", lambda a=self.masks[(c, w)]: a) if (c, w) in self.masks else None,
            geometry=lambda: GeometrySettings(), background=lambda: self.background, detection=lambda: self.detection,
            default_reference_diameters=lambda: (11.0, 16.0),
            frame_times_ms=lambda: {(c, w): 1_700_000_000_000 + 1000 * c + int(w) for c in range(self.cubes) for w in WAVELENGTHS},
        )

    def plan(self, **kwargs):
        return build_scan_plan(self.source(), **kwargs)


def _filled(plan, base=1.0):
    """ScanArrays for the whole plan, every cube filled with recognisable numbers."""
    arrays = new_arrays(plan)
    for p in range(len(plan.jobs)):
        arrays.values["mean"][p] = base + p
        arrays.std[p] = 0.5 * (base + p)
        arrays.n_valid[p] = 100 + p
        arrays.n_nominal[p] = 120 + p
        arrays.n_masked[p] = 3 * p
        arrays.done[p] = True
    return arrays


class _TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "analysis" / scan_store.FILE_NAME

    def tearDown(self) -> None:
        self._tmp.cleanup()


class FileContentTests(_TempDirCase):
    def test_a_new_file_has_the_standard_identity_axes_and_nothing_computed(self) -> None:
        plan = _App().plan()
        stored = scan_store.ScanStore(self.path).prepare(plan, dataset_label="run1")
        self.assertTrue(np.all(stored == 0))
        self.assertFalse(list(self.path.parent.glob("*.tmp")))  # created under a temporary name, then renamed
        with h5py.File(self.path, "r") as f:
            for key in ("schema_name", "schema_version", "schema_major", "schema_minor", "format_name", "format_version", "app_name",
                        "app_version", "created_by", "created_at_utc", "started_at_utc"):
                self.assertIn(key, f.attrs, key)
            self.assertEqual(f.attrs["schema_name"], "lspri_scan")
            self.assertEqual(f.attrs["dataset"], "run1")
            self.assertEqual(f["processed/scan/value/mean"].shape, (4, 3, 3, 2))
            self.assertEqual(f["processed/scan/value/mean"].chunks, (1, 3, 3, 2))  # one cube per chunk
            self.assertEqual(list(f["axes/cube_index"][()]), [0, 1, 2, 3])
            self.assertEqual(list(f["axes/roi_id"][()]), [1, 2, 3])
            self.assertEqual(list(f["axes/wavelength_nm"][()]), [500.0, 510.0, 520.0])
            self.assertEqual([s.decode() if isinstance(s, bytes) else s for s in f["axes/side"][()]], ["sample", "reference"])
            ds = f["processed/scan/std"]
            self.assertEqual([ds.dims[i].label for i in range(4)], ["cube", "roi", "wavelength", "side"])  # xarray reads these
            self.assertEqual(ds.dims[0][0].name, "/axes/cube_index")
            self.assertEqual(list(f["metadata/recipes"]), ["1"])
            self.assertEqual(list(f["metadata/roi_layouts"]), ["1"])

    def test_values_come_back_exactly_with_axes_times_and_readable_metadata(self) -> None:
        plan = _App().plan()
        store = scan_store.ScanStore(self.path)
        store.prepare(plan)
        arrays = _filled(plan)
        hashes = cell_hashes(plan)
        for p in range(len(plan.jobs)):
            store.write_cube(plan, arrays, p, hashes[p])
        back = scan_store.read_scan(self.path)
        self.assertTrue(np.array_equal(back.values["mean"], arrays.values["mean"]))
        self.assertTrue(np.array_equal(back.std, arrays.std))
        self.assertTrue(np.array_equal(back.n_valid, arrays.n_valid))
        self.assertEqual(back.n_valid.dtype, np.uint32)
        self.assertTrue(np.array_equal(back.n_masked, arrays.n_masked))  # the link between a mask and a ROI survives the file
        self.assertEqual(back.n_masked.dtype, np.uint32)
        self.assertTrue(np.array_equal(back.cell_hash, hashes))
        self.assertTrue(back.computed().all())
        self.assertEqual(back.acquired_at_unix_ms[2, 0], 1_700_000_000_000 + 2000 + 500)
        self.assertTrue(np.all(back.recipe_id == 1) and np.all(back.layout_id == 1))
        self.assertIn('"algorithm_version": %d' % SCAN_ALGORITHM_VERSION, back.recipes[1])
        self.assertIn('"roi_id": 2', back.layouts[1])

    def test_a_partial_run_leaves_the_other_cubes_never_computed(self) -> None:
        plan = _App().plan()
        store = scan_store.ScanStore(self.path)
        store.prepare(plan)
        part = subset_plan(plan, [3, 1])
        arrays = _filled(part)
        hashes = cell_hashes(plan)
        for p, cube in enumerate(part.cube_indices):
            store.write_cube(part, arrays, p, hashes[cube])
        back = scan_store.read_scan(self.path)
        self.assertEqual(list(back.computed().any(axis=1)), [False, True, False, True])
        self.assertTrue(np.all(back.values["mean"][3] == 1.0))  # the first cube of the partial run was cube 3
        self.assertTrue(np.all(back.values["mean"][1] == 2.0))
        self.assertTrue(np.all(np.isnan(back.values["mean"][0])))

    def test_stale_cube_positions(self) -> None:
        stored = np.array([[1, 2], [3, 4], [0, 0]], dtype=np.uint64)
        current = np.array([[1, 2], [3, 9], [5, 6]], dtype=np.uint64)
        self.assertEqual(scan_store.stale_cube_positions(stored, current), [1, 2])
        self.assertEqual(scan_store.stale_cube_positions(stored[:2], current), [0, 1, 2])  # shapes differ: everything


class ReadersRefuseWhatTheyDoNotKnow(_TempDirCase):
    def _file(self, **attrs) -> None:
        plan = _App().plan()
        scan_store.ScanStore(self.path).prepare(plan)
        with h5py.File(self.path, "r+") as f:
            for key, value in attrs.items():
                f.attrs[key] = value

    def test_unknown_schema_name_is_rejected(self) -> None:
        self._file(schema_name="something_else")
        with self.assertRaises(scan_store.ScanFileError):
            scan_store.read_scan(self.path)

    def test_a_newer_major_version_is_rejected_and_a_newer_minor_is_read(self) -> None:
        self._file(schema_major=2, schema_version="2.0")
        with self.assertRaises(scan_store.ScanFileError):
            scan_store.read_scan(self.path)
        self._file(schema_major=1, schema_minor=7, schema_version="1.7")
        self.assertEqual(scan_store.read_scan(self.path).cube_indices, (0, 1, 2, 3))

    def test_a_missing_or_damaged_file_is_a_scan_file_error(self) -> None:
        with self.assertRaises(scan_store.ScanFileError):
            scan_store.read_scan(self.dir / "nothing.h5")
        (self.dir / "junk.h5").write_bytes(b"not hdf5 at all")
        with self.assertRaises(scan_store.ScanFileError):
            scan_store.read_scan(self.dir / "junk.h5")


class WritingIsCrashSafe(_TempDirCase):
    def test_a_failure_in_the_middle_of_a_cube_leaves_it_never_computed_not_stale_hashed(self) -> None:
        plan = _App().plan()
        store = scan_store.ScanStore(self.path)
        store.prepare(plan)
        arrays = _filled(plan)
        hashes = cell_hashes(plan)
        store.write_cube(plan, arrays, 1, hashes[1])
        self.assertTrue(scan_store.read_scan(self.path).computed()[1].all())
        broken = _filled(plan, base=50.0)
        broken.n_nominal = broken.n_nominal[:, :2]  # the last write of the cube's data fails: 2 ROI rows instead of 3 (cannot broadcast)
        with self.assertRaises(Exception):
            store.write_cube(plan, broken, 1, hashes[1])
        back = scan_store.read_scan(self.path)
        self.assertFalse(back.computed()[1].any())  # the old hash was removed first: half-new values can never count as current
        self.assertTrue(back.computed()[[0, 2, 3]].sum() == 0)

    def test_the_hash_is_written_last(self) -> None:
        plan = _App().plan()
        store = scan_store.ScanStore(self.path)
        store.prepare(plan)
        arrays = _filled(plan)
        seen = {}
        original = h5py.File.flush

        def spy(self_):
            if "state/cell_hash" in self_ and "processed/scan/std" in self_:
                seen.setdefault("hash_at_flush", []).append(bool(self_["state/cell_hash"][0].any()))
                seen.setdefault("std_at_flush", []).append(bool(np.any(~np.isnan(self_["processed/scan/std"][0]))))
            return original(self_)

        h5py.File.flush = spy
        try:
            store.write_cube(plan, arrays, 0, cell_hashes(plan)[0])
        finally:
            h5py.File.flush = original
        # flush 1: hash cleared; flush 2: values written, hash still 0; flush 3: hash set
        self.assertEqual(seen["hash_at_flush"][:2], [False, False])
        self.assertEqual(seen["std_at_flush"][1], True)
        self.assertTrue(scan_store.read_scan(self.path).computed()[0].all())


class ReuseAndReplace(_TempDirCase):
    def test_a_compatible_file_is_reused_and_recipes_are_not_duplicated(self) -> None:
        app = _App()
        plan = app.plan()
        store = scan_store.ScanStore(self.path)
        store.prepare(plan)
        store.write_cube(plan, _filled(plan), 2, cell_hashes(plan)[2])
        again = scan_store.ScanStore(self.path).prepare(plan)
        self.assertTrue(np.array_equal(again[2], cell_hashes(plan)[2]))
        self.assertTrue(np.all(again[[0, 1, 3]] == 0))
        with h5py.File(self.path, "r") as f:
            self.assertEqual(list(f["metadata/recipes"]), ["1"])
            self.assertEqual(list(f["metadata/roi_layouts"]), ["1"])
        app.background = replace(app.background, flatten_background_sigma_px=24.0)
        changed = app.plan()
        scan_store.ScanStore(self.path).prepare(changed)
        with h5py.File(self.path, "r") as f:
            self.assertEqual(sorted(f["metadata/recipes"]), ["1", "2"])
            self.assertIn('"flatten_background_sigma_px": 24.0', f["metadata/recipes/2"].asstr()[()])
            self.assertIn('"flatten_background_sigma_px": 10.0', f["metadata/recipes/1"].asstr()[()])  # the old one stays readable

    def test_a_file_for_other_rois_is_kept_as_previous_and_a_new_one_is_made(self) -> None:
        plan = _App().plan()
        store = scan_store.ScanStore(self.path)
        store.prepare(plan)
        store.write_cube(plan, _filled(plan), 0, cell_hashes(plan)[0])
        other = _App(ids=(1, 2, 3, 4)).plan()
        stored = scan_store.ScanStore(self.path).prepare(other)
        self.assertTrue(np.all(stored == 0))
        backup = self.path.with_name(scan_store.BACKUP_NAME)
        self.assertTrue(backup.exists())
        self.assertTrue(scan_store.read_scan(backup).computed()[0].all())  # nothing was lost
        self.assertEqual(scan_store.read_scan(self.path).roi_ids, (1, 2, 3, 4))

    def test_a_schema_1_0_file_without_mask_counts_is_kept_as_previous_and_replaced(self) -> None:
        plan = _App().plan()
        store = scan_store.ScanStore(self.path)
        store.prepare(plan)
        store.write_cube(plan, _filled(plan), 0, cell_hashes(plan)[0])
        with h5py.File(self.path, "r+") as f:  # make it look like a file from before n_masked existed
            del f["processed/scan/n_masked"]
            f.attrs["schema_version"], f.attrs["schema_minor"] = "1.0", 0
        with self.assertRaises(scan_store.ScanFileError) as caught:
            scan_store.read_scan(self.path)
        self.assertIn("scan again", str(caught.exception))
        stored = scan_store.ScanStore(self.path).prepare(plan)
        self.assertTrue(np.all(stored == 0))  # every cell has to be computed again
        self.assertTrue(self.path.with_name(scan_store.BACKUP_NAME).exists())
        self.assertEqual(scan_store.read_scan(self.path).n_masked.shape, (4, 3, 3, 2))

    def test_a_file_that_is_not_ours_is_moved_aside_not_overwritten(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"precious")
        scan_store.ScanStore(self.path).prepare(_App().plan())
        self.assertEqual(self.path.with_name(scan_store.BACKUP_NAME).read_bytes(), b"precious")
        self.assertEqual(scan_store.read_scan(self.path).cube_indices, (0, 1, 2, 3))


class CellHashTests(unittest.TestCase):
    def _hashes(self, app: _App):
        return cell_hashes(app.plan())

    def test_the_same_inputs_give_the_same_nonzero_hashes_that_differ_per_roi(self) -> None:
        a, b = self._hashes(_App()), self._hashes(_App())
        self.assertTrue(np.array_equal(a, b))
        self.assertEqual(a.dtype, np.uint64)
        self.assertTrue((a != 0).all())
        self.assertEqual(len(set(a[0].tolist())), 3)

    def test_inputs_that_change_the_numbers_change_the_hash(self) -> None:
        base = self._hashes(_App())
        # a recipe setting
        app = _App()
        app.background = replace(app.background, flatten_background_sigma_px=24.0)
        self.assertTrue((self._hashes(app) != base).all())
        # a ROI moves: every cell of every cube (the other ROIs' rings exclude it)
        self.assertTrue((self._hashes(_App(shift=0.5)) != base).all())
        # one frame's chromatic affine: only that cube
        app = _App()
        app.affines[(2, 510.0)] = np.array([[1.001, 0.0, 0.2], [0.0, 1.0, 0.0]])
        h = self._hashes(app)
        self.assertEqual(list((h != base).any(axis=1)), [False, False, True, False])
        # the CONTENT of an ignore mask (same shape, one pixel flipped)
        app = _App()
        mask = np.zeros((H, W), dtype=bool)
        mask[5:9, 5:9] = True
        app.masks[(1, 500.0)] = mask
        with_mask = self._hashes(app)
        self.assertEqual(list((with_mask != base).any(axis=1)), [False, True, False, False])
        flipped = mask.copy()
        flipped[20, 20] = True
        app.masks[(1, 500.0)] = flipped
        self.assertEqual(list((self._hashes(app) != with_mask).any(axis=1)), [False, True, False, False])
        # a different file name for a frame
        app = _App()
        app.names[(3, 520.0)] = "replaced.tif"
        self.assertEqual(list((self._hashes(app) != base).any(axis=1)), [False, False, False, True])

    def test_inputs_that_do_not_change_the_numbers_do_not_change_the_hash(self) -> None:
        base = self._hashes(_App())
        moved = _App()
        moved.folder = "D:/somewhere/else/entirely"  # the dataset was moved
        self.assertTrue(np.array_equal(self._hashes(moved), base))
        app = _App()
        app.detection = replace(app.detection, formula_key="transmittance", reduction_method="median", array_rows=4)
        self.assertTrue(np.array_equal(self._hashes(app), base))  # formula and fit settings are never stored, so never stale a scan
        app = _App()
        self.assertTrue(np.array_equal(cell_hashes(app.plan(fast_tiff_decoder=False)), base))

    def test_the_same_mask_in_a_second_array_gives_the_same_hash(self) -> None:
        mask = np.zeros((H, W), dtype=bool)
        mask[5:9, 5:9] = True
        a, b = _App(), _App()
        a.masks[(1, 500.0)] = mask
        b.masks[(1, 500.0)] = mask.copy()
        self.assertTrue(np.array_equal(self._hashes(a), self._hashes(b)))

    def test_the_recipe_text_is_readable_and_stable(self) -> None:
        plan = _App().plan()
        text = recipe_json(recipe_of(plan.settings))
        self.assertEqual(text, recipe_json(recipe_of(plan.settings)))
        self.assertIn('"exclusion_mode"', text)
        self.assertNotIn("formula_key", text)


if __name__ == "__main__":
    unittest.main()
