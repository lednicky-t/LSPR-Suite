"""`prepare_session` and `SessionWriter`: the session save split in two so it can run off the GUI thread (2026-10-17).

Pure storage logic, no Qt. What is worth pinning: saving an unchanged session reads and writes no mask file (it used to decode,
scan and re-read every mask PNG, 3.7 s for 405 masks, on the GUI thread), a missing or changed mask is still written, the
files on disk are the same as a one-off `save_session` makes, and the prepared copy cannot be changed by later edits.
Design and measurements: apps/LSPRi/eva/docs/session_autosave_blocking_analysis_2026-10-17.md.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from lspr_imaging_app.analysis.provenance import FrameNamingScheme
    from lspr_imaging_app.image_tools.mask.model import MaskChange
    from lspr_imaging_app.roi.model import AreaRoi
    from lspr_imaging_app.storage import session as session_module
    from lspr_imaging_app.storage.session import (
        SessionState,
        SessionWriter,
        load_session,
        prepare_session,
        save_session,
        session_path,
    )
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc

NAMING = FrameNamingScheme(cube_digits=3, wavelength_decimals=0)


def _change(cube: int, wavelength: float = 500.0, scope: str = "individual", seed: int = 0) -> MaskChange:
    plane = np.zeros((24, 30), dtype=bool)
    plane[2 + seed % 10 : 8 + seed % 10, 3 : 12 + cube % 10] = True
    return MaskChange((cube, wavelength), scope, plane)


def _pngs(root: Path) -> list[str]:
    return sorted(p.name for p in (root / "session" / "masks").glob("*.png"))


class SessionWriterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.changes = tuple(_change(cube, seed=cube) for cube in range(1, 6))
        self.state = SessionState(mask_changes=self.changes)
        self.writer = SessionWriter()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, state: SessionState | None = None, root: Path | None = None, writer: SessionWriter | None = None) -> Path:
        return (writer or self.writer).write(root or self.root, prepare_session(state or self.state, NAMING))

    # -- the files ------------------------------------------------------------------------------

    def test_the_first_write_makes_one_png_per_mask_and_the_session_loads_back(self) -> None:
        self.write()
        self.assertEqual(len(_pngs(self.root)), 5)
        self.assertEqual((self.writer.last_stats.written, self.writer.last_stats.reused), (5, 0))
        loaded = load_session(self.root)
        self.assertEqual(loaded.mask_changes, self.changes)

    def test_a_writer_makes_the_same_files_as_a_one_off_save_session(self) -> None:
        other = self.root / "other"
        save_session(other, self.state, NAMING)
        self.write()
        self.assertEqual(_pngs(self.root), _pngs(other))
        self.assertEqual(session_path(self.root).read_bytes(), session_path(other).read_bytes())

    # -- an unchanged session costs no mask work -----------------------------------------------------

    def test_saving_an_unchanged_session_decodes_reads_and_writes_no_mask(self) -> None:
        first = self.write()
        before = session_path(self.root).read_bytes()
        pngs = {name: (self.root / "session" / "masks" / name).stat().st_mtime_ns for name in _pngs(self.root)}
        with mock.patch.object(session_module, "persist_mask_snapshot_with_status") as persist:
            second = self.write()
        persist.assert_not_called()  # the full path (read back, compare, maybe write) was not taken
        self.assertEqual(second, first)
        self.assertEqual((self.writer.last_stats.reused, self.writer.last_stats.written), (5, 0))
        self.assertEqual(session_path(self.root).read_bytes(), before)
        self.assertEqual(pngs, {name: (self.root / "session" / "masks" / name).stat().st_mtime_ns for name in _pngs(self.root)})

    def test_a_new_writer_checks_the_files_instead_of_rewriting_them(self) -> None:
        """The first save after the app starts: the files are on disk but this writer has not seen them yet."""
        self.write()
        stamps = {name: (self.root / "session" / "masks" / name).stat().st_mtime_ns for name in _pngs(self.root)}
        fresh = SessionWriter()
        self.write(writer=fresh)
        self.assertEqual((fresh.last_stats.verified, fresh.last_stats.written, fresh.last_stats.reused), (5, 0, 0))
        self.assertEqual(stamps, {name: (self.root / "session" / "masks" / name).stat().st_mtime_ns for name in _pngs(self.root)})

    def test_one_new_mask_writes_only_that_mask(self) -> None:
        self.write()
        more = SessionState(mask_changes=self.changes + (_change(9, seed=3),))
        self.write(more)
        self.assertEqual((self.writer.last_stats.reused, self.writer.last_stats.written), (5, 1))
        self.assertEqual(len(_pngs(self.root)), 6)

    # -- what must still be noticed ----------------------------------------------------------------------

    def test_a_deleted_png_is_written_again(self) -> None:
        self.write()
        victim = self.root / "session" / "masks" / _pngs(self.root)[0]
        victim.unlink()
        self.write()
        self.assertTrue(victim.is_file())
        self.assertEqual((self.writer.last_stats.written, self.writer.last_stats.reused), (1, 4))

    def test_a_changed_mask_gets_a_new_version_and_the_old_file_stays(self) -> None:
        self.write()
        changed = _change(1, seed=7)  # same frame and scope as the first mask, other pixels
        self.assertNotEqual(changed, self.changes[0])
        self.write(SessionState(mask_changes=(changed,) + self.changes[1:]))
        names = _pngs(self.root)
        self.assertEqual(len(names), 6)
        self.assertEqual(sum(name.endswith("_v2.png") for name in names), 1)
        loaded = load_session(self.root)
        self.assertEqual(loaded.mask_changes[0], changed)

    def test_going_back_to_an_older_mask_reuses_its_version(self) -> None:
        self.write()
        changed = _change(1, seed=7)
        self.write(SessionState(mask_changes=(changed,) + self.changes[1:]))
        self.write()  # the original pixels again: version 1 exists, so no third file
        self.assertEqual(len(_pngs(self.root)), 6)
        self.assertEqual(load_session(self.root).mask_changes, self.changes)

    def test_another_root_starts_from_nothing(self) -> None:
        self.write()
        other = self.root / "other"
        self.write(root=other)
        self.assertEqual((self.writer.last_stats.written, self.writer.last_stats.reused), (5, 0))
        self.assertEqual(len(_pngs(other)), 5)

    def test_a_mask_that_left_the_session_is_forgotten_not_kept_alive(self) -> None:
        self.write()
        self.write(SessionState(mask_changes=self.changes[:2]))
        self.assertEqual(len(self.writer._known), 2)

    # -- the copy -------------------------------------------------------------------------------

    def test_writing_does_not_change_the_prepared_session(self) -> None:
        prepared = prepare_session(self.state, NAMING)
        self.assertEqual(prepared.payload["mask"]["changes"], [])
        self.writer.write(self.root, prepared)
        self.assertEqual(prepared.payload["mask"]["changes"], [])

    def test_edits_after_prepare_session_do_not_reach_the_file(self) -> None:
        """The module queries behind `capture_session` hand out the live ROI objects; the GUI keeps editing them while the
        worker writes. Everything the writer reads must already be a copy."""
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=20.0, sample_diameter_px=6.0, label="a")
        prepared = prepare_session(SessionState(rois=(roi,)), NAMING)
        roi.center_x = 99.0
        roi.label = "edited"
        self.writer.write(self.root, prepared)
        loaded = load_session(self.root)
        self.assertEqual((loaded.rois[0].center_x, loaded.rois[0].label), (10.0, "a"))


if __name__ == "__main__":
    unittest.main()
