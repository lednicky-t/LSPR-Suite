"""Session autosave triggers for the LSPRimaging Evaluation rewrite.

**Only runs on the `apps/LSPRi/eva` submodule's `rewrite` branch** - see
`tests/unit/test_lspri_rewrite_analysis_core.py`'s docstring for why.

`save_session`/`load_session` were built on 2026-09-23 with nothing calling
them. This covers the layer added afterwards: when a save actually happens,
and - more importantly - the three cases where a naive autosave destroys
work rather than preserving it.

`SessionAutosave` is driven here with plain fake callables rather than real
modules wherever possible. The debounce, the dirty flag and the
save-refusing cases are all logic, and testing them through six Qt modules
would only make a failure harder to read; the one test that genuinely needs
the modules (does opening a dataset restore its session?) uses them.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import AnalysisSettingsModule
    from lspr_imaging_app.analysis.provenance import FrameNamingScheme
    from lspr_imaging_app.app_rewrite import _build_session_autosave, _cached_frame_naming, _wire_session_coordinator
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools import (
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
    )
    from lspr_imaging_app.image_tools.mask.transient_detection import KIND_PARTICLE, TransientEvent
    from lspr_imaging_app.image_tools.mask.mask_pointers import MaskPointersModule
    from lspr_imaging_app.image_tools.mask.transient_scan import ScanParams, ScanResult
    from lspr_imaging_app.plan.module import ExperimentPlanModule
    from lspr_imaging_app.plan.state import PlanEdit, PlanEditsState
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.storage import session_index
    from lspr_imaging_app.image_tools.mask.model import MaskChange
    from lspr_imaging_app.storage import session_load_task as session_load_module
    from lspr_imaging_app.storage.session import PreparedSession, SessionState, load_session, save_session, session_path
    from lspr_imaging_app.storage.session_autosave import SessionAutosave
    from lspr_imaging_app.storage.session_coordinator import SessionCoordinator
    from lspr_imaging_app.storage.session_load_task import SessionLoadTask
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

_NAMING = FrameNamingScheme.for_dataset([0], [500.0])


class RecordingSave:
    """Stands in for `SessionWriter.write`, recording every call instead of
    writing - what makes "did this save, and against which root" a plain
    assertion rather than a stat() race. Runs on the autosave's worker thread
    (2026-10-17), so `flush()` / `wait_until_written()` come before any assertion."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, PreparedSession]] = []
        self.threads: list[threading.Thread] = []

    def __call__(self, root: Path, prepared: PreparedSession) -> Path:
        self.threads.append(threading.current_thread())
        self.calls.append((Path(root), prepared))
        return Path(root) / "session" / "session.json"


class SessionAutosaveDebounceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.saved = RecordingSave()
        self.state = SessionState()
        # A 60 s interval, so nothing fires on its own: every test here is
        # about *whether* a save happens, not about waiting for a timer.
        # `flush()` is what the real checkpoints (quit, dataset switch) call
        # anyway, so driving it directly is the same code path.
        self.autosave = SessionAutosave(
            capture=lambda: self.state,
            naming=lambda: _NAMING,
            write=self.saved,
            interval_ms=60_000,
        )

    def tearDown(self) -> None:
        self.autosave.close()
        self._tmp.cleanup()

    def test_nothing_is_written_before_the_debounce_elapses(self) -> None:
        self.autosave.set_root(self.root)
        self.autosave.schedule()
        self.assertEqual(self.saved.calls, [])

    def test_a_flush_writes_the_pending_change(self) -> None:
        self.autosave.set_root(self.root)
        self.autosave.schedule()
        self.autosave.flush()
        self.assertEqual([call[0] for call in self.saved.calls], [self.root])

    def test_many_edits_collapse_into_one_write(self) -> None:
        """The whole point of the debounce: a drag is one save, not forty."""
        self.autosave.set_root(self.root)
        for _ in range(40):
            self.autosave.schedule()
        self.autosave.flush()
        self.assertEqual(len(self.saved.calls), 1)

    def test_flushing_with_nothing_pending_writes_nothing(self) -> None:
        """`flush()` hangs off `aboutToQuit` unconditionally, so quitting
        without touching anything must not rewrite the session file."""
        self.autosave.set_root(self.root)
        self.autosave.flush()
        self.assertEqual(self.saved.calls, [])

    def test_a_second_flush_after_a_save_writes_nothing(self) -> None:
        self.autosave.set_root(self.root)
        self.autosave.schedule()
        self.autosave.flush()
        self.autosave.flush()
        self.assertEqual(len(self.saved.calls), 1)

    def test_scheduling_without_a_dataset_is_a_no_op(self) -> None:
        self.autosave.schedule()
        self.autosave.flush()
        self.assertEqual(self.saved.calls, [])

    def test_switching_datasets_flushes_against_the_old_root(self) -> None:
        """The failure this prevents is not a lost save but a misfiled one:
        a pending edit written after the switch would land in the *new*
        dataset's folder, since the timer carries no root of its own."""
        other = self.root / "other"
        self.autosave.set_root(self.root)
        self.autosave.schedule()
        self.autosave.set_root(other)
        self.autosave.wait_until_written()  # the hand-over is written in the background
        self.assertEqual([call[0] for call in self.saved.calls], [self.root])

    def test_a_restore_does_not_save_itself_back(self) -> None:
        self.autosave.set_root(self.root)
        with self.autosave.suspended():
            self.autosave.schedule()
            self.autosave.schedule()
        self.autosave.flush()
        self.assertEqual(self.saved.calls, [])

    def test_an_edit_after_a_restore_still_saves(self) -> None:
        """`suspended()` must leave the autosave working, not off."""
        self.autosave.set_root(self.root)
        with self.autosave.suspended():
            self.autosave.schedule()
        self.autosave.schedule()
        self.autosave.flush()
        self.assertEqual(len(self.saved.calls), 1)

    def test_a_disabled_root_never_writes(self) -> None:
        """Set when a session file exists but could not be read. The modules
        are then at defaults, which is *not* what that file says - saving
        would overwrite a recoverable file with blank state."""
        self.autosave.set_root(self.root, enabled=False)
        self.autosave.schedule()
        self.autosave.flush()
        self.assertEqual(self.saved.calls, [])

    def test_a_failed_write_keeps_the_change_pending(self) -> None:
        """A failed save must not be recorded as a completed one, or the
        next quit would skip it as "nothing changed"."""
        attempts: list[int] = []

        def _failing_then_working(root, prepared):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("disk full")
            return Path(root)

        self.autosave._write = _failing_then_working
        self.autosave.set_root(self.root)
        self.autosave.schedule()
        with self.assertLogs("lspr_imaging_app.storage.session_autosave", level="ERROR"):
            self.autosave.flush()
        self.autosave.flush()
        self.assertEqual(len(attempts), 2)

    def test_a_failed_write_is_reported_to_the_user_not_only_logged(self) -> None:
        """Disk full / locked file / permissions: the app shows `save_failed` in
        its status bar, so the user does not believe the session is on disk."""
        reported: list[str] = []
        self.autosave.save_failed.connect(reported.append)

        def _always_failing(root, prepared):
            raise OSError("disk full")

        self.autosave._write = _always_failing
        self.autosave.set_root(self.root)
        self.autosave.schedule()
        with self.assertLogs("lspr_imaging_app.storage.session_autosave", level="ERROR"):
            self.autosave.flush()
        self.assertEqual(len(reported), 1)
        self.assertIn("disk full", reported[0])
        self.assertIn("not saved", reported[0])

    def test_a_successful_write_reports_nothing(self) -> None:
        reported: list[str] = []
        self.autosave.save_failed.connect(reported.append)
        self.autosave.set_root(self.root)
        self.autosave.schedule()
        self.autosave.flush()
        self.assertEqual(reported, [])


