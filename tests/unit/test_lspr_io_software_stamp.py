"""`lspr_io.hdf5.write_measurement_root_metadata`: every measurement file carries how to cite the software that wrote it.

Informational provenance (docs/schemas/hdf_standard.md, "Optional identity fields"): no reader interprets the two attributes, so the
acknowledgement of the software travels with the data without changing what a reader sees."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import h5py

from tests._paths import ensure_repo_paths

ensure_repo_paths()

from lspr_core import SUITE_DOI, citation_text  # noqa: E402
from lspr_io.hdf5 import write_measurement_root_metadata  # noqa: E402


def _root_metadata(handle: h5py.File, **overrides: object) -> None:
    arguments = dict(
        schema_name="lspr_measurement",
        schema_version="6.1",
        schema_major=6,
        schema_minor=1,
        format_name="lspr_hdf",
        format_version=1,
        app_name="LSPR Acquisition",
        app_version="0.4.1",
        created_by="LSPR Acquisition",
        started_at_utc=datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc),
    )
    arguments.update(overrides)
    write_measurement_root_metadata(handle, **arguments)


class SoftwareStampTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handle = h5py.File("stamp.h5", "w", driver="core", backing_store=False)
        self.addCleanup(self.handle.close)

    def test_the_file_carries_the_doi_and_the_citation_of_the_app_that_wrote_it(self) -> None:
        _root_metadata(self.handle)
        self.assertEqual(self.handle.attrs["software_doi"], SUITE_DOI)
        self.assertEqual(self.handle.attrs["software_citation"], citation_text("LSPR Acquisition", "0.4.1"))

    def test_the_identity_fields_of_the_standard_are_unchanged(self) -> None:
        _root_metadata(self.handle, app_name="Other App", app_version="9.9")
        for key, expected in (("schema_name", "lspr_measurement"), ("schema_version", "6.1"), ("app_name", "Other App"), ("app_version", "9.9")):
            self.assertEqual(self.handle.attrs[key], expected)
        self.assertIn("version 9.9", self.handle.attrs["software_citation"])

    def test_the_attributes_read_back_as_plain_text(self) -> None:
        _root_metadata(self.handle)
        for key in ("software_doi", "software_citation"):
            self.assertIsInstance(self.handle.attrs[key], str)


if __name__ == "__main__":
    unittest.main()
