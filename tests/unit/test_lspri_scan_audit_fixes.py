"""Pins for the fixes of the 2026-10-17 audit of the transient scan / Auto mask / sliders
(apps/LSPRi/eva/docs/transient_scan_automask_slider_audit_2026-10-17.md).

Pure numpy, no Qt. What is worth pinning:

- B7: the windowed contour computation gives the same events as the whole-frame computation it replaced, and its depth image the
  same numbers to within two float32 rounding steps (the reference implementation is kept here, unchanged, as the tripwire; a
  numeric change has to be measured, not assumed away).
- B3: the progress throttle lets the last report through; B2: a worker can lower its own priority; B4: the uncached decode is
  the same picture without touching the shared cache; B5: the clash check is one directory listing; B6: the session writer encodes
  the events; B1: the pieces Auto mask needs from a worker (`resolve_mask_change`, `BaseMask`, `materialize_base`).
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app import progress, thread_priority
    from lspr_imaging_app.analysis.provenance import FrameNamingScheme
    from lspr_imaging_app.image_tools.chromatic.warp import warp_boolean_mask_affine
    from lspr_imaging_app.image_tools.mask import model as mask_model
    from lspr_imaging_app.image_tools.mask import transient_detection as td
    from lspr_imaging_app.image_tools.mask.export import existing_clashes
    from lspr_imaging_app.image_tools.mask.model import MaskChange
    from lspr_imaging_app.image_tools.mask.module import MaskModule
    from lspr_imaging_app.image_tools.mask.transient_events_state import EventRecord, TransientEventsState
    from lspr_imaging_app.image_tools.mask.transient_masks import BaseDecoder, BaseMask, frame_mask, materialize_base
    from lspr_imaging_app.storage.session import SessionState, SessionWriter, prepare_session
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc


# -- B7: the windowed contour equals the whole-frame one ------------------------------------------------------------------


def _whole_frame_contour_objects(frame, reference, screen, params, frame_id):
    """`transient_detection.contour_objects` as it was before 2026-10-17: the ratio image and its blur over the WHOLE frame, then
    windows cut out of it. Kept verbatim as the reference the windowed version must reproduce bit for bit."""
    candidates = [c for c in screen.candidates if td.classify(c, params) == td.KIND_PARTICLE]
    if not candidates:
        return []
    height, width = frame.shape
    factor = max(params.bin_factor, 1)
    reference_binned = td.bin_frame(reference, factor)
    valid_binned = td.valid_area(reference_binned, params, factor)
    valid = cv2.resize(valid_binned.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST).astype(bool)
    ratio, _gain = td.ratio_image(frame, reference, valid)
    if params.contour_smooth_px > 0:
        ratio = cv2.GaussianBlur(ratio, (0, 0), params.contour_smooth_px)
    depth = 1.0 - ratio
    core_threshold = max(params.sigma_factor * screen.sigma, 0.05)
    claimed = np.zeros((height, width), dtype=bool)
    events = []
    for candidate in candidates:
        bx0, by0, bx1, by1 = candidate.bbox_xyxy
        margin = params.window_margin_px
        x0, y0 = max(bx0 - margin, 0), max(by0 - margin, 0)
        x1, y1 = min(bx1 + margin, width), min(by1 + margin, height)
        window = depth[y0:y1, x0:x1]
        window_valid = valid[y0:y1, x0:x1]
        core = ((window >= core_threshold) & window_valid).astype(np.uint8)
        count, core_labels, _stats, _ = cv2.connectedComponentsWithStats(core, connectivity=8)
        peaks = [(float(window[core_labels == k].max()), k) for k in range(1, count)]
        for peak, k in sorted(peaks, reverse=True):
            seed = core_labels == k
            if claimed[y0:y1, x0:x1][seed].any():
                continue
            level = params.contour_depth_fraction * peak
            region = ((window >= level) & window_valid).astype(np.uint8)
            _n, region_labels = cv2.connectedComponents(region, connectivity=8)
            wanted = np.unique(region_labels[seed])
            wanted = wanted[wanted > 0]
            if wanted.size == 0:
                continue
            mask = np.isin(region_labels, wanted)
            area = int(mask.sum())
            if area < params.min_area_px:
                continue
            ys, xs = np.nonzero(mask)
            ax0, ax1, ay0, ay1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
            claimed[y0:y1, x0:x1] |= mask
            events.append(
                (area, (float(xs.mean()) + x0, float(ys.mean()) + y0), (x0 + ax0, y0 + ay0, x0 + ax1, y0 + ay1), peak,
                 x0 + ax0, y0 + ay0, mask[ay0:ay1, ax0:ax1].copy())
            )
    return events


def _frame_pair(seed: int, blobs: list[tuple[int, int, int]], height: int = 240, width: int = 360):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    base = np.full((height, width), 20000.0, np.float32)
    base[((xx % 40 - 20) ** 2 + (yy % 40 - 20) ** 2) < 64] *= 0.6
    noise = lambda: (1 + 0.004 * rng.standard_normal((height, width))).astype(np.float32)  # noqa: E731
    reference_a, reference_b, own = base * noise(), base * noise(), base * noise() * np.float32(1.03)
    for cx, cy, radius in blobs:
        own[((xx - cx) ** 2 + (yy - cy) ** 2) <= radius**2] *= 0.3
    return own, td.confirmation_reference(reference_a, reference_b), td.bin_frame(own, 4), td.bin_frame(reference_a, 4)


class WindowedContourTests(unittest.TestCase):
    def test_the_windowed_contour_gives_the_same_events_as_the_whole_frame_one(self) -> None:
        params = td.DetectionParams()
        cases = [
            [(180, 120, 12)],  # in the middle
            [(180, 120, 12), (60, 60, 9)],  # two objects
            [(3, 100, 8)],  # left border
            [(356, 230, 8), (200, 3, 7)],  # right and top border
            [(30, 30, 20), (45, 40, 14)],  # overlapping windows (the second must not be found twice)
            [],  # nothing there
        ]
        compared = 0
        for seed, blobs in enumerate(cases):
            own, reference, binned_own, binned_ref = _frame_pair(seed, blobs)
            screen = td.screen_frame(binned_own, binned_ref, params, (4.0, 4.0))
            expected = _whole_frame_contour_objects(own, reference, screen, params, (1, 500.0))
            found = td.contour_objects(own, reference, screen, params, (1, 500.0))
            self.assertEqual(len(found), len(expected), f"case {seed}")
            for event, (area, centroid, bbox, depth, x0, y0, contour) in zip(found, expected):
                self.assertEqual(
                    (event.area_px, event.centroid_xy, event.bbox_xyxy, event.depth), (area, centroid, bbox, depth), f"case {seed}"
                )
                self.assertEqual((event.contour.x0, event.contour.y0), (x0, y0))
                np.testing.assert_array_equal(event.contour.mask, contour)
                compared += 1
        self.assertGreaterEqual(compared, 5, "the cases must really find objects, or this pins nothing")

    def test_the_gain_from_the_one_in_two_grid_is_the_gain_of_the_whole_ratio(self) -> None:
        own, reference, _bo, _br = _frame_pair(5, [(100, 100, 10)])
        valid = np.ones(own.shape, dtype=bool)
        valid[:30] = False
        self.assertEqual(td.ratio_gain(own, reference, valid), td.ratio_image(own, reference, valid)[1])

    def test_the_depth_of_a_window_is_the_same_slice_of_the_whole_frame_depth(self) -> None:
        """The invariant itself, on every pixel of the window (the events above only look at the middle of it): inside, at the
        image border, in a corner, and with a window so small that the blur reaches far outside it."""
        own, reference, _bo, _br = _frame_pair(9, [(150, 100, 10)], height=120, width=200)
        valid = np.ones(own.shape, dtype=bool)
        ratio, gain = td.ratio_image(own, reference, valid)
        for sigma in (1.0, 2.5):
            whole = 1.0 - cv2.GaussianBlur(ratio, (0, 0), sigma)
            for x0, y0, x1, y1 in [(60, 40, 120, 90), (0, 0, 40, 30), (160, 90, 200, 120), (0, 50, 25, 80), (100, 0, 140, 20), (90, 60, 93, 63)]:
                # Not bit for bit: OpenCV's vector code sums the blur's terms in a different order at a different position in
                # the row, so ~0.6 % of the pixels differ by one float32 step (1.2e-7 near 1). The tolerance is two steps; a window
                # that is too small (the blur reflecting at the window edge instead of seeing the real neighbours) is off by far more.
                np.testing.assert_allclose(
                    td.depth_window(own, reference, gain, sigma, (x0, y0, x1, y1)), whole[y0:y1, x0:x1], rtol=0, atol=2.5e-7
                )

    def test_the_window_reaches_as_far_as_the_blur_does(self) -> None:
        for sigma in (0.5, 1.0, 1.5, 3.0):
            image = np.zeros((41, 41), np.float32)
            image[20, 20] = 1.0
            blurred = cv2.GaussianBlur(image, (0, 0), sigma)
            reach = td._blur_reach(sigma)
            rows, cols = np.nonzero(blurred)
            self.assertLessEqual(int(max(abs(rows - 20).max(), abs(cols - 20).max())), reach - 1, f"sigma {sigma}")


# -- B3, B2 ------------------------------------------------------------------------------------------------------------


class ProgressThrottleTests(unittest.TestCase):
    def test_reports_closer_than_the_interval_are_dropped_but_the_end_always_goes_through(self) -> None:
        got: list[tuple[float, str]] = []
        with mock.patch.object(progress.time, "monotonic", side_effect=[0.0, 0.01, 0.02, 0.07, 0.08, 0.081]):
            forward = progress.throttled(lambda fraction, message: got.append((fraction, message)), 0.05)
            forward(0.1, "first")  # t=0.00: goes
            forward(0.2, "dropped")  # t=0.01
            forward(0.3, "dropped too")  # t=0.02
            forward(0.4, "late enough")  # t=0.07: goes
            forward(0.5, "dropped")  # t=0.08
            forward(1.0, "done")  # t=0.081: goes although it is early, it completes the bar
        self.assertEqual([m for _f, m in got], ["first", "late enough", "done"])

    def test_the_cancel_check_is_not_behind_the_throttle(self) -> None:
        stage = progress.StageProgress({"a": 1.0}, progress.throttled(lambda _f, _m: None, 3600.0), lambda: True)
        with self.assertRaises(progress.Cancelled):
            stage("a", 0.5)


class ThreadPriorityTests(unittest.TestCase):
    def test_a_worker_can_lower_its_own_priority_and_the_caller_keeps_its_own(self) -> None:
        if sys.platform != "win32":
            self.assertFalse(thread_priority.lower_current_thread_priority())
            return
        before = thread_priority.current_thread_priority()
        seen: dict[str, object] = {}

        def worker() -> None:
            seen["set"] = thread_priority.lower_current_thread_priority()
            seen["priority"] = thread_priority.current_thread_priority()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(5.0)
        self.assertTrue(seen["set"])
        self.assertEqual(seen["priority"], thread_priority.THREAD_PRIORITY_BELOW_NORMAL)
        self.assertEqual(thread_priority.current_thread_priority(), before)

    def test_a_failing_call_is_a_false_not_an_exception(self) -> None:
        with mock.patch.object(sys, "platform", "win32"), mock.patch("ctypes.WinDLL", side_effect=OSError("no"), create=True):
            self.assertFalse(thread_priority.lower_current_thread_priority())


# -- B4, B5, B6 --------------------------------------------------------------------------------------------------------


def _plane(seed: int, shape=(60, 80)) -> np.ndarray:
    return np.random.default_rng(seed).random(shape) > 0.6


class UncachedDecodeTests(unittest.TestCase):
    def test_the_uncached_plane_is_the_same_picture_and_leaves_the_shared_cache_alone(self) -> None:
        plane = _plane(1)
        change = MaskChange((1, 500.0), "individual", plane)
        mask_model._decode_mask.cache_clear()
        fresh = change.plane_uncached()
        np.testing.assert_array_equal(fresh, plane)
        self.assertEqual(mask_model._decode_mask.cache_info().currsize, 0, "the shared cache was not used")
        self.assertTrue(fresh.flags.writeable)
        fresh[0, 0] = not fresh[0, 0]  # the caller's own: changing it changes nothing stored
        np.testing.assert_array_equal(change.plane_uncached(), plane)
        np.testing.assert_array_equal(change.mask, plane)
        self.assertEqual(mask_model._decode_mask.cache_info().currsize, 1)
        self.assertFalse(change.mask.flags.writeable)


class ExistingClashesTests(unittest.TestCase):
    def test_the_names_that_are_already_there_in_the_order_given(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            for name in ("b.png", "masks_index.csv"):
                (Path(tmp) / name).write_bytes(b"x")
            self.assertEqual(existing_clashes(Path(tmp), ["a.png", "b.png", "masks_index.csv"]), ["b.png", "masks_index.csv"])
            self.assertEqual(existing_clashes(Path(tmp), ["a.png"]), [])

    def test_a_folder_that_is_not_there_holds_nothing(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            self.assertEqual(existing_clashes(Path(tmp) / "not_yet", ["a.png"]), [])

    def test_it_lists_the_folder_once_instead_of_asking_for_every_file(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            with mock.patch.object(Path, "exists", side_effect=AssertionError("one stat per file")):
                existing_clashes(Path(tmp), [f"m{i}.png" for i in range(50)])


class SessionWriterEventsTests(unittest.TestCase):
    def test_the_writer_encodes_the_events_the_prepared_session_only_carries_them(self) -> None:
        mask = np.zeros((10, 12), dtype=bool)
        mask[2:6, 3:8] = True
        event = td.TransientEvent(
            frame=(3, 500.0), kind=td.KIND_PARTICLE, area_px=20, centroid_xy=(5.0, 4.0), bbox_xyxy=(3, 2, 8, 6),
            depth=0.8, z=100.0, contour=td.ContourMask(x0=3, y0=2, mask=mask[2:6, 3:8].copy()),
        )
        state = SessionState(transient_events=TransientEventsState(records=(EventRecord(id=1, event=event),)))
        prepared = prepare_session(state, FrameNamingScheme(cube_digits=3, wavelength_decimals=0))
        self.assertIsNone(prepared.payload["transient_events"], "nothing is encoded on the calling (GUI) thread")
        self.assertEqual(prepared.transient_events, state.transient_events)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = SessionWriter().write(Path(tmp), prepared)
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["transient_events"]["events"]), 1)
        self.assertEqual(saved["transient_events"]["events"][0]["cube"], 3)
        keys = list(saved)
        self.assertLess(keys.index("frames"), keys.index("transient_events"))
        self.assertLess(keys.index("transient_events"), keys.index("selection"))


# -- B1: the pieces Auto mask needs from a worker ----------------------------------------------------------------------------


class ResolveMaskChangeTests(unittest.TestCase):
    def test_it_is_the_rule_of_resolve_mask_source_without_the_decoding(self) -> None:
        module = MaskModule()
        module.set_mask_change((2, 500.0), "persistent", _plane(1))
        module.set_mask_change((5, 500.0), "persistent", _plane(2))
        module.set_mask_change((6, 550.0), "individual", _plane(3))
        for frame in [(0, 500.0), (2, 500.0), (4, 550.0), (5, 600.0), (6, 550.0), (6, 500.0), (9, 500.0)]:
            change = module.resolve_mask_change(frame)
            source = module.resolve_mask_source(frame)
            if source is None:
                self.assertIsNone(change, frame)
                continue
            self.assertEqual((change.frame, change.scope), (source[0], source[2]), frame)
            np.testing.assert_array_equal(change.mask, source[1])


class MaterializeBaseTests(unittest.TestCase):
    def test_nothing_in_effect_is_none(self) -> None:
        self.assertIsNone(materialize_base(None, warp_boolean_mask_affine))

    def test_an_unwarped_base_is_a_private_copy(self) -> None:
        change = MaskChange((1, 500.0), "persistent", _plane(1))
        base = materialize_base(BaseMask(change), warp_boolean_mask_affine)
        np.testing.assert_array_equal(base, change.mask)
        base[:] = False
        np.testing.assert_array_equal(change.mask, _plane(1))

    def test_a_warped_base_is_the_chromatic_warp_of_the_mask(self) -> None:
        plane = _plane(2)
        matrix = np.array([[1.0, 0.0, 5.0], [0.0, 1.0, -3.0]])
        base = materialize_base(BaseMask(MaskChange((1, 500.0), "persistent", plane), matrix), warp_boolean_mask_affine)
        np.testing.assert_array_equal(base, warp_boolean_mask_affine(plane, matrix))

    def test_the_identity_matrix_would_have_changed_nothing_which_is_why_it_is_skipped(self) -> None:
        plane = _plane(3, (90, 130))
        np.testing.assert_array_equal(warp_boolean_mask_affine(plane, np.eye(2, 3)), plane)

    def test_the_decoder_decodes_a_shared_mask_once(self) -> None:
        change = MaskChange((1, 500.0), "persistent", _plane(4))
        decoder = BaseDecoder()
        with mock.patch.object(MaskChange, "plane_uncached", autospec=True, side_effect=MaskChange.plane_uncached) as spy:
            for _ in range(5):
                materialize_base(BaseMask(change), warp_boolean_mask_affine, decoder)
        self.assertEqual(spy.call_count, 1)

    def test_frame_mask_can_take_over_an_array_without_copying_it(self) -> None:
        base = np.zeros((20, 30), dtype=bool)
        self.assertIs(frame_mask([], (20, 30), 0, base, copy_base=False), base)
        self.assertIsNot(frame_mask([], (20, 30), 0, base), base)


if __name__ == "__main__":
    unittest.main()
