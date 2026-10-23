"""OME-Zarr export from the rewrite (`dataset/io.py`).

1. Plain export: pixels are exact uint16, metadata has typed axes (never a time axis; a per-plane time array instead), one omero
   channel per wavelength, and the pixel size when a calibration is passed.
   Editing the metadata afterwards rewrites only the root `zarr.json`.
2. Rotated export (Export tab "Apply rotation/crop", 2026-10-20): stored uint16 like the source (rounded Lanczos4
   values), the pixels the rotation created stored as 0 and listed in the `valid_mask` array; reading turns them
   into NaN (full planes and ROI patches). Flip/crop alone keep the source values exactly.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    import zarr

    from lspr_imaging_app.dataset.io import (
        dataset_load_plane_roi,
        export_ome_zarr_dataset,
        load_image_array,
        load_ome_zarr_dataset,
    )
    from lspr_core.imaging_models import ImagingAcquisitionMetadata, ImagingCubeTiming
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools.geometry.model import CropDefinition, GeometrySettings
    from lspr_imaging_app.image_tools.geometry.transform import apply_spatial_preprocessing
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

WAVELENGTHS_NM = [500.0, 600.0]


class OmeZarrExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        self.known: dict[float, np.ndarray] = {}
        records = []
        for wl in WAVELENGTHS_NM:
            rng = np.random.default_rng(int(wl))
            image = rng.integers(1, 4096, size=(120, 160), dtype=np.uint16)  # no zeros
            path = self.root / "src" / f"wl{wl:.0f}.tif"
            tifffile.imwrite(str(path), image)
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wl, spectral_cube_index=0), path=path))
            self.known[wl] = image
        self.dataset = ImageDataset(folder=self.root / "src", records=records)

    def _export(self, name: str, settings: GeometrySettings | None) -> Path:
        return export_ome_zarr_dataset(
            self.dataset, self.root / name, chunk_size_px=32, compression_enabled=True,
            shard_mode="per_image", preprocessing=settings, adaptive_workers_enabled=False,
        )

    def _export_plain(self, name: str, pixel_size_um) -> Path:
        return export_ome_zarr_dataset(
            self.dataset, self.root / name, chunk_size_px=32, compression_enabled=True,
            shard_mode="per_image", adaptive_workers_enabled=False, pixel_size_um=pixel_size_um,
        )

    # -- plain export: the form the maintainer chose ------------------------------

    def test_plain_export_has_exact_pixels_and_the_standard_metadata(self) -> None:
        destination = self._export_plain("plain", pixel_size_um=(0.5, 0.25))
        for record in load_ome_zarr_dataset(destination).records:
            read_back = load_image_array(str(record.path))
            np.testing.assert_array_equal(read_back.astype(np.uint16), self.known[float(record.key.wavelength_nm)])
        attributes = json.loads((destination / "zarr.json").read_text())["attributes"]
        ome = attributes["ome"]
        axes = ome["multiscales"][0]["axes"]
        self.assertEqual(axes[0], {"name": "cube_index"})
        self.assertEqual([a["type"] for a in axes[1:]], ["channel", "space", "space"])
        self.assertEqual([a.get("unit") for a in axes[2:]], ["micrometer", "micrometer"])
        self.assertEqual(
            ome["multiscales"][0]["datasets"][0]["coordinateTransformations"][0]["scale"], [1.0, 1.0, 0.25, 0.5]
        )
        channels = ome["omero"]["channels"]
        self.assertEqual([c["label"] for c in channels], ["500 nm", "600 nm"])
        for channel in channels:
            window = channel["window"]
            self.assertLessEqual(window["min"], window["start"])
            self.assertLess(window["start"], window["end"])
            self.assertLessEqual(window["end"], window["max"])
        self.assertEqual(attributes["lspr"]["pixel_size_um"], {"x": 0.5, "y": 0.25})
        self.assertNotIn("rotation_fill_dark", json.dumps(attributes["lspr"]))

    def test_without_calibration_there_is_no_unit_and_unit_scale(self) -> None:
        destination = self._export_plain("uncal", pixel_size_um=None)
        ome = json.loads((destination / "zarr.json").read_text())["attributes"]["ome"]
        self.assertTrue(all("unit" not in a for a in ome["multiscales"][0]["axes"]))
        self.assertEqual(
            ome["multiscales"][0]["datasets"][0]["coordinateTransformations"][0]["scale"], [1.0] * 4
        )

    def test_editing_metadata_afterwards_touches_no_pixel_shard(self) -> None:
        import zarr

        destination = self._export_plain("edit", pixel_size_um=None)

        def shard_hashes() -> dict[str, str]:
            return {
                str(p.relative_to(destination)): hashlib.md5(p.read_bytes()).hexdigest()
                for p in sorted((destination / "0" / "c").rglob("*")) if p.is_file()
            }

        before = shard_hashes()
        self.assertTrue(before)
        group = zarr.open_group(str(destination), mode="r+")
        group.attrs["lspr_processing"] = {"rotation_angle_deg": 12.5}
        self.assertEqual(before, shard_hashes())
        self.assertEqual(
            zarr.open_group(str(destination), mode="r").attrs["lspr_processing"]["rotation_angle_deg"], 12.5
        )

    # -- time axis only when times can be assigned to the cubes ---------------------

    def _timed_dataset(self, cube_start_ms: list[int]) -> ImageDataset:
        records, timings = [], []
        for cube, start_ms in enumerate(cube_start_ms):
            for offset, wl in enumerate(WAVELENGTHS_NM):
                path = self.root / "src" / f"c{cube}_wl{wl:.0f}.tif"
                tifffile.imwrite(str(path), self.known[wl])
                records.append(ImageRecord(key=ImageKey(wavelength_nm=wl, spectral_cube_index=cube), path=path))
                timings.append(ImagingCubeTiming(spectral_cube_index=cube, wavelength_nm=wl, acquired_at_unix_ms=start_ms + 100 * offset))
        metadata = ImagingAcquisitionMetadata(source_format="test", image_timings=timings)
        return ImageDataset(folder=self.root / "src", records=records, acquisition_metadata=metadata)

    def _export_dataset(self, dataset: ImageDataset, name: str) -> dict:
        destination = export_ome_zarr_dataset(
            dataset, self.root / name, chunk_size_px=32, compression_enabled=True,
            shard_mode="per_image", adaptive_workers_enabled=False,
        )
        return json.loads((destination / "zarr.json").read_text())["attributes"]

    def test_timed_dataset_gets_a_per_plane_time_array_and_still_no_time_axis(self) -> None:
        import zarr

        # Not evenly spaced, and planes inside a cube are 100 ms apart: one time per plane.
        dataset = self._timed_dataset([10_000, 12_000, 14_000, 40_000])
        destination = export_ome_zarr_dataset(
            dataset, self.root / "timed", chunk_size_px=32, compression_enabled=True,
            shard_mode="per_image", adaptive_workers_enabled=False,
        )
        attributes = json.loads((destination / "zarr.json").read_text())["attributes"]
        multiscale = attributes["ome"]["multiscales"][0]
        self.assertEqual(multiscale["axes"][0], {"name": "cube_index"})
        self.assertFalse(any(a.get("type") == "time" for a in multiscale["axes"]))
        self.assertEqual(multiscale["datasets"][0]["coordinateTransformations"][0]["scale"][:2], [1.0, 1.0])
        info = attributes["lspr"]["plane_times"]
        self.assertEqual((info["array"], info["unit"], info["origin_unix_ms"]), ("plane_times_s", "second", 10_000))
        times = np.asarray(zarr.open_group(str(destination), mode="r")["plane_times_s"][:])
        np.testing.assert_allclose(times, [[0.0, 0.1], [2.0, 2.1], [4.0, 4.1], [30.0, 30.1]])
        # The extra array must not confuse our own reader.
        self.assertEqual(len(load_ome_zarr_dataset(destination).records), 4 * len(WAVELENGTHS_NM))

    def test_per_image_times_survive_export_and_reload(self) -> None:
        # Regression: the export saved the live metadata, whose image_timings the app has already moved into the compact
        # form, so an exported dataset reloaded with no experiment times (the app then fell back to frame numbers).
        from lspr_imaging_app.dataset.model import compact_dataset_image_timings

        dataset = self._timed_dataset([10_000, 12_000, 14_000, 40_000])
        compact_dataset_image_timings(dataset)  # what loading the dataset in the app does
        destination = export_ome_zarr_dataset(
            dataset, self.root / "timed_roundtrip", chunk_size_px=32, compression_enabled=True,
            shard_mode="per_image", adaptive_workers_enabled=False,
        )
        reloaded = compact_dataset_image_timings(load_ome_zarr_dataset(destination))
        original = compact_dataset_image_timings(dataset)
        self.assertEqual(len(reloaded), 4 * len(WAVELENGTHS_NM))
        self.assertEqual(reloaded.per_frame_ms, original.per_frame_ms)
        # The sidecar is what the app reads first on the next open, so it needs the times too.
        from lspr_imaging_app.dataset.io import acquisition_metadata_sidecar_path
        from lspr_imaging_app.storage.workspace import load_acquisition_metadata_sidecar

        sidecar = load_acquisition_metadata_sidecar(acquisition_metadata_sidecar_path(destination))
        self.assertEqual(len(sidecar.image_timings), 4 * len(WAVELENGTHS_NM))

    def test_untimed_dataset_has_no_time_array(self) -> None:
        import zarr

        destination = self._export_plain("untimed", pixel_size_um=None)
        self.assertNotIn("plane_times_s", list(zarr.open_group(str(destination), mode="r").keys()))
        self.assertNotIn("plane_times", json.loads((destination / "zarr.json").read_text())["attributes"]["lspr"])

    # -- rotation/crop baked into the pixels (Export tab "Apply rotation/crop", 2026-10-20) ---------------

    def test_rotated_export_is_uint16_with_a_valid_mask_and_loads_nan_corners(self) -> None:
        settings = GeometrySettings(rotation_angle_deg=20.0, crop=CropDefinition(x=4, y=3, width=150, height=140, enabled=True))
        destination = self._export("rot", settings)
        group = zarr.open_group(str(destination), mode="r")
        self.assertEqual(group["0"].dtype, np.uint16)  # not float32: same size as the raw data
        lspr = json.loads((destination / "zarr.json").read_text())["attributes"]["lspr"]
        self.assertEqual(lspr["valid_mask"]["array"], "valid_mask")
        self.assertEqual(lspr["image_tools"]["interpolation"], "lanczos4")
        mask = np.asarray(group["valid_mask"][:])
        records = load_ome_zarr_dataset(destination).records
        self.assertEqual(len(records), len(WAVELENGTHS_NM))
        for record in records:
            read_back = load_image_array(str(record.path))
            expected = apply_spatial_preprocessing(self.known[float(record.key.wavelength_nm)], settings)
            self.assertEqual(read_back.dtype, np.float32)
            self.assertEqual(read_back.shape, expected.shape)
            np.testing.assert_array_equal(np.isnan(read_back), np.isnan(expected))  # the same pixels as the app's own
            np.testing.assert_array_equal(mask == 0, np.isnan(expected))
            self.assertTrue(np.isnan(read_back).any())
            finite = ~np.isnan(expected)
            # Stored rounded and clipped to uint16: within half a count of the app's own (Lanczos4) rotation.
            self.assertLessEqual(float(np.max(np.abs(read_back[finite] - np.clip(expected[finite], 0, 65535)))), 0.5)

    def test_rotated_12_bit_data_is_rounded_onto_the_camera_step(self) -> None:
        # A 12-bit camera writing 16-bit files: every value a multiple of 16. The second wavelength breaks the step
        # in one pixel, so that plane must be rounded to whole counts instead, and reported.
        for wl in WAVELENGTHS_NM:
            image = (self.known[wl] // 16 * 16 + 16).astype(np.uint16)
            if wl == WAVELENGTHS_NM[1]:
                image[0, 0] += 1
            tifffile.imwrite(str(self.root / "src" / f"wl{wl:.0f}.tif"), image)
            self.known[wl] = image
        settings = GeometrySettings(rotation_angle_deg=20.0)
        destination = self._export("rot12", settings)
        tools = json.loads((destination / "zarr.json").read_text())["attributes"]["lspr"]["image_tools"]
        self.assertEqual((tools["value_step"], tools["planes_not_on_value_step"]), (16, 1))
        for record in load_ome_zarr_dataset(destination).records:
            wl = float(record.key.wavelength_nm)
            read_back = load_image_array(str(record.path))
            expected = np.clip(apply_spatial_preprocessing(self.known[wl], settings), 0, 65535)
            finite = ~np.isnan(read_back)
            if wl == WAVELENGTHS_NM[0]:
                self.assertTrue(np.all(read_back[finite] % 16 == 0))
                self.assertLessEqual(float(np.max(np.abs(read_back[finite] - expected[finite]))), 8.0)
            else:  # off the step: whole counts
                self.assertLessEqual(float(np.max(np.abs(read_back[finite] - expected[finite]))), 0.5)

    def test_roi_patch_reads_of_a_rotated_export_have_nan_corners_too(self) -> None:
        settings = GeometrySettings(rotation_angle_deg=20.0)
        destination = self._export("rot_patch", settings)
        dataset = load_ome_zarr_dataset(destination)
        full = load_image_array(str(dataset.records[0].path))
        key = dataset.records[0].key
        patch = dataset_load_plane_roi(dataset, key.spectral_cube_index, key.wavelength_nm, 0, 30, 0, 40)
        np.testing.assert_array_equal(np.isnan(patch), np.isnan(full[0:30, 0:40]))
        self.assertTrue(np.isnan(patch).any())

    def test_re_export_without_rotation_keeps_the_mask_and_rotating_again_is_refused(self) -> None:
        rotated = load_ome_zarr_dataset(self._export("rot_src", GeometrySettings(rotation_angle_deg=20.0)))
        again = export_ome_zarr_dataset(rotated, self.root / "re", chunk_size_px=32, adaptive_workers_enabled=False)
        original = load_image_array(str(rotated.records[0].path))
        copied = load_image_array(str(load_ome_zarr_dataset(again).records[0].path))
        np.testing.assert_array_equal(np.isnan(copied), np.isnan(original))
        with self.assertRaisesRegex(ValueError, "already a rotated export"):
            export_ome_zarr_dataset(rotated, self.root / "re2", chunk_size_px=32, adaptive_workers_enabled=False,
                                    preprocessing=GeometrySettings(rotation_angle_deg=5.0))

    def test_without_rotation_values_stay_uint16_and_exact(self) -> None:
        settings = GeometrySettings(crop=CropDefinition(x=10, y=5, width=100, height=80, enabled=True))
        destination = self._export("crop", settings)
        for record in load_ome_zarr_dataset(destination).records:
            read_back = load_image_array(str(record.path))
            expected = self.known[float(record.key.wavelength_nm)][5:85, 10:110]
            np.testing.assert_array_equal(read_back.astype(np.uint16), expected)
            self.assertFalse(np.isnan(read_back).any())


if __name__ == "__main__":
    unittest.main()
