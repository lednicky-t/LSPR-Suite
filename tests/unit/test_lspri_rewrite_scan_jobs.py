"""Pure-logic tests for analysis step 1's job builder (`lspr_imaging_app.analysis.scan_jobs`): the app's state -> a `ScanPlan`.

The builder only resolves inputs (which frames are spectral, the ROI layout of each cube, the chromatic affine and the ignore
mask of each frame, the wavelength axis), so the fakes below are plain callables. The reference for the ignore mask is the
existing `preprocess.resolve_external_mask`; for everything else it is what `AnalysisEngine._gather_wavelength_inputs` does.
Design record: `apps/LSPRi/eva/docs/analysis_redesign_plan_2026-10-19.md`, sections 14-15. No Qt, no files.
"""

from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.scan import scan_cubes
    from lspr_imaging_app.analysis.scan_fingerprint import cell_hashes
    from lspr_imaging_app.analysis.scan_jobs import MaskRef, ScanSource, authored_mask_digest, build_scan_plan, snapshot_source
    from lspr_imaging_app.image_tools.background.model import BackgroundSettings
    from lspr_imaging_app.image_tools.geometry.model import GeometrySettings
    from lspr_imaging_app.image_tools.mask.model import MaskChange
    from lspr_imaging_app.image_tools.preprocess import resolve_external_mask
    from lspr_imaging_app.roi.model import AreaRoi, AreaRoiDetectionSettings
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402

H, W = 40, 60
IDENTITY = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])


def _rois(shift: float = 0.0, ids=(1, 2, 3)):
    return [AreaRoi(i, 12.0 + 15.0 * k + shift, 14.0, 8.0, reference_inner_diameter_px=11.0, reference_outer_diameter_px=16.0)
            for k, i in enumerate(ids)]


class _Fake:
    """A small app: ``wavelengths[cube]`` lists every frame of a cube (0.0 = dark frame); everything else is configurable."""

    def __init__(self, wavelengths: dict[int, list[float]], *, timeline: bool = False, rois_by_cube=None):
        self.wavelengths = wavelengths
        self.timeline = timeline
        self.rois_by_cube = rois_by_cube or {}
        self.masks: dict[tuple[int, float], tuple] = {}
        self.set_aside: set[tuple[int, float]] = set()
        self.affines: dict[tuple[int, float], np.ndarray] = {}
        self.between_calls: list = []
        self.loads = 0  # how many mask lookups were made (a lookup is cheap; decoding happens in MaskRef.load)
        self.geometry = GeometrySettings()

    def affine(self, cube, wl):
        return self.affines.get((cube, wl), IDENTITY)

    def between(self, a, b):
        self.between_calls.append((a, b))
        return np.array([[1.0, 0.0, 2.0], [0.0, 1.0, -1.0]])

    def _mask_ref(self, cube, wl):
        entry = self.masks.get((cube, wl))
        if entry is None:
            return None
        authored, array, scope = entry
        self.loads += 1
        return MaskRef(authored, scope, lambda: array)

    def source(self) -> ScanSource:
        return ScanSource(
            cube_indices=lambda: tuple(self.wavelengths),
            wavelengths_for_cube=lambda c: tuple(self.wavelengths[c]),
            is_spectral=lambda c, w: w != 0.0 and (c, w) not in self.set_aside,
            record_path=lambda c, w: f"c{c}_w{w}.tif",
            rois=lambda: _rois(),
            rois_at=lambda c: self.rois_by_cube.get(c, _rois()),
            has_timeline=lambda: self.timeline,
            chromatic_affine=self.affine,
            chromatic_affine_between=self.between,
            resolve_mask=lambda c, w: self._mask_ref(c, w),
            geometry=lambda: self.geometry,
            background=lambda: BackgroundSettings(),
            detection=lambda: AreaRoiDetectionSettings(),
            default_reference_diameters=lambda: (11.0, 16.0),
        )


