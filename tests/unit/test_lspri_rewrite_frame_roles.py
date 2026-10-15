"""Tests for `FrameRolesModule` (dark / ignored / reference roles) and its saved state.

Needs a QApplication only because the module is a QObject; no dataset files.
"""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

try:
    from PyQt6.QtCore import QObject, pyqtSignal
    from lspr_imaging_app.selection.frame_roles_module import (
        ROLE_DARK, ROLE_IGNORED, ROLE_NORMAL, ROLE_REFERENCE, FrameRolesModule,
    )
    from lspr_imaging_app.selection.frame_roles_state import FrameRolesState
    from lspr_imaging_app.selection.reference_frame_module import ReferenceFrameModule
    from lspr_imaging_app.undo import undo_manager
except ImportError as exc:  # pragma: no cover - depends on the checked-out branch
    raise unittest.SkipTest(f"LSPRi rewrite modules unavailable: {exc}") from exc


class _FakeDataset(QObject):
    dataset_cleared = pyqtSignal()

    def __init__(self, wavelengths) -> None:
        super().__init__()
        self._wavelengths = tuple(wavelengths)

    def wavelengths(self):
        return self._wavelengths


def _make(wavelengths=(0.0, 500.0, 550.0, 600.0)):
    dataset = _FakeDataset(wavelengths)
    reference = ReferenceFrameModule()
    roles = FrameRolesModule(dataset, reference)
    roles.restore_state(FrameRolesState())  # what a session load does
    undo_manager.clear()
    return dataset, reference, roles


class RoleOfTest(unittest.TestCase):
    def test_zero_nm_is_auto_assigned_as_dark(self) -> None:
        _ds, _ref, roles = _make()
        self.assertEqual(roles.dark_wavelength(), 0.0)
        self.assertEqual(roles.role_of(0, 0.0), ROLE_DARK)
        self.assertEqual(roles.role_of(3, 500.0), ROLE_NORMAL)

    def test_no_dark_without_a_zero_nm_frame(self) -> None:
        _ds, _ref, roles = _make((500.0, 600.0))
        self.assertIsNone(roles.dark_wavelength())

    def test_reference_role_follows_manual_and_auto(self) -> None:
        _ds, ref, roles = _make()
        ref.set_manual_frame(2, 550.0)
        self.assertEqual(roles.role_of(2, 550.0), ROLE_REFERENCE)
        self.assertEqual(roles.role_of(1, 550.0), ROLE_NORMAL)  # manual is one frame
        ref.set_mode("auto")
        ref.set_auto_wavelength(1, 600.0)
        self.assertEqual(roles.role_of(1, 600.0), ROLE_REFERENCE)
        self.assertEqual(roles.role_of(2, 600.0), ROLE_NORMAL)

    def test_ignored_wavelength_and_cube(self) -> None:
        _ds, _ref, roles = _make()
        roles.set_wavelength_ignored(550.0, True)
        roles.set_cube_ignored(4, True)
        self.assertEqual(roles.role_of(0, 550.0), ROLE_IGNORED)
        self.assertEqual(roles.role_of(4, 500.0), ROLE_IGNORED)
        self.assertEqual(roles.role_of(4, 0.0), ROLE_IGNORED)  # an ignored cube covers its dark too
        self.assertFalse(roles.is_spectral(4, 500.0))
        self.assertTrue(roles.is_spectral(0, 500.0))
        self.assertFalse(roles.is_spectral(0, 0.0))  # dark is not a spectral point


class CommandsTest(unittest.TestCase):
    def test_roles_stay_exclusive(self) -> None:
        _ds, _ref, roles = _make()
        roles.set_wavelength_ignored(550.0, True)
        roles.set_dark(550.0)
        self.assertEqual(roles.role_of(0, 550.0), ROLE_DARK)
        self.assertEqual(roles.ignored_wavelengths(), ())
        roles.set_wavelength_ignored(550.0, True)
        self.assertIsNone(roles.dark_wavelength())

    def test_dark_must_exist_in_dataset(self) -> None:
        _ds, _ref, roles = _make()
        with self.assertRaises(ValueError):
            roles.set_dark(123.0)

    def test_undo_redo_and_noop_pushes_nothing(self) -> None:
        _ds, _ref, roles = _make()
        roles.set_cube_ignored(1, False)  # already not ignored
        self.assertFalse(undo_manager.can_undo)
        roles.set_cube_ignored(1, True)
        undo_manager.undo()
        self.assertEqual(roles.ignored_cubes(), ())
        undo_manager.redo()
        self.assertEqual(roles.ignored_cubes(), (1,))

    def test_cleared_dark_is_not_auto_assigned_again(self) -> None:
        _ds, _ref, roles = _make()
        roles.clear_dark()
        saved = roles.state()
        self.assertTrue(saved.dark_user_set)
        roles.restore_state(saved)
        self.assertIsNone(roles.dark_wavelength())

    def test_changes_emit(self) -> None:
        _ds, _ref, roles = _make()
        seen: list[int] = []
        roles.roles_changed.connect(lambda: seen.append(1))
        roles.set_cube_ignored(2, True)
        self.assertEqual(len(seen), 1)


