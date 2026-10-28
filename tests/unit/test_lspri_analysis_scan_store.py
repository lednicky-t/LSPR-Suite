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
from unittest import mock

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


class UncompressedFileTests(_TempDirCase):
    """2026-10-28: the big arrays are stored plain (no growth when ROI rows are rewritten, a faster writer); a gzip file from before is converted once."""

    def _gzip_file(self, plan):
        """A scan file as the app wrote them before: the four-dimensional arrays and the acquisition times gzip-compressed, shuffled."""
        original = h5py.Group.create_dataset

        def create(group, name, *args, **kwargs):
            if len(kwargs.get("shape", ())) == 4 or name == "acquired_at_unix_ms":
                kwargs.update(compression="gzip", compression_opts=4, shuffle=len(kwargs.get("shape", ())) == 4)
            return original(group, name, *args, **kwargs)

        store = scan_store.ScanStore(self.path)
        with mock.patch.object(h5py.Group, "create_dataset", create):
            store.prepare(plan, dataset_label="old")
        arrays = _filled(plan, base=7.0)
        hashes = cell_hashes(plan)
        for p in range(len(plan.jobs) - 1):  # the last cube stays "never computed"
            store.write_cube(plan, arrays, p, hashes[p])
        return store, arrays, hashes

    def _all_datasets_plain(self) -> bool:
        with h5py.File(self.path, "r") as f:
            return all(f[path].compression is None for path in scan_store._array_paths(f))

    def test_a_new_file_is_not_compressed(self) -> None:
        scan_store.ScanStore(self.path).prepare(_App().plan(), dataset_label="run1")
        self.assertTrue(self._all_datasets_plain())

    def test_rewriting_one_roi_of_every_cube_again_and_again_does_not_change_the_file_size(self) -> None:
        plan = _App(cubes=6).plan()
        store = scan_store.ScanStore(self.path)
        store.prepare(plan, dataset_label="run1")
        arrays, hashes = _filled(plan), cell_hashes(plan)
        for p in range(len(plan.jobs)):
            store.write_cube(plan, arrays, p, hashes[p])
        size = self.path.stat().st_size
        rng = np.random.default_rng(4)
        for _ in range(5):
            arrays.values["mean"][:, 1] = rng.random(arrays.values["mean"][:, 1].shape)  # numbers that do not compress alike
            arrays.std[:, 1] = rng.random(arrays.std[:, 1].shape)
            for p in range(len(plan.jobs)):
                store.write_cells(plan, arrays, p, (1,), hashes[p])
            self.assertEqual(self.path.stat().st_size, size)

    def test_a_gzip_file_is_converted_once_with_every_number_and_hash_kept(self) -> None:
        plan = _App().plan()
        store, arrays, hashes = self._gzip_file(plan)
        self.assertFalse(self._all_datasets_plain())
        before = scan_store.read_scan(self.path)
        again = scan_store.ScanStore(self.path)
        stored = again.prepare(plan, dataset_label="old")
        self.assertTrue(self._all_datasets_plain())
        self.assertFalse(list(self.path.parent.glob("*.tmp")))
        self.assertTrue(np.array_equal(stored, hashes.astype(np.uint64) * (np.arange(len(plan.jobs)) < len(plan.jobs) - 1)[:, None]))
        after = scan_store.read_scan(self.path)
        for name in ("std", "n_valid", "n_nominal", "n_masked", "cell_hash", "recipe_id", "layout_id", "acquired_at_unix_ms"):
            self.assertTrue(np.array_equal(getattr(before, name), getattr(after, name), equal_nan=True), name)
        self.assertTrue(np.array_equal(before.values["mean"], after.values["mean"], equal_nan=True))
        self.assertEqual((before.recipes, before.layouts, before.cube_indices, before.roi_ids), (after.recipes, after.layouts, after.cube_indices, after.roi_ids))
        self.assertEqual(before.attrs, after.attrs)
        # the file is still a working file: the next cube can be written and read back
        again.write_cube(plan, arrays, len(plan.jobs) - 1, hashes[-1])
        self.assertTrue(np.all(scan_store.read_scan(self.path).cell_hash == hashes))

    def test_a_converted_file_is_not_converted_again(self) -> None:
        plan = _App().plan()
        self._gzip_file(plan)
        scan_store.ScanStore(self.path).prepare(plan, dataset_label="old")
        with mock.patch.object(scan_store.ScanStore, "_convert_to_uncompressed") as convert:
            scan_store.ScanStore(self.path).prepare(plan, dataset_label="old")
        convert.assert_not_called()

    def test_a_conversion_that_finds_a_difference_keeps_the_original_file(self) -> None:
        plan = _App().plan()
        self._gzip_file(plan)
        before = self.path.read_bytes()
        with mock.patch.object(scan_store, "_same_scan_content", return_value=False):
            scan_store.ScanStore(self.path).prepare(plan, dataset_label="old")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(list(self.path.parent.glob("*.tmp")))

    def test_a_conversion_that_cannot_swap_the_file_in_keeps_the_original_and_the_run_goes_on(self) -> None:
        plan = _App().plan()
        self._gzip_file(plan)
        before = self.path.read_bytes()
        with mock.patch.object(scan_store.os, "replace", side_effect=PermissionError("held by a reader")):
            stored = scan_store.ScanStore(self.path).prepare(plan, dataset_label="old")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(list(self.path.parent.glob("*.tmp")))
        self.assertEqual(stored.shape, (len(plan.jobs), len(plan.roi_ids)))


