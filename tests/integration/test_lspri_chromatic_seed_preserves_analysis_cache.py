"""Regression test for a bug reported against LSPRi eva's spectrum caching:
running analysis for a single ROI, then previewing a different spectral
cube, left the Spectra panel with nothing cached/displayed and the analyzed
spectra missing from HDF5 export.

Root cause (gui/chromatic_controller.py): every time a newly-viewed image
is shown, _seed_chromatic_landmarks_for_current_image() runs automatically
to fill in default chromatic-correction landmark markers for it - whether
or not chromatic correction is actually configured. Whenever it seeds a
marker, it used to call finalize_landmark_edit(), the same method used for
a deliberate landmark edit, which unconditionally clears the entire
absorbance-spectrum cache (_roi_absorbance_cache) and can disable an
already-fitted chromatic correction model. So merely looking at a
different cube wiped every other cube's already-computed spectrum, and -
depending on async timing versus the in-flight spectrum computation -
could make a just-computed spectrum vanish before it was ever displayed
or backed up to the HDF5 export.

Fixed by giving the automatic seed path its own lighter finalize
(_finalize_seeded_landmarks) that updates the landmark overlays and saves
processing state, but does not touch the analysis cache or any fitted
model - filling in a placeholder marker for a brand-new image the user
hasn't touched yet is passive bookkeeping, not a deliberate edit, and the
analysis cache is already correctly scoped by chromatic transform per
(cube, wavelength, ROI) via _roi_absorbance_signature.
"""

from __future__ import annotations

import sys

from PyQt6 import QtWidgets

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np
import unittest

from lspr_imaging_app.domain.models import FormulaSpectrumResult, ChromaticTransformModel  # noqa: E402
from lspr_imaging_app.gui.main_window import MainWindow  # noqa: E402


class ChromaticSeedPreservesAnalysisCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = MainWindow(REPO_ROOT, fast_startup=True)
        self.addCleanup(self._close_window)

    def _close_window(self) -> None:
        self.window._state.dataset = None
        self.window.close()
        self.window.deleteLater()

    def test_seeding_default_landmarks_does_not_clear_formula_spectrum_cache_or_fitted_model(self) -> None:
        window = self.window
        controller = window._chromatic_controller

        image_key = (0, 500.0)
        window._current_image_key = image_key
        window._current_processed_image = np.zeros((64, 64), dtype=np.float32)
        window._state.chromatic_landmarks = []
        window._state.preprocessing.chromatic_feature_count = 2
        # A previously fitted correction model, built from other images'
        # real landmarks - seeding placeholder markers for this new image
        # must not touch it.
        window._state.chromatic_models = [ChromaticTransformModel(spectral_cube_index=0, wavelength_nm=450.0)]
        window._state.preprocessing.chromatic_correction_enabled = True

        # A cached spectrum for some other cube, standing in for real
        # analysis work already done this session - must survive.
        fake_result = FormulaSpectrumResult(
            wavelengths_nm=np.asarray([500.0, 550.0]),
            formula_values=np.asarray([0.1, 0.2]),
            sample_reduced_value=np.asarray([10.0, 11.0]),
            reference_reduced_value=np.asarray([20.0, 21.0]),
            sample_pixel_count=np.asarray([50, 50], dtype=np.int32),
            reference_pixel_count=np.asarray([200, 200], dtype=np.int32),
        )
        fake_signature = ("fake-cube-signature",)
        window._roi_formula_spectrum_cache[fake_signature] = fake_result

        controller.sample_image_keys = lambda: [image_key]
        window._reference_image_key = lambda: None

        controller._seed_chromatic_landmarks_for_current_image()

        self.assertIn(
            fake_signature,
            window._roi_formula_spectrum_cache,
            "seeding a default chromatic landmark for a newly-viewed image must not clear "
            "already-cached absorbance spectra for other cubes",
        )
        self.assertTrue(
            window._state.chromatic_models,
            "seeding a default chromatic landmark must not disable/clear an already-fitted "
            "chromatic correction model built from other images",
        )
        self.assertTrue(window._state.preprocessing.chromatic_correction_enabled)
        # Confirm the seed actually ran (the scenario is non-trivial, not a no-op).
        self.assertTrue(controller.current_image_landmarks())


if __name__ == "__main__":
    unittest.main()
