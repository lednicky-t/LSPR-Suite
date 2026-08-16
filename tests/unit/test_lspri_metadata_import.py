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

from lspr_imaging_app.io.metadata_import import (
    CLASSIFICATION_LEGACY_CSV,
    CLASSIFICATION_LEGACY_TXT,
    CLASSIFICATION_SIDECAR_JSON,
    CLASSIFICATION_UNKNOWN,
    classify_metadata_file,
    import_metadata_files,
)
from lspr_imaging_app.storage.workspace import save_acquisition_metadata_sidecar

from lspr_core import ImagingAcquisitionMetadata

_META_DATA_TXT = """User: testuser
Date and time: 12.09.2025 12:59:11
Camera
BinningH=4
BinningV=4
MultiplExpTime=1.0
Averaging=1
ImageWidth=1300
ImageHeight=900
XoffsetImage=0
YoffsetImage=0
Measurement
FilterSetTime=100
Wavelength and exposure time camera
defined wl:real wl:exposure time
470:470:210
eof
"""

_MEASURING_TIMES_CSV = (
    "Image;Measuring time [ms];Measuring time [s];Note pump plan\n"
    "imLCTFatWL470Frame0;144;0;Pump is not running\n"
)


class ClassifyMetadataFileTests(unittest.TestCase):
    def test_classifies_legacy_csv_and_txt_by_content_not_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            # Deliberately misleading extensions/names - classification must
            # be content-based, not filename-based.
            csv_path = folder / "renamed.dat"
            txt_path = folder / "also_renamed.dat"
            csv_path.write_text(_MEASURING_TIMES_CSV, encoding="utf-8")
            txt_path.write_text(_META_DATA_TXT, encoding="utf-8")

            self.assertEqual(classify_metadata_file(csv_path), CLASSIFICATION_LEGACY_CSV)
            self.assertEqual(classify_metadata_file(txt_path), CLASSIFICATION_LEGACY_TXT)

    def test_unrelated_file_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            path.write_text("just some unrelated notes", encoding="utf-8")
            self.assertEqual(classify_metadata_file(path), CLASSIFICATION_UNKNOWN)

    def test_missing_file_is_unknown(self) -> None:
        self.assertEqual(classify_metadata_file(Path("does_not_exist.csv")), CLASSIFICATION_UNKNOWN)

    def test_classifies_own_sidecar_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sidecar_path = Path(tmp) / "acquisition_metadata.json"
            save_acquisition_metadata_sidecar(sidecar_path, ImagingAcquisitionMetadata(source_format="legacy_measuring_times_csv"))
            self.assertEqual(classify_metadata_file(sidecar_path), CLASSIFICATION_SIDECAR_JSON)


class ImportMetadataFilesTests(unittest.TestCase):
    def test_imports_a_mixed_legacy_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            csv_path = folder / "measureing_times.csv"
            txt_path = folder / "metaData.txt"
            csv_path.write_text(_MEASURING_TIMES_CSV, encoding="utf-8")
            txt_path.write_text(_META_DATA_TXT, encoding="utf-8")

            result = import_metadata_files([csv_path, txt_path])

        self.assertEqual(len(result.metadata.image_timings), 1)
        self.assertEqual(result.metadata.operator, "testuser")
        self.assertIn("Loaded from measureing_times.csv + metaData.txt.", result.notes)

    def test_unknown_files_are_noted_and_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            csv_path = folder / "measureing_times.csv"
            csv_path.write_text(_MEASURING_TIMES_CSV, encoding="utf-8")
            unrelated_path = folder / "readme.txt"
            unrelated_path.write_text("not a metadata file", encoding="utf-8")

            result = import_metadata_files([csv_path, unrelated_path])

        self.assertTrue(any("readme.txt" in note for note in result.notes))

    def test_raises_when_nothing_is_recognized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            unrelated_path = Path(tmp) / "readme.txt"
            unrelated_path.write_text("not a metadata file", encoding="utf-8")
            with self.assertRaises(ValueError):
                import_metadata_files([unrelated_path])

    def test_reimporting_a_previous_sidecar_export_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            csv_path = folder / "measureing_times.csv"
            txt_path = folder / "metaData.txt"
            csv_path.write_text(_MEASURING_TIMES_CSV, encoding="utf-8")
            txt_path.write_text(_META_DATA_TXT, encoding="utf-8")
            first = import_metadata_files([csv_path, txt_path])

            sidecar_path = folder / "acquisition_metadata.json"
            save_acquisition_metadata_sidecar(sidecar_path, first.metadata)

            second = import_metadata_files([sidecar_path])

        self.assertEqual(len(second.metadata.image_timings), len(first.metadata.image_timings))
        self.assertEqual(second.metadata.operator, first.metadata.operator)

    def test_native_or_sidecar_file_takes_precedence_over_legacy_files_selected_alongside(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            csv_path = folder / "measureing_times.csv"
            csv_path.write_text(_MEASURING_TIMES_CSV, encoding="utf-8")
            sidecar_path = folder / "acquisition_metadata.json"
            save_acquisition_metadata_sidecar(
                sidecar_path, ImagingAcquisitionMetadata(source_format="legacy_measuring_times_csv", operator="sidecar-operator")
            )

            result = import_metadata_files([csv_path, sidecar_path])

        self.assertEqual(result.metadata.operator, "sidecar-operator")
        self.assertTrue(any("Ignored" in note and "measureing_times.csv" in note for note in result.notes))


if __name__ == "__main__":
    unittest.main()