def _plain(cubes=3, wls=(0.0, 500.0, 510.0, 520.0)):
    return _Fake({c: list(wls) for c in range(cubes)})


class FramesAndAxisTests(unittest.TestCase):
    def test_dark_frame_and_set_aside_frames_are_left_out(self) -> None:
        app = _plain()
        app.set_aside.add((1, 510.0))
        plan = build_scan_plan(app.source())
        self.assertEqual(plan.cube_indices, (0, 1, 2))
        self.assertEqual([f.path for f in plan.jobs[0].frames], ["c0_w500.0.tif", "c0_w510.0.tif", "c0_w520.0.tif"])
        self.assertEqual([f.path for f in plan.jobs[1].frames], ["c1_w500.0.tif", "c1_w520.0.tif"])
        self.assertEqual(plan.wavelength_axis_nm, (500.0, 510.0, 520.0))
        self.assertEqual(plan.axis_positions[1], (0, 2))  # the gap keeps its column
        self.assertEqual(plan.n_frames, 3 + 2 + 3)

    def test_same_wavelength_set_gives_no_warning_and_identity_columns(self) -> None:
        plan = build_scan_plan(_plain().source())
        self.assertEqual(plan.warnings, ())
        self.assertTrue(all(p == (0, 1, 2) for p in plan.axis_positions))
        self.assertEqual(plan.roi_ids, (1, 2, 3))
        self.assertEqual([j.cube_pos for j in plan.jobs], [0, 1, 2])

    def test_cubes_with_different_wavelengths_use_the_union_and_warn(self) -> None:
        app = _Fake({0: [0.0, 500.0, 510.0], 1: [0.0, 500.0, 520.0], 2: [0.0, 500.0, 510.0]})
        with self.assertLogs("lspr_imaging_app.analysis.scan_jobs", level="WARNING"):
            plan = build_scan_plan(app.source())
        self.assertEqual(plan.wavelength_axis_nm, (500.0, 510.0, 520.0))
        self.assertEqual(plan.axis_positions, ((0, 1), (0, 2), (0, 1)))
        self.assertEqual(len(plan.warnings), 1)
        self.assertIn("3 of 3 cubes", plan.warnings[0])  # each cube lacks one column of the union axis

    def test_float_noise_in_a_wavelength_is_the_same_column(self) -> None:
        app = _Fake({0: [0.0, 500.0, 510.0], 1: [0.0, 500.004, 510.0]})
        plan = build_scan_plan(app.source())
        self.assertEqual(len(plan.wavelength_axis_nm), 2)
        self.assertEqual(plan.warnings, ())
        self.assertEqual(plan.axis_positions, ((0, 1), (0, 1)))

    def test_two_frames_on_one_rounded_wavelength_is_an_error(self) -> None:
        app = _Fake({0: [0.0, 500.0, 500.003, 510.0]})
        with self.assertRaises(ValueError):
            build_scan_plan(app.source())

    def test_cube_subset_and_unknown_cubes(self) -> None:
        plan = build_scan_plan(_plain(cubes=4).source(), cubes=[2, 0])
        self.assertEqual(plan.cube_indices, (2, 0))
        self.assertEqual([j.cube_pos for j in plan.jobs], [0, 1])
        with self.assertRaises(ValueError):
            build_scan_plan(_plain().source(), cubes=[7])
        with self.assertRaises(ValueError):
            build_scan_plan(_plain().source(), cubes=[])

    def test_nothing_spectral_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            build_scan_plan(_Fake({0: [0.0]}).source())

    def test_the_affine_is_copied_so_a_later_edit_cannot_change_the_plan(self) -> None:
        app = _plain(cubes=1)
        shifted = np.array([[1.002, 0.0, 0.5], [0.0, 0.998, -0.5]])
        app.affines[(0, 510.0)] = shifted
        plan = build_scan_plan(app.source())
        self.assertTrue(np.array_equal(plan.jobs[0].frames[1].affine, shifted))
        self.assertTrue(np.array_equal(plan.jobs[0].frames[0].affine, IDENTITY))
        shifted[0, 2] = 99.0
        self.assertEqual(plan.jobs[0].frames[1].affine[0, 2], 0.5)


