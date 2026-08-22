from __future__ import annotations

import sys
import unittest

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.gui.main_window import MainWindow


class _FakeWindow:
    """Just enough of MainWindow for _filtered_wavelength_values to run
    unbound against, without constructing a real (QApplication-requiring)
    MainWindow instance."""

    def __init__(self, exclude_zero: bool) -> None:
        self._exclude_zero = exclude_zero

    def _exclude_zero_wavelength_enabled(self) -> bool:
        return self._exclude_zero


class TestFilteredWavelengthValues(unittest.TestCase):
    """MainWindow._filtered_wavelength_values is the single choke point both
    dataset load (dataset_controller.py) and undo/redo (undo_manager.py) run
    `dataset.wavelengths_nm` through before storing it as
    `window._wavelength_values` - the list every wavelength slider/spinbox,
    navigation step, and candidate-wavelength computation (including
    ChromaticController.max_shift_px) reads from. Filtering here, rather than
    at each of those call sites, is what makes the "Preferences > Wavelength
    handling > Treat 0 nm as a dark reference frame" toggle apply everywhere
    at once (see the 2026-08-22 conversation).
    """

    def test_keeps_0nm_by_default(self) -> None:
        window = _FakeWindow(exclude_zero=False)
        result = MainWindow._filtered_wavelength_values(window, [0.0, 470.0, 520.0])
        self.assertEqual(result, [0.0, 470.0, 520.0])

    def test_drops_0nm_when_enabled(self) -> None:
        window = _FakeWindow(exclude_zero=True)
        result = MainWindow._filtered_wavelength_values(window, [0.0, 470.0, 520.0])
        self.assertEqual(result, [470.0, 520.0])

    def test_no_0nm_present_is_a_no_op_either_way(self) -> None:
        for exclude_zero in (False, True):
            window = _FakeWindow(exclude_zero=exclude_zero)
            result = MainWindow._filtered_wavelength_values(window, [470.0, 520.0])
            self.assertEqual(result, [470.0, 520.0])


if __name__ == "__main__":
    unittest.main()
