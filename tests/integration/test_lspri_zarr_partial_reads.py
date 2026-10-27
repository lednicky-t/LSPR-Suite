"""Chunk reads of an OME-Zarr export for the analysis scan (2026-10-27): `dataset.io.read_plane_pixels` and what the scan worker does with it.

The reader has to give, bit for bit, what reading the whole plane and picking the pixels gives (`load_image_array_uncached` + indexing),
on exports written by the app's own writer in every shard mode, with and without compression, with edge chunks that are only partly
inside the image, and with a `valid_mask` (rotated export: NaN where no pixel was measured). The scan then has to give identical cells
with chunk reads and with whole planes, and has to fall back to whole planes whenever chunk reads do not apply (background removal on, a
rotation, too many chunks, a reader that returns nothing). Design record: apps/LSPRi/eva/docs/roi_nudge_partial_scan_proposal_2026-10-26.md.

Real exports (a few seconds), no Qt except the one `ScanTask` run at the end.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import numpy as np
import tifffile

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from PyQt6 import QtWidgets

    import lspr_imaging_app.dataset.io as dio
    from lspr_imaging_app.analysis.scan import PARTIAL_READ_MAX_SHARE, scan_cubes
    from lspr_imaging_app.analysis.scan_jobs import ScanSource, build_scan_plan, snapshot_source
    from lspr_imaging_app.analysis.scan_task import ScanTask
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.image_tools.geometry.transform import spatial_transform_is_identity
    from lspr_imaging_app.roi.model import AreaRoi, AreaRoiDetectionSettings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

H, W = 96, 128
WAVELENGTHS = (500.0, 550.0, 600.0)
CUBES = (0, 1)
CHUNK = (16, 16)
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])


def _rois():
    """8 ROIs on a lattice: sample 10 px, ring 14 / 20 px (a ring box of 21 px, so one ROI touches at most 4 of the 48 chunks)."""
    return [AreaRoi(i, 18.0 + 25.0 * (k % 4), 18.0 + 24.0 * (k // 4), 10.0, reference_inner_diameter_px=14.0, reference_outer_diameter_px=20.0)
            for k, i in enumerate(range(1, 9))]


class _Exports:
    """The same small TIFF dataset exported in the forms the reader has to understand."""

    VARIANTS = {
        "image_lz4": dict(shard_mode="per_image", compression_enabled=True, chunk_shape=(16, 12)),  # rectangular; 128 / 12 leaves an edge chunk
        "cube_lz4": dict(shard_mode="per_spectral_cube", compression_enabled=True, chunk_shape=CHUNK),
        "cube_raw": dict(shard_mode="per_spectral_cube", compression_enabled=False, chunk_shape=(24, 24)),  # 96 / 24 fits, 128 / 24 does not
        "rotated": dict(shard_mode="per_spectral_cube", compression_enabled=True, chunk_shape=CHUNK, rotate=6.0),  # has a valid_mask
    }

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        source = self.root / "src"
        source.mkdir()
        records = []
        for cube in CUBES:
            for wavelength in WAVELENGTHS:
                image = np.random.default_rng(int(wavelength) + 7 * cube).integers(1, 4096, size=(H, W), dtype=np.uint16)
                path = source / f"c{cube}_w{wavelength:.0f}.tif"
                tifffile.imwrite(str(path), image)
                records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=cube), path=path))
        self.dataset = ImageDataset(folder=source, records=records)
        self.exports: dict[str, Path] = {}
        for name, options in self.VARIANTS.items():
            options = dict(options)
            rotate = options.pop("rotate", None)
            self.exports[name] = dio.export_ome_zarr_dataset(
                self.dataset, self.root / f"{name}.ome.zarr", preprocessing=GeometrySettings(rotation_angle_deg=rotate) if rotate else None,
                adaptive_workers_enabled=False, **options,
            )

    def close(self) -> None:
        self._tmp.cleanup()


def _plane_paths(export: Path) -> list[str]:
    return [str(record.path) for record in dio.load_ome_zarr_dataset(export).records]


def _pixel_sets(grid: dio.ZarrPlaneGrid) -> list[np.ndarray]:
    rng = np.random.default_rng(5)
    n = grid.height * grid.width
    corner = np.arange(grid.height * grid.width).reshape(grid.height, grid.width)[:6, :6].ravel()
    last = np.arange(n).reshape(grid.height, grid.width)[-5:, -5:].ravel()  # the edge chunks, partly outside the image
    return [
        np.array([0]), np.array([n - 1]), np.sort(rng.choice(n, 40, replace=False)), np.sort(rng.choice(n, 700, replace=False)),
        corner, last, np.array([], dtype=np.intp), rng.choice(n, 90, replace=False),  # the last one is not sorted
    ]


class ChunkReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.made = _Exports()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.made.close()

    def test_every_pixel_set_equals_the_whole_plane_read_bit_for_bit(self) -> None:
        for name, export in self.made.exports.items():
            for path in _plane_paths(export):
                grid = dio.ome_zarr_plane_grid(path)
                self.assertIsNotNone(grid, name)
                whole = dio.load_image_array_uncached(path).reshape(-1)
                for flat in _pixel_sets(grid):
                    with self.subTest(export=name, plane=Path(path).name, pixels=int(flat.size)):
                        plan = dio.partial_read_plan(grid, flat)
                        values = dio.read_plane_pixels(path, plan)
                        self.assertIsNotNone(values)
                        self.assertEqual(values.dtype, np.float32)
                        np.testing.assert_array_equal(values, whole[flat])  # NaN counts as equal: the valid mask is part of the contract

    def test_the_rotated_export_has_nan_pixels_and_the_chunk_read_returns_them(self) -> None:
        path = _plane_paths(self.made.exports["rotated"])[0]
        grid = dio.ome_zarr_plane_grid(path)
        values = dio.read_plane_pixels(path, dio.partial_read_plan(grid, np.arange(grid.height * grid.width)))
        self.assertTrue(np.isnan(values).any())
        self.assertTrue(np.isfinite(values).any())

    def test_a_chunk_read_decodes_only_the_chunks_it_was_asked_for(self) -> None:
        path = _plane_paths(self.made.exports["cube_lz4"])[1]
        grid = dio.ome_zarr_plane_grid(path)
        flat = np.arange(grid.height * grid.width).reshape(grid.height, grid.width)[20:30, 20:30].ravel()  # rows and columns 20-29: inside chunk (1, 1)
        plan = dio.partial_read_plan(grid, flat)
        self.assertEqual(plan.chunk_ids.tolist(), [grid.n_cx + 1])
        dio.read_plane_pixels(path, plan)  # fills the codec cache
        decoded: list[int] = []
        real = dict(dio._blosc_codecs)

        class _Counting:
            def __init__(self, codec):
                self._codec = codec

            def decode(self, buf):
                decoded.append(1)
                return self._codec.decode(buf)

        dio._blosc_codecs.update({name: _Counting(codec) for name, codec in real.items()})
        try:
            dio.read_plane_pixels(path, plan)
        finally:
            dio._blosc_codecs.update(real)
        self.assertEqual(len(decoded), plan.chunk_ids.size)
        self.assertLess(plan.chunk_ids.size, grid.n_chunks // 4)

    def test_too_many_chunks_gives_no_plan_and_a_pixel_outside_the_plane_is_an_error(self) -> None:
        grid = dio.ome_zarr_plane_grid(_plane_paths(self.made.exports["cube_lz4"])[0])
        everything = np.arange(grid.height * grid.width)
        self.assertIsNone(dio.partial_read_plan(grid, everything, max_share=0.5))
        self.assertIsNotNone(dio.partial_read_plan(grid, everything, max_share=1.0))
        with self.assertRaises(ValueError):
            dio.partial_read_plan(grid, np.array([grid.height * grid.width]))
        with self.assertRaises(ValueError):
            dio.partial_read_plan(grid, np.array([-1]))

    def test_a_tiff_path_has_no_grid(self) -> None:
        self.assertIsNone(dio.ome_zarr_plane_grid(str(self.made.dataset.records[0].path)))

    def test_a_damaged_shard_index_gives_none_so_the_caller_reads_the_whole_plane(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            copy = Path(tmp) / "copy.ome.zarr"
            shutil.copytree(self.made.exports["image_lz4"], copy)
            path = _plane_paths(copy)[0]
            grid = dio.ome_zarr_plane_grid(path)
            plan = dio.partial_read_plan(grid, np.array([5, 900]))
            self.assertIsNotNone(dio.read_plane_pixels(path, plan))
            meta = dio._ome_zarr_fast_read_metadata(str(copy))
            shard, _, _ = dio._shard_location(meta, 0, 0)
            data = bytearray(shard.read_bytes())
            data[-1] ^= 0xFF  # the CRC of the index
            shard.write_bytes(bytes(data))
            self.assertIsNone(dio.read_plane_pixels(path, plan))

    def test_a_shard_that_was_rewritten_is_read_again_not_taken_from_the_cache(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            copy = Path(tmp) / "copy.ome.zarr"
            shutil.copytree(self.made.exports["image_lz4"], copy)
            paths = _plane_paths(copy)
            grid = dio.ome_zarr_plane_grid(paths[0])
            flat = np.arange(0, grid.height * grid.width, 37)
            plan = dio.partial_read_plan(grid, flat)
            dio.read_plane_pixels(paths[0], plan)  # now cached
            meta = dio._ome_zarr_fast_read_metadata(str(copy))
            first, _, _ = dio._shard_location(meta, 0, 0)
            second, _, _ = dio._shard_location(meta, 0, 1)
            time.sleep(0.05)
            shutil.copyfile(second, first)  # plane (0, 0) now holds what plane (0, 1) held
            expected = dio.load_image_array_uncached(paths[1]).reshape(-1)[flat]
            np.testing.assert_array_equal(dio.read_plane_pixels(paths[0], plan), expected)


class Crc32cTests(unittest.TestCase):
    """The checksum that guards a shard's index: the standard check value, and the slow fallback equals the fast package."""

    def test_the_standard_check_value_and_the_pure_python_fallback_equal_the_library(self) -> None:
        import os

        from lspr_imaging_app.io import _zarr_export_worker as worker

        self.assertEqual(worker._crc32c(b"123456789"), 0xE3069283)  # CRC-32C (Castagnoli) check value of the standard
        data = os.urandom(5000)
        with_library = worker._crc32c(data)
        with mock.patch.dict(sys.modules, {"crc32c": None}):  # the import now fails, so `_crc32c` falls back to its own loop
            self.assertEqual(worker._crc32c(b"123456789"), 0xE3069283)
            self.assertEqual(worker._crc32c(data), with_library)
            self.assertEqual(worker._crc32c(b""), 0)