class CachedFrameNamingTest(unittest.TestCase):
    """The scheme walks every cube (16 ms for 314), so the autosave asks for it once per loaded dataset, not on every save."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dataset_model = _write_dataset(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_it_is_worked_out_again_only_after_a_dataset_is_loaded_or_cleared(self) -> None:
        dataset = DatasetModule()
        naming = _cached_frame_naming(dataset)
        with mock.patch("lspr_imaging_app.app_rewrite._frame_naming", return_value=_NAMING) as worked_out:
            self.assertIs(naming(), _NAMING)
            naming()
            naming()
            self.assertEqual(worked_out.call_count, 1)
            dataset.load_dataset(self.dataset_model)
            naming()
            naming()
            self.assertEqual(worked_out.call_count, 2)
            dataset.clear_dataset()
            naming()
            self.assertEqual(worked_out.call_count, 3)


class GatedWrite:
    """A fake `SessionWriter.write` that can be held open, to show what the GUI thread does while a write runs."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, PreparedSession]] = []
        self.threads: list[threading.Thread] = []
        self.entered = threading.Event()  # a write has started
        self.release = threading.Event()
        self.block = False

    def __call__(self, root: Path, prepared: PreparedSession) -> Path:
        self.entered.set()
        if self.block:
            assert self.release.wait(10.0), "the test never released the write"
        self.threads.append(threading.current_thread())
        self.calls.append((Path(root), prepared))
        return Path(root) / "session" / "session.json"


