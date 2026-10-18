"""Finding flowing particles in LSPRi frames: `transient_detection.py` (one frame) and `transient_scan.py` (a whole dataset).

Pure numpy, no Qt, no real dataset: the frames are synthetic (a bright field with a grid of dark disks, a little noise, a
different brightness for every wavelength, a slow drift from cube to cube) with a dark blob dropped into one cube.
Design and measurements: apps/LSPRi/eva/docs/auto_transient_mask_exploration_2026-10-16.md.
"""

from __future__ import annotations

import sys
import unittest

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.image_tools.mask import transient_detection as td
    from lspr_imaging_app.image_tools.mask import transient_gaps as tg
    from lspr_imaging_app.image_tools.mask.transient_scan import ScanParams, scan_transients
    from lspr_imaging_app.progress import Cancelled
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

HEIGHT, WIDTH = 160, 400


def _field(wavelength_index: int, cube: int, rng: np.random.Generator) -> np.ndarray:
    """A bright field with a grid of dark disks (the 'array'), as bright as this wavelength is, drifting slowly with the cube."""
    y, x = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
    field = np.full((HEIGHT, WIDTH), 30000.0, dtype=np.float32)
    field *= 0.85 + 0.15 * np.cos((x - WIDTH / 2) / WIDTH * 1.2)  # uneven illumination
    inside_disk = ((x % 40) - 20) ** 2 + ((y % 40) - 20) ** 2 < 8**2
    field[inside_disk] *= 0.55 - 0.02 * wavelength_index  # the disks absorb more at some wavelengths
    field *= 1.0 + 0.10 * wavelength_index  # every wavelength has its own brightness
    field *= 1.0 + 0.002 * cube  # slow drift
    return field * (1.0 + 0.004 * rng.standard_normal(field.shape).astype(np.float32))


def _blob(frame: np.ndarray, cx: float, cy: float, radius: float, transmittance: float) -> np.ndarray:
    y, x = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
    inside = (x - cx) ** 2 + (y - cy) ** 2 <= radius**2
    out = frame.copy()
    out[inside] *= transmittance
    return out


def _params(**kwargs) -> td.DetectionParams:
    # a small frame: bin 2, a smaller "particle" limit so the test objects stay particles
    return td.DetectionParams(bin_factor=2, **kwargs)


def _screen(frame: np.ndarray, reference: np.ndarray, params: td.DetectionParams) -> td.FrameScreen:
    return td.screen_frame(td.bin_frame(frame, 2), td.bin_frame(reference, 2), params, (2.0, 2.0))


class OneFrameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = np.random.default_rng(1)
        self.reference = _field(3, 5, self.rng)
        self.frame = _field(3, 6, self.rng)

    def test_a_clean_frame_has_nothing(self) -> None:
        self.assertEqual(_screen(self.frame, self.reference, _params()).candidates, ())

    def test_a_dark_blob_is_found_where_it_is(self) -> None:
        frame = _blob(self.frame, 200, 80, 7, 0.2)
        screen = _screen(frame, self.reference, _params())
        self.assertEqual(len(screen.candidates), 1)
        x, y = screen.candidates[0].centroid_xy
        self.assertAlmostEqual(x, 200, delta=2.0)
        self.assertAlmostEqual(y, 80, delta=2.0)
        self.assertGreater(screen.candidates[0].z, 60.0)
        # full-resolution area of a r = 7 disk is ~154 px; the screening sees it at 2x2 resolution
        self.assertAlmostEqual(screen.candidates[0].area_px, 154, delta=50)

    def test_the_noise_of_a_clean_frame_is_estimated_from_the_frame(self) -> None:
        sigma = _screen(self.frame, self.reference, _params()).sigma
        self.assertTrue(0.001 < sigma < 0.01, sigma)

    def test_a_global_brightness_change_is_cancelled(self) -> None:
        """The lamp or the exposure drifted: every pixel is 30 % darker (or brighter) than in the reference, nothing is new."""
        for gain in (0.7, 1.3):
            self.assertEqual(_screen(self.frame * gain, self.reference, _params()).candidates, (), gain)

    def test_a_blob_is_still_found_in_a_frame_that_is_darker_overall(self) -> None:
        frame = _blob(self.frame * 0.7, 200, 80, 7, 0.2)
        self.assertEqual(len(_screen(frame, self.reference, _params()).candidates), 1)

    def test_a_ghost_of_the_array_does_not_count(self) -> None:
        """The disks are 5 % darker than in the reference (a slow chemical change): that is not a particle."""
        y, x = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
        disks = ((x % 40) - 20) ** 2 + ((y % 40) - 20) ** 2 < 8**2
        ghost = self.frame.copy()
        ghost[disks] *= 0.95
        self.assertEqual(_screen(ghost, self.reference, _params()).candidates, ())

    def test_a_faint_smooth_dip_does_not_count(self) -> None:
        y, x = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
        dip = 1.0 - 0.10 * np.exp(-(((x - 200) / 80.0) ** 2 + ((y - 80) / 60.0) ** 2))
        self.assertEqual(_screen(self.frame * dip, self.reference, _params()).candidates, ())

    def test_a_particle_in_the_reference_is_not_a_detection(self) -> None:
        """A dark object in the *reference* makes the ratio bright there: it can hide an object, never invent one."""
        reference = _blob(self.reference, 200, 80, 7, 0.2)
        self.assertEqual(_screen(self.frame, reference, _params()).candidates, ())

    def test_the_brighter_neighbour_wins_the_confirmation_reference(self) -> None:
        a = np.full((4, 4), 100.0, dtype=np.float32)
        b = a.copy()
        a[1, 1] = 5.0  # a particle in one neighbour
        b[2, 2] = 7.0  # another in the other
        merged = td.confirmation_reference(a, b)
        self.assertEqual(float(merged[1, 1]), 100.0)
        self.assertEqual(float(merged[2, 2]), 100.0)
        self.assertEqual(float(td.confirmation_reference(None, b)[2, 2]), 7.0)
        with self.assertRaises(ValueError):
            td.confirmation_reference(None, None)

    def test_a_large_object_is_global_and_gets_no_contour(self) -> None:
        params = _params(max_particle_area_px=100)
        frame = _blob(self.frame, 200, 80, 20, 0.3)
        screen = _screen(frame, self.reference, params)
        self.assertEqual(td.classify(screen.candidates[0], params), td.KIND_GLOBAL)
        self.assertEqual(td.contour_objects(frame, self.reference, screen, params, (6, 600.0)), [])
        pointers = td.global_events(screen, params, (6, 600.0))
        self.assertEqual(len(pointers), 1)
        self.assertIsNone(pointers[0].contour)

    def test_the_contour_is_halfway_down_the_dip(self) -> None:
        """A black blob and a faint one of the same size get the same edge: the contour follows each object's own depth."""
        params = _params(max_particle_area_px=2000)
        areas = []
        for transmittance in (0.1, 0.5):
            frame = _blob(self.frame, 200, 80, 9, transmittance)
            screen = _screen(frame, self.reference, params)
            events = td.contour_objects(frame, self.reference, screen, params, (6, 600.0))
            self.assertEqual(len(events), 1, transmittance)
            event = events[0]
            self.assertEqual(event.kind, td.KIND_PARTICLE)
            self.assertAlmostEqual(event.depth, 1.0 - transmittance, delta=0.06)
            self.assertAlmostEqual(event.centroid_xy[0], 200, delta=1.5)
            self.assertAlmostEqual(event.area_px, np.pi * 9**2, delta=60)
            self.assertEqual(event.contour.area_px, event.area_px)
            areas.append(event.area_px)
        self.assertAlmostEqual(areas[0], areas[1], delta=60)

    def test_a_contour_is_compact_and_can_be_grown(self) -> None:
        params = _params(max_particle_area_px=2000)
        frame = _blob(self.frame, 200, 80, 7, 0.2)
        event = td.contour_objects(frame, self.reference, _screen(frame, self.reference, params), params, (6, 600.0))[0]
        self.assertLess(event.contour.mask.size, 40 * 40)  # a small box, not the whole plane
        plain = event.contour.to_array((HEIGHT, WIDTH))
        grown = event.contour.to_array((HEIGHT, WIDTH), dilate_px=3)
        self.assertEqual(plain.shape, (HEIGHT, WIDTH))
        self.assertEqual(int(plain.sum()), event.area_px)
        self.assertGreater(int(grown.sum()), int(plain.sum()) + 100)
        self.assertTrue(np.all(grown[plain]))

    def test_binning_keeps_the_mean(self) -> None:
        binned = td.bin_frame(self.frame, 4)
        self.assertEqual(binned.shape, (HEIGHT // 4, WIDTH // 4))
        self.assertAlmostEqual(float(binned.mean()), float(self.frame.mean()), delta=float(self.frame.mean()) * 1e-3)
        self.assertEqual(td.bin_frame(self.frame, 1).dtype, np.float32)


def _event(frame, x, y, area=100, kind=td.KIND_PARTICLE):
    return td.TransientEvent(
        frame=frame, kind=kind, area_px=area, centroid_xy=(x, y), bbox_xyxy=(int(x) - 5, int(y) - 5, int(x) + 5, int(y) + 5), depth=0.8, z=100.0
    )


class LinkingTests(unittest.TestCase):
    ORDER = [(0, 470.0), (0, 480.0), (0, 490.0), (0, 500.0), (1, 470.0), (1, 480.0), (1, 490.0), (1, 500.0)]

    def test_consecutive_frames_that_moved_like_a_particle_share_a_passage_and_a_speed(self) -> None:
        events = [_event(self.ORDER[i], 100 + 190 * i, 300) for i in range(4)]
        linked = td.link_passages(events, self.ORDER, image_shape=(900, 1300))
        self.assertEqual({e.passage_id for e in linked}, {1})
        self.assertTrue(all(e.step_px == 190.0 for e in linked))

    def test_a_passage_runs_across_a_cube_boundary(self) -> None:
        events = [_event(self.ORDER[i], 100 + 190 * (i - 2), 300) for i in (2, 3, 4, 5)]  # cube 0 490, 500 then cube 1 470, 480
        linked = td.link_passages(events, self.ORDER, image_shape=(900, 1300))
        self.assertEqual({e.passage_id for e in linked}, {1})

    def test_two_particles_in_the_same_frames_make_two_passages(self) -> None:
        events = [_event(self.ORDER[i], 100 + 190 * i, 200) for i in range(3)] + [_event(self.ORDER[i], 150 + 190 * i, 700) for i in range(3)]
        linked = td.link_passages(events, self.ORDER, image_shape=(900, 1300))
        self.assertEqual(len({e.passage_id for e in linked}), 2)
        self.assertEqual(len(linked), 6)

    def test_an_object_that_stays_at_one_place_for_three_frames_is_a_particle_at_rest(self) -> None:
        """A particle that arrives and stays (maintainer, 2026-10-18): labelled, kept a particle, so Auto mask masks every frame."""
        events = [_event(self.ORDER[i], 500 + i, 400) for i in range(3)]
        linked = td.link_passages(events, self.ORDER, image_shape=(900, 1300))
        self.assertEqual({e.kind for e in linked}, {td.KIND_PARTICLE})
        self.assertEqual(len({e.passage_id for e in linked}), 1)
        self.assertIsNotNone(linked[0].passage_id)
        self.assertTrue(all(td.is_resting(e) for e in linked))
        self.assertTrue(all(e.step_px == 1.0 for e in linked))  # the shift between frames, not zero for a flowing particle

    def test_a_flowing_particle_is_not_resting(self) -> None:
        events = [_event(self.ORDER[i], 100 + 190 * i, 300) for i in range(4)]
        linked = td.link_passages(events, self.ORDER, image_shape=(900, 1300))
        self.assertFalse(any(td.is_resting(e) for e in linked))
        self.assertFalse(td.is_resting(_event(self.ORDER[0], 5, 5)))  # no passage at all

    def test_a_particle_at_rest_across_a_cube_boundary_is_one_passage(self) -> None:
        events = [_event(self.ORDER[i], 500, 400) for i in (2, 3, 4, 5)]  # the last two wavelengths of cube 0, the first two of cube 1
        linked = td.link_passages(events, self.ORDER, image_shape=(900, 1300))
        self.assertEqual(len({e.passage_id for e in linked}), 1)
        self.assertEqual({e.kind for e in linked}, {td.KIND_PARTICLE})

    def test_a_particle_at_rest_and_a_flowing_one_in_the_same_frames_are_two_passages(self) -> None:
        events = [_event(self.ORDER[i], 500, 400) for i in range(4)] + [_event(self.ORDER[i], 100 + 190 * i, 700) for i in range(4)]
        linked = td.link_passages(events, self.ORDER, image_shape=(900, 1300))
        resting = [e for e in linked if td.is_resting(e)]
        self.assertEqual((len(resting), len({e.passage_id for e in resting}), len({e.passage_id for e in linked})), (4, 1, 2))

    def test_a_particle_that_arrives_flowing_and_then_rests_keeps_its_resting_frames_together(self) -> None:
        xs = [100, 290, 480, 481, 482, 482]
        events = [_event(self.ORDER[i], x, 400) for i, x in enumerate(xs)]
        linked = td.link_passages(events, self.ORDER, image_shape=(900, 1300))
        self.assertEqual({e.kind for e in linked}, {td.KIND_PARTICLE})  # none of it is dropped as an artefact
        self.assertEqual(len({e.passage_id for e in linked[2:]}), 1)  # the frame where it stopped and the frames at rest: one passage
        self.assertTrue(all(td.is_resting(e) for e in linked[2:]))
        self.assertTrue(all(e.passage_id is not None and not td.is_resting(e) for e in linked[:2]))  # the flowing part: its own

    def test_an_object_that_stays_on_the_image_border_is_still_an_artefact(self) -> None:
        """The corners and edges of cube 0 and the top edge of cubes 175-185 of the test dataset: 356 of the 374 stationary objects the first version of the rule called artefacts."""
        events = [_event(self.ORDER[i], 1295, 400) for i in range(3)]
        linked = td.link_passages(events, self.ORDER, image_shape=(900, 1300))
        self.assertEqual({e.kind for e in linked}, {td.KIND_ARTEFACT})
        self.assertEqual({e.passage_id for e in linked}, {None})

    def test_one_member_on_the_border_makes_the_whole_resting_run_the_borders(self) -> None:
        events = [_event(self.ORDER[i], x, 400) for i, x in enumerate([1270, 1281, 1290])]  # drifting towards the edge
        linked = td.link_passages(events, self.ORDER, image_shape=(900, 1300))
        self.assertEqual({e.kind for e in linked}, {td.KIND_ARTEFACT})

    def test_without_the_image_size_nothing_is_an_artefact_because_of_the_border(self) -> None:
        events = [_event(self.ORDER[i], 1295, 400) for i in range(3)]
        self.assertEqual({e.kind for e in td.link_passages(events, self.ORDER)}, {td.KIND_PARTICLE})

    def test_one_coincidence_in_a_dense_stream_is_not_an_artefact(self) -> None:
        """Particle spacing equal to the step: object 2 of frame k+1 lands where object 1 of frame k was. Two frames only."""
        events = [_event(self.ORDER[0], 300, 300), _event(self.ORDER[1], 300, 300, area=400)]
        linked = td.link_passages(events, self.ORDER, image_shape=(900, 1300))
        self.assertEqual({e.kind for e in linked}, {td.KIND_PARTICLE})

    def test_an_object_alone_on_the_image_border_is_an_artefact_but_a_moving_one_is_not(self) -> None:
        lone = [_event(self.ORDER[0], 1295, 400)]
        self.assertEqual(td.link_passages(lone, self.ORDER, image_shape=(900, 1300))[0].kind, td.KIND_ARTEFACT)
        leaving = [_event(self.ORDER[0], 1100, 400), _event(self.ORDER[1], 1295, 400)]
        self.assertEqual({e.kind for e in td.link_passages(leaving, self.ORDER, image_shape=(900, 1300))}, {td.KIND_PARTICLE})

    def test_global_events_pass_through(self) -> None:
        pointer = _event(self.ORDER[0], 600, 400, area=9000, kind=td.KIND_GLOBAL)
        self.assertEqual(td.link_passages([pointer], self.ORDER), [pointer])


class RestingParticleScanTests(unittest.TestCase):
    """A particle that arrives in cube 3 and stays at one place while four wavelengths are taken (maintainer, 2026-10-18): the scan
    finds all four frames and calls them a particle at rest, not an artefact, so the Auto mask step masks every one."""

    CUBES = 6
    WAVELENGTHS = [0.0] + [470.0 + 10 * i for i in range(7)]
    X = {2: 200.0, 3: 201.0, 4: 200.0, 5: 202.0}

    def load_plane(self, cube: int, wavelength: float) -> np.ndarray:
        index = int(round((wavelength - 470.0) / 10)) + 1 if wavelength > 0 else 0
        frame = _field(index, cube, np.random.default_rng(cube * 100 + index))
        x = self.X.get(index) if cube == 3 else None
        return _blob(frame, x, 80, 6, 0.2) if x is not None else frame

    def test_it_is_found_in_every_frame_as_a_particle_at_rest(self) -> None:
        index = {c: list(self.WAVELENGTHS) for c in range(self.CUBES)}
        params = ScanParams(detection=_params(max_particle_area_px=2000), workers=2, stride=1)
        result = scan_transients(index, self.load_plane, params)
        self.assertEqual({e.frame for e in result.events}, {(3, 480.0 + 10 * i) for i in range(4)})
        self.assertEqual({e.kind for e in result.events}, {td.KIND_PARTICLE})
        self.assertEqual(len({e.passage_id for e in result.events}), 1)
        self.assertTrue(all(td.is_resting(e) for e in result.events))
        self.assertTrue(all(e.contour is not None for e in result.events), "a contour is what Auto mask masks")

    def test_a_particle_resting_over_a_cube_boundary_is_found_in_both_cubes_as_one_passage(self) -> None:
        """It arrives in cube 3 at 510 nm and stays for one cube's worth of frames: cube 3 from 510 nm on, cube 4 up to 500 nm."""
        def load(cube: int, wavelength: float) -> np.ndarray:
            index = int(round((wavelength - 470.0) / 10)) + 1 if wavelength > 0 else 0
            frame = _field(index, cube, np.random.default_rng(cube * 100 + index))
            rests = (cube == 3 and index >= 5) or (cube == 4 and index <= 4)
            return _blob(frame, 200, 80, 6, 0.2) if rests and index > 0 else frame

        index = {c: list(self.WAVELENGTHS) for c in range(self.CUBES)}
        result = scan_transients(index, load, ScanParams(detection=_params(max_particle_area_px=2000), workers=2, stride=1))
        self.assertEqual({e.frame for e in result.events}, {(3, 510.0), (3, 520.0), (3, 530.0), (4, 470.0), (4, 480.0), (4, 490.0), (4, 500.0)})
        self.assertEqual({e.kind for e in result.events}, {td.KIND_PARTICLE})
        self.assertEqual(len({e.passage_id for e in result.events}), 1)
        self.assertTrue(all(td.is_resting(e) for e in result.events))

    def test_known_limit_a_particle_resting_for_more_than_one_cube_is_found_only_in_the_first(self) -> None:
        """Every frame is compared with the same wavelength in the PREVIOUS cube, so once the particle is in that frame too it is no
        longer 'new'. Pinned so that nobody is surprised; the way round it (a reference from before it arrived) is a separate
        feature: docs/auto_transient_scan_and_mask_plan_2026-10-16.md section 12."""
        def load(cube: int, wavelength: float) -> np.ndarray:
            index = int(round((wavelength - 470.0) / 10)) + 1 if wavelength > 0 else 0
            frame = _field(index, cube, np.random.default_rng(cube * 100 + index))
            return _blob(frame, 200, 80, 6, 0.2) if cube in (3, 4) and index in (2, 3, 4, 5) else frame

        index = {c: list(self.WAVELENGTHS) for c in range(self.CUBES)}
        result = scan_transients(index, load, ScanParams(detection=_params(max_particle_area_px=2000), workers=2, stride=1))
        self.assertEqual({e.frame[0] for e in result.events}, {3})


class ScanTests(unittest.TestCase):
    """A small dataset: 6 cubes x 8 wavelengths (one dark frame each), a blob crossing the field in cube 3."""

    CUBES = 6
    WAVELENGTHS = [0.0] + [470.0 + 10 * i for i in range(7)]
    BLOB_AT = {3: {2: 40.0, 3: 120.0, 4: 200.0, 5: 280.0}}  # cube -> {wavelength index (1-based without the dark frame): x}

    def setUp(self) -> None:
        self.index = {c: list(self.WAVELENGTHS) for c in range(self.CUBES)}
        self.calls = 0

    def load_plane(self, cube: int, wavelength: float) -> np.ndarray:
        self.calls += 1
        index = int(round((wavelength - 470.0) / 10)) + 1 if wavelength > 0 else 0
        rng = np.random.default_rng(cube * 100 + index)
        frame = _field(index, cube, rng)
        x = self.BLOB_AT.get(cube, {}).get(index)
        return _blob(frame, x, 80, 6, 0.2) if x is not None else frame

    def _params(self, **kwargs) -> ScanParams:
        return ScanParams(detection=_params(max_particle_area_px=2000), workers=2, **kwargs)

    def test_every_frame_of_the_passage_is_found_and_nothing_else(self) -> None:
        result = scan_transients(self.index, self.load_plane, self._params(stride=1))
        self.assertEqual({e.frame for e in result.events}, {(3, 480.0 + 10 * i) for i in range(4)})
        self.assertEqual({e.kind for e in result.events}, {td.KIND_PARTICLE})
        self.assertEqual(len({e.passage_id for e in result.events}), 1)
        self.assertEqual(result.frames_in_range, self.CUBES * 7)  # the dark frame is never scanned
        self.assertEqual(result.failed_frames, [])
        xs = sorted(e.centroid_xy[0] for e in result.events)
        for found, expected in zip(xs, (40, 120, 200, 280)):
            self.assertAlmostEqual(found, expected, delta=2.0)

    def test_a_stride_reads_less_and_the_neighbours_of_a_hit_are_still_found(self) -> None:
        full = scan_transients(self.index, self.load_plane, self._params(stride=1))
        self.calls = 0
        sparse = scan_transients(self.index, self.load_plane, self._params(stride=3))
        self.assertEqual({e.frame for e in sparse.events}, {e.frame for e in full.events})
        self.assertLess(sparse.frames_screened, full.frames_screened)

    def test_without_the_expansion_only_the_checked_frames_are_found(self) -> None:
        result = scan_transients(self.index, self.load_plane, self._params(stride=3, expand=False))
        wavelengths = sorted({e.frame[1] for e in result.events})
        self.assertTrue(0 < len(wavelengths) < 4, wavelengths)

    def _faint_load(self, depths_by_index):
        """A passage whose depth differs from frame to frame: `depths_by_index` = {wavelength index: transmittance}."""

        def load(cube: int, wavelength: float) -> np.ndarray:
            index = int(round((wavelength - 470.0) / 10)) + 1 if wavelength > 0 else 0
            frame = _field(index, cube, np.random.default_rng(cube * 100 + index))
            x = self.BLOB_AT.get(cube, {}).get(index)
            return _blob(frame, x, 80, 6, depths_by_index.get(index, 0.2)) if x is not None else frame

        return load

    def test_gap_filling_finds_the_faint_frames_inside_a_passage(self) -> None:
        load = self._faint_load({3: 0.88, 4: 0.88})  # the middle two frames are 12 % deep
        without = scan_transients(self.index, load, self._params(stride=1, fill_gaps=False))
        self.assertEqual({e.frame[1] for e in without.events}, {480.0, 510.0})
        filled = scan_transients(self.index, load, self._params(stride=1))
        self.assertEqual({e.frame[1] for e in filled.events}, {480.0, 490.0, 500.0, 510.0})
        self.assertEqual({e.frame[1] for e in filled.events if e.inferred}, {490.0, 500.0})
        self.assertEqual(len({e.passage_id for e in filled.events}), 1)

    def test_a_faint_frame_at_a_checked_wavelength_does_not_hide_the_passage_from_a_stride(self) -> None:
        """Stride 3 checks 500 nm: faint there (a 'weak hit'), so its neighbours are looked at and the strong frames are found."""
        load = self._faint_load({4: 0.88})
        result = scan_transients(self.index, load, self._params(stride=3))
        self.assertEqual({e.frame[1] for e in result.events}, {480.0, 490.0, 500.0, 510.0})

    def test_a_passage_that_is_faint_everywhere_makes_no_event(self) -> None:
        load = self._faint_load({2: 0.88, 3: 0.88, 4: 0.88, 5: 0.88})
        self.assertEqual(scan_transients(self.index, load, self._params(stride=1)).events, [])

    def test_the_cube_range_limits_the_scan(self) -> None:
        result = scan_transients(self.index, self.load_plane, self._params(stride=1, cube_range=(0, 2)))
        self.assertEqual(result.events, [])
        self.assertEqual(result.frames_in_range, 3 * 7)

    def test_an_unreadable_frame_is_listed_not_hidden(self) -> None:
        def flaky(cube: int, wavelength: float) -> np.ndarray:
            if (cube, wavelength) == (3, 500.0):
                raise OSError("disk says no")
            return self.load_plane(cube, wavelength)

        result = scan_transients(self.index, flaky, self._params(stride=1))
        self.assertIn((3, 500.0), result.failed_frames)
        self.assertIn((3, 480.0), {e.frame for e in result.events})  # the rest of the scan went on

    def test_cancel_stops_the_scan(self) -> None:
        ticks = []

        def cancelled() -> bool:
            ticks.append(1)
            return len(ticks) > 5

        with self.assertRaises(Cancelled):
            scan_transients(self.index, self.load_plane, self._params(stride=1), cancelled=cancelled)

    def test_progress_runs_from_zero_to_one(self) -> None:
        seen: list[float] = []
        scan_transients(self.index, self.load_plane, self._params(stride=1), progress=lambda f, m: seen.append(f))
        self.assertTrue(seen)
        self.assertEqual(seen, sorted(seen))
        self.assertAlmostEqual(seen[-1], 1.0, delta=1e-6)


class GapTargetTests(unittest.TestCase):
    ORDER = [(0, 470.0 + 10 * i) for i in range(8)]
    SHAPE = (900, 1300)

    def _linked(self, spec):
        """spec: [(position, x, y)] of one particle -> linked events."""
        events = [_event(self.ORDER[i], x, y) for i, x, y in spec]
        return td.link_passages(events, self.ORDER, image_shape=self.SHAPE)

    def test_the_missing_frames_between_two_pieces_of_one_passage_are_targets(self) -> None:
        linked = self._linked([(0, 100, 300), (1, 290, 300), (4, 860, 300)])  # 190 px a frame, frames 2 and 3 missing
        targets = tg.find_gap_targets(linked, self.ORDER, td.DetectionParams(), image_shape=self.SHAPE)
        bridged = {t.frame: t.predicted_xy for t in targets if t.frame in (self.ORDER[2], self.ORDER[3])}
        self.assertEqual(set(bridged), {self.ORDER[2], self.ORDER[3]})
        self.assertAlmostEqual(bridged[self.ORDER[2]][0], 480, delta=1.0)
        self.assertAlmostEqual(bridged[self.ORDER[3]][0], 670, delta=1.0)

    def test_pieces_that_do_not_move_like_one_particle_are_not_bridged(self) -> None:
        params = td.DetectionParams()
        too_far = self._linked([(0, 100, 300), (1, 290, 300), (4, 3000, 300)])  # would need > 450 px a frame
        targets = tg.find_gap_targets(too_far, self.ORDER, params, image_shape=self.SHAPE)
        self.assertNotIn(self.ORDER[3], {t.frame for t in targets})  # frame 2 is only the passage's own extension
        backwards = self._linked([(0, 500, 300), (1, 690, 300), (3, 400, 300)])  # the second piece turns round
        targets = tg.find_gap_targets(backwards, self.ORDER, params, image_shape=self.SHAPE)
        at_frame_two = [t.predicted_xy[0] for t in targets if t.frame == self.ORDER[2]]
        self.assertTrue(all(abs(x - 880) < 1.0 for x in at_frame_two), at_frame_two)  # the extension, not a bridge to x = 545

    def test_a_passage_is_followed_one_frame_beyond_each_end_while_that_is_inside_the_image(self) -> None:
        linked = self._linked([(2, 500, 300), (3, 690, 300), (4, 880, 300)])
        frames = {t.frame: t.predicted_xy for t in tg.find_gap_targets(linked, self.ORDER, td.DetectionParams(), image_shape=self.SHAPE)}
        self.assertEqual(set(frames), {self.ORDER[1], self.ORDER[5]})
        self.assertAlmostEqual(frames[self.ORDER[5]][0], 1070, delta=1.0)
        self.assertAlmostEqual(frames[self.ORDER[1]][0], 310, delta=1.0)
        near_edge = self._linked([(2, 700, 300), (3, 890, 300), (4, 1080, 300)])  # the next spot, 1270, is inside; the one after is not
        self.assertIn(self.ORDER[5], {t.frame for t in tg.find_gap_targets(near_edge, self.ORDER, td.DetectionParams(), image_shape=self.SHAPE)})
        leaving = self._linked([(2, 900, 300), (3, 1090, 300), (4, 1280, 300)])
        self.assertNotIn(self.ORDER[5], {t.frame for t in tg.find_gap_targets(leaving, self.ORDER, td.DetectionParams(), image_shape=self.SHAPE)})

    def test_a_frame_that_already_has_the_particle_is_not_searched_again(self) -> None:
        linked = self._linked([(2, 500, 300), (3, 690, 300), (4, 880, 300)])
        extra = _event(self.ORDER[5], 1075, 305)  # already found there
        targets = tg.find_gap_targets(linked + [extra], self.ORDER, td.DetectionParams(), image_shape=self.SHAPE)
        self.assertNotIn(self.ORDER[5], {t.frame for t in targets})

    def test_a_particle_at_rest_is_followed_one_frame_beyond_each_end_at_the_same_spot(self) -> None:
        """Its first and last frames may be too faint (it is arriving / leaving): the spot is known, so look there at the lower threshold."""
        linked = self._linked([(2, 500, 400), (3, 500, 401), (4, 501, 400)])
        self.assertTrue(all(td.is_resting(e) for e in linked))
        targets = {t.frame: t for t in tg.find_gap_targets(linked, self.ORDER, td.DetectionParams(), image_shape=self.SHAPE)}
        self.assertEqual(set(targets), {self.ORDER[1], self.ORDER[5]})
        for target in targets.values():
            self.assertAlmostEqual(target.predicted_xy[0], 500, delta=2.0)
            self.assertAlmostEqual(target.predicted_xy[1], 400, delta=2.0)
            self.assertAlmostEqual(target.search_px, 40.0, delta=1.0)

    def test_a_lone_event_has_no_velocity_and_is_not_extended(self) -> None:
        self.assertEqual(tg.find_gap_targets(self._linked([(3, 500, 300)]), self.ORDER, td.DetectionParams(), image_shape=self.SHAPE), [])


class FillEventTests(unittest.TestCase):
    def setUp(self) -> None:
        rng = np.random.default_rng(4)
        self.reference = _field(3, 5, rng)
        self.frame = _field(3, 6, rng)
        self.params = _params(max_particle_area_px=2000)

    def _target(self, x=200.0, y=80.0, radius=40.0) -> tg.GapTarget:
        return tg.GapTarget((6, 600.0), (x, y), radius, source_area_px=150)

    def test_a_faint_blob_at_the_predicted_spot_is_found_and_marked_inferred(self) -> None:
        frame = _blob(self.frame, 205, 82, 7, 0.88)  # 12 % deep: below the normal threshold
        self.assertEqual(_screen(frame, self.reference, self.params).candidates, ())
        event = tg.fill_event(frame, self.reference, self._target(), self.params)
        self.assertIsNotNone(event)
        self.assertTrue(event.inferred)
        self.assertAlmostEqual(event.centroid_xy[0], 205, delta=3.0)

    def test_the_same_blob_somewhere_else_is_not_taken(self) -> None:
        frame = _blob(self.frame, 330, 80, 7, 0.88)
        self.assertIsNone(tg.fill_event(frame, self.reference, self._target(), self.params))

    def test_a_ghost_of_the_array_at_the_predicted_spot_is_not_taken(self) -> None:
        y, x = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
        disks = ((x % 40) - 20) ** 2 + ((y % 40) - 20) ** 2 < 8**2
        ghost = self.frame.copy()
        ghost[disks] *= 0.95  # 5 %
        self.assertIsNone(tg.fill_event(ghost, self.reference, self._target(x=220.0, y=100.0), self.params))

    def test_an_object_of_the_wrong_size_is_not_taken(self) -> None:
        frame = _blob(self.frame, 200, 80, 20, 0.88)  # ~1250 px against the 150 px of the passage
        self.assertIsNone(tg.fill_event(frame, self.reference, self._target(), self.params))


class UncachedReadTests(unittest.TestCase):
    """The scan reads thousands of planes once each; it must not push the viewed frames out of the shared 160-plane cache."""

    def test_an_uncached_read_gives_the_same_pixels_and_leaves_the_cache_alone(self) -> None:
        import tempfile
        from pathlib import Path

        import tifffile

        from lspr_imaging_app.dataset import io

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            path = Path(folder) / "plane.tif"
            tifffile.imwrite(str(path), np.arange(60, dtype=np.uint16).reshape(6, 10))
            before = io._load_image_array_cached.cache_info().currsize
            plain = io.load_image_array_uncached(str(path))
            self.assertEqual(io._load_image_array_cached.cache_info().currsize, before)
            cached = io.load_image_array(str(path))
            np.testing.assert_array_equal(plain, cached)
            self.assertEqual(plain.dtype, np.float32)
            self.assertTrue(plain.flags.writeable)  # the caller's own array; the cached one is read-only
            self.assertFalse(cached.flags.writeable)


if __name__ == "__main__":
    unittest.main()
