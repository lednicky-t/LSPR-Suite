"""OME-Zarr export with rectangular chunks and an export frame (lossless padding / lossy trim), 2026-10-20.

The frame moves the chunk grid onto the ROI layout (`dataset/chunk_plan.py`): stored pixel (0, 0) is source pixel
(x0, y0). Checked here: the stored pixels (source where it exists, fill elsewhere), both readers (zarr and the app's
fast shard reader, which must understand rectangular chunks), the NGFF translation (in um when calibrated), the
`lspr` attributes, the export summary and the folder name.

Rewrite-only (`dataset/io.py`); skipped when the `apps/LSPRi/eva` submodule is not on its `rewrite` branch.
"""

from __future__ import annotations

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
    import lspr_imaging_app.dataset.io as dio
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

H, W = 40, 56
CHUNK = (16, 12)  # (height, width): rectangular, 192 px (a multiple of 8)
WAVELENGTHS_NM = [450.0, 500.0]


def find_translation(node):
    """The first NGFF ``translation`` list anywhere in the attributes."""
    if isinstance(node, dict):
        if node.get("type") == "translation":
            return node["translation"]
        for value in node.values():
            found = find_translation(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find_translation(value)
            if found is not None:
                return found
    return None


class ExportFrameTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        source = self.root / "source"
        source.mkdir()
        records = []
        self.known = []
        for wavelength in WAVELENGTHS_NM:
            image = np.random.default_rng(int(wavelength)).integers(1, 4096, size=(H, W), dtype=np.uint16)  # >= 1: 0 = fill
            path = source / f"cube0_wl{wavelength:.0f}.tif"
            tifffile.imwrite(str(path), image)
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wavelength, spectral_cube_index=0), path=path))
            self.known.append(image)
        self.dataset = ImageDataset(folder=source, records=records)

    def export(self, name: str, frame, **kwargs) -> Path:
        return dio.export_ome_zarr_dataset(self.dataset, self.root / f"{name}.ome.zarr", chunk_shape=CHUNK, frame=frame,
                                           compression_enabled=True, **kwargs)

    def stored_planes(self, destination: Path) -> list[np.ndarray]:
        import zarr

        array = zarr.open_array(destination / dio.OME_ZARR_ARRAY_DIRNAME, mode="r")
        self.assertEqual(tuple(array.chunks[2:]), CHUNK)
        planes = [np.asarray(array[0, wi]) for wi in range(len(WAVELENGTHS_NM))]
        dio._ome_zarr_fast_read_metadata.cache_clear()
        meta = dio._ome_zarr_fast_read_metadata(str(destination))
        self.assertIsNotNone(meta, "the fast reader must accept rectangular chunks")
        for wi, plane in enumerate(planes):
            fast = dio._fast_read_zarr_plane(meta, 0, wi)
            self.assertIsNotNone(fast)
            np.testing.assert_array_equal(fast.astype(plane.dtype), plane)
        return planes

    def lspr_attrs(self, destination: Path) -> dict:
        import zarr

        return dict(zarr.open_group(str(destination), mode="r").attrs[dio.OME_ZARR_LSPR_KEY])

    def test_lossless_padding_keeps_every_pixel(self) -> None:
        destination = self.export("lossless", (-5, -3, W + 5, H + 3))
        for plane, image in zip(self.stored_planes(destination), self.known):
            self.assertEqual(plane.shape, (H + 3, W + 5))
            np.testing.assert_array_equal(plane[3:, 5:], image)
            self.assertFalse(plane[:3].any() or plane[:, :5].any())  # padding is fill (0)
        attrs = self.lspr_attrs(destination)
        self.assertEqual(attrs["chunk_shape_px"], [16, 12])
        self.assertEqual(attrs["chunk_size_px"], 0)  # rectangular: square-only readers fall back to zarr
        frame = attrs["export_frame"]
        self.assertTrue(frame["lossless"])
        self.assertEqual(frame["valid_region"], {"x": 5, "y": 3, "width": W, "height": H})
        import zarr

        self.assertEqual(find_translation(dict(zarr.open_group(str(destination), mode="r").attrs)), [0.0, 0.0, -3.0, -5.0])

    def test_lossy_trim_keeps_only_the_window(self) -> None:
        destination = self.export("lossy", (8, 4, 24, 32))
        for plane, image in zip(self.stored_planes(destination), self.known):
            np.testing.assert_array_equal(plane, image[4:36, 8:32])
        frame = self.lspr_attrs(destination)["export_frame"]
        self.assertFalse(frame["lossless"])
        self.assertEqual(frame["valid_region"], {"x": 0, "y": 0, "width": 24, "height": 32})

    def test_translation_is_in_micrometres_when_calibrated(self) -> None:
        import zarr

        destination = self.export("calibrated", (-5, -3, W + 5, H + 3), pixel_size_um=(2.0, 3.0))
        self.assertEqual(find_translation(dict(zarr.open_group(str(destination), mode="r").attrs)), [0.0, 0.0, -9.0, -10.0])

    def test_no_frame_writes_no_translation(self) -> None:
        import zarr

        destination = dio.export_ome_zarr_dataset(self.dataset, self.root / "plain.ome.zarr", chunk_size_px=16)
        self.assertIsNone(find_translation(dict(zarr.open_group(str(destination), mode="r").attrs)))
        attrs = self.lspr_attrs(destination)
        self.assertEqual((attrs["chunk_size_px"], attrs["chunk_shape_px"]), (16, [16, 16]))
        self.assertNotIn("export_frame", attrs)

    def test_summary_round_trip_and_folder_name(self) -> None:
        frame = (-5, -3, W + 5, H + 3)
        destination = self.export("summary", frame)
        new = dio.describe_new_ome_zarr_export(self.dataset, None, chunk_size_px=64, compression_enabled=True,
                                               shard_mode="per_image", chunk_shape=CHUNK, frame=frame)
        existing = dio.read_existing_ome_zarr_summary(destination)
        self.assertEqual((existing.chunk_shape, existing.frame), (CHUNK, frame))
        same, rows = dio.compare_ome_zarr_summaries(existing, new)
        self.assertTrue(same, rows)
        name = dio.build_ome_zarr_export_folder_name("x", width=new.width, height=new.height, spectral_cube_count=1,
                                                     wavelength_count=2, chunk_size_px=64, shard_mode="per_image",
                                                     compression_enabled=True, dtype=np.uint16, chunk_shape=CHUNK)
        self.assertIn("_c12x16IZ_", name)

    def test_frame_outside_the_image_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.export("outside", (W + 10, 0, 20, 20))


if __name__ == "__main__":
    unittest.main()
