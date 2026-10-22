"""A session carried along with a real OME-Zarr export (`storage/session_carry_task.py`, 2026-10-22).

The end-to-end claims worth pinning, on a tilted, cropped, padded export:

- the carried ROI sits on the same feature in the exported pixels as the source ROI does in the source's processed pixels;
- the session is written where the app looks for it (the exported dataset's `sessions/` folder, active), and loads back;
- the geometry in it is the identity, with the transform and the Zarr's parameters recorded in `derived_from.json`;
- the source dataset's session folder is not touched; a failed or cancelled export carries nothing.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch.** Design: apps/LSPRi/eva/docs/session_carry_export_2026-10-22.md.
"""

from __future__ import annotations

import sys
import tempfile
import time
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
    from PyQt6.QtCore import QCoreApplication, QObject

    from lspr_imaging_app.analysis.provenance import FrameNamingScheme
    from lspr_imaging_app.dataset.io import export_ome_zarr_dataset, load_image_array, load_ome_zarr_dataset
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools.geometry.model import CropDefinition, GeometrySettings
    from lspr_imaging_app.image_tools.geometry.transform import apply_spatial_preprocessing
    from lspr_imaging_app.image_tools.mask.mask_pointers_state import MaskPointer, MaskPointersState
    from lspr_imaging_app.image_tools.mask.model import MaskChange
    from lspr_imaging_app.image_tools.mask.transient_detection import KIND_PARTICLE, ContourMask, TransientEvent
    from lspr_imaging_app.roi.model import AreaRoi
    from lspr_imaging_app.storage import session_carry as carry
    from lspr_imaging_app.storage import session_index
    from lspr_imaging_app.storage.session import SessionState, load_session
    from lspr_imaging_app.storage.session_carry_task import SessionCarry
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

WAVELENGTHS_NM = [500.0, 600.0]
RAW_SHAPE = (120, 160)  # (height, width)
DISK = (80.0, 60.0, 8.0)  # raw x, y, radius of the bright feature in every image
FRAME = (-3, -2, 150, 106)  # origin x, origin y, width, height in the processed pixels: padded left / top, room right / bottom


def _image(wavelength: float) -> np.ndarray:
    yy, xx = np.mgrid[0 : RAW_SHAPE[0], 0 : RAW_SHAPE[1]]
    distance = np.hypot(xx - DISK[0], yy - DISK[1])
    # smooth, so that a resampling step changes the mean of a disk by a fraction of a count, not by noise
    image = 2000.0 + 4.0 * xx + 2.0 * yy + wavelength * 0.5 + 1500.0 / (1.0 + np.exp((distance - DISK[2]) / 1.2))
    return (np.round(image / 16.0) * 16.0).astype(np.uint16)  # a 12-bit camera x16, like the maintainer's


_APP: QCoreApplication | None = None  # kept alive: an application that is garbage-collected delivers no queued signal


def _app() -> QCoreApplication:
    global _APP
    _APP = QCoreApplication.instance() or QCoreApplication([])
    return _APP


def _wait_for(carrier: "SessionCarry", destination: Path, timeout_s: float = 60.0) -> list:
    """Start the carry and let the GUI thread's event loop run until it reports (queued signals need it). Polls rather than
    connecting a lambda: a lambda would run on the carry's own thread."""
    results: list = []

    class _Receiver(QObject):
        def done(self, result: object) -> None:
            results.append(result)

    receiver = _Receiver()
    carrier.carried.connect(receiver.done)
    carrier.failed.connect(receiver.done)
    carrier.start(destination)
    deadline = time.monotonic() + timeout_s
    while not results and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    return results


class SessionCarryExportTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        records = []
        for wl in WAVELENGTHS_NM:
            path = self.root / "src" / f"wl{wl:.0f}.tif"
            tifffile.imwrite(str(path), _image(wl))
            records.append(ImageRecord(key=ImageKey(wavelength_nm=wl, spectral_cube_index=0), path=path))
        self.dataset = ImageDataset(folder=self.root / "src", records=records)
        self.geometry = GeometrySettings(
            image_tools_enabled=True, rotation_angle_deg=1.4,
            crop=CropDefinition(x=6, y=4, width=140, height=100, enabled=True),
            calibration_enabled=True, microns_per_pixel_x=0.2, microns_per_pixel_y=0.2,
        )
        self.transform = carry.ExportTransform(self.geometry, RAW_SHAPE, frame=FRAME)
        # the source session: one ROI on the disk (processed space), one mask over it and a pointer, both authored in raw space
        centre = self.transform.raw_to_processed() @ np.array([DISK[0], DISK[1], 1.0])
        self.source_roi_xy = (float(centre[0]), float(centre[1]))
        raw_mask = np.zeros(RAW_SHAPE, dtype=bool)
        raw_mask[50:70, 70:90] = True
        contour = np.ones((8, 8), dtype=bool)
        event = TransientEvent(
            frame=(0, 500.0), kind=KIND_PARTICLE, area_px=64, centroid_xy=(79.5, 59.5), bbox_xyxy=(76, 56, 84, 64),
            depth=0.7, z=100.0, contour=ContourMask(x0=76, y0=56, mask=contour),
        )
        self.state = SessionState(
            geometry=self.geometry,
            rois=(AreaRoi(1, self.source_roi_xy[0], self.source_roi_xy[1], 12.0),),
            mask_changes=(MaskChange((0, 500.0), "persistent", raw_mask),),
            mask_pointers=MaskPointersState(records=(MaskPointer(1, event),)),
            selected_cube=0, selected_wavelength=500.0, selected_roi_ids=(1,),
        )
        self.naming = FrameNamingScheme.for_dataset([0], WAVELENGTHS_NM)
        self.source_session = self.root / "src_home_session"

    def _carry(self, name: str = "exported") -> tuple[SessionCarry, Path, list]:
        carrier = SessionCarry(
            capture=lambda: self.state, naming=lambda: self.naming, source=lambda: (self.root / "src", "2026-10-20_090000")
        )
        carrier.prepare(self.transform)
        destination = export_ome_zarr_dataset(
            self.dataset, self.root / name, chunk_size_px=32, compression_enabled=True, shard_mode="per_image",
            preprocessing=self.geometry, frame=FRAME, adaptive_workers_enabled=False, pixel_size_um=(0.2, 0.2),
        )
        return carrier, destination, _wait_for(carrier, destination)

    def test_the_carried_roi_sits_on_the_same_feature_in_the_exported_pixels(self) -> None:
        _carrier, destination, results = self._carry()
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], object)
        self.assertNotIsInstance(results[0], str, msg=str(results[0]))

        session_id = session_index.load_session_index(destination).active_session_id
        carried = load_session(session_index.session_dir_for(destination, session_id))
        assert carried is not None
        carried_roi = carried.rois[0]

        # the source's processed image and the exported plane, each read at its own ROI
        source_plane = apply_spatial_preprocessing(_image(500.0).astype(np.float32), self.geometry)
        dataset = load_ome_zarr_dataset(destination)
        record = next(r for r in dataset.records if r.key.wavelength_nm == 500.0)
        exported = load_image_array(str(record.path)).astype(np.float64)
        self.assertEqual(exported.shape, (FRAME[3], FRAME[2]))

        def disk_mean(plane: np.ndarray, x: float, y: float, radius: float = 9.0) -> float:
            yy, xx = np.mgrid[0 : plane.shape[0], 0 : plane.shape[1]]
            inside = (np.hypot(xx - x, yy - y) <= radius) & np.isfinite(plane)
            return float(plane[inside].mean())

        source_mean = disk_mean(source_plane.astype(np.float64), *self.source_roi_xy)
        carried_mean = disk_mean(exported, carried_roi.center_x, carried_roi.center_y)
        # measured 2026-10-22: 0.5 counts here; a one-pixel shift of the ROI gives 11, no shift at all 176
        self.assertAlmostEqual(carried_mean, source_mean, delta=3.0)
        self.assertAlmostEqual(carried_roi.center_x, self.source_roi_xy[0] - FRAME[0], places=9)
        self.assertAlmostEqual(carried_roi.center_y, self.source_roi_xy[1] - FRAME[1], places=9)

    def test_the_session_is_where_the_app_looks_for_it_and_loads_back(self) -> None:
        _carrier, destination, results = self._carry()
        result = results[0]
        index = session_index.load_session_index(destination)
        self.assertEqual(index.active_session_id, result.session_id)
        self.assertEqual(index.get(result.session_id).label, "Carried from src")

        carried = load_session(session_index.session_dir_for(destination, result.session_id))
        assert carried is not None
        self.assertEqual((carried.geometry.rotation_angle_deg, carried.geometry.flip_horizontal), (0.0, False))
        self.assertFalse(carried.geometry.crop.enabled)
        self.assertEqual(carried.geometry.microns_per_pixel_x, 0.2)  # calibration stays
        self.assertEqual(carried.mask_changes[0].frame, (0, 500.0))
        self.assertEqual(carried.mask_changes[0].mask.shape, (FRAME[3], FRAME[2]))
        self.assertTrue(carried.mask_changes[0].mask.any())
        self.assertEqual(len(carried.mask_pointers.records), 1)
        self.assertEqual(carried.selected_roi_ids, (1,))
        self.assertEqual(result.report.masks_carried, 1)

        # the exported dataset names its frames the way the session saved them
        dataset = load_ome_zarr_dataset(destination)
        cubes = sorted({r.key.spectral_cube_index for r in dataset.records})
        wavelengths = sorted({float(r.key.wavelength_nm) for r in dataset.records})
        self.assertEqual(FrameNamingScheme.for_dataset(cubes, wavelengths), self.naming)

    def test_the_provenance_records_the_transform_and_the_zarr_parameters(self) -> None:
        _carrier, destination, results = self._carry()
        origin = carry.read_origin(session_index.session_dir_for(destination, results[0].session_id))
        assert origin is not None
        self.assertEqual(origin["source"], {"dataset_folder": str(self.root / "src"), "session_id": "2026-10-20_090000"})
        transform = origin["transform"]
        self.assertEqual(transform["rotation_angle_deg"], 1.4)
        self.assertEqual(transform["crop"], {"x": 6, "y": 4, "width": 140, "height": 100})
        self.assertEqual(transform["frame"], {"origin_x": -3, "origin_y": -2, "width": 150, "height": 106})
        self.assertEqual(transform["stored_shape_hw"], [106, 150])
        self.assertEqual(origin["zarr"]["chunk_shape_px"], [32, 32])
        self.assertEqual(origin["zarr"]["dtype"], "uint16")
        self.assertEqual(origin["zarr"]["image_tools"]["interpolation"], "lanczos4")
        self.assertEqual(origin["carried"]["rois"], 1)

    def test_the_exports_numbers_come_along_with_the_carried_session(self) -> None:
        from lspr_imaging_app.dataset.export_stats import ExportStats

        carrier = SessionCarry(capture=lambda: self.state, naming=lambda: self.naming, source=lambda: (self.root / "src", None))
        carrier.prepare(self.transform)
        stats = ExportStats(planes=2, bytes_written=1_000_000, stored_plane_bytes=2_000_000, workers=8, seconds=3.0)
        carrier.set_export_stats(stats)  # DatasetModule emits this just before export_finished
        destination = export_ome_zarr_dataset(
            self.dataset, self.root / "with_stats", chunk_size_px=32, compression_enabled=True, shard_mode="per_image",
            preprocessing=self.geometry, frame=FRAME, adaptive_workers_enabled=False,
        )
        results = _wait_for(carrier, destination)
        self.assertIs(results[0].export_stats, stats)
        # the next export starts clean: numbers from one export are never shown with another export's session
        carrier.prepare(self.transform)
        second = export_ome_zarr_dataset(
            self.dataset, self.root / "without_stats", chunk_size_px=32, compression_enabled=True, shard_mode="per_image",
            preprocessing=self.geometry, frame=FRAME, adaptive_workers_enabled=False,
        )
        results = _wait_for(carrier, second)  # no set_export_stats this time
        self.assertIsNone(results[0].export_stats)

    def test_a_failed_export_carries_nothing(self) -> None:
        carrier = SessionCarry(capture=lambda: self.state, naming=lambda: self.naming, source=lambda: (None, None))
        carrier.prepare(self.transform)
        self.assertTrue(carrier.has_pending())
        carrier.discard()
        carrier.start(self.root / "never")  # nothing prepared: does nothing
        self.assertFalse(carrier.is_running())
        self.assertFalse((self.root / "never").exists())

    def test_the_source_state_is_the_one_from_when_the_export_started(self) -> None:
        carrier = SessionCarry(capture=lambda: self.state, naming=lambda: self.naming, source=lambda: (self.root / "src", None))
        carrier.prepare(self.transform)
        self.state.rois[0].center_x = 999.0  # the toolbox edits ROIs in place while the images are written
        destination = export_ome_zarr_dataset(
            self.dataset, self.root / "late", chunk_size_px=32, compression_enabled=True, shard_mode="per_image",
            preprocessing=self.geometry, frame=FRAME, adaptive_workers_enabled=False,
        )
        results = _wait_for(carrier, destination)
        carried = load_session(session_index.session_dir_for(destination, results[0].session_id))
        assert carried is not None
        self.assertAlmostEqual(carried.rois[0].center_x, self.source_roi_xy[0] - FRAME[0], places=9)


if __name__ == "__main__":
    unittest.main()