class SessionAutosaveThreadingTest(unittest.TestCase):
    """The write runs on its own thread (2026-10-17): the GUI thread only reads the modules' state and hands a copy over."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.write = GatedWrite()
        self.state = SessionState()
        self.capture_threads: list[threading.Thread] = []
        self.autosave = SessionAutosave(
            capture=self._capture, naming=lambda: _NAMING, write=self.write, interval_ms=60_000,
        )
        self.autosave.set_root(self.root)

    def _capture(self) -> SessionState:
        self.capture_threads.append(threading.current_thread())
        return self.state

    def tearDown(self) -> None:
        self.write.release.set()
        self.autosave.close()
        self._tmp.cleanup()

    def _edit_and_hand_over(self, cube: int) -> None:
        """What the debounce timer does when it fires, after an edit that left the Cube at *cube*."""
        self.state = SessionState(selected_cube=cube)
        self.autosave.schedule()
        self.autosave._save_now()

    def test_the_state_is_read_on_the_gui_thread_and_written_on_another(self) -> None:
        self.autosave.schedule()
        self.autosave.flush()
        self.assertEqual(self.capture_threads, [threading.current_thread()])
        self.assertEqual(len(self.write.threads), 1)
        self.assertIsNot(self.write.threads[0], threading.current_thread())

    def test_a_slow_write_does_not_block_the_gui_thread(self) -> None:
        self.write.block = True
        began = time.perf_counter()
        self._edit_and_hand_over(1)
        self.assertLess(time.perf_counter() - began, 1.0)  # returned while the write is still held open
        self.assertTrue(self.write.entered.wait(5.0))
        self.assertEqual(self.write.calls, [])  # it has started, it has not finished
        self.write.release.set()
        self.autosave.wait_until_written()
        self.assertEqual(len(self.write.calls), 1)

    def test_edits_made_during_a_write_collapse_into_one_more_write_with_the_newest_state(self) -> None:
        self.write.block = True
        self._edit_and_hand_over(1)
        self.assertTrue(self.write.entered.wait(5.0))
        self._edit_and_hand_over(2)  # waits behind the running write
        self._edit_and_hand_over(3)  # replaces the one waiting: its state includes the older edit
        self.write.release.set()
        self.autosave.wait_until_written()
        cubes = [prepared.payload["selection"]["cube_index"] for _root, prepared in self.write.calls]
        self.assertEqual(cubes, [1, 3])

    def test_a_pending_write_for_the_old_root_survives_a_switch_and_lands_in_the_old_folder(self) -> None:
        other = self.root / "other"
        self.write.block = True
        self._edit_and_hand_over(1)  # running (root)
        self.assertTrue(self.write.entered.wait(5.0))
        self._edit_and_hand_over(2)  # waiting (root)
        self.autosave.set_root(other)  # hands the waiting one over; must not be replaced by anything for `other`
        self._edit_and_hand_over(3)  # waiting (other)
        threading.Timer(0.2, self.write.release.set).start()
        self.autosave.flush()
        self.assertEqual(
            [(call_root, prepared.payload["selection"]["cube_index"]) for call_root, prepared in self.write.calls],
            [(self.root, 1), (self.root, 2), (other, 3)],
        )

    def test_flush_returns_only_when_the_write_has_finished(self) -> None:
        def slow(root, prepared):
            time.sleep(0.3)
            self.write(root, prepared)

        self.autosave._write = slow
        self.autosave.schedule()
        self.autosave.flush()
        self.assertEqual(len(self.write.calls), 1)

    def test_waiting_for_one_root_does_not_wait_for_another(self) -> None:
        other = self.root / "other"
        self.write.block = True
        self._edit_and_hand_over(1)
        self.assertTrue(self.write.entered.wait(5.0))
        began = time.perf_counter()
        self.assertTrue(self.autosave.wait_until_written(other, timeout_s=5.0))  # nothing is being written to `other`
        self.assertLess(time.perf_counter() - began, 1.0)
        with self.assertLogs("lspr_imaging_app.storage.session_autosave", level="WARNING"):
            self.assertFalse(self.autosave.wait_until_written(self.root, timeout_s=0.2))  # this one is still running

    def test_a_failure_in_the_background_reaches_the_gui_thread_by_itself(self) -> None:
        """No flush here: the user keeps working, and the message must still arrive, on the GUI thread, via the event loop."""
        arrived: list[tuple[str, threading.Thread]] = []
        self.autosave.save_failed.connect(lambda message: arrived.append((message, threading.current_thread())))

        def failing(root, prepared):
            raise OSError("disk full")

        self.autosave._write = failing
        self.autosave.schedule()
        with self.assertLogs("lspr_imaging_app.storage.session_autosave", level="ERROR"):
            self.autosave._save_now()
            deadline = time.monotonic() + 5.0
            while not arrived and time.monotonic() < deadline:
                _APP.processEvents()
                time.sleep(0.01)
        self.assertEqual(len(arrived), 1)
        self.assertIn("disk full", arrived[0][0])
        self.assertIs(arrived[0][1], threading.current_thread())
        self.assertTrue(self.autosave._dirty)  # still pending: the next edit or the quit flush tries again


def _write_dataset(root: Path) -> ImageDataset:
    path = root / "cube0_wl500.tif"
    tifffile.imwrite(str(path), np.full((16, 20), 1000.0, dtype=np.float32))
    return ImageDataset(
        folder=root,
        records=[ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)],
        source_format="image_stack",
    )


class SessionAutosaveDatasetWiringTest(unittest.TestCase):
    """The end-to-end half: opening a dataset resolves/creates its active
    session and restores it, and editing afterwards writes a real file back
    to it - now one level deeper than the dataset's own folder, under
    `sessions/<id>/` (2026-09-26), since a dataset can hold several
    independent sessions (`storage/session_index.py`)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.dataset_model = _write_dataset(self.root)
        self._autosaves: list[SessionAutosave] = []
        self._build()

    def tearDown(self) -> None:
        for autosave in self._autosaves:
            autosave.close()
        undo_manager.clear()
        self._tmp.cleanup()

    def _build(self) -> None:
        """A complete, fresh set of modules - what a restart looks like."""
        self.dataset = DatasetModule()
        self.geometry = GeometryModule()
        self.mask = MaskModule()
        self.chromatic = ChromaticModule()
        self.background = BackgroundModule()
        self.roi_toolbox = RoiToolbox()
        self.selection = SelectionModule()
        self.analysis_settings = AnalysisSettingsModule()
        self.events = MaskPointersModule()
        self.plan = ExperimentPlanModule(self.dataset)
        self.coordinator = SessionCoordinator()
        self.session_load = SessionLoadTask()  # reads the session off the GUI thread: `_settle` waits for it
        self.autosave = _build_session_autosave(
            self.dataset, self.geometry, self.mask, self.chromatic,
            self.background, self.roi_toolbox, self.selection, self.analysis_settings, None, self.events, self.plan,
        )
        self._autosaves.append(self.autosave)
        _wire_session_coordinator(
            self.coordinator, self.autosave,
            self.geometry, self.mask, self.chromatic, self.background,
            self.roi_toolbox, self.selection, self.analysis_settings, None, self.events,
            session_load=self.session_load, experiment_plan=self.plan,
        )
        self.dataset.dataset_loaded.connect(lambda ds: self.coordinator.bind_dataset(ds.home))
        self.dataset.dataset_cleared.connect(self.coordinator.unbind)

    def _settle(self, timeout_s: float = 30.0) -> None:
        """Let a session load that is in flight finish and be applied (the result arrives as a queued signal on this thread)."""
        deadline = time.monotonic() + timeout_s
        while self.session_load.is_running() and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.005)
        _APP.processEvents()
        self.assertFalse(self.session_load.is_running(), "the session load did not finish")

    def _active_session_path(self) -> Path:
        active_id = session_index.load_session_index(self.root).active_session_id
        self.assertIsNotNone(active_id, "expected a session to have been created automatically")
        return session_path(session_index.session_dir_for(self.root, active_id))

    def test_an_edit_is_written_and_comes_back_after_a_restart(self) -> None:
        self.dataset.load_dataset(self.dataset_model)
        self.roi_toolbox.add_roi(8.0, 6.0, sample_diameter_px=4.0)
        self.autosave.flush()
        self.assertTrue(self._active_session_path().is_file())

        self._build()
        self.dataset.load_dataset(self.dataset_model)
        self._settle()
        restored = self.roi_toolbox.rois()
        self.assertEqual(len(restored), 1)
        self.assertEqual((restored[0].center_x, restored[0].center_y), (8.0, 6.0))

    def test_a_plan_edit_is_written_and_comes_back_after_a_restart(self) -> None:
        """The plan bar's edits are part of the session: a change announced by the plan module schedules a save, and the next open restores it."""
        edit = PlanEdit(step=1, measured_start_unix_ms=10_000, measured_label="H2O_--_ch1F", label="Water", end_unix_ms=42_000)
        self.dataset.load_dataset(self.dataset_model)
        self.plan.restore_state(PlanEditsState(edits=(edit,)))  # announces `steps_changed` like every command does
        self.autosave.flush()
        self.assertEqual(json.loads(self._active_session_path().read_text(encoding="utf-8"))["plan"]["edits"][0]["label"], "Water")

        self._build()
        self.dataset.load_dataset(self.dataset_model)
        self._settle()
        self.assertEqual(self.plan.state(), PlanEditsState(edits=(edit,)))

    def test_restoring_a_session_leaves_nothing_to_save(self) -> None:
        """If the restore itself counted as an edit, every dataset open
        would rewrite the file it had just read."""
        self.dataset.load_dataset(self.dataset_model)
        self.roi_toolbox.add_roi(8.0, 6.0, sample_diameter_px=4.0)
        self.autosave.flush()
        written_at = self._active_session_path().stat().st_mtime_ns

        self._build()
        self.dataset.load_dataset(self.dataset_model)
        self._settle()
        self.autosave.flush()
        self.assertEqual(self._active_session_path().stat().st_mtime_ns, written_at)

    def test_reopening_the_session_just_edited_waits_for_its_background_write(self) -> None:
        """The flush on a session switch is written in the background. Opening the *same* session again straight away must
        not read the folder before that write has landed: it would restore the previous save (here: no file, so no ROI), and
        the next autosave would then write that over the edit."""
        self.dataset.load_dataset(self.dataset_model)
        self.roi_toolbox.add_roi(8.0, 6.0, sample_diameter_px=4.0)
        real_write = self.autosave._write

        def slow_write(root, prepared):
            time.sleep(0.4)
            return real_write(root, prepared)

        self.autosave._write = slow_write
        self.coordinator.switch_to(self.coordinator.active_session_id())  # no explicit flush
        self._settle()
        self.assertEqual(len(self.roi_toolbox.rois()), 1)

    def test_a_second_session_is_independent_of_the_first(self) -> None:
        """The whole point of sessions: a new one never sees the previous
        one's ROIs, and switching back restores them."""
        self.dataset.load_dataset(self.dataset_model)
        self.roi_toolbox.add_roi(8.0, 6.0, sample_diameter_px=4.0)
        self.autosave.flush()
        first_session_id = self.coordinator.active_session_id()

        self.coordinator.create_new()
        self.assertEqual(self.roi_toolbox.rois(), ())

        self.coordinator.switch_to(first_session_id)
        self._settle()
        restored = self.roi_toolbox.rois()
        self.assertEqual(len(restored), 1)
        self.assertEqual((restored[0].center_x, restored[0].center_y), (8.0, 6.0))

    def _scan_result(self) -> ScanResult:
        events = [
            TransientEvent(
                frame=(0, 500.0), kind=KIND_PARTICLE, area_px=90, centroid_xy=(10.0, 8.0), bbox_xyxy=(5, 4, 15, 12), depth=0.8, z=120.0
            )
        ]
        return ScanResult(
            events=events, frames_in_range=1, frames_screened=1, frames_hit=1, failed_frames=[], seconds=0.1,
            stage_seconds={}, params=ScanParams(),
        )

    def test_the_scan_pointers_are_written_and_come_back_after_a_restart(self) -> None:
        """A scan takes minutes: its pointers (and the user's dismissals) are part of the session, saved by the autosave."""
        self.dataset.load_dataset(self.dataset_model)
        self.events.set_scan_result(self._scan_result())
        self.events.dismiss([1])
        self.autosave.flush()

        self._build()
        self.dataset.load_dataset(self.dataset_model)
        self._settle()
        records = self.events.records()
        self.assertEqual([(r.id, r.event.frame, r.dismissed) for r in records], [(1, (0, 500.0), True)])
        self.assertEqual(self.events.info().settings["stride"], 3)

    def test_a_second_session_has_no_pointers_of_the_first(self) -> None:
        self.dataset.load_dataset(self.dataset_model)
        self.events.set_scan_result(self._scan_result())
        self.autosave.flush()
        first = self.coordinator.active_session_id()
        self.coordinator.create_new()
        self.assertFalse(self.events.has_pointers())
        self.coordinator.switch_to(first)
        self._settle()
        self.assertTrue(self.events.has_pointers())

    def test_an_unreadable_session_disables_saving_rather_than_overwriting_it(self) -> None:
        """The destructive case. A session file this build cannot parse
        leaves every module at defaults - autosaving that would replace the
        user's real state with an empty one, and the file that could have
        been recovered by hand would be gone."""
        self.dataset.load_dataset(self.dataset_model)
        path = self._active_session_path()
        self.dataset.clear_dataset()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_name": "something_else"}), encoding="utf-8")
        original = path.read_text(encoding="utf-8")

        failures: list[str] = []
        self.session_load.failed.connect(failures.append)
        with self.assertLogs("lspr_imaging_app.storage.session_load_task", level="ERROR"):
            self.dataset.load_dataset(self.dataset_model)
            self._settle()
        self.assertEqual(len(failures), 1)  # the user is told (it used to be only a log line)
        self.assertIn("Autosave is off", failures[0])
        self.roi_toolbox.add_roi(8.0, 6.0, sample_diameter_px=4.0)
        self.autosave.flush()

        self.assertEqual(path.read_text(encoding="utf-8"), original)


    def _hold_session_loads(self) -> threading.Event:
        """Make every session load wait for the returned event before it reads (so a test can look at the app *during* a load)."""
        release = threading.Event()
        real = load_session

        def slow(root, progress=None, cancelled=None):
            release.wait(10.0)
            return real(root, progress=progress, cancelled=cancelled)

        patcher = mock.patch.object(session_load_module, "load_session", slow)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(release.set)
        return release

    def test_a_session_loads_in_the_background_and_autosave_stays_off_until_it_lands(self) -> None:
        """The window must not freeze while a big session is read, and the blank state it shows meanwhile must never be written
        over the saved session."""
        self.dataset.load_dataset(self.dataset_model)
        self.roi_toolbox.add_roi(8.0, 6.0, sample_diameter_px=4.0)
        self.autosave.flush()
        path = self._active_session_path()
        written_at = path.stat().st_mtime_ns

        self._build()
        release = self._hold_session_loads()
        self.dataset.load_dataset(self.dataset_model)  # returns while the session is still being read
        self.assertTrue(self.session_load.is_running())
        self.assertEqual(self.roi_toolbox.rois(), ())  # blank for now
        self.roi_toolbox.add_roi(30.0, 20.0, sample_diameter_px=4.0)  # an edit made during the load...
        self.autosave.flush()
        self.assertEqual(path.stat().st_mtime_ns, written_at)  # ...is not saved over the real session

        release.set()
        self._settle()
        restored = self.roi_toolbox.rois()
        self.assertEqual([(r.center_x, r.center_y) for r in restored], [(8.0, 6.0)])  # the saved session is what the user gets
        self.roi_toolbox.add_roi(9.0, 7.0, sample_diameter_px=4.0)
        self.autosave.flush()
        self.assertGreater(path.stat().st_mtime_ns, written_at)  # and autosave is on again

    def test_leaving_a_session_while_it_loads_drops_its_result(self) -> None:
        self.dataset.load_dataset(self.dataset_model)
        self.roi_toolbox.add_roi(8.0, 6.0, sample_diameter_px=4.0)
        self.autosave.flush()
        first = self.coordinator.active_session_id()

        self._build()
        release = self._hold_session_loads()
        self.dataset.load_dataset(self.dataset_model)
        self.assertTrue(self.session_load.is_running())
        self.coordinator.create_new()  # leave the session that is still being read
        release.set()
        self._settle()
        self.assertEqual(self.roi_toolbox.rois(), ())  # the new session is blank; the old one's ROIs did not land in it
        self.assertNotEqual(self.coordinator.active_session_id(), first)

    def test_cancelling_a_session_load_leaves_autosave_off(self) -> None:
        self.dataset.load_dataset(self.dataset_model)
        self.roi_toolbox.add_roi(8.0, 6.0, sample_diameter_px=4.0)
        self.autosave.flush()
        path = self._active_session_path()
        written_at = path.stat().st_mtime_ns

        self._build()
        release = self._hold_session_loads()
        self.dataset.load_dataset(self.dataset_model)
        self.session_load.cancel()  # the status bar's Cancel button
        release.set()
        self._settle()
        self.roi_toolbox.add_roi(30.0, 20.0, sample_diameter_px=4.0)
        self.autosave.flush()
        self.assertEqual(path.stat().st_mtime_ns, written_at)


