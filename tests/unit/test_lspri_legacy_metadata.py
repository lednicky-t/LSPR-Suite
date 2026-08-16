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

from lspr_core import SOURCE_FORMAT_LEGACY_MEASURING_TIMES_CSV

from lspr_imaging_app.io.legacy_metadata import (
    find_and_import_legacy_metadata,
    find_legacy_metadata_files,
    import_legacy_imaging_metadata,
    parse_measuring_times_csv,
    parse_meta_data_txt,
)

# Small synthetic subset in the real legacy format (2 wavelengths x 2 cubes,
# one pump-plan comment change partway through) - not real measurement data.
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
480:480:220
eof
"""

_MEASURING_TIMES_CSV = (
    "Image;Measuring time [ms];Measuring time [s];Note pump plan\n"
    "imLCTFatWL470Frame0;144;0;Pump is not running\n"
    "imLCTFatWL480Frame0;472;0;Pump is not running\n"
    "imLCTFatWL470Frame1;9535;9;H2O_--_ch1F_ch2P\n"
    "imLCTFatWL480Frame1;9875;9;H2O_--_ch1F_ch2P\n"
)


def _write_legacy_files(folder: Path) -> tuple[Path, Path]:
    csv_path = folder / "measureing_times.csv"
    txt_path = folder / "metaData.txt"
    csv_path.write_text(_MEASURING_TIMES_CSV, encoding="utf-8")
    txt_path.write_text(_META_DATA_TXT, encoding="utf-8")
    return csv_path, txt_path


class ParseMetaDataTxtTests(unittest.TestCase):
    def test_parses_camera_block_start_time_and_wavelength_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, txt_path = _write_legacy_files(Path(tmp))
            meta = parse_meta_data_txt(txt_path)

        self.assertEqual(meta["user"], "testuser")
        self.assertEqual(meta["started_at_utc"], "2025-09-12T12:59:11Z")
        self.assertEqual(meta["camera"]["BinningH"], "4")
        self.assertEqual(meta["camera"]["ImageWidth"], "1300")
        self.assertEqual(meta["filter_set_time_ms"], 100.0)
        self.assertEqual(meta["wavelength_table"], [(470.0, 470.0, 210.0), (480.0, 480.0, 220.0)])


class ParseMeasuringTimesCsvTests(unittest.TestCase):
    def test_parses_rows_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path, _txt_path = _write_legacy_files(Path(tmp))
            rows = parse_measuring_times_csv(csv_path)

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["image_name"], "imLCTFatWL470Frame0")
        self.assertEqual(rows[0]["elapsed_ms"], 144)
        self.assertEqual(rows[0]["comment"], "Pump is not running")
        self.assertEqual(rows[2]["comment"], "H2O_--_ch1F_ch2P")


class ImportLegacyImagingMetadataTests(unittest.TestCase):
    def test_end_to_end_import_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path, txt_path = _write_legacy_files(Path(tmp))
            metadata = import_legacy_imaging_metadata(csv_path, txt_path)

        self.assertEqual(metadata.source_format, SOURCE_FORMAT_LEGACY_MEASURING_TIMES_CSV)
        self.assertEqual(metadata.wavelengths_nm, [470.0, 480.0])
        self.assertEqual(len(metadata.image_timings), 4)

        # exposure_us = exposure_ms * MultiplExpTime * 1000
        camera_470 = metadata.camera_settings_by_wavelength[470.0]
        self.assertEqual(camera_470.exposure_us, 210_000.0)
        self.assertEqual(camera_470.binning, 4)
        self.assertEqual(camera_470.resolution_width_px, 1300)
        self.assertEqual(camera_470.resolution_height_px, 900)

        illumination_470 = metadata.illumination_settings_by_wavelength[470.0]
        self.assertEqual(illumination_470.settle_time_ms, 100.0)

    def test_start_datetime_anchors_absolute_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path, txt_path = _write_legacy_files(Path(tmp))
            metadata = import_legacy_imaging_metadata(csv_path, txt_path)

        first = next(t for t in metadata.image_timings if t.spectral_cube_index == 0 and t.wavelength_nm == 470.0)
        # 2025-09-12T12:59:11Z in Unix ms, plus the row's 144ms elapsed offset.
        expected_start_ms = 1757681951000
        self.assertEqual(first.acquired_at_unix_ms, expected_start_ms + 144)

    def test_comment_events_collapse_to_transitions_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path, txt_path = _write_legacy_files(Path(tmp))
            metadata = import_legacy_imaging_metadata(csv_path, txt_path)

        # 4 rows: an initial entry for the starting comment, plus 1 real
        # transition (rows 3-4 share row 3's new comment) - not 4 repeats.
        self.assertEqual(len(metadata.comment_events), 2)
        self.assertEqual(metadata.comment_events[0].comment, "Pump is not running")
        self.assertEqual(metadata.comment_events[1].comment, "H2O_--_ch1F_ch2P")

    def test_comment_at_resolves_latest_transition_at_or_before_a_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path, txt_path = _write_legacy_files(Path(tmp))
            metadata = import_legacy_imaging_metadata(csv_path, txt_path)

        before_run = metadata.image_timings[0].acquired_at_unix_ms - 1
        self.assertIsNone(metadata.comment_at(before_run))
        self.assertEqual(metadata.comment_at(metadata.image_timings[0].acquired_at_unix_ms), "Pump is not running")

    def test_image_name_not_matching_pattern_is_skipped_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "measureing_times.csv").write_text(
                "Image;Measuring time [ms];Measuring time [s];Note pump plan\n"
                "not_a_real_image_name;100;0;note\n"
                "imLCTFatWL470Frame0;200;0;note\n",
                encoding="utf-8",
            )
            (folder / "metaData.txt").write_text(_META_DATA_TXT, encoding="utf-8")
            metadata = import_legacy_imaging_metadata(folder / "measureing_times.csv", folder / "metaData.txt")

        self.assertEqual(len(metadata.image_timings), 1)


class FindLegacyMetadataFilesTests(unittest.TestCase):
    def test_finds_files_directly_in_dataset_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _write_legacy_files(folder)
            found = find_legacy_metadata_files(folder)

        self.assertIsNotNone(found)
        self.assertEqual(found[0].name, "measureing_times.csv")

    def test_finds_files_in_an_ancestor_directory(self) -> None:
        # Mirrors the real layout: measureing_times.csv/metaData.txt sit
        # beside images/ and zarr/, not inside them.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_legacy_files(root)
            images_dir = root / "images"
            images_dir.mkdir()
            found = find_legacy_metadata_files(images_dir)

        self.assertIsNotNone(found)

    def test_returns_none_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_legacy_metadata_files(Path(tmp)))

    def test_find_and_import_returns_none_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_and_import_legacy_metadata(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
