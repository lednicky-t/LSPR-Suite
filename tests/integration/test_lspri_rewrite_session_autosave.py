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
import unittest
from pathlib import Path

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths  # noqa: E402

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis import AnalysisSettingsModule
    from lspr_imaging_app.analysis.provenance import FrameNamingScheme
    from lspr_imaging_app.app_rewrite import _build_session_autosave
    from lspr_imaging_app.dataset import DatasetModule
    from lspr_imaging_app.dataset.model import ImageDataset, ImageKey, ImageRecord
    from lspr_imaging_app.image_tools import (
        BackgroundModule,
        ChromaticModule,
        GeometryModule,
        MaskModule,
    )
    from lspr_imaging_app.roi import RoiToolbox
    from lspr_imaging_app.selection import SelectionModule
    from lspr_imaging_app.storage.session import SessionState, session_path
    from lspr_imaging_app.storage.session_autosave import SessionAutosave
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable (not on the `rewrite` branch): {exc}") from exc

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

_NAMING = FrameNamingScheme.for_dataset([0], [500.0])


class RecordingSave:
    """Stands in for `save_session`, recording every call instead of
    writing - what makes "did this save, and against which root" a plain
    assertion rather than a stat() race."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, SessionState]] = []

    def __call__(self, root: Path, state: SessionState, naming: FrameNamingScheme) -> Path:
        self.calls.append((Path(root), state))
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
            save=self.saved,
            interval_ms=60_000,
        )

    def tearDown(self) -> None:
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

        def _failing_then_working(root, state, naming):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("disk full")
            return Path(root)

        self.autosave._save = _failing_then_working
        self.autosave.set_root(self.root)
        self.autosave.schedule()
        with self.assertLogs("lspr_imaging_app.storage.session_autosave", level="ERROR"):
            self.autosave.flush()
        self.autosave.flush()
        self.assertEqual(len(attempts), 2)


def _write_dataset(root: Path) -> ImageDataset:
    path = root / "cube0_wl500.tif"
    tifffile.imwrite(str(path), np.full((16, 20), 1000.0, dtype=np.float32))
    return ImageDataset(
        folder=root,
        records=[ImageRecord(key=ImageKey(wavelength_nm=500.0, spectral_cube_index=0), path=path)],
        source_format="image_stack",
    )


class SessionAutosaveDatasetWiringTest(unittest.TestCase):
    """The end-to-end half: opening a dataset restores its session, and
    editing afterwards writes a real file back to it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.dataset_model = _write_dataset(self.root)
        self._build()

    def tearDown(self) -> None:
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
        self.autosave = _build_session_autosave(
            self.dataset, self.geometry, self.mask, self.chromatic,
            self.background, self.roi_toolbox, self.selection, self.analysis_settings,
        )

    def test_an_edit_is_written_and_comes_back_after_a_restart(self) -> None:
        self.dataset.load_dataset(self.dataset_model)
        self.roi_toolbox.add_roi(8.0, 6.0, sample_radius_px=2.0)
        self.autosave.flush()
        self.assertTrue(session_path(self.root).is_file())

        self._build()
        self.dataset.load_dataset(self.dataset_model)
        restored = self.roi_toolbox.rois()
        self.assertEqual(len(restored), 1)
        self.assertEqual((restored[0].center_x, restored[0].center_y), (8.0, 6.0))

    def test_restoring_a_session_leaves_nothing_to_save(self) -> None:
        """If the restore itself counted as an edit, every dataset open
        would rewrite the file it had just read."""
        self.dataset.load_dataset(self.dataset_model)
        self.roi_toolbox.add_roi(8.0, 6.0, sample_radius_px=2.0)
        self.autosave.flush()
        written_at = session_path(self.root).stat().st_mtime_ns

        self._build()
        self.dataset.load_dataset(self.dataset_model)
        self.autosave.flush()
        self.assertEqual(session_path(self.root).stat().st_mtime_ns, written_at)

    def test_an_unreadable_session_disables_saving_rather_than_overwriting_it(self) -> None:
        """The destructive case. A session file this build cannot parse
        leaves every module at defaults - autosaving that would replace the
        user's real state with an empty one, and the file that could have
        been recovered by hand would be gone."""
        path = session_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_name": "something_else"}), encoding="utf-8")
        original = path.read_text(encoding="utf-8")

        with self.assertLogs("lspr_imaging_app.app_rewrite", level="ERROR"):
            self.dataset.load_dataset(self.dataset_model)
        self.roi_toolbox.add_roi(8.0, 6.0, sample_radius_px=2.0)
        self.autosave.flush()

        self.assertEqual(path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
