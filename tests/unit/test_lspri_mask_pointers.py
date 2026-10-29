"""The list of events a Mask-tab scan found: `MaskPointersModule`, its saved shape and the session file (2026-10-17).

Pure module logic, no widgets. What is worth pinning: an event is only a pointer (cosmetic, nothing recomputes), a dismissal
survives a new scan at the same place, a scan of a cube range only replaces the events inside the range, and the list survives a
save and a load (session schema 1.4) while older files without it still open.
Design: apps/LSPRi/eva/docs/auto_transient_scan_and_mask_plan_2026-10-16.md.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.provenance import FrameNamingScheme
    from lspr_imaging_app.image_tools.mask import mask_pointers_state as saved
    from lspr_imaging_app.image_tools.mask.transient_detection import (
        KIND_ARTEFACT,
        KIND_GLOBAL,
        KIND_PARTICLE,
        ContourMask,
        TransientEvent,
    )
    from lspr_imaging_app.image_tools.mask.mask_pointers import MaskPointersModule
    from lspr_imaging_app.image_tools.mask.transient_scan import ScanParams, ScanResult
    from lspr_imaging_app.storage.session import SessionState, load_session, save_session, session_path
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _event(cube, wavelength, x=100.0, y=200.0, kind=KIND_PARTICLE, area=80, passage=None, contour=True, **kwargs):
    mask = np.zeros((6, 9), dtype=bool)
    mask[1:5, 2:8] = True
    return TransientEvent(
        frame=(cube, wavelength), kind=kind, area_px=area, centroid_xy=(x, y), bbox_xyxy=(int(x) - 4, int(y) - 3, int(x) + 5, int(y) + 3),
        depth=0.8, z=120.0, contour=ContourMask(x0=int(x) - 4, y0=int(y) - 3, mask=mask) if contour else None,
        frame_sigma=0.005, passage_id=passage, step_px=190.0 if passage else None, **kwargs,
    )


def _result(events, cube_range=None) -> ScanResult:
    return ScanResult(
        events=list(events), frames_in_range=100, frames_screened=40, frames_hit=len(events), failed_frames=[(1, 500.0)],
        seconds=2.5, stage_seconds={}, params=ScanParams(cube_range=cube_range),
    )


class SavedShapeTests(unittest.TestCase):
    def test_a_list_survives_the_payload_with_its_contours(self) -> None:
        records = (
            saved.MaskPointer(1, _event(3, 620.0, passage=1, inferred=True)),
            saved.MaskPointer(2, _event(3, 630.0, kind=KIND_ARTEFACT), dismissed=True),
            saved.MaskPointer(3, _event(5, 500.0, kind=KIND_GLOBAL, area=9000, contour=False)),
        )
        info = saved.ScanInfo(created_utc="2026-10-17T10:00:00+00:00", settings={"stride": 3, "cube_range": None}, frames_in_range=100, frames_screened=40, seconds=2.5, unreadable_frames=1)
        back = saved.from_payload(json.loads(json.dumps(saved.to_payload(saved.MaskPointersState(info, records)))))
        self.assertEqual(back.info, info)
        self.assertEqual([r.id for r in back.records], [1, 2, 3])
        first = back.records[0].event
        np.testing.assert_array_equal(first.contour.mask, records[0].event.contour.mask)
        self.assertEqual((first.contour.x0, first.contour.y0), (96, 197))
        self.assertTrue(first.inferred)
        self.assertEqual((first.passage_id, first.step_px, first.frame), (1, 190.0, (3, 620.0)))
        self.assertTrue(back.records[1].dismissed)

    def test_only_particles_keep_a_contour_in_the_file(self) -> None:
        records = (saved.MaskPointer(1, _event(3, 620.0)), saved.MaskPointer(2, _event(3, 630.0, kind=KIND_ARTEFACT)))
        back = saved.from_payload(saved.to_payload(saved.MaskPointersState(None, records)))
        self.assertIsNotNone(back.records[0].event.contour)
        self.assertIsNone(back.records[1].event.contour)

    def test_a_missing_or_foreign_block_is_an_empty_list(self) -> None:
        for payload in (None, "nonsense", 5, {}):
            self.assertEqual(saved.from_payload(payload), saved.MaskPointersState())

    def test_an_unreadable_event_is_skipped_and_a_bad_contour_is_dropped(self) -> None:
        payload = saved.to_payload(saved.MaskPointersState(None, (saved.MaskPointer(1, _event(3, 620.0)), saved.MaskPointer(2, _event(4, 500.0)))))
        del payload["events"][0]["cube"]
        payload["events"][1]["contour"] = {"x0": 1, "y0": 1, "shape": [3, 3], "data": "!!!not base64!!!"}
        with self.assertLogs("lspr_imaging_app.image_tools.mask.mask_pointers_state", level="WARNING"):
            back = saved.from_payload(payload)
        self.assertEqual([r.id for r in back.records], [2])
        self.assertIsNone(back.records[0].event.contour)


class ModuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = MaskPointersModule()
        self.reasons: list[str] = []
        self.module.pointers_changed.connect(lambda change: self.reasons.append(change.reason))
        self.module.set_scan_result(
            _result([_event(3, 620.0, x=100, passage=1), _event(3, 630.0, x=290, passage=1), _event(7, 500.0, x=50, kind=KIND_GLOBAL, contour=False)])
        )
        self.reasons.clear()

    def test_a_scan_numbers_the_events_in_acquisition_order_and_says_how_it_was_made(self) -> None:
        self.assertEqual([r.id for r in self.module.records()], [1, 2, 3])
        self.assertEqual([r.event.frame for r in self.module.records()], [(3, 620.0), (3, 630.0), (7, 500.0)])
        info = self.module.info()
        self.assertEqual((info.frames_in_range, info.frames_screened, info.unreadable_frames), (100, 40, 1))
        self.assertEqual(info.settings["sigma_factor"], 60.0)
        self.assertEqual(info.settings["stride"], 3)
        self.assertTrue(info.created_utc)

    def test_the_queries_the_rows_use(self) -> None:
        self.assertEqual(self.module.pointer_cubes(), {3: (2, 0), 7: (1, 0)})
        self.assertEqual(self.module.active_pointer_cubes(), (3, 7))
        self.assertEqual([r.id for r in self.module.records_in_cube(3)], [1, 2])
        self.assertEqual([r.id for r in self.module.records_in_frame((3, 630.0))], [2])
        self.assertEqual(self.module.records_in_cube(99), ())
        self.assertEqual(self.module.counts(), (3, 0))

    def test_dismissing_marks_and_restoring_unmarks_and_both_say_so(self) -> None:
        self.assertEqual(self.module.dismiss([1, 2]), 2)
        self.assertEqual(self.reasons, ["dismissed"])
        self.assertEqual(self.module.active_pointer_cubes(), (7,))
        self.assertEqual(self.module.pointer_cubes()[3], (0, 2))
        self.assertEqual(self.module.dismiss([1]), 0)  # already dismissed: nothing changes, nothing is emitted
        self.assertEqual(self.reasons, ["dismissed"])
        self.assertEqual(self.module.restore([2]), 1)
        self.assertEqual(self.reasons, ["dismissed", "restored"])
        self.assertEqual(self.module.counts(), (2, 1))

    def test_a_new_scan_keeps_a_dismissal_where_the_event_comes_back(self) -> None:
        self.module.dismiss([2])
        self.module.set_scan_result(_result([_event(3, 620.0, x=100), _event(3, 630.0, x=296), _event(3, 640.0, x=480)]))  # 6 px off: the same object
        flags = {r.event.frame[1]: r.dismissed for r in self.module.records()}
        self.assertEqual(flags, {620.0: False, 630.0: True, 640.0: False})
        self.module.set_scan_result(_result([_event(3, 630.0, x=400)]))  # 110 px off: another object
        self.assertFalse(self.module.records()[0].dismissed)

    def test_a_scan_of_a_cube_range_replaces_only_the_events_inside_it(self) -> None:
        self.module.set_scan_result(_result([_event(4, 510.0, x=77)], cube_range=(4, 6)))
        self.assertEqual([r.event.frame for r in self.module.records()], [(3, 620.0), (3, 630.0), (4, 510.0), (7, 500.0)])
        self.module.set_scan_result(_result([], cube_range=(3, 3)))
        self.assertEqual([r.event.frame[0] for r in self.module.records()], [4, 7])
        self.assertEqual([r.id for r in self.module.records()], [1, 2])  # renumbered

    def test_clearing_empties_the_list_once(self) -> None:
        self.module.clear()
        self.assertEqual((self.module.records(), self.module.info(), self.reasons), ((), None, ["cleared"]))
        self.module.clear()
        self.assertEqual(self.reasons, ["cleared"])

    def test_restoring_a_saved_state_replaces_the_list(self) -> None:
        state = self.module.state()
        other = MaskPointersModule()
        other.restore_state(state)
        self.assertEqual(other.state(), state)
        self.assertEqual(other.pointer_cubes(), self.module.pointer_cubes())


class SessionFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.naming = FrameNamingScheme.for_dataset([0, 1], [500.0, 550.0])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _state(self) -> saved.MaskPointersState:
        module = MaskPointersModule()
        module.set_scan_result(_result([_event(3, 620.0, passage=1), _event(3, 630.0, passage=1)]))
        module.dismiss([2])
        return module.state()

    def test_the_events_go_into_the_session_file_and_come_back(self) -> None:
        state = self._state()
        save_session(self.root, SessionState(mask_pointers=state), self.naming)
        payload = json.loads(session_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], "1.6")
        self.assertEqual(len(payload["mask_pointers"]["events"]), 2)
        loaded = load_session(self.root)
        self.assertEqual(loaded.mask_pointers.info, state.info)
        self.assertEqual([(r.id, r.dismissed, r.event.frame) for r in loaded.mask_pointers.records], [(1, False, (3, 620.0)), (2, True, (3, 630.0))])
        np.testing.assert_array_equal(loaded.mask_pointers.records[0].event.contour.mask, state.records[0].event.contour.mask)

    def test_a_schema_1_3_file_without_the_block_opens_with_no_events(self) -> None:
        path = save_session(self.root, SessionState(mask_pointers=self._state()), self.naming)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = "1.3"
        payload.pop("mask_pointers")
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(load_session(self.root).mask_pointers, saved.MaskPointersState())

    def test_resaving_the_same_events_is_byte_identical(self) -> None:
        state = replace(SessionState(), mask_pointers=self._state())
        first = save_session(self.root, state, self.naming).read_bytes()
        self.assertEqual(save_session(self.root, state, self.naming).read_bytes(), first)


if __name__ == "__main__":
    unittest.main()
