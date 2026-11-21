"""What an export takes on disk, counting the drive's allocation unit (`dataset/export_disk_estimate.py`, 2026-11-20), and the
export's per-stage worker times (`export_stats.py`).

The reference case is the real one that took 5 h: 71,685 images (2,655 cubes x 27 wavelengths), 1279 x 879 uint16, 80 x 80 chunks, on a
USB exFAT drive with 2 MiB allocation units (hand-measured: 8 MiB per image as one file per image = 560 GiB, ~171 GiB as one file per cube).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.dataset import export_disk_estimate as de  # noqa: E402
from lspr_imaging_app.export_stats import ExportStats  # noqa: E402

GIB = 2**30
EXFAT_2MIB = de.DriveInfo(cluster_bytes=2 * 2**20, folder_bytes=2 * 2**20, free_bytes=374 * GIB, file_system="exFAT")
NTFS_4K = de.DriveInfo(cluster_bytes=4096, folder_bytes=1024, free_bytes=374 * GIB, file_system="NTFS")
LED_V2 = dict(n_cubes=2655, n_wavelengths=27, height=879, width=1279, itemsize=2, chunk_h=80, chunk_w=80)


class EstimateTests(unittest.TestCase):
    def test_one_file_per_image_on_a_two_mib_drive_costs_about_560_gib_and_does_not_fit(self) -> None:
        estimate = de.estimate_export_disk_use(shard_mode=de.PER_IMAGE, compressed=False, drive=EXFAT_2MIB, **LED_V2)
        self.assertEqual(estimate.files, 71_685)
        self.assertEqual(estimate.folders, 3 + 2655 + 2 * 71_685)  # root, images, c, one per cube, two per image
        self.assertAlmostEqual(estimate.on_disk_bytes / GIB, 565.0, delta=6.0)  # 8 MiB per image
        self.assertFalse(de.fits(estimate, EXFAT_2MIB))
        self.assertTrue(de.layout_is_slow(estimate))

    def test_one_file_per_cube_on_the_same_drive_fits_and_is_not_slow(self) -> None:
        estimate = de.estimate_export_disk_use(shard_mode=de.PER_CUBE, compressed=False, drive=EXFAT_2MIB, **LED_V2)
        self.assertEqual(estimate.files, 2655)
        self.assertEqual(estimate.folders, 3 + 3 * 2655)
        self.assertAlmostEqual(estimate.on_disk_bytes / GIB, 171.0, delta=3.0)
        self.assertTrue(de.fits(estimate, EXFAT_2MIB))
        self.assertFalse(de.layout_is_slow(estimate))

    def test_lz4_estimate_is_the_expected_fraction_of_the_raw_data(self) -> None:
        raw = de.estimate_export_disk_use(shard_mode=de.PER_CUBE, compressed=False, drive=NTFS_4K, **LED_V2)
        packed = de.estimate_export_disk_use(shard_mode=de.PER_CUBE, compressed=True, drive=NTFS_4K, **LED_V2)
        self.assertAlmostEqual(packed.data_bytes / raw.data_bytes, de.COMPRESSED_FRACTION, delta=0.01)
        self.assertLess(packed.on_disk_bytes, raw.on_disk_bytes)

    def test_an_ntfs_drive_has_almost_no_overhead_in_either_layout(self) -> None:
        for mode in (de.PER_IMAGE, de.PER_CUBE):
            estimate = de.estimate_export_disk_use(shard_mode=mode, compressed=False, drive=NTFS_4K, **LED_V2)
            self.assertLess(estimate.on_disk_bytes / estimate.data_bytes, 1.02, mode)
            self.assertFalse(de.layout_is_slow(estimate), mode)

    def test_a_tiny_file_still_takes_a_whole_allocation_unit(self) -> None:
        estimate = de.estimate_export_disk_use(
            n_cubes=1, n_wavelengths=1, height=1, width=1, itemsize=2, chunk_h=1, chunk_w=1, shard_mode=de.PER_CUBE, compressed=False, drive=EXFAT_2MIB
        )
        self.assertEqual(estimate.files, 1)
        self.assertLess(estimate.data_bytes, 100)
        self.assertEqual(estimate.on_disk_bytes, 2 * 2**20 + estimate.folders * 2 * 2**20)  # one unit for the file, one per folder

    def test_every_plane_counts_even_with_fewer_images_than_cubes_times_wavelengths(self) -> None:
        # The writer makes a shard for every cube x wavelength (missing / skipped records get an empty one), so the count is the product.
        estimate = de.estimate_export_disk_use(shard_mode=de.PER_IMAGE, compressed=False, drive=NTFS_4K, **{**LED_V2, "n_cubes": 10})
        self.assertEqual(estimate.files, 270)

    def test_fits_keeps_a_margin(self) -> None:
        estimate = de.DiskEstimate(files=1, folders=1, data_bytes=1, on_disk_bytes=100, folder_bytes_total=0)
        self.assertTrue(de.fits(estimate, de.DriveInfo(4096, 1024, 103, "NTFS")))   # 100 <= 0.98 * 103 = 100.94
        self.assertFalse(de.fits(estimate, de.DriveInfo(4096, 1024, 101, "NTFS")))  # 100 > 0.98 * 101 = 98.98


class DriveInfoTests(unittest.TestCase):
    def test_a_real_folder_gives_sensible_numbers_and_a_missing_one_falls_back_to_its_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            here = de.drive_info(Path(tmp))
            there = de.drive_info(Path(tmp) / "not" / "made" / "yet")
        for info in (here, there):
            self.assertIsNotNone(info)
            self.assertGreaterEqual(info.cluster_bytes, 512)
            self.assertGreaterEqual(info.folder_bytes, 512)
            self.assertGreater(info.free_bytes, 0)
        self.assertEqual(here.cluster_bytes, there.cluster_bytes)
        if here.file_system.upper() == "NTFS":
            self.assertEqual(here.folder_bytes, 1024)
        else:
            self.assertEqual(here.folder_bytes, here.cluster_bytes)


class StageTimeTests(unittest.TestCase):
    def test_nothing_known_says_nothing_and_describe_is_unchanged(self) -> None:
        stats = ExportStats(planes=10, seconds=5.0)
        self.assertEqual(stats.stage_ms_per_image(), {})
        self.assertEqual(stats.stage_line(), "")
        self.assertNotIn("Worker time", stats.describe())

    def test_stage_seconds_become_milliseconds_per_image(self) -> None:
        stats = ExportStats(planes=100, seconds=10.0)
        stats.add_stage_seconds(35.0, 0.2, 150.0, 5.0)
        stats.add_stage_seconds(0.2, 0.0, 1.6, 0.0)  # results arrive one by one
        stages = stats.stage_ms_per_image()
        self.assertAlmostEqual(stages["read_ms"], 352.0)
        self.assertAlmostEqual(stages["encode_ms"], 2.0)
        self.assertAlmostEqual(stages["folders_ms"], 1516.0)
        self.assertAlmostEqual(stages["write_ms"], 50.0)
        line = stats.stage_line()
        self.assertIn("making folders 1,516 ms", line)
        self.assertIn("reading + rotating 352 ms", line)
        self.assertTrue(stats.describe().endswith(line))

    def test_a_partial_export_still_has_its_numbers(self) -> None:
        stats = ExportStats()  # cancelled before the end: `seconds` was never set, planes were counted as they arrived
        stats.planes = 4
        stats.add_stage_seconds(1.0, 0.0, 6.0, 0.2)
        self.assertEqual(round(stats.stage_ms_per_image()["folders_ms"]), 1500)


if __name__ == "__main__":
    unittest.main()
