"""The export worker's shortcuts (2026-10-22) must give the general route's numbers bit for bit, and the export's statistics
must read right. Pure logic and files: no Qt.

1. `apply_spatial_preprocessing_export_stored` (rotate + round onto the camera step in one go) equals
   `_to_stored(apply_spatial_preprocessing_export(...))`, the route it replaces, over angles, flips, crops and steps; and it
   declines (None) whatever it does not cover.
2. `on_value_step` (bit test) agrees with the plain modulo check.
3. The worker's fast TIFF decode reads the same pixels as tifffile, and a whole shard written with the shortcuts is
   byte-for-byte the shard the general route writes.
4. `ExportStats` wording and arithmetic.

Design / measurements: apps/LSPRi/eva/docs/export_speed_2026-10-22.md.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
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
    from lspr_imaging_app.dataset import io as dataset_io
    from lspr_imaging_app.dataset.export_stats import ExportStats, format_bytes, format_duration
    from lspr_imaging_app.image_tools.geometry import transform
    from lspr_imaging_app.image_tools.geometry.model import CropDefinition, GeometrySettings
    from lspr_imaging_app.io import _zarr_export_worker as worker
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _general_route(image: np.ndarray, settings: GeometrySettings, dtype, step: int) -> np.ndarray:
    return worker._to_stored(transform.apply_spatial_preprocessing_export(image, settings), np.dtype(dtype), 0.0, step)


def _settings(angle: float, flip_h=False, flip_v=False, crop=None) -> GeometrySettings:
    return GeometrySettings(
        image_tools_enabled=True, rotation_angle_deg=angle, flip_horizontal=flip_h, flip_vertical=flip_v,
        crop=crop if crop is not None else CropDefinition(),
    )


class FusedRotateAndRoundTests(unittest.TestCase):
    def setUp(self) -> None:
        rng = np.random.default_rng(7)
        self.noise = rng.integers(0, 65536, size=(120, 160), dtype=np.uint16)  # every value, the extremes included
        self.noise[0, :5] = (0, 65535, 0, 65535, 1)
        self.camera = (rng.integers(0, 4096, size=(120, 160)) * 16).astype(np.uint16)  # a 12-bit camera in 16-bit files

    def test_equals_the_general_route_over_angles_flips_crops_and_steps(self) -> None:
        crop = CropDefinition(x=6, y=4, width=130, height=95, enabled=True)
        cases = 0
        for angle in (1.4, -7.3, 33.0, 0.2, 90.0, -45.0):
            for flip_h, flip_v in ((False, False), (True, False), (False, True), (True, True)):
                for use_crop in (False, True):
                    settings = _settings(angle, flip_h, flip_v, crop if use_crop else None)
                    for image, step in ((self.camera, 16), (self.camera, 4), (self.noise, 1), (self.noise, 16)):
                        expected = _general_route(image, settings, np.uint16, step)
                        got = transform.apply_spatial_preprocessing_export_stored(image, settings, np.uint16, step)
                        self.assertIsNotNone(got)
                        self.assertEqual(got.dtype, np.uint16)
                        np.testing.assert_array_equal(got, expected, err_msg=f"angle {angle} flips {flip_h, flip_v} crop {use_crop} step {step}")
                        cases += 1
        self.assertEqual(cases, 6 * 4 * 2 * 4)

    def test_pixels_with_no_source_are_zero_and_values_stay_on_the_step(self) -> None:
        settings = _settings(10.0)
        got = transform.apply_spatial_preprocessing_export_stored(self.camera, settings, np.uint16, 16)
        self.assertTrue(np.all(got % 16 == 0))
        self.assertEqual(int(got[0, 0]), 0)  # a corner a 10 degree rotation creates
        self.assertGreater(int((got == 0).sum()), 100)

    def test_the_top_of_the_range_is_clipped_to_the_last_value_on_the_step(self) -> None:
        full = np.full((60, 80), 65520, dtype=np.uint16)  # Lanczos rings past the data at an edge; the stored value may not
        got = transform.apply_spatial_preprocessing_export_stored(full, _settings(3.0), np.uint16, 16)
        self.assertLessEqual(int(got.max()), 65520)

    def test_it_declines_what_it_does_not_cover(self) -> None:
        image, settings = self.camera, _settings(1.4)
        self.assertIsNone(transform.apply_spatial_preprocessing_export_stored(image.astype(np.float32), settings, np.uint16, 16))
        self.assertIsNone(transform.apply_spatial_preprocessing_export_stored(image, settings, np.int16, 16))  # signed target
        self.assertIsNone(transform.apply_spatial_preprocessing_export_stored(image, settings, np.float32, 1))
        self.assertIsNone(transform.apply_spatial_preprocessing_export_stored(image, settings, np.uint16, 12))  # not a power of two
        self.assertIsNone(transform.apply_spatial_preprocessing_export_stored(image, _settings(0.0), np.uint16, 16))  # no rotation
        self.assertIsNone(transform.apply_spatial_preprocessing_export_stored(image[:4, :4], settings, np.uint16, 16))  # tiny
        self.assertIsNone(transform.apply_spatial_preprocessing_export_stored(np.stack([image] * 3, axis=-1), settings, np.uint16, 16))
        off = GeometrySettings(image_tools_enabled=False, rotation_angle_deg=1.4)
        self.assertIsNone(transform.apply_spatial_preprocessing_export_stored(image, off, np.uint16, 16))

    def test_the_plan_is_made_once_per_shape_and_geometry_and_never_changed(self) -> None:
        transform._EXPORT_PLAN_CACHE.clear()
        settings = _settings(1.4)
        transform.apply_spatial_preprocessing_export_stored(self.camera, settings, np.uint16, 16)
        (_matrix, _shape, mask), = transform._EXPORT_PLAN_CACHE.values()
        snapshot = mask.copy()
        transform.apply_spatial_preprocessing_export_stored(self.noise, settings, np.uint16, 1)  # same shape, same geometry
        self.assertEqual(len(transform._EXPORT_PLAN_CACHE), 1)
        np.testing.assert_array_equal(mask, snapshot)
        transform.apply_spatial_preprocessing_export_stored(self.camera, _settings(2.0), np.uint16, 16)  # another angle
        self.assertEqual(len(transform._EXPORT_PLAN_CACHE), 2)
        for angle in (3.0, 4.0, 5.0, 6.0):  # bounded
            transform.apply_spatial_preprocessing_export_stored(self.camera, _settings(angle), np.uint16, 16)
        self.assertLessEqual(len(transform._EXPORT_PLAN_CACHE), transform._EXPORT_PLAN_CACHE_MAX)


class OnValueStepTests(unittest.TestCase):
    def test_agrees_with_the_modulo_check(self) -> None:
        rng = np.random.default_rng(3)
        on_16 = (rng.integers(0, 4096, size=(50, 60)) * 16).astype(np.uint16)
        off_16 = on_16.copy()
        off_16[17, 23] += 1  # one pixel off the step
        signed = (rng.integers(-2048, 2048, size=(50, 60)) * 16).astype(np.int16)  # negative values too
        for values in (on_16, off_16, signed, np.zeros((5, 5), dtype=np.uint16)):
            for step in (1, 2, 4, 16, 3, 12):
                self.assertEqual(worker.on_value_step(values, step), bool(np.all(values % step == 0)), msg=f"{values.dtype} step {step}")

    def test_float_planes_take_the_plain_check(self) -> None:
        self.assertTrue(worker.on_value_step(np.array([16.0, 32.0, 0.0]), 16))
        self.assertFalse(worker.on_value_step(np.array([16.0, 33.0]), 16))


class FastDecodeAndShardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        rng = np.random.default_rng(11)
        yy, xx = np.mgrid[0:90, 0:130]
        smooth = 2000 + 8 * xx + 4 * yy + 900 * np.exp(-((xx - 60) ** 2 + (yy - 40) ** 2) / 90.0)
        self.image = ((np.round((smooth + rng.normal(0, 20, smooth.shape)) / 16)) * 16).astype(np.uint16)
        self.path = self.root / "plane.tif"
        tifffile.imwrite(str(self.path), self.image, compression="lzw", rowsperstrip=3)  # like the maintainer's files
        # each test starts as a fresh process would: nothing verified yet, the decoder not switched off
        self._saved = (dataset_io._fast_tiff_verified, dataset_io._fast_tiff_disabled)
        dataset_io._fast_tiff_verified, dataset_io._fast_tiff_disabled = 0, False
        self.addCleanup(lambda: setattr(dataset_io, "_fast_tiff_verified", self._saved[0]))
        self.addCleanup(lambda: setattr(dataset_io, "_fast_tiff_disabled", self._saved[1]))

    def test_the_fast_decode_reads_the_same_pixels_as_tifffile(self) -> None:
        got = worker._load_image(str(self.path))
        np.testing.assert_array_equal(got, tifffile.imread(str(self.path)))
        self.assertEqual(got.dtype, np.uint16)
        self.assertTrue(dataset_io.fast_tiff_decoder_active())  # it really went through the opt-in path

    def test_it_falls_back_to_tifffile_when_switched_off(self) -> None:
        dataset_io._fast_tiff_disabled = True
        with mock.patch.object(dataset_io, "_read_tiff_fast", side_effect=AssertionError("must not be called")):
            got = worker._load_image(str(self.path))
        np.testing.assert_array_equal(got, self.image)

    def test_a_multi_page_file_still_reads_as_tifffile_reads_it(self) -> None:
        stack = self.root / "stack.tif"
        tifffile.imwrite(str(stack), np.stack([self.image, self.image + 16]), compression="lzw")
        np.testing.assert_array_equal(worker._load_image(str(stack)), tifffile.imread(str(stack)))

    def _spec(self, name: str, geometry: GeometrySettings):
        height, width, ich = 80, 120, 40
        return worker.ShardWriteSpec(
            record_paths=[str(self.path)], shard_path=str(self.root / name), inner_chunk_h=ich, inner_chunk_w=ich,
            image_height=height, image_width=width, shard_h=-(-height // ich) * ich, shard_w=-(-width // ich) * ich,
            n_cy=-(-height // ich), n_cx=-(-width // ich), n_inner=(-(-height // ich)) * (-(-width // ich)), dtype_str="<u2",
            fill_value=0.0, compression_cname="lz4", apply_image_tools=True, preprocessing=geometry, value_step=16,
            frame_x0=-3, frame_y0=-2,
        )

    def test_a_shard_written_with_the_shortcuts_is_byte_for_byte_the_general_routes_shard(self) -> None:
        geometry = _settings(1.4, crop=CropDefinition(x=4, y=3, width=112, height=78, enabled=True))
        fast = worker.write_shard(self._spec("fast", geometry))
        self.assertEqual(fast.off_step_planes, 0)
        # the general route: no fused shortcut, and tifffile for the decode
        dataset_io._fast_tiff_disabled = True
        with mock.patch.object(transform, "apply_spatial_preprocessing_export_stored", return_value=None):
            general = worker.write_shard(self._spec("general", geometry))
        self.assertEqual(general.off_step_planes, 0)
        self.assertEqual((self.root / "fast").read_bytes(), (self.root / "general").read_bytes())
        self.assertEqual(fast.bytes_written, general.bytes_written)

    def test_a_plane_off_the_step_is_still_counted_and_rounded_to_whole_counts(self) -> None:
        odd = self.image.copy()
        odd[10, 10] += 1
        tifffile.imwrite(str(self.path), odd, compression="lzw", rowsperstrip=3)
        result = worker.write_shard(self._spec("off", _settings(1.4)))
        self.assertEqual(result.off_step_planes, 1)


class ExportStatsTests(unittest.TestCase):
    def test_durations(self) -> None:
        self.assertEqual(format_duration(0.4), "0 s")
        self.assertEqual(format_duration(48.2), "48 s")
        self.assertEqual(format_duration(292), "4 min 52 s")
        self.assertEqual(format_duration(60), "1 min 00 s")
        self.assertEqual(format_duration(3900), "1 h 05 min")

    def test_bytes(self) -> None:
        self.assertEqual(format_bytes(812e6), "812 MB")
        self.assertEqual(format_bytes(11.24e9), "11.2 GB")

    def test_the_arithmetic_and_the_wording(self) -> None:
        stats = ExportStats(planes=8478, bytes_written=int(11.2e9), stored_plane_bytes=1297 * 806 * 2, workers=8, seconds=292.0)
        self.assertAlmostEqual(stats.planes_per_second, 8478 / 292.0)
        self.assertEqual(stats.uncompressed_bytes, 8478 * 1297 * 806 * 2)
        self.assertAlmostEqual(stats.gb_per_minute, 8478 * 1297 * 806 * 2 / 1e9 / (292.0 / 60.0))
        self.assertAlmostEqual(stats.size_ratio, 11.2e9 / (8478 * 1297 * 806 * 2))
        text = stats.describe()
        self.assertIn("Export took 4 min 52 s", text)
        self.assertIn("8,478 images at 29 images/s", text)
        self.assertIn("GB/min of image data", text)
        self.assertIn("Wrote 11.2 GB to disk", text)
        self.assertIn("% of the 17.7 GB uncompressed", text)
        self.assertEqual(stats.headline(), "4 min 52 s, 29 images/s")

    def test_a_slow_rate_keeps_a_decimal(self) -> None:
        stats = ExportStats(planes=40, bytes_written=1_000_000, stored_plane_bytes=2_000_000, seconds=10.0)
        self.assertIn("4.0 images/s", stats.describe())
        self.assertEqual(stats.headline(), "10 s, 4.0 images/s")

    def test_nothing_written_does_not_divide_by_zero(self) -> None:
        stats = ExportStats(seconds=0.0)
        self.assertEqual((stats.planes_per_second, stats.gb_per_minute, stats.size_ratio), (0.0, 0.0, 0.0))
        self.assertEqual(stats.describe(), "Export took 0 s.")
        self.assertEqual(stats.headline(), "0 s")


if __name__ == "__main__":
    unittest.main()
