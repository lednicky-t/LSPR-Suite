from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from lspr_imaging_app.domain.exclusions import ImageExclusionRule
from lspr_imaging_app.gui.chromatic_controller import ChromaticController


def _make_controller(wavelengths: list[float], exclusions: list[ImageExclusionRule] | None = None) -> ChromaticController:
    window = SimpleNamespace(
        _wavelength_values=wavelengths,
        _state=SimpleNamespace(image_exclusions=exclusions or []),
    )
    return ChromaticController(window)


class TestCandidateChromaticWavelengths(unittest.TestCase):
    def test_excludes_the_broadband_zero_nm_frame(self) -> None:
        # Regression: a 0 nm broadband/no-filter reference frame, captured
        # alongside narrowband LCTF frames at every timepoint in some
        # acquisitions, must never be offered as a chromatic landmark sample.
        # Tracking corners/spots from it into a real narrowband frame produces
        # a huge, meaningless global shift (very different image content) that
        # silently corrupts every landmark once picked as the tracking seed.
        controller = _make_controller([0.0, 470.0, 520.0, 590.0, 660.0, 720.0])
        candidates = controller.candidate_chromatic_wavelengths(0)
        self.assertNotIn(0.0, candidates)
        self.assertEqual(candidates, [470.0, 520.0, 590.0, 660.0, 720.0])

    def test_still_respects_manual_exclusions(self) -> None:
        controller = _make_controller(
            [0.0, 470.0, 520.0, 590.0],
            exclusions=[ImageExclusionRule(spectral_cube_index=0, wavelength_nm=520.0)],
        )
        candidates = controller.candidate_chromatic_wavelengths(0)
        self.assertEqual(candidates, [470.0, 590.0])

    def test_no_wavelengths_left_when_only_broadband_is_available(self) -> None:
        controller = _make_controller([0.0])
        self.assertEqual(controller.candidate_chromatic_wavelengths(0), [])


if __name__ == "__main__":
    unittest.main()
