"""The snapshot's frame-table cache is dropped by every change that can alter its tables (2026-11-28; `analysis/scan_jobs.py` `FrameTableCache`).

Real modules from `app_rewrite._build_modules()` (no window, no dataset). Each signal the app wires to the cache is emitted, and the cache must be empty
afterwards; two real commands (a mask change, an ignored cube) are driven through their public methods to pin that the modules emit them. Also pinned:
`analysis_scan_source` hands the app's cache to the source, so every consumer of the snapshot gets it. A new input of the per-frame tables needs its signal
added in `_build_modules` and a line here.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PyQt6 import QtWidgets

from tests._paths import REPO_ROOT, ensure_repo_paths, skip_if_lspri_rewrite_absent

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

try:
    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.analysis.scan_jobs import FrameTableCache, _CubeTable
    from lspr_imaging_app.image_tools.chromatic.model import ChromaticModelChange
    from lspr_imaging_app.image_tools.mask.model import MaskComputationalChange
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    skip_if_lspri_rewrite_absent(exc)
    raise


def _filled(cache: FrameTableCache) -> None:
    cache.keep(0, _CubeTable((500.0,), {(0, 500.0): True}))
    assert len(cache) == 1


class FrameCacheWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.m = app_rewrite._build_modules()
        self.m.session_autosave._suspended = True  # nothing here may write a session
        self.cache = self.m.frame_cache

    def test_the_source_of_the_app_carries_the_cache(self) -> None:
        self.assertIsInstance(self.cache, FrameTableCache)
        with mock.patch.object(self.m.dataset, "record_map", return_value={}):  # no dataset is loaded here
            self.assertIs(app_rewrite.analysis_scan_source(self.m).frame_cache, self.cache)

    def _emptied_by(self, name: str, emit) -> None:
        _filled(self.cache)
        emit()
        self.assertEqual(len(self.cache), 0, f"{name} must drop the frame tables")

    def test_every_wired_signal_drops_the_tables(self) -> None:
        m = self.m
        with mock.patch.object(m.session_coordinator, "bind_dataset"):  # a loaded dataset binds the session; not what is pinned here
            self._emptied_by("dataset_loaded", lambda: m.dataset.dataset_loaded.emit(types.SimpleNamespace(home=Path("."))))
        self._emptied_by("dataset_cleared", m.dataset.dataset_cleared.emit)
        self._emptied_by("active_session_changed", lambda: m.session_coordinator.active_session_changed.emit(Path(".")))
        self._emptied_by("roles_changed", m.frame_roles.roles_changed.emit)
        self._emptied_by("reference_frame_changed", m.reference_frame.reference_frame_changed.emit)
        self._emptied_by("chromatic_model_changed", lambda: m.chromatic.chromatic_model_changed.emit(ChromaticModelChange(reason="refit")))
        self._emptied_by("mask_changed", lambda: m.mask.mask_changed.emit(MaskComputationalChange(reason="mask_change", frame=None, scope=None)))

    def test_a_real_mask_change_drops_the_tables(self) -> None:
        self._emptied_by("set_mask_change", lambda: self.m.mask.set_mask_change((0, 500.0), "individual", np.ones((4, 4), dtype=bool)))

    def test_a_real_ignored_cube_drops_the_tables(self) -> None:
        self._emptied_by("set_cube_ignored", lambda: self.m.frame_roles.set_cube_ignored(1, True))

    def test_the_tables_are_dropped_before_the_staleness_check_can_read_them(self) -> None:
        """The cache is connected when the modules are built, before any panel or the analysis tab connects its own slots, so on a signal it is dropped
        first (Qt calls slots in connection order): a slot that snapshots at once never sees the old tables."""
        _filled(self.cache)
        seen: list[int] = []
        self.m.mask.mask_changed.connect(lambda _change: seen.append(len(self.cache)))
        self.m.mask.mask_changed.emit(MaskComputationalChange(reason="mask_change", frame=None, scope=None))
        self.assertEqual(seen, [0])


if __name__ == "__main__":
    unittest.main()
