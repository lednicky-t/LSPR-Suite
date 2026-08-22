from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from tests._paths import REPO_ROOT, ensure_repo_paths

ensure_repo_paths()

APP_SRC = REPO_ROOT / "apps" / "LSPRi" / "eva" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

import numpy as np

from lspr_imaging_app.domain.models import PreprocessingSettings, AreaRoiDetectionSettings
from lspr_imaging_app.processing.chromatic import apply_mask_wavelength_diff
from lspr_imaging_app.storage.workspace import (
    _decode_mask_wavelength_diffs,
    _encode_mask_wavelength_diffs,
    load_processing_profile,
    save_processing_profile,
)


class TestApplyMaskWavelengthDiff(unittest.TestCase):
    def setUp(self) -> None:
        self.mask = np.zeros((5, 5), dtype=bool)

    def test_no_diff_returns_same_array_unchanged(self) -> None:
        result = apply_mask_wavelength_diff(self.mask, None)
        self.assertIs(result, self.mask)
        result = apply_mask_wavelength_diff(self.mask, {})
        self.assertIs(result, self.mask)

    def test_diff_sets_exact_pixels_and_does_not_mutate_original(self) -> None:
        diff = {(1, 2): True, (3, 4): True, (0, 0): False}
        result = apply_mask_wavelength_diff(self.mask, diff)
        self.assertFalse(self.mask.any())  # original untouched
        expected = np.zeros((5, 5), dtype=bool)
        expected[1, 2] = True
        expected[3, 4] = True
        expected[0, 0] = False
        np.testing.assert_array_equal(result, expected)

    def test_out_of_bounds_entries_are_ignored(self) -> None:
        diff = {(100, 100): True, (2, 2): True}
        result = apply_mask_wavelength_diff(self.mask, diff)
        expected = np.zeros((5, 5), dtype=bool)
        expected[2, 2] = True
        np.testing.assert_array_equal(result, expected)


class TestMaskWavelengthDiffEncoding(unittest.TestCase):
    def test_round_trips_through_encode_decode(self) -> None:
        diffs = {
            (0, 450.0): {(1, 2): True, (3, 4): False},
            (0, 550.0): {(0, 0): True},
        }
        encoded = _encode_mask_wavelength_diffs(diffs)
        decoded = _decode_mask_wavelength_diffs(encoded)
        self.assertEqual(decoded, diffs)

    def test_empty_or_none_encodes_to_none(self) -> None:
        self.assertIsNone(_encode_mask_wavelength_diffs(None))
        self.assertIsNone(_encode_mask_wavelength_diffs({}))
        self.assertIsNone(_encode_mask_wavelength_diffs({(0, 450.0): {}}))

    def test_decode_ignores_malformed_entries(self) -> None:
        raw = [
            {"cube_index": 0, "wavelength_nm": 450.0, "pixels": [[1, 2, True], ["bad"], [3]]},
            {"missing": "fields"},
            "not a dict",
        ]
        decoded = _decode_mask_wavelength_diffs(raw)
        self.assertEqual(decoded, {(0, 450.0): {(1, 2): True}})

    def test_decode_none_or_empty_returns_none(self) -> None:
        self.assertIsNone(_decode_mask_wavelength_diffs(None))
        self.assertIsNone(_decode_mask_wavelength_diffs([]))


class TestSessionMaskWavelengthDiffPersistence(unittest.TestCase):
    def test_round_trips_through_processing_profile(self) -> None:
        mask = np.zeros((4, 4), dtype=bool)
        mask[1, 1] = True
        session_mask = {
            "record_path": "reference.tiff",
            "mask": mask,
            "wavelength_diffs": {(0, 450.0): {(2, 2): True}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            save_processing_profile(
                path,
                PreprocessingSettings(),
                AreaRoiDetectionSettings(),
                [],
                session_mask=session_mask,
            )
            loaded = load_processing_profile(path)
        loaded_session_mask = loaded[7]
        self.assertIsNotNone(loaded_session_mask)
        np.testing.assert_array_equal(loaded_session_mask["mask"], mask)
        self.assertEqual(loaded_session_mask["wavelength_diffs"], {(0, 450.0): {(2, 2): True}})

    def test_session_mask_without_wavelength_diffs_decodes_to_none(self) -> None:
        mask = np.zeros((3, 3), dtype=bool)
        session_mask = {"record_path": "reference.tiff", "mask": mask}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            save_processing_profile(
                path,
                PreprocessingSettings(),
                AreaRoiDetectionSettings(),
                [],
                session_mask=session_mask,
            )
            loaded = load_processing_profile(path)
        loaded_session_mask = loaded[7]
        self.assertIsNotNone(loaded_session_mask)
        self.assertIsNone(loaded_session_mask["wavelength_diffs"])


if __name__ == "__main__":
    unittest.main()