def _zarr_source(export: Path, *, geometry=None, background=None, rois=None) -> ScanSource:
    records = {(r.key.spectral_cube_index, r.key.wavelength_nm): r for r in dio.load_ome_zarr_dataset(export).records}
    rois = rois if rois is not None else _rois()
    return ScanSource(
        cube_indices=lambda: CUBES,
        wavelengths_for_cube=lambda c: WAVELENGTHS,
        is_spectral=lambda c, w: True,
        record_path=lambda c, w: str(records[(c, w)].path),
        rois=lambda: rois,
        rois_at=lambda c: rois,
        has_timeline=lambda: False,
        chromatic_affine=lambda c, w: IDENTITY,
        chromatic_affine_between=lambda a, b: IDENTITY,
        resolve_mask=lambda c, w: None,
        geometry=lambda: geometry if geometry is not None else GeometrySettings(),
        background=lambda: background if background is not None else BackgroundSettings(),
        detection=lambda: AreaRoiDetectionSettings(),
        default_reference_diameters=lambda: (14.0, 20.0),
    )


def _same(a, b) -> bool:
    return (
        all(np.array_equal(a.values[m], b.values[m], equal_nan=True) for m in a.values)
        and all(np.array_equal(getattr(a, n), getattr(b, n), equal_nan=True) for n in ("std", "n_valid", "n_nominal", "n_masked"))
    )


class ScanChunkReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.made = _Exports()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.made.close()

    def _scan(self, export: str, *, subset=None, partial=True, geometry=None, background=None):
        plan = build_scan_plan(snapshot_source(_zarr_source(self.made.exports[export], geometry=geometry, background=background)))
        settings = replace(plan.settings, partial_reads=partial)
        jobs = tuple(job if subset is None else replace(job, roi_subset=subset) for job in plan.jobs)
        results = sorted(scan_cubes(jobs, settings, processes=1), key=lambda r: r.cube_pos)
        return results

    def test_a_full_layout_is_read_as_chunks_too_and_equals_the_whole_plane_read(self) -> None:
        self.assertEqual(PARTIAL_READ_MAX_SHARE, 1.0)  # maintainer, 2026-10-27: chunk reads whenever they apply
        partial = self._scan("cube_lz4")  # all 8 ROIs: ~58 % of the chunks of every plane
        whole = self._scan("cube_lz4", partial=False)
        self.assertEqual([r.partial_frames for r in partial], [len(WAVELENGTHS)] * len(CUBES))
        self.assertEqual([r.partial_frames for r in whole], [0, 0])
        for p, w in zip(partial, whole):
            self.assertTrue(_same(p, w))

    def test_a_lower_limit_sends_a_big_layout_back_to_whole_planes(self) -> None:
        with mock.patch("lspr_imaging_app.analysis.scan.PARTIAL_READ_MAX_SHARE", 0.3):
            results = self._scan("cube_lz4")
        self.assertEqual([r.partial_frames for r in results], [0, 0])

    def test_the_time_for_the_shard_index_is_recorded_once_per_shard(self) -> None:
        """The known bottleneck of scanning an OME-Zarr (dataset/io.py): its time must be visible, and paid once per shard, not per frame."""
        dio._shard_index_cache.clear()
        first = self._scan("cube_lz4", subset=(0,))
        self.assertTrue(all(r.index_seconds > 0.0 for r in first))  # one shard per cube, read and checked
        self.assertTrue(all(0.0 < r.crc_seconds <= r.index_seconds for r in first))
        again = self._scan("cube_lz4", subset=(0,))  # the two shards are still remembered
        self.assertEqual([r.index_seconds for r in again], [0.0, 0.0])
        before = dio.shard_index_seconds()
        dio._shard_index_cache.clear()
        self._scan("cube_lz4", subset=(0,))
        after = dio.shard_index_seconds()
        self.assertGreater(after[0], before[0])
        self.assertGreaterEqual(after[0], after[1])

    def test_one_roi_read_as_chunks_gives_the_cells_of_a_whole_plane_read(self) -> None:
        for export in ("image_lz4", "cube_lz4", "cube_raw"):
            for subset in ((0,), (5,), (2, 7)):
                with self.subTest(export=export, subset=subset):
                    partial = self._scan(export, subset=subset)
                    whole = self._scan(export, subset=subset, partial=False)
                    self.assertEqual([r.partial_frames for r in partial], [len(WAVELENGTHS)] * len(CUBES))
                    self.assertEqual([r.partial_frames for r in whole], [0, 0])
                    for p, w in zip(partial, whole):
                        self.assertTrue(_same(p, w))

    def test_the_subset_cells_equal_the_same_rois_of_a_full_scan(self) -> None:
        full = self._scan("cube_lz4", partial=False)
        part = self._scan("cube_lz4", subset=(3,))
        for p, f in zip(part, full):
            self.assertTrue(np.array_equal(p.values["mean"], f.values["mean"][[3]], equal_nan=True))
            self.assertTrue(np.array_equal(p.std, f.std[[3]], equal_nan=True))
            self.assertTrue(np.array_equal(p.n_valid, f.n_valid[[3]]))

    def test_a_rotated_export_with_a_valid_mask_reads_the_same_as_whole_planes(self) -> None:
        rois = [AreaRoi(1, 6.0, 6.0, 8.0, reference_inner_diameter_px=12.0, reference_outer_diameter_px=18.0)]  # near the NaN corner
        records_source = lambda: _zarr_source(self.made.exports["rotated"], rois=rois)  # noqa: E731
        plan = build_scan_plan(snapshot_source(records_source()))
        job = plan.jobs[0]
        outputs = [list(scan_cubes((replace(job, roi_subset=(0,)),), replace(plan.settings, partial_reads=flag), processes=1))[0] for flag in (True, False)]
        self.assertEqual((outputs[0].partial_frames, outputs[1].partial_frames), (len(WAVELENGTHS), 0))
        self.assertTrue(_same(*outputs))

    def test_chunk_reads_do_not_apply_with_the_background_on_or_a_rotation(self) -> None:
        on = BackgroundSettings(flatten_background_enabled=True, flatten_background_exclude_area_rois=False, flatten_background_sigma_px=10.0,
                                flatten_background_binning=2)
        self.assertEqual([r.partial_frames for r in self._scan("cube_lz4", subset=(0,), background=on)], [0, 0])
        rotated = GeometrySettings(rotation_angle_deg=1.0)
        self.assertEqual([r.partial_frames for r in self._scan("cube_lz4", subset=(0,), geometry=rotated)], [0, 0])
        self.assertTrue(spatial_transform_is_identity(GeometrySettings()))
        self.assertTrue(spatial_transform_is_identity(GeometrySettings(image_tools_enabled=False, rotation_angle_deg=5.0)))
        self.assertFalse(spatial_transform_is_identity(GeometrySettings(rotation_angle_deg=0.5)))
        self.assertFalse(spatial_transform_is_identity(GeometrySettings(flip_horizontal=True)))

    def test_a_reader_that_returns_nothing_falls_back_to_the_whole_plane_with_the_same_cells(self) -> None:
        reference = self._scan("cube_lz4", subset=(1,), partial=False)
        with mock.patch.object(dio, "read_plane_pixels", return_value=None):
            fallback = self._scan("cube_lz4", subset=(1,))
        self.assertEqual([r.partial_frames for r in fallback], [0, 0])
        for a, b in zip(fallback, reference):
            self.assertTrue(_same(a, b))

    def test_a_tiff_dataset_is_read_whole_as_before(self) -> None:
        records = {(r.key.spectral_cube_index, r.key.wavelength_nm): r for r in self.made.dataset.records}
        source = replace(_zarr_source(self.made.exports["cube_lz4"]), record_path=lambda c, w: str(records[(c, w)].path))
        plan = build_scan_plan(snapshot_source(source))
        results = list(scan_cubes(tuple(replace(j, roi_subset=(0,)) for j in plan.jobs), plan.settings, processes=1))
        self.assertEqual([r.partial_frames for r in results], [0, 0])

    def test_worker_processes_give_the_same_cells(self) -> None:
        plan = build_scan_plan(snapshot_source(_zarr_source(self.made.exports["cube_lz4"])))
        jobs = tuple(replace(job, roi_subset=(4,)) for job in plan.jobs)
        one = sorted(scan_cubes(jobs, plan.settings, processes=1), key=lambda r: r.cube_pos)
        two = sorted(scan_cubes(jobs, plan.settings, processes=2), key=lambda r: r.cube_pos)
        self.assertEqual([r.partial_frames for r in two], [len(WAVELENGTHS)] * len(CUBES))
        for a, b in zip(one, two):
            self.assertTrue(_same(a, b))