class SessionLoadTaskTest(unittest.TestCase):
    """The task alone: progress says what is being read, and the result is the session."""

    def test_progress_describes_what_is_being_read(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            masks = tuple(
                MaskChange((0, 500.0 + i), "individual", np.eye(8, dtype=bool)) for i in range(6)
            )
            save_session(root, SessionState(mask_changes=masks), _NAMING)
            task = SessionLoadTask()
            messages: list[str] = []
            finished: list[tuple[str, str]] = []
            loaded: list = []
            task.task_progress.connect(lambda _id, _label, _fraction, message: messages.append(message))
            task.task_finished.connect(lambda _id, outcome, message: finished.append((outcome, message)))
            task.completed.connect(loaded.append)

            task.load(root)
            deadline = time.monotonic() + 30.0
            while task.is_running() and time.monotonic() < deadline:
                _APP.processEvents()
                time.sleep(0.005)
            _APP.processEvents()

            self.assertEqual(messages[0], "Reading the session file")
            self.assertEqual(finished, [("completed", "6 masks, 0 ROIs")])
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].root, root)
            self.assertEqual(len(loaded[0].state.mask_changes), 6)
            self.assertEqual(len(loaded[0].state.mask_versions), 6)

    def test_load_session_reports_each_mask_and_stops_when_cancelled(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            masks = tuple(MaskChange((0, 500.0 + i), "individual", np.eye(8, dtype=bool)) for i in range(5))
            save_session(root, SessionState(mask_changes=masks), _NAMING)
            seen: list[tuple[float, str]] = []
            load_session(root, progress=lambda fraction, message: seen.append((fraction, message)))
            self.assertEqual([m for _f, m in seen if m.startswith("Loading masks:")][-1], "Loading masks: 5 of 5")
            self.assertEqual(sorted(f for f, _m in seen), [f for f, _m in seen])  # never goes backwards

            from lspr_imaging_app.progress import Cancelled

            with self.assertRaises(Cancelled):
                load_session(root, cancelled=lambda: True)


if __name__ == "__main__":
    unittest.main()