class RoiLayoutTests(unittest.TestCase):
    def test_without_a_timeline_every_cube_shares_one_roi_set(self) -> None:
        plan = build_scan_plan(_plain().source())
        self.assertIs(plan.jobs[0].rois, plan.jobs[1].rois)

    def test_a_timeline_gives_each_cube_its_own_layout(self) -> None:
        app = _plain()
        app.timeline = True
        app.rois_by_cube = {0: _rois(), 1: _rois(shift=3.0), 2: _rois(shift=3.0)}
        plan = build_scan_plan(app.source())
        keys = [job.rois.key for job in plan.jobs]
        self.assertNotEqual(keys[0], keys[1])
        self.assertEqual(keys[1], keys[2])  # same geometry = same layout key, so workers reuse the pixel layout
        self.assertEqual(plan.jobs[1].rois.cx[0], 12.0 + 3.0)

    def test_roi_ids_must_be_one_list_for_every_cube(self) -> None:
        app = _plain()
        app.timeline = True
        app.rois_by_cube = {0: _rois(), 1: _rois(ids=(1, 2, 4)), 2: _rois()}
        with self.assertRaises(ValueError):
            build_scan_plan(app.source())

    def test_no_rois_is_an_error(self) -> None:
        app = _plain()
        source = app.source()
        empty = ScanSource(**{**source.__dict__, "rois": lambda: []})
        with self.assertRaises(ValueError):
            build_scan_plan(empty)


class IgnoreMaskKeyTests(unittest.TestCase):
    """The staleness check does not build the ignore masks (2026-10-24): it hashes what they are made from (the authored pixels and the
    chromatic warp), which gives the same answer for far less work."""

    def setUp(self) -> None:
        rng = np.random.default_rng(3)
        self.pixels = rng.random((H, W)) > 0.8
        self.change = MaskChange((0, 500.0), "individual", self.pixels)

    def _source(self, app, change, loads):
        def resolve(cube, wl):
            if (cube, wl) != (1, 510.0):
                return None
            return MaskRef(change.frame, change.scope, lambda: (loads.append(1), change.mask)[1], change.content_digest)

        return ScanSource(**{**app.source().__dict__, "resolve_mask": resolve})

    def test_a_masks_digest_is_that_of_its_pixels(self) -> None:
        self.assertEqual(self.change.content_digest(), authored_mask_digest(self.pixels))
        other = self.pixels.copy()
        other[0, 0] = not other[0, 0]
        self.assertNotEqual(MaskChange((0, 500.0), "individual", other).content_digest(), self.change.content_digest())

    def test_a_plan_made_for_checking_decodes_nothing_and_hashes_like_a_full_plan(self) -> None:
        app = _plain()
        loads: list[int] = []
        full = build_scan_plan(self._source(app, self.change, loads))
        self.assertEqual(len(loads), 1)  # the full plan decodes the mask (once: the frames of one mask share it)
        loads.clear()
        light = build_scan_plan(self._source(app, self.change, loads), resolve_masks=False)
        self.assertEqual(loads, [])
        self.assertFalse(light.masks_resolved)
        self.assertEqual(light.ignore_mask_bytes, 0)
        frame = light.jobs[1].frames[1]
        self.assertIsNone(frame.ignore)
        self.assertIsNotNone(frame.ignore_key)
        self.assertEqual(frame.ignore_key, full.jobs[1].frames[1].ignore_key)
        self.assertTrue(np.array_equal(cell_hashes(light), cell_hashes(full)))

    def test_the_hash_follows_the_pixels_and_the_warp_and_only_of_the_cubes_that_have_the_mask(self) -> None:
        app = _plain()
        base = cell_hashes(build_scan_plan(self._source(app, self.change, []), resolve_masks=False))
        edited = self.pixels.copy()
        edited[3, 4] = not edited[3, 4]
        changed = cell_hashes(build_scan_plan(self._source(app, MaskChange((0, 500.0), "individual", edited), []), resolve_masks=False))
        self.assertTrue(np.array_equal(changed[[0, 2]], base[[0, 2]]))  # the other cubes do not have the mask
        self.assertFalse(np.any(changed[1] == base[1]))
        app.between = lambda a, b: np.array([[1.0, 0.0, 3.0], [0.0, 1.0, -1.0]])  # the chromatic warp to the frame moved
        warped = cell_hashes(build_scan_plan(self._source(app, self.change, []), resolve_masks=False))
        self.assertTrue(np.array_equal(warped[[0, 2]], base[[0, 2]]))
        self.assertFalse(np.any(warped[1] == base[1]))

    def test_a_plan_made_for_checking_cannot_be_scanned(self) -> None:
        app = _plain()
        light = build_scan_plan(self._source(app, self.change, []), resolve_masks=False)
        with self.assertRaises(ValueError):
            list(scan_cubes(light.jobs, light.settings, processes=1))


class IgnoreMaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.authored = np.zeros((H, W), dtype=bool)
        self.authored[5:12, 20:30] = True

    def test_a_mask_authored_at_the_frame_needs_no_warp(self) -> None:
        app = _plain(cubes=1)
        app.masks[(0, 500.0)] = ((0, 500.0), self.authored, "individual")
        plan = build_scan_plan(app.source())
        self.assertEqual(app.between_calls, [])
        frames = plan.jobs[0].frames
        self.assertTrue(np.array_equal(frames[0].ignore, resolve_external_mask(self.authored, app.geometry, None)))
        self.assertIsNone(frames[1].ignore)

    def test_a_mask_from_another_frame_is_warped_and_equals_the_existing_resolver(self) -> None:
        app = _plain(cubes=2)
        app.masks[(1, 510.0)] = ((0, 500.0), self.authored, "persistent")
        plan = build_scan_plan(app.source())
        self.assertEqual(app.between_calls, [((0, 500.0), (1, 510.0))])
        expected = resolve_external_mask(self.authored, app.geometry, app.between((0, 500.0), (1, 510.0)))
        self.assertTrue(np.array_equal(plan.jobs[1].frames[1].ignore, expected))

    def test_a_persistent_mask_is_one_array_per_wavelength_not_one_per_frame(self) -> None:
        app = _plain(cubes=5)
        for cube in range(1, 5):  # cube 0 is where it was authored (no warp); the later cubes all use it with the same warp
            for wl in (500.0, 510.0, 520.0):
                app.masks[(cube, wl)] = ((0, wl), self.authored, "persistent")
        plan = build_scan_plan(app.source())
        arrays = {id(f.ignore) for job in plan.jobs for f in job.frames if f.ignore is not None}
        self.assertLessEqual(len(arrays), 3)  # at most one per (authored frame, scope, warp); the resolver may share even more
        self.assertEqual(plan.ignore_mask_bytes, len(arrays) * H * W)

    def test_masks_with_different_authored_frames_are_not_mixed_up(self) -> None:
        other = np.zeros((H, W), dtype=bool)
        other[30:35, 40:50] = True
        app = _plain(cubes=2)
        app.masks[(0, 500.0)] = ((0, 500.0), self.authored, "individual")
        app.masks[(1, 500.0)] = ((1, 500.0), other, "individual")
        plan = build_scan_plan(app.source())
        self.assertFalse(np.array_equal(plan.jobs[0].frames[0].ignore, plan.jobs[1].frames[0].ignore))


