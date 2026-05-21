from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import h5py

from tests._paths import ensure_repo_paths


ensure_repo_paths()

from lspr_io import read_root_metadata, standard_measurement_metadata, write_measurement_root_metadata


class IoMetadataTests(unittest.TestCase):
    def test_standard_measurement_metadata_contains_required_fields(self) -> None:
        started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        metadata = standard_measurement_metadata(
            created_by="tester",
            started_at_utc=started_at,
            app_name="Test App",
            app_version="9.8.7",
            experiment_name="demo",
        )

        self.assertEqual(metadata["schema_name"], "lspr_measurement")
        self.assertEqual(metadata["schema_major"], 3)
        self.assertEqual(metadata["schema_minor"], 0)
        self.assertEqual(metadata["app_name"], "Test App")
        self.assertEqual(metadata["app_version"], "9.8.7")
        self.assertEqual(metadata["started_at_utc"], "2026-01-02T03:04:05Z")
        self.assertEqual(metadata["experiment_name"], "demo")

    def test_root_metadata_round_trip(self) -> None:
        started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "measurement.h5"
            with h5py.File(path, "w") as handle:
                write_measurement_root_metadata(
                    handle,
                    schema_name="lspr_measurement",
                    schema_version="3.0",
                    schema_major=3,
                    schema_minor=0,
                    format_name="experiment_run",
                    format_version=3,
                    app_name="LSPR Suite",
                    app_version="0.1.0",
                    created_by="tester",
                    created_at_utc="2026-01-02T03:04:04Z",
                    started_at_utc=started_at,
                    experiment_name="demo",
                )

            with h5py.File(path, "r") as handle:
                metadata = read_root_metadata(handle)

        self.assertEqual(metadata["schema_name"], "lspr_measurement")
        self.assertEqual(metadata["schema_version"], "3.0")
        self.assertEqual(metadata["schema_major"], 3)
        self.assertEqual(metadata["schema_minor"], 0)
        self.assertEqual(metadata["format_name"], "experiment_run")
        self.assertEqual(metadata["format_version"], 3)
        self.assertEqual(metadata["app_name"], "LSPR Suite")
        self.assertEqual(metadata["app_version"], "0.1.0")
        self.assertEqual(metadata["created_by"], "tester")
        self.assertEqual(metadata["created_at_utc"], "2026-01-02T03:04:04Z")
        self.assertEqual(metadata["started_at_utc"], "2026-01-02T03:04:05Z")

    def test_root_metadata_accepts_iso_string_started_at(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "measurement.h5"
            with h5py.File(path, "w") as handle:
                write_measurement_root_metadata(
                    handle,
                    schema_name="lspr_measurement",
                    schema_version="3.0",
                    schema_major=3,
                    schema_minor=0,
                    format_name="experiment_run",
                    format_version=3,
                    app_name="LSPR Suite",
                    app_version="0.1.0",
                    created_by="tester",
                    created_at_utc="2026-01-02T03:04:04Z",
                    started_at_utc="2026-01-02T03:04:05Z",
                    experiment_name="demo",
                )

            with h5py.File(path, "r") as handle:
                metadata = read_root_metadata(handle)

        self.assertEqual(metadata["started_at_utc"], "2026-01-02T03:04:05Z")