class StateRoundTripTest(unittest.TestCase):
    def test_state_round_trips_including_reference(self) -> None:
        _ds, ref, roles = _make()
        roles.set_dark(500.0)
        roles.set_wavelength_ignored(600.0, True)
        roles.set_cube_ignored(3, True)
        ref.set_manual_frame(2, 550.0)
        state = roles.state()
        _ds2, ref2, roles2 = _make()
        roles2.restore_state(state)
        self.assertEqual(roles2.state(), state)
        self.assertEqual(ref2.manual_frame(), (2, 550.0))
        self.assertEqual(roles2.role_of(2, 550.0), ROLE_REFERENCE)

    def test_manual_frame_kept_while_mode_is_auto(self) -> None:
        _ds, ref, roles = _make()
        ref.set_manual_frame(2, 550.0)
        ref.set_mode("auto")
        _ds2, ref2, roles2 = _make()
        roles2.restore_state(roles.state())
        self.assertEqual(ref2.mode(), "auto")
        self.assertEqual(ref2.manual_frame(), (2, 550.0))

    def test_payload_decoding_is_forgiving(self) -> None:
        self.assertEqual(FrameRolesState.from_payload(None), FrameRolesState())
        self.assertEqual(FrameRolesState.from_payload({"ignored_cubes": "x"}), FrameRolesState())
        decoded = FrameRolesState.from_payload({"ignored_cubes": [2, 1], "reference_mode": "bogus"})
        self.assertEqual(decoded.ignored_cubes, (1, 2))
        self.assertEqual(decoded.reference_mode, "auto")

    def test_dataset_cleared_resets_everything(self) -> None:
        ds, ref, roles = _make()
        roles.set_cube_ignored(1, True)
        ref.set_manual_frame(1, 500.0)
        ds.dataset_cleared.emit()
        self.assertEqual(roles.state(), FrameRolesState())


class SessionFileTest(unittest.TestCase):
    def test_frames_block_round_trips_and_old_files_get_defaults(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from lspr_imaging_app.analysis.provenance import FrameNamingScheme
        from lspr_imaging_app.storage.session import SessionState, load_session, save_session, session_path

        state = SessionState(frame_roles=FrameRolesState(
            dark_wavelength_nm=None, dark_user_set=True, ignored_wavelengths=(550.0,), ignored_cubes=(2,),
            reference_mode="manual", reference_cube=1, reference_wavelength_nm=600.0,
        ))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_session(root, state, FrameNamingScheme(cube_digits=1, wavelength_decimals=0))
            self.assertEqual(load_session(root).frame_roles, state.frame_roles)
            payload = json.loads(session_path(root).read_text(encoding="utf-8"))
            del payload["frames"]  # a file written before roles existed
            session_path(root).write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(load_session(root).frame_roles, FrameRolesState())


class _FakeSelection:
    def __init__(self, wavelength: float) -> None:
        self.wavelength = wavelength

    def current_wavelength(self) -> float:
        return self.wavelength


class WorkflowRowsAndLabelTest(unittest.TestCase):
    def test_rows_show_dark_and_ignored_summary_and_pick_clear(self) -> None:
        from lspr_imaging_app.panels.workflow.frame_roles_rows import FrameRolesRows

        _ds, _ref, roles = _make()
        selection = _FakeSelection(550.0)
        rows = FrameRolesRows(roles, selection)
        self.assertEqual(rows._dark_label.text(), "[Dark: 0 nm - unscaled]")
        self.assertEqual(rows._ignored_label.text(), "[Ignored: none]")
        rows._pick_button.click()
        self.assertEqual(rows._dark_label.text(), "[Dark: 550 nm - unscaled]")
        roles.set_wavelength_ignored(600.0, True)
        roles.set_cube_ignored(1, True)
        roles.set_cube_ignored(2, True)
        self.assertEqual(rows._ignored_label.text(), "[Ignored: 1 wavelength, 2 cubes]")
        rows._clear_button.click()
        self.assertEqual(rows._dark_label.text(), "[Dark: -]")
        self.assertFalse(rows._clear_button.isEnabled())
        selection.wavelength = 123.0  # not in the dataset: refused, and said so
        rows._pick_button.click()
        self.assertIn("not a wavelength", rows._dark_label.text())

    def test_dock_label_prefix_for_dark_reference_and_ignored_in_the_slider_colours(self) -> None:
        from lspr_imaging_app.panels.image.panel import ImagePanel
        from lspr_imaging_app.panels.image.slider_colors import SliderColors

        _ds, ref, roles = _make()
        roles.set_cube_ignored(3, True)
        ref.set_manual_frame(2, 550.0)
        colors = SliderColors()
        colors.set("dark", "#112233")
        fake = SimpleNamespace(_frame_roles=roles, _slider_colors=colors)
        prefix = ImagePanel._with_role_prefix
        self.assertEqual(prefix(fake, "Cube 0, 500 nm | N/A", 0, 500.0), "Cube 0, 500 nm | N/A")
        dark = prefix(fake, "Cube 0, 0 nm | a<b", 0, 0.0)
        self.assertIn('<span style="color:#112233">Dark background</span> Cube 0, 0 nm | a&lt;b', dark)
        self.assertIn(f'style="color:{colors.color("reference")}">Reference</span>', prefix(fake, "Cube 2, 550 nm", 2, 550.0))
        self.assertIn(f'style="color:{colors.color("ignored")}">Ignored</span>', prefix(fake, "Cube 3, 500 nm | N/A", 3, 500.0))
        self.assertEqual(prefix(SimpleNamespace(_frame_roles=None), "x", 0, 0.0), "x")


if __name__ == "__main__":
    unittest.main()