class SnapshotTests(unittest.TestCase):
    """`snapshot_source` reads everything now, on the calling thread; the plan built from it must not see later edits."""

    def test_snapshot_gives_the_same_plan_as_the_live_source(self) -> None:
        app = _Fake({c: [0.0, 500.0, 510.0] for c in range(3)}, timeline=True,
                    rois_by_cube={0: _rois(), 1: _rois(shift=2.0), 2: _rois(shift=2.0)})
        app.masks[(1, 510.0)] = ((0, 500.0), np.ones((H, W), dtype=bool), "persistent")
        live = build_scan_plan(app.source())
        snap = build_scan_plan(snapshot_source(app.source()))
        self.assertEqual(live.wavelength_axis_nm, snap.wavelength_axis_nm)
        self.assertEqual(live.axis_positions, snap.axis_positions)
        self.assertEqual([j.rois.key for j in live.jobs], [j.rois.key for j in snap.jobs])
        for a, b in zip(live.jobs, snap.jobs):
            for fa, fb in zip(a.frames, b.frames):
                self.assertEqual(fa.path, fb.path)
                self.assertTrue(np.array_equal(fa.affine, fb.affine))
                self.assertEqual(fa.ignore is None, fb.ignore is None)
                if fa.ignore is not None:
                    self.assertTrue(np.array_equal(fa.ignore, fb.ignore))

    def test_edits_after_the_snapshot_do_not_change_the_plan(self) -> None:
        app = _plain(cubes=2)
        shifted = np.array([[1.002, 0.0, 0.5], [0.0, 0.998, -0.5]])
        app.affines[(0, 500.0)] = shifted
        live_rois = _rois()
        source = app.source()
        source = ScanSource(**{**source.__dict__, "rois": lambda: live_rois, "rois_at": lambda c: live_rois})
        snap = snapshot_source(source)
        live_rois[0].center_x = 99.0  # the user drags a ROI while the scan is being prepared
        app.affines[(0, 500.0)] = IDENTITY
        app.set_aside.add((0, 510.0))
        plan = build_scan_plan(snap)
        self.assertEqual(plan.jobs[0].rois.cx[0], 12.0)
        self.assertTrue(np.array_equal(plan.jobs[0].frames[0].affine, shifted))
        self.assertEqual(len(plan.jobs[0].frames), 3)  # 510 nm still counts

    def test_masks_stay_compressed_until_the_plan_is_built(self) -> None:
        decoded = []
        app = _plain(cubes=2)
        app.masks[(1, 500.0)] = ((1, 500.0), np.ones((H, W), dtype=bool), "individual")
        source = app.source()
        inner = source.resolve_mask

        def counting(cube, wl):
            ref = inner(cube, wl)
            if ref is None:
                return None
            return MaskRef(ref.authored_frame, ref.scope, lambda: decoded.append(1) or ref.load())

        snap = snapshot_source(ScanSource(**{**source.__dict__, "resolve_mask": counting}))
        self.assertEqual(decoded, [])  # the snapshot only looked masks up
        build_scan_plan(snap)
        self.assertEqual(len(decoded), 1)

    def test_snapshot_checks_cubes_like_the_plan(self) -> None:
        with self.assertRaises(ValueError):
            snapshot_source(_plain().source(), cubes=[9])
        snap = snapshot_source(_plain(cubes=4).source(), cubes=[3, 1])
        self.assertEqual(tuple(snap.cube_indices()), (3, 1))

    def test_a_timeline_roi_that_is_reused_is_copied_once(self) -> None:
        stored = _rois()
        app = _plain(cubes=3)
        app.timeline = True
        source = app.source()
        source = ScanSource(**{**source.__dict__, "rois_at": lambda c: stored})
        snap = snapshot_source(source)
        self.assertIs(snap.rois_at(0)[0], snap.rois_at(2)[0])
        self.assertIsNot(snap.rois_at(0)[0], stored[0])


if __name__ == "__main__":
    unittest.main()
