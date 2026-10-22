"""Carrying a session into an exported dataset's coordinates (`storage/session_carry.py`, 2026-10-22). Pure logic, no Qt.

What is worth pinning: every position moves by the same map the pixels move by (a one-pixel feature is found where the carried
ROI / mask / pointer says), the chromatic affine keeps meaning the same thing on the shifted grid, geometry becomes the identity
while calibration stays, the input is never changed, and a mask or pointer that no longer fits is counted, not hidden.
Design: apps/LSPRi/eva/docs/session_carry_export_2026-10-22.md.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.chromatic.model import (
        ChromaticLandmarkObservation,
        ChromaticSettings,
        ChromaticTransformModel,
        GridBoundsDefinition,
    )
    from lspr_imaging_app.image_tools.geometry.model import CropDefinition, GeometrySettings
    from lspr_imaging_app.image_tools.geometry.transform import apply_spatial_preprocessing
    from lspr_imaging_app.image_tools.mask.mask_pointers_state import MaskPointer, MaskPointersState
    from lspr_imaging_app.image_tools.mask.model import MaskChange
    from lspr_imaging_app.image_tools.mask.transient_detection import KIND_GLOBAL, KIND_PARTICLE, ContourMask, TransientEvent
    from lspr_imaging_app.progress import Cancelled
    from lspr_imaging_app.roi.model import AreaRoi, AreaRoiGroup, RoiArrayGroup, RoiGeometry, RoiMask, RoiTimeline
    from lspr_imaging_app.storage import session_carry as carry
    from lspr_imaging_app.storage.session import SessionState
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

RAW_SHAPE = (300, 400)  # (height, width): small, so the tests stay fast


def _rotated() -> carry.ExportTransform:
    """A tilted dataset cropped inside the rotated canvas, exported with the grid padded 7 px left and 4 px up."""
    geometry = GeometrySettings(
        image_tools_enabled=True,
        rotation_angle_deg=1.4,
        crop=CropDefinition(x=8, y=6, width=370, height=270, enabled=True),
        calibration_enabled=True,
        microns_per_pixel_x=0.25,
        microns_per_pixel_y=0.25,
    )
    return carry.ExportTransform(geometry, RAW_SHAPE, frame=(-7, -4, 384, 280))


def _shift_only() -> carry.ExportTransform:
    return carry.ExportTransform(GeometrySettings(image_tools_enabled=False), RAW_SHAPE, frame=(-5, 3, 420, 290))


def _spot(shape, x, y) -> np.ndarray:
    image = np.zeros(shape, dtype=np.float32)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    image += np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 2.0**2)).astype(np.float32)
    return image


def _peak_xy(image: np.ndarray) -> tuple[float, float]:
    finite = np.nan_to_num(image, nan=-1.0)
    y, x = np.unravel_index(np.argmax(finite), finite.shape)
    return float(x), float(y)


def _state(**kwargs) -> SessionState:
    return SessionState(**kwargs)


class TransformTests(unittest.TestCase):
    def test_a_frame_sets_the_origin_and_the_stored_shape(self) -> None:
        transform = _rotated()
        self.assertEqual(transform.origin, (-7, -4))
        self.assertEqual(transform.stored_shape, (280, 384))
        self.assertEqual(transform.processed_shape, (270, 370))
        self.assertTrue(transform.lossless)

    def test_no_frame_means_the_processed_image_unshifted(self) -> None:
        transform = carry.ExportTransform(_rotated().geometry, RAW_SHAPE)
        self.assertEqual(transform.origin, (0, 0))
        self.assertEqual(transform.stored_shape, transform.processed_shape)

    def test_a_trimming_frame_is_not_lossless(self) -> None:
        transform = carry.ExportTransform(_rotated().geometry, RAW_SHAPE, frame=(10, 0, 300, 270))
        self.assertFalse(transform.lossless)

    def test_tools_off_only_shifts(self) -> None:
        transform = _shift_only()
        self.assertFalse(transform.moves_pixels)
        np.testing.assert_allclose(transform.raw_to_stored(), [[1, 0, 5], [0, 1, -3]])

    def test_a_feature_is_where_the_map_says_it_is(self) -> None:
        # The image path and the point map must agree, or every carried position would be off by a rotation's worth.
        transform = _rotated()
        for raw_x, raw_y in ((120.0, 90.0), (250.0, 200.0), (60.0, 150.0)):
            image = apply_spatial_preprocessing(_spot(RAW_SHAPE, raw_x, raw_y), transform.geometry)
            processed_peak = _peak_xy(image)
            expected = transform.raw_to_stored() @ np.array([raw_x, raw_y, 1.0])
            stored = (processed_peak[0] - transform.origin[0], processed_peak[1] - transform.origin[1])
            self.assertAlmostEqual(stored[0], expected[0], delta=1.0)
            self.assertAlmostEqual(stored[1], expected[1], delta=1.0)


class MaskTests(unittest.TestCase):
    def test_a_one_pixel_mask_lands_where_the_map_says(self) -> None:
        transform = _rotated()
        raw = np.zeros(RAW_SHAPE, dtype=bool)
        raw[100:104, 150:154] = True  # a 4 x 4 block, centre (151.5, 101.5)
        (change,), report = carry.carry_masks((MaskChange((3, 500.0), "individual", raw),), transform)
        warped = change.mask
        self.assertEqual(warped.shape, transform.stored_shape)
        ys, xs = np.nonzero(warped)
        expected = transform.raw_to_stored() @ np.array([151.5, 101.5, 1.0])
        self.assertAlmostEqual(float(xs.mean()), expected[0], delta=1.0)
        self.assertAlmostEqual(float(ys.mean()), expected[1], delta=1.0)
        self.assertEqual(report.masks_carried, 1)

    def test_a_mask_is_the_apps_own_warp_cut_to_the_frame(self) -> None:
        from lspr_imaging_app.image_tools.geometry.transform import apply_spatial_mask

        transform = _rotated()
        rng = np.random.default_rng(1)
        raw = rng.random(RAW_SHAPE) > 0.9
        (change,), _ = carry.carry_masks((MaskChange((0, 500.0), "persistent", raw),), transform)
        processed = apply_spatial_mask(raw, transform.geometry)
        ox, oy = transform.origin  # (-7, -4): the stored grid starts 7 / 4 px before the processed image
        stored = change.mask
        np.testing.assert_array_equal(stored[-oy : -oy + processed.shape[0], -ox : -ox + processed.shape[1]], processed)
        self.assertFalse(stored[:-oy].any())  # the padding is empty
        self.assertFalse(stored[:, :-ox].any())

    def test_frame_scope_and_order_are_kept(self) -> None:
        transform = _shift_only()
        raw = np.zeros(RAW_SHAPE, dtype=bool)
        raw[10:20, 10:20] = True
        changes = (MaskChange((2, 600.0), "individual", raw), MaskChange((0, 500.0), "persistent", raw))
        carried, _ = carry.carry_masks(changes, transform)
        self.assertEqual([(c.frame, c.scope) for c in carried], [((2, 600.0), "individual"), ((0, 500.0), "persistent")])
        ys, xs = np.nonzero(carried[0].mask)
        self.assertEqual((xs.min(), ys.min()), (10 + 5, 10 - 3))  # shifted by the frame origin (-5, 3)

    def test_a_mask_of_another_size_is_left_out_and_counted(self) -> None:
        transform = _shift_only()
        carried, report = carry.carry_masks((MaskChange((0, 500.0), "persistent", np.ones((10, 10), dtype=bool)),), transform)
        self.assertEqual(carried, ())
        self.assertEqual(report.masks_dropped, 1)

    def test_a_mask_trimmed_away_is_counted_as_emptied(self) -> None:
        transform = carry.ExportTransform(GeometrySettings(image_tools_enabled=False), RAW_SHAPE, frame=(200, 0, 100, 100))
        raw = np.zeros(RAW_SHAPE, dtype=bool)
        raw[10:20, 10:20] = True
        carried, report = carry.carry_masks((MaskChange((0, 500.0), "persistent", raw),), transform)
        self.assertEqual(len(carried), 1)
        self.assertEqual(report.masks_emptied, 1)

    def test_cancel_stops_the_run(self) -> None:
        raw = np.zeros(RAW_SHAPE, dtype=bool)
        changes = tuple(MaskChange((i, 500.0), "individual", raw) for i in range(5))
        with self.assertRaises(Cancelled):
            carry.carry_masks(changes, _shift_only(), cancelled=lambda: True)


class ChromaticTests(unittest.TestCase):
    def test_the_affine_means_the_same_on_the_shifted_grid(self) -> None:
        a = [[1.002, 0.001, 0.8], [-0.001, 0.998, -1.3]]
        origin = (-7, -4)
        moved = np.array(carry.carry_affine(a, origin))
        old = np.array(a)
        for q in ((10.0, 20.0), (300.0, 150.0)):  # a point on the new grid
            q_old = np.array([q[0] + origin[0], q[1] + origin[1]])  # the same point on the old grid
            via_old = old @ np.array([*q_old, 1.0]) - np.array(origin)  # old affine, then back to the new grid
            direct = moved @ np.array([*q, 1.0])
            np.testing.assert_allclose(direct, via_old, atol=1e-12)

    def test_models_landmarks_and_bounds_move_together(self) -> None:
        transform = _shift_only()  # origin (-5, 3)
        model = ChromaticTransformModel(
            spectral_cube_index=2, wavelength_nm=620.0, affine_matrix=[[1.0, 0.0, 0.5], [0.0, 1.0, -0.25]],
            global_shift_x_px=0.5, global_shift_y_px=-0.25, rmse_px=0.04,
        )
        state = _state(
            chromatic_settings=ChromaticSettings(
                chromatic_correction_enabled=True,
                chromatic_grid_bounds=GridBoundsDefinition(x=20, y=30, width=100, height=80, enabled=True),
            ),
            chromatic_models=(model,),
            chromatic_landmarks=(ChromaticLandmarkObservation(1, 2, 620.0, 40.0, 50.0),),
        )
        result = carry.carry_session(state, transform).state
        carried = result.chromatic_models[0]
        # a pure translation: the matrix is [[1,0,t],[0,1,t]] and the new t is the old one (the shift cancels for L = I)
        self.assertEqual(carried.affine_matrix, [[1.0, 0.0, 0.5], [0.0, 1.0, -0.25]])
        self.assertEqual((carried.global_shift_x_px, carried.global_shift_y_px), (0.5, -0.25))
        self.assertEqual(carried.rmse_px, 0.04)  # the fit quality is not touched
        self.assertEqual((result.chromatic_landmarks[0].x_px, result.chromatic_landmarks[0].y_px), (45.0, 47.0))
        bounds = result.chromatic_settings.chromatic_grid_bounds
        self.assertEqual((bounds.x, bounds.y, bounds.width, bounds.height), (25, 27, 100, 80))
        self.assertEqual(result.chromatic_settings.chromatic_correction_enabled, True)

    def test_a_scaling_affine_gets_a_new_translation(self) -> None:
        moved = carry.carry_affine([[1.01, 0.0, 0.0], [0.0, 1.01, 0.0]], (-100, -50))
        # old: p -> 1.01 p. New grid q = p - o. q -> 1.01 (q + o) - o: translation = 0.01 * o
        self.assertAlmostEqual(moved[0][2], 0.01 * -100, places=9)
        self.assertAlmostEqual(moved[1][2], 0.01 * -50, places=9)

    def test_a_grid_search_area_outside_the_new_grid_is_switched_off(self) -> None:
        transform = carry.ExportTransform(GeometrySettings(image_tools_enabled=False), RAW_SHAPE, frame=(200, 0, 100, 100))
        state = _state(
            chromatic_settings=ChromaticSettings(chromatic_grid_bounds=GridBoundsDefinition(x=10, y=10, width=50, height=50, enabled=True))
        )
        bounds = carry.carry_session(state, transform).state.chromatic_settings.chromatic_grid_bounds
        self.assertFalse(bounds.enabled)


class RoiTests(unittest.TestCase):
    def _roi(self) -> AreaRoi:
        timeline = RoiTimeline().with_change(5, "persistent", RoiGeometry(95.0, 65.0, 20.0, 28.0, 36.0))
        return AreaRoi(
            area_roi_id=1, center_x=100.0, center_y=70.0, sample_diameter_px=20.0, array_id="a",
            per_wavelength={(0, 620.0): (101.0, 71.5)}, timeline=timeline,
            sample_mask=RoiMask(x0=90, y0=60, mask=np.ones((4, 4), dtype=bool)),
        )

    def test_rois_move_by_the_frame_origin_and_keep_their_identity(self) -> None:
        transform = _shift_only()  # origin (-5, 3)
        state = _state(
            rois=(self._roi(),),
            groups=(AreaRoiGroup("g1", "first", area_roi_ids=[1]),),
            arrays=(RoiArrayGroup("a", "grid", 2, 2, 40.0, 40.0, 100.0, 70.0, rotation_deg=1.4, member_area_roi_ids=[1]),),
            selected_roi_ids=(1,),
        )
        result = carry.carry_session(carry.snapshot_state(state), transform)
        roi = result.state.rois[0]
        self.assertEqual((roi.center_x, roi.center_y), (105.0, 67.0))
        self.assertEqual(roi.per_wavelength, {(0, 620.0): (106.0, 68.5)})
        self.assertEqual((roi.sample_mask.x0, roi.sample_mask.y0), (95, 57))
        change = roi.timeline.persistent[0].geometry
        self.assertEqual((change.center_x, change.center_y), (100.0, 62.0))
        self.assertEqual((roi.sample_diameter_px, roi.array_id, roi.area_roi_id), (20.0, "a", 1))  # nothing but position changes
        array = result.state.arrays[0]
        self.assertEqual((array.anchor_x_px, array.anchor_y_px, array.rotation_deg), (105.0, 67.0, 1.4))
        self.assertEqual(result.state.groups[0].area_roi_ids, [1])
        self.assertEqual(result.state.selected_roi_ids, (1,))
        self.assertEqual(result.report.rois, 1)

    def test_a_roi_pushed_outside_the_image_is_counted(self) -> None:
        transform = carry.ExportTransform(GeometrySettings(image_tools_enabled=False), RAW_SHAPE, frame=(150, 0, 100, 100))
        roi = AreaRoi(1, 100.0, 50.0, 20.0)
        report = carry.carry_session(_state(rois=(roi,)), transform).report
        self.assertEqual(report.rois_outside_image, 1)

    def test_the_input_is_never_changed_and_a_snapshot_is_independent(self) -> None:
        transform = _shift_only()
        roi = self._roi()
        state = _state(rois=(roi,), groups=(AreaRoiGroup("g1", "first", area_roi_ids=[1]),))
        snapshot = carry.snapshot_state(state)
        carry.carry_session(snapshot, transform)
        self.assertEqual((roi.center_x, roi.center_y), (100.0, 70.0))
        roi.center_x = 999.0  # the toolbox edits ROIs in place
        roi.per_wavelength[(0, 620.0)] = (0.0, 0.0)
        state.groups[0].area_roi_ids.append(7)
        self.assertEqual(snapshot.rois[0].center_x, 100.0)
        self.assertEqual(snapshot.rois[0].per_wavelength, {(0, 620.0): (101.0, 71.5)})
        self.assertEqual(snapshot.groups[0].area_roi_ids, [1])


class SettingsTests(unittest.TestCase):
    def test_geometry_becomes_the_identity_but_calibration_stays(self) -> None:
        state = _state(geometry=_rotated().geometry)
        geometry = carry.carry_session(state, _rotated()).state.geometry
        self.assertEqual((geometry.rotation_angle_deg, geometry.flip_horizontal, geometry.flip_vertical), (0.0, False, False))
        self.assertFalse(geometry.crop.enabled)
        self.assertEqual((geometry.calibration_enabled, geometry.microns_per_pixel_x), (True, 0.25))
        self.assertTrue(geometry.image_tools_enabled)

    def test_settings_that_do_not_depend_on_position_are_copied(self) -> None:
        from lspr_imaging_app.image_tools.background.model import BackgroundSettings
        from lspr_imaging_app.selection.frame_roles_state import FrameRolesState

        background = BackgroundSettings(flatten_background_enabled=True, flatten_background_sigma_px=40.0, flatten_background_binning=4)
        roles = FrameRolesState(dark_wavelength_nm=0.0, dark_user_set=True, ignored_cubes=(3, 4))
        state = _state(background=background, frame_roles=roles, selected_cube=7, selected_wavelength=620.0)
        result = carry.carry_session(state, _rotated()).state
        self.assertEqual(result.background, background)
        self.assertEqual(result.frame_roles, roles)
        self.assertEqual((result.selected_cube, result.selected_wavelength), (7, 620.0))


class PointerTests(unittest.TestCase):
    def _pointer(self, ident, x, y, *, kind=KIND_PARTICLE, contour=True) -> MaskPointer:
        mask = np.zeros((8, 10), dtype=bool)
        mask[2:7, 3:9] = True
        event = TransientEvent(
            frame=(ident, 620.0), kind=kind, area_px=int(mask.sum()), centroid_xy=(x, y),
            bbox_xyxy=(int(x) - 5, int(y) - 4, int(x) + 5, int(y) + 4), depth=0.8, z=100.0,
            contour=ContourMask(x0=int(x) - 5, y0=int(y) - 4, mask=mask) if contour else None,
        )
        return MaskPointer(ident, event)

    def test_a_contour_moves_with_its_object(self) -> None:
        transform = _rotated()
        pointer = self._pointer(1, 200.0, 150.0)
        state, report = carry.carry_mask_pointers(MaskPointersState(records=(pointer,)), transform)
        moved = state.records[0].event
        expected = transform.raw_to_stored() @ np.array([200.0, 150.0, 1.0])
        self.assertAlmostEqual(moved.centroid_xy[0], expected[0], places=6)
        self.assertAlmostEqual(moved.centroid_xy[1], expected[1], places=6)
        ys, xs = np.nonzero(moved.contour.mask)
        self.assertAlmostEqual(moved.contour.x0 + xs.mean(), expected[0], delta=3.0)  # the contour's own centre sits near the centroid
        self.assertAlmostEqual(moved.contour.y0 + ys.mean(), expected[1], delta=3.0)
        x0, y0, x1, y1 = moved.bbox_xyxy
        self.assertEqual((x1 - x0, y1 - y0), moved.contour.mask.shape[::-1])  # the box is the contour's, end exclusive
        self.assertEqual(moved.area_px, moved.contour.area_px)
        self.assertEqual((state.records[0].id, moved.frame, moved.kind), (1, (1, 620.0), KIND_PARTICLE))
        self.assertEqual(report.pointers_carried, 1)

    def test_a_pure_shift_moves_the_contour_by_the_origin_exactly(self) -> None:
        transform = _shift_only()  # origin (-5, 3)
        pointer = self._pointer(1, 100.0, 80.0)
        state, _ = carry.carry_mask_pointers(MaskPointersState(records=(pointer,)), transform)
        moved = state.records[0].event.contour
        original = pointer.event.contour
        # the box is trimmed to the pixels it holds, so compare the pixels' absolute positions
        def absolute(contour: ContourMask) -> set[tuple[int, int]]:
            ys, xs = np.nonzero(contour.mask)
            return {(int(x) + contour.x0, int(y) + contour.y0) for x, y in zip(xs, ys)}

        self.assertEqual(absolute(moved), {(x + 5, y - 3) for x, y in absolute(original)})

    def test_a_pointer_outside_the_new_grid_is_dropped_and_counted(self) -> None:
        transform = carry.ExportTransform(GeometrySettings(image_tools_enabled=False), RAW_SHAPE, frame=(200, 0, 100, 100))
        inside = self._pointer(2, 250.0, 50.0)
        outside = self._pointer(1, 50.0, 50.0)
        state, report = carry.carry_mask_pointers(MaskPointersState(records=(outside, inside)), transform)
        self.assertEqual([r.id for r in state.records], [2])  # ids are kept, a gap is fine
        self.assertEqual((report.pointers_carried, report.pointers_dropped), (1, 1))

    def test_a_pointer_without_a_contour_keeps_a_moved_box(self) -> None:
        transform = _shift_only()
        pointer = self._pointer(1, 100.0, 80.0, kind=KIND_GLOBAL, contour=False)
        state, _ = carry.carry_mask_pointers(MaskPointersState(records=(pointer,)), transform)
        self.assertEqual(state.records[0].event.bbox_xyxy, (100 - 5 + 5, 80 - 4 - 3, 100 + 5 + 5, 80 + 4 - 3))
        self.assertIsNone(state.records[0].event.contour)

    def test_the_scan_info_is_kept(self) -> None:
        from lspr_imaging_app.image_tools.mask.mask_pointers_state import ScanInfo

        info = ScanInfo(created_utc="2026-10-17T10:00:00+00:00", settings={"stride": 3}, frames_in_range=10)
        state, _ = carry.carry_mask_pointers(MaskPointersState(info=info, records=()), _shift_only())
        self.assertEqual(state.info, info)


class ProvenanceTests(unittest.TestCase):
    def test_the_origin_file_round_trips(self) -> None:
        transform = _rotated()
        report = carry.CarryReport(rois=160, masks_carried=411, chromatic_models=8478, lossless=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "2026-10-22_100000"
            carry.write_origin(
                root, source_folder=Path("D:/data/run1"), source_session_id="2026-10-20_090000",
                transform=transform, report=report, zarr_attributes={"chunk_shape_px": [80, 80], "dtype": "uint16"},
            )
            origin = carry.read_origin(root)
        assert origin is not None
        self.assertEqual(origin["source"]["session_id"], "2026-10-20_090000")
        self.assertEqual(origin["transform"]["rotation_angle_deg"], 1.4)
        self.assertEqual(origin["transform"]["frame"]["origin_x"], -7)
        self.assertEqual(origin["transform"]["crop"]["width"], 370)
        self.assertEqual(origin["transform"]["stored_shape_hw"], [280, 384])
        self.assertEqual(origin["carried"]["masks"], 411)
        self.assertEqual(origin["zarr"]["chunk_shape_px"], [80, 80])

    def test_a_session_without_an_origin_file_reads_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(carry.read_origin(Path(tmp)))

    def test_the_lspr_attributes_come_from_the_metadata_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "zarr.json").write_text(json.dumps({"attributes": {"lspr": {"dtype": "uint16"}}}), encoding="utf-8")
            self.assertEqual(carry.read_zarr_attributes(Path(tmp)), {"dtype": "uint16"})
            self.assertIsNone(carry.read_zarr_attributes(Path(tmp) / "missing"))


if __name__ == "__main__":
    unittest.main()