def _reference_cell_hashes(plan) -> np.ndarray:
    """`scan_fingerprint.cell_hashes` as it was written until 2026-10-27, cell by cell: the stored hashes of every scan file are made this way, so
    the faster version must give exactly these numbers (a different hash would make every stored cell read as out of date)."""
    import hashlib

    from lspr_imaging_app.analysis import scan_fingerprint as fp
    from lspr_imaging_app.analysis.frame_reduce import local_roi_keys

    base = fp.recipe_digest(fp.recipe_of(plan.settings))
    ignore_cache: dict[int, bytes] = {}
    global_layout = fp.layout_is_global(plan.settings)
    local_keys: dict[str, list[bytes]] = {}
    out = np.zeros((len(plan.jobs), len(plan.roi_ids)), dtype=np.uint64)
    for p, job in enumerate(plan.jobs):
        cube = hashlib.blake2b(digest_size=16)
        cube.update(base)
        if global_layout:
            cube.update(job.rois.key.encode("ascii"))
        elif job.rois.key not in local_keys:
            local_keys[job.rois.key] = local_roi_keys(job.rois, plan.settings.exclusion_mode)
        for column, frame in zip(plan.axis_positions[p], job.frames):
            cube.update(np.int64(column).tobytes())
            cube.update(frame.path.replace("\\", "/").rsplit("/", 1)[-1].encode("utf-8"))
            cube.update(np.ascontiguousarray(frame.affine, dtype=np.float64).tobytes())
            if frame.ignore_key is not None:
                cube.update(frame.ignore_key)
            elif frame.ignore is None:
                cube.update(b"-")
            else:
                key = id(frame.ignore)
                if key not in ignore_cache:
                    ignore_cache[key] = fp._ignore_digest(frame.ignore)
                cube.update(ignore_cache[key])
        digest = cube.digest()
        keys = None if global_layout else local_keys[job.rois.key]
        for r, roi_id in enumerate(plan.roi_ids):
            own = b"" if keys is None else keys[r]
            h = hashlib.blake2b(digest + own + np.int64(roi_id).tobytes() + np.int64(r).tobytes(), digest_size=8).digest()
            out[p, r] = int.from_bytes(h, "little") or 1
    return out


class CellHashMatchesTheOriginalTests(unittest.TestCase):
    """The hash loop was rewritten for speed (2026-10-28); it must give the original's numbers in every situation the plan can be in."""

    def _same(self, plan) -> None:
        # ``local_masks=False`` is the formula of the reference (the whole ignore mask in every cell); the default only differs for cubes that have masks
        self.assertTrue(np.array_equal(cell_hashes(plan, local_masks=False), _reference_cell_hashes(plan)))
        has_mask = [any(f.ignore_key is not None or f.ignore is not None for f in job.frames) for job in plan.jobs]
        default = cell_hashes(plan)
        reference = _reference_cell_hashes(plan)
        self.assertTrue(np.array_equal(default[[not m for m in has_mask]], reference[[not m for m in has_mask]]))  # cubes without masks: unchanged

    def test_plain_plan_and_many_rois(self) -> None:
        self._same(_App().plan())
        self._same(_App(cubes=7, ids=tuple(range(1, 41))).plan())

    def test_plans_with_masks_chromatic_affines_and_other_names(self) -> None:
        app = _App(cubes=5)
        mask = np.zeros((H, W), dtype=bool)
        mask[5:15, 5:20] = True
        app.masks[(1, 500.0)] = mask
        app.masks[(3, 510.0)] = mask.copy()
        app.affines[(2, 520.0)] = np.array([[1.002, 0.001, -1.4], [-0.001, 0.999, 0.7]])
        app.names[(4, 500.0)] = "other_name.tif"
        self._same(app.plan())
        self._same(app.plan(resolve_masks=False))

    def test_global_layout_and_a_hand_built_frame_without_a_mask_key(self) -> None:
        app = _App(cubes=3)
        app.background = BackgroundSettings(flatten_background_enabled=True, flatten_background_exclude_area_rois=True)
        plan = app.plan()
        self._same(plan)
        keyless = []
        for job in plan.jobs:
            frames = tuple(replace(f, ignore_key=None, ignore=_ignore_for(f)) if k == 1 else f for k, f in enumerate(job.frames))
            keyless.append(replace(job, frames=frames))
        self._same(replace(plan, jobs=tuple(keyless)))

    def test_affines_that_are_not_float64_or_not_contiguous(self) -> None:
        plan = _App(cubes=2).plan()
        odd = []
        for job in plan.jobs:
            frames = []
            for k, f in enumerate(job.frames):
                affine = f.affine.astype(np.float32) if k == 0 else np.asfortranarray(f.affine) if k == 1 else f.affine.tolist()
                frames.append(replace(f, affine=affine))
            odd.append(replace(job, frames=tuple(frames)))
        odd_plan = replace(plan, jobs=tuple(odd))
        self.assertTrue(np.array_equal(cell_hashes(odd_plan), _reference_cell_hashes(odd_plan)))


def _ignore_for(frame):
    out = np.zeros((H, W), dtype=bool)
    out[2:9, 3:12] = True
    return out


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
        # every ROI moves: every cell of every cube (a single moved ROI changes only its own cells: test_lspri_scan_local_staleness.py)
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