class ScanTaskChunkReadTests(unittest.TestCase):
    """A moved ROI on a real Zarr dataset through the task: the refresh reads chunks, says so, and the file equals a full scan."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.made = _Exports()
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.made.close()

    def test_moving_one_roi_refreshes_it_from_chunks_and_the_file_equals_a_full_scan(self) -> None:
        from lspr_imaging_app.analysis import scan_store

        rois = _rois()
        source = _zarr_source(self.made.exports["cube_lz4"], rois=rois)
        task = ScanTask()
        finished: list[tuple] = []
        completed: list = []
        task.task_finished.connect(lambda *args: finished.append(args))
        task.completed.connect(completed.append)
        self.addCleanup(task.shutdown)
        path = self.made.root / "task" / "analysis" / "scan.h5"

        def run(**kwargs) -> None:
            finished.clear()
            completed.clear()
            task.start(source, store_path=path, processes=1, **kwargs)
            deadline = time.monotonic() + 60
            while not finished and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.005)
            self.app.processEvents()
            self.assertEqual(finished[0][1], "completed")

        run()
        self.assertEqual(completed[0].partial_frames, len(CUBES) * len(WAVELENGTHS))  # every ROI: chunk reads too (limit 1.0)
        self.assertGreater(completed[0].index_seconds, 0.0)  # the known bottleneck is measured and reported
        rois[2].center_x += 1.0
        run()
        self.assertEqual((completed[0].rois_scanned, completed[0].partial_frames), (1, len(CUBES) * len(WAVELENGTHS)))
        plan = build_scan_plan(snapshot_source(source))
        expected = sorted(scan_cubes(plan.jobs, replace(plan.settings, partial_reads=False), processes=1), key=lambda r: r.cube_pos)
        stored = scan_store.read_scan(path)
        self.assertTrue(stored.computed().all())
        for pos, result in enumerate(expected):
            self.assertTrue(np.array_equal(stored.values["mean"][pos], result.values["mean"], equal_nan=True))
            self.assertTrue(np.array_equal(stored.std[pos], result.std, equal_nan=True))
            self.assertTrue(np.array_equal(stored.n_valid[pos], result.n_valid))


if __name__ == "__main__":
    unittest.main()
